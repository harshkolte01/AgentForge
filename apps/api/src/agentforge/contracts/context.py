"""What the model's context window holds for a conversation, for the composer's status indicator."""
from typing import Literal

from pydantic import BaseModel, Field


class LastRunContext(BaseModel):
    """Measured by the provider's own usage reports for the conversation's latest run."""

    peak_input_tokens: int = Field(ge=0)
    model_calls: int = Field(ge=0)
    # Largest number of old tool results stubbed out of any single request in that run.
    compacted_results: int = Field(default=0, ge=0)


class ContextStatus(BaseModel):
    model: str | None = None
    provider: str | None = None
    window_tokens: int | None = None
    max_output_tokens: int | None = None
    # unknown: no window is configured. ok < 50%, notice < 80%, high otherwise.
    level: Literal["ok", "notice", "high", "unknown"]
    percent: float = Field(ge=0, le=100)
    used_tokens: int = Field(ge=0)
    # What used_tokens measures: the larger of the next turn's starting context and the latest run's peak.
    used_basis: Literal["next_turn", "last_run_peak"]
    # Estimated at about four characters per token; the last run's peak is measured.
    baseline_tokens: int = Field(ge=0)
    history_tokens: int = Field(ge=0)
    history_budget_tokens: int = Field(ge=0)
    last_run: LastRunContext | None = None
    messages_total: int = Field(ge=0)
    messages_in_full: int = Field(ge=0)
    messages_condensed: int = Field(ge=0)
    messages_omitted: int = Field(ge=0)
