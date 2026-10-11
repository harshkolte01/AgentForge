"""Activity read APIs without a database: routes, visibility parity, bounded previews, diff summaries and detail mapping.

SQL behaviour (exact counts, batching, ownership joins) is exercised against a disposable PostgreSQL database by
examples/activity_api_smoke.py.
"""
import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from unittest import TestCase
from unittest.mock import patch
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentforge.api import routes_activity
from agentforge.auth.session import current_user
from agentforge.contracts.activity import ActivitySummary, AttachmentView, MessageActivity, ToolCallDetail
from agentforge.contracts.sessions import MessageView
from agentforge.repositories import activity as activity_module
from agentforge.repositories.activity import (PREVIEW_BYTES, ActivityRepository, ToolCallNotFound, ToolDetailUnavailable,
    _clip, _uuids, diff_files)
from agentforge.repositories.sessions import SessionRepository
from agentforge.repositories.tasks import TaskNotFound
from agentforge.sandbox.workspace import build_diff
from agentforge.subagents.scope import ScopeDenied
from agentforge.tools import visibility
from agentforge.tools.lifecycle import EXECUTION_TOOLS


class FakeTasks:
    def __init__(self, *, found=True):
        self.found, self.project_id = found, uuid4()

    def get(self, task_id, owner_id):
        if not self.found:
            raise TaskNotFound("task_not_found")
        return NS(id=task_id, project_id=self.project_id)


class RouteTests(TestCase):
    def client(self, *, found=True, repository=None):
        app = FastAPI()
        app.include_router(routes_activity.router)
        app.dependency_overrides[current_user] = lambda: NS(id=uuid4())
        app.dependency_overrides[routes_activity.task_repository] = lambda: FakeTasks(found=found)
        app.state.engine = object()
        app.state.settings = NS(research_storage_path="/nonexistent")
        self.patch = patch.object(routes_activity, "ActivityRepository", lambda *args: repository)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        return TestClient(app)

    def summary(self, task_id):
        return ActivitySummary(task_id=task_id, action_count=47, note_count=2, available=True, event_watermark=9,
            terminal=True, status="COMPLETED", terminal_sequence=9)

    def test_summary_is_private_and_uncacheable(self):
        task_id = uuid4()
        repository = NS(summary=lambda task, owner: self.summary(task))
        response = self.client(repository=repository).get(f"/api/v1/tasks/{task_id}/activity-summary")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "private, no-store")
        self.assertEqual((response.json()["action_count"], response.json()["terminal_sequence"]), (47, 9))

    def test_foreign_or_unknown_tasks_are_not_found_on_both_endpoints(self):
        client = self.client(found=False, repository=NS())
        for path in (f"/api/v1/tasks/{uuid4()}/activity-summary", f"/api/v1/tasks/{uuid4()}/tool-calls/{uuid4()}"):
            response = client.get(path)
            self.assertEqual((response.status_code, response.json()["detail"]), (404, "task_not_found"))

    def test_summary_recheck_failure_is_not_found(self):
        def gone(task, owner):
            raise TaskNotFound("task_not_found")
        response = self.client(repository=NS(summary=gone)).get(f"/api/v1/tasks/{uuid4()}/activity-summary")
        self.assertEqual((response.status_code, response.json()["detail"]), (404, "task_not_found"))

    def test_detail_errors_map_to_distinct_codes(self):
        def failing(error):
            def detail(**kwargs):
                raise error
            return NS(tool_call_detail=detail)
        path = f"/api/v1/tasks/{uuid4()}/tool-calls/{uuid4()}"
        for error, code in ((ToolCallNotFound("x"), "tool_call_not_found"), (ToolDetailUnavailable("x"), "tool_detail_unavailable"),
                            (ScopeDenied("x"), "task_not_found")):
            response = self.client(repository=failing(error)).get(path)
            self.assertEqual((response.status_code, response.json()["detail"]), (404, code))

    def test_detail_is_returned_with_no_store_and_the_task_project_is_passed_on(self):
        seen = {}
        task_id, call_id = uuid4(), uuid4()

        def detail(**kwargs):
            seen.update(kwargs)
            return ToolCallDetail(task_id=task_id, tool_call_id=call_id, tool="run_tests", kind="execution", outcome="completed",
                started_at=datetime.now(timezone.utc))
        client = self.client(repository=NS(tool_call_detail=detail))
        response = client.get(f"/api/v1/tasks/{task_id}/tool-calls/{call_id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "private, no-store")
        self.assertEqual((seen["task_id"], seen["tool_call_id"]), (task_id, call_id))
        self.assertIsNotNone(seen["project_id"])


class VisibilityParityTests(TestCase):
    def test_counts_use_the_one_shared_definition(self):
        from agentforge.repositories import sessions
        self.assertIs(sessions.SELF_CORRECTION_CODES, visibility.SELF_CORRECTION_CODES)
        sql = str(activity_module._visible().compile(compile_kwargs={"literal_binds": True}))
        for hidden in visibility.HIDDEN_TOOLS | set(visibility.SELF_CORRECTION_CODES):
            self.assertIn(hidden, sql)
        self.assertFalse(visibility.is_visible_call("read_task_input"))
        self.assertFalse(visibility.is_visible_call("web_search", "DUPLICATE_CALL"))
        self.assertTrue(visibility.is_visible_call("web_search", "SCOPE_DENIED"))

    def test_detail_kinds_cover_exactly_the_documented_tools(self):
        self.assertEqual(activity_module.TOOL_DETAIL_KIND, {
            "bash": "execution", "run_tests": "execution", "run_sandbox_command": "execution", "get_test_results": "execution",
            "git_diff": "diff", "create_task_artifact": "artifact", "read_task_artifact": "artifact"})
        self.assertEqual({tool for tool, kind in activity_module.TOOL_DETAIL_KIND.items() if kind == "execution"}, set(EXECUTION_TOOLS))


class BoundsTests(TestCase):
    def test_preview_is_a_prefix_within_the_bound_and_reports_the_full_size(self):
        text = "é" * 20000                                     # 40,000 bytes
        clipped = _clip(text)
        self.assertTrue(clipped.truncated)
        self.assertEqual(clipped.bytes, 40000)
        self.assertLessEqual(len(clipped.text.encode()), PREVIEW_BYTES)
        self.assertTrue(text.startswith(clipped.text))

    def test_small_text_is_whole_and_non_text_is_absent(self):
        self.assertEqual(_clip("ok").model_dump(), {"text": "ok", "truncated": False, "bytes": 2})
        self.assertIsNone(_clip(None))
        self.assertIsNone(_clip(b"bytes"))

    def test_ids_from_json_are_references_to_check_never_trusted(self):
        good = uuid4()
        self.assertEqual(_uuids([str(good), str(good), "not-a-uuid", 7, None, str(good).upper(), "x" * 400]), [good])
        self.assertEqual(_uuids("nope"), [])
        self.assertEqual(_uuids(None), [])


class DiffSummaryTests(TestCase):
    def test_counts_per_file_from_a_real_workspace_diff(self):
        base = {"a.py": b"one\ntwo\nthree\n", "gone.txt": b"bye\n", "empty.txt": b"", "same.txt": b"s\n",
                "dashes.txt": b"a\n-- not a header\n+++ nor this\nb\n"}
        current = {"a.py": b"one\n2\nthree\nfour\n", "new.txt": b"hello\nworld\n", "same.txt": b"s\n",
                   "dashes.txt": b"a\nb\nc\n", "bin.dat": b"\xff\xfe\x00"}
        files = {item.path: (item.additions, item.deletions) for item in diff_files(build_diff(base, current))}
        self.assertEqual(files, {"a.py": (2, 1), "bin.dat": (0, 0), "dashes.txt": (1, 2), "empty.txt": (0, 0),
                                 "gone.txt": (0, 1), "new.txt": (2, 0)})

    def test_quoted_header_paths_and_bad_input(self):
        self.assertEqual([item.path for item in diff_files(build_diff({}, {'we ird"name.txt': b""}))], ['we ird"name.txt'])
        self.assertEqual(diff_files(""), [])
        self.assertEqual(diff_files("@@ -1 +1 @@\n-a\n+b\n"), [])
        many = {f"f{index}.txt": b"x\n" for index in range(300)}
        self.assertEqual(len(diff_files(build_diff({}, many))), activity_module.MAX_DETAIL_FILES)


class FakeDb:
    """Answers each `scalar(...)` and `scalars(...)` in order; enough to drive the mapping code without SQL."""
    def __init__(self, scalars=(), many=()):
        self.queue, self.many = list(scalars), list(many)

    def scalar(self, statement):
        return self.queue.pop(0)

    def scalars(self, statement):
        return self.many.pop(0)


def call_row(tool, status="SUCCEEDED", *, code=None, evidence=(), result=None, finished=True, duration=None):
    started = datetime.now(timezone.utc) - timedelta(seconds=3)
    return NS(id=uuid4(), tool_name=tool, status=status, error_code=code, evidence_ids=[str(item) for item in evidence],
        result_json=result, started_at=started, finished_at=started + timedelta(seconds=2) if finished else None)


class DetailMappingTests(TestCase):
    def repository(self, db, storage=None):
        @contextmanager
        def factory():
            yield db
        return ActivityRepository(lambda: factory(), storage)

    def detail(self, db, call, task_status="COMPLETED", task_error=None, storage=None):
        task = NS(id=uuid4(), status=task_status, error_code=task_error)
        db.queue.insert(0, call)
        with patch.object(activity_module.ResearchRepository, "_owned", return_value=task):
            return self.repository(db, storage).tool_call_detail(task_id=task.id, tool_call_id=call.id if call else uuid4(),
                owner_id=uuid4(), project_id=uuid4())

    def test_a_call_that_is_not_in_the_task_is_not_found(self):
        with self.assertRaises(ToolCallNotFound):
            self.detail(FakeDb(), None)

    def test_tools_without_a_detail_kind_and_hidden_calls_are_unavailable(self):
        for call in (call_row("web_search"), call_row("read_repo_file"), call_row("read_task_input"), call_row("unknown", "BLOCKED"),
                     call_row("run_tests", "FAILED", code="SCHEMA_INVALID")):
            with self.assertRaises(ToolDetailUnavailable, msg=call.tool_name):
                self.detail(FakeDb(), call)

    def test_outcome_mapping_never_reports_success_for_an_open_call(self):
        task = NS(status="RUNNING", error_code=None)
        outcome = ActivityRepository._outcome
        for status, code, expected in (("SUCCEEDED", None, "completed"), ("FAILED", None, "failed"), ("BLOCKED", "SCOPE_DENIED", "blocked"),
                                       ("UNKNOWN", "UNKNOWN_REMOTE_OUTCOME", "unknown"), ("FAILED", "USER_CANCELLED", "cancelled")):
            self.assertEqual(outcome(call_row("run_tests", status, code=code), task), expected)
        self.assertEqual(outcome(call_row("run_tests", "INTENT", finished=False), task), "running")
        self.assertEqual(outcome(call_row("run_tests", "INTENT"), NS(status="FAILED", error_code="X")), "unknown")
        self.assertEqual(outcome(call_row("run_tests", "INTENT"), NS(status="CANCELLED", error_code=None)), "cancelled")
        self.assertEqual(outcome(call_row("run_tests", "INTENT"), NS(status="PARTIAL", error_code="USER_CANCELLED")), "cancelled")

    def test_duration_prefers_the_recorded_value_and_ignores_garbage(self):
        duration = ActivityRepository._duration
        self.assertEqual(duration(call_row("x"), {"duration_ms": 1234}), 1234)
        self.assertEqual(duration(call_row("x"), {"duration_ms": "9"}), 2000)
        self.assertEqual(duration(call_row("x"), {"duration_ms": -5}), 2000)
        self.assertIsNone(duration(call_row("x", "INTENT", finished=False), {}))

    def test_a_failing_test_run_is_a_completed_call_with_its_exit_code_and_evidence_output(self):
        evidence_id = uuid4()
        evidence = NS(evidence_json={"profile_id": "python_unittest", "exit_code": 1, "timed_out": False, "qualified": False,
            "output_truncated": False, "observed_test_count": 7, "stdout": "o" * 50000, "stderr": "boom", "argv": ["secret"]})
        result = {"status": "ok", "artifact_ids": [], "arguments": {"path": "SECRET"}, "duration_ms": 77}
        with patch.object(activity_module, "CodingRepository"):
            detail = self.detail(FakeDb([evidence]), call_row("run_tests", evidence=[evidence_id], result=result))
        self.assertEqual((detail.outcome, detail.kind, detail.duration_ms), ("completed", "execution", 77))
        self.assertEqual((detail.execution.exit_code, detail.execution.qualified, detail.execution.observed_test_count), (1, False, 7))
        self.assertTrue(detail.stdout.truncated)
        self.assertEqual(detail.stdout.bytes, 50000)
        self.assertLessEqual(len(detail.stdout.text), PREVIEW_BYTES)
        self.assertEqual(detail.stderr.text, "boom")
        self.assertIsNone(detail.unavailable_reason)
        self.assertNotIn("SECRET", detail.model_dump_json())
        self.assertNotIn("argv", detail.model_dump_json())

    def test_evidence_values_of_the_wrong_type_are_dropped(self):
        evidence = NS(evidence_json={"profile_id": 5, "exit_code": True, "timed_out": "no", "qualified": 1, "observed_test_count": -3,
            "stdout": {"x": 1}, "stderr": None})
        with patch.object(activity_module, "CodingRepository"):
            detail = self.detail(FakeDb([evidence]), call_row("run_sandbox_command", evidence=[uuid4()], result={}))
        self.assertEqual(detail.execution.model_dump(), {"profile_id": None, "exit_code": None, "timed_out": None, "qualified": None,
            "output_truncated": None, "observed_test_count": None})
        self.assertIsNone(detail.stdout)
        self.assertIsNone(detail.stderr)

    def test_missing_evidence_is_a_reason_not_an_error(self):
        with patch.object(activity_module, "CodingRepository"):
            lost = self.detail(FakeDb([None]), call_row("run_tests", evidence=[uuid4()], result={}))
            nothing = self.detail(FakeDb(), call_row("run_tests", "BLOCKED", code="BUDGET_EXCEEDED", result={}))
            open_call = self.detail(FakeDb(), call_row("run_tests", "INTENT", finished=False), task_status="RUNNING")
        self.assertEqual((lost.execution, lost.unavailable_reason), (None, "evidence_unavailable"))
        self.assertEqual((nothing.outcome, nothing.error_code, nothing.unavailable_reason), ("blocked", "BUDGET_EXCEEDED", "no_output_recorded"))
        self.assertEqual((open_call.outcome, open_call.unavailable_reason), ("running", "not_finished"))

    def test_error_codes_are_only_exposed_when_they_look_like_codes(self):
        with patch.object(activity_module, "CodingRepository"):
            detail = self.detail(FakeDb(), call_row("run_tests", "FAILED", code="boom: /etc/passwd leaked", result={}))
        self.assertIsNone(detail.error_code)

    def test_artifacts_must_resolve_through_the_task_scoped_repository(self):
        artifact_id, other = uuid4(), uuid4()
        entry = {"id": str(artifact_id), "filename": "r.md", "mime_type": "text/markdown", "byte_size": 4,
                 "storage_key": "k", "sha256": "d" * 64}

        def get_artifact(self_, requested, **scope):
            if requested != str(artifact_id):
                raise ScopeDenied("outside the task")
            return entry
        storage = NS(read=lambda key, digest, max_bytes: b"body")
        result = {"artifact_ids": [str(artifact_id), str(other), "garbage"]}
        with patch.object(activity_module.ResearchRepository, "get_artifact", get_artifact):
            detail = self.detail(FakeDb(), call_row("create_task_artifact", result=result), storage=storage)
        self.assertEqual([item.id for item in detail.artifacts], [str(artifact_id)])
        self.assertEqual(detail.artifacts[0].download_url, f"/api/v1/tasks/{detail.task_id}/artifacts/{artifact_id}")
        self.assertEqual(detail.preview.text, "body")
        self.assertEqual(detail.unavailable_reason, "artifact_missing")
        self.assertNotIn("storage_key", detail.model_dump_json())
        self.assertNotIn("sha256", detail.model_dump_json())

    def test_unreadable_artifact_storage_keeps_the_link_and_explains_the_missing_preview(self):
        artifact_id = uuid4()
        entry = {"id": str(artifact_id), "filename": "r.md", "mime_type": "text/markdown", "byte_size": 4, "storage_key": "k", "sha256": "d" * 64}

        def unreadable(key, digest, max_bytes):
            raise ScopeDenied("integrity")
        with patch.object(activity_module.ResearchRepository, "get_artifact", lambda self_, requested, **scope: entry):
            detail = self.detail(FakeDb(), call_row("create_task_artifact", result={"artifact_ids": [str(artifact_id)]}), storage=NS(read=unreadable))
            nostorage = self.detail(FakeDb(), call_row("create_task_artifact", result={"artifact_ids": [str(artifact_id)]}))
        for item in (detail, nostorage):
            self.assertEqual(len(item.artifacts), 1)
            self.assertIsNone(item.preview)
            self.assertEqual(item.unavailable_reason, "artifact_unavailable")

    def test_artifact_call_without_any_record_is_artifact_missing(self):
        detail = self.detail(FakeDb(), call_row("create_task_artifact", result={}))
        self.assertEqual((detail.artifacts, detail.unavailable_reason), ([], "artifact_missing"))


class ContractTests(TestCase):
    def test_no_internal_field_can_appear_in_a_detail_response(self):
        fields = set(ToolCallDetail.model_fields)
        for forbidden in ("intent_json_sanitized", "args_digest", "operation_key", "arguments", "result_json", "storage_key", "sha256"):
            self.assertNotIn(forbidden, fields)
        with self.assertRaises(ValueError):
            ToolCallDetail(task_id=uuid4(), tool_call_id=uuid4(), tool="run_tests", kind="execution", outcome="completed",
                started_at=datetime.now(timezone.utc), arguments={"x": 1})

    def test_message_activity_defaults_keep_old_payloads_valid(self):
        message = MessageView(id=uuid4(), session_id=uuid4(), sequence=1, role="user", content="hi", task_id=None,
            created_at=datetime.now(timezone.utc))
        self.assertEqual((message.steps, message.activity, message.attachments), ([], None, []))
        self.assertFalse(MessageActivity().available)


class MessagesBatchTests(TestCase):
    """`messages` asks for the whole page's activity and attachments in one grouped call each."""

    def test_one_batched_lookup_per_kind_of_data(self):
        session_id, owner = uuid4(), uuid4()
        rows = []
        for sequence in range(1, 201, 2):
            task_id = uuid4()
            for role in ("user", "assistant"):
                rows.append(NS(id=uuid4(), session_id=session_id, sequence=sequence + (role == "assistant"), role=role,
                    content=role, task_id=task_id, created_at=datetime.now(timezone.utc)))

        class Begin:
            def __enter__(self): return self
            def __exit__(self, *args): return False

        class Handle:
            scalars = staticmethod(lambda statement: rows)
            execute = staticmethod(lambda statement: [])
            begin = staticmethod(lambda: Begin())
            def __enter__(self): return self
            def __exit__(self, *args): return False

        calls = {"activity": [], "attachments": [], "steps": []}
        assistant_tasks = {row.task_id for row in rows if row.role == "assistant"}
        user_tasks = {row.task_id for row in rows if row.role == "user"}
        first_user = next(row for row in rows if row.role == "user")
        shown = {first_user.task_id: [AttachmentView(name="notes.txt", characters=5)]}
        known = {task_id: MessageActivity(action_count=60, available=True) for task_id in assistant_tasks}
        repository = SessionRepository.__new__(SessionRepository)
        repository.session_factory = Handle
        with patch.object(SessionRepository, "owned", return_value=NS(id=session_id, active_task_id=None)), \
             patch("agentforge.repositories.sessions.settle_conversation"), \
             patch("agentforge.repositories.sessions.tool_steps", side_effect=lambda s, ids: calls["steps"].append(set(ids)) or {}), \
             patch("agentforge.repositories.sessions.activity_for_tasks", side_effect=lambda s, ids: calls["activity"].append(set(ids)) or known), \
             patch("agentforge.repositories.sessions.attachments_for_tasks", side_effect=lambda s, ids: calls["attachments"].append(set(ids)) or shown):
            page = repository.messages(session_id, owner, limit=100)
        self.assertEqual((len(calls["activity"]), len(calls["attachments"]), len(calls["steps"])), (1, 1, 1))
        self.assertEqual(calls["activity"][0], assistant_tasks)
        self.assertEqual(calls["attachments"][0], user_tasks)
        by_role = {role: [item for item in page if item.role == role] for role in ("user", "assistant")}
        self.assertTrue(all(item.activity.action_count == 60 for item in by_role["assistant"]))   # exact, beyond the old 40 cap
        self.assertTrue(all(item.activity is None for item in by_role["user"]))
        self.assertEqual([a.name for a in by_role["user"][0].attachments], ["notes.txt"])
        self.assertTrue(all(item.attachments == [] for item in by_role["assistant"]))
        self.assertTrue(all(item.attachments == [] for item in by_role["user"][1:]))
        self.assertNotIn("content", json.dumps([a.model_dump() for item in page for a in item.attachments]))
