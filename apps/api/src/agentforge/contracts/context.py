"""What the model's context window holds for a conversation, for the composer's status indicator."""
from typing import Literal

from pydantic import BaseModel, Field


class LastRunContext(BaseModel):
    """Measured by the provider's own usage reports for the conversation's latest run."""

    peak_input_tokens: int = Field(ge=0)
    model_calls: int = Field(ge=0)
    # Largest number of old tool results stubbed out of any single request in that run.
    compacted_results: int = Field(default=0, ge=0)
    # Of the main agent's own requests (a delegated subagent has a context of its own), as the provider measured them: the
    # newest, which is what the model holds right now while the run goes on, and the first, which is the run's starting size.
    latest_input_tokens: int | None = Field(default=None, ge=0)
    first_input_tokens: int | None = Field(default=None, ge=0)
    # The run is still going on, so `latest_input_tokens` is the live size of the model's context.
    active: bool = False


class ContextStatus(BaseModel):
    model: str | None = None
    provider: str | None = None
    window_tokens: int | None = None
    max_output_tokens: int | None = None
    input_limit_tokens: int | None = None
    # unknown: no window is configured. ok < 50%, notice < 80%, high otherwise.
    level: Literal["ok", "notice", "high", "unknown"]
    percent: float = Field(ge=0, le=100)
    used_tokens: int = Field(ge=0)
    # Next message's estimated starting input. A previous run's peak is separate historical data.
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
    summary_through_ordinal: int = 0
    uncovered_turns: int = 0
    retrieval_available: bool = False
    snapshot_revision: int = 0
