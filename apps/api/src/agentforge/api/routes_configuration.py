"""Configuration presence, not live provider or worker qualification."""

from fastapi import APIRouter, Depends, Request

from agentforge.auth.session import current_user
from agentforge.database.models import User

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
    missing = [name for name, present in required.items() if not present]
    coding_required = {name: present for name, present in required.items() if name != "TAVILY_API_KEY"}
    coding_required["E2B_API_KEY"] = bool(getattr(settings, "e2b_api_key", None))
    coding_required["E2B_TEMPLATE_ID"] = bool(getattr(settings, "e2b_template_id", None))
    coding_required["E2B_TEMPLATE_QUALIFIED=true"] = bool(getattr(settings, "e2b_template_qualified", False))
    coding_missing = [name for name, present in coding_required.items() if not present]
    return {"research_configured": not missing, "missing_settings": missing,
            "coding_configured": not coding_missing, "coding_missing_settings": coding_missing,
            "coding_repository_access": "public_only", "coding_sandbox_provider": "e2b",
            "tracing_configured": bool(settings.neatlogs_api_key),
            "worker_status": "not_observed", "checkpointer_status": "not_observed",
            "qualification": "Configuration presence only; providers and worker have not been checked."}
