"""Configuration presence and worker heartbeat, not live provider qualification."""

from fastapi import APIRouter, Depends, Request

from agentforge.api.dependencies import sessions
from agentforge.auth.session import current_user
from agentforge.database.models import User
from agentforge.repositories.workers import worker_presence

router = APIRouter(prefix="/api/v1/configuration", tags=["configuration"])


@router.get("")
def configuration(request: Request, user: User = Depends(current_user)) -> dict:
    settings = request.app.state.settings
    required = {
        "LLM_MODEL": bool(settings.llm_model),
        "LLM_API_KEY": bool(settings.llm_api_key),
        "LLM_CONTEXT_WINDOW_TOKENS": settings.llm_context_window_tokens is not None,
        "LLM_INPUT_USD_PER_MILLION": settings.llm_input_usd_per_million is not None,
        "LLM_OUTPUT_USD_PER_MILLION": settings.llm_output_usd_per_million is not None,
        "LLM_SUPPORTS_TOOLS=true": settings.llm_supports_tools,
        "LLM_SUPPORTS_JSON=true": settings.llm_supports_json,
        "TAVILY_API_KEY": bool(settings.tavily_api_key),
    }
    from agentforge.llm.limits import LUNA_LONG_CONTEXT_THRESHOLD, LUNA_WINDOW, input_limit, is_luna
    available = input_limit(settings.llm_model, settings.llm_context_window_tokens,
                            getattr(settings, "llm_max_output_tokens", 8192))
    required["LLM_CONTEXT_WINDOW_TOKENS"] = bool(available) and (
        not is_luna(settings.llm_model) or settings.llm_context_window_tokens <= LUNA_WINDOW)
    if is_luna(settings.llm_model):
        required["LLM_REASONING_EFFORT=none"] = getattr(settings, "llm_reasoning_effort", None) == "none"
    if is_luna(settings.llm_model) and available and available > LUNA_LONG_CONTEXT_THRESHOLD:
        required.update({
            "LLM_LONG_CONTEXT_INPUT_USD_PER_MILLION": getattr(settings, "llm_long_context_input_usd_per_million", None) is not None,
            "LLM_LONG_CONTEXT_OUTPUT_USD_PER_MILLION": getattr(settings, "llm_long_context_output_usd_per_million", None) is not None,
        })
    missing = [name for name, present in required.items() if not present]
    general_missing = [name for name, present in required.items() if name != "TAVILY_API_KEY" and not present]
    coding_required = {name: present for name, present in required.items() if name != "TAVILY_API_KEY"}
    coding_required["E2B_API_KEY"] = bool(getattr(settings, "e2b_api_key", None))
    coding_missing = [name for name, present in coding_required.items() if not present]
    return {"research_configured": not missing, "missing_settings": missing,
            "conversation_queue_enabled": getattr(settings, "conversation_queue_enabled", True),
            "general_configured": not general_missing, "general_missing_settings": general_missing,
            "general_capabilities": ["text_attachment_reads", "task_owned_text_artifacts"] + (["web_research"] if settings.tavily_api_key else [])
                + (["code_sandbox"] if settings.e2b_api_key else []),
            "general_execution": "agent", "general_sandbox_execution": "e2b" if settings.e2b_api_key else "unsupported",
            "configured_model": settings.llm_model or None, "configured_provider": getattr(settings, "llm_provider", "openai"),
            "per_task_cost_limit_usd": None,
            "coding_configured": not coding_missing, "coding_missing_settings": coding_missing,
            "coding_repository_access": "github_app_user_installation" if getattr(settings, "auth_mode", None) == "github_app" else "public_only", "coding_sandbox_provider": "e2b",
            "tracing_configured": bool(settings.neatlogs_api_key),
            "web_research_configured": bool(settings.tavily_api_key),
            "github_writes_enabled": bool(getattr(settings, "enable_github_writes", False)),
            "auth_mode": getattr(settings, "auth_mode", None),
            # Whether anything will pick up a queued message: a worker said it was alive in the last 30 seconds.
            # The session is opened inside worker_presence, so an unreachable database reads as offline.
            "worker": worker_presence(lambda: sessions(request)()),
            "qualification": "Configuration presence and worker heartbeat only; providers have not been checked."}
