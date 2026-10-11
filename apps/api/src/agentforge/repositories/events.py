"""Append events in the same transaction as their state change."""

import json
from typing import Any
from uuid import UUID
from sqlalchemy import select
from agentforge.contracts.activity import LiveStreamSnapshot
from agentforge.contracts.events import TaskEventView
from agentforge.contracts.tasks import TERMINAL_STATUSES
from agentforge.database.models import Project, Task, TaskEvent
from agentforge.database.phase3 import TaskStream
from .runs import SessionFactory

_KEYS = {"status", "previous_status", "step_id", "step_key", "spawn_id", "type", "version", "error_code", "attempt", "lease_generation", "evidence_ids", "artifact_ids", "checks", "reason", "tool", "detail", "model", "input_tokens", "output_tokens", "sandbox_id", "base_sha", "revision", "profile_id", "exit_code", "qualified", "failure_evidence_ids", "approval_id", "action_type", "state", "commit_sha", "pr_number", "pr_url", "head_branch", "base_branch", "repo_id", "delivery_kind", "branch_url", "text", "epoch", "call_id", "tool_call_id", "agent_run_id", "outcome", "timed_out", "duration_ms"}


def append_event(session, task: Task, event_type: str, payload: dict[str, Any]) -> None:
    """Caller holds the Task row lock; never log arbitrary provider/model text.

    The one exception is `assistant.message`: a short note the model wrote for the person to read next to its tool
    calls (the same kind of text as the reply), bounded by the stream that emits it.
    """
    if event_type not in {"task.created", "task.status", "task.cancel_requested", "task.plan.created", "task.step.updated", "subagent.started", "subagent.finished", "task.verified", "task.recovered", "task.finished", "tool.started", "tool.finished", "model.started", "model.finished", "assistant.message", "sandbox.workspace.created", "test.finished", "task.repair.started", "approval.required", "approval.decided", "github.delivery.confirmed"}:
        raise ValueError("unknown_event_type")
    if not set(payload).issubset(_KEYS) or len(json.dumps(payload, allow_nan=False).encode()) > 16000:
        raise ValueError("unsafe_event_payload")
    task.event_sequence += 1
    session.add(TaskEvent(task_id=task.id, sequence=task.event_sequence,
                          event_type=event_type, payload=payload))


class EventRepository:
    def __init__(self, session_factory: SessionFactory):
        self.session_factory = session_factory

    def stream_snapshot(self, task_id: UUID, owner_id: UUID):
        """(epoch, revision, kind, text) of the live text, or None. Never raises: streaming is best effort.

        A task that is already terminal shows no live text: a row left behind by a worker that died is reported as
        cleared (one revision newer, exactly as `clear_stream` writes it), so nothing stale outlives the task.
        """
        try:
            with self.session_factory() as session:
                row = session.execute(select(TaskStream.epoch, TaskStream.revision, TaskStream.kind, TaskStream.text, Task.status)
                    .join(Task, Task.id == TaskStream.task_id)
                    .where(TaskStream.task_id == task_id, Task.created_by == owner_id)).first()
            if row is None:
                return None
            if row.status in TERMINAL_STATUSES and (row.text or row.kind != "progress"):
                return row.epoch, row.revision + 1, "progress", ""
            return row.epoch, row.revision, row.kind, row.text
        except Exception:
            return None

    def live_snapshot(self, task_id: UUID, owner_id: UUID) -> LiveStreamSnapshot:
        """The whole current live text with the task's event watermark, for a client that lost its place."""
        from .tasks import TaskNotFound
        with self.session_factory() as session:
            task = session.execute(select(Task.event_sequence, Task.status).join(Project).where(
                Task.id == task_id, Task.created_by == owner_id, Project.owner_id == owner_id)).first()
        if task is None:
            raise TaskNotFound("task_not_found")
        live = self.stream_snapshot(task_id, owner_id)
        epoch, revision, kind, text = live if live is not None else (0, 0, "progress", "")
        return LiveStreamSnapshot(task_id=task_id, epoch=epoch, revision=revision, kind=kind, text=text,
                                  event_sequence=task.event_sequence, terminal=task.status in TERMINAL_STATUSES)

    def list_after(self, task_id: UUID, owner_id: UUID, after_sequence: int = 0,
                   limit: int = 100) -> list[TaskEventView]:
        from .tasks import TaskNotFound
        if after_sequence < 0 or not 1 <= limit <= 200:
            raise ValueError("invalid_event_cursor")
        with self.session_factory() as session:
            owner = session.scalar(select(Task.id).join(Project).where(
                Task.id == task_id, Task.created_by == owner_id, Project.owner_id == owner_id))
            if owner is None:
                raise TaskNotFound("task_not_found")
            return [TaskEventView.model_validate(row) for row in session.scalars(
                select(TaskEvent).where(TaskEvent.task_id == task_id,
                    TaskEvent.sequence > after_sequence).order_by(TaskEvent.sequence).limit(limit))]
