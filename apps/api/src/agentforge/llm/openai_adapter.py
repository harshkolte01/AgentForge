"""AsyncOpenAI Chat Completions adapter with measured usage and bounded, cost-safe retries.

Official contracts reviewed:
https://developers.openai.com/api/reference/python
https://developers.openai.com/api/reference/python/resources/chat/subresources/completions/methods/create

The SDK's own retries stay disabled; this adapter does its own, bounded and visible. It retries failures that
produced no completion (rate limits, 5xx, failed or dropped connections, timeouts, transient error frames sent
mid-stream), with jittered backoff that honours the provider's retry-after. A stream that breaks (or simply ends
without a finish reason) after it started is retried too, since repeating one request costs far less than failing
the whole run; `on_retry` tells the caller to throw away what it showed.
Quota and credential errors are never retried.
"""

import asyncio
import copy
import json
import random
from collections.abc import Awaitable, Callable
from typing import Any, cast

import httpx
import openai
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam, ChatCompletionToolParam

from .models import (
    ModelConfigurationError, ModelContextLimit, ModelProviderError,
    ModelRates, ModelResponse, ModelUsage, PER_MESSAGE_OVERHEAD_TOKENS,
    PROTOCOL_OVERHEAD_TOKENS, conservative_input_tokens, provider_detail,
)
from .limits import LUNA_LONG_CONTEXT_THRESHOLD, LUNA_WINDOW, input_limit, is_luna



class _StreamBroke(Exception):
    """A stream failed after it had started delivering output; the provider's error is its __cause__."""


MAX_RETRY_WAIT_SEC = 30.0
BASE_RETRY_WAIT_SEC = 1.0
# The `code`/`type` of an error frame sent mid-stream that say a later attempt may succeed (see `_classify`).
RATE_LIMIT_KINDS = frozenset({"rate_limit_exceeded", "rate_limit_error"})
TRANSIENT_KINDS = frozenset({"server_error", "overloaded", "overloaded_error", "service_unavailable", "service_unavailable_error",
                             "internal_error", "internal_server_error"})
CONNECT_TIMEOUT_SEC = 15.0


class OpenAIAdapter:
    def __init__(self, *, api_key: str, model_id: str, rates: ModelRates,
                 context_window_tokens: int, max_output_tokens: int,
                 timeout_sec: float = 60, reasoning_effort: str | None = None,
                 parallel_tool_calls: bool = True, strict_tools: bool = False,
                 long_context_rates: ModelRates | None = None,
                 max_retries: int = 2, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
        if not api_key or not model_id or context_window_tokens <= max_output_tokens or max_output_tokens < 1:
            raise ModelConfigurationError("explicit model, API key and valid token limits are required")
        if not 0 <= max_retries <= 8:
            raise ModelConfigurationError("model retries must be between 0 and 8")
        self.model_id = model_id
        self.rates = rates
        self.long_context_rates = long_context_rates
        self.context_window_tokens = context_window_tokens
        self.max_output_tokens = max_output_tokens
        self.reasoning_effort = reasoning_effort
        self.parallel_tool_calls = parallel_tool_calls
        self.strict_tools = strict_tools
        self.max_retries = max_retries
        self._sleep = sleep
        if is_luna(model_id):
            if reasoning_effort != "none":
                raise ModelConfigurationError("gpt-6-luna Chat Completions tool calls require reasoning_effort=none")
            if context_window_tokens > LUNA_WINDOW:
                raise ModelConfigurationError("gpt-6-luna window exceeds its verified provider limit")
            if self.max_input_tokens > LUNA_LONG_CONTEXT_THRESHOLD and long_context_rates is None:
                raise ModelConfigurationError("gpt-6-luna long context requires explicit long-context input and output rates")
            if long_context_rates is not None and (
                long_context_rates.input_per_million < rates.input_per_million or
                long_context_rates.output_per_million < rates.output_per_million
            ):
                raise ModelConfigurationError("long-context rates cannot be lower than standard rates")
        # Pin the official origin; user/model data and ambient OPENAI_BASE_URL
        # cannot redirect credentials. No hidden SDK retry may bypass the ledger.
        # `timeout_sec` bounds each wait for data (a reasoning model can think a while before its first token); connecting
        # gets a short limit of its own so an unreachable provider fails fast and is retried.
        self._client = AsyncOpenAI(api_key=api_key, base_url="https://api.openai.com/v1",
                                   timeout=httpx.Timeout(timeout_sec, connect=min(CONNECT_TIMEOUT_SEC, timeout_sec)), max_retries=0)

    @property
    def max_input_tokens(self) -> int:
        """The most input a request may carry once room for the reply is set aside."""
        return input_limit(self.model_id, self.context_window_tokens, self.max_output_tokens) or 0

    @property
    def context_budget_tokens(self) -> int:
        """Estimated conversation size above which the runtime compacts old tool results: half of the
        usable window, which leaves room for what the next turn adds."""
        return self.max_input_tokens // 2

    def with_output_limit(self, max_output_tokens: int) -> "OpenAIAdapter":
        """This adapter with replies capped at `max_output_tokens` (never above its own cap).

        For small side requests, such as naming a conversation, whose worst-case cost should reflect the few
        tokens they can produce rather than a full answer. It shares the client, so the ledger, the pinned
        origin and the no-hidden-retries rule are unchanged; `self` is not modified.
        """
        if max_output_tokens < 1:
            raise ModelConfigurationError("an output limit must be positive")
        limited = copy.copy(self)
        limited.max_output_tokens = min(max_output_tokens, self.max_output_tokens)
        return limited

    def maximum_request_cost(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]):
        count = conservative_input_tokens(messages, tools)
        if count > self.max_input_tokens:
            raise ModelContextLimit("bounded model context is exhausted")
        # Character estimates are useful for fitting, but cannot cap the cost of arbitrary text.
        # A byte-level upper bound also covers dense code and multilingual inputs. Successful
        # requests cannot exceed the provider input limit; rejected requests generate no output.
        byte_bound = len(json.dumps({"messages": messages, "tools": tools}, ensure_ascii=False).encode("utf-8"))
        upper = min(self.max_input_tokens, byte_bound + PROTOCOL_OVERHEAD_TOKENS + PER_MESSAGE_OVERHEAD_TOKENS * len(messages))
        return self._rates_for(upper).cost(upper, self.max_output_tokens)

    def _rates_for(self, input_tokens: int) -> ModelRates:
        if is_luna(self.model_id) and input_tokens > LUNA_LONG_CONTEXT_THRESHOLD and self.long_context_rates:
            return self.long_context_rates
        return self.rates

    # --- request assembly -------------------------------------------------------------------
    def _request(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]],
                 tool_choice: dict[str, Any] | str | None) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self.model_id, "messages": cast(list[ChatCompletionMessageParam], messages),
            "max_completion_tokens": self.max_output_tokens, "store": False, "service_tier": "default"}
        if self.reasoning_effort is not None:
            kwargs["reasoning_effort"] = self.reasoning_effort
        if tools:
            kwargs.update(tools=cast(list[ChatCompletionToolParam], tools),
                          tool_choice=tool_choice or "auto", parallel_tool_calls=self.parallel_tool_calls)
        return kwargs

    # --- failure handling -------------------------------------------------------------------
    @staticmethod
    def _classify(exc: BaseException) -> ModelProviderError | ModelContextLimit:
        """Map an SDK failure to an error that carries the provider's own message (see `provider_detail`), so the
        person reads what the provider actually said. The request itself is never included."""
        if isinstance(exc, (ModelProviderError, ModelContextLimit)):
            return exc
        detail = provider_detail(exc)
        if isinstance(exc, openai.RateLimitError):
            if getattr(exc, "code", None) == "insufficient_quota":
                return ModelProviderError("model quota exhausted", code="MODEL_QUOTA_EXCEEDED", detail=detail)
            return ModelProviderError("model rate limited", code="RATE_LIMIT", retriable=True, detail=detail)
        if isinstance(exc, openai.BadRequestError) and getattr(exc, "code", None) == "context_length_exceeded":
            return ModelContextLimit(detail or "provider context window exceeded")
        if isinstance(exc, (openai.AuthenticationError, openai.PermissionDeniedError)):
            return ModelProviderError("model credentials were rejected", code="AUTH_ERROR", detail=detail)
        if isinstance(exc, openai.InternalServerError) or (
                isinstance(exc, openai.APIStatusError) and exc.status_code in (408, 409, 425, 502, 503, 504, 529)):
            return ModelProviderError("model provider unavailable", retriable=True, detail=detail)
        if isinstance(exc, openai.APITimeoutError):
            return ModelProviderError("model request timed out", retriable=True, detail=detail)
        if isinstance(exc, openai.APIConnectionError) or isinstance(exc, (httpx.TransportError, ConnectionError)):
            return ModelProviderError("model provider unreachable", retriable=True, detail=detail)
        if isinstance(exc, openai.APIError) and not isinstance(exc, (openai.APIStatusError, openai.APIResponseValidationError)):
            # An error frame inside a stream that had already answered 200 (`openai._streaming` raises it as a bare
            # APIError whose code and type come from the frame's `error` object): no status, so judge it by its kind.
            kinds = {getattr(exc, "code", None), getattr(exc, "type", None)} - {None}
            if "insufficient_quota" in kinds:
                return ModelProviderError("model quota exhausted", code="MODEL_QUOTA_EXCEEDED", detail=detail)
            if kinds & RATE_LIMIT_KINDS:
                return ModelProviderError("model rate limited", code="RATE_LIMIT", retriable=True, detail=detail)
            if not kinds or kinds & TRANSIENT_KINDS:
                return ModelProviderError("model provider unavailable", retriable=True, detail=detail)
        return ModelProviderError("model provider request failed", detail=detail)

    @staticmethod
    def _retry_after(exc: BaseException) -> float | None:
        cause = exc.__cause__ if isinstance(exc, _StreamBroke) else exc
        try:
            headers = cause.response.headers  # type: ignore[attr-defined]
            if headers.get("retry-after-ms") is not None:
                return float(headers["retry-after-ms"]) / 1000
            value = headers.get("retry-after")
            return float(value) if value is not None else None
        except Exception:
            return None

    async def _with_retries(self, call: Callable[[], Awaitable[ModelResponse]],
                            on_retry: Callable[[], None] | None = None,
                            on_attempt_failed: Callable[[str], None] | None = None) -> ModelResponse:
        for attempt in range(self.max_retries + 1):
            try:
                return await call()
            except ModelContextLimit:
                raise
            except Exception as exc:
                error = self._classify(exc.__cause__ if isinstance(exc, _StreamBroke) else exc)
                if not isinstance(error, ModelProviderError) or not error.retriable or attempt >= self.max_retries:
                    raise error from None
                if isinstance(exc, _StreamBroke) and on_retry is not None:
                    try:
                        on_retry()  # what the broken attempt showed is void
                    except Exception:
                        pass
                if on_attempt_failed is not None:
                    try:
                        on_attempt_failed(error.code)  # a request failed and is about to be sent again (tracing counts these)
                    except Exception:
                        pass
                wait = self._retry_after(exc)
                if wait is None:
                    # Equal jitter: at least half the exponential step, so retries back off, and a random rest, so
                    # requests that failed together do not all come back together.
                    base = BASE_RETRY_WAIT_SEC * (2 ** attempt)
                    wait = base / 2 + random.uniform(0, base / 2)
                await self._sleep(min(max(wait, 0.0), MAX_RETRY_WAIT_SEC))
        raise ModelProviderError("model provider request failed")  # unreachable; keeps the type checker honest

    # --- completion -------------------------------------------------------------------------
    async def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]],
                       on_text: Callable[[str], None] | None = None,
                       tool_choice: dict[str, Any] | str | None = None,
                       on_narration: Callable[[str], None] | None = None,
                       on_retry: Callable[[], None] | None = None,
                       on_tool: Callable[[int, str, str, str], None] | None = None,
                       on_attempt_failed: Callable[[str], None] | None = None) -> ModelResponse:
        """One completion. With on_text, the person-facing reply is delivered as it arrives; with on_narration,
        so is the plain text the model writes next to its tool calls; with on_tool, so is each tool call as the model
        writes it: `(index, call id, tool name, next piece of its JSON arguments)`, in the order the provider sends them.
        The returned response (message, usage, finish reason) is identical to the non-streaming path. `on_retry` runs
        before a broken stream is requested again, so the caller can clear what that attempt showed. `on_attempt_failed` is told
        the error code of every failed request that is about to be retried (rate limit, 5xx, timeout, broken stream)."""
        self.maximum_request_cost(messages, tools)
        request = self._request(messages, tools, tool_choice)
        if on_text is not None or on_narration is not None or on_tool is not None:
            return await self._with_retries(lambda: self._complete_streaming(request, bool(tools), on_text, on_narration, on_tool), on_retry, on_attempt_failed)
        return await self._with_retries(lambda: self._complete_once(request), None, on_attempt_failed)

    async def _complete_once(self, request: dict[str, Any]) -> ModelResponse:
        response = await self._client.chat.completions.create(**request)
        if not response.choices:
            raise ModelProviderError("model provider returned no completion")
        choice = response.choices[0]
        message = choice.message.model_dump(exclude_none=True)
        # Only documented replay fields are sent back. Never include parsed,
        # refusal metadata, audio, annotations or provider-specific extras.
        message = {key: message[key] for key in ("role", "content", "tool_calls") if key in message}
        usage = response.usage
        if usage is None:
            measured = ModelUsage(0, 0, None)
        else:
            measured = ModelUsage(usage.prompt_tokens, usage.completion_tokens,
                                  self._rates_for(usage.prompt_tokens).cost(usage.prompt_tokens, usage.completion_tokens))
        return ModelResponse(message, measured, response.model, choice.finish_reason)

    async def _complete_streaming(self, request: dict[str, Any], has_tools: bool,
                                  on_text: Callable[[str], None] | None,
                                  on_narration: Callable[[str], None] | None = None,
                                  on_tool: Callable[[int, str, str, str], None] | None = None) -> ModelResponse:
        parts: list[str] = []
        calls: dict[int, dict[str, Any]] = {}
        finish_reason: str | None = None
        usage = None
        reported_model = self.model_id
        stream = None
        received = False

        def show(*piece, sink: Callable[..., None] | None = on_text) -> None:
            if sink is None:
                return
            try:
                sink(*piece)
            except Exception:
                pass  # Showing progress must never fail the model call.

        try:
            stream = await self._client.chat.completions.create(
                **request, stream=True, stream_options={"include_usage": True})
            async for chunk in stream:
                if getattr(chunk, "usage", None) is not None:
                    usage = chunk.usage
                if getattr(chunk, "model", None):
                    reported_model = chunk.model
                if not chunk.choices:
                    continue
                received = True
                choice = chunk.choices[0]
                delta = choice.delta
                if delta is not None and delta.content:
                    parts.append(delta.content)
                    # With tools offered, content is narration written next to the calls, not the reply.
                    show(delta.content, sink=on_narration if has_tools else on_text)
                for call in (delta.tool_calls or []) if delta is not None else []:
                    slot = calls.setdefault(call.index, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                    if call.id:
                        slot["id"] = call.id
                    piece = ""
                    if call.function is not None:
                        slot["function"]["name"] += call.function.name or ""
                        piece = call.function.arguments or ""
                        slot["function"]["arguments"] += piece
                    show(call.index, slot["id"], slot["function"]["name"], piece, sink=on_tool)
                if choice.finish_reason:
                    finish_reason = choice.finish_reason
        except Exception as exc:
            if received and not isinstance(exc, (ModelProviderError, ModelContextLimit)):
                raise _StreamBroke() from exc
            raise self._classify(exc) from None
        finally:
            if stream is not None:
                try:
                    await stream.close()
                except Exception:
                    pass
        if finish_reason is None:
            if received:
                # The stream closed cleanly but cut short: what it showed is void, so it is retried like one that broke.
                raise _StreamBroke() from ModelProviderError(
                    "model provider stream ended before the reply finished", retriable=True, detail="no finish_reason")
            raise ModelProviderError("model provider returned no completion")
        content = "".join(parts)
        message: dict[str, Any] = {"role": "assistant"}
        if content or not calls:
            message["content"] = content
        if calls:
            message["tool_calls"] = [calls[index] for index in sorted(calls)]
        if usage is None:
            measured = ModelUsage(0, 0, None)
        else:
            measured = ModelUsage(usage.prompt_tokens, usage.completion_tokens,
                                  self._rates_for(usage.prompt_tokens).cost(usage.prompt_tokens, usage.completion_tokens))
        return ModelResponse(message, measured, reported_model, finish_reason)

    async def aclose(self) -> None:
        await self._client.close()
