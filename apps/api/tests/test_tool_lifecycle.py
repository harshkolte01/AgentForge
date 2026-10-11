"""Tool lifecycle events: the gateway reports start and finish with accurate outcomes, and termination settles leftovers."""
import asyncio
import json
from decimal import Decimal
from types import SimpleNamespace as NS
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from agentforge.contracts.agent_definition import Budget
from agentforge.repositories.events import _KEYS
from agentforge.subagents.budget import BudgetExceeded
from agentforge.subagents.scope import ScopeDenied
from agentforge.tools.catalog import ToolCatalog
from agentforge.tools.contracts import ToolOutcome
from agentforge.tools.errors import ToolError
from agentforge.tools.gateway import ToolGateway, UnknownToolOutcome
from agentforge.tools.lifecycle import (CallLifecycle, execution_labels, finished_payload, outcome_from_row,
                                        reconcile_open_calls)
from agentforge.tools.visibility import HIDDEN_TOOLS, SELF_CORRECTION_CODES, is_visible_call

from test_tool_gateway import FakeBudgets, FakeCalls, FakeResources, args

TOOLS = ("read_repo_file", "write", "bash", "read_task_input", "web_search")
SECRET = "sk-live-0123456789-secret"


class Journal:
    """One ordered log shared by the repository, the adapters and the event sink."""

    def __init__(self):
        self.log, self.events = [], []

    def sink(self, event_type, payload):
        self.log.append(event_type)
        self.events.append((event_type, payload))

    def of(self, event_type):
        return [payload for kind, payload in self.events if kind == event_type]


class JournalCalls(FakeCalls):
    def __init__(self, journal):
        super().__init__()
        self.journal = journal

    def record_outcome(self, **kwargs):
        self.journal.log.append("persisted:" + kwargs["status"])
        super().record_outcome(**kwargs)


def make(handlers, *, sink="journal", allowed=TOOLS):
    journal = Journal()
    catalog = ToolCatalog()
    for name in TOOLS:
        async def default(args, ctx):
            return ToolOutcome(status="ok", cost_usd=Decimal(0))
        catalog.register(name, handlers.get(name, default), max_cost_usd=Decimal(0), timeout_sec=5)
    calls, budgets = JournalCalls(journal), FakeBudgets()
    gateway = ToolGateway(
        catalog=catalog, calls=calls, budgets=budgets, resources=FakeResources({"repo_id": "owner/name", "ref": "a" * 40}),
        reauthorize=AsyncMock(), ctx=NS(task_id=uuid4()),
        scope=NS(allowed_tools=allowed, permissions=NS(), budget=Budget()),
        run_id=uuid4(), reservation_id=uuid4(),
        lifecycle=journal.sink if sink == "journal" else sink)
    return gateway, journal, calls, budgets


def ok(**data):
    return ToolOutcome(status="ok", data=data, cost_usd=Decimal(0))


class LifecycleCase(IsolatedAsyncioTestCase):
    def setUp(self):
        patcher = patch("agentforge.tools.gateway.authorize_tool")
        patcher.start()
        self.addCleanup(patcher.stop)

    def assertAllowlisted(self, journal):
        for _, payload in journal.events:
            self.assertTrue(set(payload) <= _KEYS, set(payload) - _KEYS)
            self.assertLess(len(json.dumps(payload)), 16000)
            self.assertNotIn(SECRET, json.dumps(payload))


class EventShapeTests(LifecycleCase):
    async def test_start_precedes_the_adapter_and_finish_follows_the_persisted_outcome(self):
        journal = None

        async def read(args, ctx):
            journal.log.append("adapter")
            return ok(content="x")
        gateway, journal, calls, _ = make({"read_repo_file": read})
        await gateway.execute("read_repo_file", args(path="src/a.py"), "call_1")
        self.assertEqual(journal.log, ["tool.started", "adapter", "persisted:SUCCEEDED", "tool.finished"])
        tool_call_id = str(calls.intents[0][0])
        started, finished = journal.of("tool.started")[0], journal.of("tool.finished")[0]
        self.assertEqual(started, {"tool": "read_repo_file", "detail": "src/a.py", "call_id": f"{gateway.run_id}:tool:call_1"[-100:],
                                   "tool_call_id": tool_call_id, "agent_run_id": str(gateway.run_id)})
        self.assertEqual({key: finished[key] for key in ("tool", "status", "outcome", "call_id", "tool_call_id", "agent_run_id",
                                                         "evidence_ids", "artifact_ids")},
                         {"tool": "read_repo_file", "status": "ok", "outcome": "completed", "call_id": started["call_id"],
                          "tool_call_id": tool_call_id, "agent_run_id": str(gateway.run_id), "evidence_ids": [], "artifact_ids": []})
        self.assertIsInstance(finished["duration_ms"], int)
        self.assertNotIn("error_code", finished)
        self.assertAllowlisted(journal)

    async def test_evidence_and_artifact_ids_are_reported_after_validation(self):
        async def read(args, ctx):
            return ToolOutcome(status="ok", cost_usd=Decimal(0), evidence_ids=("e1",), artifact_ids=("a1",))
        gateway, journal, _, _ = make({"read_repo_file": read})
        await gateway.execute("read_repo_file", args(path="a.py"), "c1")
        finished = journal.of("tool.finished")[0]
        self.assertEqual((finished["evidence_ids"], finished["artifact_ids"]), (["e1"], ["a1"]))

    async def test_parallel_calls_of_one_tool_get_distinct_tool_call_ids(self):
        gate = asyncio.Event()

        async def read(args, ctx):
            await gate.wait()
            return ok()
        gateway, journal, calls, _ = make({"read_repo_file": read})
        pending = [asyncio.create_task(gateway.execute("read_repo_file", args(path=f"f{i}.py"), f"c{i}")) for i in range(3)]
        await asyncio.sleep(0.05)
        self.assertEqual(len(journal.of("tool.started")), 3)
        self.assertEqual(journal.of("tool.finished"), [])
        gate.set()
        await asyncio.gather(*pending)
        ids = {payload["tool_call_id"] for payload in journal.of("tool.started")}
        self.assertEqual(ids, {str(call_id) for call_id, _ in calls.intents})
        self.assertEqual(len(ids), 3)
        self.assertEqual({payload["tool_call_id"] for payload in journal.of("tool.finished")}, ids)
        self.assertEqual(len({payload["call_id"] for payload in journal.of("tool.started")}), 3)

    async def test_a_gateway_without_a_sink_still_runs(self):
        gateway, journal, _, _ = make({}, sink=None)
        outcome = await gateway.execute("read_repo_file", args(path="a.py"), "c1")
        self.assertEqual(outcome.status, "ok")
        self.assertEqual(journal.events, [])

    async def test_a_failing_sink_never_changes_the_call(self):
        def broken(event_type, payload):
            raise RuntimeError("lease lost")
        gateway, journal, calls, _ = make({}, sink=broken)
        with self.assertLogs("agentforge.tools.lifecycle", "WARNING"):
            outcome = await gateway.execute("read_repo_file", args(path="a.py"), "c1")
        self.assertEqual(outcome.status, "ok")
        self.assertEqual(calls.last()["status"], "SUCCEEDED")

    async def test_no_raw_arguments_or_output_reach_any_payload(self):
        async def write(args, ctx):
            return ok(echo=args.content, stdout=SECRET)
        gateway, journal, _, _ = make({"write": write})
        await gateway.execute("write", args(path="notes.txt", content=SECRET), "c1")
        self.assertEqual(len(journal.events), 2)
        self.assertEqual(journal.of("tool.started")[0]["detail"], "notes.txt")
        self.assertAllowlisted(journal)


class OutcomeTests(LifecycleCase):
    async def test_an_adapter_error_result_is_failed_not_blocked(self):
        async def search(args, ctx):
            return ToolOutcome(status="error", error_code="SEARCH_PROVIDER_ERROR", cost_usd=Decimal(0))
        gateway, journal, _, _ = make({"web_search": search})
        await gateway.execute("web_search", args(query="q"), "c1")
        finished = journal.of("tool.finished")[0]
        self.assertEqual((finished["outcome"], finished["status"], finished["error_code"]), ("failed", "error", "SEARCH_PROVIDER_ERROR"))

    async def test_a_typed_adapter_rejection_is_failed(self):
        async def read(args, ctx):
            raise ToolError("FILE_NOT_FOUND", "nope")
        gateway, journal, _, _ = make({"read_repo_file": read})
        await gateway.execute("read_repo_file", args(path="a.py"), "c1")
        self.assertEqual(journal.log, ["tool.started", "persisted:FAILED", "tool.finished"])
        self.assertEqual(journal.of("tool.finished")[0]["outcome"], "failed")

    async def test_an_adapter_that_rejects_its_own_result_is_a_visible_failure_not_a_self_correction(self):
        async def read(args, ctx):
            raise ValueError("invalid_adapter_result")
        gateway, journal, calls, _ = make({"read_repo_file": read})
        outcome = await gateway.execute("read_repo_file", args(path="a.py"), "c1")
        self.assertEqual(outcome.error_code, "TOOL_FAILED")
        self.assertEqual(calls.last()["error_code"], "TOOL_FAILED")
        self.assertEqual(journal.of("tool.finished")[0]["outcome"], "failed")

    async def test_a_refusal_before_the_adapter_is_a_started_and_blocked_pair(self):
        gateway, journal, calls, _ = make({})
        gateway.resources.verify_tool_inputs = AsyncMock(side_effect=ScopeDenied("not yours"))
        await gateway.execute("read_repo_file", args(path="a.py"), "c1")
        self.assertEqual([kind for kind, _ in journal.events], ["tool.started", "tool.finished"])
        self.assertLess(journal.log.index("persisted:BLOCKED"), journal.log.index("tool.finished"))
        started, finished = journal.of("tool.started")[0], journal.of("tool.finished")[0]
        self.assertEqual(started["detail"], "a.py")
        self.assertEqual((finished["outcome"], finished["status"], finished["error_code"]), ("blocked", "error", "SCOPE_DENIED"))
        self.assertEqual(finished["tool_call_id"], started["tool_call_id"])
        self.assertEqual(calls.last()["status"], "BLOCKED")

    async def test_budget_refusals_are_blocked(self):
        gateway, journal, _, budgets = make({})
        budgets.authorize_operation = lambda *a, **k: (_ for _ in ()).throw(BudgetExceeded("budget_exceeded"))
        with self.assertRaises(BudgetExceeded):  # the run ends; the step must still not stay running
            await gateway.execute("read_repo_file", args(path="a.py"), "c1")
        self.assertEqual(journal.log, ["tool.started", "persisted:BLOCKED", "tool.finished"])
        finished = journal.of("tool.finished")[0]
        self.assertEqual((finished["outcome"], finished["error_code"]), ("blocked", "BUDGET_EXCEEDED"))

    async def test_cancellation_is_reported_as_cancelled_after_persisting_and_is_not_masked(self):
        async def stopped(args, ctx):
            raise asyncio.CancelledError()
        gateway, journal, calls, _ = make({"read_repo_file": stopped})
        with self.assertRaises(asyncio.CancelledError):
            await gateway.execute("read_repo_file", args(path="a.py"), "c1")
        self.assertEqual(journal.log, ["tool.started", "persisted:FAILED", "tool.finished"])
        finished = journal.of("tool.finished")[0]
        self.assertEqual((finished["outcome"], finished["status"], finished["error_code"]), ("cancelled", "error", "USER_CANCELLED"))

    async def test_cancellation_survives_a_sink_that_fails(self):
        async def stopped(args, ctx):
            raise asyncio.CancelledError()

        def broken(event_type, payload):
            raise RuntimeError("database gone")
        gateway, _, calls, _ = make({"read_repo_file": stopped}, sink=broken)
        with self.assertLogs("agentforge.tools.lifecycle", "WARNING"), self.assertRaises(asyncio.CancelledError):
            await gateway.execute("read_repo_file", args(path="a.py"), "c1")
        self.assertEqual(calls.last()["error_code"], "USER_CANCELLED")

    async def test_cancellation_before_the_adapter_is_cancelled_not_blocked(self):
        gateway, journal, _, _ = make({})
        gateway.reauthorize = AsyncMock(side_effect=asyncio.CancelledError())
        with self.assertRaises(asyncio.CancelledError):
            await gateway.execute("read_repo_file", args(path="a.py"), "c1")
        self.assertEqual([kind for kind, _ in journal.events], ["tool.started", "tool.finished"])
        self.assertEqual(journal.of("tool.finished")[0]["outcome"], "cancelled")

    async def test_an_uncertain_effect_is_unknown(self):
        async def run(args, ctx):
            raise RuntimeError("provider connection dropped")
        gateway, journal, calls, _ = make({"bash": run})
        with self.assertRaises(UnknownToolOutcome):
            await gateway.execute("bash", args(command="pytest"), "c1")
        self.assertEqual(calls.last()["status"], "UNKNOWN")
        self.assertEqual(journal.log, ["tool.started", "persisted:UNKNOWN", "tool.finished"])
        finished = journal.of("tool.finished")[0]
        self.assertEqual((finished["outcome"], finished["status"], finished["error_code"]), ("unknown", "error", "UNKNOWN_REMOTE_OUTCOME"))

    async def test_a_cancelled_effect_stays_unknown(self):
        async def run(args, ctx):
            raise asyncio.CancelledError()
        gateway, journal, _, _ = make({"bash": run})
        with self.assertRaises(asyncio.CancelledError):
            await gateway.execute("bash", args(command="pytest"), "c1")
        self.assertEqual(journal.of("tool.finished")[0]["outcome"], "unknown")


class ExecutionEvidenceTests(LifecycleCase):
    async def run_profile(self, tool, data, **extra):
        async def handler(args, ctx):
            return ok(**data)
        gateway, journal, calls, _ = make({tool: handler})
        await gateway.execute(tool, json.dumps({"command": "pytest -q"}), "c1")
        return journal.of("tool.finished")[0], calls

    async def test_failing_tests_are_a_successful_call_with_a_nonzero_exit_code(self):
        finished, calls = await self.run_profile("bash", {
            "exit_code": 1, "timed_out": False, "qualified": False, "stdout": SECRET, "stderr": "FAILED test_x"})
        self.assertEqual((finished["status"], finished["outcome"]), ("ok", "completed"))
        self.assertEqual((finished["exit_code"], finished["timed_out"], finished["qualified"]), (1, False, False))
        self.assertNotIn(SECRET, json.dumps(finished))
        self.assertNotIn("FAILED", json.dumps(finished))
        # The durable row keeps the same labels for reconciliation and detail views, never the output.
        self.assertEqual(calls.last()["result_json"]["execution"], {"exit_code": 1, "timed_out": False, "qualified": False})

    async def test_a_passing_run_carries_exit_zero_and_qualification(self):
        finished, _ = await self.run_profile("bash", {"exit_code": 0, "timed_out": False, "qualified": True})
        self.assertEqual((finished["exit_code"], finished["qualified"]), (0, True))

    async def test_a_timeout_is_labelled(self):
        finished, _ = await self.run_profile("bash", {"exit_code": -9, "timed_out": True, "qualified": False})
        self.assertEqual((finished["exit_code"], finished["timed_out"]), (-9, True))

    async def test_missing_or_mistyped_labels_are_omitted(self):
        finished, calls = await self.run_profile("bash", {"exit_code": True, "timed_out": "no", "qualified": 1})
        for key in ("exit_code", "timed_out", "qualified"):
            self.assertNotIn(key, finished)
        self.assertNotIn("execution", calls.last()["result_json"])

    async def test_other_tools_never_carry_execution_labels(self):
        async def read(args, ctx):
            return ok(exit_code=3, timed_out=True, qualified=True)
        gateway, journal, _, _ = make({"read_repo_file": read})
        await gateway.execute("read_repo_file", args(path="a.py"), "c1")
        self.assertFalse({"exit_code", "timed_out", "qualified"} & set(journal.of("tool.finished")[0]))

    def test_label_extraction_is_strictly_typed(self):
        self.assertEqual(execution_labels("bash", {"exit_code": 0, "stdout": "x"}), {"exit_code": 0})
        self.assertEqual(execution_labels("bash", None), {})
        self.assertEqual(execution_labels("web_search", {"exit_code": 0}), {})


class SuppressionTests(LifecycleCase):
    async def test_self_corrections_and_internal_reads_emit_nothing(self):
        gateway, journal, calls, _ = make({})
        await gateway.execute("read_repo_file", "{not json", "bad")            # SCHEMA_INVALID
        await gateway.execute("read_repo_file", args(path="a.py"), "c1")
        await gateway.execute("read_task_input", args(input_kind="goal"), "c3")
        self.assertEqual([kind for kind, _ in journal.events], ["tool.started", "tool.finished"])
        self.assertEqual(journal.of("tool.started")[0]["detail"], "a.py")
        await gateway.execute("read_repo_file", args(path="a.py"), "c2")       # the same call again is a normal step
        self.assertEqual([kind for kind, _ in journal.events], ["tool.started", "tool.finished"] * 2)
        self.assertEqual(calls.last()["status"], "SUCCEEDED")

    async def test_an_unregistered_or_unauthorized_name_is_hidden(self):
        gateway, journal, calls, _ = make({}, allowed=("read_repo_file",))
        await gateway.execute("made_up_tool", args(), "c1")
        await gateway.execute("bash", args(command="ls"), "c2")
        self.assertEqual(journal.events, [])
        self.assertEqual({fields["tool_name"] for _, fields in calls.intents}, {"unknown"})

    def test_the_shared_definition(self):
        self.assertIn("SCHEMA_INVALID", SELF_CORRECTION_CODES)
        self.assertEqual(HIDDEN_TOOLS, {"read_task_input", "unknown"})
        self.assertTrue(is_visible_call("bash"))
        self.assertTrue(is_visible_call("web_search", "SCOPE_DENIED"))
        self.assertFalse(is_visible_call("web_search", "SCHEMA_INVALID"))
        self.assertFalse(is_visible_call("read_task_input"))
        self.assertFalse(is_visible_call("unknown", "TOOL_FAILED"))


class CallLifecycleTests(TestCase):
    def test_finish_is_sent_once_and_never_without_a_visible_start(self):
        sent = []
        calls = CallLifecycle(lambda kind, payload: sent.append(kind), tool="web_search", tool_call_id=uuid4(),
                              agent_run_id=uuid4(), operation_key="k")
        calls.finish("completed")
        calls.finish("failed")
        self.assertEqual(sent, ["tool.started", "tool.finished"])
        hidden = CallLifecycle(lambda kind, payload: sent.append(kind), tool="web_search", tool_call_id=uuid4(),
                               agent_run_id=uuid4(), operation_key="k")
        hidden.finish("failed", error_code="SCHEMA_INVALID")
        self.assertEqual(sent, ["tool.started", "tool.finished"])

    def test_a_start_that_could_not_be_written_is_not_followed_by_a_finish(self):
        sent = []

        def flaky(kind, payload):
            if kind == "tool.started":
                raise RuntimeError("down")
            sent.append(kind)
        calls = CallLifecycle(flaky, tool="web_search", tool_call_id=uuid4(), agent_run_id=uuid4(), operation_key="k")
        with self.assertLogs("agentforge.tools.lifecycle", "WARNING"):
            calls.start()
            calls.finish("completed")
        self.assertEqual(sent, [])

    def test_outcome_mapping(self):
        self.assertEqual([outcome_from_row(status, None) for status in ("SUCCEEDED", "FAILED", "BLOCKED", "UNKNOWN")],
                         ["completed", "failed", "blocked", "unknown"])
        self.assertEqual(outcome_from_row("FAILED", "USER_CANCELLED"), "cancelled")
        self.assertEqual(outcome_from_row("BLOCKED", "USER_CANCELLED"), "cancelled")
        self.assertEqual(outcome_from_row("UNKNOWN", "USER_CANCELLED"), "unknown")

    def test_finished_payload_status_is_ok_only_when_completed(self):
        for outcome in ("failed", "blocked", "cancelled", "unknown"):
            self.assertEqual(finished_payload(tool="t", outcome=outcome, call_id="c", tool_call_id="i", agent_run_id="r")["status"], "error")
        self.assertEqual(finished_payload(tool="t", outcome="completed", call_id="c", tool_call_id="i", agent_run_id="r")["status"], "ok")


class FakeSession:
    """Just enough of a Session for `reconcile_open_calls`: it replays canned rows."""

    def __init__(self, events, rows):
        self.events, self.rows = events, rows

    def execute(self, statement):
        return NS(all=lambda: [(kind, dict(payload)) for kind, payload in self.events])

    def scalars(self, statement):
        return list(self.rows.values())


def row(status, *, tool="bash", error_code=None, result=None, evidence=(), run=None):
    return NS(id=uuid4(), status=status, tool_name=tool, error_code=error_code, result_json=result,
              evidence_ids=list(evidence), agent_run_id=run or uuid4())


def started(row_, call_id="c"):
    return ("tool.started", {"tool": row_.tool_name, "call_id": call_id, "tool_call_id": str(row_.id),
                             "agent_run_id": str(row_.agent_run_id)})


def finished(row_):
    return ("tool.finished", {"tool": row_.tool_name, "tool_call_id": str(row_.id), "outcome": "completed"})


class ReconcileTests(TestCase):
    def reconcile(self, events, rows, *, status="FAILED", error_code=None):
        appended = []
        session = FakeSession(events, {str(item.id): item for item in rows})
        with patch("agentforge.tools.lifecycle.append_event", lambda s, task, kind, payload: appended.append((kind, payload))):
            count = reconcile_open_calls(session, NS(id=uuid4()), final_status=status, error_code=error_code)
        self.assertEqual(count, len(appended))
        return appended

    def test_a_crashed_call_becomes_unknown_never_a_success(self):
        crashed = row("INTENT")
        (kind, payload), = self.reconcile([started(crashed)], [crashed])
        self.assertEqual(kind, "tool.finished")
        self.assertEqual((payload["outcome"], payload["status"], payload["error_code"]), ("unknown", "error", "INTERRUPTED"))
        self.assertEqual((payload["tool_call_id"], payload["agent_run_id"], payload["call_id"], payload["tool"]),
                         (str(crashed.id), str(crashed.agent_run_id), "c", "bash"))
        self.assertTrue(set(payload) <= _KEYS)

    def test_a_cancelled_task_cancels_the_calls_it_left_running(self):
        left = row("INTENT")
        (_, payload), = self.reconcile([started(left)], [left], status="CANCELLED", error_code="USER_CANCELLED")
        self.assertEqual((payload["outcome"], payload["error_code"]), ("cancelled", "USER_CANCELLED"))

    def test_a_call_whose_outcome_was_persisted_reports_that_outcome(self):
        done = row("SUCCEEDED", result={"duration_ms": 120, "artifact_ids": ["a1"],
                                        "execution": {"exit_code": 1, "timed_out": False, "qualified": False, "stdout": SECRET}},
                   evidence=["e1"])
        blocked = row("BLOCKED", tool="web_search", error_code="SCOPE_DENIED")
        cancelled = row("FAILED", tool="web_search", error_code="USER_CANCELLED")
        unknown = row("UNKNOWN", error_code="UNKNOWN_REMOTE_OUTCOME")
        out = self.reconcile([started(item) for item in (done, blocked, cancelled, unknown)], [done, blocked, cancelled, unknown])
        self.assertEqual([item["outcome"] for _, item in out], ["completed", "blocked", "cancelled", "unknown"])
        self.assertEqual(out[0][1]["status"], "ok")
        self.assertEqual((out[0][1]["exit_code"], out[0][1]["timed_out"], out[0][1]["qualified"], out[0][1]["duration_ms"]),
                         (1, False, False, 120))
        self.assertEqual((out[0][1]["evidence_ids"], out[0][1]["artifact_ids"]), (["e1"], ["a1"]))
        self.assertNotIn(SECRET, json.dumps(out))
        # A finished row keeps its own cause even when the task was cancelled afterwards.
        self.assertEqual(out[1][1]["error_code"], "SCOPE_DENIED")

    def test_settled_calls_are_left_alone_and_reconciling_twice_adds_nothing(self):
        settled, left = row("SUCCEEDED"), row("INTENT")
        events = [started(settled), finished(settled), started(left)]
        first = self.reconcile(events, [settled, left])
        self.assertEqual([item["tool_call_id"] for _, item in first], [str(left.id)])
        again = self.reconcile(events + [("tool.finished", first[0][1])], [settled, left])
        self.assertEqual(again, [])

    def test_calls_are_settled_in_the_order_they_started(self):
        first, second = row("INTENT"), row("INTENT")
        out = self.reconcile([started(first), started(second)], [second, first])
        self.assertEqual([item["tool_call_id"] for _, item in out], [str(first.id), str(second.id)])

    def test_legacy_events_without_a_tool_call_id_are_ignored(self):
        legacy = [("tool.started", {"tool": "web_search", "call_id": "old"}), ("tool.finished", {"tool": "web_search", "call_id": "old"}),
                  ("tool.started", {"tool": "web_search", "call_id": "older"})]
        self.assertEqual(self.reconcile(legacy, []), [])

    def test_a_start_with_no_row_is_unknown(self):
        orphan = row("INTENT")
        (_, payload), = self.reconcile([started(orphan)], [])
        self.assertEqual(payload["outcome"], "unknown")
