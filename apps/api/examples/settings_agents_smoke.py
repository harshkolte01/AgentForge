"""Disposable owner-wide Settings management; no provider calls or live DB writes."""
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import UUID, uuid4

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from _disposable_db import admin_engine
from agentforge.settings import Settings
import agentforge.settings as settings_module

ROOT = Path(__file__).resolve().parents[3]
name = "agentforge_settings_smoke_" + uuid4().hex[:16]
created = False
admin = engine = None
try:
    source = make_url(Settings().database_url)
    admin = admin_engine(source)
    with admin.connect() as connection:
        owner = connection.dialect.identifier_preparer.quote_identifier(source.username)
        connection.execute(text('CREATE DATABASE "' + name + '" OWNER ' + owner))
    created = True
    settings = Settings(_env_file=None, app_env="test", auth_mode="disabled",
        database_url=source.set(database=name).render_as_string(hide_password=False),
        llm_api_key=None, tavily_api_key=None, neatlogs_api_key=None, e2b_api_key=None)
    config = Config(str(ROOT / "apps/api/alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "apps/api/alembic"))
    with patch.object(settings_module, "get_settings", return_value=settings):
        command.upgrade(config, "head")
    from agentforge.database.models import User, Project, Task
    from agentforge.auth.session import current_user
    from agentforge.main import create_app
    from agentforge.subagents.registry import load_builtin
    engine = create_engine(settings.database_url)
    sessions = sessionmaker(engine)
    user_id, other_id, first, second, foreign = (uuid4() for _ in range(5))
    with sessions.begin() as db:
        db.add_all([User(id=user_id, github_user_id=-701, display_name="Settings fixture"), User(id=other_id, github_user_id=-702, display_name="Other fixture")])
        db.flush()
        db.add_all([Project(id=first, owner_id=user_id, name="Old namespace", created_at=datetime(2026, 1, 1, tzinfo=timezone.utc)),
                    Project(id=second, owner_id=user_id, name="Another namespace", created_at=datetime(2026, 2, 1, tzinfo=timezone.utc)),
                    Project(id=foreign, owner_id=other_id, name="Foreign namespace")])
    with sessions() as db:
        identity, outsider = db.get(User, user_id), db.get(User, other_id)
        db.expunge_all()
    definition = load_builtin("agent", 1).model_dump(mode="json")
    definition["name"] = "alpha-researcher"
    definition["allowed_tools"] = ["read_research_source", "web_search", "fetch_webpage"]
    for key in ("github_read", "sandbox_read", "sandbox_write", "sandbox_exec"):
        definition["permissions"][key] = False
    for key in definition["permissions"]:
        if key.startswith("allowed_"):
            definition["permissions"][key] = []
    app = create_app(settings)
    app.dependency_overrides[current_user] = lambda: identity
    with TestClient(app) as client:
        response = client.post("/api/v1/agents", json={"definition": definition})
        assert response.status_code == 201, "automatic workspace agent save failed: " + str(response.status_code) + " " + response.text[:400]
        alpha = response.json()
        assert alpha["project_id"] == str(first), "new agent did not use canonical workspace"
        definition["name"] = "beta-researcher"
        response = client.post("/api/v1/agents", json={"project_id": str(second), "definition": definition})
        assert response.status_code == 201
        beta = response.json()
        app.dependency_overrides[current_user] = lambda: outsider
        response = client.post("/api/v1/agents", json={"project_id": str(foreign), "definition": definition})
        assert response.status_code == 201
        outsider_agent = response.json()["id"]
        app.dependency_overrides[current_user] = lambda: identity
        all_agents = client.get("/api/v1/agents").json()
        assert {item["id"] for item in all_agents} == {alpha["id"], beta["id"]}, "owner-wide listing lost existing namespaces or leaked an agent"
        assert len(client.get("/api/v1/agents", params={"project_id": str(second)}).json()) == 1
        assert client.get("/api/v1/agents", params={"project_id": str(foreign)}).status_code == 404
        first_page = client.get("/api/v1/agents?limit=1").json()
        next_page = client.get("/api/v1/agents?limit=1&offset=1").json()
        assert first_page[0]["id"] != next_page[0]["id"], "pagination repeated an agent"
        assert client.get(f"/api/v1/agents/{outsider_agent}").status_code == 404
        assert client.get(f"/api/v1/agents/{beta['id']}").json()["project_id"] == str(second)
        definition["version"] = 2
        response = client.post("/api/v1/agents", json={"project_id": beta["project_id"], "definition": definition})
        assert response.status_code == 201 and response.json()["id"] == beta["id"], "version save moved an existing agent"
        assert len(client.get(f"/api/v1/agents/{beta['id']}/versions").json()) == 2
        response = client.post(f"/api/v1/agents/{beta['id']}/test", json={"version": 1, "goal": "Read the controlled documentation fixture", "max_cost_usd": "0.25"})
        assert response.status_code == 202, "exact saved-version test could not be queued"
        with sessions() as db:
            task = db.get(Task, UUID(response.json()["id"]))
            assert task.project_id == second and task.permissions_json["agent_version"] == 1
        assert client.delete(f"/api/v1/agents/{beta['id']}").status_code == 204
        assert client.get(f"/api/v1/agents/{beta['id']}").json()["disabled_at"] is not None
    print("PASS owner-wide hidden-workspace list/pagination; automatic create; foreign owner denial; original-scope version/test/disable preserved")
except Exception as error:
    print("FAIL Settings fixture class=" + type(error).__name__)
    if isinstance(error, AssertionError):
        print(str(error))
    raise SystemExit(1) from None
finally:
    if engine is not None:
        engine.dispose()
    if created:
        with admin.connect() as connection:
            connection.execute(text('DROP DATABASE "' + name + '" WITH (FORCE)'))
        print("PASS disposable database removed")
    if admin is not None:
        admin.dispose()
