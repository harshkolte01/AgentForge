"""Disposable PostgreSQL checks for the sidebar status of conversations (running, waiting for approval, failed, unread)."""
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4
from sqlalchemy import create_engine, event, inspect, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from _disposable_db import admin_engine
from agentforge.settings import Settings
import agentforge.settings as settings_module

ROOT = Path(__file__).resolve().parents[3]
name = "agentforge_status_" + uuid4().hex[:12]
admin = engine = None
created = False


def check(condition, reason):
    if not condition:
        raise AssertionError(reason)


async def run(settings):
    global engine
    from alembic import command
    from alembic.autogenerate import compare_metadata
    from alembic.config import Config
    from alembic.migration import MigrationContext
    from agentforge.database.models import Base, Project, User
    from agentforge.database.sessions import Conversation, TurnExecution, ToolApproval
    from agentforge.contracts.sessions import AutoTurnCreate, SessionCreate
    from agentforge.orchestration.store import WorkflowStore
    from agentforge.repositories.jobs import JobRepository
    from agentforge.repositories.sessions import SessionRepository
    from agentforge.repositories.tasks import TaskNotFound
    cfg = Config(str(ROOT / "apps/api/alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "apps/api/alembic"))
    command.upgrade(cfg, "0012_agent_sandbox_approvals")
    engine = create_engine(settings.database_url)
    factory = sessionmaker(engine, expire_on_commit=False)

    # 0. The migration: conversations that exist already count as seen, nothing is reordered, and it reverses cleanly.
    legacy_user, legacy_project, legacy_chat = uuid4(), uuid4(), uuid4()
    with engine.begin() as db:
        db.execute(text("INSERT INTO users(id,github_user_id,display_name) VALUES (:id,-571,'Legacy')"), {"id": legacy_user})
        db.execute(text("INSERT INTO projects(id,owner_id,name) VALUES (:id,:owner,'Legacy')"), {"id": legacy_project, "owner": legacy_user})
        db.execute(text("INSERT INTO conversations(id,owner_id,project_id,title,mode,last_sequence,updated_at) VALUES (:id,:owner,:project,'Legacy','general',6,'2026-01-02T03:04:05Z')"),
            {"id": legacy_chat, "owner": legacy_user, "project": legacy_project})
    for attempt in ("upgrade", "undo and upgrade again"):
        command.upgrade(cfg, "head")
        with engine.connect() as db:
            read, updated = db.execute(text("SELECT read_sequence, updated_at FROM conversations WHERE id=:id"), {"id": legacy_chat}).one()
        check(read == 6 and updated == datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc), f"{attempt}: existing conversations were not marked as seen, or were reordered")
        if attempt == "upgrade":
            command.downgrade(cfg, "0012_agent_sandbox_approvals")
            with engine.connect() as db:
                check("read_sequence" not in {c["name"] for c in inspect(db).get_columns("conversations")}, "downgrade left the column")
    with engine.connect() as connection:
        check(not compare_metadata(MigrationContext.configure(connection, opts={"compare_type": True, "compare_server_default": True}), Base.metadata), "migration drift")
    print("PASS migration backfills existing conversations as seen without reordering them, and reverses cleanly", flush=True)

    user, other = uuid4(), uuid4()
    with factory.begin() as db:
        db.add_all([User(id=user, github_user_id=-572, display_name="Status fixture"), User(id=other, github_user_id=-573, display_name="Other")])
        db.flush()
        db.add_all([Project(id=uuid4(), owner_id=user, name="Status fixture"), Project(id=uuid4(), owner_id=other, name="Other")])
    repo = SessionRepository(factory, context_budget=48000)
    jobs = JobRepository(factory)

    def claim(target):
        while True:
            lease = jobs.claim("fixture-worker")
            check(lease is not None, "missing worker job")
            if lease.task_id == target:
                return WorkflowStore(factory, lease)
            jobs.complete(lease)

    def start(chat, goal="Do the thing"):
        turn = repo.enqueue(chat, user, AutoTurnCreate(routing="auto", idempotency_key=uuid4(), goal=goal))
        check(turn.task is not None, "turn was not dispatched")
        return claim(turn.task.id)

    def end(store, status="COMPLETED", **kwargs):
        store.finish(status, "Done." if status != "FAILED" else "It broke.", **kwargs)
        jobs.complete(store.lease)

    def state(chat):
        single = repo.get(chat, user).status
        listed = next(item.status for item in repo.list(user) if item.id == chat)
        check(single == listed, f"get says {single} but list says {listed}")
        return single

    def fresh():
        return repo.create(user, SessionCreate()).id

    # 1. Nothing has happened yet.
    chat = fresh()
    check(state(chat) == "idle" and repo.get(chat, user).read_sequence == 0, "a new conversation is not idle")

    # 2. Running, then waiting for approval, then running again once the card is answered.
    store = start(chat)
    check(state(chat) == "running", "an active task is not running")
    with factory.begin() as db:
        db.add(ToolApproval(task_id=store.task().id, user_id=user, operation_key="op1", tool="bash", summary="ls"))
    check(state(chat) == "waiting_approval", "a pending approval is not shown")
    with factory.begin() as db:
        db.execute(update(ToolApproval).where(ToolApproval.task_id == store.task().id).values(state="APPROVED"))
    check(state(chat) == "running", "an answered approval still shows as waiting")
    with factory.begin() as db:
        db.execute(update(ToolApproval).where(ToolApproval.task_id == store.task().id).values(state="PENDING"))
    other_chat = fresh()
    other_store = start(other_chat)
    check(state(other_chat) == "running", "another conversation's approval leaked")
    end(other_store, "CANCELLED", error_code="USER_CANCELLED")
    print("PASS running and waiting-for-approval follow the active task and its pending approval only", flush=True)

    # 3. A finished reply is unread until seen; seeing moves forward only, never past the last message, never reorders.
    end(store)
    check(state(chat) == "unread", "a finished reply is not unread")
    before = repo.get(chat, user)
    order_before = [item.id for item in repo.list(user)]
    check(before.last_sequence == 2 and len(order_before) >= 2, "fixture expectation")
    check(repo.mark_read(chat, user, 1).status == "unread", "reading the question cleared the unread reply")
    seen = repo.mark_read(chat, user, 2)
    check(seen.status == "idle" and seen.read_sequence == 2, "reading the reply did not clear it")
    check(repo.mark_read(chat, user, 0).read_sequence == 2, "read position moved backwards")
    check(repo.mark_read(chat, user, 10_000).read_sequence == 2, "read position passed the last message")
    after = repo.get(chat, user)
    check((after.updated_at, after.revision) == (before.updated_at, before.revision), "marking read reordered or re-versioned the conversation")
    check([item.id for item in repo.list(user)] == order_before, "marking read changed the order of the list")
    print("PASS unread until seen; the read position only moves forward, is clamped, and never reorders the sidebar", flush=True)

    # 4. Failed shows until seen; a Stop shows nothing; a partial reply is unread like any other.
    store = start(chat, "Try something hard")
    end(store, "FAILED", error_code="CHILD_RUNTIME_ERROR")
    check(state(chat) == "failed", "a failed reply is not shown")
    repo.mark_read(chat, user, repo.get(chat, user).last_sequence)
    check(state(chat) == "idle", "a failed reply stays after being seen")
    store = start(chat, "Try again")
    repo.queue_command(chat, user, "stop", task_id=store.task().id)
    end(store, "CANCELLED", error_code="USER_CANCELLED")
    check(state(chat) == "idle", "the owner's own Stop was flagged")
    store = start(chat, "One more")
    end(store, "PARTIAL")
    check(state(chat) == "unread", "a partial reply is not unread")
    print("PASS failed shows until seen, a Stop shows nothing, partial replies are unread", flush=True)

    # 5. The gap between one message ending and the next starting is still "running"; a message that cannot start is "failed".
    chat = fresh()
    first = start(chat, "First")
    repo.enqueue(chat, user, AutoTurnCreate(routing="auto", idempotency_key=uuid4(), goal="Second"))
    end(first)
    check(repo.get(chat, user).active_task_id is None and state(chat) == "running", "the hand-off to the next message flickers")
    with factory.begin() as db:
        db.execute(update(TurnExecution).where(TurnExecution.session_id == chat, TurnExecution.state == "queued").values(state="blocked", error_code="repository_unavailable"))
        db.execute(update(Conversation).where(Conversation.id == chat).values(queue_paused=True))
    check(state(chat) == "failed" and repo.get(chat, user).queued_count == 1, "a message that could not start is not failed")
    repo.mark_read(chat, user, repo.get(chat, user).last_sequence)
    check(state(chat) == "failed", "a blocked queue stopped needing attention after being seen")
    repo.queue_command(chat, user, "resume")
    check(state(chat) == "running", "resuming did not show the work as running")
    chat = fresh()
    first = start(chat, "Long one")
    repo.enqueue(chat, user, AutoTurnCreate(routing="auto", idempotency_key=uuid4(), goal="Waiting behind it"))
    repo.queue_command(chat, user, "stop", task_id=first.task().id)
    end(first, "CANCELLED", error_code="USER_CANCELLED")
    check(repo.get(chat, user).queue_paused and state(chat) == "idle", "a queue the owner paused with Stop was flagged")
    print("PASS the hand-off between messages stays running; a message that cannot start is failed until resolved", flush=True)

    # 6. Another account can neither read nor mark this conversation, and sees none of it.
    try:
        repo.mark_read(chat, other, 1)
        raise AssertionError("a foreign account marked a conversation read")
    except TaskNotFound:
        pass
    check(not repo.list(other), "a foreign account lists this conversation")
    print("PASS marking read is owner-only", flush=True)

    # 7. The cost of the list does not grow with the number of conversations.
    statements = []
    event.listen(engine, "before_cursor_execute", lambda *args: statements.append(args[2]))
    for count in (1, 40):
        while len(repo.list(user, limit=100)) < count + 3:
            fresh()
        statements.clear()
        listed = repo.list(user, limit=100)
        counts_seen = len(statements)
        check(len(listed) >= count, "fixture expectation")
        if count == 1:
            baseline = counts_seen
    check(counts_seen == baseline, f"list issued {counts_seen} statements for many conversations but {baseline} for few")
    check(counts_seen <= 6, f"list issued {counts_seen} statements")
    print(f"PASS listing {len(listed)} conversations takes {counts_seen} statements, the same as listing a few", flush=True)


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
