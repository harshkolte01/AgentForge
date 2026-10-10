"""Persisted metadata-only event envelopes, ordered within a task."""

from datetime import datetime
from typing import Any
from uuid import UUID
from pydantic import BaseModel, ConfigDict


class TaskEventView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    task_id: UUID
    sequence: int
    event_type: str
    payload: dict[str, Any]
    created_at: datetime
