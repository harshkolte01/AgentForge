"""Bounded model protocol shared by real and explicitly injected fixture models."""

import json
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol


class ModelConfigurationError(ValueError):
    pass


class ModelProviderError(RuntimeError):
    """Safe error without the provider request, credentials, or response body."""


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
                       on_text: Callable[[str], None] | None = None) -> ModelResponse:
        """on_text, when given, receives content tokens as they stream; implementations may ignore it."""
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


def conservative_input_tokens(messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> int:
    """Bound by UTF-8 bytes plus protocol overhead instead of undercounting chars.

    This deliberately overestimates typical BPE input. The operator's context
    window and output cap still bound every request; actual provider usage is
    accounted separately. No tool output is silently dropped from conversation.
    """
    payload = json.dumps({"messages": messages, "tools": tools}, ensure_ascii=False, allow_nan=False)
    return len(payload.encode("utf-8")) + 4096 + 256 * len(messages)
