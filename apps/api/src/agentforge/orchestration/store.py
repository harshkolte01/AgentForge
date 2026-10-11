"""Fenced and idempotent parent writes with transactionally sequenced events."""
from uuid import UUID
from sqlalchemy import select
from agentforge.contracts.tasks import MAX_ANSWER_CHARS, TERMINAL_STATUSES
from agentforge.database.models import AgentRun, Task
from agentforge.repositories.events import append_event
from agentforge.repositories.jobs import Lease, assert_lease
from agentforge.repositories.runs import SessionFactory
from agentforge.tools.lifecycle import reconcile_open_calls


class WorkflowStore:
    def __init__(self, sessions: SessionFactory, lease: Lease):
        self.sessions, self.lease = sessions, lease

    def locked_task(self, session, *, allow_cancel=False):
        row = session.scalar(select(Task).where(Task.id == self.lease.task_id).with_for_update())
        if row is None:
            raise ValueError("task_missing")
        assert_lease(session, row, self.lease, allow_cancel=allow_cancel)
        return row

    def task(self):
        with self.sessions() as session:
            row = session.get(Task, self.lease.task_id)
            if row is None:
                raise ValueError("task_missing")
            session.expunge(row)
            return row

    def transition(self, status: str):
        allowed = {"CREATED": {"PLANNING"}, "PLANNING": {"RUNNING"}}
        with self.sessions() as session, session.begin():
            task = self.locked_task(session)
            if task.status == status:
                return
            if status not in allowed.get(task.status, set()):
                raise ValueError("invalid_task_transition")
            previous, task.status = task.status, status
            append_event(session, task, "task.status", {"status": status, "previous_status": previous})

    def pin_repository(self, repository):
        """Records the commit the selected repository is read at. Done once, before the tools are bound to it."""
        with self.sessions() as session, session.begin():
            task = self.locked_task(session)
            task.permissions_json = {**task.permissions_json, "repository": repository.model_dump(mode="json")}

    def completed_run(self, run_id: UUID) -> dict | None:
        with self.sessions() as session:
            run = session.get(AgentRun, run_id)
            if run and run.task_id == self.lease.task_id and run.finished_at and run.result_json:
                return run.result_json
            return None

    def finish(self, status: str, summary: str, result: dict | None = None, error_code: str | None = None):
        """Ends the task. `summary` is saved as the assistant's message exactly as given."""
        if status not in TERMINAL_STATUSES:
            raise ValueError("invalid_terminal_status")
        with self.sessions() as session, session.begin():
            from agentforge.repositories.sessions import lock_task_conversation
            lock_task_conversation(session, self.lease.task_id)
            task = self.locked_task(session, allow_cancel=True)
            if task.status in TERMINAL_STATUSES:
                return
            # A cancel racing the end of a run wins unless the task is already durably terminal.
            if task.cancel_requested_at is not None:
                status, summary, error_code = "CANCELLED", "Stopped.", "USER_CANCELLED"
            task.status, task.final_summary = status, summary[:MAX_ANSWER_CHARS]
            task.final_result, task.error_code = result, error_code
            # Settle every step still shown as running, from the durable tool-call rows, before the task ends.
            reconcile_open_calls(session, task, final_status=status, error_code=error_code)
            append_event(session, task, "task.finished", {"status": status, "error_code": error_code})
            from agentforge.repositories.sessions import complete_task_conversation
            complete_task_conversation(session, task.id)
        from .stream import clear_stream
        clear_stream(self.sessions, self.lease.task_id)

    def reply_stream(self):
        """Live copy of the text the model is writing (see orchestration/stream.py); the notes it writes before tool
        calls are saved as `assistant.message` events as each one is complete."""
        from .stream import ReplyStream
        return ReplyStream(self.sessions, self.lease.task_id, emit=self.progress)

    def progress(self, event_type: str, payload: dict):
        with self.sessions() as session, session.begin():
            task = self.locked_task(session)
            append_event(session, task, event_type, payload)
