"""Local session bootstrap. OAuth remains a later phase."""

from secrets import compare_digest

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from agentforge.auth.session import (
    COOKIE_NAME, current_user, local_auth_enabled, require_local_transport,
)
from agentforge.database.models import User

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.get("/session")
def session_status(request: Request, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    if not local_auth_enabled(request.app.state.settings):
        return {"enabled": False, "authenticated": False, "mode": "disabled"}
    require_local_transport(request)
    try:
        user = current_user(request)
    except HTTPException as error:
        if error.status_code != 401:
            raise
        return {"enabled": True, "authenticated": False, "mode": "local_single_user"}
    return {"enabled": True, "authenticated": True, "mode": "local_single_user",
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
def me(user: User = Depends(current_user)) -> dict:
    return {"id": str(user.id), "display_name": user.display_name, "mode": "local_single_user"}


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response, user: User = Depends(current_user)) -> None:
    request.app.state.auth_sessions.revoke(request.cookies.get(COOKIE_NAME))
    response.delete_cookie(COOKIE_NAME, path="/api/v1", httponly=True, samesite="strict")
