"""Revocable sessions; never accept a client-supplied identity."""

from ipaddress import ip_address
from types import SimpleNamespace
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from agentforge.database.models import User
from agentforge.settings import Settings

COOKIE_NAME = "agentforge_local_session"


def local_auth_enabled(settings: Settings) -> bool:
    token = settings.local_bootstrap_token
    return (settings.app_env != "production" and settings.auth_mode == "local_single_user"
            and settings.local_user_id is not None and token is not None
            and len(token.get_secret_value()) >= 32)


def _loopback(host: str | None) -> bool:
    if host == "localhost":
        return True
    try:
        return ip_address(host or "").is_loopback
    except ValueError:
        return False


def require_local_transport(request: Request, *, mutation: bool = False) -> None:
    settings: Settings = request.app.state.settings
    if not local_auth_enabled(settings):
        raise HTTPException(503, "local_auth_not_configured")
    # Use the actual ASGI peer and Host. Forwarded headers never establish trust.
    try:
        hostname = request.url.hostname
    except ValueError:
        raise HTTPException(403, "local_access_only") from None
    if (request.client is None or not _loopback(request.client.host)
            or not _loopback(hostname)):
        raise HTTPException(403, "local_access_only")
    origin = request.headers.get("origin")
    if mutation and not origin:
        raise HTTPException(403, "origin_required")
    if origin:
        try:
            parsed = urlsplit(origin)
            origin_hostname = parsed.hostname
        except ValueError:
            raise HTTPException(403, "origin_not_allowed") from None
        if (origin not in settings.allowed_origins or parsed.scheme not in {"http", "https"}
                or not _loopback(origin_hostname) or parsed.path or parsed.query or parsed.fragment):
            raise HTTPException(403, "origin_not_allowed")


def current_user(request: Request) -> User:
    settings: Settings = request.app.state.settings
    if settings.auth_mode == "github_app":
        require_public_transport(request, mutation=request.method not in {"GET", "HEAD", "OPTIONS"})
        from agentforge.auth.durable import PUBLIC_COOKIE_NAME
        identity = durable_sessions(request).resolve(request.cookies.get(PUBLIC_COOKIE_NAME))
        if identity is None:
            raise HTTPException(401, "session_required")
    else:
        require_local_transport(request, mutation=request.method not in {"GET", "HEAD", "OPTIONS"})
        identity = local_sessions(request).resolve(request.cookies.get(COOKIE_NAME))
        if identity is None or identity != settings.local_user_id:
            raise HTTPException(401, "session_required")
    # A deleted/changed identity cannot keep reading through an old cookie or SSE stream.
    with Session(request.app.state.engine) as database:
        user = database.get(User, identity)
        # Bootstrap identities carry negative GitHub ids; they never authenticate a public deployment.
        if user is None or (settings.auth_mode == "github_app" and (user.github_user_id or 0) < 0):
            raise HTTPException(401, "session_identity_unavailable")
        database.expunge(user)
        return user


def durable_sessions(request):
    from sqlalchemy.orm import sessionmaker
    from agentforge.auth.durable import DurableSessionStore
    return DurableSessionStore(sessionmaker(bind=request.app.state.engine, expire_on_commit=False), request.app.state.settings)


def local_sessions(request):
    """Local sign-in shares the durable session table, so restarting the API keeps the user signed in.

    The local TTL is both the absolute and idle limit; cookie name, path and SameSite stay distinct."""
    from sqlalchemy.orm import sessionmaker
    from agentforge.auth.durable import DurableSessionStore
    ttl = request.app.state.settings.local_session_ttl_sec
    return DurableSessionStore(sessionmaker(bind=request.app.state.engine, expire_on_commit=False),
        SimpleNamespace(session_absolute_ttl_sec=ttl, session_idle_ttl_sec=ttl))


def require_public_transport(request: Request, *, mutation: bool = False):
    settings = request.app.state.settings
    public = urlsplit(str(settings.api_public_base_url))
    # Trust only configured public Host. The deployment must forward HTTPS and
    # strip untrusted forwarded headers before the ASGI server.
    if request.url.hostname != public.hostname:
        raise HTTPException(403, "host_not_allowed")
    if settings.app_env == "production" and (public.scheme != "https" or request.url.scheme != "https"
            or str(settings.app_base_url).split(":", 1)[0] != "https"):
        raise HTTPException(503, "https_configuration_required")
    origin = request.headers.get("origin")
    if mutation and not origin:
        raise HTTPException(403, "origin_required")
    if origin and origin not in settings.allowed_origins:
        raise HTTPException(403, "origin_not_allowed")
