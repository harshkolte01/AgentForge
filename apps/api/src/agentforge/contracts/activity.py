"""Wire contracts for task activity: live text snapshots, activity summaries and tool call details.

Additive to the existing task/session API. Nothing here carries raw tool arguments, source contents,
credentials or provider exception strings; details are allowlisted fields and bounded previews of evidence
and artifacts the task already owns.
"""
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# One vocabulary for how a tool call ended. "running" is only ever derived by a client from a start without a finish.
ToolCallOutcome = Literal["running", "completed", "failed", "blocked", "cancelled", "unknown"]
# What a live text row holds: a progress note being written, or the answer being written.
LiveTextKind = Literal["progress", "answer"]
# How a tool is presented when inspected. Tools without a detail kind are compact status rows only.
ToolDetailKind = Literal["execution", "diff", "artifact"]


class LiveStreamSnapshot(BaseModel):
    """The whole current live text of a task, for offset-mismatch recovery and polling fallback.

    (epoch, revision) order snapshots and deltas: a larger epoch wins; within one epoch a larger revision wins;
    within one (epoch, revision) text only grows by appending at `offset == len(text so far)`.
    With no live row, epoch and revision are 0 and text is empty.
    """
    model_config = ConfigDict(extra="forbid")
    task_id: UUID
    epoch: int = Field(0, ge=0)
    revision: int = Field(0, ge=0)
    kind: LiveTextKind = "progress"
    text: str = ""
    # The task's durable event watermark when this was read, and whether the task is already terminal.
    event_sequence: int = Field(0, ge=0)
    terminal: bool = False


class MessageActivity(BaseModel):
    """What the work behind one assistant message amounted to. Counts use the same presentation rules as the client."""
    model_config = ConfigDict(extra="forbid")
    action_count: int = Field(0, ge=0)   # exact: not capped, self-corrections and internal reads excluded
    note_count: int = Field(0, ge=0)     # progress notes the agent wrote (assistant.message events)
    available: bool = False              # action_count > 0 or note_count > 0: note-only activity is still activity
    event_watermark: int = Field(0, ge=0)
    terminal: bool = False
    status: str | None = None            # task status: COMPLETED, FAILED or CANCELLED (older messages may say PARTIAL)
    error_code: str | None = None
    approval_pending: bool = False       # the agent is waiting for the person to approve a tool call
    duration_ms: int | None = Field(None, ge=0)   # how long the run worked, once it has finished
    source_count: int = Field(0, ge=0)
    artifact_count: int = Field(0, ge=0)


class ActivitySummary(MessageActivity):
    """GET /api/v1/tasks/{task_id}/activity-summary"""
    task_id: UUID
    # Sequence of the task.finished event when the task is terminal, otherwise null.
    terminal_sequence: int | None = Field(None, ge=0)


class AttachmentView(BaseModel):
    """A user message's attachment as displayed: its name and size, never its content."""
    model_config = ConfigDict(extra="forbid")
    name: str = Field(max_length=120)
    characters: int | None = Field(None, ge=0)


class ToolDetailText(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str
    truncated: bool = False
    bytes: int | None = Field(None, ge=0)


class ToolDetailExecution(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile_id: str | None = None
    exit_code: int | None = None
    timed_out: bool | None = None
    qualified: bool | None = None
    output_truncated: bool | None = None
    observed_test_count: int | None = None


class ToolDetailFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(max_length=500)
    additions: int | None = Field(None, ge=0)
    deletions: int | None = Field(None, ge=0)


class ToolDetailArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    filename: str
    mime_type: str
    byte_size: int = Field(ge=0)
    download_url: str  # /api/v1/tasks/{task_id}/artifacts/{id}, an owner-authorized route


class ToolCallDetail(BaseModel):
    """GET /api/v1/tasks/{task_id}/tool-calls/{tool_call_id}"""
    model_config = ConfigDict(extra="forbid")
    task_id: UUID
    tool_call_id: UUID
    tool: str
    kind: ToolDetailKind
    outcome: ToolCallOutcome
    error_code: str | None = None
    started_at: datetime
    finished_at: datetime | None = None
    duration_ms: int | None = Field(None, ge=0)
    execution: ToolDetailExecution | None = None
    files: list[ToolDetailFile] = []
    stdout: ToolDetailText | None = None
    stderr: ToolDetailText | None = None
    diff: ToolDetailText | None = None
    preview: ToolDetailText | None = None
    artifacts: list[ToolDetailArtifact] = []
    # Why there is nothing more to show ("evidence_unavailable", "artifact_missing", ...), when that is the case.
    unavailable_reason: str | None = None
