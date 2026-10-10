"""Persist immutable run identity and committed tool intent before external effects."""

from collections.abc import Callable, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from agentforge.database.models import (
    AgentDefinition, AgentDefinitionVersion, AgentRun, BudgetReservation,
    Project, Task, ToolCall,
)

SessionFactory = Callable[[], Session]


class AgentRunRepository:
    def __init__(self, session_factory: SessionFactory):
        self.session_factory = session_factory

    def record_start(self, *, run_id: UUID, task_id: UUID, owner_id: UUID, project_id: UUID,
                     definition_version_id: UUID, definition_sha256: str,
                     definition_snapshot: dict[str, Any], brief_sha256: str,
                     effective_permissions: dict[str, Any], effective_budget: dict[str, Any],
                     reservation_id: UUID) -> UUID:
        with self.session_factory() as session, session.begin():
            task = session.scalar(select(Task).join(Project).where(
                Task.id == task_id, Task.project_id == project_id,
                Task.created_by == owner_id, Project.owner_id == owner_id).with_for_update(of=Task))
            reservation = session.get(BudgetReservation, reservation_id)
            snapshot = session.get(AgentDefinitionVersion, definition_version_id)
            definition = session.get(AgentDefinition, snapshot.definition_id) if snapshot else None
            if (task is None or task.cancel_requested_at is not None
                or task.status in {"COMPLETED", "PARTIAL", "FAILED", "CANCELLED"}
                or datetime.now(timezone.utc) >= task.created_at + timedelta(seconds=task.max_wall_time_sec)
                or reservation is None or reservation.task_id != task_id
                or reservation.run_id != run_id or reservation.settled_at is not None
                or datetime.now(timezone.utc) >= reservation.deadline
                or snapshot is None or definition is None or definition.disabled_at is not None
                or definition.project_id not in (None, project_id)
                or snapshot.sha256 != definition_sha256 or snapshot.definition_json != definition_snapshot):
                raise ValueError("run_identity_not_authorized")
            session.add(AgentRun(id=run_id, task_id=task_id,
                definition_version_id=definition_version_id, definition_sha256=definition_sha256,
                definition_snapshot=definition_snapshot, brief_sha256=brief_sha256,
                effective_permissions_json=effective_permissions, effective_budget_json=effective_budget,
                budget_reservation_id=reservation_id, reserved_cost_usd=reservation.amount_usd,
                status="RUNNING"))
        return run_id

    def record_finish(self, *, run_id: UUID, status: str,
                      result_json: dict[str, Any] | None, error_code: str | None = None) -> None:
        if status not in {"COMPLETED", "PARTIAL", "FAILED", "BLOCKED", "CANCELLED"}:
            raise ValueError("invalid_run_status")
        with self.session_factory() as session, session.begin():
            run = session.scalar(select(AgentRun).where(AgentRun.id == run_id).with_for_update())
            if run is None:
                raise ValueError("run_not_found")
            if run.finished_at is not None:
                if run.status != status or run.result_json != result_json or run.error_code != error_code:
                    raise ValueError("run_already_finished")
                return
            run.status, run.result_json, run.error_code = status, result_json, error_code
            run.finished_at = datetime.now(timezone.utc)


class ToolCallRepository:
    def __init__(self, session_factory: SessionFactory):
        self.session_factory = session_factory

    def record_intent(self, *, task_id: UUID, run_id: UUID, tool_name: str,
                      args_digest: str, intent_json_sanitized: dict[str, Any],
                      operation_key: str) -> UUID:
        call_id = uuid4()
        # A separate transaction is required: the adapter must never run if this commit fails.
        with self.session_factory() as session, session.begin():
            run = session.get(AgentRun, run_id)
            if run is None or run.task_id != task_id or run.status != "RUNNING":
                raise ValueError("tool_run_not_active")
            session.add(ToolCall(id=call_id, task_id=task_id, agent_run_id=run_id,
                tool_name=tool_name, operation_key=operation_key, args_digest=args_digest,
                intent_json_sanitized=intent_json_sanitized, status="INTENT", evidence_ids=[]))
        return call_id

    def record_outcome(self, *, call_id: UUID, status: str,
                       result_json: dict[str, Any] | None = None, error_code: str | None = None,
                       evidence_ids: Sequence[str] = ()) -> None:
        if status not in {"SUCCEEDED", "FAILED", "BLOCKED", "UNKNOWN"}:
            raise ValueError("invalid_tool_status")
        with self.session_factory() as session, session.begin():
            call = session.scalar(select(ToolCall).where(ToolCall.id == call_id).with_for_update())
            if call is None:
                raise ValueError("tool_call_not_found")
            if call.finished_at is not None:
                if (call.status, call.result_json, call.error_code, call.evidence_ids) != (
                    status, result_json, error_code, list(evidence_ids)):
                    raise ValueError("tool_call_already_finished")
                return
            call.status, call.result_json, call.error_code = status, result_json, error_code
            call.evidence_ids, call.finished_at = list(evidence_ids), datetime.now(timezone.utc)
