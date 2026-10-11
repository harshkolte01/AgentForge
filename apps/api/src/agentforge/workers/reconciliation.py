"""Conservative recovery: completed children reuse results, ambiguity never replays."""
from datetime import datetime, timezone
from sqlalchemy import select
from agentforge.database.models import AgentRun, BudgetOperation, BudgetReservation, TaskStep, ToolCall
from agentforge.repositories.events import append_event


def reconcile(store, budgets) -> bool:
    """Return True if an abandoned child prevents safe continuation.

    A checkpoint is not a provider idempotency receipt. Unknown model/tool calls
    are charged at their reserved upper bounds and the task closes PARTIAL.
    """
    unsettled = []
    ambiguous = False
    now = datetime.now(timezone.utc)
    with store.sessions() as session, session.begin():
        task = store.locked_task(session, allow_cancel=True)
        # Also inspect settled slices: a record_finish failure can leave a RUNNING
        # audit row even when the finally block successfully settled its budget.
        reservations = list(session.scalars(select(BudgetReservation).where(
            BudgetReservation.task_id == task.id)))
        for reservation in reservations:
            if reservation.settled_at is None:
                unsettled.append(reservation.id)
            run = session.get(AgentRun, reservation.run_id)
            if run is not None and run.finished_at is not None and run.result_json is not None:
                continue
            if run is None:
                from agentforge.database.sessions import SummaryAttempt
                attempt = session.get(SummaryAttempt, reservation.run_id)
                if attempt is not None:
                    # Summaries have no external effects. An interrupted paid
                    # summary is charged but never repeated; originals survive.
                    for operation in session.scalars(select(BudgetOperation).where(
                        BudgetOperation.reservation_id == reservation.id, BudgetOperation.recorded_at.is_(None))):
                        operation.cost_usd, operation.recorded_at = operation.max_cost_usd, now
                        operation.usage_json = {"cost_source": "authorized_upper_bound", "recovery": "summary_interrupted"}
                    attempt.status = "failed" if attempt.status == "prepared" else attempt.status
                    continue
                from .conversation_title import title_run_id
                if reservation.run_id == title_run_id(task.id):
                    # Naming the conversation has no external effect and the reply does not depend on it. An
                    # interrupted paid call is charged at its upper bound and never repeated; the title stays as it was.
                    for operation in session.scalars(select(BudgetOperation).where(
                        BudgetOperation.reservation_id == reservation.id, BudgetOperation.recorded_at.is_(None))):
                        operation.cost_usd, operation.recorded_at = operation.max_cost_usd, now
                        operation.usage_json = {"cost_source": "authorized_upper_bound", "recovery": "title_interrupted"}
                    continue
                # Parent-required coding checks have no child AgentRun. Their
                # successful durable receipt establishes exact operation outcome.
                from agentforge.database.coding import CodingOperation
                operations = list(session.scalars(select(BudgetOperation).where(
                    BudgetOperation.reservation_id == reservation.id)))
                if operations and all(operation.operation_key.startswith("coding:parent:") and
                    session.scalar(select(CodingOperation.id).where(CodingOperation.task_id == task.id,
                        CodingOperation.operation_key == operation.operation_key,
                        CodingOperation.status == "SUCCEEDED")) is not None for operation in operations):
                    continue
            ambiguous = True
            result = {"status": "PARTIAL", "summary": "The previous worker stopped during a child invocation. Its unknown paid operations were accounted for conservatively; no provider call was repeated.",
                "observations": [], "artifact_ids": [], "evidence_ids": [], "retryable": False,
                "next_action_suggestion": "Review recorded evidence and start a new bounded task if more research is needed."}
            if run is not None and run.finished_at is None:
                run.status, run.result_json = "PARTIAL", result
                run.error_code, run.finished_at = "UNKNOWN_REMOTE_OUTCOME", now
            for call in session.scalars(select(ToolCall).where(
                ToolCall.agent_run_id == reservation.run_id, ToolCall.status == "INTENT")):
                call.status, call.error_code, call.finished_at = "UNKNOWN", "UNKNOWN_REMOTE_OUTCOME", now
            for operation in session.scalars(select(BudgetOperation).where(
                BudgetOperation.reservation_id == reservation.id, BudgetOperation.recorded_at.is_(None))):
                operation.cost_usd, operation.recorded_at = operation.max_cost_usd, now
                operation.usage_json = {"cost_source": "authorized_upper_bound", "recovery": "unknown_worker_outcome"}
            step = session.scalar(select(TaskStep).where(TaskStep.agent_run_id == reservation.run_id))
            if step is not None and step.result_json is None:
                step.status, step.result_json = "PARTIAL", result
        if store.lease.reclaimed:
            append_event(session, task, "task.recovered", {"lease_generation": store.lease.generation,
                "reason": "unknown_child_outcome" if ambiguous else "checkpoint_resume"})
    for reservation_id in unsettled:
        budgets.settle(reservation_id)
    return ambiguous
