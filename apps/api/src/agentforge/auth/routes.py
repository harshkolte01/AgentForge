"""Local bootstrap and GitHub App public login share one session authority."""

import logging
from secrets import compare_digest
from secrets import token_urlsafe
import base64
from hashlib import sha256
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session
from fastapi.responses import RedirectResponse
from starlette.concurrency import run_in_threadpool

from agentforge.auth.session import (
    COOKIE_NAME, current_user, local_auth_enabled, require_local_transport, require_public_transport, durable_sessions,
    local_sessions,
)
from agentforge.auth.durable import PUBLIC_COOKIE_NAME, OAUTH_COOKIE_NAME
from agentforge.auth.github_service import GitHubIdentityService, github_configured, callback_url
from agentforge.api.dependencies import sessions
from agentforge.integrations.github_client import GitHubError
from agentforge.database.models import User

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
logger = logging.getLogger(__name__)

# Internal failure -> the stable `?auth_error=` code the web app explains. Anything unlisted is `unavailable`.
AUTH_ERRORS = {
    "github_authorization_denied": "denied",
    "github_authorization_expired": "expired",
    "github_signup_disabled": "signup_closed",
    "github_login_not_allowed": "not_allowed",
    "github_auth_not_configured": "app_misconfigured",
    "github_app_misconfigured": "app_misconfigured",
    "github_expiring_tokens_required": "app_misconfigured",
    "github_refresh_token_required": "app_misconfigured",
    "github_app_user_token_required": "app_misconfigured",
    "github_identity_invalid": "app_misconfigured",
    "github_login_rate_limited": "rate_limited",
    "github_rate_limited": "rate_limited",
}
# GitHub's `error` on the callback: the user pressed Cancel, or the App's callback URL does not match.
CALLBACK_ERRORS = {"access_denied": "github_authorization_denied", "redirect_uri_mismatch": "github_app_misconfigured"}
INSTALLED_PATH = "/settings?section=github&installed=1"


def app_url(settings, path: str, auth_error: str | None = None) -> str:
    url = str(settings.app_base_url).rstrip("/") + path
    return url + ("&" if "?" in url else "?") + urlencode({"auth_error": auth_error}) if auth_error else url


def safe_next(value: str | None) -> str | None:
    """A same-origin path to land on after sign-in; anything else is dropped."""
    if (not value or len(value) > 512 or not value.startswith("/") or value.startswith("//") or "\\" in value
            or any(ord(character) < 33 or ord(character) == 127 for character in value)):
        return None
    return value


def navigation(request: Request) -> bool:
    """A top-level browser navigation gets a redirect it can show; a fetch gets the JSON error."""
    headers = request.headers
    return headers.get("sec-fetch-mode") == "navigate" or "text/html" in headers.get("accept", "")


@router.get("/session")
def session_status(request: Request, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    settings = request.app.state.settings
    public = settings.auth_mode == "github_app" and github_configured(settings)
    if not local_auth_enabled(settings) and not public:
        return {"enabled": False, "authenticated": False, "mode": "disabled"}
    if public:
        require_public_transport(request)
    else:
        require_local_transport(request)
    try:
        user = current_user(request)
    except HTTPException as error:
        if error.status_code != 401:
            raise
        return {"enabled": True, "authenticated": False, "mode": settings.auth_mode}
    return {"enabled": True, "authenticated": True, "mode": settings.auth_mode,
            "user": {"id": str(user.id), "display_name": user.display_name}}


@router.post("/session", status_code=204)
def create_session(request: Request, response: Response) -> None:
    require_local_transport(request, mutation=True)
    settings = request.app.state.settings
    authorization = request.headers.get("authorization", "")
    expected = settings.local_bootstrap_token.get_secret_value()
    supplied = authorization[7:] if authorization.startswith("Bearer ") else ""
    if not supplied or len(supplied) > 512 or not compare_digest(supplied.encode(), expected.encode()):
        raise HTTPException(401, "invalid_bootstrap_token")
    with Session(request.app.state.engine) as database:
        if database.get(User, settings.local_user_id) is None:
            raise HTTPException(503, "local_identity_not_provisioned")
    store = local_sessions(request)
    store.revoke(request.cookies.get(COOKIE_NAME))
    token = store.issue(settings.local_user_id)
    response.set_cookie(COOKIE_NAME, token, max_age=settings.local_session_ttl_sec,
                        httponly=True, samesite="strict", secure=request.url.scheme == "https",
                        path="/api/v1")
    response.headers["Cache-Control"] = "no-store"


@router.get("/me")
def me(request: Request, user: User = Depends(current_user)) -> dict:
    return {"id": str(user.id), "display_name": user.display_name, "mode": request.app.state.settings.auth_mode}


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response, user: User = Depends(current_user)) -> None:
    if request.app.state.settings.auth_mode == "github_app":
        durable_sessions(request).revoke(request.cookies.get(PUBLIC_COOKIE_NAME))
        response.delete_cookie(PUBLIC_COOKIE_NAME, path="/", secure=True, httponly=True, samesite="lax")
    else:
        local_sessions(request).revoke(request.cookies.get(COOKIE_NAME))
    response.delete_cookie(COOKIE_NAME, path="/api/v1", httponly=True, samesite="strict")


@router.get("/github/start")
def github_start(request: Request, next: str = ""):
    settings = request.app.state.settings
    redirect_errors = navigation(request)
    if not github_configured(settings):
        if redirect_errors:
            return RedirectResponse(app_url(settings, "/chat", "app_misconfigured"), status_code=303)
        raise HTTPException(503, "github_auth_not_configured")
    try:
        require_public_transport(request)
    except HTTPException as error:
        if redirect_errors and error.detail == "https_configuration_required":
            return RedirectResponse(app_url(settings, "/chat", "app_misconfigured"), status_code=303)
        raise
    state, binding, verifier = token_urlsafe(32), token_urlsafe(32), token_urlsafe(48)
    try:
        GitHubIdentityService(sessions(request), settings).start(state, binding, verifier, safe_next(next))
    except GitHubError as error:
        if redirect_errors:
            return RedirectResponse(app_url(settings, "/chat", "rate_limited"), status_code=303)
        raise HTTPException(429, str(error)) from None
    except Exception as error:
        logger.error("GitHub sign-in could not start: %s", type(error).__name__)
        if redirect_errors:
            return RedirectResponse(app_url(settings, "/chat", "unavailable"), status_code=303)
        raise HTTPException(503, "github_login_unavailable") from None
    challenge = base64.urlsafe_b64encode(sha256(verifier.encode()).digest()).decode().rstrip("=")
    response = RedirectResponse("https://github.com/login/oauth/authorize?" + urlencode({
        "client_id": settings.github_client_id, "redirect_uri": callback_url(settings), "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256", "allow_signup": "true"}), status_code=302)
    response.set_cookie(OAUTH_COOKIE_NAME, binding, max_age=600, httponly=True, secure=settings.app_env == "production",
        samesite="lax", path="/api/v1/auth/github")
    return response


async def after_installation(request: Request, settings) -> RedirectResponse:
    """GitHub's post-installation redirect. When the App requests user authorization during installation it carries a
    `code` but none of our state, so it can never sign anyone in: a live session goes to Settings, anyone else signs in
    afresh and lands there."""
    token = request.cookies.get(PUBLIC_COOKIE_NAME)
    if token and await run_in_threadpool(durable_sessions(request).resolve, token):
        return RedirectResponse(app_url(settings, INSTALLED_PATH), status_code=303)
    start = str(settings.api_public_base_url).rstrip("/") + "/api/v1/auth/github/start?" + urlencode({"next": INSTALLED_PATH})
    return RedirectResponse(start, status_code=303)


@router.get("/github/callback")
async def github_callback(request: Request, code: str = "", state: str = "", error: str = "",
                          installation_id: str = "", setup_action: str = ""):
    settings = request.app.state.settings
    if not github_configured(settings):
        return RedirectResponse(app_url(settings, "/chat", "app_misconfigured"), status_code=303)
    require_public_transport(request)
    binding = request.cookies.get(OAUTH_COOKIE_NAME)
    installed = bool(installation_id or setup_action)
    try:
        if installed and not (state and binding):
            response = await after_installation(request, settings)
        else:
            service = GitHubIdentityService(sessions(request), settings)
            if error:
                try:
                    # Spend the transaction so a cancelled sign-in's state cannot be replayed.
                    await run_in_threadpool(service.consume, state, binding)
                except GitHubError:
                    pass
                raise GitHubError(CALLBACK_ERRORS.get(error, "github_unavailable"))
            verifier, next_path = await run_in_threadpool(service.consume, state, binding)
            if not code or len(code) > 512:
                raise GitHubError("github_authorization_failed")
            identity = await service.signup(code, verifier)
            store = durable_sessions(request)
            await run_in_threadpool(store.revoke, request.cookies.get(PUBLIC_COOKIE_NAME))
            token = await run_in_threadpool(store.issue, identity)
            response = RedirectResponse(app_url(settings, INSTALLED_PATH if installed else next_path or "/chat"), status_code=303)
            response.set_cookie(PUBLIC_COOKIE_NAME, token, max_age=settings.session_absolute_ttl_sec,
                httponly=True, secure=True, samesite="lax", path="/")
    except GitHubError as failure:
        response = RedirectResponse(app_url(settings, "/chat", AUTH_ERRORS.get(str(failure), "unavailable")), status_code=303)
    except Exception as failure:
        # The type only: driver messages can carry SQL parameters and provider messages can carry tokens.
        logger.error("GitHub sign-in callback failed: %s", type(failure).__name__)
        response = RedirectResponse(app_url(settings, "/chat", "unavailable"), status_code=303)
    response.delete_cookie(OAUTH_COOKIE_NAME, path="/api/v1/auth/github", httponly=True,
        secure=settings.app_env == "production", samesite="lax")
    return response
