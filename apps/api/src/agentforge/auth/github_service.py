"""GitHub identity signup, encrypted short-lived tokens and rotation fencing."""
from datetime import timedelta
from uuid import uuid4
from sqlalchemy import select, func, update
from agentforge.auth.crypto import CredentialCipher
from agentforge.auth.durable import digest, now
from agentforge.database.models import User, Project
from agentforge.database.identity import OAuthTransaction, GitHubCredential, GitHubInstallation, GitHubRepositoryGrant
from agentforge.integrations.github_app import GitHubAppClient, valid_github_id
from agentforge.integrations.github_app import github_for_user  # public shared task adapter
from agentforge.integrations.github_client import GitHubError

def github_configured(settings):
    try:
        CredentialCipher(settings.github_token_encryption_key)
    except ValueError:
        return False
    return bool(settings.auth_mode == "github_app" and settings.github_client_id
        and settings.github_client_secret and settings.github_app_id)

def callback_url(settings):
    return str(settings.github_oauth_callback_url or (str(settings.api_public_base_url).rstrip("/") + "/api/v1/auth/github/callback"))

class GitHubIdentityService:
    def __init__(self, session_factory, settings, *, transport=None):
        self.session_factory, self.settings, self.transport = session_factory, settings, transport
        self.cipher = CredentialCipher(settings.github_token_encryption_key)

    def start(self, state, binding, verifier):
        stamp = now()
        with self.session_factory() as session, session.begin():
            # DB-wide admission protects OAuth writes across instances. Expired
            # rows are pruned; row storage is bounded even without a cleanup job.
            from sqlalchemy import text, delete
            session.execute(text("SELECT pg_advisory_xact_lock(578109562)"))
            session.execute(delete(OAuthTransaction).where(OAuthTransaction.expires_at < stamp))
            recent = session.scalar(select(func.count()).select_from(OAuthTransaction).where(
                OAuthTransaction.created_at >= stamp - timedelta(minutes=1)))
            if recent >= 100:
                raise GitHubError("github_login_rate_limited")
            session.add(OAuthTransaction(state_hash=digest(state), binding_hash=digest(binding),
                verifier_encrypted=self.cipher.encrypt(verifier), expires_at=stamp + timedelta(minutes=10)))

    def consume(self, state, binding):
        if not state or not binding or len(state) > 128 or len(binding) > 128:
            raise GitHubError("github_authorization_failed")
        with self.session_factory() as session, session.begin():
            row = session.scalar(select(OAuthTransaction).where(OAuthTransaction.state_hash == digest(state)).with_for_update())
            from secrets import compare_digest
            if (row is None or row.consumed_at or row.expires_at <= now()
                    or not compare_digest(row.binding_hash, digest(binding))):
                raise GitHubError("github_authorization_failed")
            row.consumed_at = now()
            return self.cipher.decrypt(row.verifier_encrypted)

    def _store_token(self, row, value):
        stamp = now()
        # Expiring user tokens are required to avoid silently accepting a broad,
        # permanently valid credential deployment.
        if type(value.get("expires_in")) is not int or not 60 <= value["expires_in"] <= 86400:
            raise GitHubError("github_expiring_tokens_required")
        if not isinstance(value.get("refresh_token"), str) or not value["refresh_token"].startswith("ghr_"):
            raise GitHubError("github_refresh_token_required")
        refresh_ttl = value.get("refresh_token_expires_in")
        if type(refresh_ttl) is not int or not 60 <= refresh_ttl <= 20000000:
            raise GitHubError("github_response_invalid")
        row.access_encrypted = self.cipher.encrypt(value["access_token"])
        row.refresh_encrypted = self.cipher.encrypt(value["refresh_token"])
        row.expires_at = stamp + timedelta(seconds=value["expires_in"])
        row.refresh_expires_at = stamp + timedelta(seconds=refresh_ttl)
        row.revoked_at, row.refresh_started_at = None, None

    async def signup(self, code, verifier):
        client = GitHubAppClient(self.settings, transport=self.transport)
        value = await client.oauth({"code": code, "code_verifier": verifier, "redirect_uri": callback_url(self.settings)})
        client.token = value["access_token"]
        profile = await client.request("GET", "/user")
        identifier, login = profile.get("id"), profile.get("login")
        if not valid_github_id(identifier) or not isinstance(login, str) or not login or len(login) > 100:
            raise GitHubError("github_identity_invalid")
        with self.session_factory() as session, session.begin():
            # One lock per GitHub identity makes concurrent initial signups
            # atomic without any implicit email/name account linking.
            from sqlalchemy import text
            session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": identifier})
            user = session.scalar(select(User).where(User.github_user_id == identifier).with_for_update())
            if user is None:
                if not self.settings.public_signup_enabled:
                    raise GitHubError("github_signup_disabled")
                user = User(id=uuid4(), github_user_id=identifier, display_name=login)
                session.add(user)
                session.flush()
                session.add(Project(id=uuid4(), owner_id=user.id, name="My workspace"))
            else:
                user.display_name = login
            credential = session.scalar(select(GitHubCredential).where(GitHubCredential.user_id == user.id).with_for_update())
            if credential is None:
                credential = GitHubCredential(user_id=user.id)
                session.add(credential)
            self._store_token(credential, value)
            session.flush()
            return user.id

    async def access_token(self, user_id):
        stamp = now()
        with self.session_factory() as session, session.begin():
            row = session.scalar(select(GitHubCredential).where(GitHubCredential.user_id == user_id).with_for_update())
            if row is None or row.revoked_at or row.refresh_started_at:
                raise GitHubError("github_reauthorization_required")
            if row.expires_at and row.expires_at > stamp + timedelta(seconds=60):
                return self.cipher.decrypt(row.access_encrypted)
            if not row.refresh_encrypted or not row.refresh_expires_at or row.refresh_expires_at <= stamp:
                raise GitHubError("github_reauthorization_required")
            refresh = self.cipher.decrypt(row.refresh_encrypted)
            # Commit the intent before calling the rotating refresh endpoint.
            # Any uncertainty requires login; the consumed token is never retried.
            row.refresh_started_at = stamp
        try:
            value = await GitHubAppClient(self.settings, transport=self.transport).oauth({
                "grant_type": "refresh_token", "refresh_token": refresh})
            with self.session_factory() as session, session.begin():
                row = session.scalar(select(GitHubCredential).where(GitHubCredential.user_id == user_id).with_for_update())
                if row is None or row.refresh_started_at != stamp or row.revoked_at:
                    raise GitHubError("github_reauthorization_required")
                self._store_token(row, value)
            return value["access_token"]
        except Exception:
            with self.session_factory() as session, session.begin():
                session.execute(update(GitHubCredential).where(GitHubCredential.user_id == user_id,
                    GitHubCredential.refresh_started_at == stamp).values(revoked_at=now()))
            raise GitHubError("github_reauthorization_required") from None

    def record_grants(self, user_id, pairs):
        with self.session_factory() as session, session.begin():
            from sqlalchemy import text
            # Serialize only cached grant writes. Authorization always uses the
            # live user-token membership results, never these cached rows.
            session.execute(text("SELECT pg_advisory_xact_lock(578109564)"))
            for installation, repository in pairs:
                row = session.get(GitHubInstallation, installation["id"])
                if row is None:
                    row = GitHubInstallation(id=installation["id"])
                    session.add(row)
                row.account_id, row.account_login = installation["account"]["id"], installation["account"]["login"]
                row.permissions_json = installation.get("permissions", {})
                row.repository_selection = installation.get("repository_selection", "selected")
                row.revoked_at, row.updated_at = None, now()
                session.flush()
                grant = session.scalar(select(GitHubRepositoryGrant).where(GitHubRepositoryGrant.user_id == user_id,
                    GitHubRepositoryGrant.repository_id == repository["id"]))
                if grant is None:
                    grant = GitHubRepositoryGrant(user_id=user_id, repository_id=repository["id"])
                    session.add(grant)
                grant.installation_id, grant.full_name = row.id, repository["full_name"]
                grant.permissions_json, grant.checked_at, grant.revoked_at = repository.get("permissions", {}), now(), None
