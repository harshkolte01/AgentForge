"""GitHub identity signup, encrypted short-lived tokens and rotation fencing."""
import asyncio
import json
from datetime import timedelta
from secrets import compare_digest
from time import monotonic
from uuid import uuid4
import httpx
from sqlalchemy import select, func, update, text, delete
from sqlalchemy.dialects.postgresql import insert
from agentforge.auth.crypto import CredentialCipher
from agentforge.auth.durable import digest, now
from agentforge.database.models import User, Project
from agentforge.database.identity import OAuthTransaction, GitHubCredential, GitHubInstallation, GitHubRepositoryGrant
from agentforge.integrations.github_app import GitHubAppClient, valid_github_id
from agentforge.integrations.github_app import github_for_user  # public shared task adapter
from agentforge.integrations.github_client import GitHubError

# A DB-wide ceiling only; per-client limiting belongs to the proxy (infra/deploy/nginx.conf).
OAUTH_STARTS_PER_MINUTE = 1000
# Concurrent callers wait this long for another request's refresh; a fence older than the stale
# limit belongs to a crashed attempt (the HTTP timeout is 20s) and may be retried.
REFRESH_WAIT_SEC, REFRESH_STALE_SEC = 10, 60
# GitHub's token-endpoint `error` codes worth telling apart; anything else is a generic failure.
TOKEN_ERRORS = {"bad_verification_code": "github_authorization_expired",
    "incorrect_client_credentials": "github_app_misconfigured", "redirect_uri_mismatch": "github_app_misconfigured",
    "bad_refresh_token": "github_reauthorization_required"}

def github_configured(settings):
    try:
        CredentialCipher(settings.github_token_encryption_key)
    except ValueError:
        return False
    return bool(settings.auth_mode == "github_app" and settings.github_client_id
        and settings.github_client_secret and settings.github_app_id)

def callback_url(settings):
    return str(settings.github_oauth_callback_url or (str(settings.api_public_base_url).rstrip("/") + "/api/v1/auth/github/callback"))


class TokenEndpointProbe(httpx.AsyncBaseTransport):
    """Observes one OAuth token request so its failure can be classified: did it reach GitHub, and what did
    GitHub answer? Keeps only the status and GitHub's `error` code, never a token."""

    def __init__(self, inner=None):
        self.inner = inner or httpx.AsyncHTTPTransport(trust_env=False)
        self.outcome, self.status, self.error = None, None, None

    async def handle_async_request(self, request):
        try:
            response = await self.inner.handle_async_request(request)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
            # No connection was made, so GitHub never saw the request (a rotating refresh token is unspent).
            self.outcome = "unsent"
            raise
        except BaseException:
            self.outcome = "lost"
            raise
        self.outcome, self.status, body = "response", response.status_code, bytearray()
        try:
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > 20_000:
                    break
        except httpx.HTTPError:
            self.outcome = "lost"
            raise
        finally:
            await response.aclose()
        try:
            value = json.loads(body)
            if isinstance(value, dict) and isinstance(value.get("error"), str):
                self.error = value["error"][:100]
        except ValueError:
            pass
        return httpx.Response(response.status_code, headers={"Content-Type": "application/json"}, content=bytes(body))

    async def aclose(self):
        await self.inner.aclose()


class GitHubIdentityService:
    def __init__(self, session_factory, settings, *, transport=None):
        self.session_factory, self.settings, self.transport = session_factory, settings, transport
        self.cipher = CredentialCipher(settings.github_token_encryption_key)

    def start(self, state, binding, verifier, next_path=None):
        stamp = now()
        with self.session_factory() as session, session.begin():
            # DB-wide admission protects OAuth writes across instances. Expired
            # rows are pruned; row storage is bounded even without a cleanup job.
            session.execute(text("SELECT pg_advisory_xact_lock(578109562)"))
            session.execute(delete(OAuthTransaction).where(OAuthTransaction.expires_at < stamp))
            recent = session.scalar(select(func.count()).select_from(OAuthTransaction).where(
                OAuthTransaction.created_at >= stamp - timedelta(minutes=1)))
            if recent >= OAUTH_STARTS_PER_MINUTE:
                raise GitHubError("github_login_rate_limited")
            # The post-sign-in path rides in the encrypted verifier (verifiers never contain a newline).
            secret = verifier + ("\n" + next_path if next_path else "")
            session.add(OAuthTransaction(state_hash=digest(state), binding_hash=digest(binding),
                verifier_encrypted=self.cipher.encrypt(secret), expires_at=stamp + timedelta(minutes=10)))

    def consume(self, state, binding):
        """Spend the transaction once; returns (verifier, next_path or None)."""
        if not state or not binding or len(state) > 128 or len(binding) > 128:
            raise GitHubError("github_authorization_expired")
        with self.session_factory() as session, session.begin():
            row = session.scalar(select(OAuthTransaction).where(OAuthTransaction.state_hash == digest(state)).with_for_update())
            if (row is None or row.consumed_at or row.expires_at <= now()
                    or not compare_digest(row.binding_hash, digest(binding))):
                raise GitHubError("github_authorization_expired")
            row.consumed_at = now()
            verifier, _, next_path = self.cipher.decrypt(row.verifier_encrypted).partition("\n")
            return verifier, next_path or None

    async def token_request(self, payload):
        """Call GitHub's token endpoint, raising a GitHubError that says why it failed. `github_unreachable`
        means the request never left this host."""
        probe = TokenEndpointProbe(self.transport)
        try:
            return await GitHubAppClient(self.settings, transport=probe).oauth(payload)
        except GitHubError:
            if probe.outcome in {None, "unsent"}:
                raise GitHubError("github_unreachable") from None
            if probe.outcome == "lost" or probe.status >= 500:
                raise GitHubError("github_unavailable") from None
            if probe.status == 429:
                raise GitHubError("github_rate_limited") from None
            if probe.error in TOKEN_ERRORS:
                raise GitHubError(TOKEN_ERRORS[probe.error]) from None
            if probe.status == 200 and probe.error is None:
                # A well-formed answer without a `ghu_` user token: the credentials belong to an OAuth App.
                raise GitHubError("github_app_user_token_required") from None
            raise GitHubError("github_authorization_failed") from None

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
        value = await self.token_request({"code": code, "code_verifier": verifier, "redirect_uri": callback_url(self.settings)})
        profile = await GitHubAppClient(self.settings, token=value["access_token"], transport=self.transport).request("GET", "/user")
        identifier, login = profile.get("id"), profile.get("login")
        if not valid_github_id(identifier) or not isinstance(login, str) or not login or len(login) > 100:
            raise GitHubError("github_identity_invalid")
        # Checked on every sign-in, so removing a login from the list locks out an existing account too.
        if self.settings.github_allowed_logins and login.lower() not in self.settings.github_allowed_logins:
            raise GitHubError("github_login_not_allowed")
        return await asyncio.to_thread(self._save_identity, identifier, login, value)

    def _save_identity(self, identifier, login, value):
        with self.session_factory() as session, session.begin():
            # One lock per GitHub identity makes concurrent initial signups
            # atomic without any implicit email/name account linking.
            session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": identifier})
            user = session.scalar(select(User).where(User.github_user_id == identifier).with_for_update())
            if user is None:
                # A non-empty allowlist (checked in `signup`) is an invitation list: it admits new accounts too.
                if not self.settings.public_signup_enabled and not self.settings.github_allowed_logins:
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
        deadline = monotonic() + REFRESH_WAIT_SEC
        while True:
            action, value = await asyncio.to_thread(self._claim_refresh, user_id)
            if action == "token":
                return value
            if action == "refresh":
                break
            # Another request is rotating the refresh token; its result is what this caller needs.
            if monotonic() >= deadline:
                raise GitHubError("github_unavailable")
            await asyncio.sleep(0.25)
        stamp, refresh = value
        try:
            value = await self.token_request({"grant_type": "refresh_token", "refresh_token": refresh})
        except GitHubError as error:
            if str(error) in {"github_unreachable", "github_rate_limited"}:
                # GitHub never saw, or refused unprocessed (429), the request, so the refresh token is unspent:
                # release the fence and let the caller retry.
                await asyncio.to_thread(self._end_refresh, user_id, stamp, revoke=False)
                raise GitHubError("github_unavailable") from None
            await asyncio.to_thread(self._end_refresh, user_id, stamp, revoke=True)
            raise GitHubError("github_reauthorization_required") from None
        try:
            await asyncio.to_thread(self._finish_refresh, user_id, stamp, value)
        except Exception:
            # The old refresh token is spent and the new one was not stored: only a new sign-in recovers.
            await asyncio.to_thread(self._end_refresh, user_id, stamp, revoke=True)
            raise GitHubError("github_reauthorization_required") from None
        return value["access_token"]

    def _claim_refresh(self, user_id):
        """("token", access) when usable, ("wait", None) while another refresh runs, else fence a refresh and return
        ("refresh", (stamp, refresh_token))."""
        stamp = now()
        with self.session_factory() as session, session.begin():
            row = session.scalar(select(GitHubCredential).where(GitHubCredential.user_id == user_id).with_for_update())
            if row is None or row.revoked_at:
                raise GitHubError("github_reauthorization_required")
            if row.refresh_started_at and row.refresh_started_at > stamp - timedelta(seconds=REFRESH_STALE_SEC):
                return "wait", None
            try:
                if row.expires_at and row.expires_at > stamp + timedelta(seconds=60):
                    return "token", self.cipher.decrypt(row.access_encrypted)
                if not row.refresh_encrypted or not row.refresh_expires_at or row.refresh_expires_at <= stamp:
                    raise GitHubError("github_reauthorization_required")
                refresh = self.cipher.decrypt(row.refresh_encrypted)
            except GitHubError:
                raise
            except ValueError:
                # Encrypted under a different key (GITHUB_TOKEN_ENCRYPTION_KEY changed).
                raise GitHubError("github_reauthorization_required") from None
            # Commit the intent before calling the rotating refresh endpoint. The
            # stamp fences completion: only this attempt may store or revoke.
            row.refresh_started_at = stamp
            return "refresh", (stamp, refresh)

    def _finish_refresh(self, user_id, stamp, value):
        with self.session_factory() as session, session.begin():
            row = session.scalar(select(GitHubCredential).where(GitHubCredential.user_id == user_id).with_for_update())
            if row is None or row.refresh_started_at != stamp or row.revoked_at:
                raise GitHubError("github_reauthorization_required")
            self._store_token(row, value)

    def _end_refresh(self, user_id, stamp, *, revoke):
        values = {"revoked_at": now()} if revoke else {"refresh_started_at": None}
        with self.session_factory() as session, session.begin():
            session.execute(update(GitHubCredential).where(GitHubCredential.user_id == user_id,
                GitHubCredential.refresh_started_at == stamp).values(**values))

    def record_grants(self, user_id, pairs):
        """Cache installations and repository grants. Authorization always uses the live user-token
        membership results, never these rows. Blocking: call through a thread from async code."""
        installations = {installation["id"]: installation for installation, _ in pairs}
        grants = {repository["id"]: (installation["id"], repository) for installation, repository in pairs}
        if not grants:
            return
        stamp = now()
        # Single upserts in key order: concurrent writers serialize on row locks and cannot deadlock.
        statement = insert(GitHubInstallation).values([{"id": key, "account_id": value["account"]["id"],
            "account_login": value["account"]["login"], "permissions_json": value.get("permissions", {}),
            "repository_selection": value.get("repository_selection", "selected"), "revoked_at": None,
            "updated_at": stamp} for key, value in sorted(installations.items())])
        installation_upsert = statement.on_conflict_do_update(index_elements=[GitHubInstallation.id], set_={
            name: statement.excluded[name] for name in ("account_id", "account_login", "permissions_json",
                "repository_selection", "revoked_at", "updated_at")})
        statement = insert(GitHubRepositoryGrant).values([{"id": uuid4(), "user_id": user_id, "repository_id": key,
            "installation_id": installation_id, "full_name": repository["full_name"],
            "permissions_json": repository.get("permissions", {}), "checked_at": stamp, "revoked_at": None}
            for key, (installation_id, repository) in sorted(grants.items())])
        grant_upsert = statement.on_conflict_do_update(index_elements=["user_id", "repository_id"], set_={
            name: statement.excluded[name] for name in ("installation_id", "full_name", "permissions_json",
                "checked_at", "revoked_at")})
        with self.session_factory() as session, session.begin():
            session.execute(installation_upsert)
            session.execute(grant_upsert)
