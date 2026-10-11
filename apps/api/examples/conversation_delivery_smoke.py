"""Disposable PostgreSQL queue/context/recovery regressions. No live providers."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
from uuid import uuid4
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from _disposable_db import admin_engine
from agentforge.settings import Settings
import agentforge.settings as settings_module

ROOT = Path(__file__).resolve().parents[3]
name = "agentforge_delivery_" + uuid4().hex[:12]
admin = engine = None
created = False


def check(condition, reason):
    if not condition:
        raise AssertionError(reason)


async def run(settings):
    global engine
    from alembic import command
    from alembic.config import Config
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from agentforge.database.models import Base, BudgetOperation, Project, Task, TaskJob, User
    from agentforge.database.sessions import (Conversation, ConversationMessage, ConversationSummary,
        SessionTurn, TurnExecution, ProviderInput, SummaryAttempt)
    from agentforge.contracts.sessions import AutoTurnCreate, SessionCreate, SessionPatch
    from agentforge.repositories.sessions import SessionRepository
    from agentforge.repositories.tasks import TaskConflict, TaskNotFound
    from agentforge.repositories.conversation_context import ConversationContextRepository, history_snapshot, encoded
    from agentforge.repositories.jobs import JobRepository
    from agentforge.orchestration.store import WorkflowStore
    from agentforge.orchestration.context import agent_context
    from agentforge.workers.conversation_context import prepare_turn_context
    from agentforge.workers.reconciliation import reconcile
    from agentforge.subagents.budget import BudgetService
    from agentforge.llm.models import ModelResponse, ModelUsage
    cfg = Config(str(ROOT / "apps/api/alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "apps/api/alembic"))
    command.upgrade(cfg, "0010_task_stream_revision")
    engine = create_engine(settings.database_url)
    # Seed the previous schema, including a task-free reply, before upgrading.
    seed_user, seed_project, seed_chat, seed_turn, seed_request, seed_reply = [uuid4() for _ in range(6)]
    with engine.begin() as db:
        db.execute(text("INSERT INTO users(id,github_user_id,display_name) VALUES (:id,-553,'Legacy fixture')"), {"id": seed_user})
        db.execute(text("INSERT INTO projects(id,owner_id,name) VALUES (:id,:owner,'Legacy')"), {"id": seed_project, "owner": seed_user})
        db.execute(text("INSERT INTO conversations(id,owner_id,project_id,title,mode,last_sequence) VALUES (:id,:owner,:project,'Legacy','general',2)"),
            {"id": seed_chat, "owner": seed_user, "project": seed_project})
        for ident, sequence, role, content in ((seed_request, 1, "user", "Earlier question"), (seed_reply, 2, "assistant", "Earlier answer")):
            db.execute(text("INSERT INTO conversation_messages(id,session_id,sequence,role,content) VALUES (:id,:chat,:seq,:role,:content)"),
                {"id": ident, "chat": seed_chat, "seq": sequence, "role": role, "content": content})
        db.execute(text("INSERT INTO session_turns(id,session_id,idempotency_key,request_sha256,user_message_id) VALUES (:id,:chat,:key,:digest,:message)"),
            {"id": seed_turn, "chat": seed_chat, "key": uuid4(), "digest": "0" * 64, "message": seed_request})
    command.upgrade(cfg, "head")
    factory = sessionmaker(engine, expire_on_commit=False)
    with engine.connect() as connection:
        check(not compare_metadata(MigrationContext.configure(connection, opts={"compare_type": True,
            "compare_server_default": True}), Base.metadata), "migration drift")
    user, other, project = uuid4(), uuid4(), uuid4()
    with factory.begin() as db:
        db.add_all([User(id=user, github_user_id=-551, display_name="Fixture"), User(id=other, github_user_id=-552, display_name="Other")])
        db.flush()
        db.add(Project(id=project, owner_id=user, name="Delivery fixture"))
    repo = SessionRepository(factory, context_budget=48000)
    legacy = repo.turns(seed_chat, seed_user)
    check(legacy[0].assistant_message.id == seed_reply and legacy[0].state == "completed", "task-free migration lost reply")
    creation = SessionCreate(idempotency_key=uuid4())
    with ThreadPoolExecutor(max_workers=2) as pool:
        copies = list(pool.map(lambda _: repo.create(user, creation), range(2)))
    check(copies[0].id == copies[1].id, "creation retry duplicated conversation")
    conversation = copies[0]
    try:
        repo.create(user, creation.model_copy(update={"title": "different"}))
        raise AssertionError("creation payload conflict accepted")
    except TaskConflict:
        pass
    request = AutoTurnCreate(routing="auto", idempotency_key=uuid4(), goal="Use the cobalt option; keep the report in Hindi.",
        attachments=[{"name": "original-decisions.txt", "content": "The selected option is cobalt."}])
    with ThreadPoolExecutor(max_workers=2) as pool:
        copies = list(pool.map(lambda _: repo.enqueue(conversation.id, user, request), range(2)))
    first = copies[0]
    check(first.turn_id == copies[1].turn_id and first.task.id == copies[1].task.id, "duplicate first turn")
    second_request = AutoTurnCreate(routing="auto", idempotency_key=uuid4(), goal="Explain the second option.")
    second = repo.enqueue(conversation.id, user, second_request)
    third_request = AutoTurnCreate(routing="auto", idempotency_key=uuid4(), goal="FUTURE SECRET must stay outside the preceding turn.")
    third = repo.enqueue(conversation.id, user, third_request)
    check(second.task is None and third.task is None and second.queue_position == 1 and third.queue_position == 2, "FIFO admission")
    check(repo.replay(conversation.id, user, second_request).turn_id == second.turn_id, "queued replay")
    try:
        repo.patch(conversation.id, user, SessionPatch(archived=True))
        raise AssertionError("archived waiting work")
    except TaskConflict:
        pass
    jobs = JobRepository(factory)

    def claim(target):
        while True:
            lease = jobs.claim("fixture-worker")
            check(lease is not None, "missing worker job")
            with factory() as db:
                status = db.get(Task, lease.task_id).status
            if status in {"COMPLETED", "PARTIAL", "FAILED", "CANCELLED"}:
                jobs.complete(lease)
                continue
            check(lease.task_id == target, "wrong dispatch order")
            return WorkflowStore(factory, lease)

    store = claim(first.task.id)
    with factory.begin() as db:
        db.get(Task, first.task.id).created_at = datetime.now(timezone.utc) - timedelta(hours=1)
    jobs.begin_execution(store.lease)
    started = store.task()
    check(started.execution_deadline > datetime.now(timezone.utc), "queue waiting exhausted execution deadline")
    jobs.begin_execution(store.lease)
    check(store.task().execution_deadline == started.execution_deadline, "execution retry extended deadline")
    store.finish("COMPLETED", "The choices were cobalt, copper and silver. We selected cobalt.")
    jobs.complete(store.lease)
    replay = repo.replay(conversation.id, user, request)
    check(replay.assistant_message and "choices" in replay.assistant_message.content, "reply linked by adjacency")
    repo.dispatch_ready()
    active = repo.replay(conversation.id, user, second_request)
    check(active.task is not None and active.state == "running", "queued task not dispatched")
    store = claim(active.task.id)
    async def authority():
        await jobs.authority(store.lease)
    await prepare_turn_context(store, NS(), settings, authority)
    task = store.task()
    history = json.dumps(task.permissions_json["session_context"])
    check("choices were cobalt" in history and "FUTURE SECRET" not in history, "incorrect execution context boundary")
    context = agent_context(task)
    history_repo = ConversationContextRepository(factory)
    search = history_repo.search(context, "cobalt")
    check(search["messages"], "historical message search")
    original = history_repo.resource(context, f"attachment:{first.turn_id}:0")
    check(original["content"] == "The selected option is cobalt.", "earlier attachment unavailable")
    found = history_repo.search(context, "original-decisions")
    check(any(x["kind"] == "attachment" for x in found["resources"]), "attachment not discoverable")
    for ref in (f"message:{third.user_message.id}", f"attachment:{third.turn_id}:0"):
        try:
            history_repo.resource(context, ref)
            raise AssertionError("future queued input leaked")
        except Exception as exc:
            from agentforge.subagents.scope import ScopeDenied
            check(isinstance(exc, ScopeDenied), "unexpected resource failure")
    try:
        repo.turns(conversation.id, other)
        raise AssertionError("foreign queue readable")
    except TaskNotFound:
        pass
    repo.queue_command(conversation.id, user, "stop", task_id=active.task.id)
    store.finish("CANCELLED", "Stopped.")
    jobs.complete(store.lease)
    repo.dispatch_ready()
    check(repo.get(conversation.id, user).queue_paused and repo.replay(conversation.id, user, third_request).task is None, "Stop drained queue")
    repo.queue_command(conversation.id, user, "resume")
    repo.dispatch_ready()
    third_active = repo.replay(conversation.id, user, third_request)
    check(third_active.task is not None, "Resume did not dispatch")
    repo.queue_command(conversation.id, user, "stop", task_id=active.task.id)
    with factory() as db:
        check(db.get(Task, third_active.task.id).cancel_requested_at is None, "stale Stop cancelled successor")
    store = claim(third_active.task.id)
    store.finish("COMPLETED", "Third completed.")
    jobs.complete(store.lease)
    print("PASS creation/turn races, FIFO, turn-linked receipts, Stop/Resume, stale Stop and frozen history boundaries", flush=True)

    long = repo.create(user, SessionCreate(title="Long history"))
    for i in range(25):
        turn = repo.enqueue(long.id, user, AutoTurnCreate(routing="auto", idempotency_key=uuid4(),
            goal="Keep all output in Hindi." if i == 0 else f"Follow-up number {i}."))
        current = claim(turn.task.id)
        current.finish("COMPLETED", f"Response {i}." + (" Selected cobalt." if i == 0 else ""))
        jobs.complete(current.lease)
    target_request = AutoTurnCreate(routing="auto", idempotency_key=uuid4(), goal="Continue using the earlier language and option.")
    target = repo.enqueue(long.id, user, target_request)
    store = claim(target.task.id)
    class SummaryModel:
        model_id = "fixture/summary"
        def maximum_request_cost(self, messages, tools): return Decimal("0.001")
        async def complete(self, messages, tools, **kwargs):
            body = json.loads(messages[1]["content"])
            first_message = body["turns"][0]["messages"][0]
            return ModelResponse({"role": "assistant", "tool_calls": [{"id": "summary", "type": "function",
                "function": {"name": "submit_summary", "arguments": json.dumps({"entries": [{"kind": "constraint",
                    "text": first_message["text"], "source_ids": [first_message["resource_id"]]}]})}}]},
                ModelUsage(100, 20, Decimal("0.0001")), self.model_id, "tool_calls")
    model = SummaryModel()
    runtime = NS(service=NS(models=NS(choose=lambda _: model)))
    await prepare_turn_context(store, runtime, settings, authority)
    digest = store.task().permissions_json["session_context"]
    check("Hindi" in json.dumps(digest), "early constraint lost after 50 messages")
    check(digest["coverage"]["uncovered_turns"] == 0, "small legacy history was needlessly left uncovered")
    with factory() as db:
        check(db.scalar(select(func.count()).select_from(ConversationSummary)) == 1, "missing summary")
        check(db.scalar(select(func.count()).select_from(ProviderInput)) == 1, "missing exact input audit")
    await prepare_turn_context(store, runtime, settings, authority)
    with factory() as db:
        check(db.scalar(select(func.count()).select_from(SummaryAttempt)) == 1, "summary was repeated")
        for budget in (2000, 7500, 48000):
            check(len(encoded(history_snapshot(db, db.get(Conversation, long.id), budget=budget)).encode()) <= budget,
                "context exceeded encoded allowance")
    check(not reconcile(store, BudgetService(factory)), "completed summary prevented workflow recovery")
    print("PASS long-history summary provenance, measured accounting, immutable snapshot reuse and bounded legacy catch-up", flush=True)
    store.finish("COMPLETED", "Long conversation complete.")
    jobs.complete(store.lease)

    invalid_target = repo.enqueue(long.id, user, AutoTurnCreate(routing="auto", idempotency_key=uuid4(), goal="Continue again."))
    store = claim(invalid_target.task.id)
    class InvalidSummaryModel(SummaryModel):
        async def complete(self, messages, tools, **kwargs):
            response = await super().complete(messages, tools, **kwargs)
            payload = json.loads(response.message["tool_calls"][0]["function"]["arguments"])
            payload["entries"][0]["source_ids"] = ["message:" + str(uuid4())]
            response.message["tool_calls"][0]["function"]["arguments"] = json.dumps(payload)
            return response
    invalid = InvalidSummaryModel()
    await prepare_turn_context(store, NS(service=NS(models=NS(choose=lambda _: invalid))), settings, authority)
    with factory() as db:
        check(db.scalar(select(func.count()).select_from(ConversationSummary)) == 1, "invented summary sources were accepted")
        check(db.scalar(select(SummaryAttempt.status).where(SummaryAttempt.task_id == store.lease.task_id)) == "failed", "summary failure not durable")
    check("Hindi" in json.dumps(store.task().permissions_json["session_context"]), "failed summary erased earlier decisions")
    store.finish("COMPLETED", "Continued from the retained summary.")
    jobs.complete(store.lease)
    print("PASS invalid summary rejected without losing previous memory or retrying paid work", flush=True)

    capped = repo.create(user, SessionCreate(title="Capacity"))
    head = repo.enqueue(capped.id, user, AutoTurnCreate(routing="auto", idempotency_key=uuid4(), goal="Head"))
    for i in range(20):
        queued = repo.enqueue(capped.id, user, AutoTurnCreate(routing="auto", idempotency_key=uuid4(), goal=f"queued {i}"))
    try:
        repo.enqueue(capped.id, user, AutoTurnCreate(routing="auto", idempotency_key=uuid4(), goal="overflow"))
        raise AssertionError("queue overflow accepted")
    except TaskConflict:
        pass
    repo.queue_command(capped.id, user, "cancel", turn_id=queued.turn_id)
    check(repo.get(capped.id, user).queued_count == 19, "waiting cancellation lost queue count")
    current = claim(head.task.id)
    current.finish("PARTIAL", "Unknown remote outcome.", error_code="UNKNOWN_REMOTE_OUTCOME")
    jobs.complete(current.lease)
    check(repo.get(capped.id, user).queue_paused, "ambiguous effect did not pause queue")
    print("PASS bounded intake, waiting cancellation and fail-closed ambiguous effects", flush=True)


try:
    source = make_url(Settings().database_url)
    admin = admin_engine(source)
    with admin.connect() as connection:
        owner = connection.dialect.identifier_preparer.quote_identifier(source.username)
        connection.execute(text('CREATE DATABASE "' + name + '" OWNER ' + owner))
    created = True
    settings = Settings(_env_file=None, database_url=source.set(database=name).render_as_string(hide_password=False),
        app_env="test", auth_mode="disabled", llm_api_key=None, tavily_api_key=None, neatlogs_api_key=None)
    with patch.object(settings_module, "get_settings", return_value=settings):
        asyncio.run(run(settings))
except BaseException as exc:
    import traceback
    print("FAIL " + type(exc).__name__, flush=True)
    if isinstance(exc, AssertionError): print(str(exc), flush=True)
    for frame in traceback.extract_tb(exc.__traceback__):
        print(f"LOCATION {Path(frame.filename).name}:{frame.lineno} {frame.name}", flush=True)
    raise SystemExit(1) from None
finally:
    if engine is not None: engine.dispose()
    if created:
        with admin.connect() as connection:
            connection.execute(text('DROP DATABASE "' + name + '" WITH (FORCE)'))
        print("PASS disposable database removed", flush=True)
    if admin is not None: admin.dispose()
