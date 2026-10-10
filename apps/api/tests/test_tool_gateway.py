"""Tool gateway: bound identifiers, precise error feedback, duplicate refusal, limits and concurrency."""
import asyncio
import json
from decimal import Decimal
from types import SimpleNamespace as NS
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from agentforge.contracts.agent_definition import Budget
from agentforge.subagents.scope import ScopeDenied
from agentforge.tools.catalog import ToolCatalog
from agentforge.tools.contracts import ToolOutcome
from agentforge.tools.errors import ToolError
from agentforge.tools.gateway import MAX_CONSECUTIVE_ERRORS, ToolGateway, UnknownToolOutcome

REPO, SHA = "owner/name", "a" * 40
ALL = ("read_repo_file", "list_repo_files", "read_sandbox_file", "write_sandbox_file", "get_test_results")


class FakeCalls:
    def __init__(self):
        self.intents, self.outcomes = [], {}

    def record_intent(self, **kwargs):
        call_id = uuid4()
        self.intents.append((call_id, kwargs))
        return call_id

    def record_outcome(self, *, call_id, status, result_json=None, error_code=None, evidence_ids=()):
        self.outcomes[call_id] = {"status": status, "result_json": result_json, "error_code": error_code}

    def last(self):
        return list(self.outcomes.values())[-1]


class FakeBudgets:
    def __init__(self):
        self.usage = []

    def check_active(self, reservation_id): pass

    def authorize_operation(self, *args, **kwargs): pass

    def record_usage(self, reservation_id, key, cost, usage=None):
        self.usage.append(cost)


class FakeResources:
    def __init__(self, bound=None):
        self.bound = {"repo_id": REPO, "ref": SHA} if bound is None else bound

    def bindings(self, ctx, name):
        return self.bound

    async def verify_tool_inputs(self, ctx, name, args): pass

    async def verify_references(self, ctx, *, evidence_ids, artifact_ids): pass


def ok(**data):
    return ToolOutcome(status="ok", data=data, cost_usd=Decimal(0))


def make(handlers=None, *, max_tool_calls=20, bound=None):
    handlers = handlers or {}
    catalog = ToolCatalog()
    for name in ALL:
        async def default(args, ctx):
            return ok(echo=args.model_dump(mode="json"))
        catalog.register(name, handlers.get(name, default), max_cost_usd=Decimal(0), timeout_sec=5)
    calls, budgets = FakeCalls(), FakeBudgets()
    gateway = ToolGateway(
        catalog=catalog, calls=calls, budgets=budgets, resources=FakeResources(bound), reauthorize=AsyncMock(),
        ctx=NS(task_id=uuid4()), scope=NS(allowed_tools=ALL, permissions=NS(),
            budget=Budget(max_steps=10, max_tool_calls=max_tool_calls)),
        run_id=uuid4(), reservation_id=uuid4())
    return gateway, calls, budgets


def args(**kwargs):
    return json.dumps(kwargs)


class GatewayTests(IsolatedAsyncioTestCase):
    def setUp(self):
        patcher = patch("agentforge.tools.gateway.authorize_tool")
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_bound_identifiers_are_hidden_from_the_schema_and_filled_in(self):
        gateway, _, _ = make()
        schema = {item["function"]["name"]: item["function"]["parameters"] for item in gateway.schemas()}
        self.assertEqual(set(schema["read_repo_file"]["properties"]), {"path", "line_start", "max_lines"})
        self.assertEqual(schema["read_repo_file"]["required"], ["path"])
        self.assertIn("run_id", schema["get_test_results"]["properties"])
        self.assertNotIn("task_id", schema["get_test_results"]["properties"])  # always the task's own
        outcome = await gateway.execute("read_repo_file", args(path="a.py"), "c1")
        self.assertEqual(outcome.status, "ok")
        self.assertEqual((outcome.data["echo"]["repo_id"], outcome.data["echo"]["ref"]), (REPO, SHA))

    async def test_a_conflicting_identifier_is_refused_but_the_right_one_is_tolerated(self):
        gateway, _, _ = make()
        same = await gateway.execute("read_repo_file", args(path="a.py", repo_id=REPO, ref=SHA), "c1")
        self.assertEqual(same.status, "ok")
        wrong = await gateway.execute("read_repo_file", args(path="b.py", repo_id="other/repo"), "c2")
        self.assertEqual((wrong.status, wrong.error_code), ("error", "SCOPE_DENIED"))
        self.assertIn("repo_id", wrong.error_detail)

    async def test_without_a_resolver_binding_the_model_still_supplies_the_field(self):
        gateway, _, _ = make(bound={})
        schema = {item["function"]["name"]: item["function"]["parameters"] for item in gateway.schemas()}
        self.assertIn("repo_id", schema["read_repo_file"]["properties"])
        missing = await gateway.execute("read_repo_file", args(path="a.py"), "c1")
        self.assertEqual(missing.error_code, "SCHEMA_INVALID")
        self.assertIn("repo_id", missing.error_detail)

    async def test_schema_errors_name_each_field_without_echoing_values(self):
        gateway, calls, _ = make()
        outcome = await gateway.execute("read_repo_file",
            args(path="a.py", line_start="SECRET-VALUE", max_lines=9999, bogus=1), "c1")
        self.assertEqual(outcome.error_code, "SCHEMA_INVALID")
        for field in ("line_start", "max_lines", "bogus"):
            self.assertIn(field, outcome.error_detail)
        self.assertNotIn("SECRET-VALUE", outcome.error_detail)
        self.assertNotIn("SECRET-VALUE", json.dumps(outcome.data))
        self.assertEqual({issue["field"] for issue in outcome.data["issues"]}, {"line_start", "max_lines", "bogus"})
        self.assertEqual(calls.last()["status"], "BLOCKED")
        self.assertNotIn("SECRET-VALUE", json.dumps(calls.last()["result_json"]))

    async def test_malformed_json_and_non_objects_are_schema_errors_not_crashes(self):
        gateway, _, _ = make()
        for raw in ("{not json", "[1, 2]", '"text"'):
            outcome = await gateway.execute("read_repo_file", raw, f"c{raw[:3]}")
            self.assertEqual(outcome.error_code, "SCHEMA_INVALID", raw)

    async def test_null_for_an_optional_field_means_omitted(self):
        gateway, _, _ = make()
        outcome = await gateway.execute("read_repo_file", args(path="a.py", line_start=None, max_lines=None), "c1")
        self.assertEqual(outcome.status, "ok")
        self.assertEqual(outcome.data["echo"]["max_lines"], 200)

    async def test_unknown_and_unlisted_tools_get_distinct_codes(self):
        gateway, _, _ = make()
        self.assertEqual((await gateway.execute("rm_rf", "{}", "c1")).error_code, "TOOL_UNAVAILABLE")
        gateway.scope = NS(**{**gateway.scope.__dict__, "allowed_tools": ("read_repo_file",)})
        self.assertEqual((await gateway.execute("git_diff", "{}", "c2")).error_code, "TOOL_UNAVAILABLE")
        self.assertEqual((await gateway.execute("list_repo_files", "{}", "c3")).error_code, "SCOPE_DENIED")

    async def test_a_typed_adapter_error_is_definite_even_for_an_effectful_tool(self):
        async def reject(args, ctx):
            raise ToolError("STALE_WRITE", "Read the file again.")
        gateway, calls, budgets = make({"write_sandbox_file": reject})
        outcome = await gateway.execute("write_sandbox_file",
            args(sandbox_id="s", path="a.txt", content="SECRET-BODY"), "c1")
        self.assertEqual((outcome.status, outcome.error_code, outcome.error_detail), ("error", "STALE_WRITE", "Read the file again."))
        self.assertEqual(outcome.cost_usd, Decimal(0))
        self.assertEqual(calls.last()["status"], "FAILED")  # not UNKNOWN: nothing happened
        self.assertEqual(budgets.usage[-1], Decimal(0))
        recorded = calls.last()["result_json"]["arguments"]
        self.assertNotIn("SECRET-BODY", json.dumps(recorded))
        self.assertEqual(recorded["content_chars"], len("SECRET-BODY"))

    async def test_an_untyped_failure_of_an_effectful_tool_stops_the_run_as_unknown(self):
        async def explode(args, ctx):
            raise RuntimeError("provider said something private")
        gateway, calls, budgets = make({"write_sandbox_file": explode})
        with self.assertRaises(UnknownToolOutcome) as caught:
            await gateway.execute("write_sandbox_file", args(sandbox_id="s", path="a.txt", content="x"), "c1")
        self.assertNotIn("private", str(caught.exception))
        self.assertEqual(calls.last()["status"], "UNKNOWN")
        self.assertIsNone(budgets.usage[-1])  # unknown charge keeps the cap

    async def test_an_untyped_failure_of_a_read_is_an_ordinary_error(self):
        async def explode(args, ctx):
            raise RuntimeError("boom")
        gateway, _, _ = make({"read_repo_file": explode})
        outcome = await gateway.execute("read_repo_file", args(path="a.py"), "c1")
        self.assertEqual(outcome.error_code, "TOOL_FAILED")
        self.assertIsNone(outcome.error_detail)

    async def test_an_identical_successful_read_is_refused_until_something_could_have_changed(self):
        gateway, calls, _ = make()
        first = await gateway.execute("read_repo_file", args(path="a.py"), "c1")
        again = await gateway.execute("read_repo_file", args(path="a.py", line_start=1), "c2")  # same after defaults
        self.assertEqual(first.status, "ok")
        self.assertEqual((again.status, again.error_code), ("error", "DUPLICATE_CALL"))
        self.assertIn("call number 1", again.error_detail)
        self.assertEqual((await gateway.execute("read_repo_file", args(path="b.py"), "c3")).status, "ok")
        await gateway.execute("write_sandbox_file", args(sandbox_id="s", path="a.py", content="x"), "c4")
        self.assertEqual((await gateway.execute("read_repo_file", args(path="a.py"), "c5")).status, "ok")
        self.assertEqual(calls.last()["status"], "SUCCEEDED")

    async def test_duplicates_do_not_count_as_failures_and_failed_reads_can_be_retried(self):
        attempts = []
        async def flaky(args, ctx):
            attempts.append(1)
            if len(attempts) == 1:
                return ToolOutcome(status="error", cost_usd=Decimal(0), error_code="FETCH_NETWORK_ERROR")
            return ok(done=True)
        gateway, _, _ = make({"read_repo_file": flaky})
        self.assertEqual((await gateway.execute("read_repo_file", args(path="a.py"), "c1")).status, "error")
        self.assertEqual((await gateway.execute("read_repo_file", args(path="a.py"), "c2")).status, "ok")
        self.assertEqual((await gateway.execute("read_repo_file", args(path="a.py"), "c3")).error_code, "DUPLICATE_CALL")
        self.assertEqual(gateway.consecutive_errors, 0)

    async def test_a_call_whose_result_was_compacted_away_may_be_repeated(self):
        gateway, _, _ = make()
        await gateway.execute("read_repo_file", args(path="a.py"), "c1")
        await gateway.execute("read_repo_file", args(path="b.py"), "c2")
        self.assertEqual((await gateway.execute("read_repo_file", args(path="a.py"), "c3")).error_code, "DUPLICATE_CALL")
        gateway.forget(["c1", "unknown-id"])
        self.assertEqual((await gateway.execute("read_repo_file", args(path="a.py"), "c4")).status, "ok")
        self.assertEqual((await gateway.execute("read_repo_file", args(path="b.py"), "c5")).error_code, "DUPLICATE_CALL")

    async def test_effectful_calls_are_never_deduplicated(self):
        gateway, _, _ = make()
        for index in range(2):
            outcome = await gateway.execute("write_sandbox_file", args(sandbox_id="s", path="a.py", content="x"), f"c{index}")
            self.assertEqual(outcome.status, "ok")

    async def test_the_tool_limit_returns_an_error_instead_of_aborting_the_run(self):
        gateway, calls, _ = make(max_tool_calls=2)
        await gateway.execute("read_repo_file", args(path="a.py"), "c1")
        await gateway.execute("read_repo_file", args(path="b.py"), "c2")
        self.assertEqual(gateway.calls_left, 0)
        outcome = await gateway.execute("read_repo_file", args(path="c.py"), "c3")
        self.assertEqual(outcome.error_code, "TOOL_LIMIT_REACHED")
        self.assertEqual(calls.last()["status"], "BLOCKED")

    async def test_consecutive_failures_are_counted_noticed_and_reset_by_success(self):
        async def fail(args, ctx):
            raise ToolError("FILE_NOT_FOUND", "no such file")
        gateway, _, _ = make({"read_repo_file": fail})
        for index in range(3):
            await gateway.execute("read_repo_file", args(path=f"{index}.py"), f"c{index}")
        self.assertEqual(gateway.consecutive_errors, 3)
        self.assertIn("failed 3 times", gateway.repeated_failure_notice("read_repo_file"))
        self.assertIsNone(gateway.repeated_failure_notice("list_repo_files"))
        await gateway.execute("list_repo_files", args(), "ok1")
        self.assertEqual(gateway.consecutive_errors, 0)
        self.assertLess(gateway.consecutive_errors, MAX_CONSECUTIVE_ERRORS)

    async def test_independent_reads_overlap_while_effects_run_one_at_a_time(self):
        running = {"now": 0, "peak": 0}
        async def slow(args, ctx):
            running["now"] += 1
            running["peak"] = max(running["peak"], running["now"])
            await asyncio.sleep(0.03)
            running["now"] -= 1
            return ok()
        gateway, _, _ = make({"read_repo_file": slow, "write_sandbox_file": slow})
        await asyncio.gather(*(gateway.execute("read_repo_file", args(path=f"{i}.py"), f"r{i}") for i in range(4)))
        self.assertGreaterEqual(running["peak"], 2)
        running["peak"] = 0
        await asyncio.gather(*(gateway.execute("write_sandbox_file", args(sandbox_id="s", path=f"{i}.py", content="x"), f"w{i}")
                               for i in range(4)))
        self.assertEqual(running["peak"], 1)

    async def test_the_ledger_keeps_a_digest_and_structure_never_the_raw_arguments(self):
        gateway, calls, _ = make()
        await gateway.execute("read_repo_file", args(path="src/a.py", line_start=5), "c1")
        _, intent = calls.intents[0]
        self.assertEqual(set(intent["intent_json_sanitized"]), {"arguments_sha256"})
        recorded = calls.last()["result_json"]
        self.assertEqual(recorded["arguments"]["path"], "src/a.py")
        self.assertEqual(recorded["arguments"]["line_start"], 5)
        self.assertIn("duration_ms", recorded)


class PaidToolBudgetTests(IsolatedAsyncioTestCase):
    """The stress-test failure: six paid searches requested at once overdrew a stage's slice and aborted the run."""

    def setUp(self):
        patcher = patch("agentforge.tools.gateway.authorize_tool")
        patcher.start()
        self.addCleanup(patcher.stop)

    def paid_gateway(self, handler, budgets=None):
        gateway, calls, fake = make({"read_repo_file": handler})
        gateway.catalog._tools["read_repo_file"] = type(gateway.catalog._tools["read_repo_file"])(
            "read_repo_file", handler, Decimal("0.02"), 5)
        if budgets is not None:
            gateway.budgets = budgets
        return gateway, calls

    async def test_paid_calls_run_one_at_a_time_and_are_not_parallel_safe(self):
        running = {"now": 0, "peak": 0}
        async def slow(args, ctx):
            running["now"] += 1
            running["peak"] = max(running["peak"], running["now"])
            await asyncio.sleep(0.03)
            running["now"] -= 1
            return ok()
        gateway, _ = self.paid_gateway(slow)
        self.assertFalse(gateway.is_parallel_safe("read_repo_file"))  # paid
        self.assertTrue(gateway.is_parallel_safe("list_repo_files"))  # free read
        self.assertFalse(gateway.is_parallel_safe("write_sandbox_file"))  # effect
        self.assertEqual(gateway.max_tool_cost, Decimal("0.02"))
        await asyncio.gather(*(gateway.execute("read_repo_file", args(path=f"{i}.py"), f"p{i}") for i in range(4)))
        self.assertEqual(running["peak"], 1)

    async def test_running_out_of_budget_is_an_outcome_the_model_can_act_on_not_an_abort(self):
        from agentforge.subagents.budget import BudgetExceeded
        class Refusing(FakeBudgets):
            def authorize_operation(self, *a, **k):
                raise BudgetExceeded("child_budget_exceeded")
        gateway, calls = self.paid_gateway(lambda *a: None, budgets=Refusing())
        outcome = await gateway.execute("read_repo_file", args(path="a.py"), "c1")
        self.assertEqual((outcome.status, outcome.error_code), ("error", "BUDGET_EXCEEDED"))
        self.assertIn("Submit what you have", outcome.error_detail)
        self.assertTrue(gateway.budget_exhausted)
        self.assertEqual(calls.last()["status"], "BLOCKED")

    async def test_a_finished_or_cancelled_run_still_aborts(self):
        from agentforge.subagents.budget import BudgetExceeded
        class Settled(FakeBudgets):
            def check_active(self, reservation_id):
                raise BudgetExceeded("reservation_settled")
        gateway, _ = self.paid_gateway(lambda *a: None, budgets=Settled())
        with self.assertRaises(BudgetExceeded):
            await gateway.execute("read_repo_file", args(path="a.py"), "c1")
        self.assertFalse(gateway.budget_exhausted)
