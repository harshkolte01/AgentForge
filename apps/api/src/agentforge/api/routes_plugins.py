"""Plugins: the catalog, a person's connections to MCP servers, and what each connection offers the agent."""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from agentforge.api.dependencies import sessions
from agentforge.auth.session import current_user
from agentforge.database.models import User
from agentforge.plugins.catalog import catalog_view
from agentforge.plugins.service import PluginError, PluginService

router = APIRouter(prefix="/api/v1/plugins", tags=["plugins"])


class PluginConnect(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # A catalog key, or "custom" for a server the person typed in (then name and server_url are required).
    plugin_key: str = Field(min_length=1, max_length=40, pattern=r"^[a-z0-9-]+$")
    name: str | None = Field(default=None, max_length=120)
    server_url: str | None = Field(default=None, max_length=2048)
    # Sent as "Authorization: Bearer <token>", or as the value of header_name when that names another header.
    token: str | None = Field(default=None, max_length=4096, repr=False)
    header_name: str | None = Field(default=None, max_length=64)


class PluginPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool | None = None
    token: str | None = Field(default=None, max_length=4096, repr=False)
    disabled_tools: list[str] | None = Field(default=None, max_length=200)


def service(request: Request) -> PluginService:
    return PluginService(sessions(request), request.app.state.settings)


def failure(error: PluginError) -> HTTPException:
    return HTTPException(error.status, {"code": error.code, "message": error.message})


@router.get("")
async def overview(svc: PluginService = Depends(service), user: User = Depends(current_user)):
    """The catalog, this person's connections, and whether credentials can be saved on this server."""
    connections = await run_in_threadpool(svc.list, user.id)
    return {"catalog": catalog_view(), "connections": connections, **svc.capabilities()}


@router.post("", status_code=201)
async def connect(body: PluginConnect, svc: PluginService = Depends(service), user: User = Depends(current_user)):
    """Connects to a server. It is saved only if the server answers and offers at least one tool."""
    try:
        return await svc.connect(user.id, body)
    except PluginError as error:
        raise failure(error) from None


@router.patch("/{connection_id}")
async def change(connection_id: UUID, body: PluginPatch, svc: PluginService = Depends(service), user: User = Depends(current_user)):
    try:
        return await svc.update(user.id, connection_id, body)
    except PluginError as error:
        raise failure(error) from None


@router.post("/{connection_id}/refresh")
async def refresh(connection_id: UUID, svc: PluginService = Depends(service), user: User = Depends(current_user)):
    """Reads the server's tools again. A server that cannot be reached is reported on the connection (status, last_error)."""
    try:
        return await svc.refresh(user.id, connection_id)
    except PluginError as error:
        raise failure(error) from None


@router.delete("/{connection_id}", status_code=204)
async def remove(connection_id: UUID, svc: PluginService = Depends(service), user: User = Depends(current_user)):
    try:
        await run_in_threadpool(svc.delete, user.id, connection_id)
    except PluginError as error:
        raise failure(error) from None
    return Response(status_code=204)
