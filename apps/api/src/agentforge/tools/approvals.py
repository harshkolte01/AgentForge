"""Inline approval of tool calls that change something.

A person picks one of two modes, once, for every conversation:

* "ask": before the agent runs a command, writes or edits a file, or opens a pull request, the call stops and the chat shows
  an Approve / Decline card. The run waits as long as it takes; Stop ends it.
* "auto": nothing asks. Every call runs.

The mode is read fresh before each call, so switching it releases a call that is already waiting. The card is the
`approval.required` event; the answer is the row's state, written by `decide`, and announced as `approval.decided`.
"""
import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

from agentforge.database.models import Project, Task, User
from agentforge.database.sessions import ToolApproval

from .errors import ToolError

log = logging.getLogger(__name__)

POLL_SECONDS = 0.75
# A wait can last hours; the poll slows down to this while nobody answers (an answer still lands within a couple of seconds).
MAX_POLL_SECONDS = 2.5
MODES = ("ask", "auto")


class ApprovalNotFound(LookupError):
    pass


def get_mode(sessions, user_id: UUID) -> str:
    with sessions() as db:
        mode = db.scalar(select(User.approval_mode).where(User.id == user_id))
    return mode if mode in MODES else "ask"


def set_mode(sessions, user_id: UUID, mode: str) -> str:
    if mode not in MODES:
        raise ValueError("invalid_approval_mode")
    with sessions() as db, db.begin():
        db.execute(update(User).where(User.id == user_id).values(approval_mode=mode))
    return mode


def decide(sessions, owner_id: UUID, approval_id: UUID, decision: str) -> dict:
    """Records the person's answer. Only the owner of the task can answer, and only while it is still pending."""
    if decision not in ("approve", "deny"):
        raise ValueError("invalid_decision")
    state = "APPROVED" if decision == "approve" else "DENIED"
    with sessions() as db, db.begin():
        row = db.scalar(select(ToolApproval).join(Task, Task.id == ToolApproval.task_id).join(Project, Project.id == Task.project_id)
            .where(ToolApproval.id == approval_id, Task.created_by == owner_id, Project.owner_id == owner_id).with_for_update(of=ToolApproval))
        if row is None:
            raise ApprovalNotFound("approval_not_found")
        if row.state == "PENDING":
            row.state, row.decided_at = state, datetime.now(timezone.utc)
        return {"id": str(row.id), "state": row.state}


class ToolApprovals:
    def __init__(self, sessions, emit: Callable[[str, dict], None], *, poll_seconds: float = POLL_SECONDS) -> None:
        self.sessions, self.emit, self.poll_seconds = sessions, emit, poll_seconds

    def _open(self, ctx, operation_key: str, tool: str, summary: str, detail: dict) -> tuple[UUID, str, bool]:
        """The card's row, created once per call (a replayed call finds its earlier row and its answer)."""
        with self.sessions() as db, db.begin():
            statement = insert(ToolApproval).values(task_id=ctx.task_id, user_id=ctx.user_id, operation_key=operation_key,
                tool=tool, summary=summary, detail_json=detail).on_conflict_do_nothing(
                index_elements=[ToolApproval.task_id, ToolApproval.operation_key]).returning(ToolApproval.id)
            created = db.scalar(statement)
            row = db.scalar(select(ToolApproval).where(ToolApproval.task_id == ctx.task_id, ToolApproval.operation_key == operation_key))
            return row.id, row.state, created is not None

    def _state(self, approval_id: UUID) -> str:
        with self.sessions() as db:
            return db.scalar(select(ToolApproval.state).where(ToolApproval.id == approval_id)) or "CANCELLED"

    def _settle(self, approval_id: UUID, state: str) -> str:
        """Moves a pending card to `state`; returns whatever state it ended in (a person's answer can win the race)."""
        with self.sessions() as db, db.begin():
            row = db.scalar(select(ToolApproval).where(ToolApproval.id == approval_id).with_for_update())
            if row is not None and row.state == "PENDING":
                row.state, row.decided_at = state, datetime.now(timezone.utc)
            return row.state if row is not None else "CANCELLED"

    async def asks(self, ctx) -> bool:
        """Whether a call would stop for an answer right now ("Ask me")."""
        return await asyncio.to_thread(get_mode, self.sessions, ctx.user_id) != "auto"

    async def allows(self, ctx, *, operation_key: str, tool: str, summary: str, detail: dict,
                     check: Callable[[], Awaitable[None]], truncated: bool = False) -> bool:
        """True when the call may run. `check` raises when the run must stop (cancelled, access revoked), which ends the wait.

        `summary` is what the card shows, exactly as the call will run it; `truncated` says it had to be cut to fit. A
        command is never approved from part of its text: one too long to show is refused before anyone is asked.
        """
        if await asyncio.to_thread(get_mode, self.sessions, ctx.user_id) == "auto":
            return True
        if truncated and tool == "bash":
            raise ToolError("COMMAND_TOO_LONG", "This command is too long to show in full for approval. Write the script to a file "
                            "with write (the person sees its content), then run that file with a short bash command.")
        approval_id, state, created = await asyncio.to_thread(self._open, ctx, operation_key, tool, summary, detail)
        payload = {"approval_id": str(approval_id), "tool": tool, "detail": summary, "state": "PENDING",
                   **({"truncated": True} if truncated else {})}
        if created:
            self.emit("approval.required", payload)
        pause = self.poll_seconds
        try:
            while state == "PENDING":
                await asyncio.sleep(pause)
                pause = min(MAX_POLL_SECONDS, pause * 1.25)
                await check()
                state = await asyncio.to_thread(self._state, approval_id)
                if state == "PENDING" and await asyncio.to_thread(get_mode, self.sessions, ctx.user_id) == "auto":
                    state = await asyncio.to_thread(self._settle, approval_id, "APPROVED")
        except BaseException:
            await asyncio.to_thread(self._settle, approval_id, "CANCELLED")
            raise
        if created or state != "PENDING":
            # The card already carries the text; the answer only needs to say which card and how it was answered.
            self.emit("approval.decided", {"approval_id": str(approval_id), "tool": tool, "state": state})
        return state == "APPROVED"
