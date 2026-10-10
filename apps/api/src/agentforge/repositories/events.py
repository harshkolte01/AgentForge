"""Append events in the same transaction as their state change."""

import json
from typing import Any
from uuid import UUID
from sqlalchemy import select
from agentforge.contracts.events import TaskEventView
from agentforge.database.models import Project, Task, TaskEvent
from .runs import SessionFactory

_KEYS = {"status", "previous_status", "step_id", "step_key", "spawn_id", "type", "version", "error_code", "attempt", "lease_generation", "evidence_ids", "artifact_ids", "checks", "reason", "tool", "model", "input_tokens", "output_tokens", "sandbox_id", "base_sha", "revision", "profile_id", "exit_code", "qualified", "failure_evidence_ids", "approval_id", "action_type", "state", "commit_sha", "pr_number", "pr_url", "head_branch", "base_branch", "repo_id"}


def append_event(session, task: Task, event_type: str, payload: dict[str, Any]) -> None:
    """Caller holds the Task row lock; never log arbitrary provider/model text."""
    if event_type not in {"task.created", "task.status", "task.cancel_requested", "task.plan.created", "task.step.updated", "subagent.started", "subagent.finished", "task.verified", "task.recovered", "task.finished", "tool.started", "tool.finished", "model.started", "model.finished", "sandbox.workspace.created", "test.finished", "task.repair.started", "approval.required", "approval.decided", "github.delivery.confirmed"}:
        raise ValueError("unknown_event_type")
    if not set(payload).issubset(_KEYS) or len(json.dumps(payload, allow_nan=False).encode()) > 16000:
        raise ValueError("unsafe_event_payload")
    task.event_sequence += 1
    session.add(TaskEvent(task_id=task.id, sequence=task.event_sequence,
                          event_type=event_type, payload=payload))


class EventRepository:
    def __init__(self, session_factory: SessionFactory):
        self.session_factory = session_factory

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
