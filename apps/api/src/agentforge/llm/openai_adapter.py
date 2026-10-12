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

Reasoning effort is chosen per person and applied per run (`with_reasoning_effort`). Without reasoning the Chat
Completions endpoint serves every request, exactly as it always did. A model whose Chat Completions endpoint takes function
calls only without reasoning (gpt-6-luna, see `effort.py`) is sent through the Responses endpoint when an effort is asked
for. The runtime keeps speaking the chat message format either way: this adapter translates the history it is given into
Responses input and the reply back into one assistant message, so nothing above it changes.
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
from .effort import EFFORTS, efforts_for, read_timeout, support_for, uses_responses
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


# --- chat format <-> Responses format ---------------------------------------------------------
# The runtime's history and tools are Chat Completions shaped. These are the only places that know the Responses shape.
RESPONSES_URL = "https://api.openai.com/v1/responses"


def _plain_text(content: Any) -> str:
    """The text of a message's content: a string, or the text parts of a list."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(part.get("text", "") for part in content if isinstance(part, dict) and part.get("type") == "text")
    return ""


def _input_part(part: dict[str, Any]) -> dict[str, Any]:
    kind = part.get("type")
    if kind == "text":
        return {"type": "input_text", "text": part.get("text", "")}
    if kind == "image_url":
        image = part.get("image_url")
        image = image if isinstance(image, dict) else {"url": image}
        return {"type": "input_image", "image_url": image.get("url", ""), "detail": image.get("detail", "auto")}
    if kind == "file":
        file = part.get("file") or {}
        return {"type": "input_file", **{key: file[key] for key in ("filename", "file_data", "file_id") if file.get(key)}}
    raise ModelConfigurationError(f"a {kind!r} message part cannot be sent to the Responses endpoint")


def to_responses_input(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A chat history as Responses `input` items: an assistant's tool calls and their results become `function_call` and
    `function_call_output` items tied by the call's id. Items carry no provider `id`, so nothing refers to a stored response."""
    items: list[dict[str, Any]] = []
    for message in messages:
        role, content = message.get("role"), message.get("content")
        if role == "tool":
            items.append({"type": "function_call_output", "call_id": message["tool_call_id"], "output": _plain_text(content)})
        elif role == "assistant":
            text = _plain_text(content)
            if text.strip():
                items.append({"role": "assistant", "content": text})
            for call in message.get("tool_calls") or []:
                function = call["function"]
                items.append({"type": "function_call", "call_id": call["id"], "name": function["name"], "arguments": function.get("arguments") or "{}"})
        elif role == "user" and isinstance(content, list):
            items.append({"role": "user", "content": [_input_part(part) for part in content]})
        elif role in ("system", "developer"):
            items.append({"role": "developer", "content": _plain_text(content)})
        else:
            items.append({"role": role, "content": content if isinstance(content, str) else _plain_text(content)})
    return items


def to_responses_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Chat function tools as Responses function tools. `strict` is always stated: the Responses endpoint turns it on when it
    is left out, which would reject any schema that is not written for strict mode."""
    converted = []
    for tool in tools:
        function = tool.get("function") if tool.get("type") == "function" else None
        if function is None:
            converted.append(tool)
            continue
        item = {"type": "function", "name": function["name"], "parameters": function.get("parameters") or {"type": "object", "properties": {}},
                "strict": bool(function.get("strict", False))}
        if function.get("description"):
            item["description"] = function["description"]
        converted.append(item)
    return converted


def to_responses_tool_choice(choice: dict[str, Any] | str | None) -> dict[str, Any] | str:
    if isinstance(choice, dict) and isinstance(choice.get("function"), dict):
        return {"type": "function", "name": choice["function"]["name"]}
    return choice or "auto"


def message_from_response(response: Any) -> tuple[dict[str, Any], str]:
    """The reply of a finished Responses call as one chat-format assistant message, and its finish reason.

    Reasoning items are not kept: nothing above this adapter could store them, and a request that carries none is valid.
    """
    if response.status == "failed":
        raise _response_error(getattr(response, "error", None))
    if response.status not in ("completed", "incomplete"):
        raise ModelProviderError("model request did not finish", retriable=True, detail=f"response status: {response.status}")
    parts: list[str] = []
    calls: list[dict[str, Any]] = []
    for item in response.output or []:
        if item.type == "message":
            for piece in item.content:
                text = getattr(piece, "text", None) if piece.type == "output_text" else getattr(piece, "refusal", None)
                if text:
                    parts.append(text)
        elif item.type == "function_call":
            calls.append({"id": item.call_id, "type": "function", "function": {"name": item.name, "arguments": item.arguments}})
    content = "".join(parts)
    if response.status == "incomplete":
        reason = getattr(response.incomplete_details, "reason", None)
        if reason == "content_filter":
            finish = "content_filter"
        elif not content and not calls:
            # Everything the model was allowed to write went into thinking: there is nothing to continue from.
            raise ModelProviderError("model used its whole output allowance thinking",
                                     detail="The model used its whole output allowance thinking and wrote no answer. Choose a lower effort and try again.")
        else:
            finish = "length"
    else:
        finish = "tool_calls" if calls else "stop"
    message: dict[str, Any] = {"role": "assistant"}
    if content or not calls:
        message["content"] = content
    if calls:
        message["tool_calls"] = calls
    return message, finish


def _response_error(error: Any) -> Exception:
    """A failed response (or an `error` event) as the error the adapter already knows how to classify and retry."""
    code = getattr(error, "code", None)
    text = getattr(error, "message", None) or "the model provider reported an error"
    if code == "context_length_exceeded":
        return ModelContextLimit(text)
    return openai.APIError(text, httpx.Request("POST", RESPONSES_URL), body={"code": code, "message": text})


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
        self.reasoning_effort = self._checked_effort(model_id, reasoning_effort)
        self.timeout_sec = timeout_sec
        self.parallel_tool_calls = parallel_tool_calls
        self.strict_tools = strict_tools
        self.max_retries = max_retries
        self._sleep = sleep
        if is_luna(model_id):
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

    @staticmethod
    def _checked_effort(model_id: str, effort: str | None) -> str | None:
        if effort is None:
            return None
        if effort not in EFFORTS or (support_for(model_id) and effort not in efforts_for(model_id)):
            raise ModelConfigurationError(f"{model_id} does not take reasoning effort {effort!r}")
        return effort

    def with_reasoning_effort(self, effort: str | None) -> "OpenAIAdapter":
        """This adapter thinking at `effort` (None: the provider's own default); `self` is not modified.

        It shares the client, so the ledger, the pinned origin and the no-hidden-retries rule are unchanged.
        """
        chosen = self._checked_effort(self.model_id, effort)
        if chosen == self.reasoning_effort:
            return self
        changed = copy.copy(self)
        changed.reasoning_effort = chosen
        return changed

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
        # A few tokens of output cannot hold any thinking, and a title or a summary gains nothing from it.
        if support_for(self.model_id) is not None:
            limited.reasoning_effort = "none"
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
        # gpt-6-luna reasons by default, and its Chat Completions endpoint takes function calls only without reasoning:
        # whatever effort is asked for, a request that reaches this endpoint says none (asked-for efforts use `_responses`).
        effort = "none" if is_luna(self.model_id) else self.reasoning_effort
        if effort is not None:
            kwargs["reasoning_effort"] = effort
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
        if uses_responses(self.model_id, self.reasoning_effort):
            request = self._responses_request(messages, tools, tool_choice)
            if on_text is not None or on_narration is not None or on_tool is not None:
                return await self._with_retries(lambda: self._responses_streaming(request, bool(tools), on_text, on_narration, on_tool), on_retry, on_attempt_failed)
            return await self._with_retries(lambda: self._responses_once(request), None, on_attempt_failed)
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

    # --- the Responses endpoint -------------------------------------------------------------
    def _responses_request(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]],
                           tool_choice: dict[str, Any] | str | None) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self.model_id, "input": to_responses_input(messages), "max_output_tokens": self.max_output_tokens,
            "reasoning": {"effort": self.reasoning_effort}, "store": False, "service_tier": "default",
            # A model that thinks longer says nothing for longer before its first token.
            "timeout": httpx.Timeout(read_timeout(self.timeout_sec, self.reasoning_effort), connect=min(CONNECT_TIMEOUT_SEC, self.timeout_sec))}
        if tools:
            kwargs.update(tools=to_responses_tools(tools), tool_choice=to_responses_tool_choice(tool_choice),
                          parallel_tool_calls=self.parallel_tool_calls)
        return kwargs

    def _responses_result(self, response: Any, reported_model: str | None = None) -> ModelResponse:
        message, finish = message_from_response(response)
        usage = response.usage
        if usage is None:
            measured = ModelUsage(0, 0, None)
        else:
            measured = ModelUsage(usage.input_tokens, usage.output_tokens,
                                  self._rates_for(usage.input_tokens).cost(usage.input_tokens, usage.output_tokens))
        return ModelResponse(message, measured, getattr(response, "model", None) or reported_model or self.model_id, finish)

    async def _responses_once(self, request: dict[str, Any]) -> ModelResponse:
        try:
            response = await self._client.responses.create(**request)
        except Exception as exc:
            raise self._classify(exc) from None
        return self._responses_result(response)

    async def _responses_streaming(self, request: dict[str, Any], has_tools: bool,
                                   on_text: Callable[[str], None] | None,
                                   on_narration: Callable[[str], None] | None = None,
                                   on_tool: Callable[[int, str, str, str], None] | None = None) -> ModelResponse:
        final = None
        received = False
        stream = None
        slots: dict[int, tuple[int, str, str]] = {}  # output index -> (tool index, call id, name)

        def show(*piece, sink: Callable[..., None] | None = on_text) -> None:
            if sink is None:
                return
            try:
                sink(*piece)
            except Exception:
                pass  # Showing progress must never fail the model call.

        try:
            stream = await self._client.responses.create(**request, stream=True)
            async for event in stream:
                kind = event.type
                if kind in ("response.output_text.delta", "response.refusal.delta"):
                    received = True
                    # With tools offered, text is narration written next to the calls, not the reply.
                    show(event.delta, sink=on_narration if has_tools else on_text)
                elif kind == "response.output_item.added" and event.item.type == "function_call":
                    received = True
                    slots[event.output_index] = (len(slots), event.item.call_id, event.item.name)
                    show(*slots[event.output_index], "", sink=on_tool)
                elif kind == "response.function_call_arguments.delta" and event.output_index in slots:
                    index, call_id, name = slots[event.output_index]
                    show(index, call_id, name, event.delta, sink=on_tool)
                elif kind in ("response.completed", "response.incomplete"):
                    final = event.response
                elif kind == "response.failed":
                    raise _response_error(getattr(event.response, "error", None))
                elif kind == "error":
                    raise _response_error(event)
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
        if final is None:
            if received:
                # The stream closed cleanly but cut short: what it showed is void, so it is retried like one that broke.
                raise _StreamBroke() from ModelProviderError(
                    "model provider stream ended before the reply finished", retriable=True, detail="no completed event")
            raise ModelProviderError("model provider returned no completion")
        return self._responses_result(final)

    async def aclose(self) -> None:
        await self._client.close()
