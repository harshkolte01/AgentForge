"""Conversation history is bounded lower-trust input, never an authorization."""
from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .tasks import TaskCreate, TaskView, TextAttachment
from .coding import RepoSelection

class SessionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: UUID | None = None
    title: str = Field(default="New session", min_length=1, max_length=120)
    mode: Literal["research", "general", "coding"] = "general"
    repository: RepoSelection | None = None

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
    steps: list[StepView] = []

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


class AutoTurnCreate(BaseModel):
    """A user message plus explicit resource selection; intent never grants resources."""
    model_config = ConfigDict(extra="forbid")
    routing: Literal["auto"]
    idempotency_key: UUID
    goal: str = Field(min_length=1, max_length=8000)
    repository: RepoSelection | None = None
    continue_from_task_id: UUID | None = None
    max_cost_usd: Decimal = Field(default=Decimal("0.50"), gt=0, le=5, decimal_places=4, allow_inf_nan=False)
    max_wall_time_sec: int = Field(default=300, ge=30, le=1200)
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
