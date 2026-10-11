"""Durable, task-serialized usage ledger. Main runs record usage; delegated children enforce their slice.

All mutations lock the parent task first. Unknown or interrupted operation usage
is recorded at its upper bound. Settlement is idempotent; a crashed child keeps
its reservation until explicit reconciliation. Reservations and their numbers
exist because every operation and charge hangs off one; they are set high enough
never to refuse anything. A run ends when the model answers, on error, or when a
person stops it.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from agentforge.database.models import (
    AgentRun, BudgetOperation, BudgetReservation, BudgetTransaction, Project, Task,
)
from agentforge.repositories.runs import SessionFactory

_QUANTUM = Decimal("0.0001")
_TERMINAL = {"COMPLETED", "PARTIAL", "FAILED", "CANCELLED"}
# Children one parent run may create over its lifetime. They may run at the same time, so this is also the most that can be
# working at once.
MAX_CHILDREN = 8


class BudgetExceeded(ValueError):
    """A safe code without provider text or sensitive arguments."""


def _money(value: Decimal, *, positive: bool = False) -> Decimal:
    value = Decimal(str(value))
    if not value.is_finite() or value < 0 or (positive and value == 0):
        raise BudgetExceeded("invalid_cost_bound")
    # Round charges/caps up so database NUMERIC rounding cannot create free usage.
    return value.quantize(_QUANTUM, rounding=ROUND_CEILING)


@dataclass(frozen=True)
class Reservation:
    id: UUID
    run_id: UUID
    amount_usd: Decimal
    deadline: datetime


class BudgetService:
    def __init__(self, session_factory: SessionFactory):
        self.session_factory = session_factory

    @staticmethod
    def _value(row: BudgetReservation) -> Reservation:
        return Reservation(row.id, row.run_id, row.amount_usd, row.deadline)

    @staticmethod
    def _task(session: Session, task_id: UUID) -> Task:
        task = session.scalar(select(Task).where(Task.id == task_id).with_for_update())
        if task is None:
            raise BudgetExceeded("task_not_found")
        return task

    def _locked(self, session: Session, reservation_id: UUID) -> tuple[Task, BudgetReservation]:
        # Read only the immutable parent identifier before acquiring the common lock.
        task_id = session.scalar(select(BudgetReservation.task_id).where(BudgetReservation.id == reservation_id))
        if task_id is None:
            raise BudgetExceeded("reservation_not_found")
        task = self._task(session, task_id)
        reservation = session.scalar(select(BudgetReservation).where(
            BudgetReservation.id == reservation_id).execution_options(populate_existing=True))
        if reservation is None:
            raise BudgetExceeded("reservation_not_found")
        return task, reservation

    @staticmethod
    def _active(task: Task, row: BudgetReservation) -> None:
        if task.cancel_requested_at is not None or task.status in _TERMINAL:
            raise BudgetExceeded("task_not_active")
        if row.settled_at is not None:
            raise BudgetExceeded("reservation_settled")
        if row.depth == 2 and datetime.now(timezone.utc) >= row.deadline:
            raise BudgetExceeded("child_deadline_exceeded")

    @staticmethod
    def _consumed(session: Session, reservation_id: UUID) -> Decimal:
        return session.scalar(select(func.coalesce(func.sum(func.coalesce(
            BudgetOperation.cost_usd, BudgetOperation.max_cost_usd)), 0)).where(
                BudgetOperation.reservation_id == reservation_id)) or Decimal(0)

    def reserve(self, *, task_id: UUID, run_id: UUID, amount_usd: Decimal,
                owner_id: UUID, project_id: UUID, max_steps: int, max_tool_calls: int,
                max_wall_time_sec: int, depth: int = 1, parent_run_id: UUID | None = None) -> Reservation:
        # Reservation is a ceiling: rounding it up could exceed a requested cap.
        requested = Decimal(str(amount_usd))
        if not requested.is_finite() or requested <= 0:
            raise BudgetExceeded("invalid_cost_bound")
        amount = requested.quantize(_QUANTUM, rounding=ROUND_FLOOR)
        if amount <= 0:
            raise BudgetExceeded("reservation_below_currency_precision")
        if depth not in (1, 2) or (depth == 2) != (parent_run_id is not None):
            raise BudgetExceeded("nested_spawn_denied")
        now = datetime.now(timezone.utc)
        with self.session_factory() as session, session.begin():
            task = self._task(session, task_id)
            project = session.get(Project, project_id)
            if (project is None or project.owner_id != owner_id or task.created_by != owner_id
                or task.project_id != project_id):
                raise BudgetExceeded("task_not_authorized")
            if task.cancel_requested_at is not None or task.status in _TERMINAL:
                raise BudgetExceeded("task_not_active")
            deadline = now + timedelta(seconds=max_wall_time_sec)
            # Two top-level runs never overlap: an abandoned or concurrent one fails closed. Children of the running parent are
            # siblings and may overlap each other, but nothing else may be active beside the parent and them.
            active = session.scalar(select(BudgetReservation.id).where(
                BudgetReservation.task_id == task_id, BudgetReservation.settled_at.is_(None)).limit(1))
            if parent_run_id is None:
                if active is not None:
                    raise BudgetExceeded("concurrent_spawn_denied")
            else:
                parent = session.get(AgentRun, parent_run_id)
                if parent is None or parent.task_id != task_id or parent.status != "RUNNING" or parent.parent_run_id is not None:
                    raise BudgetExceeded("parent_run_not_active")
                parent_reservation = session.get(BudgetReservation, parent.budget_reservation_id)
                if parent_reservation is None or parent_reservation.settled_at is not None:
                    raise BudgetExceeded("parent_run_not_active")
                others = session.scalar(select(BudgetReservation.id).where(
                    BudgetReservation.task_id == task_id, BudgetReservation.settled_at.is_(None),
                    BudgetReservation.id != parent.budget_reservation_id, BudgetReservation.depth != 2).limit(1))
                if others is not None:
                    raise BudgetExceeded("concurrent_spawn_denied")
                # A child's run row is written after its reservation, in another transaction. Siblings starting together are
                # counted by their reservation until their run exists, so a burst of calls cannot slip past the limit.
                started = session.scalar(select(func.count()).select_from(AgentRun).where(AgentRun.parent_run_id == parent_run_id))
                starting = session.scalar(select(func.count()).select_from(BudgetReservation).outerjoin(
                    AgentRun, AgentRun.id == BudgetReservation.run_id).where(
                    BudgetReservation.task_id == task_id, BudgetReservation.depth == 2,
                    BudgetReservation.settled_at.is_(None), AgentRun.id.is_(None)))
                if started + starting >= MAX_CHILDREN:
                    raise BudgetExceeded("delegation_limit")
            row = BudgetReservation(id=uuid4(), task_id=task_id, run_id=run_id,
                amount_usd=amount, max_steps=max_steps, max_tool_calls=max_tool_calls,
                depth=depth, deadline=deadline)
            session.add(row)
            session.flush()
            session.add(BudgetTransaction(task_id=task_id, reservation_id=row.id,
                                          kind="RESERVE", amount_usd=amount))
            result = self._value(row)
        return result

    def check_active(self, reservation_id: UUID) -> Reservation:
        with self.session_factory() as session, session.begin():
            task, row = self._locked(session, reservation_id)
            self._active(task, row)
            return self._value(row)

    def authorize_operation(self, reservation_id: UUID, operation_key: str,
                            max_cost_usd: Decimal, kind: str = "model") -> None:
        cost = _money(max_cost_usd)
        if kind not in {"model", "tool"} or not operation_key or len(operation_key) > 200:
            raise BudgetExceeded("invalid_operation")
        with self.session_factory() as session, session.begin():
            task, row = self._locked(session, reservation_id)
            self._active(task, row)
            if session.scalar(select(BudgetOperation.id).where(BudgetOperation.reservation_id == row.id,
                    BudgetOperation.operation_key == operation_key)) is not None:
                raise BudgetExceeded("operation_already_authorized")
            if row.depth == 2:
                if self._consumed(session, row.id) + cost > row.amount_usd:
                    raise BudgetExceeded("child_cost_exceeded")
                count = session.scalar(select(func.count()).select_from(BudgetOperation).where(
                    BudgetOperation.reservation_id == row.id, BudgetOperation.kind == kind))
                if count >= (row.max_steps if kind == "model" else row.max_tool_calls):
                    raise BudgetExceeded("child_operation_limit")
            session.add(BudgetOperation(reservation_id=row.id, operation_key=operation_key,
                                        kind=kind, max_cost_usd=cost))

    def record_usage(self, reservation_id: UUID, operation_key: str,
                     cost_usd: Decimal | None, usage: dict[str, Any] | None = None) -> None:
        cost = _money(cost_usd) if cost_usd is not None else None
        with self.session_factory() as session, session.begin():
            _, row = self._locked(session, reservation_id)
            if row.settled_at is not None:
                raise BudgetExceeded("reservation_settled")
            operation = session.scalar(select(BudgetOperation).where(
                BudgetOperation.reservation_id == row.id, BudgetOperation.operation_key == operation_key))
            if operation is None:
                raise BudgetExceeded("operation_not_authorized")
            measured = cost if cost is not None else operation.max_cost_usd
            if operation.recorded_at is not None:
                if operation.cost_usd != measured:
                    raise BudgetExceeded("usage_already_recorded")
                return
            operation.cost_usd = measured
            operation.usage_json = {
                "cost_source": (
                    "authorized_upper_bound" if cost is None else
                    "configured_rates_from_reported_usage" if usage and "input_tokens" in usage else
                    "adapter_reported_cost"
                ),
                "provider_usage": usage,
            }
            operation.recorded_at = datetime.now(timezone.utc)

    def settle(self, reservation_id: UUID, actual_cost_usd: Decimal | None = None) -> Decimal:
        actual = _money(actual_cost_usd) if actual_cost_usd is not None else None
        with self.session_factory() as session, session.begin():
            _, row = self._locked(session, reservation_id)
            if row.settled_at is not None:
                if actual is not None and actual != row.charged_usd:
                    raise BudgetExceeded("settlement_conflict")
                return row.charged_usd or Decimal(0)
            consumed = self._consumed(session, row.id)
            # No caller may undercut recorded or in-flight usage with a lower aggregate.
            charged = max(consumed, actual) if actual is not None else consumed
            row.charged_usd, row.settled_at = charged, datetime.now(timezone.utc)
            session.add_all([
                BudgetTransaction(task_id=row.task_id, reservation_id=row.id,
                                  kind="CHARGE", amount_usd=charged),
                BudgetTransaction(task_id=row.task_id, reservation_id=row.id,
                                  kind="RELEASE", amount_usd=row.amount_usd),
            ])
            run = session.get(AgentRun, row.run_id)
            if run is not None:
                run.measured_cost_usd = charged
        return charged
