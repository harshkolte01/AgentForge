"""Tool gateway: bound identifiers, precise error feedback, effect-unknown handling, concurrency and the ledger.

The gateway does not count or cap calls: a model may repeat a call or make as many as it likes. The one exception is an
identical call that already failed in a way that cannot change (DETERMINISTIC_FAILURES), which is answered from that
failure instead of being sent again."""
import asyncio
import json
from decimal import Decimal
from types import SimpleNamespace as NS
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from agentforge.contracts.agent_definition import Budget
from agentforge.tools.catalog import ToolCatalog
from agentforge.tools.contracts import ToolOutcome
from agentforge.tools.errors import ToolError
from agentforge.tools.gateway import ToolGateway, UnknownToolOutcome

REPO, SHA = "owner/name", "a" * 40
ALL = ("read_repo_file", "list_repo_files", "read", "write", "grep")


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


def make(handlers=None, *, bound=None):
    handlers = handlers or {}
    catalog = ToolCatalog()
    for name in ALL:
        async def default(args, ctx):
            return ok(echo=args.model_dump(mode="json"))
        catalog.register(name, handlers.get(name, default), max_cost_usd=Decimal(0), timeout_sec=5)
    calls, budgets = FakeCalls(), FakeBudgets()
    gateway = ToolGateway(
        catalog=catalog, calls=calls, budgets=budgets, resources=FakeResources(bound), reauthorize=AsyncMock(),
        ctx=NS(task_id=uuid4(), parent_run_id=None), scope=NS(allowed_tools=ALL, permissions=NS(), budget=Budget()),
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
        self.assertEqual(set(schema["read"]["properties"]), {"path", "offset", "limit"})  # nothing is bound for workspace tools
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

    async def test_invalid_json_says_where_the_parser_stopped_without_echoing_the_arguments(self):
        gateway, _, _ = make()
        outcome = await gateway.execute("write", '{"path": "a.py", "content": "SECRET-VALUE" "more"}', "c1")
        self.assertEqual(outcome.error_code, "SCHEMA_INVALID")
        self.assertIn("Expecting ',' delimiter at character 43", outcome.error_detail)
        self.assertNotIn("SECRET-VALUE", outcome.error_detail)

    async def test_oversized_arguments_are_a_refusal_the_model_can_act_on_not_a_failed_run(self):
        ran = []
        async def write(args, ctx):
            ran.append(args)
            return ok()
        gateway, calls, budgets = make({"write": write})
        outcome = await gateway.execute("write", args(path="big.txt", content="x" * 140_000), "c1")
        self.assertEqual((outcome.status, outcome.error_code, outcome.cost_usd), ("error", "INVALID_ARGUMENT", Decimal(0)))
        self.assertIn("128 KB", outcome.error_detail)
        self.assertIn("parts", outcome.error_detail)
        self.assertEqual((ran, budgets.usage), ([], []))
        self.assertEqual((calls.last()["status"], calls.last()["error_code"]), ("BLOCKED", "INVALID_ARGUMENT"))
        # The same tool still works with a call of a reasonable size.
        self.assertEqual((await gateway.execute("write", args(path="big.txt", content="x" * 1000), "c2")).status, "ok")

    async def test_null_for_an_optional_field_means_omitted(self):
        gateway, _, _ = make()
        outcome = await gateway.execute("read_repo_file", args(path="a.py", line_start=None, max_lines=None), "c1")
        self.assertEqual(outcome.status, "ok")
        self.assertEqual(outcome.data["echo"]["max_lines"], 200)

    async def test_unknown_and_unlisted_tools_get_distinct_codes(self):
        gateway, _, _ = make()
        self.assertEqual((await gateway.execute("rm_rf", "{}", "c1")).error_code, "TOOL_UNAVAILABLE")
        gateway.scope = NS(**{**gateway.scope.__dict__, "allowed_tools": ("read_repo_file",)})
        self.assertEqual((await gateway.execute("bash", "{}", "c2")).error_code, "TOOL_UNAVAILABLE")  # real tool, not installed
        self.assertEqual((await gateway.execute("list_repo_files", "{}", "c3")).error_code, "SCOPE_DENIED")

    async def test_a_typed_adapter_error_is_definite_even_for_an_effectful_tool(self):
        async def reject(args, ctx):
            raise ToolError("STALE_WRITE", "Read the file again.")
        gateway, calls, budgets = make({"write": reject})
        outcome = await gateway.execute("write", args(path="a.txt", content="SECRET-BODY"), "c1")
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
        gateway, calls, budgets = make({"write": explode})
        with self.assertRaises(UnknownToolOutcome) as caught:
            await gateway.execute("write", args(path="a.txt", content="x"), "c1")
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

    async def test_an_identical_call_is_run_every_time_it_is_made(self):
        runs = []
        async def counting(args, ctx):
            runs.append(args.path)
            return ok(done=True)
        gateway, calls, _ = make({"read_repo_file": counting})
        for index in range(30):
            outcome = await gateway.execute("read_repo_file", args(path="a.py"), f"c{index}")
            self.assertEqual(outcome.status, "ok")
        self.assertEqual(len(runs), 30)  # no repeat refusal and no call limit
        self.assertEqual(len(calls.outcomes), 30)
        self.assertEqual(calls.last()["status"], "SUCCEEDED")

    async def test_failing_calls_are_never_cut_off_by_a_failure_streak(self):
        async def fail(args, ctx):
            raise ToolError("FILE_NOT_FOUND", "no such file")
        gateway, _, _ = make({"read_repo_file": fail})
        for index in range(12):
            outcome = await gateway.execute("read_repo_file", args(path=f"{index}.py"), f"c{index}")
            self.assertEqual((outcome.status, outcome.error_code), ("error", "FILE_NOT_FOUND"))
        for gone in ("consecutive_errors", "calls_left", "forget", "repeated_failure_notice"):
            self.assertFalse(hasattr(gateway, gone), gone)

    async def test_a_failed_read_can_be_retried_and_succeeds(self):
        attempts = []
        async def flaky(args, ctx):
            attempts.append(1)
            if len(attempts) == 1:
                return ToolOutcome(status="error", cost_usd=Decimal(0), error_code="FETCH_NETWORK_ERROR")
            return ok(done=True)
        gateway, _, _ = make({"read_repo_file": flaky})
        self.assertEqual((await gateway.execute("read_repo_file", args(path="a.py"), "c1")).status, "error")
        self.assertEqual((await gateway.execute("read_repo_file", args(path="a.py"), "c2")).status, "ok")

    async def test_an_identical_call_that_failed_for_good_is_answered_from_that_failure(self):
        runs = []
        async def refused(args, ctx):
            runs.append(args.path)
            return ToolOutcome(status="error", cost_usd=Decimal(0), error_code="FETCH_HTTP_ERROR")
        gateway, calls, budgets = make({"read_repo_file": refused})
        first = await gateway.execute("read_repo_file", args(path="a.py", line_start=1), "c1")
        self.assertEqual((first.error_code, calls.last()["status"]), ("FETCH_HTTP_ERROR", "FAILED"))
        # The same call, with its keys in another order, is not sent again.
        again = await gateway.execute("read_repo_file", '{"line_start": 1, "path": "a.py"}', "c2")
        self.assertEqual((again.status, again.error_code, again.cost_usd), ("error", "REPEATED_FAILED_CALL", Decimal(0)))
        self.assertIn("FETCH_HTTP_ERROR", again.error_detail)
        self.assertEqual((runs, calls.last()["status"], calls.last()["error_code"]), (["a.py"], "BLOCKED", "REPEATED_FAILED_CALL"))
        self.assertEqual(len(budgets.usage), 1)  # nothing was claimed for the repeat
        # Different arguments are a different call and still run.
        await gateway.execute("read_repo_file", args(path="b.py", line_start=1), "c3")
        self.assertEqual(runs, ["a.py", "b.py"])

    async def test_failures_that_can_change_are_never_remembered(self):
        runs = []
        async def failing(args, ctx):
            runs.append(1)
            return ToolOutcome(status="error", cost_usd=Decimal(0), error_code="FETCH_NETWORK_ERROR")
        gateway, _, _ = make({"read_repo_file": failing})
        for index in range(3):
            self.assertEqual((await gateway.execute("read_repo_file", args(path="a.py"), f"c{index}")).error_code,
                             "FETCH_NETWORK_ERROR")
        self.assertEqual(len(runs), 3)

    async def test_a_remembered_failure_belongs_to_one_run(self):
        async def refused(args, ctx):
            return ToolOutcome(status="error", cost_usd=Decimal(0), error_code="FETCH_CONTENT_TYPE_UNSUPPORTED")
        first, _, _ = make({"read_repo_file": refused})
        await first.execute("read_repo_file", args(path="a.py"), "c1")
        second, _, _ = make({"read_repo_file": refused})
        self.assertEqual((await second.execute("read_repo_file", args(path="a.py"), "c1")).error_code,
                         "FETCH_CONTENT_TYPE_UNSUPPORTED")

    async def test_independent_reads_overlap_while_effects_run_one_at_a_time(self):
        running = {"now": 0, "peak": 0}
        async def slow(args, ctx):
            running["now"] += 1
            running["peak"] = max(running["peak"], running["now"])
            await asyncio.sleep(0.03)
            running["now"] -= 1
            return ok()
        gateway, _, _ = make({"read_repo_file": slow, "write": slow})
        await asyncio.gather(*(gateway.execute("read_repo_file", args(path=f"{i}.py"), f"r{i}") for i in range(4)))
        self.assertGreaterEqual(running["peak"], 2)
        running["peak"] = 0
        await asyncio.gather(*(gateway.execute("write", args(path=f"{i}.py", content="x"), f"w{i}")
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
    """Paid tools still run one at a time so recorded usage stays attributable; the gateway never rejects for spend."""

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
        self.assertFalse(gateway.is_parallel_safe("write"))  # effect
        await asyncio.gather(*(gateway.execute("read_repo_file", args(path=f"{i}.py"), f"p{i}") for i in range(4)))
        self.assertEqual(running["peak"], 1)

    async def test_a_refusal_from_the_budget_service_aborts_the_call_and_is_recorded_as_blocked(self):
        from agentforge.subagents.budget import BudgetExceeded
        class Refusing(FakeBudgets):
            def authorize_operation(self, *a, **k):
                raise BudgetExceeded("child_budget_exceeded")
        gateway, calls = self.paid_gateway(lambda *a: None, budgets=Refusing())
        with self.assertRaises(BudgetExceeded):
            await gateway.execute("read_repo_file", args(path="a.py"), "c1")
        self.assertEqual((calls.last()["status"], calls.last()["error_code"]), ("BLOCKED", "BUDGET_EXCEEDED"))

    async def test_a_finished_or_cancelled_run_still_aborts(self):
        from agentforge.subagents.budget import BudgetExceeded
        class Settled(FakeBudgets):
            def check_active(self, reservation_id):
                raise BudgetExceeded("reservation_settled")
        gateway, _ = self.paid_gateway(lambda *a: None, budgets=Settled())
        with self.assertRaises(BudgetExceeded):
            await gateway.execute("read_repo_file", args(path="a.py"), "c1")
