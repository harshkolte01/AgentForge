"""Disposable plugin lifecycle: connect, credentials, tools, per-conversation choice and what a run is given.

Talks to a local fake MCP server (tests/test_plugins.py); no provider is called and no live database is written."""
import json
import sys
from pathlib import Path
from unittest.mock import patch
from uuid import UUID, uuid4

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from _disposable_db import admin_engine
from agentforge.settings import Settings
import agentforge.settings as settings_module

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps/api/tests"))
from test_plugins import FakeServer  # noqa: E402

name = "agentforge_plugins_smoke_" + uuid4().hex[:16]
created = False
admin = engine = None
servers = []
TOKEN = "fixture-token-" + "t" * 20
try:
    source = make_url(Settings().database_url)
    admin = admin_engine(source)
    with admin.connect() as connection:
        owner = connection.dialect.identifier_preparer.quote_identifier(source.username)
        connection.execute(text('CREATE DATABASE "' + name + '" OWNER ' + owner))
    created = True
    settings = Settings(_env_file=None, app_env="test", auth_mode="disabled", plugin_allow_private_network=True,
        plugin_encryption_key=SecretStr("plugin-fixture-key-" + "k" * 24),
        database_url=source.set(database=name).render_as_string(hide_password=False),
        llm_api_key=None, tavily_api_key=None, neatlogs_api_key=None, e2b_api_key=None)
    config = Config(str(ROOT / "apps/api/alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "apps/api/alembic"))
    with patch.object(settings_module, "get_settings", return_value=settings):
        command.upgrade(config, "head")
    from agentforge.auth.session import current_user
    from agentforge.database.models import User
    from agentforge.database.plugins import PluginConnection
    from agentforge.database.sessions import Conversation
    from agentforge.main import create_app
    from agentforge.plugins.runtime import register_plugins
    from agentforge.plugins.service import cipher_for, connections_for_task
    from agentforge.tools.catalog import ToolCatalog

    engine = create_engine(settings.database_url)
    sessions = sessionmaker(engine)
    user_id, other_id = uuid4(), uuid4()
    with sessions.begin() as db:
        db.add_all([User(id=user_id, github_user_id=-801, display_name="Plugin fixture"), User(id=other_id, github_user_id=-802, display_name="Other fixture")])
    with sessions() as db:
        identity, outsider = db.get(User, user_id), db.get(User, other_id)
        db.expunge_all()
    open_server, locked_server = FakeServer(), FakeServer(auth=f"Bearer {TOKEN}")
    servers += [open_server, locked_server]
    app = create_app(settings)
    app.dependency_overrides[current_user] = lambda: identity
    with TestClient(app) as client:
        overview = client.get("/api/v1/plugins").json()
        assert [item["key"] for item in overview["catalog"]] == ["deepwiki", "context7", "github", "linear"], "catalog changed"
        assert overview["connections"] == [] and overview["credentials"] is True and overview["enabled"] is True

        # --- connecting ----------------------------------------------------------------------------------------------
        response = client.post("/api/v1/plugins", json={"plugin_key": "custom", "name": "Open Demo", "server_url": open_server.url})
        assert response.status_code == 201, response.text
        demo = response.json()
        assert demo["tool_count"] == 5 and demo["auth"] == "none" and demo["has_credential"] is False and demo["status"] == "connected", demo
        assert demo["server_name"] == "Fake Server" and {tool["name"] for tool in demo["tools"]} >= {"echo", "make_issue"}
        assert next(tool for tool in demo["tools"] if tool["name"] == "echo")["read_only"] is True
        assert client.post("/api/v1/plugins", json={"plugin_key": "custom", "name": "x", "server_url": "ftp://nope"}).status_code == 422
        for body, status, code in (({"plugin_key": "github"}, 422, "plugin_token_required"), ({"plugin_key": "nonexistent"}, 404, "plugin_unknown"),
                                   ({"plugin_key": "custom", "server_url": open_server.url}, 422, "plugin_name_required"),
                                   ({"plugin_key": "custom", "name": "Bad", "server_url": locked_server.url, "token": "wrong-token-wrong"}, 422, "plugin_auth_failed")):
            response = client.post("/api/v1/plugins", json=body)
            assert (response.status_code, response.json()["detail"]["code"]) == (status, code), (body, response.status_code, response.text)
            assert "wrong-token-wrong" not in response.text
        assert len(client.get("/api/v1/plugins").json()["connections"]) == 1, "a failed connection was saved"

        response = client.post("/api/v1/plugins", json={"plugin_key": "custom", "name": "Locked Demo", "server_url": locked_server.url, "token": TOKEN})
        assert response.status_code == 201, response.text
        locked = response.json()
        assert TOKEN not in response.text and locked["has_credential"] is True and locked["auth"] == "token"
        assert locked["server_url"] == locked_server.url
        with sessions() as db:
            stored = db.get(PluginConnection, UUID(locked["id"]))
            assert stored.credential_encrypted.startswith("v1:") and TOKEN not in stored.credential_encrypted, "credential stored in the clear"
            assert cipher_for(settings).decrypt(stored.credential_encrypted) == TOKEN and stored.auth_kind == "bearer"
            assert {demo_row.slug for demo_row in db.query(PluginConnection)} == {"open-demo", "locked-demo"}
        assert TOKEN not in client.get("/api/v1/plugins").text
        response = client.post("/api/v1/plugins", json={"plugin_key": "custom", "name": "Open Demo", "server_url": open_server.url})
        assert response.status_code == 201 and response.json()["id"] != demo["id"], "a second custom server must be allowed"
        duplicate = response.json()
        with sessions() as db:
            assert db.get(PluginConnection, UUID(duplicate["id"])).slug == "open-demo-2"
        assert client.delete(f"/api/v1/plugins/{duplicate['id']}").status_code == 204

        # --- another person cannot see or change it -----------------------------------------------------------------------
        app.dependency_overrides[current_user] = lambda: outsider
        assert client.get("/api/v1/plugins").json()["connections"] == []
        assert client.patch(f"/api/v1/plugins/{demo['id']}", json={"enabled": False}).status_code == 404
        assert client.post(f"/api/v1/plugins/{demo['id']}/refresh").status_code == 404
        assert client.delete(f"/api/v1/plugins/{demo['id']}").status_code == 404
        app.dependency_overrides[current_user] = lambda: identity

        # --- changing --------------------------------------------------------------------------------------------------
        changed = client.patch(f"/api/v1/plugins/{demo['id']}", json={"disabled_tools": ["make_issue", "not-a-tool"]}).json()
        assert [tool["name"] for tool in changed["tools"] if not tool["enabled"]] == ["make_issue"] and changed["enabled_tool_count"] == 4
        assert client.patch(f"/api/v1/plugins/{locked['id']}", json={"token": "rotated-wrong-token"}).status_code == 422
        with sessions() as db:
            assert cipher_for(settings).decrypt(db.get(PluginConnection, UUID(locked["id"])).credential_encrypted) == TOKEN, "a rejected token replaced the good one"
        locked_server.auth = "Bearer rotated-token-value"
        assert client.patch(f"/api/v1/plugins/{locked['id']}", json={"token": "rotated-token-value"}).status_code == 200
        assert client.post(f"/api/v1/plugins/{locked['id']}/refresh").json()["status"] == "connected"
        locked_server.auth = "Bearer something-else-entirely"
        failed = client.post(f"/api/v1/plugins/{locked['id']}/refresh").json()
        assert failed["status"] == "error" and failed["last_error"] and failed["tool_count"] == 5, "a failed refresh must keep the tools and say why"
        locked_server.auth = "Bearer rotated-token-value"
        assert client.post(f"/api/v1/plugins/{locked['id']}/refresh").json()["status"] == "connected"
        assert client.patch(f"/api/v1/plugins/{locked['id']}", json={"enabled": False}).json()["enabled"] is False

        # --- conversations choose their plugins -----------------------------------------------------------------------
        created_session = client.post("/api/v1/sessions", json={"title": "Default"}).json()
        assert created_session["plugins"] == [demo["id"]], "a new conversation starts with the plugins left switched on"
        explicit = client.post("/api/v1/sessions", json={"title": "Both", "plugins": [demo["id"], locked["id"]]}).json()
        assert set(explicit["plugins"]) == {demo["id"], locked["id"]}
        none = client.post("/api/v1/sessions", json={"title": "None", "plugins": []}).json()
        assert none["plugins"] == []
        assert client.post("/api/v1/sessions", json={"title": "Foreign", "plugins": [str(uuid4())]}).status_code == 404
        app.dependency_overrides[current_user] = lambda: outsider
        assert client.patch(f"/api/v1/sessions/{explicit['id']}", json={"plugins": [demo["id"]]}).status_code == 404
        app.dependency_overrides[current_user] = lambda: identity
        patched = client.patch(f"/api/v1/sessions/{none['id']}", json={"plugins": [locked["id"]]}).json()
        assert patched["plugins"] == [locked["id"]] and client.get(f"/api/v1/sessions/{none['id']}").json()["plugins"] == [locked["id"]]
        assert any(item["id"] == none["id"] and item["plugins"] == [locked["id"]] for item in client.get("/api/v1/sessions").json())
        keep = client.patch(f"/api/v1/sessions/{none['id']}", json={"title": "Renamed"}).json()
        assert keep["plugins"] == [locked["id"]], "renaming must not change the plugin choice"

        # The tools a conversation's plugins add to every request count towards its context.
        bare = client.post("/api/v1/sessions", json={"title": "Bare", "plugins": []}).json()
        plain = client.get("/api/v1/context", params={"session_id": bare["id"]}).json()["baseline_tokens"]
        loaded = client.get("/api/v1/context", params={"session_id": created_session["id"]}).json()["baseline_tokens"]
        assert loaded > plain, (plain, loaded)
        assert client.get("/api/v1/context").json()["baseline_tokens"] == loaded, "a draft starts with the plugins left on"

        # --- what a run is given ---------------------------------------------------------------------------------------
        sent = client.post(f"/api/v1/sessions/{explicit['id']}/turns", json={"routing": "auto", "idempotency_key": str(uuid4()), "goal": "use the plugins"})
        assert sent.status_code == 202, sent.text
        task_id = UUID(sent.json()["task"]["id"])
        rows = connections_for_task(sessions, task_id, user_id)
        assert [row.slug for row in rows] == ["open-demo", "locked-demo"], [row.slug for row in rows]
        catalog = ToolCatalog()
        runtime = register_plugins(catalog, rows, settings)
        names = catalog.plugin_tool_names()
        assert "open-demo__echo" in names and "locked-demo__make_issue" in names and "open-demo__make_issue" not in names, names
        assert connections_for_task(sessions, task_id, other_id) == [], "another owner must not get the plugins"

        # --- removing -----------------------------------------------------------------------------------------------------
        assert client.delete(f"/api/v1/plugins/{locked['id']}").status_code == 204
        assert client.get(f"/api/v1/sessions/{explicit['id']}").json()["plugins"] == [demo["id"]], "a removed plugin must leave its conversations"
        assert client.get(f"/api/v1/sessions/{none['id']}").json()["plugins"] == []
        with sessions() as db:
            assert db.get(Conversation, UUID(explicit["id"])).plugins_json == [demo["id"]]
    print("PASS connect/validate/credential encryption/ownership/refresh failure/tool switches/conversation choice/run registration/removal")
except Exception as error:
    print("FAIL Plugin fixture class=" + type(error).__name__)
    if isinstance(error, AssertionError):
        print(str(error))
    else:
        import traceback
        traceback.print_exc()
    raise SystemExit(1) from None
finally:
    for server in servers:
        server.close()
    if engine is not None:
        engine.dispose()
    if created:
        with admin.connect() as connection:
            connection.execute(text('DROP DATABASE "' + name + '" WITH (FORCE)'))
        print("PASS disposable database removed")
    if admin is not None:
        admin.dispose()
