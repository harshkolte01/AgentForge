"""Append events in the same transaction as their state change."""

import json
from typing import Any, NamedTuple
from uuid import UUID
from sqlalchemy import and_, case, false, func, select
from agentforge.contracts.activity import LiveStreamSnapshot
from agentforge.contracts.events import TaskEventView
from agentforge.contracts.tasks import TERMINAL_STATUSES
from agentforge.database.models import Project, Task, TaskEvent
from agentforge.database.phase3 import TaskStream
from .runs import SessionFactory

_KEYS = {"status", "previous_status", "step_id", "step_key", "spawn_id", "type", "version", "error_code", "attempt", "lease_generation", "evidence_ids", "artifact_ids", "checks", "reason", "tool", "detail", "model", "input_tokens", "output_tokens", "sandbox_id", "base_sha", "revision", "profile_id", "exit_code", "qualified", "failure_evidence_ids", "approval_id", "action_type", "state", "commit_sha", "pr_number", "pr_url", "head_branch", "base_branch", "repo_id", "delivery_kind", "branch_url", "text", "epoch", "call_id", "tool_call_id", "agent_run_id", "parent_run_id", "child_run_id", "outcome", "timed_out", "duration_ms", "truncated"}


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


ToolIdentity = tuple[str | None, str | None, str | None]   # tool, call_id, detail: what a `tool` row is about


def _identity(row) -> ToolIdentity | None:
    """Which tool call a live row shows. Only a `tool` or `output` row has one; prose rows read as none."""
    return (row.tool, row.call_id, row.detail) if row.kind in ("tool", "output") else None


class LiveRead(NamedTuple):
    terminal: bool
    event_sequence: int | None
    live: tuple[int, int, str, int, str] | None   # epoch, revision, kind, length, text (whole or the unsent rest)
    tool: ToolIdentity | None = None              # the tool call `live` shows, when its kind is `tool`


class EventRepository:
    def __init__(self, session_factory: SessionFactory):
        self.session_factory = session_factory

    def stream_snapshot(self, task_id: UUID, owner_id: UUID):
        """(epoch, revision, kind, text, tool) of the live text, or None. `tool` is (tool, call_id, detail) for a live
        tool call and None otherwise. Never raises: streaming is best effort.

        A task that is already terminal shows no live text: a row left behind by a worker that died is reported as
        cleared (one revision newer, exactly as `clear_stream` writes it), so nothing stale outlives the task.
        """
        try:
            with self.session_factory() as session:
                row = session.execute(select(TaskStream.epoch, TaskStream.revision, TaskStream.kind, TaskStream.text,
                        TaskStream.tool, TaskStream.call_id, TaskStream.detail, Task.status)
                    .join(Task, Task.id == TaskStream.task_id)
                    .where(TaskStream.task_id == task_id, Task.created_by == owner_id)).first()
            if row is None:
                return None
            if row.status in TERMINAL_STATUSES and (row.text or row.kind != "progress"):
                return row.epoch, row.revision + 1, "progress", "", None
            return row.epoch, row.revision, row.kind, row.text, _identity(row)
        except Exception:
            return None

    def stream_since(self, task_id: UUID, owner_id: UUID, state: tuple[int, int] | None = None, sent: int = 0) -> LiveRead:
        """One pass of the SSE loop in one statement: the task's status and event watermark, and the live text as
        (epoch, revision, kind, length, text). A reader that already holds `sent` characters of the same (epoch,
        revision) gets only the rest of the text; any other reader (or a shorter row) gets the whole of it. Lengths
        are counted in characters by PostgreSQL, so offsets agree with the text Python receives.

        Never raises (streaming is best effort): a failed read is LiveRead(False, None, None). A terminal task's
        leftover text is reported as cleared, as in `stream_snapshot`.
        """
        try:
            same = (and_(TaskStream.epoch == state[0], TaskStream.revision == state[1], func.length(TaskStream.text) >= sent)
                if state is not None else false())
            with self.session_factory() as session:
                row = session.execute(select(Task.status, Task.event_sequence, TaskStream.epoch, TaskStream.revision,
                        TaskStream.kind, TaskStream.tool, TaskStream.call_id, TaskStream.detail,
                        func.length(TaskStream.text).label("length"),
                        case((same, func.substr(TaskStream.text, sent + 1)), else_=TaskStream.text).label("text"))
                    .outerjoin(TaskStream, TaskStream.task_id == Task.id)
                    .where(Task.id == task_id, Task.created_by == owner_id)).first()
        except Exception:
            return LiveRead(False, None, None)
        if row is None:
            return LiveRead(False, None, None)
        terminal = row.status in TERMINAL_STATUSES
        tool = None
        if row.epoch is None:
            live = None
        elif terminal and (row.length or row.kind != "progress"):
            live = (row.epoch, row.revision + 1, "progress", 0, "")
        else:
            live, tool = (row.epoch, row.revision, row.kind, row.length, row.text), _identity(row)
        return LiveRead(terminal, row.event_sequence, live, tool)

    def live_snapshot(self, task_id: UUID, owner_id: UUID) -> LiveStreamSnapshot:
        """The whole current live text with the task's event watermark, for a client that lost its place."""
        from .tasks import TaskNotFound
        # The live text first, then the task: a clear seen here was written after the task ended, so the status and event
        # watermark read next already include that end (see the SSE loop in api/routes_events.py).
        live = self.stream_snapshot(task_id, owner_id)
        with self.session_factory() as session:
            task = session.execute(select(Task.event_sequence, Task.status).join(Project).where(
                Task.id == task_id, Task.created_by == owner_id, Project.owner_id == owner_id)).first()
        if task is None:
            raise TaskNotFound("task_not_found")
        epoch, revision, kind, text, tool = live if live is not None else (0, 0, "progress", "", None)
        name, call_id, detail = tool or (None, None, None)
        return LiveStreamSnapshot(task_id=task_id, epoch=epoch, revision=revision, kind=kind, text=text, tool=name, call_id=call_id,
                                  detail=detail, event_sequence=task.event_sequence, terminal=task.status in TERMINAL_STATUSES)

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
