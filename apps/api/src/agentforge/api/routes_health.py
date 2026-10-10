"""Liveness and readiness probes."""

from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError

from agentforge.database.engine import ping_database

router = APIRouter(prefix="/api/v1/health", tags=["health"])

CheckStatus = Literal["ok", "error", "not_configured"]
ReadyStatus = Literal["ready", "degraded"]


class LiveResponse(BaseModel):
    status: Literal["ok"]


class CheckResult(BaseModel):
    status: CheckStatus
    detail: str | None = None


class ReadyChecks(BaseModel):
    database: CheckResult
    checkpointer: CheckResult
    worker: CheckResult


class ReadyResponse(BaseModel):
    status: ReadyStatus
    checks: ReadyChecks = Field(
        description="Database is required in Phase 1. Checkpointer and worker are reported but not required yet."
    )


@router.get("/live", response_model=LiveResponse)
def live() -> LiveResponse:
    """Process liveness only; does not touch PostgreSQL."""
    return LiveResponse(status="ok")


@router.get(
    "/ready",
    response_model=ReadyResponse,
    responses={503: {"model": ReadyResponse}},
)
def ready(request: Request) -> JSONResponse:
    """Ping PostgreSQL. Checkpointer and worker are not configured in Phase 1."""
    try:
        ping_database(request.app.state.engine)
        database = CheckResult(status="ok")
    except (SQLAlchemyError, OSError):
        # Do not include the exception text; SQLAlchemy messages can contain the URL.
        database = CheckResult(status="error", detail="database_unreachable")

    payload = ReadyResponse(
        status="ready" if database.status == "ok" else "degraded",
        checks=ReadyChecks(
            database=database,
            checkpointer=CheckResult(status="not_configured"),
            worker=CheckResult(status="not_configured"),
        ),
    )
    return JSONResponse(
        content=payload.model_dump(exclude_none=True),
        status_code=200 if payload.status == "ready" else 503,
    )
