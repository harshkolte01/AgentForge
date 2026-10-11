"""Conversation history is bounded lower-trust input, never an authorization."""
from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .activity import AttachmentView, MessageActivity
from .tasks import UNLIMITED_COST_USD, UNLIMITED_WALL_TIME_SEC, TaskCreate, TaskView, TextAttachment
from .coding import RepoSelection

class SessionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: UUID | None = None
    title: str = Field(default="New session", min_length=1, max_length=120)
    mode: Literal["research", "general", "coding"] = "general"
    repository: RepoSelection | None = None
    idempotency_key: UUID | None = None

class SessionPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, min_length=1, max_length=120)
    archived: bool | None = None
    repository: RepoSelection | None = None

class SessionView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    project_id: UUID
    title: str
    mode: Literal["research", "general", "coding"]
    archived: bool
    active_task_id: UUID | None
    last_sequence: int
    created_at: datetime
    updated_at: datetime
    repository: RepoSelection | None = None
    continuation_task_id: UUID | None = None
    queue_paused: bool = False
    queued_count: int = 0
    revision: int = 0
    # What the sidebar shows beside the conversation: "running" while the agent works (or the next message is about to
    # start), "waiting_approval" while a tool call waits for the person, "failed" when the latest reply failed or a
    # queued message could not start, "unread" when a reply finished that the owner has not seen yet, otherwise "idle".
    status: Literal["idle", "running", "waiting_approval", "failed", "unread"] = "idle"
    read_sequence: int = 0


class ReadReceipt(BaseModel):
    """The owner has seen the conversation up to this message. It can only move forward and never past the last message."""
    model_config = ConfigDict(extra="forbid")
    sequence: int = Field(ge=0, le=2_000_000)

class StepView(BaseModel):
    """One tool call made while producing a reply: the tool's name and how it ended, never its content."""
    tool: str
    status: Literal["done", "failed"]


class MessageView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    session_id: UUID
    sequence: int
    role: Literal["user", "assistant"]
    content: str
    task_id: UUID | None
    created_at: datetime
    # `steps` is the older, capped list of finished calls; `activity` carries the exact counts the client should trust.
    steps: list[StepView] = []
    activity: MessageActivity | None = None            # assistant messages that came from a task
    attachments: list[AttachmentView] = []             # user messages: names and sizes of attached files only
    turn_id: UUID | None = None
    turn_state: str | None = None

class SessionTurnCreate(TaskCreate):
    # Project is derived from the owned session, never selected by this caller.
    project_id: UUID | None = None
    idempotency_key: UUID

    @model_validator(mode="after")
    def no_project_override(self):
        if self.project_id is not None:
            raise ValueError("project is derived from session")
        return self

class TurnView(BaseModel):
    session_id: UUID
    user_message: MessageView
    task: TaskView | None
    assistant_message: MessageView | None = None
    replayed: bool = False
    turn_id: UUID | None = None
    state: str = "running"
    queue_position: int | None = None
    error_code: str | None = None


class QueueCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Stop is fenced to what the browser actually observed, never a successor.
    task_id: UUID | None = None


class AutoTurnCreate(BaseModel):
    """A user message plus explicit resource selection; intent never grants resources."""
    model_config = ConfigDict(extra="forbid")
    routing: Literal["auto"]
    idempotency_key: UUID
    goal: str = Field(min_length=1, max_length=8000)
    repository: RepoSelection | None = None
    continue_from_task_id: UUID | None = None
    # Accepted for older clients and ignored: runs are not limited.
    max_cost_usd: Decimal = Field(default=UNLIMITED_COST_USD, gt=0, decimal_places=4, allow_inf_nan=False)
    max_wall_time_sec: int = Field(default=UNLIMITED_WALL_TIME_SEC, ge=1)
    allowed_web_hosts: tuple[str, ...] = Field(default=(), max_length=20)
    attachments: tuple[TextAttachment, ...] = Field(default=(), max_length=4)
    agent_key: str | None = Field(default=None, max_length=160, pattern=r"^project/[0-9a-f-]{36}/[a-z][a-z0-9-]+$")
    agent_version: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_bounds(self):
        # Reuse the explicit task's complete grant/input/budget validation.
        checked = TaskCreate(project_id=UUID(int=0), kind="general", **self.model_dump(exclude={
            "routing", "idempotency_key", "repository", "continue_from_task_id"}))
        self.goal = checked.goal
        return self
