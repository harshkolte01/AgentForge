"""Liveness and readiness probes."""

from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from agentforge.database.engine import ping_database
from agentforge.repositories.workers import worker_presence

router = APIRouter(prefix="/api/v1/health", tags=["health"])

CheckStatus = Literal["ok", "error", "not_configured", "not_observed"]
ReadyStatus = Literal["ready", "degraded"]


class LiveResponse(BaseModel):
    status: Literal["ok"]


class CheckResult(BaseModel):
    status: CheckStatus
    detail: str | None = None


class ReadyChecks(BaseModel):
    database: CheckResult
    worker: CheckResult


class ReadyResponse(BaseModel):
    status: ReadyStatus
    checks: ReadyChecks = Field(
        description="Readiness is database connectivity only. The worker check reports a recent worker heartbeat but "
        "never makes the API unready."
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
    """Ping PostgreSQL; report (without gating on) whether a worker has said it is alive."""
    try:
        ping_database(request.app.state.engine)
        database = CheckResult(status="ok")
    except (SQLAlchemyError, OSError):
        # Do not include the exception text; SQLAlchemy messages can contain the URL.
        database = CheckResult(status="error", detail="database_unreachable")
    worker = CheckResult(status="not_observed", detail="no_recent_worker_heartbeat")
    if database.status == "ok":
        presence = worker_presence(sessionmaker(bind=request.app.state.engine))
        if presence["status"] == "online":
            worker = CheckResult(status="ok", detail="worker_heartbeat_recent")

    payload = ReadyResponse(
        status="ready" if database.status == "ok" else "degraded",
        checks=ReadyChecks(
            database=database,
            worker=worker,
        ),
    )
    return JSONResponse(
        content=payload.model_dump(exclude_none=True),
        status_code=200 if payload.status == "ready" else 503,
    )
