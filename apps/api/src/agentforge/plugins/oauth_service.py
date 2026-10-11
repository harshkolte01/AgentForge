"""Signing in to plugins with OAuth: starting a sign-in, finishing it at the callback, and keeping the tokens usable.

The flow is bound three ways, so a callback can only finish a sign-in the same person started in the same browser:
  * `state`, a random value in the authorization URL, of which only a hash is kept and which is single use;
  * a cookie set when the sign-in starts, of which only a hash is kept;
  * the signed-in person, who must be the one who started it.
A sign-in that fails for any reason is spent: nothing is retried with the same state.

Tokens are encrypted with the plugin key and read only here and by the MCP client's token source (`OAuthTokens`), which refreshes
an access token before it expires, once when the server refuses it, and never twice at the same time for one connection.
"""
import asyncio
import hmac
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert

from agentforge.auth.crypto import CredentialCipher
from agentforge.database.models import User
from agentforge.database.plugins import PluginConnection, PluginOAuthClient, PluginOAuthFlow, PluginOAuthGrant

from .catalog import BY_KEY
from .mcp import McpError, validate_url
from .oauth import (
    Client, FLOW_TTL, OAuthError, REFRESH_SKEW, TokenClient, Tokens, authorization_url, callback_url, check_issuer, digest, discover,
    exchange_code, metadata_document_url, new_binding, new_state, new_verifier, refresh_tokens, register, revoke,
)
from .schema import clean_text, make_slug
from .service import NAME_LIMIT, PluginError, cipher_for, probe, view, _failure

log = logging.getLogger(__name__)

MAX_PENDING_SIGN_INS = 5
# A registration is renewed when it has less than a day left.
CLIENT_RENEW = timedelta(days=1)


def _fail(error: OAuthError) -> PluginError:
    return PluginError(error.status, error.code, error.message)


def _union(*scopes: str | None) -> str | None:
    seen: dict[str, None] = {}
    for scope in scopes:
        for item in (scope or "").split():
            seen.setdefault(item)
    return " ".join(seen)[:1000] or None


def _secret(cipher: CredentialCipher, value: str | None) -> str | None:
    return cipher.encrypt(value) if value else None


@dataclass(frozen=True)
class Started:
    url: str
    binding: str


@dataclass(frozen=True)
class Grant:
    """A connection's tokens and the client they belong to, as read from the database (still encrypted)."""
    connection_id: UUID
    issuer: str
    token_endpoint: str
    revocation_endpoint: str | None
    resource: str
    client_id: str
    client_secret_encrypted: str | None
    client_auth_method: str
    registration_kind: str
    scope: str | None
    access: str
    refresh: str | None
    expires_at: datetime | None

    def client(self, cipher: CredentialCipher) -> TokenClient:
        secret = cipher.decrypt(self.client_secret_encrypted) if self.client_secret_encrypted else None
        return TokenClient(self.token_endpoint, self.client_id, secret, self.client_auth_method, self.resource)

    def stale(self, now: datetime | None = None) -> bool:
        return self.expires_at is not None and (now or datetime.now(timezone.utc)) >= self.expires_at - REFRESH_SKEW


def _grant_of(row: PluginOAuthGrant) -> Grant:
    return Grant(row.connection_id, row.issuer, row.token_endpoint, row.revocation_endpoint, row.resource, row.client_id,
                 row.client_secret_encrypted, row.client_auth_method, row.registration_kind, row.scope, row.access_token_encrypted,
                 row.refresh_token_encrypted, row.expires_at)


def load_grant(sessions, connection_id: UUID) -> Grant | None:
    with sessions() as db:
        row = db.get(PluginOAuthGrant, connection_id)
        return _grant_of(row) if row else None


def mark_reauthorize(sessions, connection_id: UUID, message: str, scope: str | None = None) -> None:
    """The sign-in can no longer be used: the connection says so (and, when more access is needed, remembers what)."""
    with sessions() as db, db.begin():
        row = db.get(PluginConnection, connection_id, with_for_update=True)
        if row is None:
            return
        row.status, row.last_error = "reauthorize", message
        if scope:
            grant = db.get(PluginOAuthGrant, connection_id, with_for_update=True)
            if grant is not None:
                grant.scope = _union(grant.scope, scope)


# Refreshes of one connection never overlap inside a process (a refresh token that rotates would be spent by the first of two).
_LOCKS: dict[UUID, asyncio.Lock] = {}


class OAuthTokens:
    """The MCP client's source of the Authorization header for one signed-in connection."""

    def __init__(self, sessions, settings, connection_id: UUID) -> None:
        self.sessions, self.settings, self.connection_id = sessions, settings, connection_id
        self._used: str | None = None

    def _cipher(self) -> CredentialCipher:
        cipher = cipher_for(self.settings)
        if cipher is None:
            raise McpError("PLUGIN_AUTH_INVALID", "This plugin's sign-in cannot be read because no encryption key is configured.")
        return cipher

    def _read(self, grant: Grant) -> str:
        try:
            return self._cipher().decrypt(grant.access)
        except ValueError:
            raise McpError("PLUGIN_AUTH_INVALID", "This plugin's saved sign-in can no longer be read. Reconnect it.") from None

    async def headers(self) -> dict[str, str]:
        grant = await asyncio.to_thread(load_grant, self.sessions, self.connection_id)
        if grant is None:
            raise McpError("PLUGIN_REAUTHORIZE", "This plugin is not signed in. Open it and choose Reconnect.")
        if grant.stale():
            grant = await self._renew(grant, force=False)
        self._used = self._read(grant)
        return {"Authorization": f"Bearer {self._used}"}

    async def rejected(self) -> bool:
        grant = await asyncio.to_thread(load_grant, self.sessions, self.connection_id)
        if grant is None:
            return False
        if self._used is not None and self._read(grant) != self._used:
            # Another call already got a new token since this one was sent.
            return True
        try:
            await self._renew(grant, force=True)
        except McpError:
            return False
        return True

    async def insufficient(self, scope: str | None) -> None:
        await asyncio.to_thread(mark_reauthorize, self.sessions, self.connection_id,
                                "This plugin needs more access than was granted. Reconnect to grant it.", scope)

    async def _renew(self, seen: Grant, *, force: bool) -> Grant:
        cipher = self._cipher()
        allow_private = self.settings.plugin_allow_private_network
        async with _LOCKS.setdefault(self.connection_id, asyncio.Lock()):
            grant = await asyncio.to_thread(load_grant, self.sessions, self.connection_id)
            if grant is None:
                raise McpError("PLUGIN_REAUTHORIZE", "This plugin is not signed in. Open it and choose Reconnect.")
            if grant.access != seen.access or (not force and not grant.stale()):
                return grant  # someone renewed it while this call waited
            if not grant.refresh:
                await self._give_up("This plugin's sign-in has expired. Reconnect it.")
            try:
                tokens = await refresh_tokens(grant.client(cipher), cipher.decrypt(grant.refresh), allow_private=allow_private)
            except ValueError:
                await self._give_up("This plugin's saved sign-in can no longer be read. Reconnect it.")
            except OAuthError as error:
                if error.code in ("plugin_oauth_grant_rejected", "plugin_oauth_client_rejected"):
                    # A refresh token that rotates may have been spent by another process a moment ago: use what it saved.
                    latest = await asyncio.to_thread(load_grant, self.sessions, self.connection_id)
                    if latest is not None and latest.refresh != grant.refresh:
                        return latest
                    if error.code == "plugin_oauth_client_rejected" and grant.registration_kind == "dynamic":
                        await asyncio.to_thread(self._forget_client, grant)
                    await self._give_up("This plugin's sign-in has expired or was withdrawn. Reconnect it.")
                raise McpError("PLUGIN_AUTH_UNAVAILABLE", "The sign-in service could not be reached to renew this plugin's access. Try again shortly.") from None
            saved = await asyncio.to_thread(self._save, grant, tokens, cipher)
            return saved

    async def _give_up(self, message: str):
        await asyncio.to_thread(mark_reauthorize, self.sessions, self.connection_id, message)
        raise McpError("PLUGIN_REAUTHORIZE", message)

    def _forget_client(self, grant: Grant) -> None:
        with self.sessions() as db, db.begin():
            db.execute(delete(PluginOAuthClient).where(PluginOAuthClient.issuer == grant.issuer, PluginOAuthClient.client_id == grant.client_id))

    def _save(self, previous: Grant, tokens: Tokens, cipher: CredentialCipher) -> Grant:
        """Writes renewed tokens, only if nobody wrote since they were read (then theirs stand and are returned)."""
        with self.sessions() as db, db.begin():
            row = db.get(PluginOAuthGrant, self.connection_id, with_for_update=True)
            if row is None:
                raise McpError("PLUGIN_REAUTHORIZE", "This plugin is not signed in. Open it and choose Reconnect.")
            if row.refresh_token_encrypted != previous.refresh or row.access_token_encrypted != previous.access:
                return _grant_of(row)
            row.access_token_encrypted = cipher.encrypt(tokens.access)
            row.refresh_token_encrypted = cipher.encrypt(tokens.refresh) if tokens.refresh else None
            row.expires_at = tokens.expires_at
            row.scope = tokens.scope or row.scope
            connection = db.get(PluginConnection, self.connection_id, with_for_update=True)
            if connection is not None and connection.status == "reauthorize":
                connection.status, connection.last_error = "connected", None
            db.flush()
            return _grant_of(row)


@dataclass(frozen=True)
class Revocation:
    """What it takes to tell an authorization server to forget a connection's tokens, kept past the connection's deletion."""
    endpoint: str
    client: TokenClient
    token: str
    hint: str
    allow_private: bool


async def send_revocation(revocation: Revocation) -> None:
    await revoke(revocation.endpoint, revocation.client, revocation.token, revocation.hint, allow_private=revocation.allow_private)


def revocation_for(db, connection_id: UUID, settings) -> Revocation | None:
    """Read before a connection is deleted. None when there is nothing to revoke or the server has no revocation endpoint."""
    row = db.get(PluginOAuthGrant, connection_id)
    cipher = cipher_for(settings)
    if row is None or not row.revocation_endpoint or cipher is None:
        return None
    try:
        grant = _grant_of(row)
        refresh = cipher.decrypt(grant.refresh) if grant.refresh else None
        return Revocation(row.revocation_endpoint, grant.client(cipher), refresh or cipher.decrypt(grant.access),
                          "refresh_token" if refresh else "access_token", settings.plugin_allow_private_network)
    except ValueError:
        return None


class OAuthService:
    def __init__(self, sessions, settings) -> None:
        self.sessions, self.settings = sessions, settings

    def _require(self) -> CredentialCipher:
        if not self.settings.plugins_enabled:
            raise PluginError(403, "plugins_disabled", "Plugins are turned off on this server.")
        if not self.settings.plugin_oauth_enabled:
            raise PluginError(403, "plugin_oauth_disabled", "Signing in to plugins is turned off on this server.")
        cipher = cipher_for(self.settings)
        if cipher is None:
            raise PluginError(409, "plugin_encryption_not_configured",
                              "Signing in needs an encryption key to keep the tokens. Ask the operator to set PLUGIN_ENCRYPTION_KEY.")
        return cipher

    # --- starting ----------------------------------------------------------------------------------------------------
    def _target(self, user_id: UUID, body) -> dict:
        """Which server a sign-in is for, checked before any network call: a catalog plugin that signs in, a server the person
        typed in, or a connection they already have (to sign in again)."""
        with self.sessions() as db:
            rows = list(db.scalars(select(PluginConnection).where(PluginConnection.user_id == user_id)))
            if body.connection_id is not None:
                row = next((item for item in rows if item.id == body.connection_id), None)
                if row is None:
                    raise PluginError(404, "plugin_not_found", "That plugin is not connected.")
                grant = db.get(PluginOAuthGrant, row.id)
                return {"plugin_key": row.plugin_key, "name": row.name, "url": row.server_url, "connection_id": row.id, "scope": grant.scope if grant else None}
        entry = None
        if body.plugin_key != "custom":
            entry = BY_KEY.get(body.plugin_key)
            if entry is None:
                raise PluginError(404, "plugin_unknown", "That plugin is not in the catalog.")
            if not entry.oauth:
                raise PluginError(422, "plugin_oauth_unsupported", f"{entry.name} does not sign in with OAuth.")
            name, url = entry.name, entry.server_url
        else:
            name, url = clean_text(body.name or "", NAME_LIMIT), (body.server_url or "").strip()
            if not name:
                raise PluginError(422, "plugin_name_required", "Give the plugin a name.")
            if not url:
                raise PluginError(422, "plugin_url_required", "Enter the server's address.")
        try:
            url = validate_url(url, allow_private=self.settings.plugin_allow_private_network)[0]
        except McpError as error:
            raise _failure(error) from None
        if len(rows) >= self.settings.plugin_max_connections:
            raise PluginError(409, "plugin_limit_reached", f"You can connect up to {self.settings.plugin_max_connections} plugins. Remove one first.")
        if entry is not None and any(row.plugin_key == entry.key for row in rows):
            raise PluginError(409, "plugin_already_connected", f"{entry.name} is already connected.")
        return {"plugin_key": entry.key if entry else "custom", "name": name, "url": url, "connection_id": None, "scope": None}

    def _cached_client(self, cipher: CredentialCipher, issuer: str, redirect: str) -> Client | None:
        with self.sessions() as db:
            row = db.scalar(select(PluginOAuthClient).where(PluginOAuthClient.issuer == issuer, PluginOAuthClient.redirect_uri == redirect))
            if row is None or (row.expires_at is not None and row.expires_at <= datetime.now(timezone.utc) + CLIENT_RENEW):
                return None
            try:
                return Client(row.client_id, cipher.decrypt(row.client_secret_encrypted) if row.client_secret_encrypted else None,
                              row.client_auth_method, "dynamic", row.expires_at)
            except ValueError:
                return None

    def _remember_client(self, cipher: CredentialCipher, issuer: str, redirect: str, client: Client) -> None:
        values = {"issuer": issuer, "redirect_uri": redirect, "client_id": client.client_id, "client_secret_encrypted": _secret(cipher, client.secret),
                  "client_auth_method": client.method, "expires_at": client.expires_at}
        with self.sessions() as db, db.begin():
            statement = insert(PluginOAuthClient).values(**values)
            db.execute(statement.on_conflict_do_update(index_elements=["issuer", "redirect_uri"], set_={key: value for key, value in values.items()
                                                                                                       if key not in ("issuer", "redirect_uri")}))

    def _save_flow(self, user_id: UUID, values: dict) -> None:
        with self.sessions() as db, db.begin():
            db.execute(delete(PluginOAuthFlow).where(PluginOAuthFlow.expires_at < func.now()))
            stale = list(db.scalars(select(PluginOAuthFlow.id).where(PluginOAuthFlow.user_id == user_id)
                                    .order_by(PluginOAuthFlow.created_at.desc()).offset(MAX_PENDING_SIGN_INS - 1)))
            if stale:
                db.execute(delete(PluginOAuthFlow).where(PluginOAuthFlow.id.in_(stale)))
            db.add(PluginOAuthFlow(user_id=user_id, **values))

    async def start(self, user_id: UUID, body) -> Started:
        """Looks the server up, registers this app with it, and returns the address the person's browser goes to."""
        cipher = self._require()
        settings = self.settings
        allow_private = settings.plugin_allow_private_network
        target = await asyncio.to_thread(self._target, user_id, body)
        try:
            discovery = await discover(target["url"], allow_private=allow_private)
        except OAuthError as error:
            raise _fail(error) from None
        redirect = callback_url(settings)
        given = (body.client_id.strip(), (body.client_secret or "").strip() or None) if body.client_id and body.client_id.strip() else None
        uses_document = discovery.metadata_documents and metadata_document_url(settings) and settings.plugin_oauth_registration == "auto"
        cached = None if given or uses_document else await asyncio.to_thread(self._cached_client, cipher, discovery.issuer, redirect)
        try:
            client = await register(discovery, settings, allow_private=allow_private, given=given, cached=cached)
        except OAuthError as error:
            raise _fail(error) from None
        if client.kind == "dynamic" and cached is None:
            await asyncio.to_thread(self._remember_client, cipher, discovery.issuer, redirect, client)
        scope = _union(target["scope"], discovery.scope)
        state, binding, verifier = new_state(), new_binding(), new_verifier()
        await asyncio.to_thread(self._save_flow, user_id, dict(
            state_hash=digest(state), binding_hash=digest(binding), connection_id=target["connection_id"], plugin_key=target["plugin_key"],
            name=target["name"], server_url=target["url"], resource=discovery.resource, issuer=discovery.issuer, token_endpoint=discovery.token_endpoint,
            revocation_endpoint=discovery.revocation_endpoint, iss_supported=discovery.iss_supported, client_id=client.client_id,
            client_secret_encrypted=_secret(cipher, client.secret), client_auth_method=client.method, registration_kind=client.kind,
            redirect_uri=redirect, scope=scope, verifier_encrypted=cipher.encrypt(verifier), expires_at=datetime.now(timezone.utc) + FLOW_TTL))
        return Started(authorization_url(discovery, client.client_id, redirect, state, verifier, scope), binding)

    # --- finishing ---------------------------------------------------------------------------------------------------
    def _consume(self, user_id: UUID, state: str, binding: str | None) -> dict:
        """The sign-in `state` belongs to, if this is the person and the browser that started it. Spent either way."""
        with self.sessions() as db, db.begin():
            flow = db.scalar(select(PluginOAuthFlow).where(PluginOAuthFlow.state_hash == digest(state)).with_for_update()) if state else None
            if flow is None:
                raise PluginError(400, "plugin_oauth_state_invalid", "That sign-in is no longer valid. Start it again.")
            snapshot = {column.name: getattr(flow, column.name) for column in PluginOAuthFlow.__table__.columns}
            db.delete(flow)
        if snapshot["expires_at"] <= datetime.now(timezone.utc):
            raise PluginError(400, "plugin_oauth_expired", "That sign-in took too long. Start it again.")
        if not binding or not hmac.compare_digest(snapshot["binding_hash"], digest(binding)) or snapshot["user_id"] != user_id:
            raise PluginError(400, "plugin_oauth_state_invalid", "That sign-in did not start in this browser. Start it again.")
        return snapshot

    def _save(self, flow: dict, tokens: Tokens, cipher: CredentialCipher, server_name: str | None, specs: list[dict]) -> dict:
        """Creates the connection (or updates the one being signed in again) and stores its grant, together."""
        stamp = datetime.now(timezone.utc)
        with self.sessions() as db, db.begin():
            db.scalar(select(User).where(User.id == flow["user_id"]).with_for_update())
            rows = list(db.scalars(select(PluginConnection).where(PluginConnection.user_id == flow["user_id"])))
            if flow["connection_id"] is not None:
                row = next((item for item in rows if item.id == flow["connection_id"]), None)
                if row is None:
                    raise PluginError(404, "plugin_not_found", "That plugin is no longer connected.")
                row.auth_kind, row.auth_header, row.credential_encrypted = "oauth", None, None
                row.status, row.last_error, row.server_name, row.tools_json, row.tools_synced_at = "connected", None, server_name, specs, stamp
                row.disabled_tools = [name for name in row.disabled_tools or [] if name in {tool["name"] for tool in specs}]
            else:
                if len(rows) >= self.settings.plugin_max_connections:
                    raise PluginError(409, "plugin_limit_reached", "You have reached the plugin limit. Remove one first.")
                if flow["plugin_key"] != "custom" and any(item.plugin_key == flow["plugin_key"] for item in rows):
                    raise PluginError(409, "plugin_already_connected", f"{flow['name']} is already connected.")
                slug = make_slug(flow["plugin_key"] if flow["plugin_key"] != "custom" else flow["name"], {item.slug for item in rows})
                row = PluginConnection(user_id=flow["user_id"], plugin_key=flow["plugin_key"], slug=slug, name=flow["name"], server_url=flow["server_url"],
                                       auth_kind="oauth", enabled=True, status="connected", last_error=None, server_name=server_name, tools_json=specs,
                                       disabled_tools=[], tools_synced_at=stamp)
                db.add(row)
                db.flush()
            grant = db.get(PluginOAuthGrant, row.id, with_for_update=True)
            fields = dict(issuer=flow["issuer"], token_endpoint=flow["token_endpoint"], revocation_endpoint=flow["revocation_endpoint"], resource=flow["resource"],
                          client_id=flow["client_id"], client_secret_encrypted=flow["client_secret_encrypted"], client_auth_method=flow["client_auth_method"],
                          registration_kind=flow["registration_kind"], scope=tokens.scope or flow["scope"], access_token_encrypted=cipher.encrypt(tokens.access),
                          refresh_token_encrypted=cipher.encrypt(tokens.refresh) if tokens.refresh else None, expires_at=tokens.expires_at)
            if grant is None:
                db.add(PluginOAuthGrant(connection_id=row.id, **fields))
            else:
                for key, value in fields.items():
                    setattr(grant, key, value)
            db.flush()
            db.refresh(row)
            return view(row)

    async def finish(self, user_id: UUID, *, state: str, code: str, iss: str | None, error: str, binding: str | None) -> dict:
        """The connection the sign-in produced. PluginError for anything that stops it, with a code the app can show."""
        cipher = self._require()
        flow = await asyncio.to_thread(self._consume, user_id, state, binding)
        try:
            # The issuer is checked before anything else the response says is acted on or shown (RFC 9207).
            check_issuer(iss, flow["issuer"], advertised=flow["iss_supported"])
        except OAuthError as problem:
            raise _fail(problem) from None
        if error:
            denied = error == "access_denied"
            raise PluginError(400, "plugin_oauth_denied" if denied else "plugin_oauth_failed",
                              "The sign-in was cancelled." if denied else "The sign-in service reported a problem. Try again.")
        if not code or len(code) > 4096:
            raise PluginError(400, "plugin_oauth_state_invalid", "The sign-in response was incomplete. Start it again.")
        try:
            client = TokenClient(flow["token_endpoint"], flow["client_id"], cipher.decrypt(flow["client_secret_encrypted"]) if flow["client_secret_encrypted"] else None,
                                 flow["client_auth_method"], flow["resource"])
            tokens = await exchange_code(client, code, cipher.decrypt(flow["verifier_encrypted"]), flow["redirect_uri"],
                                         allow_private=self.settings.plugin_allow_private_network)
        except ValueError:
            raise PluginError(409, "plugin_credential_unreadable", "The sign-in could not be completed. Try again.") from None
        except OAuthError as problem:
            if problem.code == "plugin_oauth_client_rejected" and flow["registration_kind"] == "dynamic":
                await asyncio.to_thread(self._forget, flow)
            raise _fail(problem) from None
        try:
            server_name, specs = await probe(flow["server_url"], {"Authorization": f"Bearer {tokens.access}"}, self.settings)
        except McpError as problem:
            raise _failure(problem) from None
        return await asyncio.to_thread(self._save, flow, tokens, cipher, server_name, specs)

    def _forget(self, flow: dict) -> None:
        with self.sessions() as db, db.begin():
            db.execute(delete(PluginOAuthClient).where(PluginOAuthClient.issuer == flow["issuer"], PluginOAuthClient.client_id == flow["client_id"]))
