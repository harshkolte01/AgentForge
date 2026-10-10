"""Bounded spawn briefs and evidence-linked result schemas."""

import json
from typing import Annotated, Any, Literal, Self

from pydantic import Field, model_validator

from .agent_definition import OutputSchemaId, StrictContract

TextItems = Annotated[tuple[Annotated[str, Field(min_length=1, max_length=2000)], ...], Field(strict=False, max_length=100)]
Ids = Annotated[tuple[Annotated[str, Field(min_length=1, max_length=256)], ...], Field(strict=False, max_length=100)]


class SpawnRequest(StrictContract):
    type: str = Field(min_length=3, max_length=160, pattern=r"^(builtin/[a-z][a-z0-9-]+|project/[0-9a-f-]{36}/[a-z][a-z0-9-]+)$")
    version: int = Field(ge=1)
    prompt: str = Field(min_length=20, max_length=8000)
    context_refs: Annotated[tuple[str, ...], Field(strict=False, max_length=20)] = ()
    requested_budget_usd: float | None = Field(default=None, gt=0, le=5)

    @model_validator(mode="after")
    def distinct_refs(self) -> Self:
        if len(self.context_refs) != len(set(self.context_refs)):
            raise ValueError("duplicate context references")
        if any(not ref or len(ref) > 256 for ref in self.context_refs):
            raise ValueError("invalid context reference")
        return self


class SubagentResult(StrictContract):
    status: Literal["PASS", "FAIL", "PARTIAL", "BLOCKED"]
    summary: str = Field(min_length=1, max_length=8000)
    observations: TextItems = ()
    artifact_ids: Ids = ()
    evidence_ids: Ids = ()
    retryable: bool = False
    next_action_suggestion: str | None = Field(default=None, max_length=2000)


class ResearchClaim(StrictContract):
    claim: str = Field(min_length=1, max_length=2000)
    source_ids: Annotated[Ids, Field(min_length=1)]
    confidence: Literal["verified", "supported", "uncertain"]


class ResearchResult(SubagentResult):
    claims: Annotated[tuple[ResearchClaim, ...], Field(strict=False, max_length=100)] = ()
    unknowns: TextItems = ()


class ExecutionResult(SubagentResult):
    changed_paths: Ids = ()
    test_evidence_ids: Ids = ()


class ReviewFinding(StrictContract):
    severity: Literal["critical", "high", "medium", "low"]
    description: str = Field(min_length=1, max_length=2000)
    path: str | None = Field(default=None, max_length=1024)
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)
    evidence_ids: Annotated[Ids, Field(min_length=1)]

    @model_validator(mode="after")
    def line_range(self) -> Self:
        if self.line_end is not None and (self.line_start is None or self.line_end < self.line_start):
            raise ValueError("invalid line range")
        if self.line_start is not None and self.path is None:
            raise ValueError("line requires a file path")
        return self


class ReviewResult(SubagentResult):
    findings: Annotated[tuple[ReviewFinding, ...], Field(strict=False, max_length=100)] = ()
    acceptance_checks: TextItems = ()


RESULT_MODELS: dict[str, type[SubagentResult]] = {
    "research_result_v1": ResearchResult,
    "execution_result_v1": ExecutionResult,
    "review_result_v1": ReviewResult,
}


def parse_result(schema_id: OutputSchemaId, raw: str | dict[str, Any]) -> SubagentResult:
    model = RESULT_MODELS[schema_id]
    if isinstance(raw, str):
        if len(raw.encode("utf-8")) > 262144:
            raise ValueError("result exceeds size limit")
        return model.model_validate_json(raw)
    # Validate JSON-shaped data through the same strict boundary as provider JSON.
    return model.model_validate_json(json.dumps(raw, allow_nan=False))
