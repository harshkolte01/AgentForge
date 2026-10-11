"""Phase 8 live text on a disposable PostgreSQL database: migrations 0009-0010, SQL, epoch and revision fencing, resets,
the stream snapshot endpoint and the SSE endpoint.

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
signal.signal(signal.SIGALRM, _timeout); signal.alarm(120)
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
        print("PASS migrations through 0010")
        engine = create_engine(settings.database_url)
        def columns():
            with engine.connect() as c:
                return {r.column_name: (r.data_type, r.is_nullable, r.column_default) for r in c.execute(text(
                    "SELECT column_name, data_type, is_nullable, column_default FROM information_schema.columns WHERE table_name = 'task_streams'"))}
        cols = columns()
        check(cols["revision"][:2] == ("integer", "NO") and cols["revision"][2] == "0", f"revision column {cols['revision']}")
        check(cols["kind"][:2] == ("text", "NO") and "progress" in cols["kind"][2], f"kind column {cols['kind']}")
        command.downgrade(cfg, "0009_task_streams")
        check("revision" not in columns() and "kind" not in columns() and "epoch" in columns(), f"downgrade left {sorted(columns())}")
        command.upgrade(cfg, "head")
        check("revision" in columns() and "kind" in columns(), "re-upgrade")
        print("PASS migration 0010 is additive and reversible")
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
        with sessions() as db:
            from sqlalchemy.exc import IntegrityError
            try:
                with db.begin():
                    db.execute(text("INSERT INTO task_streams (task_id, epoch, kind) VALUES (:t, 1, 'bogus')"), {"t": task_id})
                check(False, "an unknown kind was accepted")
            except IntegrityError:
                pass
        s1 = ReplyStream(sessions, task_id)
        s1.feed("Hello")
        check(events.stream_snapshot(task_id, user) == (1, 0, "progress", "Hello"), f"first {events.stream_snapshot(task_id, user)}")
        time.sleep(0.15); s1.feed(" world"); s1.end()
        check(events.stream_snapshot(task_id, user) == (1, 0, "progress", "Hello world"), "grew without a new revision")
        check(events.stream_snapshot(task_id, uuid4()) is None, "foreign owner sees stream")
        s2 = ReplyStream(sessions, task_id)                  # a newer attempt takes over
        s2.feed("Fresh")
        check(events.stream_snapshot(task_id, user) == (2, 1, "progress", "Fresh"), f"new epoch {events.stream_snapshot(task_id, user)}")
        s1._text += " STALE"; s1._sync(force=True)       # the stale worker writes late: fenced out by epoch, and stops
        check(events.stream_snapshot(task_id, user) == (2, 1, "progress", "Fresh"), "stale epoch overwrote the stream")
        check(s1.disabled, "a fenced-out stream keeps writing")
        s2.discard()                                     # a clear is an empty reset with a newer revision
        check(events.stream_snapshot(task_id, user) == (2, 2, "progress", ""), f"discard {events.stream_snapshot(task_id, user)}")
        s3 = ReplyStream(sessions, task_id)              # an older revision of the current epoch cannot bring text back
        s3._epoch, s3._revision = 2, 1
        s3._write("RESURRECTED", "answer", 1)
        check(events.stream_snapshot(task_id, user) == (2, 2, "progress", ""), "stale revision resurrected text")
        check(s3.disabled, "a fenced-out revision keeps writing")
        print("PASS upsert, growth, owner scoping, epoch and revision fencing, empty reset")
        snapshot = events.live_snapshot(task_id, user)
        check(snapshot.epoch == 2 and snapshot.revision == 2 and snapshot.text == "" and not snapshot.terminal, f"live_snapshot {snapshot}")
        try:
            events.live_snapshot(task_id, uuid4()); check(False, "foreign owner read the snapshot")
        except __import__("agentforge.repositories.tasks", fromlist=["TaskNotFound"]).TaskNotFound:
            pass
        with sessions.begin() as db:
            original_status = db.scalar(text("SELECT status FROM tasks WHERE id = :t"), {"t": task_id})
            db.execute(text("UPDATE task_streams SET text = 'left behind', kind = 'answer' WHERE task_id = :t"), {"t": task_id})
            db.execute(text("UPDATE tasks SET status = 'FAILED' WHERE id = :t"), {"t": task_id})
        check(events.stream_snapshot(task_id, user) == (2, 3, "progress", ""), "text left on a terminal task was reported")
        with sessions.begin() as db:
            db.execute(text("UPDATE tasks SET status = :s WHERE id = :t"), {"t": task_id, "s": original_status})
        clear_stream(sessions, task_id)
        check(events.stream_snapshot(task_id, user) == (2, 3, "progress", ""), f"clear_stream is not observable {events.stream_snapshot(task_id, user)}")
        s2._epoch, s2._revision = 2, 2
        s2._write("late", "answer", 2)                  # a late writer after the terminal clear is fenced out
        check(events.stream_snapshot(task_id, user) == (2, 3, "progress", ""), "writer resurrected text after the clear")
        print("PASS snapshot read model, terminal blanking, observable terminal clear")
        # HTTP: SSE endpoint and snapshot endpoint
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
            w = ReplyStream(sessions, task_id)
            for piece in ("Stream", "ing ", "works ✓"):
                w.feed(piece); time.sleep(0.25)
            w.end()
            w.commit_reply()   # the call ended without tool calls: the text is the answer
            time.sleep(0.5)
            w.discard()   # the answer is thrown away: the client must be told with an empty reset
        # a clean start: the previous section left a cleared row
        clear_stream(sessions, task_id)
        threading.Thread(target=writer, daemon=True).start()
        import httpx, uvicorn
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=8765, log_level="error"))
        threading.Thread(target=server.run, daemon=True).start()
        for _ in range(100):
            if server.started: break
            time.sleep(0.1)
        base = "http://127.0.0.1:8765/api/v1/tasks/"
        client_text, client_state = "", None
        def apply(d):
            global client_text, client_state
            if d["reset"]:
                check(d["offset"] == 0, f"reset not at offset 0 {d}")
                client_text, client_state = d["text"], (d["epoch"], d["revision"])
            else:
                check((d["epoch"], d["revision"]) == client_state and d["offset"] == len(client_text), f"append out of place {d} vs {client_state} {len(client_text)}")
                client_text += d["text"]
        try:
            with httpx.stream("GET", base + f"{task_id}/events", timeout=30) as response:
                check(response.status_code == 200, f"sse status {response.status_code}")
                check(response.headers["cache-control"] == "no-cache, no-store, no-transform", response.headers["cache-control"])
                check(response.headers["x-accel-buffering"] == "no", "buffering header")
                event = None; deadline = time.time() + 25
                for line in response.iter_lines():
                    if line.startswith("event: "): event = line[7:]
                    elif line.startswith("data: ") and event == "message.delta":
                        d = json.loads(line[6:]); got.append(d); apply(d)
                        check(set(d) == {"task_id", "epoch", "revision", "kind", "offset", "text", "reset"}, f"frame fields {d}")
                        if client_text == "" and any(x["text"] for x in got): break   # text was shown, then cleared
                    if time.time() > deadline: break
        finally:
            pass
        assembled = [d for d in got if d["text"]]
        check(got[0]["reset"] is True and got[0]["offset"] == 0, f"first frame must reset {got[:1]}")
        check(any(d["reset"] is False and d["offset"] > 0 for d in got), f"deltas were not incremental {got}")
        check(any(d["kind"] == "answer" and "Streaming works ✓".startswith(d["text"]) for d in assembled), f"answer kind {got}")
        check(got[-1]["reset"] is True and got[-1]["text"] == "", f"the discard was not delivered as an empty reset {got[-1:]}")
        print("PASS SSE delivered message.delta resets and appends, ending with an empty reset:",
              [(d["epoch"], d["revision"], d["kind"], d["offset"], d["text"], d["reset"]) for d in got])
        # A reconnect starts again at offset 0 with a reset, even onto the cleared row.
        with httpx.stream("GET", base + f"{task_id}/events", timeout=10) as response:
            event, frame = None, None
            for line in response.iter_lines():
                if line.startswith("event: "): event = line[7:]
                elif line.startswith("data: ") and event == "message.delta": frame = json.loads(line[6:]); break
        check(frame is not None and frame["reset"] is True and frame["offset"] == 0 and frame["text"] == "", f"reconnect {frame}")
        print("PASS reconnect begins with an empty reset at offset 0")
        # Snapshot endpoint: owner, no-store, foreign / unknown tasks
        w = ReplyStream(sessions, task_id); w.feed("Snapshot text"); w.end(); w.commit_reply()
        reply = httpx.get(base + f"{task_id}/stream-snapshot", timeout=10)
        body = reply.json()
        check(reply.status_code == 200 and reply.headers["cache-control"] == "private, no-store", f"snapshot {reply.status_code} {reply.headers.get('cache-control')}")
        check(body["task_id"] == str(task_id) and body["kind"] == "answer" and body["text"] == "Snapshot text" and body["terminal"] is False
              and body["epoch"] >= 1 and body["revision"] >= 0 and body["event_sequence"] >= 1, f"snapshot body {body}")
        missing = httpx.get(base + f"{uuid4()}/stream-snapshot", timeout=10)
        check(missing.status_code == 404 and missing.json()["detail"] == "task_not_found", f"foreign/missing {missing.status_code} {missing.text}")
        server.should_exit = True
        print("PASS stream-snapshot endpoint:", body)
        clear_stream(sessions, task_id)
        after = events.stream_snapshot(task_id, user)
        check(after is not None and after[2:] == ("progress", ""), f"cleanup left text {after}")
        print("PASS cleanup is an observable empty reset")
        # Notes written next to tool calls: saved as ordered events, the live copy cleared, a later reply under a newer epoch.
        from agentforge.repositories.events import append_event
        def emit(kind, payload):
            with sessions.begin() as db:
                append_event(db, db.get(Task, task_id, with_for_update=True), kind, payload)
        narrator = ReplyStream(sessions, task_id, emit=emit)
        before = events.stream_snapshot(task_id, user)
        narrator.begin(); narrator.feed_narration("I'll look that up."); narrator.end()
        note = events.stream_snapshot(task_id, user)
        check(note[3] == "I'll look that up." and note[2] == "progress" and note[0] == before[0] + 1, f"live note {note}")
        narrator.commit_narration()
        saved = [e for e in events.list_after(task_id, user, 0, 100) if e.event_type == "assistant.message"]
        check(len(saved) == 1 and saved[0].payload == {"text": "I'll look that up.", "epoch": note[0]}, f"note event {saved}")
        cleared = events.stream_snapshot(task_id, user)
        check(cleared == (note[0], note[1] + 1, "progress", ""), f"live copy of a saved note was not cleared {cleared}")
        narrator.begin(); narrator.feed("The answer."); narrator.end(); narrator.commit_reply()
        reply = events.stream_snapshot(task_id, user)
        check(reply[0] == note[0] + 1 and reply[2:] == ("answer", "The answer."), f"reply epoch {reply}")
        print("PASS notes saved as events in order, live copy cleared by a reset, reply on a newer epoch")
        clear_stream(sessions, task_id)
except BaseException as exc:
    import traceback; traceback.print_exc(); print("FAIL"); sys.exit(1)
finally:
    if engine is not None: engine.dispose()
    if created:
        with admin.connect() as c: c.execute(text('DROP DATABASE "' + name + '" WITH (FORCE)'))
        print("disposable database removed")
    if admin is not None: admin.dispose()
