"""Opaque session cookies persist only as hashes and expire on idle/absolute time."""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from secrets import token_urlsafe
from sqlalchemy import func, select, update
from agentforge.database.identity import AuthSession
from agentforge.database.models import User

PUBLIC_COOKIE_NAME = "__Host-agentforge_session"
OAUTH_COOKIE_NAME = "agentforge_oauth_binding"

def digest(value):
    return sha256(value.encode()).hexdigest()

def now():
    return datetime.now(timezone.utc)

class DurableSessionStore:
    def __init__(self, session_factory, settings):
        self.session_factory, self.settings = session_factory, settings

    def issue(self, user_id):
        token, stamp = token_urlsafe(48), now()
        with self.session_factory() as session, session.begin():
            if session.scalar(select(User.id).where(User.id == user_id).with_for_update()) is None:
                raise ValueError("identity_unavailable")
            session.execute(update(AuthSession).where(AuthSession.user_id == user_id,
                AuthSession.revoked_at.is_(None), AuthSession.expires_at <= stamp).values(revoked_at=stamp))
            # Bounded concurrent sessions per identity, oldest first.
            rows = list(session.scalars(select(AuthSession).where(AuthSession.user_id == user_id,
                AuthSession.revoked_at.is_(None)).order_by(AuthSession.created_at).with_for_update()))
            for row in rows[:-9]:
                row.revoked_at = stamp
            session.add(AuthSession(user_id=user_id, token_hash=digest(token),
                last_seen_at=stamp, expires_at=stamp + timedelta(seconds=self.settings.session_absolute_ttl_sec)))
        return token

    def resolve(self, token):
        """Runs on every request and every few seconds of an open stream, so it is a plain read; it writes (one
        conditional UPDATE, no row lock) only when the session has idled out or last_seen_at is due a refresh."""
        if not token or len(token) > 128:
            return None
        stamp, hashed = now(), digest(token)
        with self.session_factory() as session:
            row = session.execute(select(AuthSession.user_id, AuthSession.revoked_at, AuthSession.expires_at,
                AuthSession.last_seen_at).where(AuthSession.token_hash == hashed)).first()
        if row is None or row.revoked_at or row.expires_at <= stamp:
            return None
        live = (AuthSession.token_hash == hashed, AuthSession.revoked_at.is_(None))
        if row.last_seen_at + timedelta(seconds=self.settings.session_idle_ttl_sec) <= stamp:
            with self.session_factory() as session, session.begin():
                session.execute(update(AuthSession).where(*live).values(revoked_at=stamp))
            return None
        # A quarter of the idle limit, at most a minute: always refreshed well before it could idle out.
        if row.last_seen_at + timedelta(seconds=min(60, self.settings.session_idle_ttl_sec // 4)) <= stamp:
            with self.session_factory() as session, session.begin():
                # Fenced on the row still being live, so a logout that landed after the read above wins.
                refreshed = session.execute(update(AuthSession).where(*live, AuthSession.expires_at > stamp)
                    .values(last_seen_at=func.greatest(AuthSession.last_seen_at, stamp)).returning(AuthSession.user_id)).first()
            return refreshed.user_id if refreshed else None
        return row.user_id

    def revoke(self, token):
        if token and len(token) <= 128:
            with self.session_factory() as session, session.begin():
                session.execute(update(AuthSession).where(AuthSession.token_hash == digest(token)).values(revoked_at=now()))
