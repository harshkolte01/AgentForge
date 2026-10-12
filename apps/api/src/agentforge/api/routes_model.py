"""The person's reasoning effort for the model: what it offers, what is in force, and changing it."""
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool

from agentforge.api.dependencies import sessions
from agentforge.auth.session import current_user
from agentforge.database.models import User
from agentforge.llm.effort import Effort
from agentforge.repositories.model_preferences import UnsupportedEffort, effort_choices, set_effort

router = APIRouter(prefix="/api/v1", tags=["model"])
logger = logging.getLogger(__name__)


def unavailable(exc: SQLAlchemyError) -> HTTPException:
    """The preference cannot be read or kept (the database is behind this version: migration 0021 is not applied yet). The
    composer then simply offers no effort menu; nothing else depends on it."""
    logger.warning("reasoning effort unavailable (is migration 0021_reasoning_effort applied?): %s", type(exc).__name__)
    return HTTPException(503, "reasoning_effort_unavailable")


class ReasoningEffortBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    effort: Effort


@router.get("/settings/reasoning-effort")
async def read_effort(request: Request, user: User = Depends(current_user)):
    """`efforts` is empty (and `effort` null) when the configured model offers no choice. The choice applies to runs that start
    after it is made."""
    model = request.app.state.settings.llm_model
    try:
        return await run_in_threadpool(effort_choices, sessions(request), user.id, model)
    except SQLAlchemyError as exc:
        raise unavailable(exc) from None


@router.put("/settings/reasoning-effort")
async def write_effort(body: ReasoningEffortBody, request: Request, user: User = Depends(current_user)):
    model = request.app.state.settings.llm_model
    try:
        return await run_in_threadpool(set_effort, sessions(request), user.id, model, body.effort)
    except UnsupportedEffort:
        raise HTTPException(422, "unsupported_reasoning_effort") from None
    except SQLAlchemyError as exc:
        raise unavailable(exc) from None
