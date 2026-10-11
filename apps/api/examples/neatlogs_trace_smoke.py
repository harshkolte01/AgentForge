"""Disposable PostgreSQL check of Neatlogs tracing through the real worker path.

`runner.execute` runs with the real SDK, exporting to a local OTLP receiver (127.0.0.1), against a throwaway database. The agent
itself is a fixture (no provider is called, nothing leaves this machine). It checks that a conversation's turns become one
Neatlogs session, that a task's trace root says how it ended, and that a failed task is marked failed.
"""
import asyncio
import sys
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from _disposable_db import admin_engine
from pydantic import SecretStr
from agentforge.settings import Settings
import agentforge.settings as settings_module

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps/api/tests"))
from otlp_fakes import Receiver  # noqa: E402

name = "agentforge_trace_" + uuid4().hex[:12]
admin = engine = receiver = None
created = False


def check(condition, reason):
    if not condition:
        raise AssertionError(reason)


async def run(settings, receiver):
    global engine
    from alembic import command
    from alembic.config import Config
    from agentforge.contracts.sessions import AutoTurnCreate, SessionCreate
    from agentforge.database.models import Project, User
    from agentforge.llm.models import ModelResponse, ModelUsage
    from agentforge.repositories.jobs import JobRepository
    from agentforge.repositories.sessions import SessionRepository
    from agentforge.telemetry.neatlogs_setup import Telemetry
    from agentforge.telemetry.runtime import ObservedModel
    from agentforge.workers import runner
    cfg = Config(str(ROOT / "apps/api/alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "apps/api/alembic"))
    command.upgrade(cfg, "head")
    engine = create_engine(settings.database_url)
    factory = sessionmaker(engine, expire_on_commit=False)
    user = uuid4()
    with factory.begin() as db:
        db.add(User(id=user, github_user_id=-771, display_name="Trace fixture"))
        db.flush()
        db.add(Project(id=uuid4(), owner_id=user, name="Trace fixture"))
    repo = SessionRepository(factory, context_budget=48000)
    jobs = JobRepository(factory)
    telemetry = Telemetry(settings)
    check(telemetry.enabled, "tracing did not start")
    worker_settings = settings.model_copy(update={"conversation_titles_enabled": False, "worker_heartbeat_sec": 10})

    class Model:
        model_id = "fixture/model"

        def maximum_request_cost(self, messages, tools):
            return Decimal(0)

        async def complete(self, messages, tools, **kwargs):
            return ModelResponse({"role": "assistant", "content": "ok"}, ModelUsage(40, 8, Decimal("0.001")), "fixture/model", "stop")

    def claim(target):
        while True:
            lease = jobs.claim("fixture-worker")
            check(lease is not None, "missing worker job")
            if lease.task_id == target:
                return lease
            jobs.complete(lease)

    async def turn(chat, goal, *, fail):
        admitted = repo.enqueue(chat, user, AutoTurnCreate(routing="auto", idempotency_key=uuid4(), goal=goal))
        lease = claim(admitted.task.id)

        async def agent(*, store, service, authority, config):
            await ObservedModel(Model(), telemetry, store).complete([{"role": "user", "content": goal}], [])
            if fail:
                store.finish("FAILED", "The model is not configured correctly.", error_code="MODEL_PROVIDER_ERROR")
            else:
                store.finish("COMPLETED", "Done.")

        async def close():
            return None
        with patch.object(runner, "compose_runtime", lambda *_: NS(service=NS(), models=NS(choose=lambda _: Model()), close=close, after_turn=None)), \
                patch.object(runner, "run_agent_turn", agent):
            await runner.execute(worker_settings, factory, telemetry, jobs, lease)
        return admitted.task.id

    chat = repo.create(user, SessionCreate()).id
    other = repo.create(user, SessionCreate()).id
    first = await turn(chat, "first private question", fail=False)
    second = await turn(chat, "second private question", fail=True)
    elsewhere = await turn(other, "another conversation", fail=False)
    telemetry.shutdown()

    roots = {span.attributes["task_id"]: span for span in receiver.named("agentforge_task")}
    check(set(roots) == {str(first), str(second), str(elsewhere)}, f"one trace root per task, got {sorted(roots)}")
    check(len({span.trace_id for span in roots.values()}) == 3, "each task is its own trace")
    check(roots[str(first)].attributes["neatlogs.session.id"] == str(chat), "the first turn is not in its conversation's session")
    check(roots[str(second)].attributes["neatlogs.session.id"] == str(chat), "the second turn is not in the same session")
    check(roots[str(elsewhere)].attributes["neatlogs.session.id"] == str(other), "another conversation shares the session")
    check(all(span.attributes["neatlogs.end_user.id"] == str(user) for span in roots.values()), "the person is not the end user")
    print("PASS a conversation's turns are one Neatlogs session; another conversation is another", flush=True)

    ok, bad = roots[str(first)], roots[str(second)]
    check(ok.attributes["agentforge.task.status"] == "COMPLETED" and ok.status == 1, "a finished task's root is not OK")
    check(bad.attributes["agentforge.task.status"] == "FAILED" and bad.status == 2, "a failed task's root is not marked failed")
    check(bad.attributes["agentforge.task.error_code"] == "MODEL_PROVIDER_ERROR" and bad.attributes["agentforge.error.code"] == "MODEL_PROVIDER_ERROR", "the failure code is missing")
    check((ok.attributes["agentforge.task.tool_calls"], ok.attributes["agentforge.task.tool_failures"]) == (0, 0), "tool-call counts are wrong")
    check(str(first) in ok.attributes["task_id"] and ok.attributes["lease_generation"] >= 1, "task and lease are not on the root")
    print("PASS the trace root carries the task's own outcome; a failed task ends failed with its code", flush=True)

    models = [span for span in receiver.named("model_request") if span.trace_id == ok.trace_id]
    check(len(models) == 1 and models[0].parent_id == ok.span_id, "the model call is not inside its task's trace")
    check(models[0].attributes["neatlogs.llm.token_count.total"] == 48 and abs(models[0].attributes["neatlogs.llm.cost_usd"] - 0.001) < 1e-9, "usage is missing")
    check("private question" not in repr([span.__dict__ for span in receiver.spans]), "a prompt reached the export")
    print("PASS the model call is in its task's trace with usage and cost, and no prompt left the process", flush=True)


try:
    source = make_url(Settings().database_url)
    admin = admin_engine(source)
    with admin.connect() as connection:
        owner = connection.dialect.identifier_preparer.quote_identifier(source.username)
        connection.execute(text('CREATE DATABASE "' + name + '" OWNER ' + owner))
    created = True
    receiver = Receiver()
    settings = Settings(_env_file=None, database_url=source.set(database=name).render_as_string(hide_password=False),
        app_env="test", auth_mode="disabled", llm_api_key=None, tavily_api_key=None,
        neatlogs_api_key=SecretStr("nl_test_key_for_local_receiver"), neatlogs_endpoint=receiver.url)
    with patch.object(settings_module, "get_settings", return_value=settings):
        asyncio.run(run(settings, receiver))
except BaseException as exc:
    import traceback
    print("FAIL " + type(exc).__name__, flush=True)
    if isinstance(exc, AssertionError): print(str(exc), flush=True)
    for frame in traceback.extract_tb(exc.__traceback__):
        print(f"LOCATION {Path(frame.filename).name}:{frame.lineno} {frame.name}", flush=True)
    raise SystemExit(1) from None
finally:
    if engine is not None: engine.dispose()
    if receiver is not None: receiver.close()
    if created:
        with admin.connect() as connection:
            connection.execute(text('DROP DATABASE "' + name + '" WITH (FORCE)'))
        print("PASS disposable database removed", flush=True)
    if admin is not None: admin.dispose()
