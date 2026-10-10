"""AsyncOpenAI Chat Completions adapter with measured usage and bounded, cost-safe retries.

Official contracts reviewed:
https://developers.openai.com/api/reference/python
https://developers.openai.com/api/reference/python/resources/chat/subresources/completions/methods/create

The SDK's own retries stay disabled so no hidden request bypasses the ledger. This adapter retries
only failures that produced no completion (rate limits, 5xx, failed connections), a bounded number
of times with jittered backoff, because such a request was not billed and the run can continue.
"""

import asyncio
import random
from collections.abc import Awaitable, Callable
from typing import Any, cast

import openai
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam, ChatCompletionToolParam

from .models import (
    SUBMIT_RESULT, ModelConfigurationError, ModelContextLimit, ModelProviderError,
    ModelRates, ModelResponse, ModelUsage, conservative_input_tokens,
)

MAX_RETRY_WAIT_SEC = 8.0
BASE_RETRY_WAIT_SEC = 0.5


class OpenAIAdapter:
    def __init__(self, *, api_key: str, model_id: str, rates: ModelRates,
                 context_window_tokens: int, max_output_tokens: int,
                 timeout_sec: float = 60, reasoning_effort: str | None = None,
                 parallel_tool_calls: bool = True, strict_tools: bool = False,
                 max_retries: int = 2, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
        if not api_key or not model_id or context_window_tokens <= max_output_tokens or max_output_tokens < 1:
            raise ModelConfigurationError("explicit model, API key and valid token limits are required")
        if not 0 <= max_retries <= 5:
            raise ModelConfigurationError("model retries must be between 0 and 5")
        self.model_id = model_id
        self.rates = rates
        self.context_window_tokens = context_window_tokens
        self.max_output_tokens = max_output_tokens
        self.reasoning_effort = reasoning_effort
        self.parallel_tool_calls = parallel_tool_calls
        self.strict_tools = strict_tools
        self.max_retries = max_retries
        self._sleep = sleep
        if model_id.startswith("gpt-6-luna"):
            if reasoning_effort != "none":
                raise ModelConfigurationError("gpt-6-luna Chat Completions tool calls require reasoning_effort=none")
            if context_window_tokens > 262144:
                raise ModelConfigurationError("gpt-6-luna configured pricing requires short-context cap <=262144")
        # Pin the official origin; user/model data and ambient OPENAI_BASE_URL
        # cannot redirect credentials. No hidden SDK retry may bypass the ledger.
        self._client = AsyncOpenAI(api_key=api_key, base_url="https://api.openai.com/v1",
                                   timeout=timeout_sec, max_retries=0)

    @property
    def context_budget_bytes(self) -> int:
        """Conversation size, in the same conservative units as maximum_request_cost, above which the
        runtime compacts old tool results. Half the usable window leaves room for the reply."""
        return max(0, self.context_window_tokens - self.max_output_tokens) // 2

    def maximum_request_cost(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]):
        count = conservative_input_tokens(messages, tools)
        if count + self.max_output_tokens > self.context_window_tokens:
            raise ModelContextLimit("bounded model context is exhausted")
        return self.rates.cost(count, self.max_output_tokens)

    # --- request assembly -------------------------------------------------------------------
    def _request(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]],
                 tool_choice: dict[str, Any] | str | None) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self.model_id, "messages": cast(list[ChatCompletionMessageParam], messages),
            "max_completion_tokens": self.max_output_tokens, "store": False, "service_tier": "default"}
        if self.reasoning_effort is not None:
            kwargs["reasoning_effort"] = self.reasoning_effort
        if tools:
            # With tools offered the model answers by calling a tool (finally submit_result), so JSON
            # mode would only push it to write JSON text instead of calling one.
            kwargs.update(tools=cast(list[ChatCompletionToolParam], tools),
                          tool_choice=tool_choice or "auto", parallel_tool_calls=self.parallel_tool_calls)
        else:
            kwargs["response_format"] = {"type": "json_object"}
        return kwargs

    # --- failure handling -------------------------------------------------------------------
    @staticmethod
    def _classify(exc: BaseException, *, before_output: bool = True) -> ModelProviderError | ModelContextLimit:
        """Map an SDK failure to a safe error. Raw SDK errors may include request data, so only
        the error class and a few stable fields are consulted, never the message."""
        if isinstance(exc, (ModelProviderError, ModelContextLimit)):
            return exc
        if isinstance(exc, openai.RateLimitError):
            if getattr(exc, "code", None) == "insufficient_quota":
                return ModelProviderError("model quota exhausted", code="MODEL_QUOTA_EXCEEDED")
            return ModelProviderError("model rate limited", code="RATE_LIMIT", retriable=before_output)
        if isinstance(exc, openai.BadRequestError) and getattr(exc, "code", None) == "context_length_exceeded":
            return ModelContextLimit("provider context window exceeded")
        if isinstance(exc, (openai.AuthenticationError, openai.PermissionDeniedError)):
            return ModelProviderError("model credentials were rejected", code="AUTH_ERROR")
        if isinstance(exc, openai.InternalServerError):
            return ModelProviderError("model provider unavailable", retriable=before_output)
        if isinstance(exc, openai.APITimeoutError):
            # The provider may have started (and billed) generation, so a timeout is not repeated.
            return ModelProviderError("model request timed out")
        if isinstance(exc, openai.APIConnectionError):
            return ModelProviderError("model provider unreachable", retriable=before_output)
        return ModelProviderError("model provider request failed")

    @staticmethod
    def _retry_after(exc: BaseException) -> float | None:
        try:
            value = exc.response.headers.get("retry-after")  # type: ignore[attr-defined]
            return float(value) if value is not None else None
        except Exception:
            return None

    async def _with_retries(self, call: Callable[[], Awaitable[ModelResponse]]) -> ModelResponse:
        for attempt in range(self.max_retries + 1):
            try:
                return await call()
            except ModelContextLimit:
                raise
            except Exception as exc:
                error = self._classify(exc)
                if not isinstance(error, ModelProviderError) or not error.retriable or attempt >= self.max_retries:
                    raise error from None
                wait = self._retry_after(exc)
                if wait is None:
                    wait = BASE_RETRY_WAIT_SEC * (2 ** attempt) + random.uniform(0, 0.25)
                await self._sleep(min(max(wait, 0.0), MAX_RETRY_WAIT_SEC))
        raise ModelProviderError("model provider request failed")  # unreachable; keeps the type checker honest

    # --- completion -------------------------------------------------------------------------
    async def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]],
                       on_text: Callable[[str], None] | None = None,
                       tool_choice: dict[str, Any] | str | None = None) -> ModelResponse:
        """One completion. With on_text, the person-facing reply is delivered as it arrives; the
        returned response (message, usage, finish reason) is identical to the non-streaming path."""
        self.maximum_request_cost(messages, tools)
        request = self._request(messages, tools, tool_choice)
        if on_text is not None:
            return await self._with_retries(lambda: self._complete_streaming(request, bool(tools), on_text))
        return await self._with_retries(lambda: self._complete_once(request))

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
                                  self.rates.cost(usage.prompt_tokens, usage.completion_tokens))
        return ModelResponse(message, measured, response.model, choice.finish_reason)

    async def _complete_streaming(self, request: dict[str, Any], has_tools: bool,
                                  on_text: Callable[[str], None]) -> ModelResponse:
        parts: list[str] = []
        calls: dict[int, dict[str, Any]] = {}
        finish_reason: str | None = None
        usage = None
        reported_model = self.model_id
        stream = None
        received = False

        def show(text: str) -> None:
            try:
                on_text(text)
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
                    if not has_tools:
                        show(delta.content)  # with tools offered, content is narration, not the reply
                for call in (delta.tool_calls or []) if delta is not None else []:
                    slot = calls.setdefault(call.index, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                    if call.id:
                        slot["id"] = call.id
                    if call.function is not None:
                        slot["function"]["name"] += call.function.name or ""
                        slot["function"]["arguments"] += call.function.arguments or ""
                        if call.function.arguments and slot["function"]["name"] == SUBMIT_RESULT:
                            show(call.function.arguments)  # the typed result is the reply envelope
                if choice.finish_reason:
                    finish_reason = choice.finish_reason
        except Exception as exc:
            # Once output has started the request may be billed, so it is no longer safe to repeat.
            raise self._classify(exc, before_output=not received) from None
        finally:
            if stream is not None:
                try:
                    await stream.close()
                except Exception:
                    pass
        if finish_reason is None:
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
                                  self.rates.cost(usage.prompt_tokens, usage.completion_tokens))
        return ModelResponse(message, measured, reported_model, finish_reason)

    async def aclose(self) -> None:
        await self._client.close()
