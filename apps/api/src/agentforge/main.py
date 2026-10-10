"""FastAPI application factory, CORS, and route mounting."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from agentforge.api.routes_health import router as health_router
from agentforge.database.engine import create_db_engine
from agentforge.settings import Settings, get_settings


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
    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved.allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )
    app.include_router(health_router)
    return app


app = create_app()
