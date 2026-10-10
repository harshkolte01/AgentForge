"""The child loop end to end with a scripted model: finishing, repair feedback, limits, concurrency, compaction."""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace as NS
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from agentforge.contracts.agent_definition import Budget
from agentforge.llm.models import SUBMIT_RESULT, ModelContextLimit, ModelResponse, ModelUsage
from agentforge.subagents.context import RESULT_PREFIX
from agentforge.subagents.registry import load_builtin
from agentforge.subagents.runtime import ChildRuntime
from agentforge.subagents.scope import ScopeDenied
from agentforge.tools.contracts import ToolOutcome

from test_tool_gateway import ALL, args, make as make_gateway, ok

PASS = {"status": "PASS", "summary": "All done.", "evidence_ids": ["e1"]}


def calls(*items):
    """An assistant turn that requests tools: (name, arguments) pairs."""
    return {"role": "assistant", "content": None, "tool_calls": [
        {"id": f"call-{index}-{name}", "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}
        for index, (name, arguments) in enumerate(items)]}


def submit(payload, ident="submit-1"):
    return {"role": "assistant", "content": None, "tool_calls": [
        {"id": ident, "type": "function", "function": {"name": SUBMIT_RESULT, "arguments": json.dumps(payload)}}]}


def text(content):
    return {"role": "assistant", "content": content}


class ScriptedModel:
    model_id = "scripted"

    def __init__(self, turns, context_budget_tokens=None, max_input_tokens=None):
        self.turns, self.seen = list(turns), []
        self.context_budget_tokens, self.max_input_tokens = context_budget_tokens, max_input_tokens

    def maximum_request_cost(self, messages, tools):
        return Decimal("0.001")

    async def complete(self, messages, tools, on_text=None, tool_choice=None):
        self.seen.append({"messages": messages, "tools": [t["function"]["name"] for t in tools], "tool_choice": tool_choice})
        message = self.turns.pop(0)
        return ModelResponse(message, ModelUsage(1, 1, Decimal("0.0001")), "scripted", "tool_calls" if message.get("tool_calls") else "stop")


class FakeGateway:
    """Enough gateway for the loop: schemas, execution, and the limit signals the runtime reads."""

    def __init__(self, max_tool_calls=40, outcome=None):
        self.max_tool_calls, self.used, self.consecutive_errors = max_tool_calls, 0, 0
        self.executed = []
        self.outcome = outcome or (lambda name, raw: ok(text="x", evidence_ids=["e1"]))

    forgotten = ()

    def forget(self, ids):
        self.forgotten = tuple(sorted({*self.forgotten, *ids}))

    def schemas(self):
        return [{"type": "function", "function": {"name": "read_repo_file", "description": "d", "parameters": {"type": "object"}}}]

    @property
    def calls_left(self):
        return max(0, self.max_tool_calls - self.used)

    def is_parallel_safe(self, name):
        return True

    def repeated_failure_notice(self, name):
        return None

    async def execute(self, name, raw, call_id):
        self.used += 1
        self.executed.append((name, call_id))
        outcome = self.outcome(name, raw)
        return outcome


def evidence_outcome(*ids, data=None):
    return ToolOutcome(status="ok", data=data or {"text": "x"}, evidence_ids=ids, cost_usd=Decimal(0))


async def run(model, gateway, *, max_steps=8, max_tool_calls=40, wall=120, remaining=None, stream=None, definition=None, money=None):
    budget = Budget(max_steps=max_steps, max_tool_calls=max_tool_calls, max_llm_cost_usd=1.0, max_wall_time_sec=wall)
    reservation = NS(id=uuid4(), run_id=uuid4(), deadline=datetime.now(timezone.utc) + timedelta(seconds=remaining or wall))
    budgets = NS(check_active=lambda _id: None, authorize_operation=lambda *a, **k: None, record_usage=lambda *a, **k: None,
                 **({"remaining_usd": money} if money else {}))
    return await ChildRuntime().run(
        definition=definition or load_builtin("general-executor", 5), ctx=NS(task_id=uuid4()),
        scope=NS(budget=budget, allowed_tools=("read_repo_file",)), model=model, gateway=gateway, budgets=budgets,
        reservation=reservation, messages=[{"role": "developer", "content": "rules"}, {"role": "user", "content": "go"}],
        resources=NS(verify_references=AsyncMock()), reauthorize=AsyncMock(), stream=stream)


def tool_reply(model, call_index, position=-1):
    return model.seen[call_index]["messages"][position]


class FinishingTests(IsolatedAsyncioTestCase):
    async def test_a_tool_turn_then_submit_result_finishes_with_the_typed_result(self):
        model = ScriptedModel([calls(("read_repo_file", {"path": "a.py"})), submit(PASS)])
        gateway = FakeGateway(outcome=lambda n, r: evidence_outcome("e1"))
        result = await run(model, gateway)
        self.assertEqual((result.status, result.evidence_ids), ("PASS", ("e1",)))
        self.assertEqual(gateway.executed[0][0], "read_repo_file")
        self.assertEqual(model.seen[0]["tools"], ["read_repo_file", SUBMIT_RESULT])
        self.assertIsNone(model.seen[0]["tool_choice"])
        reply = tool_reply(model, 1)
        self.assertEqual(reply["role"], "tool")
        body = json.loads(reply["content"][len(RESULT_PREFIX):])
        self.assertEqual(body["limits"]["steps_left"], 7)
        self.assertEqual(body["limits"]["tool_calls_left"], 39)

    async def test_the_result_schema_is_the_submit_tools_parameters(self):
        model = ScriptedModel([submit(PASS)])
        captured = {}
        original = model.complete
        async def spy(messages, tools, **kwargs):
            captured["tool"] = next(t for t in tools if t["function"]["name"] == SUBMIT_RESULT)
            return await original(messages, tools, **kwargs)
        model.complete = spy
        await run(model, FakeGateway())
        properties = captured["tool"]["function"]["parameters"]["properties"]
        self.assertTrue({"status", "summary", "claims", "evidence_ids"} <= set(properties))

    async def test_nulls_for_omitted_optional_fields_are_accepted(self):
        payload = {**PASS, "observations": None, "next_action_suggestion": None, "claims": None}
        result = await run(ScriptedModel([submit(payload)]), FakeGateway())
        self.assertEqual(result.status, "PASS")

    async def test_a_plain_json_reply_still_works_for_models_that_ignore_the_tool(self):
        result = await run(ScriptedModel([text(json.dumps(PASS))]), FakeGateway())
        self.assertEqual(result.status, "PASS")

    async def test_a_prose_reply_is_forced_into_a_typed_submission_once(self):
        model = ScriptedModel([text("I think we are done."), submit(PASS)])
        result = await run(model, FakeGateway())
        self.assertEqual(result.status, "PASS")
        self.assertEqual(model.seen[1]["tools"], [SUBMIT_RESULT])
        self.assertEqual(model.seen[1]["tool_choice"]["function"]["name"], SUBMIT_RESULT)
        self.assertIn("submit_result", model.seen[1]["messages"][-1]["content"])

    async def test_a_second_prose_reply_ends_partial_rather_than_looping(self):
        result = await run(ScriptedModel([text("done"), text("really done")]), FakeGateway())
        self.assertEqual(result.status, "PARTIAL")


class RepairTests(IsolatedAsyncioTestCase):
    async def test_a_rejected_result_names_the_fields_and_can_be_fixed_with_tools_still_available(self):
        model = ScriptedModel([submit({"status": "MAYBE"}), submit(PASS, "submit-2")])
        result = await run(model, FakeGateway())
        self.assertEqual(result.status, "PASS")
        rejection = tool_reply(model, 1)
        self.assertEqual((rejection["role"], rejection["tool_call_id"]), ("tool", "submit-1"))
        self.assertTrue(rejection["content"].startswith("RESULT REJECTED"))
        self.assertIn("status", rejection["content"])
        self.assertIn("summary", rejection["content"])
        self.assertEqual(model.seen[1]["tools"], ["read_repo_file", SUBMIT_RESULT])  # can still gather evidence

    async def test_a_pass_without_evidence_is_told_the_rule(self):
        model = ScriptedModel([submit({"status": "PASS", "summary": "done"}), submit({"status": "PARTIAL", "summary": "no evidence"}, "s2")])
        result = await run(model, FakeGateway())
        self.assertEqual(result.status, "PARTIAL")
        self.assertIn("A PASS needs evidence", tool_reply(model, 1)["content"])

    async def test_evidence_the_run_never_received_is_refused_with_a_generic_hint(self):
        async def verify(ctx, *, evidence_ids, artifact_ids):
            if evidence_ids:
                raise ScopeDenied("secret detail")
        resources = NS(verify_references=verify)
        model = ScriptedModel([submit(PASS), submit({"status": "PARTIAL", "summary": "x"}, "s2")])
        budget = Budget(max_steps=8, max_tool_calls=10, max_llm_cost_usd=1.0, max_wall_time_sec=120)
        reservation = NS(id=uuid4(), run_id=uuid4(), deadline=datetime.now(timezone.utc) + timedelta(seconds=120))
        await ChildRuntime().run(definition=load_builtin("general-executor", 5), ctx=NS(task_id=uuid4()),
            scope=NS(budget=budget, allowed_tools=()), model=model, gateway=FakeGateway(),
            budgets=NS(check_active=lambda _i: None, authorize_operation=lambda *a, **k: None, record_usage=lambda *a, **k: None),
            reservation=reservation, messages=[], resources=resources, reauthorize=AsyncMock())
        content = tool_reply(model, 1)["content"]
        self.assertIn("not one that a tool returned", content)
        self.assertNotIn("secret detail", content)

    async def test_repeated_rejection_ends_partial_instead_of_failing_the_run(self):
        model = ScriptedModel([submit({"status": "x"}, f"s{i}") for i in range(5)])
        result = await run(model, FakeGateway())
        self.assertEqual(result.status, "PARTIAL")
        self.assertEqual(len(model.seen), 3)  # the original plus two repairs


class LimitTests(IsolatedAsyncioTestCase):
    async def test_the_last_step_is_a_forced_honest_submission_not_a_lost_run(self):
        model = ScriptedModel([calls(("read_repo_file", {"path": "a"})), calls(("read_repo_file", {"path": "b"})),
                               submit({"status": "PARTIAL", "summary": "Ran out of steps.", "evidence_ids": ["e1"]})])
        result = await run(model, FakeGateway(outcome=lambda n, r: evidence_outcome("e1")), max_steps=3)
        self.assertEqual(result.status, "PARTIAL")
        last = model.seen[2]
        self.assertEqual(last["tools"], [SUBMIT_RESULT])
        self.assertEqual(last["tool_choice"]["function"]["name"], SUBMIT_RESULT)
        self.assertIn("STEP LIMIT REACHED", last["messages"][-1]["content"])

    async def test_running_out_of_tool_calls_forces_the_final_answer(self):
        model = ScriptedModel([calls(("read_repo_file", {"path": "a"})), submit({"status": "PARTIAL", "summary": "No calls left."})])
        gateway = FakeGateway(max_tool_calls=1)
        result = await run(model, gateway, max_tool_calls=1)
        self.assertEqual(result.status, "PARTIAL")
        self.assertIn("TOOL CALL LIMIT REACHED", model.seen[1]["messages"][-1]["content"])
        self.assertEqual(model.seen[1]["tools"], [SUBMIT_RESULT])

    async def test_a_streak_of_failing_tools_forces_the_final_answer(self):
        model = ScriptedModel([submit({"status": "BLOCKED", "summary": "Tools failed."})])
        gateway = FakeGateway()
        gateway.consecutive_errors = 5
        result = await run(model, gateway)
        self.assertEqual(result.status, "BLOCKED")
        self.assertIn("TOOLS KEEP FAILING", model.seen[0]["messages"][-1]["content"])

    async def test_little_time_left_forces_the_final_answer(self):
        model = ScriptedModel([submit({"status": "PARTIAL", "summary": "Out of time."})])
        result = await run(model, FakeGateway(), wall=120, remaining=8)  # under the 20 s reserved for answering
        self.assertEqual(result.status, "PARTIAL")
        self.assertEqual(model.seen[0]["tools"], [SUBMIT_RESULT])

    async def test_calling_other_tools_after_the_limit_is_a_partial_result(self):
        model = ScriptedModel([calls(("read_repo_file", {"path": "a"}))])
        result = await run(model, FakeGateway(), max_steps=1)
        self.assertEqual(result.status, "PARTIAL")

    async def test_low_resources_add_a_notice_to_the_tool_result(self):
        model = ScriptedModel([calls(("read_repo_file", {"path": "a"})), submit(PASS)])
        await run(model, FakeGateway(max_tool_calls=3, outcome=lambda n, r: evidence_outcome("e1")), max_tool_calls=3)
        body = json.loads(tool_reply(model, 1)["content"][len(RESULT_PREFIX):])
        self.assertIn("nearly out", body["notice"])


class ToolTurnTests(IsolatedAsyncioTestCase):
    async def test_errors_reach_the_model_with_detail_guidance_and_no_cost(self):
        failure = ToolOutcome(status="error", cost_usd=Decimal(0), error_code="FILE_NOT_FOUND",
                              error_detail="There is no file at 'x.py'. Closest real paths: a.py.")
        model = ScriptedModel([calls(("read_repo_file", {"path": "x.py"})), submit(PASS)])
        await run(model, FakeGateway(outcome=lambda n, r: failure))
        body = json.loads(tool_reply(model, 1)["content"][len(RESULT_PREFIX):])
        self.assertEqual((body["status"], body["error_code"]), ("error", "FILE_NOT_FOUND"))
        self.assertIn("Closest real paths", body["detail"])
        self.assertIn("suggested paths", body["guidance"])
        self.assertNotIn("cost_usd", body)

    async def test_submit_beside_other_calls_is_refused_and_the_other_calls_still_run(self):
        mixed = {"role": "assistant", "content": None, "tool_calls": [
            {"id": "r1", "type": "function", "function": {"name": "read_repo_file", "arguments": "{}"}},
            {"id": "s1", "type": "function", "function": {"name": SUBMIT_RESULT, "arguments": json.dumps(PASS)}}]}
        model = ScriptedModel([mixed, submit(PASS, "s2")])
        gateway = FakeGateway(outcome=lambda n, r: evidence_outcome("e1"))
        result = await run(model, gateway)
        self.assertEqual(result.status, "PASS")
        self.assertEqual([name for name, _ in gateway.executed], ["read_repo_file"])
        replies = {m["tool_call_id"]: m["content"] for m in model.seen[1]["messages"] if m["role"] == "tool"}
        self.assertEqual(set(replies), {"r1", "s1"})  # every requested call is answered
        self.assertIn("by itself", replies["s1"])

    async def test_independent_reads_run_together_and_each_call_is_answered(self):
        running = {"now": 0, "peak": 0}
        async def slow(args, ctx):
            running["now"] += 1
            running["peak"] = max(running["peak"], running["now"])
            await asyncio.sleep(0.03)
            running["now"] -= 1
            return ok(evidence_ids=[])
        gateway, _, _ = make_gateway({"read_repo_file": slow})
        three = calls(*(("read_repo_file", {"path": f"{i}.py"}) for i in range(3)))
        model = ScriptedModel([three, submit({"status": "PARTIAL", "summary": "read three files"})])
        with patch("agentforge.tools.gateway.authorize_tool"):
            result = await run(model, gateway)
        self.assertEqual(result.status, "PARTIAL")
        self.assertGreaterEqual(running["peak"], 2)
        ids = [m["tool_call_id"] for m in model.seen[1]["messages"] if m["role"] == "tool"]
        self.assertEqual(ids, [call["id"] for call in three["tool_calls"]])  # answered in request order

    async def test_effectful_calls_in_one_turn_run_in_the_order_requested(self):
        gateway = FakeGateway()
        gateway.is_parallel_safe = lambda name: name != "write_sandbox_file"
        order = []
        gateway.outcome = lambda name, raw: (order.append(name), ok())[1]
        turn = calls(("read_repo_file", {"path": "a"}), ("write_sandbox_file", {}), ("read_repo_file", {"path": "b"}))
        model = ScriptedModel([turn, submit({"status": "PARTIAL", "summary": "x"})])
        await run(model, gateway)
        self.assertEqual(order, ["read_repo_file", "write_sandbox_file", "read_repo_file"])


class CompactionTests(IsolatedAsyncioTestCase):
    async def test_old_results_are_stubbed_but_keep_their_ids_while_recent_ones_stay_whole(self):
        big = {"content": "x" * 3000}
        turns = [calls(("read_repo_file", {"path": f"{i}.py"})) for i in range(7)] + [submit(PASS)]
        model = ScriptedModel(turns, context_budget_tokens=3000)
        gateway = FakeGateway(outcome=lambda n, r: evidence_outcome("e1", data=big))
        result = await run(model, gateway, max_steps=20)
        self.assertEqual(result.status, "PASS")
        last = [m for m in model.seen[-1]["messages"] if m["role"] == "tool"]
        stubs = [m for m in last if '"compacted": true' in m["content"]]
        whole = [m for m in last if '"compacted"' not in m["content"]]
        self.assertTrue(stubs)
        self.assertEqual(len(whole), 4)  # the four most recent results stay in full
        self.assertTrue(gateway.forgotten)  # stubbed calls may be repeated to fetch them back
        self.assertTrue(set(gateway.forgotten) <= {m["tool_call_id"] for m in stubs})
        for stub in stubs:
            self.assertIn('"e1"', stub["content"])
            self.assertLess(len(stub["content"]), 600)
        # Nothing is compacted while the conversation fits.
        self.assertFalse([m for m in model.seen[0]["messages"] if '"compacted"' in str(m.get("content"))])


class LimitedModel(ScriptedModel):
    """A model whose own guard refuses any request over `limit` characters, like the real window check."""

    def __init__(self, turns, limit, **kwargs):
        super().__init__(turns, **kwargs)
        self.limit = limit

    def maximum_request_cost(self, messages, tools):
        if len(json.dumps(messages)) > self.limit:
            raise ModelContextLimit("bounded model context is exhausted")
        return Decimal("0.001")


def sized(*ids, chars=3000):
    return ToolOutcome(status="ok", data={"content": "x" * chars}, evidence_ids=ids, cost_usd=Decimal(0))


class SmallWindowTests(IsolatedAsyncioTestCase):
    """The failure seen with a 32K window: ten large reads requested in one turn overflowed the request."""

    async def test_a_big_parallel_batch_runs_only_what_fits_and_defers_the_rest(self):
        batch = calls(*(("read_repo_file", {"path": f"{i}.py"}) for i in range(5)))
        model = ScriptedModel([batch, submit({"status": "PARTIAL", "summary": "read what fit"})], max_input_tokens=9000)
        gateway = FakeGateway(outcome=lambda n, r: sized("e1"))
        result = await run(model, gateway)
        self.assertEqual(result.status, "PARTIAL")
        self.assertEqual(len(gateway.executed), 1)  # room for one result, not five
        replies = {m["tool_call_id"]: json.loads(m["content"][len(RESULT_PREFIX):]) for m in model.seen[1]["messages"] if m["role"] == "tool"}
        self.assertEqual(set(replies), {call["id"] for call in batch["tool_calls"]})  # every call is answered
        deferred = [r for r in replies.values() if r.get("error_code") == "DEFERRED"]
        self.assertEqual(len(deferred), 4)
        self.assertIn("at most 1 per turn", deferred[0]["detail"])
        self.assertIn("later turn", deferred[0]["guidance"])

    async def test_a_large_window_runs_the_whole_batch(self):
        batch = calls(*(("read_repo_file", {"path": f"{i}.py"}) for i in range(5)))
        model = ScriptedModel([batch, submit({"status": "PARTIAL", "summary": "read all"})], max_input_tokens=262_144)
        gateway = FakeGateway(outcome=lambda n, r: sized("e1"))
        await run(model, gateway)
        self.assertEqual(len(gateway.executed), 5)

    async def test_with_no_room_for_even_one_result_the_run_wraps_up_instead_of_failing(self):
        model = ScriptedModel([submit({"status": "PARTIAL", "summary": "Window too small to read more."})], max_input_tokens=3000)
        gateway = FakeGateway()
        result = await run(model, gateway)
        self.assertEqual(result.status, "PARTIAL")
        self.assertEqual(gateway.executed, [])
        self.assertEqual(model.seen[0]["tools"], [SUBMIT_RESULT])
        self.assertIn("CONTEXT WINDOW IS FULL", model.seen[0]["messages"][-1]["content"])

    async def test_a_request_over_the_window_is_compacted_harder_rather_than_aborting_the_run(self):
        turns = [calls(("read_repo_file", {"path": f"{i}.py"})) for i in range(5)] + [submit(PASS)]
        model = LimitedModel(turns, limit=12_000)  # no gentle compaction budget set: only the hard pass can save it
        gateway = FakeGateway(outcome=lambda n, r: sized("e1"))
        result = await run(model, gateway, max_steps=20)
        self.assertEqual(result.status, "PASS")
        last = [m for m in model.seen[-1]["messages"] if m["role"] == "tool"]
        self.assertTrue(any('"compacted": true' in m["content"] for m in last))
        self.assertNotIn('"compacted"', last[-1]["content"])  # the newest result, not yet read, stays whole

    async def test_a_conversation_that_can_never_fit_ends_partial_with_a_plain_explanation(self):
        model = LimitedModel([], limit=10)
        result = await run(model, FakeGateway())
        self.assertEqual(result.status, "PARTIAL")
        self.assertIn("more room", result.summary)
        self.assertEqual(model.seen, [])  # nothing was sent to the provider


class BudgetWrapUpTests(IsolatedAsyncioTestCase):
    async def test_a_nearly_spent_budget_forces_the_final_answer_while_one_call_is_still_affordable(self):
        gateway = FakeGateway()
        gateway.max_tool_cost = Decimal("0.02")
        turns = [calls(("read_repo_file", {"path": "a"})), submit({"status": "PARTIAL", "summary": "Budget spent."})]
        model = ScriptedModel(turns)
        # The model bound is 0.001, so the reserve is 2 x 0.001 + the 0.02 tool cap: 0.02 left is under it, 0.5 is not.
        left = iter([Decimal("0.02")])
        result = await run(model, gateway, money=lambda _id: next(left))
        self.assertEqual(result.status, "PARTIAL")
        self.assertEqual(model.seen[1]["tools"], [SUBMIT_RESULT])
        self.assertIn("BUDGET IS NEARLY SPENT", model.seen[1]["messages"][-1]["content"])

    async def test_a_refused_tool_call_for_lack_of_budget_wraps_the_run_up(self):
        gateway = FakeGateway()
        turns = [calls(("read_repo_file", {"path": "a"})), submit({"status": "PARTIAL", "summary": "Out of budget."})]
        refused = ToolOutcome(status="error", cost_usd=Decimal(0), error_code="BUDGET_EXCEEDED", error_detail="cannot cover")
        def outcome(name, raw):
            gateway.budget_exhausted = True
            return refused
        gateway.outcome = outcome
        model = ScriptedModel(turns)
        result = await run(model, gateway)
        self.assertEqual(result.status, "PARTIAL")
        self.assertEqual(model.seen[1]["tools"], [SUBMIT_RESULT])
        self.assertIn("BUDGET IS NEARLY SPENT", model.seen[1]["messages"][-1]["content"])

    async def test_with_plenty_of_budget_nothing_is_forced(self):
        gateway = FakeGateway()
        gateway.max_tool_cost = Decimal("0.02")
        model = ScriptedModel([calls(("read_repo_file", {"path": "a"})), submit(PASS)])
        result = await run(model, gateway, money=lambda _id: Decimal("0.5"))
        self.assertEqual(result.status, "PASS")
        self.assertEqual(model.seen[1]["tools"], ["read_repo_file", SUBMIT_RESULT])
