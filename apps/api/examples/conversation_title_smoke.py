"""Disposable PostgreSQL checks for generated conversation titles. Fixture models only; no live providers."""
import asyncio
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
from uuid import uuid4
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from _disposable_db import admin_engine
from agentforge.settings import Settings
import agentforge.settings as settings_module

ROOT = Path(__file__).resolve().parents[3]
name = "agentforge_title_" + uuid4().hex[:12]
admin = engine = None
created = False


def check(condition, reason):
    if not condition:
        raise AssertionError(reason)


async def run(settings):
    global engine
    from alembic import command
    from alembic.config import Config
    from agentforge.database.models import BudgetOperation, BudgetReservation, Project, TaskEvent, User
    from agentforge.database.sessions import ConversationChange
    from agentforge.contracts.sessions import AutoTurnCreate, SessionCreate, SessionPatch
    from agentforge.llm.models import ModelProviderError, ModelResponse, ModelUsage
    from agentforge.orchestration.store import WorkflowStore
    from agentforge.repositories.jobs import JobRepository, TaskCancelled
    from agentforge.repositories.sessions import SessionRepository
    from agentforge.subagents.budget import BudgetService
    from agentforge.workers import conversation_title
    from agentforge.workers.conversation_title import OUTPUT_TOKENS, name_conversation, title_run_id
    from agentforge.workers.reconciliation import reconcile
    cfg = Config(str(ROOT / "apps/api/alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "apps/api/alembic"))
    command.upgrade(cfg, "head")
    engine = create_engine(settings.database_url)
    factory = sessionmaker(engine, expire_on_commit=False)
    user = uuid4()
    with factory.begin() as db:
        db.add(User(id=user, github_user_id=-561, display_name="Title fixture"))
        db.flush()
        db.add(Project(id=uuid4(), owner_id=user, name="Title fixture"))
    repo = SessionRepository(factory, context_budget=48000)
    jobs = JobRepository(factory)
    budgets = BudgetService(factory)
    on = NS(conversation_titles_enabled=True)

    class TitleModel:
        """Replies with `reply`; `during` runs inside the call, as another request would."""
        model_id = "fixture/title"

        def __init__(self, reply="Fix flaky login test", finish="stop", cost="0.0003", bound="0.0005", raises=None, delay=0, during=None):
            self.reply, self.finish, self.cost, self.bound = reply, finish, Decimal(cost), Decimal(bound)
            self.raises, self.delay, self.during = raises, delay, during
            self.calls, self.limit = [], None

        def with_output_limit(self, tokens):
            self.limit = tokens
            return self

        def maximum_request_cost(self, messages, tools):
            return self.bound

        async def complete(self, messages, tools, **kwargs):
            self.calls.append((messages, tools, kwargs))
            if self.during:
                self.during()
            if self.delay:
                await asyncio.sleep(self.delay)
            if self.raises:
                raise self.raises
            return ModelResponse({"role": "assistant", "content": self.reply}, ModelUsage(210, 9, self.cost), self.model_id, self.finish)

    def runtime_for(model):
        return NS(models=NS(choose=lambda _: model))

    def claim(target):
        while True:
            lease = jobs.claim("fixture-worker")
            check(lease is not None, "missing worker job")
            if lease.task_id == target:
                return WorkflowStore(factory, lease)
            jobs.complete(lease)  # an earlier fixture task that was finished elsewhere

    def start(conversation, goal):
        """Admit a message and take its task, as the worker does just before the reply."""
        turn = repo.enqueue(conversation, user, AutoTurnCreate(routing="auto", idempotency_key=uuid4(), goal=goal))
        check(turn.task is not None, "turn was not dispatched")
        return claim(turn.task.id)

    def finish(store):
        store.finish("COMPLETED", "Done.")
        jobs.complete(store.lease)

    def title_of(conversation):
        return repo.get(conversation, user).title

    def fresh(title=None):
        return repo.create(user, SessionCreate(title=title) if title else SessionCreate()).id

    # 1. A generated title replaces the first-message title, is accounted, and leaves no trace in the reply's activity.
    goal = "hey can you help me debug why my flaky login test keeps failing on CI but passes locally"
    chat = fresh()
    store = start(chat, goal)
    check(title_of(chat) == goal, "the first message is the provisional title")
    revision = repo.get(chat, user).revision
    model = TitleModel()
    await name_conversation(store, runtime_for(model), on)
    check(title_of(chat) == "Fix flaky login test", "generated title not applied")
    check(model.limit == OUTPUT_TOKENS and len(model.calls) == 1 and model.calls[0][1] == [], "small tool-free request")
    check(goal in model.calls[0][0][1]["content"] and model.calls[0][0][0]["role"] == "developer", "title request carries the message")
    check(repo.get(chat, user).revision == revision + 1, "revision not bumped")
    with factory() as db:
        change = db.scalar(select(ConversationChange).where(ConversationChange.session_id == chat).order_by(ConversationChange.sequence.desc()))
        check(change.event_type == "conversation.title.updated", "open clients are not told")
        reservation = db.scalar(select(BudgetReservation).where(BudgetReservation.run_id == title_run_id(store.task().id)))
        check(reservation is not None and reservation.settled_at is not None and reservation.charged_usd == Decimal("0.0003"), "title spend not settled")
        operation = db.scalar(select(BudgetOperation).where(BudgetOperation.reservation_id == reservation.id))
        check(operation.operation_key == f"title:{store.task().id}" and operation.cost_usd == Decimal("0.0003")
            and operation.usage_json["provider_usage"]["purpose"] == "title", "title operation not recorded")
        events = {e.event_type for e in db.scalars(select(TaskEvent).where(TaskEvent.task_id == store.task().id))}
        check(not events & {"model.started", "model.finished"}, "title call leaked into the reply's activity")
        check(repo.last_run_context(db, chat) is None, "title call counted as the reply's context")
    await name_conversation(store, runtime_for(model), on)
    check(len(model.calls) == 1, "a task made its title call twice")
    finish(store)
    print("PASS generated title applied once, accounted, announced and kept out of the reply's activity", flush=True)

    # 2. A name the person chose is never overwritten, before or during the call.
    chat = fresh("Quarterly planning")
    store = start(chat, "Plan the quarterly roadmap for the platform team")
    model = TitleModel()
    await name_conversation(store, runtime_for(model), on)
    check(title_of(chat) == "Quarterly planning" and not model.calls, "a title given on creation was replaced")
    finish(store)
    chat = fresh()
    store = start(chat, "Draft a launch announcement for the new billing page")
    model = TitleModel("Billing launch announcement", during=lambda: repo.patch(chat, user, SessionPatch(title="My launch notes")))
    await name_conversation(store, runtime_for(model), on)
    check(title_of(chat) == "My launch notes", "a rename during generation was overwritten")
    with factory() as db:
        check(db.scalar(select(BudgetReservation).where(BudgetReservation.run_id == title_run_id(store.task().id))).settled_at is not None, "raced call unsettled")
    finish(store)
    store = start(chat, "Add a second paragraph about pricing")
    model = TitleModel()
    await name_conversation(store, runtime_for(model), on)
    check(title_of(chat) == "My launch notes" and not model.calls, "a renamed conversation was renamed again")
    finish(store)
    print("PASS creation-time and renamed titles are never replaced, including by a racing generation", flush=True)

    # 3. A greeting names nothing; the next of the first three messages tries again, and later messages never do.
    chat = fresh()
    store = start(chat, "hey")
    model = TitleModel("NONE")
    await name_conversation(store, runtime_for(model), on)
    check(title_of(chat) == "hey", "a greeting was given a title")
    finish(store)
    store = start(chat, "Write a cover letter for a nursing job in Leeds")
    model = TitleModel("Nursing cover letter")
    await name_conversation(store, runtime_for(model), on)
    check(title_of(chat) == "Nursing cover letter", "the follow-up did not name the conversation")
    check('"n":2' in model.calls[0][0][1]["content"].replace(" ", "") and "hey" in model.calls[0][0][1]["content"], "both opening messages are read")
    finish(store)
    chat = fresh()
    silent = TitleModel("NONE")
    for greeting in ("hi", "hello", "thanks"):
        store = start(chat, greeting)
        await name_conversation(store, runtime_for(silent), on)
        finish(store)
    store = start(chat, "Explain how database indexes work")
    await name_conversation(store, runtime_for(silent), on)
    check(len(silent.calls) == 3 and title_of(chat) == "hi", "a fourth message tried to name the conversation")
    finish(store)
    print("PASS greetings retry within the first three messages and never after", flush=True)

    # 4. Failures keep the title, never fail the reply, and leave the ledger free for it.
    scenarios = {
        "provider error": TitleModel(raises=ModelProviderError("down", code="RATE_LIMIT")),
        "malformed": TitleModel(raises=ValueError("boom")),
        "cut short": TitleModel("Fix flaky login", finish="length"),
        "empty": TitleModel(""),
        "too expensive": TitleModel(bound="1.0"),
    }
    for label, model in scenarios.items():
        chat = fresh()
        store = start(chat, f"Summarise the {label} design document")
        await name_conversation(store, runtime_for(model), on)
        check(title_of(chat) == f"Summarise the {label} design document", f"{label}: the title changed")
        with factory() as db:
            held = db.scalar(select(BudgetReservation).where(BudgetReservation.task_id == store.task().id, BudgetReservation.settled_at.is_(None)))
        check(held is None, f"{label}: left a reservation that would block the reply")
        spare = budgets.reserve(task_id=store.task().id, run_id=uuid4(), amount_usd=Decimal("0.05"), owner_id=user,
            project_id=store.task().project_id, max_steps=1, max_tool_calls=1, max_wall_time_sec=30)
        budgets.settle(spare.id)
        finish(store)
    with factory() as db:
        errored = db.scalar(select(BudgetOperation).where(BudgetOperation.operation_key.like("title:%"), BudgetOperation.cost_usd == Decimal("0.0005")))
        check(errored is not None, "an unknown outcome was not charged at its upper bound")
    chat = fresh()
    store = start(chat, "Translate this paragraph into French")
    slow = TitleModel(delay=1)
    with patch.object(conversation_title, "TIMEOUT_SEC", 0.05):
        await name_conversation(store, runtime_for(slow), on)
    check(title_of(chat) == "Translate this paragraph into French", "a slow title changed the conversation")
    with factory() as db:
        check(db.scalar(select(BudgetReservation).where(BudgetReservation.task_id == store.task().id, BudgetReservation.settled_at.is_(None))) is None, "timeout left a reservation")
    finish(store)
    chat = fresh()
    store = start(chat, "Compare two sorting algorithms")
    model = TitleModel()
    await name_conversation(store, runtime_for(model), NS(conversation_titles_enabled=False))
    check(not model.calls and title_of(chat) == "Compare two sorting algorithms", "disabled titles still ran")
    finish(store)
    print("PASS failures, timeouts, cost bound and the off switch keep the title and free the ledger", flush=True)

    # 5. Stop is honoured before any paid call.
    chat = fresh()
    store = start(chat, "Research the history of the printing press")
    repo.queue_command(chat, user, "stop", task_id=store.task().id)
    model = TitleModel()
    try:
        await name_conversation(store, runtime_for(model), on)
        raise AssertionError("a stopped task made a title call")
    except TaskCancelled:
        pass
    check(not model.calls, "paid call after Stop")
    store.finish("CANCELLED", "Stopped.")
    jobs.complete(store.lease)
    print("PASS Stop is raised, not swallowed, before any paid title call", flush=True)

    # 6. A worker that died mid-title does not fail the reply or repeat the call; real ambiguity still does.
    chat = fresh()
    store = start(chat, "Explain the difference between TCP and UDP")
    task = store.task()
    interrupted = budgets.reserve(task_id=task.id, run_id=title_run_id(task.id), amount_usd=Decimal("0.01"), owner_id=user,
        project_id=task.project_id, max_steps=1, max_tool_calls=1, max_wall_time_sec=30)
    budgets.authorize_operation(interrupted.id, f"title:{task.id}", Decimal("0.0005"))
    check(reconcile(store, budgets) is False, "an interrupted title call failed the turn")
    with factory() as db:
        check(db.get(BudgetReservation, interrupted.id).settled_at is not None, "interrupted title reservation left open")
        check(db.scalar(select(BudgetOperation.cost_usd).where(BudgetOperation.reservation_id == interrupted.id)) == Decimal("0.0005"), "interrupted call not charged")
    model = TitleModel()
    await name_conversation(store, runtime_for(model), on)
    check(not model.calls and title_of(chat).startswith("Explain the difference"), "an interrupted title call was repeated")
    other_chat = fresh()
    other = start(other_chat, "Explain what a mutex is")
    stranger = budgets.reserve(task_id=other.task().id, run_id=uuid4(), amount_usd=Decimal("0.01"), owner_id=user,
        project_id=other.task().project_id, max_steps=1, max_tool_calls=1, max_wall_time_sec=30)
    budgets.authorize_operation(stranger.id, "model:unknown", Decimal("0.0005"))
    check(reconcile(other, budgets) is True, "an unknown abandoned child is no longer ambiguous")
    print("PASS an interrupted title call is charged once, never repeated and cannot fail a reply; other ambiguity still fails closed", flush=True)

    # 7. Through the real worker: the title lands before the agent starts, and a failing title never stops the run.
    from agentforge.workers import runner
    telemetry = NS(callbacks=lambda: [], decorate=lambda function, **_: function)
    worker_settings = settings.model_copy(update={"conversation_titles_enabled": True, "worker_heartbeat_sec": 10})

    async def worker_turn(model, goal):
        chat = fresh()
        turn = repo.enqueue(chat, user, AutoTurnCreate(routing="auto", idempotency_key=uuid4(), goal=goal))
        lease = claim(turn.task.id).lease
        seen = {}

        async def close():
            return None

        async def agent(*, store, service, authority, config):
            seen["title_when_agent_started"] = title_of(chat)
            store.finish("COMPLETED", "Here is the answer.")

        with patch.object(runner, "compose_runtime", lambda *_: NS(service=NS(), models=NS(choose=lambda _: model), close=close)), \
                patch.object(runner, "run_agent_turn", agent):
            await runner.execute(worker_settings, factory, telemetry, jobs, lease)
        with factory() as db:
            from agentforge.database.models import Task
            return chat, seen, db.get(Task, turn.task.id).status

    chat, seen, status = await worker_turn(TitleModel("Debug failing CI pipeline"), "my CI pipeline fails on the lint step and I cannot see why")
    check(status == "COMPLETED" and seen["title_when_agent_started"] == "Debug failing CI pipeline", "title was not in place before the agent started")
    check(title_of(chat) == "Debug failing CI pipeline", "title lost after the run")
    goal = "please convert this CSV export into a tidy JSON file"
    chat, seen, status = await worker_turn(TitleModel(raises=ModelProviderError("down", code="MODEL_PROVIDER_ERROR")), goal)
    check(status == "COMPLETED" and seen["title_when_agent_started"] == goal and title_of(chat) == goal, "a failing title stopped or changed the run")
    print("PASS the worker names the conversation before the agent starts; a failing title never stops the run", flush=True)


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
