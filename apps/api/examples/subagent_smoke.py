"""Disposable PostgreSQL end-to-end delegation test. No live provider or workspace effects."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
from uuid import uuid4
from decimal import Decimal
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, select, text, delete
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from agentforge.settings import Settings
from _disposable_db import admin_engine
import agentforge.settings as settings_module

ROOT = Path(__file__).resolve().parents[3]


async def exercise(settings, sessions):
    from agentforge.database.models import User, Project, Task, AgentRun, BudgetReservation, ToolCall, BudgetOperation, BudgetTransaction
    from agentforge.orchestration.context import agent_context
    from agentforge.contracts.subagent import SpawnRequest
    from agentforge.repositories.runs import AgentRunRepository, ToolCallRepository
    from agentforge.repositories.events import append_event
    from agentforge.repositories.delegation import read_delegation
    from agentforge.repositories.activity import ActivityRepository, ToolCallNotFound
    from agentforge.security.policies import AuthorizationService
    from agentforge.subagents.registry import AgentRegistry, AGENT_KEY, AGENT_VERSION, _builtin_cache
    from agentforge.subagents.spawn_service import SpawnService
    from agentforge.subagents.budget import BudgetService, BudgetExceeded, MAX_CHILDREN
    from agentforge.tools.catalog import ToolCatalog
    from agentforge.tools.contracts import ToolOutcome
    from agentforge.tools.delegation import register_delegation
    from agentforge.llm.models import ModelResponse, ModelUsage, ModelProviderError
    from agentforge.llm.router import ModelRouter
    from agentforge.main import create_app
    from agentforge.auth.session import current_user
    from fastapi.testclient import TestClient
    from agentforge.workers.reconciliation import reconcile
    _builtin_cache.clear()
    owner, outsider, project = uuid4(), uuid4(), uuid4()
    with sessions.begin() as db:
        db.add_all([User(id=owner, github_user_id=-8701, display_name="Owner"), User(id=outsider, github_user_id=-8702, display_name="Other")])
        db.flush()
        db.add(Project(id=project, owner_id=owner, name="Fixture"))
    with sessions() as db:
        identity, other = db.get(User, owner), db.get(User, outsider)
        db.expunge_all()

    def response(content=None, tool=None, args=None, call_id="call"):
        message = {"role": "assistant", "content": content}
        if tool:
            message["tool_calls"] = [{"id": call_id, "type": "function", "function": {"name": tool, "arguments": json.dumps(args)}}]
        return ModelResponse(message, ModelUsage(1, 1, Decimal("0.0001")), "fixture", "tool_calls" if tool else "stop")

    class Model:
        model_id = "fixture"
        def __init__(self, scenario):
            self.scenario, self.seen = scenario, []
            self.started = asyncio.Event()
        def maximum_request_cost(self, messages, tools): return Decimal("0.001")
        async def complete(self, messages, tools, on_text=None, on_narration=None):
            names = {t["function"]["name"] for t in tools}
            self.seen.append((messages, names))
            child = "delegate_task" not in names
            if not child:
                if messages[-1]["role"] == "tool":
                    if self.scenario == "failure": assert '"status": "error"' in messages[-1]["content"]
                    return response("Parent integrated the report.")
                return response(tool="delegate_task", args={"title": "Review auth", "prompt": "Inspect authentication and report evidence.",
                    "mode": "work" if self.scenario == "approval" else "research"})
            assert "PRIVATE_PARENT_CONTEXT" not in str(messages)
            assert "create_pull_request" not in names and "delegate_task" not in names
            if self.scenario == "cancel":
                self.started.set()
                await asyncio.Event().wait()
            if self.scenario == "failure": raise ModelProviderError("fixture failure")
            if messages[-1]["role"] == "tool":
                if self.scenario == "approval": assert "DECLINED" in messages[-1]["content"]
                return response(("Child report: checked the evidence." + " Verified source." * 4200 if self.scenario == "success" else "Child report: checked the evidence."))
            if on_narration: on_narration("Checking the assigned evidence.")
            if self.scenario == "approval":
                return response("Checking the assigned evidence.", "write", {"path": "example.txt", "content": "fixture"})
            assert "write" not in names and "bash" not in names
            return response("Checking the assigned evidence.", "web_search", {"query": "fixture"})

    for scenario in ("success", "failure", "approval", "cancel"):
        task_id = uuid4()
        with sessions.begin() as db:
            task = Task(id=task_id, project_id=project, created_by=owner, goal="PRIVATE_PARENT_CONTEXT", status="RUNNING",
                thread_id=str(uuid4()), max_cost_usd=100, max_wall_time_sec=600, permissions_json={})
            db.add(task)
        ctx = agent_context(task)
        events = []
        def emit(kind, payload):
            with sessions.begin() as db:
                row = db.scalar(select(Task).where(Task.id == task_id).with_for_update())
                append_event(db, row, kind, payload)
            events.append((kind, payload))
        catalog = ToolCatalog()
        writes = []
        async def search(args, execution): return ToolOutcome(status="ok", data={"text": "Evidence"}, cost_usd=Decimal(0))
        async def write(args, execution):
            writes.append(args)
            return ToolOutcome(status="ok", cost_usd=Decimal(0))
        catalog.register("web_search", search, max_cost_usd=Decimal(0))
        catalog.register("write", write, max_cost_usd=Decimal(0))
        approvals = []
        async def allows(ctx, **kwargs):
            approvals.append((ctx, kwargs))
            return False
        model = Model(scenario)
        budgets = BudgetService(sessions)
        auth = AuthorizationService(sessions)
        service = SpawnService(registry=AgentRegistry(sessions, auth), authorizer=auth, budgets=budgets,
            runs=AgentRunRepository(sessions), calls=ToolCallRepository(sessions), catalog=catalog,
            models=ModelRouter(model), lifecycle=emit, approvals=NS(allows=allows))
        register_delegation(catalog, service)
        future = asyncio.create_task(service.spawn_subagent(SpawnRequest(type=AGENT_KEY, version=AGENT_VERSION, prompt=task.goal), ctx))
        if scenario == "cancel":
            await asyncio.wait_for(model.started.wait(), 10)
            future.cancel()
            try: await future
            except asyncio.CancelledError: pass
            else: raise AssertionError("stop was swallowed")
        else:
            result = await asyncio.wait_for(future, 15)
            assert result.status == "PASS", result
        with sessions() as db:
            runs = list(db.scalars(select(AgentRun).where(AgentRun.task_id == task_id)))
            assert len(runs) == 2
            parent = next(r for r in runs if r.parent_run_id is None)
            child = next(r for r in runs if r.parent_run_id is not None)
            assert child.parent_run_id == parent.id
            assert child.status == {"success": "COMPLETED", "failure": "FAILED", "approval": "COMPLETED", "cancel": "CANCELLED"}[scenario]
            assert all(r.settled_at for r in db.scalars(select(BudgetReservation).where(BudgetReservation.task_id == task_id)))
            call = db.scalar(select(ToolCall).where(ToolCall.task_id == task_id, ToolCall.tool_name == "delegate_task"))
            call_id = call.id
        detail = read_delegation(sessions, task_id, call_id, owner)
        assert detail["title"] == "Review auth" and detail["status"] == child.status
        if scenario == "success":
            from agentforge.database.sessions import ToolResultSnapshot
            with sessions() as db:
                full_report = db.scalar(select(ToolResultSnapshot).where(ToolResultSnapshot.task_id == task_id,
                    ToolResultSnapshot.operation_key.endswith(":report")))
                assert full_report is not None and full_report.content == detail["summary"]
        try: read_delegation(sessions, task_id, call_id, outsider)
        except ToolCallNotFound: pass
        else: raise AssertionError("foreign owner read child")
        assert ActivityRepository(sessions).summary(task_id, owner).action_count == 1
        assert ActivityRepository(sessions).summary(task_id, owner).note_count == 0
        assert not writes
        if scenario == "approval": assert approvals and approvals[0][0].parent_run_id == parent.id
        if scenario == "success":
            assert any(p.get("parent_run_id") and kind == "assistant.message" for kind, p in events)
            app = create_app(settings)
            app.dependency_overrides[current_user] = lambda: identity
            with TestClient(app) as client:
                route = f"/api/v1/tasks/{task_id}/tool-calls/{call_id}/subagent"
                response_ = client.get(route)
                assert response_.status_code == 200, response_.text
                assert response_.json()["summary"].startswith("Child report: checked the evidence.")
                assert len(response_.json()["summary"]) > 16000
                app.dependency_overrides[current_user] = lambda: other
                assert client.get(route).status_code == 404
            # Child accounting refuses excess usage, and an active sibling prevents another child.
            with sessions.begin() as db:
                for ident in (parent.id, child.id):
                    row = db.get(AgentRun, ident); row.status = "RUNNING"; row.finished_at = None; row.result_json = None
                    reservation = db.get(BudgetReservation, row.budget_reservation_id); reservation.settled_at = None
                    db.execute(delete(BudgetTransaction).where(BudgetTransaction.reservation_id == reservation.id, BudgetTransaction.kind.in_(("CHARGE", "RELEASE"))))
                db.get(BudgetReservation, child.budget_reservation_id).max_tool_calls = 1
                db.get(BudgetReservation, child.budget_reservation_id).max_steps = 2
            try: budgets.authorize_operation(child.budget_reservation_id, "overflow", Decimal(0), kind="tool")
            except BudgetExceeded: pass
            else: raise AssertionError("child tool limit ignored")
            try: budgets.authorize_operation(child.budget_reservation_id, "oversteps", Decimal(0))
            except BudgetExceeded: pass
            else: raise AssertionError("child model limit ignored")
            with sessions.begin() as db:
                db.get(BudgetReservation, child.budget_reservation_id).deadline = datetime.now(timezone.utc) - timedelta(seconds=1)
            try: budgets.check_active(child.budget_reservation_id)
            except BudgetExceeded: pass
            else: raise AssertionError("child deadline ignored")
            with sessions.begin() as db:
                db.get(BudgetReservation, child.budget_reservation_id).deadline = datetime.now(timezone.utc) + timedelta(seconds=60)
            try: budgets.authorize_operation(child.budget_reservation_id, "overcost", Decimal(6))
            except BudgetExceeded: pass
            else: raise AssertionError("child cost limit ignored")
            # Siblings of one parent may be active together, up to the per-parent limit (counting ones whose run row does not exist
            # yet); anything that is not the parent or one of its children still fails closed.
            def reserve(**kwargs):
                return budgets.reserve(task_id=task_id, run_id=uuid4(), owner_id=owner, project_id=project, amount_usd=Decimal(1),
                    max_steps=2, max_tool_calls=2, max_wall_time_sec=10, **kwargs)
            accepted = 1  # the child that is still open
            while True:
                try: reserve(depth=2, parent_run_id=parent.id)
                except BudgetExceeded as exc:
                    assert str(exc) == "delegation_limit", exc
                    break
                accepted += 1
            assert accepted == MAX_CHILDREN, accepted
            try: reserve(depth=1)
            except BudgetExceeded as exc: assert str(exc) == "concurrent_spawn_denied", exc
            else: raise AssertionError("a second top-level run was accepted beside the active one")
            store = NS(sessions=sessions, lease=NS(reclaimed=False), locked_task=lambda db, **kw: db.scalar(select(Task).where(Task.id == task_id).with_for_update()))
            assert reconcile(store, budgets)
            with sessions() as db:
                assert all(r.settled_at for r in db.scalars(select(BudgetReservation).where(BudgetReservation.task_id == task_id)))
                assert all(r.status != "RUNNING" for r in db.scalars(select(AgentRun).where(AgentRun.task_id == task_id)))
        print("PASS", scenario, flush=True)

    await parallel(sessions, owner, project)


async def parallel(sessions, owner, project):
    """Three assignments requested in one turn work side by side; each is reported back and nothing is left open."""
    from agentforge.database.models import Task, AgentRun, BudgetReservation, ToolCall
    from agentforge.orchestration.context import agent_context
    from agentforge.contracts.subagent import SpawnRequest
    from agentforge.repositories.runs import AgentRunRepository, ToolCallRepository
    from agentforge.repositories.events import append_event
    from agentforge.repositories.activity import ActivityRepository
    from agentforge.security.policies import AuthorizationService
    from agentforge.subagents.registry import AgentRegistry, AGENT_KEY, AGENT_VERSION
    from agentforge.subagents.spawn_service import SpawnService
    from agentforge.subagents.budget import BudgetService
    from agentforge.tools.catalog import ToolCatalog
    from agentforge.tools.contracts import ToolOutcome
    from agentforge.tools.delegation import register_delegation
    from agentforge.llm.models import ModelResponse, ModelUsage
    from agentforge.llm.router import ModelRouter

    titles = ["Auth flow", "Billing", "Search index"]
    started, everyone = [], asyncio.Event()
    usage = ModelUsage(1, 1, Decimal("0.0001"))

    class Model:
        model_id = "fixture"
        def __init__(self): self.reports = []
        def maximum_request_cost(self, messages, tools): return Decimal("0.001")
        async def complete(self, messages, tools, on_text=None, on_narration=None):
            if "delegate_task" in {t["function"]["name"] for t in tools}:
                if messages[-1]["role"] == "tool":
                    self.reports = [m["content"] for m in messages if m["role"] == "tool"]
                    return ModelResponse({"role": "assistant", "content": "Integrated all reports."}, usage, "fixture", "stop")
                requested = [{"id": f"call-{index}", "type": "function", "function": {"name": "delegate_task", "arguments": json.dumps(
                    {"title": title, "prompt": f"Investigate {title} and report evidence.", "mode": "research"})}} for index, title in enumerate(titles)]
                return ModelResponse({"role": "assistant", "content": None, "tool_calls": requested}, usage, "fixture", "tool_calls")
            title = next(title for title in titles if title in messages[-1]["content"])
            started.append(title)
            if len(started) == len(titles): everyone.set()
            # A child only gets past this line if the others have started too: run one after another, it would wait forever.
            await asyncio.wait_for(everyone.wait(), 10)
            return ModelResponse({"role": "assistant", "content": f"Child report on {title}."}, usage, "fixture", "stop")

    task_id = uuid4()
    with sessions.begin() as db:
        task = Task(id=task_id, project_id=project, created_by=owner, goal="Compare three areas", status="RUNNING",
            thread_id=str(uuid4()), max_cost_usd=100, max_wall_time_sec=600, permissions_json={})
        db.add(task)
    ctx = agent_context(task)
    events = []
    def emit(kind, payload):
        with sessions.begin() as db:
            row = db.scalar(select(Task).where(Task.id == task_id).with_for_update())
            append_event(db, row, kind, payload)
        events.append((kind, payload))
    catalog = ToolCatalog()
    async def search(args, execution): return ToolOutcome(status="ok", data={"text": "Evidence"}, cost_usd=Decimal(0))
    catalog.register("web_search", search, max_cost_usd=Decimal(0))
    model = Model()
    auth = AuthorizationService(sessions)
    service = SpawnService(registry=AgentRegistry(sessions, auth), authorizer=auth, budgets=BudgetService(sessions),
        runs=AgentRunRepository(sessions), calls=ToolCallRepository(sessions), catalog=catalog, models=ModelRouter(model), lifecycle=emit)
    register_delegation(catalog, service)
    result = await asyncio.wait_for(service.spawn_subagent(SpawnRequest(type=AGENT_KEY, version=AGENT_VERSION, prompt=task.goal), ctx), 30)
    assert (result.status, result.summary) == ("PASS", "Integrated all reports."), result
    assert sorted(started) == sorted(titles), started
    assert len(model.reports) == 3 and all("Child report on" in report for report in model.reports), model.reports
    with sessions() as db:
        runs = list(db.scalars(select(AgentRun).where(AgentRun.task_id == task_id)))
        parent = next(run for run in runs if run.parent_run_id is None)
        children = [run for run in runs if run.parent_run_id == parent.id]
        assert len(children) == 3 and all(run.status == "COMPLETED" for run in children), [run.status for run in children]
        assert all(row.settled_at for row in db.scalars(select(BudgetReservation).where(BudgetReservation.task_id == task_id)))
        calls = list(db.scalars(select(ToolCall).where(ToolCall.task_id == task_id, ToolCall.tool_name == "delegate_task")))
        assert len(calls) == 3 and all(call.status == "SUCCEEDED" for call in calls), [call.status for call in calls]
    child_ids = {str(run.id) for run in children}
    starts = [payload for kind, payload in events if kind == "tool.started" and payload.get("tool") == "delegate_task"]
    assert len(starts) == 3 and {payload["child_run_id"] for payload in starts} == child_ids, starts
    assert {payload["agent_run_id"] for kind, payload in events if payload.get("parent_run_id")} <= child_ids
    assert ActivityRepository(sessions).summary(task_id, owner).action_count == 3
    print("PASS parallel", flush=True)


def main():
    name = "agentforge_subagent_smoke_" + uuid4().hex[:10]
    source = make_url(Settings().database_url)
    admin = admin_engine(source)
    engine = None
    created = False
    try:
        with admin.connect() as db:
            db.execute(text('CREATE DATABASE "' + name + '" OWNER ' + db.dialect.identifier_preparer.quote_identifier(source.username)))
        created = True
        settings = Settings(_env_file=None, database_url=source.set(database=name).render_as_string(hide_password=False),
            app_env="test", auth_mode="disabled", llm_api_key=None, tavily_api_key=None, neatlogs_api_key=None)
        with patch.object(settings_module, "get_settings", return_value=settings):
            from alembic import command
            from alembic.config import Config
            cfg = Config(str(ROOT / "apps/api/alembic.ini")); cfg.set_main_option("script_location", str(ROOT / "apps/api/alembic"))
            command.upgrade(cfg, "head")
            engine = create_engine(settings.database_url)
            asyncio.run(exercise(settings, sessionmaker(engine, expire_on_commit=False)))
        print("subagent smoke OK")
    finally:
        if engine is not None: engine.dispose()
        if created:
            with admin.connect() as db: db.execute(text('DROP DATABASE "' + name + '" WITH (FORCE)'))
        admin.dispose()


if __name__ == "__main__": main()
