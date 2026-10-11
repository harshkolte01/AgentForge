"""Live tool calls on a disposable PostgreSQL database: migration 0016 (up, down, no drift), the `tool` kind and its
columns, a call written by the model, then running with output, redaction, fencing, the snapshot read model and the
SSE frames a browser receives.

Run from apps/api: uv run python examples/tool_stream_smoke.py
"""
import json, signal, sys, threading, time
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4
sys.path.insert(0, str(Path(__file__).resolve().parent))
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from agentforge.settings import Settings
from _disposable_db import admin_engine
import agentforge.settings as settings_module
def _timeout(*a): raise TimeoutError('smoke timed out')
signal.signal(signal.SIGALRM, _timeout); signal.alarm(150)
ROOT = Path(__file__).resolve().parents[3]
name = "agentforge_toolstream_smoke_" + uuid4().hex[:10]
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
        app_env="test", auth_mode="disabled", llm_api_key=None, tavily_api_key=None, neatlogs_api_key=None)
    with patch.object(settings_module, "get_settings", return_value=settings):
        from alembic import command
        from alembic.config import Config
        from alembic.autogenerate import compare_metadata
        from alembic.migration import MigrationContext
        cfg = Config(str(ROOT / "apps/api/alembic.ini")); cfg.set_main_option("script_location", str(ROOT / "apps/api/alembic"))
        command.upgrade(cfg, "head")
        engine = create_engine(settings.database_url)
        def columns():
            with engine.connect() as c:
                return {r.column_name: (r.data_type, r.is_nullable) for r in c.execute(text(
                    "SELECT column_name, data_type, is_nullable FROM information_schema.columns WHERE table_name = 'task_streams'"))}
        cols = columns()
        for column in ("tool", "call_id", "detail"):
            check(cols.get(column) == ("text", "YES"), f"{column}: {cols.get(column)}")
        with engine.connect() as c:
            drift = compare_metadata(MigrationContext.configure(c, opts={"compare_type": True, "compare_server_default": True}),
                                     __import__("agentforge.database.models", fromlist=["Base"]).Base.metadata)
            check(not drift, f"metadata drift: {drift}")
        print("PASS migration 0016 adds the columns, with no metadata drift")

        from agentforge.database.models import User, Project, Task
        from agentforge.contracts.sessions import SessionCreate, SessionTurnCreate
        from agentforge.repositories.sessions import SessionRepository
        from agentforge.repositories.events import EventRepository, append_event
        from agentforge.orchestration.stream import ReplyStream, clear_stream
        sessions = sessionmaker(engine, expire_on_commit=False)
        user, project = uuid4(), uuid4()
        with sessions.begin() as db:
            db.add(User(id=user, github_user_id=-9, display_name="Fixture")); db.flush()
            db.add(Project(id=project, owner_id=user, name="Fixture"))
        repo = SessionRepository(sessions)
        conversation = repo.create(user, SessionCreate(project_id=project, mode="general"))
        turn = repo.turn(conversation.id, user, SessionTurnCreate(idempotency_key=uuid4(), kind="general", goal="Write a file and run it."))
        task_id = turn.task.id
        events = EventRepository(sessions)

        def exec_sql(sql, **params):
            with sessions.begin() as db:
                db.execute(text(sql), {"t": task_id, **params})
        try:
            exec_sql("INSERT INTO task_streams (task_id, epoch, kind) VALUES (:t, 1, 'bogus')")
            check(False, "an unknown kind was accepted")
        except IntegrityError:
            pass
        for kind in ("tool", "output"):
            exec_sql("INSERT INTO task_streams (task_id, epoch, kind, text, tool, call_id) VALUES (:t, 1, :k, 'ls', 'bash', 'x')", k=kind)
            exec_sql("DELETE FROM task_streams WHERE task_id = :t")
        print("PASS kinds 'tool' and 'output' are accepted and unknown kinds are still refused")

        def emit(kind, payload):
            with sessions.begin() as db:
                append_event(db, db.get(Task, task_id, with_for_update=True), kind, payload)
        now = [0.0]
        stream = ReplyStream(sessions, task_id, emit=emit, clock=lambda: now[0])
        call = "run:tool:call_1"
        arguments = json.dumps({"path": "src/app.py", "content": "print('hi')\nprint('there')\n"})

        # --- a note, then a call being written ---------------------------------------------------------
        stream.begin()
        stream.feed_narration("I'll create the file.")
        stream.feed_tool(0, call, "write", arguments[:25])
        saved = [e for e in events.list_after(task_id, user, 0, 100) if e.event_type == "assistant.message"]
        check(len(saved) == 1 and saved[0].payload == {"text": "I'll create the file.", "epoch": 1}, f"note {saved}")
        first = events.stream_snapshot(task_id, user)
        check(first[0] == 2 and first[2] == "tool" and first[4] == ("write", call, "src/app.py"), f"call named {first}")
        now[0] += 0.2
        stream.feed_tool(0, call, "write", arguments[25:])
        stream.end()
        grown = events.stream_snapshot(task_id, user)
        check(grown[:2] == first[:2] and grown[3] == "print('hi')\nprint('there')\n" and grown[4] == first[4], f"grew {grown}")
        check(events.stream_snapshot(task_id, uuid4()) is None, "a stranger saw a tool call")
        read = events.stream_since(task_id, user, (grown[0], grown[1]), 5)
        check(read.live[:4] == (grown[0], grown[1], "tool", len(grown[3])) and read.live[4] == grown[3][5:] and read.tool == grown[4], f"since {read}")
        snapshot = events.live_snapshot(task_id, user)
        check((snapshot.kind, snapshot.tool, snapshot.call_id, snapshot.detail) == ("tool", "write", call, "src/app.py"), f"snapshot {snapshot}")
        print("PASS a note is saved, then the call shows (named, with its path), grows by appending and reads back with its identity")

        # --- the call runs: its output streams, redacted ------------------------------------------------
        sink = stream.output(call, "bash")
        running = events.stream_snapshot(task_id, user)
        check(running[0] == grown[0] + 1 and running[2] == "output" and running[3] == "" and running[4] == ("bash", call, None), f"running {running}")
        now[0] += 0.2
        sink("installing...\n")
        now[0] += 0.2
        token = "ghp_" + "b" * 30
        sink(f"token {token}\ndone\n")
        shown = events.stream_snapshot(task_id, user)
        check("installing...\n" in shown[3] and token not in shown[3] and "[REDACTED]" in shown[3], f"output {shown}")
        for number in range(900):
            now[0] += 0.2
            sink(f"line {number:04d} " + "x" * 30 + "\n")
        tail = events.stream_snapshot(task_id, user)
        check(tail[3].startswith("…\n") and len(tail[3]) <= 6002 and tail[3].endswith("line 0899 " + "x" * 30 + "\n"), "tail window")
        check(tail[1] > shown[1], "the moving window must be a replacement")
        print("PASS the running call's output is redacted and bounded to its last whole lines")

        # --- fencing: a stale writer cannot bring output back -------------------------------------------
        newer = ReplyStream(sessions, task_id, clock=lambda: now[0])
        newer.begin(); newer.feed("Next model call")
        stale = events.stream_snapshot(task_id, user)
        now[0] += 0.2
        sink("STALE OUTPUT\n")
        check(events.stream_snapshot(task_id, user) == stale and stale[2] == "progress" and stale[4] is None, f"stale {stale}")
        print("PASS a newer epoch fences the old call's output out, and prose rows carry no tool")

        # --- settle clears; the end of the task clears too -----------------------------------------------
        stream2 = ReplyStream(sessions, task_id, clock=lambda: now[0])
        stream2.begin(); sink2 = stream2.output(call, "bash"); sink2("work\n")
        stream2.settle("some-other-call")
        check(events.stream_snapshot(task_id, user)[2] == "output", "settling another call cleared this one")
        stream2.settle(call)
        cleared = events.stream_snapshot(task_id, user)
        check(cleared[2:] == ("progress", "", None), f"settle {cleared}")
        stream2.begin(); stream2.output(call, "bash")("left behind\n")
        with sessions.begin() as db:
            db.execute(text("UPDATE tasks SET status = 'FAILED' WHERE id = :t"), {"t": task_id})
        ended = events.stream_snapshot(task_id, user)
        check(ended[2:] == ("progress", "", None), f"a row left on a finished task was reported as it is {ended}")
        with sessions.begin() as db:
            db.execute(text("UPDATE tasks SET status = 'RUNNING' WHERE id = :t"), {"t": task_id})
        clear_stream(sessions, task_id)
        with engine.connect() as c:
            row = c.execute(text("SELECT kind, text, tool, call_id, detail FROM task_streams WHERE task_id = :t"), {"t": task_id}).one()
        check(tuple(row) == ("progress", "", None, None, None), f"clear_stream left {tuple(row)}")
        print("PASS settle is selective, a finished task shows no call, clear_stream empties the columns")

        # --- the browser's view: SSE frames ---------------------------------------------------------------
        from fastapi.testclient import TestClient
        from agentforge.main import create_app
        from agentforge.auth.session import current_user
        app = create_app(settings); app.state.engine = engine
        with sessions() as db:
            identity = db.get(User, user); db.expunge(identity)
        app.dependency_overrides[current_user] = lambda: identity
        import agentforge.api.routes_events as routes_events
        routes_events.current_user = lambda request: identity
        def writer():
            time.sleep(1.0)
            w = ReplyStream(sessions, task_id, emit=emit)
            w.begin()
            piece = json.dumps({"command": "npm test -- --watch=false"})
            w.feed_tool(0, "run:tool:sse", "bash", piece[:12]); time.sleep(0.3)
            w.feed_tool(0, "run:tool:sse", "bash", piece[12:]); w.end(); time.sleep(0.4)
            out = w.output("run:tool:sse", "bash")
            for number in range(3):
                out(f"step {number}\n"); time.sleep(0.3)
            w.settle("run:tool:sse")
        threading.Thread(target=writer, daemon=True).start()
        import httpx, uvicorn
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=8766, log_level="error"))
        threading.Thread(target=server.run, daemon=True).start()
        for _ in range(100):
            if server.started: break
            time.sleep(0.1)
        got, held, version = [], "", None
        base = "http://127.0.0.1:8766/api/v1/tasks/"
        with httpx.stream("GET", base + f"{task_id}/events", timeout=30) as response:
            event, deadline = None, time.time() + 25
            for line in response.iter_lines():
                if line.startswith("event: "): event = line[7:]
                elif line.startswith("data: ") and event == "message.delta":
                    d = json.loads(line[6:]); got.append(d)
                    if d["reset"]:
                        check(d["offset"] == 0, f"reset not at 0 {d}")
                        held, version = d["text"], (d["epoch"], d["revision"])
                    else:
                        check((d["epoch"], d["revision"]) == version and d["offset"] == len(held), f"append out of place {d}")
                        held += d["text"]
                    if d["kind"] in ("tool", "output"):
                        check(d["tool"] == "bash" and d["call_id"] == "run:tool:sse", f"a tool frame without its identity {d}")
                    else:
                        check("tool" not in d and "call_id" not in d, f"a prose frame carrying a tool {d}")
                    if held == "" and any(x["text"].startswith("step 2") or "step 2" in x["text"] for x in got): break
                if time.time() > deadline: break
        server.should_exit = True
        written = [d for d in got if d["kind"] == "tool"]
        printed = [d for d in got if d["kind"] == "output"]
        check(any(d["text"].startswith("npm test") for d in written), f"the call as written never arrived {got}")
        check(any(d["reset"] is False and d["offset"] > 0 for d in printed), f"no incremental output frame {got}")
        check(any("step 1" in d["text"] for d in printed) and got[-1]["kind"] == "progress" and got[-1]["text"] == "", f"output / clear {got[-3:]}")
        print("PASS SSE frames carry the call's identity, apply in order and end with an empty reset:",
              [(d["epoch"], d["revision"], d["kind"], d["offset"], d["text"][:14], d["reset"]) for d in got])

        # --- downgrade converts a live tool row rather than failing --------------------------------------
        clear_stream(sessions, task_id)
        stream3 = ReplyStream(sessions, task_id); stream3.begin(); stream3.feed_tool(0, "z", "bash", '{"command": "ls"}')
        command.downgrade(cfg, "0015_delegated_runs")
        check("tool" not in columns() and "epoch" in columns(), f"downgrade left {sorted(columns())}")
        with engine.connect() as c:
            row = c.execute(text("SELECT kind, text FROM task_streams WHERE task_id = :t"), {"t": task_id}).one()
        check(tuple(row) == ("progress", ""), f"a tool row survived the downgrade as {tuple(row)}")
        command.upgrade(cfg, "head")
        check(all(column in columns() for column in ("tool", "call_id", "detail")), "re-upgrade")
        print("PASS migration 0016 is reversible")
except BaseException as exc:
    import traceback; traceback.print_exc(); print("FAIL"); sys.exit(1)
finally:
    if engine is not None: engine.dispose()
    if created:
        with admin.connect() as c: c.execute(text('DROP DATABASE "' + name + '" WITH (FORCE)'))
        print("disposable database removed")
    if admin is not None: admin.dispose()
