"""Local bootstrap and GitHub App public login share one session authority."""

from secrets import compare_digest
from secrets import token_urlsafe
import base64
from hashlib import sha256
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session
from fastapi.responses import RedirectResponse

from agentforge.auth.session import (
    COOKIE_NAME, current_user, local_auth_enabled, require_local_transport, require_public_transport, durable_sessions,
)
from agentforge.auth.durable import PUBLIC_COOKIE_NAME, OAUTH_COOKIE_NAME
from agentforge.auth.github_service import GitHubIdentityService, github_configured, callback_url
from agentforge.api.dependencies import sessions
from agentforge.integrations.github_client import GitHubError
from agentforge.database.models import User

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


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
    request.app.state.auth_sessions.revoke(request.cookies.get(COOKIE_NAME))
    token = request.app.state.auth_sessions.issue(settings.local_user_id, settings.local_session_ttl_sec)
    response.set_cookie(COOKIE_NAME, token, max_age=settings.local_session_ttl_sec,
                        httponly=True, samesite="strict", secure=request.url.scheme == "https",
                        path="/api/v1")
    response.headers["Cache-Control"] = "no-store"


@router.get("/me")
def me(request: Request, user: User = Depends(current_user)) -> dict:
    return {"id": str(user.id), "display_name": user.display_name, "mode": request.app.state.settings.auth_mode}


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response, user: User = Depends(current_user)) -> None:
    request.app.state.auth_sessions.revoke(request.cookies.get(COOKIE_NAME))
    if request.app.state.settings.auth_mode == "github_app":
        durable_sessions(request).revoke(request.cookies.get(PUBLIC_COOKIE_NAME))
        response.delete_cookie(PUBLIC_COOKIE_NAME, path="/", secure=True, httponly=True, samesite="lax")
    response.delete_cookie(COOKIE_NAME, path="/api/v1", httponly=True, samesite="strict")


@router.get("/github/start")
def github_start(request: Request):
    settings = request.app.state.settings
    if not github_configured(settings):
        raise HTTPException(503, "github_auth_not_configured")
    require_public_transport(request)
    state, binding, verifier = token_urlsafe(32), token_urlsafe(32), token_urlsafe(48)
    try:
        GitHubIdentityService(sessions(request), settings).start(state, binding, verifier)
    except GitHubError as error:
        raise HTTPException(429, str(error)) from None
    challenge = base64.urlsafe_b64encode(sha256(verifier.encode()).digest()).decode().rstrip("=")
    response = RedirectResponse("https://github.com/login/oauth/authorize?" + urlencode({
        "client_id": settings.github_client_id, "redirect_uri": callback_url(settings), "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256", "allow_signup": "true"}), status_code=302)
    response.set_cookie(OAUTH_COOKIE_NAME, binding, max_age=600, httponly=True, secure=settings.app_env == "production",
        samesite="lax", path="/api/v1/auth/github")
    return response


@router.get("/github/callback")
async def github_callback(request: Request, code: str = "", state: str = ""):
    settings = request.app.state.settings
    if not github_configured(settings):
        raise HTTPException(503, "github_auth_not_configured")
    require_public_transport(request)
    target = str(settings.app_base_url).rstrip("/") + "/chat"
    try:
        service = GitHubIdentityService(sessions(request), settings)
        verifier = service.consume(state, request.cookies.get(OAUTH_COOKIE_NAME))
        if not code or len(code) > 512:
            raise GitHubError("github_authorization_failed")
        identity = await service.signup(code, verifier)
        durable_sessions(request).revoke(request.cookies.get(PUBLIC_COOKIE_NAME))
        token = durable_sessions(request).issue(identity)
        response = RedirectResponse(target, status_code=303)
        response.set_cookie(PUBLIC_COOKIE_NAME, token, max_age=settings.session_absolute_ttl_sec,
            httponly=True, secure=True, samesite="lax", path="/")
    except (GitHubError, ValueError):
        response = RedirectResponse(target + "?auth_error=github_authorization_failed", status_code=303)
    response.delete_cookie(OAUTH_COOKIE_NAME, path="/api/v1/auth/github", httponly=True,
        secure=settings.app_env == "production", samesite="lax")
    return response
