"""Bounded spawn requests and the plain outcome of an agent run."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from .agent_definition import StrictContract
from .tasks import MAX_ANSWER_CHARS


class SpawnRequest(StrictContract):
    type: str = Field(min_length=3, max_length=160, pattern=r"^(builtin/[a-z][a-z0-9-]+|project/[0-9a-f-]{36}/[a-z][a-z0-9-]+)$")
    version: int = Field(ge=1)
    prompt: str = Field(min_length=1, max_length=8000)
    context_refs: Annotated[tuple[str, ...], Field(strict=False, max_length=20)] = ()
    requested_budget_usd: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def distinct_refs(self) -> Self:
        if len(self.context_refs) != len(set(self.context_refs)):
            raise ValueError("duplicate context references")
        if any(not ref or len(ref) > 256 for ref in self.context_refs):
            raise ValueError("invalid context reference")
        return self


class SubagentResult(StrictContract):
    """PASS: the agent answered, and `summary` is its answer exactly as written. FAIL: the run broke, and `summary`
    is the error a person reads (a provider's own message, for instance); `error_code` says what kind."""
    status: Literal["PASS", "FAIL"]
    summary: str = Field(min_length=1, max_length=MAX_ANSWER_CHARS)
    error_code: str | None = Field(default=None, max_length=64)
