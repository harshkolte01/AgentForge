"""Connecting, refreshing and removing a person's plugins, and choosing which ones a conversation may use.

A connection is saved only after the server has answered: it must initialize and list at least one usable tool. The
credential is stored encrypted and is never returned by any call; only whether one is set. Network work is async, database
work is synchronous (called in a thread by the routes).
"""
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import Text, cast, func, select, update

from agentforge.auth.crypto import CredentialCipher
from agentforge.database.models import User
from agentforge.database.plugins import PluginConnection
from agentforge.database.sessions import Conversation, TurnExecution

from .catalog import BY_KEY, CatalogPlugin
from .mcp import McpClient, McpError, clean_headers, validate_url
from .schema import clean_text, make_slug, tool_specs

log = logging.getLogger(__name__)

NAME_LIMIT = 60
DEFAULT_HEADER = "Authorization"


class PluginError(Exception):
    """A request that cannot be done, with the HTTP status, a stable code and a message a person can act on."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def cipher_for(settings) -> CredentialCipher | None:
    key = settings.plugin_secret
    return CredentialCipher(key, domain="plugin") if key is not None else None


def auth_headers(row: PluginConnection, cipher: CredentialCipher | None) -> dict[str, str]:
    """The header that carries a connection's credential, or none. Raises PluginError when it cannot be read."""
    if row.auth_kind == "none" or not row.credential_encrypted:
        return {}
    if cipher is None:
        raise PluginError(409, "plugin_encryption_not_configured", "Plugin credentials cannot be read because no encryption key is configured.")
    try:
        secret = cipher.decrypt(row.credential_encrypted)
    except ValueError:
        raise PluginError(409, "plugin_credential_unreadable", "The saved credential can no longer be read. Enter it again.") from None
    return {DEFAULT_HEADER: f"Bearer {secret}"} if row.auth_kind == "bearer" else {row.auth_header or DEFAULT_HEADER: secret}


def view(row: PluginConnection) -> dict[str, Any]:
    """What the browser may see of a connection. Never the credential, only whether there is one."""
    catalog = BY_KEY.get(row.plugin_key)
    disabled = set(row.disabled_tools or [])
    tools = [{"name": tool["name"], "title": tool.get("title") or "", "description": (tool.get("description") or "")[:300],
              "read_only": bool(tool.get("read_only")), "enabled": tool["name"] not in disabled} for tool in row.tools_json or []]
    return {"id": str(row.id), "plugin_key": row.plugin_key, "name": row.name, "server_url": row.server_url,
            "description": catalog.description if catalog else None,
            "auth": "none" if row.auth_kind == "none" else "oauth" if row.auth_kind == "oauth" else "token",
            "has_credential": bool(row.credential_encrypted) or row.auth_kind == "oauth", "enabled": row.enabled, "status": row.status, "last_error": row.last_error,
            "server_name": row.server_name, "tools": tools, "tool_count": len(tools), "enabled_tool_count": sum(tool["enabled"] for tool in tools),
            "tools_synced_at": row.tools_synced_at.isoformat() if row.tools_synced_at else None, "created_at": row.created_at.isoformat() if row.created_at else None}


async def probe(url: str, headers: dict[str, str], settings, auth=None) -> tuple[str | None, list[dict]]:
    """Connects, and returns the server's name and its usable tools. McpError on any failure."""
    client = McpClient(url, headers=headers, allow_private=settings.plugin_allow_private_network, timeout=min(30, settings.plugin_call_timeout_sec), auth=auth)
    try:
        specs = tool_specs(await client.list_tools())
        if not specs:
            raise McpError("PLUGIN_NO_TOOLS", "The server connected but offers no tools.")
        return client.server_name, specs
    finally:
        await client.aclose()


def _failure(error: McpError) -> PluginError:
    return PluginError(422, error.code.lower(), error.message)


class PluginService:
    def __init__(self, sessions, settings) -> None:
        self.sessions, self.settings = sessions, settings

    # --- reads -------------------------------------------------------------------------------------------------------
    def _require_enabled(self) -> None:
        if not self.settings.plugins_enabled:
            raise PluginError(403, "plugins_disabled", "Plugins are turned off on this server.")

    def _owned(self, db, user_id: UUID, connection_id: UUID, *, lock: bool = False) -> PluginConnection:
        query = select(PluginConnection).where(PluginConnection.id == connection_id, PluginConnection.user_id == user_id)
        row = db.scalar(query.with_for_update() if lock else query)
        if row is None:
            raise PluginError(404, "plugin_not_found", "That plugin is not connected.")
        return row

    def list(self, user_id: UUID) -> list[dict]:
        with self.sessions() as db:
            return [view(row) for row in db.scalars(select(PluginConnection).where(PluginConnection.user_id == user_id)
                                                    .order_by(PluginConnection.created_at, PluginConnection.id))]

    def capabilities(self) -> dict[str, Any]:
        from .oauth import callback_url
        encrypted = cipher_for(self.settings) is not None
        # `oauth_redirect_uri` is what a person registers when they sign in with an OAuth app of their own.
        return {"enabled": self.settings.plugins_enabled, "credentials": encrypted, "max_connections": self.settings.plugin_max_connections,
                "oauth": self.settings.plugin_oauth_enabled and encrypted, "oauth_redirect_uri": callback_url(self.settings)}

    # --- connecting --------------------------------------------------------------------------------------------------
    def _plan(self, user_id: UUID, body) -> dict[str, Any]:
        """Everything a new connection needs, checked before any network call."""
        self._require_enabled()
        entry: CatalogPlugin | None = None
        if body.plugin_key != "custom":
            entry = BY_KEY.get(body.plugin_key)
            if entry is None:
                raise PluginError(404, "plugin_unknown", "That plugin is not in the catalog.")
        token = (body.token or "").strip() or None
        if entry is not None:
            name, url, header = entry.name, entry.server_url, None
            if entry.auth == "required" and not token:
                raise PluginError(422, "plugin_token_required", f"{entry.name} needs an access token ({entry.token_label}).")
            if entry.auth == "none":
                token = None
        else:
            name = clean_text(body.name or "", NAME_LIMIT)
            url, header = (body.server_url or "").strip(), (body.header_name or "").strip() or None
            if not name:
                raise PluginError(422, "plugin_name_required", "Give the plugin a name.")
            if not url:
                raise PluginError(422, "plugin_url_required", "Enter the server's address.")
        try:
            url = validate_url(url, allow_private=self.settings.plugin_allow_private_network)[0]
            if header:
                clean_headers({header: "x"})
        except McpError as error:
            raise _failure(error) from None
        if token and cipher_for(self.settings) is None:
            raise PluginError(409, "plugin_encryption_not_configured",
                              "Saving a credential needs an encryption key. Ask the operator to set PLUGIN_ENCRYPTION_KEY.")
        kind = "none" if not token else ("header" if header and header.lower() != DEFAULT_HEADER.lower() else "bearer")
        with self.sessions() as db:
            rows = list(db.scalars(select(PluginConnection).where(PluginConnection.user_id == user_id)))
        if len(rows) >= self.settings.plugin_max_connections:
            raise PluginError(409, "plugin_limit_reached", f"You can connect up to {self.settings.plugin_max_connections} plugins. Remove one first.")
        if entry is not None and any(row.plugin_key == entry.key for row in rows):
            raise PluginError(409, "plugin_already_connected", f"{entry.name} is already connected.")
        return {"entry": entry, "name": name, "url": url, "token": token, "kind": kind, "header": header if kind == "header" else None}

    async def connect(self, user_id: UUID, body) -> dict:
        plan = self._plan(user_id, body)
        cipher = cipher_for(self.settings) if plan["token"] else None
        headers = ({DEFAULT_HEADER: f"Bearer {plan['token']}"} if plan["kind"] == "bearer"
                   else {plan["header"]: plan["token"]} if plan["kind"] == "header" else {})
        try:
            server_name, specs = await probe(plan["url"], headers, self.settings)
        except McpError as error:
            if error.code == "PLUGIN_AUTH_FAILED" and not plan["token"] and self.settings.plugin_oauth_enabled:
                await self._offer_oauth(plan["url"])
            raise _failure(error) from None
        encrypted = cipher.encrypt(plan["token"]) if cipher and plan["token"] else None
        with self.sessions() as db, db.begin():
            # Rechecked under the user's row lock: two requests at once cannot connect the same plugin twice or pass the limit.
            db.scalar(select(User).where(User.id == user_id).with_for_update())
            rows = list(db.scalars(select(PluginConnection).where(PluginConnection.user_id == user_id)))
            if len(rows) >= self.settings.plugin_max_connections:
                raise PluginError(409, "plugin_limit_reached", "You have reached the plugin limit. Remove one first.")
            if plan["entry"] is not None and any(row.plugin_key == plan["entry"].key for row in rows):
                raise PluginError(409, "plugin_already_connected", f"{plan['name']} is already connected.")
            slug = make_slug(plan["entry"].key if plan["entry"] else plan["name"], {row.slug for row in rows})
            row = PluginConnection(user_id=user_id, plugin_key=plan["entry"].key if plan["entry"] else "custom", slug=slug, name=plan["name"],
                server_url=plan["url"], auth_kind=plan["kind"], auth_header=plan["header"], credential_encrypted=encrypted, enabled=True,
                status="connected", last_error=None, server_name=server_name, tools_json=specs, disabled_tools=[],
                tools_synced_at=datetime.now(timezone.utc))
            db.add(row)
            db.flush()
            db.refresh(row)
            return view(row)

    async def _offer_oauth(self, url: str) -> None:
        """A server that turned a request without a credential away: when it signs people in with OAuth, say so, so the app can
        send the person to sign in (the request is answered 409 `plugin_oauth_required`)."""
        from .oauth import OAuthError, discover
        try:
            await discover(url, allow_private=self.settings.plugin_allow_private_network)
        except OAuthError:
            return
        if cipher_for(self.settings) is None:
            raise PluginError(409, "plugin_encryption_not_configured",
                              "This server signs in with OAuth, which needs an encryption key to keep the tokens. Ask the operator to set PLUGIN_ENCRYPTION_KEY.")
        raise PluginError(409, "plugin_oauth_required", "This server signs in with OAuth. Continue to sign in.")

    # --- changing ----------------------------------------------------------------------------------------------------
    async def refresh(self, user_id: UUID, connection_id: UUID) -> dict:
        """Reads the server's tools again. A failure is recorded on the connection (the tools it had stay), not raised."""
        self._require_enabled()
        auth = None
        with self.sessions() as db:
            row = self._owned(db, user_id, connection_id)
            url, cipher = row.server_url, cipher_for(self.settings)
            try:
                headers = auth_headers(row, cipher)
            except PluginError as error:
                return self._record_failure(user_id, connection_id, error.code, error.message)
            if row.auth_kind == "oauth":
                from .oauth_service import OAuthTokens
                auth = OAuthTokens(self.sessions, self.settings, connection_id)
        try:
            server_name, specs = await probe(url, headers, self.settings, auth)
        except McpError as error:
            return self._record_failure(user_id, connection_id, error.code.lower(), error.message)
        with self.sessions() as db, db.begin():
            row = self._owned(db, user_id, connection_id, lock=True)
            row.tools_json, row.server_name, row.status, row.last_error = specs, server_name, "connected", None
            row.tools_synced_at = datetime.now(timezone.utc)
            # A tool the server no longer offers cannot stay switched off.
            row.disabled_tools = [name for name in row.disabled_tools or [] if name in {tool["name"] for tool in specs}]
            return view(row)

    def _record_failure(self, user_id: UUID, connection_id: UUID, code: str, message: str) -> dict:
        with self.sessions() as db, db.begin():
            row = self._owned(db, user_id, connection_id, lock=True)
            # A sign-in that no longer works is not the server being down: the person is asked to sign in again.
            row.status = "reauthorize" if code in ("plugin_reauthorize", "plugin_scope_insufficient") else "error"
            row.last_error = message
            return view(row)

    async def update(self, user_id: UUID, connection_id: UUID, patch) -> dict:
        self._require_enabled()
        fields = patch.model_fields_set
        token = (patch.token or "").strip() if "token" in fields else None
        if token:
            with self.sessions() as db:
                row = self._owned(db, user_id, connection_id)
                if row.auth_kind == "oauth":
                    raise PluginError(422, "plugin_token_not_used", "This plugin signs in with OAuth. Choose Reconnect to sign in again.")
                listed = BY_KEY.get(row.plugin_key)
                if listed is not None and listed.auth == "none" and not listed.oauth:
                    raise PluginError(422, "plugin_token_not_used", "This plugin does not use a credential.")
                url, kind, header = row.server_url, row.auth_kind, row.auth_header
            cipher = cipher_for(self.settings)
            if cipher is None:
                raise PluginError(409, "plugin_encryption_not_configured", "Saving a credential needs an encryption key. Ask the operator to set PLUGIN_ENCRYPTION_KEY.")
            kind = "bearer" if kind == "none" else kind
            headers = {DEFAULT_HEADER: f"Bearer {token}"} if kind == "bearer" else {header or DEFAULT_HEADER: token}
            try:
                server_name, specs = await probe(url, headers, self.settings)
            except McpError as error:
                raise _failure(error) from None
        with self.sessions() as db, db.begin():
            row = self._owned(db, user_id, connection_id, lock=True)
            if "enabled" in fields and patch.enabled is not None:
                row.enabled = patch.enabled
            if "disabled_tools" in fields and patch.disabled_tools is not None:
                known = {tool["name"] for tool in row.tools_json or []}
                row.disabled_tools = sorted({name for name in patch.disabled_tools if name in known})
            if token:
                row.auth_kind, row.credential_encrypted = kind, cipher.encrypt(token)
                row.server_name, row.tools_json, row.status, row.last_error = server_name, specs, "connected", None
                row.tools_synced_at = datetime.now(timezone.utc)
                row.disabled_tools = [name for name in row.disabled_tools or [] if name in {tool["name"] for tool in specs}]
            return view(row)

    def delete(self, user_id: UUID, connection_id: UUID):
        """Removes a connection. Returns what it takes to tell the authorization server to forget its tokens (for a connection
        that signed in with OAuth), to be sent after the response: removing never waits on, or fails because of, that."""
        revocation = None
        with self.sessions() as db, db.begin():
            row = self._owned(db, user_id, connection_id, lock=True)
            if row.auth_kind == "oauth":
                from .oauth_service import revocation_for
                revocation = revocation_for(db, connection_id, self.settings)
            db.delete(row)
            # Conversations that listed it no longer do.
            db.execute(update(Conversation).where(Conversation.owner_id == user_id, func.jsonb_typeof(Conversation.plugins_json) == "array")
                       .values(plugins_json=Conversation.plugins_json.op("-")(cast(str(connection_id), Text))))
        return revocation


# --- conversations ---------------------------------------------------------------------------------------------------
def default_selection(db, user_id: UUID) -> list[str]:
    """The plugins a new conversation starts with: the connections their owner left switched on."""
    return [str(identifier) for identifier in db.scalars(select(PluginConnection.id).where(
        PluginConnection.user_id == user_id, PluginConnection.enabled.is_(True)).order_by(PluginConnection.created_at, PluginConnection.id))]


def checked_selection(db, user_id: UUID, ids) -> list[str]:
    """`ids` as a list of the person's own connection ids, without duplicates. Raises PluginError for one that is not theirs."""
    wanted = list(dict.fromkeys(str(identifier) for identifier in ids))
    if len(wanted) > 100:
        raise PluginError(422, "plugin_selection_too_large", "Too many plugins selected.")
    if wanted:
        owned = {str(identifier) for identifier in db.scalars(select(PluginConnection.id).where(
            PluginConnection.user_id == user_id, PluginConnection.id.in_([UUID(item) for item in wanted])))}
        if not set(wanted) <= owned:
            raise PluginError(404, "plugin_not_found", "One of those plugins is not connected.")
    return wanted


def connections_for_conversation(sessions, user_id: UUID, session_id: UUID | None) -> list[PluginConnection]:
    """The connections a conversation uses (or, with no conversation yet, the ones a new one starts with). Detached rows."""
    with sessions() as db:
        if session_id is None:
            chosen = default_selection(db, user_id)
        else:
            chosen = db.scalar(select(Conversation.plugins_json).where(Conversation.id == session_id, Conversation.owner_id == user_id))
        if not chosen or not isinstance(chosen, list):
            return []
        rows = list(db.scalars(select(PluginConnection).where(PluginConnection.user_id == user_id,
            PluginConnection.id.in_([UUID(item) for item in chosen])).order_by(PluginConnection.created_at, PluginConnection.id)))
        for row in rows:
            db.expunge(row)
        return rows


def connections_for_task(sessions, task_id: UUID, user_id: UUID) -> list[PluginConnection]:
    """The connections the task's conversation may use, in the order they were connected. Detached rows, safe to read."""
    with sessions() as db:
        chosen = db.scalar(select(Conversation.plugins_json).join(
            TurnExecution, TurnExecution.session_id == Conversation.id).where(TurnExecution.task_id == task_id))
        if not chosen or not isinstance(chosen, list):
            return []
        rows = list(db.scalars(select(PluginConnection).where(PluginConnection.user_id == user_id,
            PluginConnection.id.in_([UUID(item) for item in chosen])).order_by(PluginConnection.created_at, PluginConnection.id)))
        for row in rows:
            db.expunge(row)
        return rows

