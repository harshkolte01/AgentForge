"""Activity read APIs on a disposable PostgreSQL database: exact counts, message batching, tool call details.

Seeds tasks, tool calls, events, evidence and artifacts, then exercises the real endpoints and repositories:
exact counts beyond 40 actions, note-only activity, suppression, pending approvals, a bounded number of SQL statements
for a 100 message page, foreign-owner and cross-task rejection, bounded previews and missing artifacts.

Run from apps/api: uv run python examples/activity_api_smoke.py
"""
import json, os, shutil, signal, sys, tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4
sys.path.insert(0, str(Path(__file__).resolve().parent))
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from agentforge.settings import Settings
from _disposable_db import admin_engine
import agentforge.settings as settings_module
def _timeout(*a): raise TimeoutError('activity smoke timed out')
signal.signal(signal.SIGALRM, _timeout); signal.alarm(120)
ROOT = Path(__file__).resolve().parents[3]
name = "agentforge_activity_smoke_" + uuid4().hex[:10]
admin = engine = None; created = False
# Resolved because the storage walk refuses symlinked ancestors (macOS temp dirs live behind /var).
storage_root = Path(tempfile.mkdtemp(prefix="agentforge_activity_")).resolve()
os.chmod(storage_root, 0o700)
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
        app_env="test", auth_mode="disabled", llm_api_key=None, tavily_api_key=None, neatlogs_api_key=None,
        research_storage_path=storage_root)
    with patch.object(settings_module, "get_settings", return_value=settings):
        from alembic import command
        from alembic.config import Config
        cfg = Config(str(ROOT / "apps/api/alembic.ini")); cfg.set_main_option("script_location", str(ROOT / "apps/api/alembic"))
        command.upgrade(cfg, "head")
        print("PASS migrations")
        engine = create_engine(settings.database_url)
        from agentforge.database.models import (AgentDefinition, AgentDefinitionVersion, AgentRun, BudgetReservation, Project, Task,
            TaskEvent, ToolCall, User)
        from agentforge.database.coding import CodingArtifact, CodingEvidence, CodingSession
        from agentforge.database.sessions import ToolApproval
        from agentforge.database.research_models import ResearchArtifact, ResearchEvidence, ResearchSource
        from agentforge.database.sessions import Conversation, ConversationMessage
        from agentforge.integrations.artifact_storage import ArtifactStorage
        from agentforge.repositories.sessions import SessionRepository
        from agentforge.sandbox.workspace import build_diff
        sessions = sessionmaker(engine, expire_on_commit=False)
        storage = ArtifactStorage(storage_root)
        alice, bob = uuid4(), uuid4()
        alice_project, bob_project = uuid4(), uuid4()
        definition, version = uuid4(), uuid4()
        with sessions.begin() as db:
            db.add_all([User(id=alice, github_user_id=-21, display_name="Alice"), User(id=bob, github_user_id=-22, display_name="Bob")]); db.flush()
            db.add_all([Project(id=alice_project, owner_id=alice, name="A"), Project(id=bob_project, owner_id=bob, name="B")]); db.flush()
            db.add(AgentDefinition(id=definition, namespace="builtin", agent_key="fixture", project_id=None)); db.flush()
            db.add(AgentDefinitionVersion(id=version, definition_id=definition, version=1, definition_json={}, sha256="0" * 64))

        def seed_task(owner, project, *, status="COMPLETED", kind="general", error_code=None, attachments=None):
            task_id, run_id = uuid4(), uuid4()
            permissions = {"kind": kind, **({"attachments": attachments} if attachments is not None else {})}
            with sessions.begin() as db:
                db.add(Task(id=task_id, project_id=project, created_by=owner, goal="fixture goal", status=status,
                    thread_id=str(uuid4()), max_cost_usd=0.01, max_wall_time_sec=300, permissions_json=permissions, error_code=error_code))
                db.flush()
                reservation = uuid4()
                db.add(BudgetReservation(id=reservation, task_id=task_id, run_id=run_id, amount_usd=1, max_steps=2, max_tool_calls=200,
                    depth=1, deadline=datetime.now(timezone.utc) + timedelta(minutes=5))); db.flush()
                db.add(AgentRun(id=run_id, task_id=task_id, definition_version_id=version, definition_sha256="0" * 64,
                    definition_snapshot={}, brief_sha256="0" * 64, effective_permissions_json={}, effective_budget_json={},
                    budget_reservation_id=reservation, reserved_cost_usd=1, status="COMPLETED"))
            return task_id, run_id

        SECRET = "SECRET-ARGUMENT-VALUE"
        def call(task_id, run_id, tool, status="SUCCEEDED", *, code=None, evidence=(), artifacts=(), duration=12, arguments=None):
            call_id = uuid4()
            finished = status != "INTENT"
            result = None if not finished else {"status": "ok", "artifact_ids": [str(a) for a in artifacts], "attempt": 1,
                "arguments": arguments if arguments is not None else {"path": SECRET}, "duration_ms": duration}
            with sessions.begin() as db:
                db.add(ToolCall(id=call_id, task_id=task_id, agent_run_id=run_id, tool_name=tool, operation_key=f"{run_id}:tool:{call_id}",
                    args_digest="a" * 64, intent_json_sanitized={"arguments_sha256": SECRET}, status=status, result_json=result,
                    error_code=code, evidence_ids=[str(e) for e in evidence],
                    finished_at=datetime.now(timezone.utc) if finished else None))
            return call_id

        def events(task_id, items):
            with sessions.begin() as db:
                task = db.get(Task, task_id)
                for event_type, payload in items:
                    task.event_sequence += 1
                    db.add(TaskEvent(task_id=task_id, sequence=task.event_sequence, event_type=event_type, payload=payload))

        # ---- exact counts ---------------------------------------------------------------------
        many, many_run = seed_task(alice, alice_project)
        for _ in range(45):
            call(many, many_run, "web_search")
        call(many, many_run, "fetch_webpage", "BLOCKED", code="SCOPE_DENIED")
        call(many, many_run, "run_tests", "INTENT")                       # left open by an ended task: shown, never dropped
        for _ in range(3):
            call(many, many_run, "read_task_input")                      # the run reading its own request
        for code in ("SCHEMA_INVALID", "DUPLICATE_CALL", "TOOL_LIMIT_REACHED"):
            call(many, many_run, "web_search", "FAILED", code=code)       # the model correcting itself
        call(many, many_run, "unknown", "BLOCKED", code="TOOL_UNAVAILABLE")  # unregistered name
        events(many, [("assistant.message", {"text": f"note {i}", "epoch": i}) for i in range(1, 4)]
            + [("assistant.message", {"text": "", "epoch": 4}), ("task.finished", {"status": "COMPLETED"})])
        with sessions.begin() as db:
            db.add(ResearchSource(id=uuid4(), task_id=many, operation_key="op", ordinal=0, url="https://example.com/a", title="A",
                content="x", content_sha256="0" * 64, source_type="webpage", fetch_status="FETCHED"))
        note_only, _ = seed_task(alice, alice_project)
        events(note_only, [("assistant.message", {"text": "Looking into it.", "epoch": 1}), ("task.finished", {"status": "COMPLETED"})])
        nothing, _ = seed_task(alice, alice_project)
        waiting, waiting_run = seed_task(alice, alice_project, status="RUNNING")
        call(waiting, waiting_run, "git_diff", "INTENT")
        with sessions.begin() as db:
            db.add(ToolApproval(id=uuid4(), task_id=waiting, user_id=alice, operation_key="b", tool="bash", summary="ls", state="PENDING"))
            db.add(ToolApproval(id=uuid4(), task_id=nothing, user_id=alice, operation_key="c", tool="bash", summary="ls", state="APPROVED"))

        from fastapi.testclient import TestClient
        from agentforge.main import create_app
        from agentforge.auth.session import current_user
        app = create_app(settings); app.state.engine = engine
        with sessions() as db:
            identities = {"alice": db.get(User, alice), "bob": db.get(User, bob)}
            for item in identities.values(): db.expunge(item)
        who = {"user": identities["alice"]}
        app.dependency_overrides[current_user] = lambda: who["user"]
        client = TestClient(app)
        FORBIDDEN = ("intent_json_sanitized", "args_digest", "operation_key", "arguments", SECRET, "storage_key", "sha256")

        def get(path, user="alice", expect=200):
            who["user"] = identities[user]
            response = client.get(path)
            check(response.status_code == expect, f"{path} as {user}: {response.status_code} {response.text[:300]}")
            if expect == 200:
                check(response.headers.get("cache-control") == "private, no-store", f"cache-control {response.headers.get('cache-control')}")
                for word in FORBIDDEN:
                    check(word not in response.text, f"{word} leaked in {path}: {response.text[:400]}")
            return response.json()

        summary = get(f"/api/v1/tasks/{many}/activity-summary")
        check(summary["action_count"] == 47, summary)                      # 45 + blocked + open run_tests; nothing hidden
        check(summary["note_count"] == 3 and summary["available"] and summary["terminal"], summary)
        check(summary["status"] == "COMPLETED" and summary["source_count"] == 1 and summary["artifact_count"] == 0, summary)
        check(summary["event_watermark"] == 5 and summary["terminal_sequence"] == 5, summary)
        check(summary["approval_pending"] is False and summary["error_code"] is None, summary)
        print("PASS exact action count beyond 40, hidden calls and empty notes excluded")
        summary = get(f"/api/v1/tasks/{note_only}/activity-summary")
        check(summary["action_count"] == 0 and summary["note_count"] == 1 and summary["available"] is True, summary)
        summary = get(f"/api/v1/tasks/{nothing}/activity-summary")
        check(summary["available"] is False and summary["terminal_sequence"] is None and summary["approval_pending"] is False, summary)  # an answered approval is not pending
        summary = get(f"/api/v1/tasks/{waiting}/activity-summary")
        check(summary["approval_pending"] is True and summary["terminal"] is False and summary["action_count"] == 1, summary)
        check(summary["terminal_sequence"] is None, summary)
        print("PASS note-only activity available, empty activity not, pending approval, running call counted")
        get(f"/api/v1/tasks/{many}/activity-summary", "bob", 404)
        get(f"/api/v1/tasks/{uuid4()}/activity-summary", "alice", 404)
        print("PASS foreign and unknown task summary is 404")

        # ---- messages: batch counts, attachments, bounded statements ---------------------------------
        repo = SessionRepository(sessions)
        with sessions.begin() as db:
            conversation = Conversation(owner_id=alice, project_id=alice_project, title="Many", mode="general", archived=False, last_sequence=0)
            db.add(conversation); db.flush()
            conversation_id = conversation.id
        pairs = []
        for index in range(50):
            attachments = [{"name": "notes.txt", "content": "界" * 7}, {"name": "b.md", "content": "x" * 100}] if index == 0 else None
            task_id, run_id = seed_task(alice, alice_project, attachments=attachments)
            pairs.append((task_id, run_id))
            if index % 5 == 0:
                for _ in range(3):
                    call(task_id, run_id, "web_search")
        with sessions.begin() as db:
            for index, (task_id, _) in enumerate(pairs):
                db.add(ConversationMessage(session_id=conversation_id, sequence=2 * index + 1, role="user", content=f"q{index}", task_id=task_id))
                db.add(ConversationMessage(session_id=conversation_id, sequence=2 * index + 2, role="assistant", content=f"a{index}", task_id=task_id))
            db.get(Conversation, conversation_id).last_sequence = 100
        counted = []
        listener = lambda conn, cursor, statement, *rest: counted.append(statement)
        event.listen(engine, "before_cursor_execute", listener)
        try:
            page = repo.messages(conversation_id, alice, limit=100)
        finally:
            event.remove(engine, "before_cursor_execute", listener)
        check(len(page) == 100, len(page))
        check(len(counted) <= 20, f"{len(counted)} statements for a 100 message page")
        first_user, first_assistant, fifth_assistant = page[0], page[1], page[11]
        check([(a.name, a.characters) for a in first_user.attachments] == [("notes.txt", 7), ("b.md", 100)], first_user.attachments)
        check(first_assistant.attachments == [] and page[2].attachments == [], "attachments on the wrong messages")
        check(first_assistant.activity.action_count == 3 and first_assistant.activity.available and first_assistant.activity.terminal, first_assistant.activity)
        check(len(first_assistant.steps) == 3, first_assistant.steps)
        check(page[3].activity.action_count == 0 and page[3].activity.available is False, page[3].activity)
        check(first_user.activity is None and page[2].activity is None, "user messages carry activity")
        check("content" not in json.dumps([a.model_dump() for a in first_user.attachments]), "attachment content leaked")
        # the same page through the HTTP route keeps the original fields
        who["user"] = identities["alice"]
        body = client.get(f"/api/v1/sessions/{conversation_id}/messages?limit=100").json()
        check(len(body) == 100 and body[1]["activity"]["action_count"] == 3 and body[0]["attachments"][0]["name"] == "notes.txt", body[:2])
        check({"id", "session_id", "sequence", "role", "content", "task_id", "created_at", "steps"} <= set(body[0]), body[0].keys())
        print(f"PASS message page: exact activity, attachments by name, {len(counted)} SQL statements for 100 messages")
        big = [message for message in page if message.role == "assistant"]
        check(len(big) == 50, "assistant count")
        # a conversation that held 45+ actions reports them exactly in its message (steps stays capped for compatibility)
        with sessions.begin() as db:
            extra = Conversation(owner_id=alice, project_id=alice_project, title="Exact", mode="general", archived=False, last_sequence=2)
            db.add(extra); db.flush()
            db.add(ConversationMessage(session_id=extra.id, sequence=1, role="user", content="go", task_id=None))
            db.add(ConversationMessage(session_id=extra.id, sequence=2, role="assistant", content="done", task_id=many))
            extra_id = extra.id
        exact = repo.messages(extra_id, alice)[1]
        check(exact.activity.action_count == 47 and len(exact.steps) <= 40 and exact.activity.note_count == 3, (exact.activity, len(exact.steps)))
        print("PASS message activity is exact while steps stays capped")

        # a real turn reports its attachments (names and sizes) on the receipt and on replay, and in the history afterwards
        from agentforge.contracts.sessions import SessionCreate, SessionTurnCreate
        live = repo.create(alice, SessionCreate(project_id=alice_project, mode="general"))
        turn_request = SessionTurnCreate(idempotency_key=uuid4(), kind="general", goal="Summarise the attached notes please.",
            attachments=[{"name": "meeting.txt", "content": "界" * 40}])
        receipt = repo.turn(live.id, alice, turn_request)
        check([(a.name, a.characters) for a in receipt.user_message.attachments] == [("meeting.txt", 40)], receipt.user_message.attachments)
        replay = repo.turn(live.id, alice, turn_request)
        check(replay.replayed and [(a.name, a.characters) for a in replay.user_message.attachments] == [("meeting.txt", 40)], replay.user_message)
        history = repo.messages(live.id, alice)
        check([(a.name, a.characters) for a in history[0].attachments] == [("meeting.txt", 40)] and history[0].activity is None, history)
        print("PASS attachments on the turn receipt, its replay and the history")

        # ---- tool call detail --------------------------------------------------------------------------
        coding, coding_run = seed_task(alice, alice_project, kind="coding")
        session_id = uuid4()
        with sessions.begin() as db:
            db.add(CodingSession(id=session_id, task_id=coding, repo_id="o/r", commit_sha="a" * 40, baseline_revision="1" * 64, revision="2" * 64))
        stdout = ("ok é\n" * 6000)          # ~42 KB: past the 24 KB preview bound
        evidence_id, log_id = uuid4(), uuid4()
        log_bytes = b"# Actual test execution\nfull log"
        key, digest = storage.write(task_id=coding, artifact_id=log_id, content=log_bytes)
        with sessions.begin() as db:
            db.add(CodingEvidence(id=evidence_id, task_id=coding, session_id=session_id, operation_key="t1", kind="test", revision="2" * 64,
                evidence_json={"profile_id": "python_unittest", "exit_code": 1, "timed_out": False, "qualified": False, "output_truncated": False,
                    "observed_test_count": 7, "stdout": stdout, "stderr": "Traceback boom", "argv": ["python", "-c", SECRET], "cleanup_confirmed": True}))
            db.flush()
            db.add(CodingArtifact(id=log_id, task_id=coding, operation_key="t1", kind="test", filename="python_unittest.log",
                mime_type="text/plain; charset=utf-8", storage_key=key, sha256=digest, byte_size=len(log_bytes), metadata_json={}))
        run_tests = call(coding, coding_run, "run_tests", evidence=[evidence_id], artifacts=[log_id], duration=4321)
        results_call = call(coding, coding_run, "get_test_results", evidence=[evidence_id])
        detail = get(f"/api/v1/tasks/{coding}/tool-calls/{run_tests}")
        check(detail["kind"] == "execution" and detail["tool"] == "run_tests", detail)
        check(detail["outcome"] == "completed" and detail["duration_ms"] == 4321 and detail["error_code"] is None, detail)
        check(detail["execution"] == {"profile_id": "python_unittest", "exit_code": 1, "timed_out": False, "qualified": False,
            "output_truncated": False, "observed_test_count": 7}, detail["execution"])
        check(detail["stdout"]["truncated"] is True and detail["stdout"]["bytes"] == len(stdout.encode()), detail["stdout"]["bytes"])
        check(len(detail["stdout"]["text"].encode()) <= 24 * 1024 and stdout.startswith(detail["stdout"]["text"]), "stdout preview not a bounded prefix")
        check(detail["stderr"] == {"text": "Traceback boom", "truncated": False, "bytes": 14}, detail["stderr"])
        check(detail["artifacts"] == [{"id": str(log_id), "filename": "python_unittest.log", "mime_type": "text/plain; charset=utf-8",
            "byte_size": len(log_bytes), "download_url": f"/api/v1/tasks/{coding}/artifacts/{log_id}"}], detail["artifacts"])
        check(detail["unavailable_reason"] is None and "argv" not in json.dumps(detail), detail)
        # the download link really is the existing owner-authorized route
        who["user"] = identities["alice"]
        check(client.get(detail["artifacts"][0]["download_url"]).status_code == 200, "download link")
        inspected = get(f"/api/v1/tasks/{coding}/tool-calls/{results_call}")
        check(inspected["tool"] == "get_test_results" and inspected["execution"]["exit_code"] == 1 and inspected["artifacts"] == [], inspected)
        print("PASS execution detail: evidence-derived fields, bounded stdout/stderr, log download")

        # outcomes
        cancelled = call(coding, coding_run, "run_sandbox_command", "FAILED", code="USER_CANCELLED")
        blocked = call(coding, coding_run, "run_tests", "BLOCKED", code="BUDGET_EXCEEDED")
        unknown = call(coding, coding_run, "run_tests", "UNKNOWN", code="UNKNOWN_REMOTE_OUTCOME")
        failed = call(coding, coding_run, "run_tests", "FAILED", code="SANDBOX_CONFIGURATION_REQUIRED")
        for call_id, outcome, code, reason in ((cancelled, "cancelled", "USER_CANCELLED", "no_output_recorded"),
                (blocked, "blocked", "BUDGET_EXCEEDED", "no_output_recorded"), (unknown, "unknown", "UNKNOWN_REMOTE_OUTCOME", "no_output_recorded"),
                (failed, "failed", "SANDBOX_CONFIGURATION_REQUIRED", "no_output_recorded")):
            item = get(f"/api/v1/tasks/{coding}/tool-calls/{call_id}")
            check(item["outcome"] == outcome and item["error_code"] == code and item["execution"] is None, item)
            check(item["unavailable_reason"] == reason, item)
        running_task, running_run = seed_task(alice, alice_project, status="RUNNING", kind="coding")
        open_call = call(running_task, running_run, "run_tests", "INTENT")
        item = get(f"/api/v1/tasks/{running_task}/tool-calls/{open_call}")
        check(item["outcome"] == "running" and item["finished_at"] is None and item["duration_ms"] is None and item["unavailable_reason"] == "not_finished", item)
        ended_task, ended_run = seed_task(alice, alice_project, status="FAILED", kind="coding")
        stuck = call(ended_task, ended_run, "run_tests", "INTENT")
        check(get(f"/api/v1/tasks/{ended_task}/tool-calls/{stuck}")["outcome"] == "unknown", "stuck call on an ended task")
        cancelled_task, cancelled_run = seed_task(alice, alice_project, status="CANCELLED", kind="coding", error_code="USER_CANCELLED")
        stopped = call(cancelled_task, cancelled_run, "run_tests", "INTENT")
        check(get(f"/api/v1/tasks/{cancelled_task}/tool-calls/{stopped}")["outcome"] == "cancelled", "stopped call on a cancelled task")
        print("PASS outcomes: cancelled, blocked, unknown, failed, running, interrupted")

        # evidence missing / belonging to another task
        gone = call(coding, coding_run, "run_tests", evidence=[uuid4()])
        item = get(f"/api/v1/tasks/{coding}/tool-calls/{gone}")
        check(item["execution"] is None and item["stdout"] is None and item["unavailable_reason"] == "evidence_unavailable", item)
        bob_task, bob_run = seed_task(bob, bob_project, kind="coding")
        bob_session, bob_evidence, bob_log = uuid4(), uuid4(), uuid4()
        bob_key, bob_digest = storage.write(task_id=bob_task, artifact_id=bob_log, content=b"BOB PRIVATE LOG")
        with sessions.begin() as db:
            db.add(CodingSession(id=bob_session, task_id=bob_task, repo_id="o/r", commit_sha="a" * 40, baseline_revision="1" * 64, revision="2" * 64))
            db.flush()
            db.add(CodingEvidence(id=bob_evidence, task_id=bob_task, session_id=bob_session, operation_key="t", kind="test", revision="2" * 64,
                evidence_json={"profile_id": "p", "exit_code": 0, "stdout": "BOB PRIVATE OUTPUT", "stderr": ""}))
            db.add(CodingArtifact(id=bob_log, task_id=bob_task, operation_key="t", kind="test", filename="p.log", mime_type="text/plain",
                storage_key=bob_key, sha256=bob_digest, byte_size=15, metadata_json={}))
        stolen = call(coding, coding_run, "run_tests", evidence=[bob_evidence], artifacts=[bob_log])
        response = client.get(f"/api/v1/tasks/{coding}/tool-calls/{stolen}")
        item = response.json()
        check("BOB" not in response.text and str(bob_log) not in response.text and str(bob_evidence) not in response.text, response.text)
        check(item["execution"] is None and item["artifacts"] == [] and item["unavailable_reason"] in ("evidence_unavailable", "artifact_missing"), item)
        bob_call = call(bob_task, bob_run, "run_tests", evidence=[bob_evidence])
        get(f"/api/v1/tasks/{coding}/tool-calls/{bob_call}", "alice", 404)            # another owner's call through my task
        get(f"/api/v1/tasks/{bob_task}/tool-calls/{bob_call}", "alice", 404)          # another owner's task
        get(f"/api/v1/tasks/{coding}/tool-calls/{uuid4()}", "alice", 404)
        other_mine = call(many, many_run, "run_tests")
        get(f"/api/v1/tasks/{coding}/tool-calls/{other_mine}", "alice", 404)          # my own call, but a different task
        check(client.get(f"/api/v1/tasks/{coding}/tool-calls/{other_mine}").json()["detail"] == "tool_call_not_found", "code")
        print("PASS evidence/artifacts of another task or owner are rejected; foreign and cross-task call ids are 404")

        # diff
        base = {"a.py": b"one\ntwo\nthree\n", "gone.txt": b"bye\n", "keep.txt": b"same\n", "empty.txt": b""}
        current = {"a.py": b"one\n2\nthree\nfour\n", "new.txt": b"hello\nworld\n", "keep.txt": b"same\n", "dir/-- odd.txt": b"x\n"}
        diff_text = build_diff(base, current).encode()
        diff_artifact, diff_evidence = uuid4(), uuid4()
        dkey, ddigest = storage.write(task_id=coding, artifact_id=diff_artifact, content=diff_text)
        with sessions.begin() as db:
            db.add(CodingEvidence(id=diff_evidence, task_id=coding, session_id=session_id, operation_key="d1", kind="diff", revision="2" * 64,
                evidence_json={"changed_paths": ["a.py", "empty.txt", "gone.txt", "new.txt"], "base_sha": "a" * 40, "sha256": ddigest}))
            db.flush()
            db.add(CodingArtifact(id=diff_artifact, task_id=coding, operation_key="d1", kind="diff", filename="changes.diff",
                mime_type="text/x-diff; charset=utf-8", storage_key=dkey, sha256=ddigest, byte_size=len(diff_text), metadata_json={}))
        diff_call = call(coding, coding_run, "git_diff", evidence=[diff_evidence], artifacts=[diff_artifact])
        item = get(f"/api/v1/tasks/{coding}/tool-calls/{diff_call}")
        stats = {f["path"]: (f["additions"], f["deletions"]) for f in item["files"]}
        check(stats == {"a.py": (2, 1), "dir/-- odd.txt": (1, 0), "empty.txt": (0, 0), "gone.txt": (0, 1), "new.txt": (2, 0)}, stats)
        check(item["kind"] == "diff" and item["diff"]["text"] == diff_text.decode() and item["diff"]["truncated"] is False, item["diff"])
        check(item["artifacts"][0]["id"] == str(diff_artifact) and item["unavailable_reason"] is None and item["stdout"] is None, item)
        # a large diff is previewed in a bounded way but its changed files still come from the whole stored diff
        huge_base = {"big.txt": b""}
        huge_now = {"big.txt": ("line of change\n" * 4000).encode(), "z.txt": b"tail\n"}
        huge = build_diff(huge_base, huge_now).encode()
        huge_artifact, huge_evidence = uuid4(), uuid4()
        hkey, hdigest = storage.write(task_id=coding, artifact_id=huge_artifact, content=huge)
        with sessions.begin() as db:
            db.add(CodingEvidence(id=huge_evidence, task_id=coding, session_id=session_id, operation_key="d2", kind="diff", revision="2" * 64,
                evidence_json={"changed_paths": ["big.txt", "z.txt"], "base_sha": "a" * 40, "sha256": hdigest}))
            db.flush()
            db.add(CodingArtifact(id=huge_artifact, task_id=coding, operation_key="d2", kind="diff", filename="changes.diff",
                mime_type="text/x-diff", storage_key=hkey, sha256=hdigest, byte_size=len(huge), metadata_json={}))
        huge_call = call(coding, coding_run, "git_diff", evidence=[huge_evidence], artifacts=[huge_artifact])
        item = get(f"/api/v1/tasks/{coding}/tool-calls/{huge_call}")
        check(item["diff"]["truncated"] is True and item["diff"]["bytes"] == len(huge) and len(item["diff"]["text"].encode()) <= 24 * 1024, item["diff"]["bytes"])
        check({f["path"]: f["additions"] for f in item["files"]} == {"big.txt": 4000, "z.txt": 1}, item["files"])
        # the diff file was removed from storage: paths still listed from the evidence, with a reason
        os.unlink(storage_root / dkey)
        item = get(f"/api/v1/tasks/{coding}/tool-calls/{diff_call}")
        check(item["diff"] is None and item["unavailable_reason"] == "artifact_unavailable", item)
        check([f["path"] for f in item["files"]] == ["a.py", "empty.txt", "gone.txt", "new.txt"] and item["files"][0]["additions"] is None, item["files"])
        print("PASS diff detail: changed-file summary, bounded preview, storage loss reported")

        # artifacts
        general, general_run = seed_task(alice, alice_project)
        body_text = ("# Report\n" + "paragraph é\n" * 4000)
        report_id = uuid4()
        rkey, rdigest = storage.write(task_id=general, artifact_id=report_id, content=body_text.encode())
        with sessions.begin() as db:
            db.add(ResearchArtifact(id=report_id, task_id=general, operation_key="c1", kind="general_markdown", filename="general-report.md",
                mime_type="text/markdown; charset=utf-8", storage_key=rkey, sha256=rdigest, byte_size=len(body_text.encode()), metadata_json={"title": SECRET}))
        created_call = call(general, general_run, "create_task_artifact", artifacts=[report_id], arguments={"format": "markdown", "content_chars": 10})
        item = get(f"/api/v1/tasks/{general}/tool-calls/{created_call}")
        check(item["kind"] == "artifact" and item["artifacts"][0]["download_url"] == f"/api/v1/tasks/{general}/artifacts/{report_id}", item)
        check(item["preview"]["truncated"] is True and item["preview"]["bytes"] == len(body_text.encode()) and item["preview"]["text"].startswith("# Report"), item["preview"]["bytes"])
        check(len(item["preview"]["text"].encode()) <= 24 * 1024, "artifact preview unbounded")
        receipt = uuid4()
        with sessions.begin() as db:
            db.add(ResearchEvidence(id=receipt, task_id=general, kind="document_read", validation_state="READ",
                evidence_json={"resource": f"artifact:{report_id}", "run_id": str(general_run), "operation_key": "k", "sha256": rdigest, "start": 0, "end": 10, "total": 10}))
        read_call = call(general, general_run, "read_task_artifact", evidence=[receipt], arguments={"artifact_id": str(report_id)})
        item = get(f"/api/v1/tasks/{general}/tool-calls/{read_call}")
        check(item["tool"] == "read_task_artifact" and item["artifacts"][0]["id"] == str(report_id) and item["preview"] is not None, item)
        # artifact row present, file gone
        os.unlink(storage_root / rkey)
        item = get(f"/api/v1/tasks/{general}/tool-calls/{created_call}")
        check(item["artifacts"][0]["id"] == str(report_id) and item["preview"] is None and item["unavailable_reason"] == "artifact_unavailable", item)
        # artifact record missing entirely
        phantom = call(general, general_run, "create_task_artifact", artifacts=[uuid4()])
        item = get(f"/api/v1/tasks/{general}/tool-calls/{phantom}")
        check(item["artifacts"] == [] and item["preview"] is None and item["unavailable_reason"] == "artifact_missing", item)
        # an artifact that belongs to another owner's task, referenced from my call
        foreign_artifact = uuid4()
        fkey, fdigest = storage.write(task_id=bob_task, artifact_id=foreign_artifact, content=b"BOB PRIVATE DELIVERABLE")
        with sessions.begin() as db:
            db.add(ResearchArtifact(id=foreign_artifact, task_id=bob_task, operation_key="c2", kind="general_text", filename="general-output.txt",
                mime_type="text/plain", storage_key=fkey, sha256=fdigest, byte_size=23, metadata_json={}))
        smuggled = call(general, general_run, "create_task_artifact", artifacts=[foreign_artifact])
        response = client.get(f"/api/v1/tasks/{general}/tool-calls/{smuggled}")
        check("BOB" not in response.text and str(foreign_artifact) not in response.text, response.text)
        check(response.json()["artifacts"] == [] and response.json()["unavailable_reason"] == "artifact_missing", response.json())
        print("PASS artifact detail: bounded preview, receipt-based read, missing and foreign artifacts become reasons")

        # tools without a detail kind, hidden calls
        for tool, status, code in (("web_search", "SUCCEEDED", None), ("read_repo_file", "SUCCEEDED", None), ("read_task_input", "SUCCEEDED", None),
                ("run_tests", "FAILED", "SCHEMA_INVALID"), ("unknown", "BLOCKED", "TOOL_UNAVAILABLE")):
            made = call(coding, coding_run, tool, status, code=code)
            body = get(f"/api/v1/tasks/{coding}/tool-calls/{made}", "alice", 404)
            check(body["detail"] == "tool_detail_unavailable", (tool, body))
        print("PASS tools without a detail kind and hidden calls are 404 tool_detail_unavailable")
        get(f"/api/v1/tasks/{coding}/tool-calls/{run_tests}", "bob", 404)
        print("PASS foreign owner cannot read a call detail")
except BaseException:
    import traceback; traceback.print_exc(); print("FAIL"); sys.exit(1)
finally:
    if engine is not None: engine.dispose()
    if created:
        with admin.connect() as c:
            c.execute(text('DROP DATABASE IF EXISTS "' + name + '" WITH (FORCE)'))
    if admin is not None: admin.dispose()
    shutil.rmtree(storage_root, ignore_errors=True)
print("activity api smoke OK")
