"""Conversation history is bounded lower-trust input, never an authorization."""
from datetime import datetime
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .tasks import TaskCreate, TaskView

class SessionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: UUID
    title: str = Field(default="New session", min_length=1, max_length=120)
    mode: Literal["research", "general", "coding"] = "general"

class SessionPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, min_length=1, max_length=120)
    archived: bool | None = None

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

class MessageView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    session_id: UUID
    sequence: int
    role: Literal["user", "assistant"]
    content: str
    task_id: UUID
    created_at: datetime

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
    task: TaskView
    replayed: bool = False
