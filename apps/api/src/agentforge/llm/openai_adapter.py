"""AsyncOpenAI Chat Completions adapter with retries disabled and measured usage.

Official contracts reviewed:
https://developers.openai.com/api/reference/python
https://developers.openai.com/api/reference/python/resources/chat/subresources/completions/methods/create
"""

from typing import Any, cast

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam, ChatCompletionToolParam

from .models import (
    ModelConfigurationError, ModelContextLimit, ModelProviderError,
    ModelRates, ModelResponse, ModelUsage, conservative_input_tokens,
)


class OpenAIAdapter:
    def __init__(self, *, api_key: str, model_id: str, rates: ModelRates,
                 context_window_tokens: int, max_output_tokens: int,
                 timeout_sec: float = 60) -> None:
        if not api_key or not model_id or context_window_tokens <= max_output_tokens or max_output_tokens < 1:
            raise ModelConfigurationError("explicit model, API key and valid token limits are required")
        self.model_id = model_id
        self.rates = rates
        self.context_window_tokens = context_window_tokens
        self.max_output_tokens = max_output_tokens
        # Pin the official origin; user/model data and ambient OPENAI_BASE_URL
        # cannot redirect credentials. No hidden SDK retry may bypass the ledger.
        self._client = AsyncOpenAI(api_key=api_key, base_url="https://api.openai.com/v1",
                                   timeout=timeout_sec, max_retries=0)

    def maximum_request_cost(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]):
        count = conservative_input_tokens(messages, tools)
        if count + self.max_output_tokens > self.context_window_tokens:
            raise ModelContextLimit("bounded model context is exhausted")
        return self.rates.cost(count, self.max_output_tokens)

    async def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ModelResponse:
        self.maximum_request_cost(messages, tools)
        try:
            # JSON mode is additionally validated against the specialized schema
            # locally. A schema failure gets at most one formatting-only retry.
            kwargs: dict[str, Any] = {}
            if tools:
                kwargs.update(tools=cast(list[ChatCompletionToolParam], tools),
                              tool_choice="auto", parallel_tool_calls=False)
            response = await self._client.chat.completions.create(
                model=self.model_id,
                messages=cast(list[ChatCompletionMessageParam], messages),
                max_completion_tokens=self.max_output_tokens,
                response_format={"type": "json_object"}, store=False, **kwargs,
            )
        except Exception:
            # Raw SDK errors may include request data. The caller conservatively
            # records unknown usage for any request without a confirmed response.
            raise ModelProviderError("model provider request failed") from None
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

    async def aclose(self) -> None:
        await self._client.close()
