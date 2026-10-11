"""The agent loop end to end with a scripted model.

The model gets the conversation and its tools. A reply without tool calls is the answer, exactly as written; the
loop has no result tool, no status other than PASS, and no step, call, time or cost limit. Provider errors are the
caller's to show, so they propagate.
"""
import asyncio
import json
from decimal import Decimal
from types import SimpleNamespace as NS
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from langgraph.errors import NodeCancelledError

from agentforge.contracts.agent_definition import Budget
from agentforge.contracts.tasks import MAX_ANSWER_CHARS
from agentforge.llm.models import ModelContextLimit, ModelProviderError, ModelResponse, ModelUsage
from agentforge.subagents.context import RESULT_PREFIX
from agentforge.subagents.registry import AGENT_VERSION, load_builtin
from agentforge.subagents.runtime import CUT_OFF_NOTE, CUTOFF_NOTICE, EMPTY_NOTICE, ChildRuntime
from agentforge.tools.contracts import ToolOutcome

from test_tool_gateway import make as make_gateway, ok


def calls(*items, content=None):
    """An assistant turn that requests tools: (name, arguments) pairs, with optional text written beside them."""
    return {"role": "assistant", "content": content, "tool_calls": [
        {"id": f"call-{index}-{name}", "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}
        for index, (name, arguments) in enumerate(items)]}


def text(content):
    return {"role": "assistant", "content": content}


def cut_off(message):
    """A scripted turn that the provider stopped for the output length limit."""
    return ("length", message)


class ScriptedModel:
    """Plays its turns in order. A turn is a message, (finish_reason, message), a ModelResponse or an exception.

    Like the real adapter it streams a call's text to `on_narration` while tools are offered and to `on_text` when
    none are, and it does so before the call returns.
    """
    model_id = "scripted"

    def __init__(self, turns, context_budget_tokens=None, max_input_tokens=None):
        self.turns, self.seen = list(turns), []
        self.context_budget_tokens, self.max_input_tokens = context_budget_tokens, max_input_tokens

    def maximum_request_cost(self, messages, tools):
        return Decimal("0.001")

    async def complete(self, messages, tools, on_text=None, tool_choice=None, on_narration=None):
        self.seen.append({"messages": messages, "tools": [t["function"]["name"] for t in tools], "tool_choice": tool_choice})
        turn = self.turns.pop(0)
        if isinstance(turn, BaseException):
            raise turn
        if isinstance(turn, ModelResponse):
            return turn
        reason, message = turn if isinstance(turn, tuple) else (None, turn)
        sink = on_narration if tools else on_text
        if sink is not None and message.get("content"):
            sink(message["content"])
        reason = reason or ("tool_calls" if message.get("tool_calls") else "stop")
        return ModelResponse(message, ModelUsage(1, 1, Decimal("0.0001")), "scripted", reason)


class FakeGateway:
    """Enough gateway for the loop: schemas, execution, and which tools may run side by side."""

    def __init__(self, outcome=None, parallel=True):
        self.executed, self.parallel = [], parallel
        self.outcome = outcome or (lambda name, raw: ok(text="x"))

    def schemas(self):
        return [{"type": "function", "function": {"name": "read_repo_file", "description": "d", "parameters": {"type": "object"}}}]

    def is_parallel_safe(self, name):
        return self.parallel

    async def execute(self, name, raw, call_id):
        self.executed.append((name, call_id))
        return self.outcome(name, raw)


class Ledger:
    """What the loop reports to the budget service: checks, bounds authorized and usage recorded."""

    def __init__(self):
        self.checked, self.authorized, self.usage = 0, [], []

    def check_active(self, _id):
        self.checked += 1

    def authorize_operation(self, reservation_id, key, bound, kind=None):
        self.authorized.append((key, bound, kind))

    def record_usage(self, reservation_id, key, cost, usage=None):
        self.usage.append((key, cost, usage))


def evidence_outcome(data=None):
    return ToolOutcome(status="ok", data=data or {"text": "x"}, cost_usd=Decimal(0))


def sized(chars=3000):
    return ToolOutcome(status="ok", data={"content": "x" * chars}, cost_usd=Decimal(0))


async def run(model, gateway, *, stream=None, ledger=None, reauthorize=None, messages=None):
    reservation = NS(id=uuid4(), run_id=uuid4())
    return await ChildRuntime().run(
        definition=load_builtin("agent", AGENT_VERSION), ctx=NS(task_id=uuid4()),
        scope=NS(budget=Budget(), allowed_tools=("read_repo_file",)), model=model, gateway=gateway,
        budgets=ledger or Ledger(), reservation=reservation,
        messages=messages or [{"role": "developer", "content": "rules"}, {"role": "user", "content": "go"}],
        resources=NS(), reauthorize=reauthorize or AsyncMock(), stream=stream)


def tool_messages(model, call_index):
    return [m for m in model.seen[call_index]["messages"] if m["role"] == "tool"]


def body(message):
    return json.loads(message["content"][len(RESULT_PREFIX):])


class AnswerTests(IsolatedAsyncioTestCase):
    async def test_a_reply_without_tool_calls_is_the_answer_exactly_as_written(self):
        model = ScriptedModel([text("Hello! How can I help?")])
        result = await run(model, FakeGateway())
        self.assertEqual((result.status, result.summary, result.error_code), ("PASS", "Hello! How can I help?", None))
        self.assertEqual(len(model.seen), 1)
        self.assertEqual(model.seen[0]["tools"], ["read_repo_file"])  # no result tool is offered
        self.assertIsNone(model.seen[0]["tool_choice"])

    async def test_the_answer_is_never_parsed_graded_or_relabelled(self):
        for reply in ('{"status": "PARTIAL", "summary": "x"}', "## Result\n\n- **done**\n- `code`", "w", "I could not do that."):
            result = await run(ScriptedModel([text(reply)]), FakeGateway())
            self.assertEqual((result.status, result.summary), ("PASS", reply))

    async def test_surrounding_whitespace_is_trimmed_and_nothing_else(self):
        result = await run(ScriptedModel([text("  \n  line one\n\nline two  \n")]), FakeGateway())
        self.assertEqual(result.summary, "line one\n\nline two")

    async def test_an_answer_is_cut_to_the_saved_message_limit(self):
        result = await run(ScriptedModel([text("a" * (MAX_ANSWER_CHARS + 500))]), FakeGateway())
        self.assertEqual(len(result.summary), MAX_ANSWER_CHARS)

    async def test_the_conversation_is_sent_as_given_and_no_limit_text_is_added(self):
        conversation = [{"role": "developer", "content": "rules"}, {"role": "user", "content": "first"},
                        {"role": "assistant", "content": "reply"}, {"role": "user", "content": "second"}]
        model = ScriptedModel([text("ok")])
        await run(model, FakeGateway(), messages=conversation)
        self.assertEqual(model.seen[0]["messages"], conversation)

    async def test_every_model_call_is_authorized_checked_and_recorded(self):
        ledger, reauthorize = Ledger(), AsyncMock()
        model = ScriptedModel([calls(("read_repo_file", {"path": "a"})), text("done")])
        await run(model, FakeGateway(), ledger=ledger, reauthorize=reauthorize)
        self.assertEqual(reauthorize.await_count, 2)
        self.assertEqual(ledger.checked, 2)
        self.assertEqual([kind for _, _, kind in ledger.authorized], ["model", "model"])
        self.assertEqual([key.rsplit(":", 2)[1:] for key, _, _ in ledger.authorized], [["model", "0"], ["model", "1"]])
        self.assertEqual([cost for _, cost, _ in ledger.usage], [Decimal("0.0001")] * 2)
        usage = ledger.usage[0][2]
        self.assertEqual((usage["input_tokens"], usage["output_tokens"], usage["configured_model"], usage["reported_model"]),
                         (1, 1, "scripted", "scripted"))

    async def test_a_model_that_cannot_stream_works_without_a_sink(self):
        class Plain:
            model_id = "plain"
            def maximum_request_cost(self, messages, tools): return Decimal("0.001")
            async def complete(self, messages, tools):  # no on_text / on_narration parameters
                return ModelResponse(text("fine"), ModelUsage(1, 1, Decimal("0")), "plain", "stop")
        self.assertEqual((await run(Plain(), FakeGateway())).summary, "fine")

    async def test_a_malformed_model_message_is_a_provider_error(self):
        for turn in ({"role": "user", "content": "x"},
                     {"role": "assistant", "content": None, "tool_calls": "nope"},
                     {"role": "assistant", "content": None, "tool_calls": [
                         {"id": "same", "type": "function", "function": {"name": "read_repo_file", "arguments": "{}"}},
                         {"id": "same", "type": "function", "function": {"name": "read_repo_file", "arguments": "{}"}}]}):
            with self.subTest(turn=turn), self.assertRaises(ModelProviderError):
                await run(ScriptedModel([turn]), FakeGateway())


class ToolTurnTests(IsolatedAsyncioTestCase):
    async def test_one_tool_call_then_the_answer(self):
        model = ScriptedModel([calls(("read_repo_file", {"path": "a.py"})), text("The file is empty.")])
        gateway = FakeGateway(outcome=lambda n, r: evidence_outcome({"text": "contents"}))
        result = await run(model, gateway)
        self.assertEqual((result.status, result.summary), ("PASS", "The file is empty."))
        self.assertEqual(gateway.executed, [("read_repo_file", "call-0-read_repo_file")])
        second = model.seen[1]["messages"]
        self.assertEqual([m["role"] for m in second[-2:]], ["assistant", "tool"])
        reply = second[-1]
        self.assertEqual(reply["tool_call_id"], "call-0-read_repo_file")
        self.assertTrue(reply["content"].startswith(RESULT_PREFIX))
        self.assertEqual((body(reply)["tool"], body(reply)["status"], body(reply)["data"]), ("read_repo_file", "ok", {"text": "contents"}))

    async def test_tool_results_carry_no_limit_counters_or_notices(self):
        model = ScriptedModel([calls(("read_repo_file", {"path": "a"})), text("done")])
        await run(model, FakeGateway())
        payload = body(tool_messages(model, 1)[0])
        for key in ("limits", "notice", "steps_left", "tool_calls_left", "cost_usd"):
            self.assertNotIn(key, payload)

    async def test_errors_reach_the_model_with_detail_guidance_and_no_cost(self):
        failure = ToolOutcome(status="error", cost_usd=Decimal(0), error_code="FILE_NOT_FOUND",
                              error_detail="There is no file at 'x.py'. Closest real paths: a.py.")
        model = ScriptedModel([calls(("read_repo_file", {"path": "x.py"})), text("It does not exist.")])
        await run(model, FakeGateway(outcome=lambda n, r: failure))
        payload = body(tool_messages(model, 1)[0])
        self.assertEqual((payload["status"], payload["error_code"]), ("error", "FILE_NOT_FOUND"))
        self.assertIn("Closest real paths", payload["detail"])
        self.assertIn("suggested paths", payload["guidance"])
        self.assertNotIn("cost_usd", payload)

    async def test_a_long_run_is_never_cut_short(self):
        turns = [calls(("read_repo_file", {"path": f"{index}.py"})) for index in range(150)] + [text("Finished.")]
        gateway = FakeGateway()
        result = await run(ScriptedModel(turns), gateway)
        self.assertEqual((result.status, result.summary), ("PASS", "Finished."))
        self.assertEqual(len(gateway.executed), 150)

    async def test_the_same_call_can_be_made_again(self):
        turns = [calls(("read_repo_file", {"path": "a"})), calls(("read_repo_file", {"path": "a"})), text("twice")]
        gateway = FakeGateway()
        self.assertEqual((await run(ScriptedModel(turns), gateway)).summary, "twice")
        self.assertEqual(len(gateway.executed), 2)

    async def test_an_oversized_result_is_previewed_with_its_reference_kept(self):
        huge = ToolOutcome(status="ok", data={"resource_id": "res-1", "content": "y" * 40000}, evidence_ids=("e1",), cost_usd=Decimal(0))
        model = ScriptedModel([calls(("read_repo_file", {"path": "a"})), text("done")])
        await run(model, FakeGateway(outcome=lambda n, r: huge))
        content = tool_messages(model, 1)[0]["content"]
        self.assertLessEqual(len(content.encode()), 16000 + len(RESULT_PREFIX))
        data = body(tool_messages(model, 1)[0])["data"]
        self.assertEqual((data["preview_truncated"], data["resource_id"]), (True, "res-1"))

    async def test_a_tool_call_with_text_beside_it_keeps_the_text_in_history(self):
        model = ScriptedModel([calls(("read_repo_file", {"path": "a"}), content="I'll read the file first."), text("done")])
        await run(model, FakeGateway())
        assistant = [m for m in model.seen[1]["messages"] if m["role"] == "assistant"][0]
        self.assertEqual(assistant["content"], "I'll read the file first.")
        self.assertEqual(len(assistant["tool_calls"]), 1)

    async def test_invalid_tool_calls_from_the_model_are_provider_errors(self):
        bad = {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "web", "function": {"name": "x", "arguments": "{}"}}]}
        with self.assertRaises(ModelProviderError):
            await run(ScriptedModel([bad]), FakeGateway())
        broken = {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "x", "arguments": {}}}]}
        with self.assertRaises(ModelProviderError):
            await run(ScriptedModel([broken]), FakeGateway())


class ConcurrencyTests(IsolatedAsyncioTestCase):
    """The real gateway decides what is safe to overlap; the loop only has to honour it."""

    def setUp(self):
        patcher = patch("agentforge.tools.gateway.authorize_tool")
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_independent_reads_run_together_and_each_call_is_answered_in_order(self):
        running = {"now": 0, "peak": 0}
        async def slow(args, ctx):
            running["now"] += 1
            running["peak"] = max(running["peak"], running["now"])
            await asyncio.sleep(0.03)
            running["now"] -= 1
            return ok()
        gateway, _, _ = make_gateway({"read_repo_file": slow})
        three = calls(*(("read_repo_file", {"path": f"{i}.py"}) for i in range(3)))
        model = ScriptedModel([three, text("read three files")])
        result = await run(model, gateway)
        self.assertEqual(result.summary, "read three files")
        self.assertGreaterEqual(running["peak"], 2)
        ids = [m["tool_call_id"] for m in tool_messages(model, 1)]
        self.assertEqual(ids, [call["id"] for call in three["tool_calls"]])

    async def test_an_effectful_call_runs_alone_and_in_the_order_requested(self):
        running = {"now": 0, "peak": 0, "order": []}
        def tracked(label):
            async def handler(args, ctx):
                running["now"] += 1
                running["peak"] = max(running["peak"], running["now"])
                running["order"].append(label)
                await asyncio.sleep(0.02)
                running["now"] -= 1
                return ok()
            return handler
        gateway, _, _ = make_gateway({"read_repo_file": tracked("read"), "write": tracked("write")})
        turn = calls(("read_repo_file", {"path": "a"}), ("write", {"path": "a", "content": "x"}), ("read_repo_file", {"path": "b"}))
        model = ScriptedModel([turn, text("done")])
        await run(model, gateway)
        self.assertEqual(running["order"], ["read", "write", "read"])
        self.assertEqual(running["peak"], 1)
        self.assertEqual(len(tool_messages(model, 1)), 3)

    async def test_a_failing_parallel_call_cancels_its_siblings_and_ends_the_run(self):
        finished = []
        async def slow(args, ctx):
            await asyncio.sleep(0.2)
            finished.append(args.path)
            return ok()
        class Boom(FakeGateway):
            async def execute(self, name, raw, call_id):
                if call_id.startswith("call-0"):
                    raise RuntimeError("lease lost")
                return await slow(NS(path=call_id), None)
        model = ScriptedModel([calls(("read_repo_file", {}), ("read_repo_file", {})), text("never")])
        with self.assertRaises(RuntimeError):
            await run(model, Boom())
        self.assertEqual(finished, [])


class CompactionTests(IsolatedAsyncioTestCase):
    async def test_old_results_are_stubbed_but_keep_their_ids_while_recent_ones_stay_whole(self):
        big = {"content": "x" * 3000, "resource_id": "res-1"}
        turns = [calls(("read_repo_file", {"path": f"{i}.py"})) for i in range(7)] + [text("All read.")]
        model = ScriptedModel(turns, context_budget_tokens=3000)
        result = await run(model, FakeGateway(outcome=lambda n, r: evidence_outcome(big)))
        self.assertEqual(result.summary, "All read.")
        last = tool_messages(model, -1)
        stubs = [m for m in last if '"compacted": true' in m["content"]]
        whole = [m for m in last if '"compacted"' not in m["content"]]
        self.assertTrue(stubs)
        self.assertEqual(len(whole), 4)  # the four most recent results stay in full
        for stub in stubs:
            self.assertIn('"res-1"', stub["content"])
            self.assertLess(len(stub["content"]), 700)
        # Nothing is compacted while the conversation fits.
        self.assertFalse([m for m in model.seen[0]["messages"] if '"compacted"' in str(m.get("content"))])

    async def test_how_many_results_were_compacted_is_recorded_with_the_usage(self):
        ledger = Ledger()
        turns = [calls(("read_repo_file", {"path": f"{i}.py"})) for i in range(7)] + [text("ok")]
        await run(ScriptedModel(turns, context_budget_tokens=3000), FakeGateway(outcome=lambda n, r: sized()), ledger=ledger)
        self.assertEqual(ledger.usage[0][2]["compacted_results"], 0)
        self.assertGreater(ledger.usage[-1][2]["compacted_results"], 0)

class LimitedModel(ScriptedModel):
    """A model whose own guard refuses any request over `limit` characters, like the real window check."""

    def __init__(self, turns, limit, **kwargs):
        super().__init__(turns, **kwargs)
        self.limit = limit

    def maximum_request_cost(self, messages, tools):
        if len(json.dumps(messages)) > self.limit:
            raise ModelContextLimit("bounded model context is exhausted")
        return Decimal("0.001")


class SmallWindowTests(IsolatedAsyncioTestCase):
    """The failure seen with a 32K window: ten large reads requested in one turn overflowed the request."""

    async def test_a_big_parallel_batch_runs_only_what_fits_and_defers_the_rest(self):
        batch = calls(*(("read_repo_file", {"path": f"{i}.py"}) for i in range(5)))
        model = ScriptedModel([batch, text("read what fit")], max_input_tokens=25_000)
        gateway = FakeGateway(outcome=lambda n, r: sized())
        result = await run(model, gateway)
        self.assertEqual(result.summary, "read what fit")
        self.assertEqual(len(gateway.executed), 1)  # room for one result, not five
        replies = {m["tool_call_id"]: body(m) for m in tool_messages(model, 1)}
        self.assertEqual(set(replies), {call["id"] for call in batch["tool_calls"]})  # every call is answered
        deferred = [r for r in replies.values() if r.get("error_code") == "DEFERRED"]
        self.assertEqual(len(deferred), 4)
        self.assertIn("at most 1 per turn", deferred[0]["detail"])
        self.assertIn("later turn", deferred[0]["guidance"])

    async def test_a_large_window_runs_the_whole_batch(self):
        batch = calls(*(("read_repo_file", {"path": f"{i}.py"}) for i in range(5)))
        gateway = FakeGateway(outcome=lambda n, r: sized())
        await run(ScriptedModel([batch, text("read all")], max_input_tokens=262_144), gateway)
        self.assertEqual(len(gateway.executed), 5)

    async def test_with_no_room_for_even_one_result_the_calls_are_deferred_and_the_model_answers(self):
        batch = calls(("read_repo_file", {"path": "a"}))
        model = ScriptedModel([batch, text("The window is too small to read more.")], max_input_tokens=3000)
        gateway = FakeGateway()
        result = await run(model, gateway)
        self.assertEqual(result.summary, "The window is too small to read more.")
        self.assertEqual(gateway.executed, [])
        deferred = body(tool_messages(model, 1)[0])
        self.assertEqual(deferred["error_code"], "DEFERRED")
        self.assertIn("no room left", deferred["detail"])

    async def test_a_request_over_the_window_is_compacted_harder_rather_than_aborting_the_run(self):
        turns = [calls(("read_repo_file", {"path": f"{i}.py"})) for i in range(5)] + [text("done")]
        model = LimitedModel(turns, limit=12_000)  # no gentle compaction budget set: only the hard pass can save it
        result = await run(model, FakeGateway(outcome=lambda n, r: sized()))
        self.assertEqual(result.summary, "done")
        last = tool_messages(model, -1)
        self.assertTrue(any('"compacted": true' in m["content"] for m in last))
        self.assertNotIn('"compacted"', last[-1]["content"])  # the newest result, not yet read, stays whole

    async def test_a_conversation_that_can_never_fit_raises_the_context_limit_without_calling_the_model(self):
        model = LimitedModel([], limit=10)
        with self.assertRaises(ModelContextLimit) as caught:
            await run(model, FakeGateway())
        self.assertIn("no longer fits", str(caught.exception))
        self.assertEqual(model.seen, [])  # nothing was sent to the provider


class ProviderErrorTests(IsolatedAsyncioTestCase):
    async def test_a_provider_error_propagates_unchanged_with_the_providers_message(self):
        failure = ModelProviderError("model rate limited", code="RATE_LIMIT", detail="429: You exceeded your current quota.")
        with self.assertRaises(ModelProviderError) as caught:
            await run(ScriptedModel([failure]), FakeGateway())
        self.assertIs(caught.exception, failure)
        self.assertEqual(caught.exception.shown, "429: You exceeded your current quota.")

    async def test_a_provider_context_error_propagates(self):
        failure = ModelContextLimit("400: maximum context length is 128000 tokens")
        with self.assertRaises(ModelContextLimit) as caught:
            await run(ScriptedModel([failure]), FakeGateway())
        self.assertIs(caught.exception, failure)

    async def test_an_error_after_some_tool_work_still_propagates(self):
        model = ScriptedModel([calls(("read_repo_file", {"path": "a"})), ModelProviderError("down", detail="503: overloaded")])
        with self.assertRaises(ModelProviderError):
            await run(model, FakeGateway())

    async def test_the_failed_call_is_recorded_with_unknown_cost(self):
        ledger = Ledger()
        with self.assertRaises(ModelProviderError):
            await run(ScriptedModel([ModelProviderError("down")]), FakeGateway(), ledger=ledger)
        self.assertEqual([(cost, usage) for _, cost, usage in ledger.usage], [(None, None)])  # the bound stays charged

    async def test_a_stop_request_is_not_swallowed(self):
        with self.assertRaises((asyncio.CancelledError, NodeCancelledError)):
            await run(ScriptedModel([asyncio.CancelledError()]), FakeGateway())

    async def test_pressing_stop_while_the_model_is_working_cancels_the_run_and_clears_the_live_text(self):
        sink = RecordingSink()
        started = asyncio.Event()
        class Slow(ScriptedModel):
            async def complete(self, messages, tools, on_text=None, tool_choice=None, on_narration=None):
                on_narration("Working on it")
                started.set()
                await asyncio.sleep(30)
        running = asyncio.create_task(run(Slow([]), FakeGateway(), stream=sink))
        await started.wait()
        running.cancel()
        with self.assertRaises(asyncio.CancelledError):  # the real thing, not a wrapped error
            await running
        self.assertEqual(sink.log[0], "begin")
        self.assertIn("discard", sink.log)
        self.assertNotIn("commit_reply", sink.log)

    async def test_a_failing_authorization_ends_the_run_before_the_model_is_called(self):
        model = ScriptedModel([text("never")])
        with self.assertRaises(PermissionError):
            await run(model, FakeGateway(), reauthorize=AsyncMock(side_effect=PermissionError("revoked")))
        self.assertEqual(model.seen, [])

    async def test_unusual_finish_reasons_are_provider_errors_naming_the_reason(self):
        for reason in ("content_filter", "function_call", "error"):
            with self.subTest(reason=reason), self.assertRaises(ModelProviderError) as caught:
                await run(ScriptedModel([(reason, text("partial words"))]), FakeGateway())
            self.assertIn(reason, caught.exception.shown)
            self.assertEqual(caught.exception.detail, f"finish_reason: {reason}")


class EmptyReplyTests(IsolatedAsyncioTestCase):
    async def test_an_empty_reply_is_asked_for_once_more(self):
        model = ScriptedModel([text(""), text("Here you go.")])
        result = await run(model, FakeGateway())
        self.assertEqual((result.status, result.summary), ("PASS", "Here you go."))
        self.assertEqual(model.seen[1]["messages"][-1], {"role": "user", "content": EMPTY_NOTICE})
        self.assertEqual(len(model.seen[1]["messages"]), len(model.seen[0]["messages"]) + 1)  # the empty turn is not replayed

    async def test_a_blank_or_missing_reply_counts_as_empty(self):
        for first in (text("  \n "), {"role": "assistant", "content": None}):
            result = await run(ScriptedModel([first, text("ok")]), FakeGateway())
            self.assertEqual(result.summary, "ok")

    async def test_two_empty_replies_are_a_provider_error(self):
        model = ScriptedModel([text(""), text("")])
        with self.assertRaises(ModelProviderError) as caught:
            await run(model, FakeGateway())
        self.assertIn("empty", caught.exception.shown)
        self.assertEqual(len(model.seen), 2)

    async def test_the_second_chance_is_for_the_whole_run(self):
        model = ScriptedModel([text(""), calls(("read_repo_file", {"path": "a"})), text("")])
        with self.assertRaises(ModelProviderError):
            await run(model, FakeGateway())


class LengthCutoffTests(IsolatedAsyncioTestCase):
    async def test_a_reply_cut_off_while_calling_tools_is_asked_for_again_shorter(self):
        model = ScriptedModel([cut_off(calls(("read_repo_file", {"path": "a"}))), calls(("read_repo_file", {"path": "a"})), text("done")])
        gateway = FakeGateway()
        result = await run(model, gateway)
        self.assertEqual(result.summary, "done")
        self.assertEqual(len(gateway.executed), 1)  # the cut-off call's unusable arguments never ran
        retry = model.seen[1]["messages"]
        self.assertEqual(retry[-1], {"role": "user", "content": CUTOFF_NOTICE})
        self.assertFalse([m for m in retry if m.get("tool_calls")])  # and the cut-off turn is not replayed

    async def test_it_gives_up_with_a_provider_error_after_two_retries(self):
        cut = lambda: cut_off(calls(("read_repo_file", {"path": "a"})))
        model = ScriptedModel([cut(), cut(), cut(), text("never")])
        with self.assertRaises(ModelProviderError) as caught:
            await run(model, FakeGateway())
        self.assertEqual(len(model.seen), 3)
        self.assertIn("output space", str(caught.exception))
        self.assertEqual(caught.exception.detail, "finish_reason: length")

    async def test_a_reply_cut_off_without_tool_calls_is_the_answer_with_a_note(self):
        result = await run(ScriptedModel([cut_off(text("The first half of a long answer"))]), FakeGateway())
        self.assertEqual(result.status, "PASS")
        self.assertEqual(result.summary, "The first half of a long answer" + CUT_OFF_NOTE)

    async def test_a_cut_off_reply_with_no_text_is_an_empty_reply(self):
        model = ScriptedModel([cut_off(text("")), text("ok")])
        self.assertEqual((await run(model, FakeGateway())).summary, "ok")


class RecordingSink:
    """The live-text hooks the loop drives, in the order it drives them."""

    def __init__(self):
        self.log = []

    def begin(self): self.log.append("begin")
    def feed(self, piece): self.log.append(("feed", piece))
    def feed_narration(self, piece): self.log.append(("narration", piece))
    def end(self): self.log.append("end")
    def commit_narration(self): self.log.append("commit_narration")
    def commit_reply(self): self.log.append("commit_reply")
    def discard(self): self.log.append("discard")


class StreamHookTests(IsolatedAsyncioTestCase):
    async def test_the_final_answer_is_committed_as_the_reply(self):
        sink = RecordingSink()
        await run(ScriptedModel([text("Hello there")]), FakeGateway(), stream=sink)
        self.assertEqual(sink.log, ["begin", ("narration", "Hello there"), "end", "commit_reply"])

    async def test_text_beside_tool_calls_is_committed_as_narration_and_the_reply_after_it_as_the_reply(self):
        sink = RecordingSink()
        model = ScriptedModel([calls(("read_repo_file", {"path": "a"}), content="I'll read the file."), text("It is empty.")])
        await run(model, FakeGateway(), stream=sink)
        self.assertEqual(sink.log, ["begin", ("narration", "I'll read the file."), "end", "commit_narration",
                                    "begin", ("narration", "It is empty."), "end", "commit_reply"])

    async def test_a_tool_call_without_text_still_ends_with_commit_narration(self):
        sink = RecordingSink()
        await run(ScriptedModel([calls(("read_repo_file", {"path": "a"})), text("ok")]), FakeGateway(), stream=sink)
        self.assertEqual(sink.log.count("commit_narration"), 1)
        self.assertEqual(sink.log.count("commit_reply"), 1)
        self.assertLess(sink.log.index("commit_narration"), sink.log.index("commit_reply"))

    async def test_the_loop_hands_the_sink_both_text_callbacks(self):
        seen = {}
        class Probe(ScriptedModel):
            async def complete(self, messages, tools, on_text=None, tool_choice=None, on_narration=None):
                seen.update(on_text=on_text, on_narration=on_narration)
                return await super().complete(messages, tools)
        sink = RecordingSink()
        await run(Probe([text("x")]), FakeGateway(), stream=sink)
        self.assertEqual((seen["on_text"], seen["on_narration"]), (sink.feed, sink.feed_narration))

    async def test_a_provider_error_discards_whatever_is_on_screen(self):
        sink = RecordingSink()
        with self.assertRaises(ModelProviderError):
            await run(ScriptedModel([ModelProviderError("down")]), FakeGateway(), stream=sink)
        self.assertEqual(sink.log[0], "begin")
        self.assertEqual(sink.log.count("discard"), 2)  # once for the failed call, once more as the run ends
        self.assertNotIn("commit_reply", sink.log)

    async def test_a_stop_request_discards_whatever_is_on_screen(self):
        sink = RecordingSink()
        with self.assertRaises((asyncio.CancelledError, NodeCancelledError)):
            await run(ScriptedModel([asyncio.CancelledError()]), FakeGateway(), stream=sink)
        self.assertIn("discard", sink.log)
        self.assertNotIn("commit_reply", sink.log)

    async def test_an_attempt_that_is_thrown_away_is_discarded_before_the_retry(self):
        sink = RecordingSink()
        model = ScriptedModel([cut_off(calls(("read_repo_file", {"path": "a"}), content="Let me")), text("Done")])
        await run(model, FakeGateway(), stream=sink)
        self.assertEqual(sink.log, ["begin", ("narration", "Let me"), "end", "discard", "begin", ("narration", "Done"), "end", "commit_reply"])

    async def test_an_empty_reply_is_discarded_before_it_is_asked_for_again(self):
        sink = RecordingSink()
        await run(ScriptedModel([text(""), text("Hi")]), FakeGateway(), stream=sink)
        self.assertEqual(sink.log, ["begin", "end", "discard", "begin", ("narration", "Hi"), "end", "commit_reply"])

    async def test_a_sink_with_only_some_hooks_still_works(self):
        sink = NS(begin=lambda: None, feed=lambda piece: None, feed_narration=lambda piece: None, end=lambda: None,
                  commit_narration=lambda: None, commit_reply=lambda: None)  # no discard
        result = await run(ScriptedModel([text("fine")]), FakeGateway(), stream=sink)
        self.assertEqual(result.summary, "fine")
