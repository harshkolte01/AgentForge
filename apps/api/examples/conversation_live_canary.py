"""Explicit paid conversation qualification; disposable DB, no search or sandbox.

Run only intentionally: python examples/conversation_live_canary.py --run
The configured model is called for real. Runs are not limited, so the script itself checks that the canary stayed cheap.
"""
import argparse
import asyncio
from decimal import Decimal
from pathlib import Path
import re
from unittest.mock import patch
from uuid import uuid4
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from _disposable_db import admin_engine
from agentforge.settings import Settings
import agentforge.settings as settings_module

ROOT = Path(__file__).resolve().parents[3]


async def qualify(settings, engine):
    from agentforge.database.models import BudgetOperation, Project, Task, ToolCall, User
    from agentforge.contracts.sessions import AutoTurnCreate, SessionCreate
    from agentforge.repositories.sessions import SessionRepository
    from agentforge.repositories.jobs import JobRepository
    from agentforge.orchestration.store import WorkflowStore
    from agentforge.workers.conversation_context import prepare_turn_context
    from agentforge.orchestration.agent_turn import run_agent_turn
    from agentforge.bootstrap import compose_runtime
    from agentforge.telemetry.neatlogs_setup import Telemetry
    sessions = sessionmaker(engine, expire_on_commit=False)
    owner, project = uuid4(), uuid4()
    with sessions.begin() as db:
        db.add(User(id=owner, github_user_id=-991, display_name="Qualification fixture"))
        db.flush()
        db.add(Project(id=project, owner_id=owner, name="Qualification"))
    repo, jobs = SessionRepository(sessions, context_budget=48000), JobRepository(sessions)
    conversation = repo.create(owner, SessionCreate(title="Live recall qualification"))
    for index in range(26):
        goal = ("Choose cobalt. Answer follow-ups in Hindi." if index == 0 else
            "Correction: replace cobalt with copper as the final selected option." if index == 18 else f"Record progress note {index}.")
        turn = repo.enqueue(conversation.id, owner, AutoTurnCreate(routing="auto", idempotency_key=uuid4(), goal=goal,
            attachments=[{"name": "original-decisions.txt", "content": "The release code is AF-COPPER-731."}] if index == 0 else []))
        lease = jobs.claim("qualification-fixture")
        store = WorkflowStore(sessions, lease)
        store.finish("COMPLETED", "Acknowledged the user's decision." if index in {0, 18} else f"Recorded note {index}.")
        jobs.complete(lease)
    request = AutoTurnCreate(routing="auto", idempotency_key=uuid4(),
        goal="Read the earlier attached original-decisions.txt and tell me its exact release code and our final chosen option. Reply in Hindi in one short sentence; check earlier corrections.")
    target = repo.enqueue(conversation.id, owner, request)
    lease = jobs.claim("qualification-live")
    store = WorkflowStore(sessions, lease)
    runtime = compose_runtime(settings, sessions, Telemetry(settings), store)
    async def authority():
        await jobs.authority(lease)
    try:
        async with asyncio.timeout(180):
            await prepare_turn_context(store, runtime, settings, authority)
            await run_agent_turn(store=store, service=runtime.service, authority=authority,
                config={"configurable": {"thread_id": str(target.task.id)}})
        task = store.task()
        answer = task.final_summary or ""
        with sessions() as db:
            tools = set(db.scalars(select(ToolCall.tool_name).where(ToolCall.task_id == task.id, ToolCall.status == "SUCCEEDED")))
            charged = db.scalar(select(func.coalesce(func.sum(BudgetOperation.cost_usd), 0)))
        assert task.status == "COMPLETED", "model did not complete the conversational canary"
        assert "AF-COPPER-731" in answer, "old attachment release code not recovered"
        assert re.search(r"copper|कॉपर|तांब|कॉप", answer, re.I), "later corrected choice not retained"
        assert not re.search(r"cobalt|कोबाल्ट", answer, re.I), "superseded choice returned"
        assert re.search(r"[\u0900-\u097f]", answer), "earlier language constraint lost"
        assert "read_context_resource" in tools, "model did not read the stored original"
        assert charged <= Decimal("0.50"), "the canary spent more than expected"
        print(f"PASS live recall, correction, Hindi constraint and earlier attachment retrieval; charged USD {charged}", flush=True)
    finally:
        await runtime.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="Make paid calls using the configured model credentials.")
    if not parser.parse_args().run:
        parser.print_help()
        return
    configured = Settings()
    source = make_url(configured.database_url)
    admin = admin_engine(source)
    name = "agentforge_live_recall_" + uuid4().hex[:12]
    engine = None
    created = False
    try:
        with admin.connect() as db:
            db.execute(text('CREATE DATABASE "' + name + '" OWNER ' + db.dialect.identifier_preparer.quote_identifier(source.username)))
        created = True
        settings = configured.model_copy(update={"database_url": source.set(database=name).render_as_string(hide_password=False),
            "app_env": "test", "auth_mode": "disabled", "tavily_api_key": None, "e2b_api_key": None,
            "neatlogs_api_key": None})
        with patch.object(settings_module, "get_settings", return_value=settings):
            from alembic import command
            from alembic.config import Config
            cfg = Config(str(ROOT / "apps/api/alembic.ini"))
            cfg.set_main_option("script_location", str(ROOT / "apps/api/alembic"))
            command.upgrade(cfg, "head")
            engine = create_engine(settings.database_url)
            asyncio.run(qualify(settings, engine))
    except BaseException as exc:
        print("FAIL " + type(exc).__name__, flush=True)
        if isinstance(exc, AssertionError): print(str(exc), flush=True)
        # Never expose provider errors, SQL parameters, or connection strings.
        raise SystemExit(1) from None
    finally:
        if engine: engine.dispose()
        if created:
            with admin.connect() as db: db.execute(text('DROP DATABASE "' + name + '" WITH (FORCE)'))
            print("PASS disposable database removed", flush=True)
        admin.dispose()


if __name__ == "__main__":
    main()
