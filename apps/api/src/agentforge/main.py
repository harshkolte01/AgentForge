"""FastAPI application factory, CORS, and route mounting."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from agentforge.api.routes_health import router as health_router
from agentforge.api.routes_tasks import router as tasks_router
from agentforge.api.routes_events import router as events_router
from agentforge.api.routes_projects import router as projects_router
from agentforge.api.routes_artifacts import router as artifacts_router
from agentforge.api.routes_configuration import router as configuration_router
from agentforge.auth.routes import router as auth_router
from agentforge.auth.session import SessionStore
from agentforge.api.routes_github import router as github_router
from agentforge.api.routes_approvals import router as approvals_router
from agentforge.database.engine import create_db_engine
from agentforge.settings import Settings, get_settings
from agentforge.security.request_limits import RequestBodyLimitMiddleware


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    owns_engine = not hasattr(app.state, "engine")
    if owns_engine:
        app.state.engine = create_db_engine(settings.database_url)
    try:
        yield
    finally:
        if owns_engine:
            app.state.engine.dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the ASGI app. Uvicorn loads `agentforge.main:app`."""
    resolved = settings or get_settings()
    app = FastAPI(
        title="AgentForge AI API",
        version="0.1.0",
        description="HTTP API for the AgentForge AI workspace.",
        lifespan=lifespan,
        openapi_tags=[
            {
                "name": "health",
                "description": "Liveness and readiness probes.",
            }
        ],
    )
    app.state.settings = resolved
    app.state.auth_sessions = SessionStore()
    app.add_middleware(RequestBodyLimitMiddleware, maximum=1_000_000)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved.allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(projects_router)
    app.include_router(tasks_router)
    app.include_router(events_router)
    app.include_router(artifacts_router)
    app.include_router(configuration_router)
    app.include_router(github_router)
    app.include_router(approvals_router)
    @app.middleware("http")
    async def private_response_headers(request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/api/v1/") and not request.url.path.startswith("/api/v1/health/"):
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
        return response
    return app


app = create_app()
