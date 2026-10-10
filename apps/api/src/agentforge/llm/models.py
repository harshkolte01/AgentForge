"""Bounded model protocol shared by real and explicitly injected fixture models."""

import json
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol


# The synthetic tool a child finishes with; its arguments are the typed result.
SUBMIT_RESULT = "submit_result"


class ModelConfigurationError(ValueError):
    pass


class ModelProviderError(RuntimeError):
    """Safe error without the provider request, credentials, or response body.

    code is a stable taxonomy value (MODEL_PROVIDER_ERROR, RATE_LIMIT, AUTH_ERROR, ...) the caller
    stores and shows; retriable marks failures that happened before any completion was produced.
    """

    def __init__(self, message: str = "model provider request failed", *, code: str = "MODEL_PROVIDER_ERROR",
                 retriable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retriable = retriable


class ModelContextLimit(ValueError):
    pass


@dataclass(frozen=True)
class ModelUsage:
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal | None


@dataclass(frozen=True)
class ModelResponse:
    message: dict[str, Any]
    usage: ModelUsage
    model_id: str
    finish_reason: str


class ToolCallingModel(Protocol):
    model_id: str

    def maximum_request_cost(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> Decimal: ...

    async def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]],
                       on_text: Callable[[str], None] | None = None,
                       tool_choice: dict[str, Any] | str | None = None) -> ModelResponse:
        """on_text, when given, receives the person-facing reply as it streams (message content, or the
        arguments of a submit_result call); implementations may ignore it. tool_choice forces a tool."""
        ...


@dataclass(frozen=True)
class ModelRates:
    """Operator supplied USD rates snapshot, per million tokens; no guessed prices."""

    input_per_million: Decimal
    output_per_million: Decimal

    def __post_init__(self) -> None:
        for value in (self.input_per_million, self.output_per_million):
            if not value.is_finite() or value < 0:
                raise ModelConfigurationError("model rates must be finite and nonnegative")

    def cost(self, input_tokens: int, output_tokens: int) -> Decimal:
        if input_tokens < 0 or output_tokens < 0:
            raise ValueError("negative token usage")
        return (self.input_per_million * input_tokens + self.output_per_million * output_tokens) / Decimal(1_000_000)


# English prose and code average 3.5-4 bytes per token (measured here: about 3.5 on real runs). Three keeps
# a margin; characters outside ASCII (CJK, emoji) cost far more per byte, so each is charged two tokens.
ASCII_CHARS_PER_TOKEN = 3
NON_ASCII_TOKENS_PER_CHAR = 2
PROTOCOL_OVERHEAD_TOKENS = 1500
PER_MESSAGE_OVERHEAD_TOKENS = 32


def estimate_tokens(text: str) -> int:
    """A deliberately high estimate of the tokens in `text`, never a count."""
    if text.isascii():
        return -(-len(text) // ASCII_CHARS_PER_TOKEN)
    ascii_chars = sum(1 for char in text if char < "")
    return -(-ascii_chars // ASCII_CHARS_PER_TOKEN) + NON_ASCII_TOKENS_PER_CHAR * (len(text) - ascii_chars)


def conservative_input_tokens(messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> int:
    """An upper-biased estimate of a request's input tokens, used to bound its cost and to fit the window.

    It leans high on purpose: the operator's window and output cap bound every request, and real usage is
    accounted separately from the provider's own report. It was once one token per byte, which overstated
    real usage about 3.5 times and made small windows unusable.
    """
    payload = json.dumps({"messages": messages, "tools": tools}, ensure_ascii=False, allow_nan=False)
    return estimate_tokens(payload) + PROTOCOL_OVERHEAD_TOKENS + PER_MESSAGE_OVERHEAD_TOKENS * len(messages)
