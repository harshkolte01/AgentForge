"""Bounded model protocol shared by real and explicitly injected fixture models."""

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

# Credentials a provider message could echo back. Everything else in the message is shown as the provider wrote it.
_SECRETS = re.compile(r"(sk-[A-Za-z0-9_\-]{8,}|Bearer\s+[A-Za-z0-9._\-]{8,}|(?i:api[_-]?key)[\"'=:\s]+[A-Za-z0-9._\-]{8,}|(?<=://)[^/\s:@]+:[^/\s@]+@)")
DETAIL_CHARS = 600


def redact(text: str) -> str:
    return _SECRETS.sub("[redacted]", text)


def provider_detail(exc: BaseException) -> str:
    """What the provider itself said about a failure ("429: You exceeded your current quota…"), with credentials removed."""
    message = getattr(exc, "message", None)
    if not isinstance(message, str) or not message.strip():
        message = str(exc)
    status = getattr(exc, "status_code", None)
    text = " ".join(redact(message).split())[:DETAIL_CHARS]
    return f"{status}: {text}" if status and text else text


class ModelConfigurationError(ValueError):
    pass


class ModelProviderError(RuntimeError):
    """A failure of the model provider, carrying the provider's own (credential-free) message in `detail`.

    code is a stable taxonomy value (MODEL_PROVIDER_ERROR, RATE_LIMIT, AUTH_ERROR, ...) the caller
    stores; retriable marks failures that happened before any completion was produced. `shown` is what a person reads.
    """

    def __init__(self, message: str = "model provider request failed", *, code: str = "MODEL_PROVIDER_ERROR",
                 retriable: bool = False, detail: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.retriable = retriable
        self.detail = detail

    @property
    def shown(self) -> str:
        return self.detail or str(self)


class ModelContextLimit(ValueError):
    pass


@dataclass(frozen=True)
class ModelUsage:
    """What the provider measured for one request, split the way it is billed.

    `cached_tokens` and `cache_write_tokens` are parts of `input_tokens` (read from, and written to, the provider's prompt
    cache); `reasoning_tokens` is the part of `output_tokens` spent thinking. A provider that does not report a part says 0.
    """
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal | None
    cached_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0

    @property
    def uncached_input_tokens(self) -> int:
        return max(0, self.input_tokens - self.cached_tokens - self.cache_write_tokens)


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
                       tool_choice: dict[str, Any] | str | None = None,
                       on_narration: Callable[[str], None] | None = None) -> ModelResponse:
        """The text a model writes streams to on_narration while tools are offered (it may turn out to be a note
        before tool calls or the final answer, which is only known when the call ends) and to on_text when none are;
        implementations may ignore both. tool_choice forces a tool. A model that also takes `on_retry` and `on_tool` (see
        OpenAIAdapter) is given them; one that does not is simply not told."""
        ...


@dataclass(frozen=True)
class ModelRates:
    """Operator supplied USD rates snapshot, per million tokens; no guessed prices.

    The cache rates are optional: without them every input token is priced at the input rate, which overstates the bill
    for cached reads (the provider charges a fraction of the input rate) and understates cache writes on providers that
    charge more for them. Set both from the provider's price list to make the ledger exact.
    """

    input_per_million: Decimal
    output_per_million: Decimal
    cached_input_per_million: Decimal | None = None
    cache_write_per_million: Decimal | None = None

    def __post_init__(self) -> None:
        for value in (self.input_per_million, self.output_per_million, self.cached_input_per_million, self.cache_write_per_million):
            if value is not None and (not value.is_finite() or value < 0):
                raise ModelConfigurationError("model rates must be finite and nonnegative")
        if self.cached_input_per_million is not None and self.cached_input_per_million > self.input_per_million:
            raise ModelConfigurationError("the cached input rate cannot be higher than the input rate")

    def cost(self, input_tokens: int, output_tokens: int, cached_tokens: int = 0, cache_write_tokens: int = 0) -> Decimal:
        """The price of one request. `cached_tokens` and `cache_write_tokens` are the parts of `input_tokens` the provider read
        from, and wrote to, its prompt cache; a part larger than the input is clamped, never charged twice."""
        if min(input_tokens, output_tokens, cached_tokens, cache_write_tokens) < 0:
            raise ValueError("negative token usage")
        cached = min(cached_tokens, input_tokens)
        written = min(cache_write_tokens, input_tokens - cached)
        plain = input_tokens - cached - written
        cached_rate = self.cached_input_per_million if self.cached_input_per_million is not None else self.input_per_million
        write_rate = self.cache_write_per_million if self.cache_write_per_million is not None else self.input_per_million
        return (self.input_per_million * plain + cached_rate * cached + write_rate * written
                + self.output_per_million * output_tokens) / Decimal(1_000_000)


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


# The keys an assistant message carries for the model's own earlier thinking (see `openai_adapter.message_from_response`).
# They are passed back to a provider that takes them and never shown as text, so they are counted as what they are.
REASONING_KEY = "reasoning"
REASONING_TOKENS_KEY = "reasoning_tokens"
# An encrypted reasoning item whose token count the provider did not report is estimated from its size at this ratio.
ENCRYPTED_CHARS_PER_TOKEN = 4


def reasoning_input_tokens(message: dict[str, Any]) -> int:
    """The input tokens an assistant message's earlier thinking will cost when it is passed back, or 0 without any."""
    items = message.get(REASONING_KEY)
    if not isinstance(items, list) or not items:
        return 0
    reported = message.get(REASONING_TOKENS_KEY)
    if isinstance(reported, int) and not isinstance(reported, bool) and reported > 0:
        return reported
    encrypted = sum(len(item.get("encrypted_content") or "") for item in items if isinstance(item, dict))
    return -(-encrypted // ENCRYPTED_CHARS_PER_TOKEN)


def without_reasoning(messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """The messages without their reasoning keys (the encrypted text is opaque and far larger than the thinking it stands
    for), and the input tokens that thinking will cost instead. Messages without reasoning are returned as they are."""
    stripped, tokens = [], 0
    for message in messages:
        if REASONING_KEY in message or REASONING_TOKENS_KEY in message:
            tokens += reasoning_input_tokens(message)
            message = {key: value for key, value in message.items() if key not in (REASONING_KEY, REASONING_TOKENS_KEY)}
        stripped.append(message)
    return stripped, tokens


def message_input_tokens(messages: list[dict[str, Any]]) -> int:
    """Estimate media as visual/page input, never as the base64 transport string."""
    messages, media_tokens = without_reasoning(messages)
    sanitized = []
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            sanitized.append(message)
            continue
        parts = []
        for part in content:
            if part.get("type") == "image_url":
                parts.append({"type": "text", "text": "[image attachment]"})
                media_tokens += 8192
            elif part.get("type") == "file" and part.get("file", {}).get("file_data", "").startswith("data:application/pdf;base64,"):
                import base64, io
                from pypdf import PdfReader
                try:
                    data = base64.b64decode(part["file"]["file_data"].split(",", 1)[1], validate=True)
                    pages = len(PdfReader(io.BytesIO(data), strict=True).pages)
                except Exception:
                    pages = 20  # Fail conservatively; ingestion rejects malformed files.
                media_tokens += max(1, pages) * 32768
                parts.append({"type": "text", "text": "[PDF attachment]"})
            else:
                parts.append(part)
        sanitized.append({**message, "content": parts})
    return estimate_tokens(json.dumps(sanitized, ensure_ascii=False, allow_nan=False)) + media_tokens


def conservative_input_tokens(messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> int:
    """An upper-biased estimate of a request's input tokens, used to bound its cost and to fit the window.

    It leans high on purpose: the operator's window and output cap bound every request, and real usage is
    accounted separately from the provider's own report. It was once one token per byte, which overstated
    real usage about 3.5 times and made small windows unusable.
    """
    if any(isinstance(message.get("content"), list) for message in messages):
        base = message_input_tokens(messages) + estimate_tokens(json.dumps({"tools": tools}, ensure_ascii=False, allow_nan=False)) + PROTOCOL_OVERHEAD_TOKENS + PER_MESSAGE_OVERHEAD_TOKENS * len(messages)
    else:
        stripped, reasoning = without_reasoning(messages)
        payload = json.dumps({"messages": stripped, "tools": tools}, ensure_ascii=False, allow_nan=False)
        base = estimate_tokens(payload) + reasoning + PROTOCOL_OVERHEAD_TOKENS + PER_MESSAGE_OVERHEAD_TOKENS * len(messages)
    return base + max(256, -(-base // 20))  # shared 5% fitting margin, at least 256 tokens
