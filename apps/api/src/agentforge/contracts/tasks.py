"""Public bounded task contracts; identity is always supplied by the server."""

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agentforge.contracts.coding import CodingTaskSpec

TaskStatus = Literal["CREATED", "PLANNING", "RUNNING", "VERIFYING", "RETRYING", "AWAITING_APPROVAL", "COMPLETED", "PARTIAL", "FAILED", "CANCELLED"]
TERMINAL_STATUSES = frozenset({"COMPLETED", "PARTIAL", "FAILED", "CANCELLED"})


class TaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: UUID
    kind: Literal["research", "coding"] = "research"
    coding: CodingTaskSpec | None = None
    goal: str = Field(min_length=20, max_length=8000)
    max_cost_usd: Decimal = Field(default=Decimal("0.50"), gt=0, le=5, decimal_places=4, allow_inf_nan=False)
    max_wall_time_sec: int = Field(default=300, ge=30, le=1200)
    allowed_web_hosts: tuple[str, ...] = Field(default=(), max_length=20)

    @model_validator(mode="after")
    def validate_kind(self):
        if (self.kind == "coding") != (self.coding is not None):
            raise ValueError("coding specification is required only for coding tasks")
        if self.kind == "coding" and self.allowed_web_hosts:
            raise ValueError("coding tasks do not accept web permissions")
        return self

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
    kind: Literal["research", "coding"] = "research"
    coding: CodingTaskSpec | None = None
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
