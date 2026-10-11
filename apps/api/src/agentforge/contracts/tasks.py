"""Public bounded task contracts; identity is always supplied by the server."""

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agentforge.contracts.coding import CodingTaskSpec

TaskStatus = Literal["CREATED", "PLANNING", "RUNNING", "VERIFYING", "RETRYING", "AWAITING_APPROVAL", "COMPLETED", "PARTIAL", "FAILED", "CANCELLED"]
TERMINAL_STATUSES = frozenset({"COMPLETED", "PARTIAL", "FAILED", "CANCELLED"})
# The longest answer kept whole. A coding answer can be long; a message is cut only if it goes beyond this.
MAX_ANSWER_CHARS = 100_000
# Runs are not limited. A task row and its budget reservation need positive numbers, so these are set high enough
# never to stop anything; usage is still recorded, and a person ends a run with Stop.
UNLIMITED_COST_USD = Decimal("100000")
UNLIMITED_WALL_TIME_SEC = 7 * 24 * 3600


class TextAttachment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    content: str = Field(min_length=1, max_length=8000)


class TaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: UUID
    kind: Literal["research", "general", "coding"] = "research"
    coding: CodingTaskSpec | None = None
    continue_from_task_id: UUID | None = None
    goal: str = Field(min_length=1, max_length=8000)
    # Accepted for older clients and ignored: runs are not limited (see UNLIMITED_* below).
    max_cost_usd: Decimal = Field(default=UNLIMITED_COST_USD, gt=0, decimal_places=4, allow_inf_nan=False)
    max_wall_time_sec: int = Field(default=UNLIMITED_WALL_TIME_SEC, ge=1)
    allowed_web_hosts: tuple[str, ...] = Field(default=(), max_length=20)

    attachments: tuple[TextAttachment, ...] = Field(default=(), max_length=4)
    agent_key: str | None = Field(default=None, max_length=160, pattern=r"^project/[0-9a-f-]{36}/[a-z][a-z0-9-]+$")
    agent_version: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_kind(self):
        if self.continue_from_task_id is not None and self.kind != "coding":
            raise ValueError("workspace continuation is available only for coding")
        if (self.kind == "coding") != (self.coding is not None):
            raise ValueError("coding specification is required only for coding tasks")
        if sum(len(item.content.encode()) for item in self.attachments) > 16000:
            raise ValueError("combined attachments exceed 16000 bytes")
        if (self.agent_key is None) != (self.agent_version is None):
            raise ValueError("agent key and immutable version are required together")
        if self.kind == "coding" and (self.attachments or self.agent_key):
            raise ValueError("coding tasks do not accept research attachments or agents")
        if self.kind == "coding" and self.allowed_web_hosts:
            raise ValueError("coding tasks do not accept web permissions")
        return self

    @field_validator("goal")
    @classmethod
    def clean_goal(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("goal must contain non-whitespace text")
        return value

    @field_validator("allowed_web_hosts")
    @classmethod
    def validate_hosts(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        import re
        from ipaddress import ip_address
        if len(set(values)) != len(values):
            raise ValueError("duplicate web hosts")
        for value in values:
            if (len(value) > 253 or not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", value)
                or ".." in value or "." not in value or value.endswith((".local", ".internal", ".localhost"))):
                raise ValueError("exact public DNS hostnames are required")
            try:
                ip_address(value)
            except ValueError:
                continue
            raise ValueError("IP literals are not allowed")
        return values


class TaskView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    project_id: UUID
    kind: Literal["research", "general", "coding"] = "research"
    coding: CodingTaskSpec | None = None
    continue_from_task_id: UUID | None = None
    goal: str
    status: TaskStatus
    thread_id: str
    max_cost_usd: Decimal
    max_wall_time_sec: int
    created_at: datetime
    updated_at: datetime
    cancel_requested_at: datetime | None
    final_summary: str | None
    final_result: dict[str, Any] | None
    error_code: str | None
    event_sequence: int

    @model_validator(mode="before")
    @classmethod
    def task_kind(cls, value):
        if hasattr(value, "permissions_json"):
            permissions = value.permissions_json or {}
            result = {name: getattr(value, name) for name in cls.model_fields if hasattr(value, name)}
            result["kind"] = permissions.get("kind", "research")
            result["coding"] = permissions.get("coding")
            result["continue_from_task_id"] = (permissions.get("continuation") or {}).get("task_id")
            return result
        return value


class TaskStepView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    task_id: UUID
    step_key: str
    agent_type: str
    definition_version: int
    objective: str
    acceptance_criteria: list[str]
    depends_on: list[str]
    status: str
    agent_run_id: UUID | None
    result: dict[str, Any] | None
