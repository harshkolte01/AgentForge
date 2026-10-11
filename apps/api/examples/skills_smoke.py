"""Disposable skills lifecycle: built-in on/off, a person's own skills, ownership, limits, and what a run is offered.

No provider is called and no live database is written."""
import asyncio
from pathlib import Path
from types import SimpleNamespace as NS
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
name = "agentforge_skills_smoke_" + uuid4().hex[:16]
created = False
admin = engine = None
BODY = "# Mine\n\nDo the careful steps in my order, then report what happened."
try:
    source = make_url(Settings().database_url)
    admin = admin_engine(source)
    with admin.connect() as connection:
        owner = connection.dialect.identifier_preparer.quote_identifier(source.username)
        connection.execute(text('CREATE DATABASE "' + name + '" OWNER ' + owner))
    created = True
    settings = Settings(_env_file=None, app_env="test", auth_mode="disabled", skill_max_per_user=4,
        database_url=source.set(database=name).render_as_string(hide_password=False),
        llm_api_key=None, tavily_api_key=None, neatlogs_api_key=None, e2b_api_key=None)
    config = Config(str(ROOT / "apps/api/alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "apps/api/alembic"))
    with patch.object(settings_module, "get_settings", return_value=settings):
        command.upgrade(config, "head")
    from agentforge.auth.session import current_user
    from agentforge.database.models import User
    from agentforge.database.skills import SkillSetting, UserSkill
    from agentforge.main import create_app
    from agentforge.skills.runtime import register_skills
    from agentforge.skills.service import load_offered, offered
    from agentforge.tools.catalog import ToolCatalog
    from agentforge.tools.contracts import UseSkillArgs
    from agentforge.tools.errors import ToolError

    engine = create_engine(settings.database_url)
    sessions = sessionmaker(engine)
    user_id, other_id = uuid4(), uuid4()
    with sessions.begin() as db:
        db.add_all([User(id=user_id, github_user_id=-901, display_name="Skills fixture"), User(id=other_id, github_user_id=-902, display_name="Other fixture")])
    with sessions() as db:
        identity, outsider = db.get(User, user_id), db.get(User, other_id)
        db.expunge_all()
    app = create_app(settings)
    app.dependency_overrides[current_user] = lambda: identity
    with TestClient(app) as client:
        # --- built in ---------------------------------------------------------------------------------------------------
        overview = client.get("/api/v1/skills").json()
        assert overview["enabled"] is True and overview["max_custom"] == 4 and overview["custom"] == []
        names = [item["name"] for item in overview["builtin"]]
        assert names[:3] == ["deep-research", "code-review", "debug-and-fix"] and len(names) == 10, names
        assert all(item["enabled"] and item["source"] == "builtin" and "instructions" not in item for item in overview["builtin"])
        detail = client.get("/api/v1/skills/builtin:code-review").json()
        assert detail["instructions"].startswith("# Code review") and detail["key"] == "builtin:code-review", detail
        for bad in ("builtin:nope", "nonsense", "custom:not-a-uuid", f"custom:{uuid4()}"):
            assert client.get(f"/api/v1/skills/{bad}").status_code == 404, bad
        assert [item.name for item in offered(sessions, user_id)] == names

        response = client.patch("/api/v1/skills/builtin:code-review", json={"enabled": False})
        assert response.status_code == 200 and response.json()["enabled"] is False
        assert "code-review" not in [item.name for item in offered(sessions, user_id)]
        assert load_offered(sessions, user_id, "code-review") is None and load_offered(sessions, other_id, "code-review") is not None
        assert next(item for item in client.get("/api/v1/skills").json()["builtin"] if item["name"] == "code-review")["enabled"] is False
        assert client.patch("/api/v1/skills/builtin:code-review", json={"enabled": True}).json()["enabled"] is True
        with sessions() as db:
            assert db.get(SkillSetting, (user_id, "code-review")).enabled is True, "switching back on keeps one row"
        for body in ({"description": "Changing a built-in is not allowed here."}, {"name": "renamed-skill"}, {}):
            response = client.patch("/api/v1/skills/builtin:write-tests", json=body)
            assert response.status_code == 422 and response.json()["detail"]["code"] == "skill_builtin_readonly", (body, response.text)
        assert client.delete("/api/v1/skills/builtin:write-tests").status_code == 422
        assert client.patch("/api/v1/skills/builtin:nope", json={"enabled": False}).status_code == 404

        # --- a person's own --------------------------------------------------------------------------------------------
        def create(**fields):
            return client.post("/api/v1/skills", json={"name": "my-skill", "description": "Does my thing when I ask for my thing.", "instructions": BODY, **fields})

        response = create()
        assert response.status_code == 201, response.text
        mine = response.json()
        assert mine["source"] == "custom" and mine["instructions"] == BODY and mine["enabled"] is True and mine["key"] == f"custom:{mine['id']}" if "id" in mine else True
        assert mine["key"].startswith("custom:")
        listed = client.get("/api/v1/skills").json()["custom"]
        assert [item["name"] for item in listed] == ["my-skill"] and "instructions" not in listed[0]
        assert client.get(f"/api/v1/skills/{mine['key']}").json()["instructions"] == BODY
        for fields, status, code, field in (({"name": "My Skill"}, 422, "skill_invalid", "name"), ({"name": "ab"}, 422, "skill_invalid", "name"),
                                            ({"description": "short"}, 422, "skill_invalid", "description"), ({"instructions": "tiny"}, 422, "skill_invalid", "instructions"),
                                            ({"name": "code-review"}, 409, "skill_name_reserved", "name"), ({}, 409, "skill_name_taken", "name")):
            response = create(**fields)
            detail = response.json()["detail"]
            assert (response.status_code, detail["code"], detail.get("field")) == (status, code, field), (fields, response.status_code, response.text)
        assert client.post("/api/v1/skills", json={"name": "x-skill", "description": "Does my thing when I ask for my thing.", "instructions": BODY, "extra": 1}).status_code == 422

        second = create(name="second-skill", enabled=False).json()
        assert second["enabled"] is False
        assert [item.name for item in offered(sessions, user_id)][-1:] == ["my-skill"], "a switched-off skill is not offered"
        assert create(name="third-skill").status_code == 201 and create(name="fourth-skill").status_code == 201
        response = create(name="fifth-skill")
        assert response.status_code == 409 and response.json()["detail"]["code"] == "skill_limit_reached", response.text

        # --- ownership -------------------------------------------------------------------------------------------------
        app.dependency_overrides[current_user] = lambda: outsider
        assert client.get("/api/v1/skills").json()["custom"] == []
        for call in (lambda: client.get(f"/api/v1/skills/{mine['key']}"), lambda: client.patch(f"/api/v1/skills/{mine['key']}", json={"enabled": False}),
                     lambda: client.delete(f"/api/v1/skills/{mine['key']}")):
            assert call().status_code == 404
        assert create().status_code == 201, "the same name is free for another person"
        assert [item.name for item in offered(sessions, other_id)][-1] == "my-skill"
        assert load_offered(sessions, other_id, "my-skill")[0].instructions == BODY
        app.dependency_overrides[current_user] = lambda: identity

        # --- editing ---------------------------------------------------------------------------------------------------
        changed = client.patch(f"/api/v1/skills/{mine['key']}", json={"description": "A better description that says when to use it.", "instructions": BODY + "\n\nMore steps follow here."}).json()
        assert changed["description"].startswith("A better") and changed["instructions"].endswith("follow here.")
        assert client.patch(f"/api/v1/skills/{mine['key']}", json={"name": "renamed-skill"}).json()["name"] == "renamed-skill"
        for fields, status, code in (({"name": "second-skill"}, 409, "skill_name_taken"), ({"name": "deep-research"}, 409, "skill_name_reserved"),
                                     ({"name": "Bad Name"}, 422, "skill_invalid"), ({"instructions": "x"}, 422, "skill_invalid")):
            response = client.patch(f"/api/v1/skills/{mine['key']}", json=fields)
            assert (response.status_code, response.json()["detail"]["code"]) == (status, code), (fields, response.text)
        assert client.get(f"/api/v1/skills/{mine['key']}").json()["name"] == "renamed-skill", "a rejected change must not partly apply"
        assert client.patch(f"/api/v1/skills/{mine['key']}", json={"enabled": False}).json()["enabled"] is False
        assert load_offered(sessions, user_id, "renamed-skill") is None and "renamed-skill" not in [item.name for item in offered(sessions, user_id)]
        assert client.patch(f"/api/v1/skills/{mine['key']}", json={"enabled": True}).json()["enabled"] is True

        # --- what a run is offered ---------------------------------------------------------------------------------------
        catalog = ToolCatalog()
        listing = register_skills(catalog, sessions, user_id)
        assert [item[0] for item in listing] == [item.name for item in offered(sessions, user_id)] and catalog.extra_tool_names() == ("use_skill",)

        async def load(owner_id, skill):
            return await catalog.get("use_skill").handler(UseSkillArgs.model_validate({"name": skill}), NS(task=NS(user_id=owner_id)))
        loaded = asyncio.run(load(user_id, "renamed-skill"))
        assert loaded.data["instructions"].endswith("follow here.") and loaded.data["source"] == "yours"
        assert asyncio.run(load(user_id, "deep-research")).data["source"] == "built-in"
        try:
            asyncio.run(load(other_id, "second-skill"))
            raise AssertionError("another person's skill was loaded")
        except ToolError as error:
            assert error.code == "SKILL_NOT_FOUND"
        try:
            asyncio.run(load(user_id, "second-skill"))  # switched off
            raise AssertionError("a switched-off skill was loaded")
        except ToolError as error:
            assert error.code == "SKILL_NOT_FOUND"
        everything = client.get("/api/v1/skills").json()
        for item in [*everything["builtin"], *everything["custom"]]:
            assert client.patch(f"/api/v1/skills/{item['key']}", json={"enabled": False}).status_code == 200
        assert offered(sessions, user_id) == [], "everything switched off offers nothing"
        bare = ToolCatalog()
        assert register_skills(bare, sessions, user_id) == () and bare.extra_tool_names() == ()

        # --- the context meter -------------------------------------------------------------------------------------------
        quiet = client.get("/api/v1/context").json()["baseline_tokens"]
        assert client.patch("/api/v1/skills/builtin:deep-research", json={"enabled": True}).status_code == 200
        assert client.get("/api/v1/context").json()["baseline_tokens"] > quiet, "an offered skill must count towards the context"

        # --- removing --------------------------------------------------------------------------------------------------
        assert client.delete(f"/api/v1/skills/{mine['key']}").status_code == 204
        assert client.get(f"/api/v1/skills/{mine['key']}").status_code == 404 and client.delete(f"/api/v1/skills/{mine['key']}").status_code == 404
        with sessions() as db:
            assert db.query(UserSkill).filter(UserSkill.user_id == user_id).count() == 3
            assert db.query(UserSkill).filter(UserSkill.user_id == other_id).count() == 1
    print("PASS built-in on/off; own skills create/edit/rename/delete; validation; limits; ownership; what a run is offered and can load; context meter")
except Exception as error:
    print("FAIL Skills fixture class=" + type(error).__name__)
    if isinstance(error, AssertionError):
        print(str(error))
    else:
        import traceback
        traceback.print_exc()
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
