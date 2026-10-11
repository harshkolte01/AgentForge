"""A single explicit provider/model serves all Phase 2 role profiles."""

from agentforge.settings import Settings

from .models import ModelConfigurationError, ModelRates, ToolCallingModel
from .openai_adapter import OpenAIAdapter


class ModelRouter:
    def __init__(self, model: ToolCallingModel) -> None:
        self.model = model

    def choose(self, profile: str) -> ToolCallingModel:
        if profile not in ("default", "research", "coding", "review"):
            raise ModelConfigurationError("unknown model profile")
        return self.model

    @classmethod
    def from_settings(cls, settings: Settings) -> "ModelRouter":
        if settings.llm_provider != "openai":
            raise ModelConfigurationError("only the explicit OpenAI adapter is installed")
        if not settings.llm_model or settings.llm_api_key is None or settings.llm_context_window_tokens is None:
            raise ModelConfigurationError("LLM_MODEL, LLM_API_KEY and LLM_CONTEXT_WINDOW_TOKENS are required")
        if settings.llm_input_usd_per_million is None or settings.llm_output_usd_per_million is None:
            raise ModelConfigurationError("explicit model input and output rates are required")
        if not settings.llm_supports_tools or not settings.llm_supports_json:
            raise ModelConfigurationError("operator must verify configured model tool calling and JSON support")
        return cls(OpenAIAdapter(
            api_key=settings.llm_api_key.get_secret_value(), model_id=settings.llm_model,
            rates=ModelRates(settings.llm_input_usd_per_million, settings.llm_output_usd_per_million),
            long_context_rates=(ModelRates(settings.llm_long_context_input_usd_per_million,
                                           settings.llm_long_context_output_usd_per_million)
                if settings.llm_long_context_input_usd_per_million is not None
                and settings.llm_long_context_output_usd_per_million is not None else None),
            context_window_tokens=settings.llm_context_window_tokens,
            # A small window keeps at least half of itself for input, whatever the output setting says.
            max_output_tokens=min(settings.llm_max_output_tokens, settings.llm_context_window_tokens // 2),
            timeout_sec=settings.llm_timeout_sec,
            reasoning_effort=settings.llm_reasoning_effort,
            parallel_tool_calls=settings.llm_parallel_tool_calls, strict_tools=settings.llm_strict_tools,
            max_retries=settings.llm_max_retries,
        ))
