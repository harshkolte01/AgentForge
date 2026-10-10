"""Revocable local sessions; never accept a client-supplied identity."""

from dataclasses import dataclass
from hashlib import sha256
from ipaddress import ip_address
from secrets import token_urlsafe
from threading import Lock
from time import monotonic
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from agentforge.database.models import User
from agentforge.settings import Settings

COOKIE_NAME = "agentforge_local_session"


@dataclass(frozen=True)
class LocalSession:
    user_id: UUID
    expires_at: float


class SessionStore:
    """Process-local by design: restarting the API revokes every session."""

    def __init__(self) -> None:
        self._sessions: dict[str, LocalSession] = {}
        self._lock = Lock()

    def issue(self, user_id: UUID, ttl: int) -> str:
        token = token_urlsafe(48)
        now = monotonic()
        with self._lock:
            self._sessions = {key: value for key, value in self._sessions.items()
                              if value.expires_at > now}
            # Local setup should not create an unbounded session registry.
            if len(self._sessions) >= 32:
                oldest = min(self._sessions, key=lambda key: self._sessions[key].expires_at)
                del self._sessions[oldest]
            self._sessions[sha256(token.encode()).hexdigest()] = LocalSession(user_id, now + ttl)
        return token

    def resolve(self, token: str | None) -> LocalSession | None:
        if not token or len(token) > 128:
            return None
        key = sha256(token.encode()).hexdigest()
        with self._lock:
            result = self._sessions.get(key)
            if result and result.expires_at <= monotonic():
                del self._sessions[key]
                return None
            return result

    def revoke(self, token: str | None) -> None:
        if token:
            with self._lock:
                self._sessions.pop(sha256(token.encode()).hexdigest(), None)


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
        session = request.app.state.auth_sessions.resolve(request.cookies.get(COOKIE_NAME))
        if session is None or session.user_id != settings.local_user_id:
            raise HTTPException(401, "session_required")
        identity = session.user_id
    # A deleted/changed identity cannot keep reading through an old cookie or SSE stream.
    with Session(request.app.state.engine) as database:
        user = database.get(User, identity)
        if user is None:
            raise HTTPException(401, "session_identity_unavailable")
        database.expunge(user)
        return user


def durable_sessions(request):
    from sqlalchemy.orm import sessionmaker
    from agentforge.auth.durable import DurableSessionStore
    return DurableSessionStore(sessionmaker(bind=request.app.state.engine, expire_on_commit=False), request.app.state.settings)


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
