"""Disposable PostgreSQL qualification. No live API, worker or provider effects."""
from decimal import Decimal
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from uuid import uuid4
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url, URL
from sqlalchemy.orm import sessionmaker
from agentforge.settings import Settings
from _disposable_db import admin_engine
import agentforge.settings as settings_module

ROOT = Path(__file__).resolve().parents[3]
name = "agentforge_sessions_smoke_" + uuid4().hex[:12]
admin = engine = None
created = False
stage = "setup"

def check(value, message):
    if not value:
        raise AssertionError(message)

def run(settings):
    global engine, stage
    from alembic import command
    from alembic.config import Config
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    stage = "migrations"
    cfg = Config(str(ROOT / "apps/api/alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "apps/api/alembic"))
    command.upgrade(cfg, "head")
    from agentforge.database.models import Base, User, Project, Task, TaskJob, AgentRun
    from agentforge.database.sessions import ConversationMessage, SessionTurn
    from agentforge.contracts.sessions import AutoTurnCreate, SessionCreate, SessionPatch, SessionTurnCreate
    from agentforge.repositories.sessions import SessionRepository, bounded_context
    from agentforge.repositories.tasks import TaskNotFound, TaskConflict
    from agentforge.repositories.admission import AdmissionDenied
    engine = create_engine(settings.database_url)
    with engine.connect() as connection:
        drift = compare_metadata(MigrationContext.configure(connection, opts={"compare_type": True, "compare_server_default": True}), Base.metadata)
        check(not drift, "migration metadata drift")
    sessions = sessionmaker(engine, expire_on_commit=False)
    repo = SessionRepository(sessions)
    user, outsider, project, otherproject = uuid4(), uuid4(), uuid4(), uuid4()
    with sessions.begin() as db:
        db.add_all([User(id=user, github_user_id=-123, display_name="Fixture"), User(id=outsider, github_user_id=-124, display_name="Other")])
        db.flush()
        db.add_all([Project(id=project, owner_id=user, name="Fixture"), Project(id=otherproject, owner_id=outsider, name="Other")])
    stage = "natural messages and internal workspace"
    natural = repo.create(user, SessionCreate())
    check(natural.project_id == project, "canonical workspace created a duplicate project")
    hello = AutoTurnCreate(routing="auto", idempotency_key=uuid4(), goal="hey")
    explicit_hello = SessionTurnCreate(**hello.model_dump(exclude={"routing", "repository", "continue_from_task_id"}), kind="general")
    try:
        repo.turn(natural.id, outsider, explicit_hello, original_digest=repo.request_digest(hello),
            automatic={"repository": None, "inherit_repository": False, "inherit_continuation": False, "continue_from_task_id": None})
    except TaskNotFound:
        pass
    else:
        raise AssertionError("outsider sent natural message")
    with sessions() as db:
        check(not list(db.scalars(select(Task))), "foreign send created a task")
    print("PASS canonical existing workspace and owner-only turns", flush=True)
    stage = "turn idempotency"
    conversation = repo.create(user, SessionCreate(project_id=project, mode="general"))
    request = SessionTurnCreate(idempotency_key=uuid4(), kind="general", goal="Research a concrete question with evidence.")
    with ThreadPoolExecutor(max_workers=2) as pool:
        turns = list(pool.map(lambda _: repo.turn(conversation.id, user, request), range(2)))
    check(turns[0].task.id == turns[1].task.id, "same key created duplicate task")
    with sessions() as db:
        check(len(list(db.scalars(select(SessionTurn).where(SessionTurn.session_id == conversation.id)))) == 1, "duplicate turn receipt")
        check(len(list(db.scalars(select(TaskJob)))) == 1, "duplicate job")
    for changed in (request.model_copy(update={"goal": "A changed goal with same request identity."}),):
        try:
            repo.turn(conversation.id, user, changed)
        except TaskConflict:
            pass
        else:
            raise AssertionError("conflicting request accepted")
    try:
        repo.turn(conversation.id, user, request.model_copy(update={"idempotency_key": uuid4()}))
    except TaskConflict:
        pass
    else:
        raise AssertionError("overlapping turn accepted")
    for action in (lambda: repo.get(conversation.id, outsider), lambda: repo.messages(conversation.id, outsider),
                   lambda: repo.turn(conversation.id, outsider, request)):
        try:
            action()
        except TaskNotFound:
            pass
        else:
            raise AssertionError("cross-owner conversation access")
    stage = "receipt recovery"
    with sessions.begin() as db:
        task = db.get(Task, turns[0].task.id)
        task.status, task.final_summary = "PARTIAL", "Supported evidence and explicit limitations."
    check(len(repo.messages(conversation.id, user)) == 2, "missing recovered assistant receipt")
    check(len(repo.messages(conversation.id, user)) == 2, "duplicate recovered assistant receipt")
    check(repo.get(conversation.id, user).active_task_id is None, "active task not cleared")
    check(len(repo.messages(conversation.id, user, after_sequence=1)) == 1, "message cursor incorrect")
    second = repo.turn(conversation.id, user, request.model_copy(update={"idempotency_key": uuid4()}))
    with sessions() as db:
        context = db.get(Task, second.task.id).permissions_json["session_context"]
        check(context["untrusted_history"] is True, "history promoted to trust")
        check(len(json.dumps(context, ensure_ascii=False, separators=(",", ":"))) <= 7500, "history bound exceeded")
        check(context["recent_messages"][0]["message_id"], "provenance missing")
    try:
        repo.patch(conversation.id, user, SessionPatch(archived=True))
    except TaskConflict:
        pass
    else:
        raise AssertionError("active conversation archived")
    stage = "immutability"
    try:
        with sessions.begin() as db:
            db.execute(text("UPDATE conversation_messages SET content='tampered'"))
    except Exception:
        pass
    else:
        raise AssertionError("immutable message changed")
    stage = "custom agent API"
    from fastapi.testclient import TestClient
    from agentforge.main import create_app
    from agentforge.auth.session import current_user
    from agentforge.subagents.registry import load_builtin, AgentRegistry
    from agentforge.orchestration.context import research_context
    from agentforge.repositories.definitions import DefinitionRepository
    from agentforge.database.models import AgentDefinition, AgentDefinitionVersion
    import asyncio
    definition = load_builtin("researcher", 1).model_dump(mode="json")
    definition["name"] = "docs-writer"
    definition["allowed_tools"] = ["web_search", "fetch_webpage"]
    definition["permissions"]["github_read"] = False
    for key in definition["permissions"]:
        if key.startswith("allowed_"):
            definition["permissions"][key] = []
    app = create_app(settings)
    app.state.engine = engine
    with sessions() as db:
        identity = db.get(User, user)
        db.expunge(identity)
    app.dependency_overrides[current_user] = lambda: identity
    with TestClient(app) as client:
        stage = "automatic session HTTP contracts"
        fresh = client.post("/api/v1/sessions", json={"title": "New conversation", "repository": {"repo_id": "owner/repo", "ref": "main"}})
        check(fresh.status_code == 201 and fresh.json()["project_id"] == str(project), "project-free session create failed")
        fresh_id = fresh.json()["id"]
        greeting = {"routing": "auto", "idempotency_key": str(uuid4()), "goal": "hey", "repository": None}
        reply = client.post(f"/api/v1/sessions/{fresh_id}/turns", json=greeting)
        # Greetings are answered by the routed model, never by a canned reply.
        check(reply.status_code == 202 and reply.json()["task"] is not None and reply.json()["assistant_message"] is None, "greeting did not go through the model-routed task path")
        retry = client.post(f"/api/v1/sessions/{fresh_id}/turns", json=greeting)
        check(retry.json()["replayed"] and retry.json()["user_message"]["id"] == reply.json()["user_message"]["id"] and retry.json()["task"]["id"] == reply.json()["task"]["id"], "HTTP greeting retry lost receipt")
        with sessions.begin() as db:
            greeted = db.get(Task, reply.json()["task"]["id"])
            greeted.status = "PARTIAL"
            db.scalar(select(TaskJob).where(TaskJob.task_id == greeted.id)).status = "COMPLETED"
        inherited = {"routing": "auto", "idempotency_key": str(uuid4()), "goal": "Write a useful draft"}
        admitted = client.post(f"/api/v1/sessions/{fresh_id}/turns", json=inherited)
        check(admitted.status_code == 202 and admitted.json()["task"]["kind"] == "general", "semantic routing was not queued through bounded task path")
        conflict = client.post(f"/api/v1/sessions/{fresh_id}/turns", json={**inherited, "repository": None})
        check(conflict.status_code == 409, "inherit/null idempotency distinction lost over HTTP")
        with sessions.begin() as db:
            task = db.get(Task, admitted.json()["task"]["id"])
            check(task.permissions_json["automatic"]["repository"] == {"repo_id": "owner/repo", "ref": "main"}, "inherited selected repository missing")
            task.status = "PARTIAL"
            db.scalar(select(TaskJob).where(TaskJob.task_id == task.id)).status = "COMPLETED"
        print("PASS project-free HTTP sessions, routed greeting replay and inherit-vs-null idempotency", flush=True)
        response = client.post("/api/v1/agents", json={"project_id": str(project), "definition": definition})
        check(response.status_code == 201, "custom agent create failed " + str(response.status_code) + " " + response.text[:400])
        agent_id = response.json()["id"]
        check(client.post("/api/v1/agents", json={"project_id": str(project), "definition": definition}).status_code == 409, "same immutable version accepted")
        definition["version"] = 2
        definition["system_prompt"] += " Explain limitations clearly."
        check(client.post("/api/v1/agents", json={"project_id": str(project), "definition": definition}).status_code == 201, "custom version update failed")
        check(len(client.get(f"/api/v1/agents/{agent_id}/versions").json()) == 2, "immutable version history missing")
        response = client.post(f"/api/v1/agents/{agent_id}/test", json={"version": 1, "goal": "Explain concrete source documentation", "max_cost_usd": "0.25"})
        check(response.status_code == 202, "custom test not queued")
        tested = response.json()
        with sessions() as db:
            custom_task = db.get(Task, tested["id"])
            ctx = research_context(custom_task)
            key = custom_task.permissions_json["agent_key"]
        resolved = asyncio.run(AgentRegistry(sessions).resolve_for_task(key, 1, ctx))
        check(resolved.definition.version == 1, "test silently switched to current version")
        from phase2_mock_child import FixtureModel, FixtureResources
        from agentforge.subagents.spawn_service import SpawnService
        from agentforge.subagents.budget import BudgetService
        from agentforge.repositories.runs import AgentRunRepository, ToolCallRepository
        from agentforge.security.policies import AuthorizationService
        from agentforge.tools.catalog import ToolCatalog
        from agentforge.llm.router import ModelRouter
        from agentforge.contracts.subagent import SpawnRequest
        resources, catalog = FixtureResources(user, project, custom_task.id), ToolCatalog()
        catalog.register("web_search", resources.search, max_cost_usd=Decimal(0))
        authorizer = AuthorizationService(sessions)
        shared = SpawnService(registry=AgentRegistry(sessions, authorizer), authorizer=authorizer,
            budgets=BudgetService(sessions), runs=AgentRunRepository(sessions), calls=ToolCallRepository(sessions),
            catalog=catalog, models=ModelRouter(FixtureModel(resources.evidence_id)), resources=resources)
        result = asyncio.run(shared.spawn_subagent(SpawnRequest(type=key, version=1,
            prompt="Use the synthetic fixture source and report explicit synthetic evidence limitations."), ctx))
        check(result.status == "PARTIAL", "synthetic custom fixture misrepresented as verified")
        with sessions() as db:
            run = db.scalar(select(AgentRun).where(AgentRun.task_id == custom_task.id))
            check(run.definition_version_id == resolved.version_id, "shared spawn used another immutable version")
            check(run.finished_at is not None, "shared spawn result not persisted")
        stage = "public management storage bounds"
        from agentforge.subagents.registry import canonical_snapshot
        from agentforge.contracts.agent_definition import AgentDefinition as DefinitionContract
        with sessions.begin() as db:
            for version in range(3, 101):
                definition["version"] = version
                snapshot, digest = canonical_snapshot(DefinitionContract.model_validate(definition))
                DefinitionRepository(db).save_project(project_id=project, agent_key=definition["name"],
                    definition_json=snapshot, sha256=digest, created_by=user, version=version)
            db.add_all([Project(id=uuid4(), owner_id=user, name="Bound fixture") for _ in range(98)])
        definition["version"] = 101
        rejected = client.post("/api/v1/agents", json={"project_id": str(project), "definition": definition})
        check(rejected.status_code == 409 and rejected.json()["detail"] == "agent_version_limit_reached", "agent version storage cap bypass")
        with ThreadPoolExecutor(max_workers=2) as pool:
            admitted = list(pool.map(lambda _: client.post("/api/v1/projects", json={"name": "Last project"}).status_code, range(2)))
        check(sorted(admitted) == [201, 409], "concurrent project storage cap bypass")
        check(client.delete(f"/api/v1/agents/{agent_id}").status_code == 204, "custom agent disable failed")
        check(client.post(f"/api/v1/agents/{agent_id}/test", json={"version": 1, "goal": "Explain concrete source documentation"}).status_code == 409, "disabled custom agent test admitted")
    print("PASS owner-scoped custom agent create, immutable version conflict/history, real queued exact older-version registry test, and soft disable", flush=True)
    stage = "coding continuation"
    from agentforge.contracts.tasks import TaskCreate
    from agentforge.repositories.tasks import TaskRepository
    from agentforge.repositories.jobs import JobRepository
    from agentforge.repositories.coding import CodingRepository
    from agentforge.repositories.coding_continuation import seed_continuation
    from agentforge.orchestration.coding_context import coding_context
    from agentforge.database.coding import CodingEvidence
    with sessions.begin() as db:
        for item in db.scalars(select(TaskJob)):
            item.status = "COMPLETED"
    coding_request = TaskCreate(project_id=project, kind="coding", goal="Fix addition", coding={
        "repository": {"repo_id": "owner/repo", "ref": "a" * 40}, "test_profiles": ["python_unittest"]})
    from agentforge.contracts.coding import RepoSelection
    code_conversation = repo.create(user, SessionCreate(project_id=project, repository=RepoSelection(repo_id="owner/repo", ref="main")))
    original = repo.turn(code_conversation.id, user, SessionTurnCreate(idempotency_key=uuid4(), **coding_request.model_dump(exclude={"project_id"})),
        automatic={"repository": {"repo_id": "owner/repo", "ref": "main"}, "resolved": True}).task
    jobs = JobRepository(sessions, lease_sec=90)
    lease = jobs.claim("continuation-fixture")
    with sessions() as db:
        original_row = db.get(Task, original.id)
        original_ctx = coding_context(original_row)
    workspace = CodingRepository(sessions, lease=lease)
    baseline = {"app.py": b"def add(a,b): return a-b\n", "test_app.py": b"# baseline tests\n"}
    workspace.ensure_session(original_ctx, repo_id="owner/repo", commit_sha="a" * 40, files=baseline)
    import hashlib
    workspace.write_file(original_ctx, operation_key="fixture:write", path="app.py",
        content="def add(a,b): return a+b\n", expected_sha256=hashlib.sha256(baseline["app.py"]).hexdigest())
    with sessions.begin() as db:
        db.get(Task, original.id).status = "PARTIAL"
    jobs.complete(lease)
    check(repo.get(code_conversation.id, user).continuation_task_id == original.id, "branch selection did not recognize pinned workspace")
    research = repo.turn(code_conversation.id, user, SessionTurnCreate(idempotency_key=uuid4(), goal="Research a related question", kind="general")).task
    with sessions.begin() as db:
        db.get(Task, research.id).status = "COMPLETED"
        db.get(Task, research.id).final_summary = "A natural related answer."
        db.get(TaskJob, db.scalar(select(TaskJob.id).where(TaskJob.task_id == research.id))).status = "COMPLETED"
    check(repo.get(code_conversation.id, user).continuation_task_id == original.id, "intervening research lost saved changes")
    followup = repo.turn(code_conversation.id, user, SessionTurnCreate(idempotency_key=uuid4(), kind="general", goal="add tests"),
        automatic={"repository": {"repo_id": "owner/repo", "ref": "main"}, "inherit_continuation": True}).task
    lease = jobs.claim("continuation-fixture-2")
    from agentforge.orchestration.store import WorkflowStore
    WorkflowStore(sessions, lease).resolve_automatic("coding", coding=coding_request.coding)
    with sessions() as db:
        followup_row = db.get(Task, followup.id)
        followup_ctx = coding_context(followup_row)
        receipt = followup_row.permissions_json["continuation"]
    workspace2 = CodingRepository(sessions, lease=lease)
    seed_continuation(workspace2, followup_ctx, receipt)
    check(workspace2.files(followup_ctx)["app.py"] == b"def add(a,b): return a+b\n", "followup discarded changes")
    check(workspace2.files(followup_ctx, baseline=True) == baseline, "original baseline lost")
    check(workspace2.session(followup_ctx)["id"] != workspace.session(original_ctx)["id"], "workspace reused across tasks")
    with sessions() as db:
        check(not list(db.scalars(select(CodingEvidence).where(CodingEvidence.task_id == followup.id))), "old execution evidence reused")
    try:
        TaskRepository(sessions).create(outsider, coding_request.model_copy(update={"project_id": otherproject, "continue_from_task_id": original.id}))
    except TaskConflict:
        pass
    else:
        raise AssertionError("cross-owner continuation accepted")
    with sessions.begin() as db:
        db.get(Task, followup.id).status = "PARTIAL"
    check(repo.get(code_conversation.id, user).continuation_task_id == followup.id, "latest coding workspace was not offered")
    cleared = repo.patch(code_conversation.id, user, SessionPatch(repository=None))
    check(cleared.continuation_task_id is None, "No repository retained continuation authority")
    reselected = repo.patch(code_conversation.id, user, SessionPatch(repository=RepoSelection(repo_id="owner/repo", ref="main")))
    check(reselected.continuation_task_id is None, "repository reselection reused pre-reset workspace")
    print("PASS coding followup retains immutable changed files and original baseline with fresh workspace, grants, tests and owner isolation", flush=True)
    stage = "global atomic admission"
    from agentforge.contracts.tasks import TaskCreate
    from agentforge.repositories.tasks import TaskRepository
    from agentforge.repositories import admission
    with sessions.begin() as db:
        for task in db.scalars(select(Task)):
            task.status = "PARTIAL"
    public_users, public_projects = [uuid4(), uuid4()], [uuid4(), uuid4()]
    with sessions.begin() as db:
        db.add_all([User(id=uid, github_user_id=90000 + index, display_name="Quota fixture") for index, uid in enumerate(public_users)])
        db.flush()
        db.add_all([Project(id=pid, owner_id=uid, name="Quota") for uid, pid in zip(public_users, public_projects)])
    limits = settings.model_copy(update={"global_max_concurrent_tasks": 1, "global_max_daily_cost_usd": 20})
    def admit(index):
        try:
            TaskRepository(sessions).create(public_users[index], TaskCreate(project_id=public_projects[index], goal="continue"))
            return "admitted"
        except AdmissionDenied as exc:
            return str(exc)
    with patch.object(admission, "get_settings", return_value=limits), ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(admit, (0, 1)))
    check(outcomes.count("admitted") == 1 and outcomes.count("global_concurrent_task_limit") == 1, "cross-user global race exceeded quota")
    limits = settings.model_copy(update={"global_max_concurrent_tasks": 20, "global_max_daily_cost_usd": Decimal("0.1")})
    with patch.object(admission, "get_settings", return_value=limits):
        check(admit(0) == "global_daily_cost_limit", "global daily cost exceeded")
    print("PASS global cross-user concurrent admission serialization and conservative daily spending bounds", flush=True)
    print("PASS migrations and metadata; concurrent turn idempotency; owner isolation; single active turn; durable recovery; cursor; untrusted bounded provenance; immutable receipts", flush=True)

try:
    source = make_url(Settings().database_url)
    check(source.host in ("localhost", "127.0.0.1", "::1"), "nonlocal configured DB")
    admin = admin_engine(source)
    with admin.connect() as connection:
        owner = connection.dialect.identifier_preparer.quote_identifier(source.username)
        connection.execute(text('CREATE DATABASE "' + name + '" OWNER ' + owner))
    created = True
    settings = Settings(_env_file=None, database_url=source.set(database=name).render_as_string(hide_password=False),
        langgraph_checkpoint_db_url=source.set(drivername="postgresql", database=name).render_as_string(hide_password=False),
        app_env="test", auth_mode="disabled", llm_api_key=None, tavily_api_key=None, neatlogs_api_key=None)
    with patch.object(settings_module, "get_settings", return_value=settings):
        # admission imported after patch, ensuring no live URL/settings are reused.
        run(settings)
except BaseException as exc:
    import traceback
    print("FAIL stage=" + stage + " class=" + type(exc).__name__, flush=True)
    if isinstance(exc, AssertionError):
        print("ASSERTION " + str(exc), flush=True)
    for frame in traceback.extract_tb(exc.__traceback__):
        print(f"LOCATION {Path(frame.filename).name}:{frame.lineno} {frame.name}", flush=True)
    raise SystemExit(1) from None
finally:
    if engine is not None:
        engine.dispose()
    if created:
        with admin.connect() as connection:
            connection.execute(text('DROP DATABASE "' + name + '" WITH (FORCE)'))
        print("PASS disposable database removed", flush=True)
    if admin is not None:
        admin.dispose()
