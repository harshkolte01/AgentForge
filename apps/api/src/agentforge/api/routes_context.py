"""Context window status for the composer; reads stored usage only, never calls a provider."""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from agentforge.api.dependencies import sessions
from agentforge.auth.session import current_user
from agentforge.contracts.context import ContextStatus
from agentforge.database.models import User
from agentforge.repositories.sessions import context_budget_chars, SessionRepository
from agentforge.repositories.tasks import TaskNotFound
from agentforge.subagents.context_status import EMPTY_USAGE, build_status

router = APIRouter(prefix="/api/v1/context", tags=["context"])


@router.get("", response_model=ContextStatus)
async def context_status(request: Request, session_id: UUID | None = None, user: User = Depends(current_user)):
    """With no session_id, the starting context of a conversation that does not exist yet."""
    settings = request.app.state.settings
    budget = context_budget_chars(settings)
    usage = EMPTY_USAGE
    if session_id is not None:
        try:
            usage = await run_in_threadpool(SessionRepository(sessions(request), context_budget=budget).context_usage, session_id, user.id)
        except TaskNotFound:
            raise HTTPException(404, "session_not_found") from None
    extra = 0
    if getattr(settings, "plugins_enabled", False):
        from agentforge.plugins.runtime import estimated_tokens
        from agentforge.plugins.service import connections_for_conversation
        extra = estimated_tokens(await run_in_threadpool(connections_for_conversation, sessions(request), user.id, session_id))
    return build_status(model=settings.llm_model or None, provider=getattr(settings, "llm_provider", "openai"),
        window=settings.llm_context_window_tokens, max_output=settings.llm_max_output_tokens,
        usage=usage, history_budget_chars=budget, extra_baseline=extra)
