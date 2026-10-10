"""Phase 8 live reply streaming on a disposable PostgreSQL database: migration 0009, SQL, epoch fencing and the SSE endpoint.

Run from apps/api: uv run python examples/phase8_streaming_smoke.py
"""
import json, signal, sys, threading, time
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4
sys.path.insert(0, str(Path(__file__).resolve().parent))
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from agentforge.settings import Settings
from _disposable_db import admin_engine
import agentforge.settings as settings_module
def _timeout(*a): raise TimeoutError('e2e timed out')
signal.signal(signal.SIGALRM, _timeout); signal.alarm(90)
ROOT = Path(__file__).resolve().parents[3]
name = "agentforge_streaming_smoke_" + uuid4().hex[:10]
admin = engine = None; created = False
def check(v, m):
    if not v: raise AssertionError(m)
try:
    source = make_url(Settings().database_url)
    admin = admin_engine(source)
    with admin.connect() as c:
        c.execute(text('CREATE DATABASE "' + name + '" OWNER ' + c.dialect.identifier_preparer.quote_identifier(source.username)))
    created = True
    settings = Settings(_env_file=None, database_url=source.set(database=name).render_as_string(hide_password=False),
        langgraph_checkpoint_db_url=source.set(drivername="postgresql", database=name).render_as_string(hide_password=False),
        app_env="test", auth_mode="disabled", llm_api_key=None, tavily_api_key=None, neatlogs_api_key=None)
    with patch.object(settings_module, "get_settings", return_value=settings):
        from alembic import command
        from alembic.config import Config
        from alembic.autogenerate import compare_metadata
        from alembic.migration import MigrationContext
        cfg = Config(str(ROOT / "apps/api/alembic.ini")); cfg.set_main_option("script_location", str(ROOT / "apps/api/alembic"))
        command.upgrade(cfg, "head")
        print("PASS migrations through 0009")
        engine = create_engine(settings.database_url)
        with engine.connect() as c:
            drift = compare_metadata(MigrationContext.configure(c, opts={"compare_type": True, "compare_server_default": True}), __import__("agentforge.database.models", fromlist=["Base"]).Base.metadata)
            check(not drift, f"metadata drift: {drift}")
        print("PASS no metadata drift")
        from agentforge.database.models import User, Project, Task
        from agentforge.contracts.sessions import SessionCreate, SessionTurnCreate
        from agentforge.repositories.sessions import SessionRepository
        from agentforge.repositories.events import EventRepository
        from agentforge.orchestration.stream import ReplyStream, clear_stream
        sessions = sessionmaker(engine, expire_on_commit=False)
        user, project = uuid4(), uuid4()
        with sessions.begin() as db:
            db.add(User(id=user, github_user_id=-9, display_name="Fixture")); db.flush()
            db.add(Project(id=project, owner_id=user, name="Fixture"))
        repo = SessionRepository(sessions)
        conversation = repo.create(user, SessionCreate(project_id=project, mode="general"))
        turn = repo.turn(conversation.id, user, SessionTurnCreate(idempotency_key=uuid4(), kind="general", goal="Research a concrete question with evidence."))
        task_id = turn.task.id
        events = EventRepository(sessions)
        check(events.stream_snapshot(task_id, user) is None, "snapshot before any text")
        s1 = ReplyStream(sessions, task_id, "response")
        s1.feed('{"intent":"conversation","response":"Hello'); 
        check(events.stream_snapshot(task_id, user) == (1, "Hello"), f"first {events.stream_snapshot(task_id, user)}")
        time.sleep(0.15); s1.feed(' world"}'); s1.end()
        check(events.stream_snapshot(task_id, user) == (1, "Hello world"), "grew")
        check(events.stream_snapshot(task_id, uuid4()) is None, "foreign owner sees stream")
        s2 = ReplyStream(sessions, task_id, "response")      # a newer attempt takes over
        s2.feed('{"response":"Fresh'); 
        check(events.stream_snapshot(task_id, user) == (2, "Fresh"), "new epoch")
        s1.begin(); s1._epoch = 1; s1._text = "STALE"; s1._write()   # stale worker write is fenced out
        check(events.stream_snapshot(task_id, user) == (2, "Fresh"), "stale writer overwrote the stream")
        print("PASS upsert, growth, owner scoping, epoch fencing")
        # SSE endpoint
        from fastapi.testclient import TestClient
        from agentforge.main import create_app
        from agentforge.auth.session import current_user
        app = create_app(settings); app.state.engine = engine
        with sessions() as db:
            identity = db.get(User, user); db.expunge(identity)
        app.dependency_overrides[current_user] = lambda: identity
        import agentforge.api.routes_events as routes_events
        routes_events.current_user = lambda request: identity   # the stream re-authenticates directly on every pass
        got = []
        def writer():
            time.sleep(1.0)
            w = ReplyStream(sessions, task_id, "response")
            for piece in ('{"intent":"conversation","res', 'ponse":"Stream', 'ing ', 'works ✓"}'):
                w.feed(piece); time.sleep(0.25)
            w.end()
        # fresh stream row so the endpoint sees a clean start
        clear_stream(sessions, task_id)
        threading.Thread(target=writer, daemon=True).start()
        import httpx, uvicorn
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=8765, log_level="error"))
        threading.Thread(target=server.run, daemon=True).start()
        for _ in range(100):
            if server.started: break
            time.sleep(0.1)
        text_so_far = ""
        try:
            with httpx.stream("GET", f"http://127.0.0.1:8765/api/v1/tasks/{task_id}/events", timeout=30) as response:
                check(response.status_code == 200, f"sse status {response.status_code}")
                event = None; deadline = time.time() + 20
                for line in response.iter_lines():
                    if line.startswith("event: "): event = line[7:]
                    elif line.startswith("data: ") and event == "message.delta":
                        d = json.loads(line[6:]); got.append(d)
                        text_so_far = d["text"] if d["offset"] == 0 else text_so_far + d["text"]
                        if text_so_far == "Streaming works ✓": break
                    if time.time() > deadline: break
        finally:
            server.should_exit = True
        check(text_so_far == "Streaming works ✓", f"assembled {text_so_far!r} from {got}")
        check(len(got) >= 2 and got[0]["offset"] == 0 and any(d["offset"] > 0 for d in got), f"deltas were not incremental {got}")
        print("PASS SSE delivered incremental message.delta events:", [(d["epoch"], d["offset"], d["text"]) for d in got])
        s3 = ReplyStream(sessions, task_id, "response"); s3.feed('{"response":"x"}')
        clear_stream(sessions, task_id)
        check(events.stream_snapshot(task_id, user) is None, "cleanup left a row")
        print("PASS cleanup")
except BaseException as exc:
    import traceback; traceback.print_exc(); print("FAIL"); sys.exit(1)
finally:
    if engine is not None: engine.dispose()
    if created:
        with admin.connect() as c: c.execute(text('DROP DATABASE "' + name + '" WITH (FORCE)'))
        print("disposable database removed")
    if admin is not None: admin.dispose()
