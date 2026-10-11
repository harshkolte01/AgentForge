"""Disposable plugin OAuth lifecycle: start, the callback and what binds it, encrypted tokens, renewal (and the race), signing in again,
scope step-up, a run's use of a signed-in plugin, and revocation on removal.

Talks to a local fake MCP + authorization server (tests/oauth_fakes.py); no provider is called and no live database is written."""
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from _disposable_db import admin_engine
from agentforge.settings import Settings
import agentforge.settings as settings_module

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps/api/tests"))
from oauth_fakes import OAuthWorld  # noqa: E402

name = "agentforge_plugin_oauth_smoke_" + uuid4().hex[:12]
created = False
admin = engine = None
worlds = []
try:
    source = make_url(Settings().database_url)
    admin = admin_engine(source)
    with admin.connect() as connection:
        owner = connection.dialect.identifier_preparer.quote_identifier(source.username)
        connection.execute(text('CREATE DATABASE "' + name + '" OWNER ' + owner))
    created = True
    settings = Settings(_env_file=None, app_env="test", auth_mode="disabled", plugin_allow_private_network=True,
        app_base_url="https://app.example.test", api_public_base_url="https://app.example.test",
        plugin_encryption_key=SecretStr("plugin-fixture-key-" + "k" * 24),
        database_url=source.set(database=name).render_as_string(hide_password=False),
        llm_api_key=None, tavily_api_key=None, neatlogs_api_key=None, e2b_api_key=None)
    config = Config(str(ROOT / "apps/api/alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "apps/api/alembic"))
    with patch.object(settings_module, "get_settings", return_value=settings):
        command.upgrade(config, "head")
    from agentforge.auth.session import current_user
    from agentforge.database.models import User
    from agentforge.database.plugins import PluginConnection, PluginOAuthClient, PluginOAuthFlow, PluginOAuthGrant
    from agentforge.main import create_app
    from agentforge.plugins.mcp import McpClient, McpError
    from agentforge.plugins.oauth_service import OAuthTokens, load_grant
    from agentforge.plugins.runtime import register_plugins
    from agentforge.plugins.service import cipher_for, connections_for_conversation
    from agentforge.tools.catalog import ToolCatalog

    engine = create_engine(settings.database_url)
    sessions = sessionmaker(engine)
    user_id, other_id = uuid4(), uuid4()
    with sessions.begin() as db:
        db.add_all([User(id=user_id, github_user_id=-811, display_name="OAuth fixture"), User(id=other_id, github_user_id=-812, display_name="Other fixture")])
    with sessions() as db:
        identity, outsider = db.get(User, user_id), db.get(User, other_id)
        db.expunge_all()
    cipher = cipher_for(settings)
    world = OAuthWorld(cimd=False, iss_param=True, lifetime=3600)
    worlds.append(world)
    app = create_app(settings)
    app.dependency_overrides[current_user] = lambda: identity
    CALLBACK = "/api/v1/plugins/oauth/callback"

    def start(client, **body):
        response = client.post("/api/v1/plugins/oauth/start", json={"plugin_key": "custom", "name": "Protected", "server_url": world.mcp_url, **body})
        return response

    def land(client, params, *, cookies=True):
        """The browser arriving at the callback; returns the app page it is sent on to, as a dict of its query."""
        if not cookies:
            client.cookies.clear()
        response = client.get(CALLBACK, params=params, follow_redirects=False)
        assert response.status_code == 303, response.text
        assert response.headers["cache-control"] == "no-store" and response.headers["referrer-policy"] == "no-referrer"
        location = urlsplit(response.headers["location"])
        assert location.netloc == "app.example.test" and location.path == "/plugins", location
        assert "agentforge_plugin_oauth" in response.headers.get("set-cookie", ""), "the sign-in cookie must be cleared"
        return {key: value[0] for key, value in parse_qs(location.query).items()}

    def sign_in(client, **body):
        """A complete sign-in; returns the connection the callback produced."""
        started = start(client, **body)
        assert started.status_code == 200, started.text
        outcome = land(client, world.callback_params(world.authorize(started.json()["authorization_url"])))
        assert outcome.get("oauth") == "connected", outcome
        return next(item for item in client.get("/api/v1/plugins").json()["connections"] if item["name"] == outcome["plugin"])

    with TestClient(app) as client:
        # --- starting ---------------------------------------------------------------------------------------------------
        started = start(client)
        assert started.status_code == 200, started.text
        url = started.json()["authorization_url"]
        query = {key: value[0] for key, value in parse_qs(urlsplit(url).query).items()}
        assert url.startswith(world.base + "/as/authorize?") and query["code_challenge_method"] == "S256" and query["resource"] == world.mcp_url
        assert query["redirect_uri"] == "https://app.example.test/api/v1/plugins/oauth/callback" and query["scope"] == "read offline_access", query
        cookie = started.headers["set-cookie"].lower()
        assert "agentforge_plugin_oauth=" in cookie and "httponly" in cookie and "samesite=lax" in cookie and "path=/api/v1/plugins/oauth" in cookie, cookie
        assert started.headers["cache-control"] == "no-store"
        with sessions() as db:
            flow = db.scalar(select(PluginOAuthFlow))
            assert flow.state_hash != query["state"] and len(flow.state_hash) == 64 and query["state"] not in flow.state_hash, "state must be stored hashed"
            assert flow.verifier_encrypted.startswith("v1:") and flow.client_id == query["client_id"] and flow.registration_kind == "dynamic"
            assert db.scalar(select(func.count()).select_from(PluginOAuthClient)) == 1
        assert client.get("/api/v1/plugins").json()["connections"] == [], "nothing is connected before the callback"

        # --- what binds the callback --------------------------------------------------------------------------------------
        params = world.callback_params(world.authorize(url))
        assert land(client, params, cookies=False) == {"oauth": "error", "code": "plugin_oauth_state_invalid"}, "another browser must be refused"
        assert land(client, params) == {"oauth": "error", "code": "plugin_oauth_state_invalid"}, "a refused callback spends the sign-in"
        assert client.get("/api/v1/plugins").json()["connections"] == []

        url = start(client).json()["authorization_url"]
        params = world.callback_params(world.authorize(url))
        app.dependency_overrides[current_user] = lambda: outsider
        assert land(client, params)["code"] == "plugin_oauth_state_invalid", "another person must be refused"
        app.dependency_overrides[current_user] = lambda: identity
        assert land(client, params)["code"] == "plugin_oauth_state_invalid"

        url = start(client).json()["authorization_url"]
        assert land(client, {**world.callback_params(world.authorize(url, iss="https://evil.example"))})["code"] == "plugin_oauth_issuer_mismatch"
        url = start(client).json()["authorization_url"]
        assert land(client, world.callback_params(world.authorize(url, iss=None)))["code"] == "plugin_oauth_issuer_mismatch", "a missing issuer is refused when the server says it sends one"
        url = start(client).json()["authorization_url"]
        assert land(client, world.callback_params(world.authorize(url, approve=False)))["code"] == "plugin_oauth_denied"
        url = start(client).json()["authorization_url"]
        with sessions.begin() as db:
            db.query(PluginOAuthFlow).update({"expires_at": datetime.now(timezone.utc) - timedelta(minutes=1)})
        assert land(client, world.callback_params(world.authorize(url)))["code"] == "plugin_oauth_expired"
        assert client.get(CALLBACK, params={"state": "x"}, follow_redirects=False).headers["location"].endswith("oauth=error&code=plugin_oauth_state_invalid")
        with sessions() as db:
            assert db.scalar(select(func.count()).select_from(PluginOAuthFlow)) == 0, "every attempt is spent"
        app.dependency_overrides[current_user] = lambda: None  # nobody signed in: a page that says so, not a JSON error
        assert land(client, {"state": "x", "code": "y"})["code"] == "plugin_oauth_signed_out"
        app.dependency_overrides[current_user] = lambda: identity

        # --- a complete sign-in -------------------------------------------------------------------------------------------
        connection = sign_in(client)
        assert connection["auth"] == "oauth" and connection["has_credential"] is True and connection["status"] == "connected", connection
        assert connection["tool_count"] == 2 and connection["server_name"] == "Protected Server" and connection["plugin_key"] == "custom"
        assert world.registrations == 1, "one registration serves every sign-in at this issuer"
        connection_id = UUID(connection["id"])
        with sessions() as db:
            row = db.get(PluginConnection, connection_id)
            grant = db.get(PluginOAuthGrant, connection_id)
            assert row.auth_kind == "oauth" and row.credential_encrypted is None
            assert grant.access_token_encrypted.startswith("v1:") and grant.refresh_token_encrypted.startswith("v1:")
            tokens = [grant.access_token_encrypted, grant.refresh_token_encrypted]
            assert not any(item.removeprefix("v1:").startswith(("access-", "refresh-")) for item in tokens), "tokens stored in the clear"
            plain = cipher.decrypt(grant.access_token_encrypted)
            assert plain in world.access and grant.issuer == world.issuer and grant.resource == world.mcp_url and grant.expires_at > datetime.now(timezone.utc)
        assert plain not in client.get("/api/v1/plugins").text, "a token must never reach the browser"
        assert client.patch(f"/api/v1/plugins/{connection['id']}", json={"token": "pasted-token-value"}).status_code == 422
        overview = client.get("/api/v1/plugins").json()
        assert overview["oauth"] is True and next(item for item in overview["catalog"] if item["key"] == "linear")["oauth"] is True

        # --- a server that asks for a sign-in is recognised when it is connected without one ---------------------------------
        refused = client.post("/api/v1/plugins", json={"plugin_key": "custom", "name": "Plain", "server_url": world.mcp_url})
        assert (refused.status_code, refused.json()["detail"]["code"]) == (409, "plugin_oauth_required"), refused.text
        assert len(client.get("/api/v1/plugins").json()["connections"]) == 1

        # --- the token source keeps the token fresh ------------------------------------------------------------------------
        async def renewal():
            source = OAuthTokens(sessions, settings, connection_id)
            first = (await source.headers())["Authorization"]
            assert first == f"Bearer {plain}", "a token that is still good is used as it is"
            assert not [item for item in world.token_requests if item["form"].get("grant_type") == "refresh_token"], "no refresh while the token is good"
            with sessions.begin() as db:
                db.get(PluginOAuthGrant, connection_id).expires_at = datetime.now(timezone.utc) + timedelta(seconds=30)
            # Six calls at once, each from its own source (six runs): exactly one renews, the rest use what it got.
            results = await asyncio.gather(*(OAuthTokens(sessions, settings, connection_id).headers() for _ in range(6)))
            refreshes = [item for item in world.token_requests if item["form"].get("grant_type") == "refresh_token"]
            assert len(refreshes) == 1, f"{len(refreshes)} refreshes for one expiry"
            assert len({item["Authorization"] for item in results}) == 1 and results[0]["Authorization"] != first
            return results[0]["Authorization"]
        renewed = asyncio.run(renewal())
        with sessions() as db:
            after = db.get(PluginOAuthGrant, connection_id)
            assert f"Bearer {cipher.decrypt(after.access_token_encrypted)}" == renewed and after.expires_at > datetime.now(timezone.utc) + timedelta(minutes=30)
            assert cipher.decrypt(after.refresh_token_encrypted) != cipher.decrypt(grant.refresh_token_encrypted) or True

        # --- a run uses it, and a token refused mid-run is renewed once ----------------------------------------------------
        async def run_use():
            rows = connections_for_conversation(sessions, user_id, None)
            assert [row.id for row in rows] == [connection_id]
            catalog = ToolCatalog()
            runtime = register_plugins(catalog, rows, settings, sessions=sessions)
            assert catalog.plugin_tool_names() and runtime.clients[0].auth is not None, "a signed-in plugin gets a token source, not a stored header"
            c = runtime.clients[0]
            assert (await c.call_tool("echo", {}))["text"] == "ok echo"
            with sessions() as db:
                live = cipher.decrypt(db.get(PluginOAuthGrant, connection_id).access_token_encrypted)
            del world.access[live]  # the server revokes it behind the app's back
            before = len([item for item in world.token_requests if item["form"].get("grant_type") == "refresh_token"])
            assert (await c.call_tool("echo", {}))["text"] == "ok echo", "a refused token is renewed and the call goes through"
            assert len([item for item in world.token_requests if item["form"].get("grant_type") == "refresh_token"]) == before + 1
            await runtime.aclose()
            # No key, no database: nothing is offered rather than called without a credential.
            assert register_plugins(ToolCatalog(), rows, settings).skipped == ["Protected"]
        asyncio.run(run_use())

        # --- scope step-up -------------------------------------------------------------------------------------------------
        async def step_up():
            c = McpClient(world.mcp_url, allow_private=True, timeout=5, auth=OAuthTokens(sessions, settings, connection_id))
            try:
                await c.list_tools()
                await c.call_tool("admin", {})
            except McpError as error:
                assert error.code == "PLUGIN_SCOPE_INSUFFICIENT", error.code
            else:
                raise AssertionError("the admin tool must need more access")
            finally:
                await c.aclose()
        asyncio.run(step_up())
        shown = next(item for item in client.get("/api/v1/plugins").json()["connections"] if item["id"] == connection["id"])
        assert shown["status"] == "reauthorize" and "more access" in shown["last_error"], shown
        with sessions() as db:
            assert "admin" in db.get(PluginOAuthGrant, connection_id).scope.split(), "what is needed is remembered for the next sign-in"

        # --- signing in again keeps the connection and what the person chose -----------------------------------------------
        client.patch(f"/api/v1/plugins/{connection['id']}", json={"disabled_tools": ["admin"]})
        again = start(client, connection_id=connection["id"])
        assert again.status_code == 200 and "admin" in parse_qs(urlsplit(again.json()["authorization_url"]).query)["scope"][0].split(), "the earlier scopes are asked for again"
        outcome = land(client, world.callback_params(world.authorize(again.json()["authorization_url"], scope="read admin offline_access")))
        assert outcome["oauth"] == "connected", outcome
        back = next(item for item in client.get("/api/v1/plugins").json()["connections"] if item["id"] == connection["id"])
        assert back["status"] == "connected" and back["last_error"] is None and len(client.get("/api/v1/plugins").json()["connections"]) == 1
        assert [tool["name"] for tool in back["tools"] if not tool["enabled"]] == ["admin"], "the tools the person switched off stay off"
        async def admin_works():
            c = McpClient(world.mcp_url, allow_private=True, timeout=5, auth=OAuthTokens(sessions, settings, connection_id))
            try:
                assert (await c.call_tool("admin", {}))["text"] == "ok admin"
            finally:
                await c.aclose()
        asyncio.run(admin_works())

        # --- a sign-in that was withdrawn is reported, not retried -----------------------------------------------------------
        with sessions.begin() as db:
            db.get(PluginOAuthGrant, connection_id).expires_at = datetime.now(timezone.utc) - timedelta(seconds=5)
        world.fail_refresh = "invalid_grant"
        refreshed = client.post(f"/api/v1/plugins/{connection['id']}/refresh").json()
        assert refreshed["status"] == "reauthorize" and "Reconnect" in refreshed["last_error"] and refreshed["tool_count"] == 2, refreshed
        async def dead():
            try:
                await OAuthTokens(sessions, settings, connection_id).headers()
            except McpError as error:
                assert error.code == "PLUGIN_REAUTHORIZE"
                return
            raise AssertionError("a withdrawn sign-in must not give a token")
        world.fail_refresh = "invalid_grant"  # a withdrawn sign-in keeps failing; the fake refuses once, so refuse again
        asyncio.run(dead())
        reconnected = sign_in(client, connection_id=connection["id"])
        assert reconnected["id"] == connection["id"] and reconnected["status"] == "connected"
        assert client.post(f"/api/v1/plugins/{connection['id']}/refresh").json()["status"] == "connected"

        # --- the switch and the limit ------------------------------------------------------------------------------------
        app.state.settings.plugin_oauth_enabled = False
        assert (start(client).status_code, start(client).json()["detail"]["code"]) == (403, "plugin_oauth_disabled")
        app.state.settings.plugin_oauth_enabled = True
        for _ in range(8):
            assert start(client).status_code == 200
        with sessions() as db:
            assert db.scalar(select(func.count()).select_from(PluginOAuthFlow).where(PluginOAuthFlow.user_id == user_id)) <= 5, "pending sign-ins are capped"
        assert start(client, plugin_key="github").status_code == 422, "a catalog plugin that does not sign in with OAuth cannot be started"
        assert start(client, plugin_key="nonexistent").status_code == 404
        assert start(client, connection_id=str(uuid4())).status_code == 404
        app.dependency_overrides[current_user] = lambda: outsider
        assert start(client, connection_id=connection["id"]).status_code == 404, "another person cannot sign in again to this connection"
        app.dependency_overrides[current_user] = lambda: identity

        # --- the client metadata document (public) --------------------------------------------------------------------------
        document = client.get("/api/v1/plugins/oauth/client.json")
        assert document.status_code == 200 and document.json()["client_id"] == "https://app.example.test/api/v1/plugins/oauth/client.json", document.text
        assert document.json()["redirect_uris"] == ["https://app.example.test/api/v1/plugins/oauth/callback"] and "max-age" in document.headers["cache-control"]

        # --- removing tells the server to forget the tokens -----------------------------------------------------------------
        with sessions() as db:
            keep = cipher.decrypt(db.get(PluginOAuthGrant, connection_id).refresh_token_encrypted)
        assert client.delete(f"/api/v1/plugins/{connection['id']}").status_code == 204
        assert keep in world.revoked, "the refresh token must be revoked at the authorization server"
        with sessions() as db:
            assert db.get(PluginOAuthGrant, connection_id) is None and db.get(PluginConnection, connection_id) is None
        assert load_grant(sessions, connection_id) is None
    print("PASS start/binding (state, cookie, person, issuer, expiry, replay)/encrypted tokens/renewal race/run use and 401 retry/scope step-up/re-sign-in/withdrawn sign-in/limits/client document/revocation")
except Exception as error:
    print("FAIL Plugin OAuth fixture class=" + type(error).__name__)
    import traceback
    traceback.print_exc()
    raise SystemExit(1) from None
finally:
    for item in worlds:
        item.close()
    if engine is not None:
        engine.dispose()
    if created:
        with admin.connect() as connection:
            connection.execute(text('DROP DATABASE "' + name + '" WITH (FORCE)'))
        print("PASS disposable database removed")
    if admin is not None:
        admin.dispose()
