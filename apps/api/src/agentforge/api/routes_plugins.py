"""Plugins: the catalog, a person's connections to MCP servers, and what each connection offers the agent."""
import inspect
import logging
from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from agentforge.api.dependencies import sessions
from agentforge.auth.session import current_user
from agentforge.database.models import User
from agentforge.plugins.catalog import catalog_view
from agentforge.plugins.oauth import metadata_document, metadata_document_url
from agentforge.plugins.oauth_service import OAuthService, send_revocation
from agentforge.plugins.service import PluginError, PluginService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/plugins", tags=["plugins"])
OAUTH_COOKIE = "agentforge_plugin_oauth"
OAUTH_COOKIE_PATH = "/api/v1/plugins/oauth"


class PluginConnect(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # A catalog key, or "custom" for a server the person typed in (then name and server_url are required).
    plugin_key: str = Field(min_length=1, max_length=40, pattern=r"^[a-z0-9-]+$")
    name: str | None = Field(default=None, max_length=120)
    server_url: str | None = Field(default=None, max_length=2048)
    # Sent as "Authorization: Bearer <token>", or as the value of header_name when that names another header.
    token: str | None = Field(default=None, max_length=4096, repr=False)
    header_name: str | None = Field(default=None, max_length=64)


class PluginOAuthStart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plugin_key: str = Field(min_length=1, max_length=40, pattern=r"^[a-z0-9-]+$")
    name: str | None = Field(default=None, max_length=120)
    server_url: str | None = Field(default=None, max_length=2048)
    # Set to sign in again to a connection the person already has.
    connection_id: UUID | None = None
    # An OAuth app the person registered themselves (for a server that does not register apps automatically).
    client_id: str | None = Field(default=None, max_length=1024)
    client_secret: str | None = Field(default=None, max_length=4096, repr=False)


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


def oauth_service(request: Request) -> OAuthService:
    return OAuthService(sessions(request), request.app.state.settings)


def app_url(settings, query: dict[str, str]) -> str:
    return str(settings.app_base_url).rstrip("/") + "/plugins?" + urlencode(query)


def signed_in_user(request: Request) -> User | None:
    """Who is signed in, or None. The callback is a browser navigation, so someone signed out gets a page that says so, not a JSON
    error. Resolved as FastAPI resolves `Depends(current_user)`, overrides included."""
    resolver = request.app.dependency_overrides.get(current_user, current_user)
    try:
        return resolver(request) if inspect.signature(resolver).parameters else resolver()
    except HTTPException:
        return None


@router.post("/oauth/start")
async def oauth_start(body: PluginOAuthStart, request: Request, svc: OAuthService = Depends(oauth_service), user: User = Depends(current_user)):
    """Begins signing in to a server with OAuth. Returns the address the person's browser goes to; the sign-in is finished by the
    callback below, in the same browser (a cookie set here ties the two together)."""
    try:
        started = await svc.start(user.id, body)
    except PluginError as error:
        raise failure(error) from None
    response = JSONResponse({"authorization_url": started.url})
    response.set_cookie(OAUTH_COOKIE, started.binding, max_age=600, httponly=True, secure=request.app.state.settings.app_env == "production",
                        samesite="lax", path=OAUTH_COOKIE_PATH)
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/oauth/callback")
async def oauth_callback(request: Request, code: str = "", state: str = "", iss: str | None = None, error: str = ""):
    """Where the authorization server sends the person's browser back to. Always answers with a redirect into the app, saying how it
    went (`oauth=connected` or `oauth=error&code=...`), and never echoes anything the server sent."""
    settings = request.app.state.settings
    outcome = {"oauth": "error", "code": "plugin_oauth_failed"}
    try:
        user = await run_in_threadpool(signed_in_user, request)
        if user is None:
            raise PluginError(401, "plugin_oauth_signed_out", "Sign in to AgentForge, then connect the plugin again.")
        connection = await OAuthService(sessions(request), settings).finish(
            user.id, state=state, code=code, iss=iss, error=error, binding=request.cookies.get(OAUTH_COOKIE))
        outcome = {"oauth": "connected", "plugin": connection["name"]}
    except PluginError as problem:
        outcome = {"oauth": "error", "code": problem.code}
    except Exception as problem:  # noqa: BLE001
        # The type only: a provider's message can carry a token or a code.
        logger.error("Plugin sign-in callback failed: %s", type(problem).__name__)
    response = RedirectResponse(app_url(settings, outcome), status_code=303)
    response.delete_cookie(OAUTH_COOKIE, path=OAUTH_COOKIE_PATH, httponly=True, samesite="lax")
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@router.get("/oauth/client.json")
def oauth_client_document(request: Request):
    """This app's Client ID Metadata Document: what an authorization server reads at the client id it is given. Public."""
    settings = request.app.state.settings
    if not (settings.plugins_enabled and settings.plugin_oauth_enabled and metadata_document_url(settings)):
        raise HTTPException(404, "not_found")
    return JSONResponse(metadata_document(settings), headers={"Cache-Control": "public, max-age=3600"})


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
        revocation = await run_in_threadpool(svc.delete, user.id, connection_id)
    except PluginError as error:
        raise failure(error) from None
    # The authorization server is told to forget the tokens after the answer is sent; removing never waits on it.
    return Response(status_code=204, background=BackgroundTask(send_revocation, revocation) if revocation else None)
