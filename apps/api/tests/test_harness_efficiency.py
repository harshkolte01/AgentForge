"""What the harness does to keep a run cheap without changing what the model is asked to do.

Four things, each pinned here: usage is recorded the way it is billed (cached reads, cache writes, thinking); the model's
encrypted reasoning travels back with the tool results of a run on the Responses endpoint and is counted as the tokens it
stands for; compaction keeps the prefix the provider cached stable instead of stubbing one more result at every step; a
command's whole output is saved in the sandbox when the shown result had to be cut.
"""
import json
from decimal import Decimal
from types import SimpleNamespace as NS
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock
from uuid import uuid4

from agentforge.contracts.agent_definition import Budget
from agentforge.llm.models import (
    ModelConfigurationError, ModelRates, ModelResponse, ModelUsage, conservative_input_tokens, message_input_tokens,
    reasoning_input_tokens, without_reasoning,
)
from agentforge.llm.openai_adapter import OpenAIAdapter, message_from_response, reasoning_items, to_responses_input
from agentforge.subagents.context import COMPACTION_TARGET, RESULT_PREFIX, compact_messages, conversation_tokens
from agentforge.subagents.registry import AGENT_VERSION, load_builtin
from agentforge.subagents.runtime import ChildRuntime
from agentforge.tools.contracts import ToolOutcome

from test_child_runtime import FakeGateway, Ledger, ScriptedModel, calls, sized, text
from test_reasoning_effort import Responses, call_item, luna, reply, text_item

RATES = ModelRates(Decimal("0.10"), Decimal("0.50"))
ENCRYPTED = "gAAAA" * 400  # 2000 characters of ciphertext standing for a few hundred reasoning tokens


def reasoning_item(ident="rs_1", encrypted=ENCRYPTED):
    return NS(type="reasoning", id=ident, encrypted_content=encrypted, summary=[])


def usage(input_tokens=1000, output_tokens=100, cached=0, written=0, reasoning=0):
    return NS(input_tokens=input_tokens, output_tokens=output_tokens,
              input_tokens_details=NS(cached_tokens=cached, cache_write_tokens=written),
              output_tokens_details=NS(reasoning_tokens=reasoning))


class RatesTests(TestCase):
    def test_without_cache_rates_every_input_token_costs_the_input_rate_as_before(self):
        self.assertEqual(RATES.cost(1_000_000, 0), Decimal("0.10"))
        self.assertEqual(RATES.cost(1_000_000, 0, cached_tokens=900_000, cache_write_tokens=100_000), Decimal("0.10"))

    def test_cache_rates_price_each_part_of_the_input_at_its_own_rate(self):
        rates = ModelRates(Decimal("0.10"), Decimal("0.50"), cached_input_per_million=Decimal("0.01"), cache_write_per_million=Decimal("0.125"))
        # 800k cached at 0.01, 100k written at 0.125, 100k plain at 0.10, 10k output at 0.50.
        self.assertEqual(rates.cost(1_000_000, 10_000, cached_tokens=800_000, cache_write_tokens=100_000),
                         Decimal("0.008") + Decimal("0.0125") + Decimal("0.01") + Decimal("0.005"))

    def test_only_the_cached_rate_is_enough_and_writes_then_cost_the_input_rate(self):
        rates = ModelRates(Decimal("0.10"), Decimal("0.50"), cached_input_per_million=Decimal("0.01"))
        self.assertEqual(rates.cost(1_000_000, 0, cached_tokens=500_000, cache_write_tokens=500_000), Decimal("0.005") + Decimal("0.05"))

    def test_cache_parts_larger_than_the_input_are_clamped_and_never_charged_twice(self):
        rates = ModelRates(Decimal("0.10"), Decimal("0.50"), cached_input_per_million=Decimal("0.01"))
        self.assertEqual(rates.cost(1000, 0, cached_tokens=5000, cache_write_tokens=5000), Decimal("0.00001"))

    def test_a_cached_rate_above_the_input_rate_and_negative_counts_are_refused(self):
        with self.assertRaises(ModelConfigurationError):
            ModelRates(Decimal("0.10"), Decimal("0.50"), cached_input_per_million=Decimal("0.20"))
        with self.assertRaises(ValueError):
            RATES.cost(10, 0, cached_tokens=-1)

    def test_usage_knows_its_uncached_part(self):
        measured = ModelUsage(1000, 50, None, cached_tokens=700, cache_write_tokens=200, reasoning_tokens=40)
        self.assertEqual(measured.uncached_input_tokens, 100)
        self.assertEqual(ModelUsage(10, 5, None).uncached_input_tokens, 10)  # the parts default to 0 for fixture models


class ResponsesUsageTests(IsolatedAsyncioTestCase):
    async def test_cached_written_and_reasoning_tokens_are_measured_and_priced(self):
        model = luna("high")
        model.rates = ModelRates(Decimal("0.10"), Decimal("0.50"), Decimal("0.01"), Decimal("0.125"))
        answer = reply([text_item("ok")])
        answer.usage = usage(1000, 100, cached=800, written=100, reasoning=60)
        model._client = NS(responses=Responses([answer]))
        response = await model.complete([{"role": "user", "content": "hi"}], [])
        measured = response.usage
        self.assertEqual((measured.input_tokens, measured.cached_tokens, measured.cache_write_tokens, measured.reasoning_tokens), (1000, 800, 100, 60))
        self.assertEqual(measured.cost_usd, (Decimal("0.01") * 800 + Decimal("0.125") * 100 + Decimal("0.10") * 100 + Decimal("0.50") * 100) / 1_000_000)

    async def test_a_usage_object_without_the_details_reports_zero_parts(self):
        model = luna("high")
        model._client = NS(responses=Responses([reply([text_item("ok")])]))
        measured = (await model.complete([{"role": "user", "content": "hi"}], [])).usage
        self.assertEqual((measured.cached_tokens, measured.cache_write_tokens, measured.reasoning_tokens), (0, 0, 0))
        self.assertEqual(measured.cost_usd, RATES.cost(10, 5))

    async def test_the_request_asks_for_encrypted_reasoning_back(self):
        responses = Responses([reply([text_item("ok")])])
        model = luna("high")
        model._client = NS(responses=responses)
        await model.complete([{"role": "user", "content": "hi"}], [])
        self.assertEqual(responses.requests[0]["include"], ["reasoning.encrypted_content"])
        self.assertIs(responses.requests[0]["store"], False)


class ChatUsageTests(IsolatedAsyncioTestCase):
    async def test_cached_prompt_tokens_and_reasoning_are_read_from_the_chat_usage_details(self):
        completions = []
        async def create(**kwargs):
            completions.append(kwargs)
            message = NS(model_dump=lambda exclude_none=True: {"role": "assistant", "content": "ok"})
            return NS(choices=[NS(message=message, finish_reason="stop")], model="m",
                      usage=NS(prompt_tokens=2000, completion_tokens=100, prompt_tokens_details=NS(cached_tokens=1500),
                               completion_tokens_details=NS(reasoning_tokens=30)))
        model = OpenAIAdapter(api_key="k", model_id="gpt-test", rates=ModelRates(Decimal("1"), Decimal("2"), Decimal("0.1")),
                              context_window_tokens=100_000, max_output_tokens=4096)
        model._client = NS(chat=NS(completions=NS(create=create)))
        measured = (await model.complete([{"role": "user", "content": "hi"}], [])).usage
        self.assertEqual((measured.cached_tokens, measured.cache_write_tokens, measured.reasoning_tokens), (1500, 0, 30))
        self.assertEqual(measured.cost_usd, (Decimal("0.1") * 1500 + Decimal("1") * 500 + Decimal("2") * 100) / 1_000_000)

    async def test_reasoning_carried_on_the_history_never_reaches_chat_completions(self):
        completions = []
        async def create(**kwargs):
            completions.append(kwargs)
            message = NS(model_dump=lambda exclude_none=True: {"role": "assistant", "content": "ok"})
            return NS(choices=[NS(message=message, finish_reason="stop")], model="m", usage=None)
        model = OpenAIAdapter(api_key="k", model_id="gpt-test", rates=RATES, context_window_tokens=100_000, max_output_tokens=4096)
        model._client = NS(chat=NS(completions=NS(create=create)))
        history = [{"role": "user", "content": "go"},
                   {"role": "assistant", "content": "", "tool_calls": [], "reasoning": [{"type": "reasoning", "id": "rs", "encrypted_content": "x"}], "reasoning_tokens": 5},
                   {"role": "user", "content": "more"}]
        await model.complete(history, [])
        sent = completions[0]["messages"]
        self.assertEqual(sent[1], {"role": "assistant", "content": "", "tool_calls": []})
        self.assertEqual(history[1]["reasoning_tokens"], 5)  # the caller's history is not modified


class ReasoningContinuityTests(TestCase):
    def test_encrypted_reasoning_is_kept_on_the_message_and_items_without_it_are_not(self):
        message, finish = message_from_response(reply([reasoning_item(), NS(type="reasoning", id="rs_2", encrypted_content=None, summary=[]),
                                                       text_item("Note."), call_item("c1", "read_file", "{}")]))
        self.assertEqual(finish, "tool_calls")
        self.assertEqual(message["reasoning"], [{"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": ENCRYPTED}])
        self.assertEqual((message["content"], message["tool_calls"][0]["id"]), ("Note.", "c1"))
        plain, _ = message_from_response(reply([text_item("just text")]))
        self.assertNotIn("reasoning", plain)

    def test_the_summary_text_is_kept_in_the_shape_the_endpoint_takes_back(self):
        item = NS(type="reasoning", id="rs_1", encrypted_content=ENCRYPTED, summary=[NS(type="summary_text", text="Plan: read a, then b.")])
        message, _ = message_from_response(reply([item, text_item("x")]))
        self.assertEqual(message["reasoning"][0]["summary"], [{"type": "summary_text", "text": "Plan: read a, then b."}])

    def test_the_reported_reasoning_tokens_are_recorded_on_the_message_for_the_estimate(self):
        model = luna("high")
        answer = reply([reasoning_item(), call_item("c1", "read_file", "{}")])
        answer.usage = usage(reasoning=250)
        message = model._responses_result(answer).message
        self.assertEqual(message["reasoning_tokens"], 250)
        self.assertEqual(reasoning_input_tokens(message), 250)
        self.assertEqual(reasoning_input_tokens({"role": "assistant", "content": "no thinking kept"}), 0)
        # Without a reported count the ciphertext size stands in, far below its character count.
        unreported = {"role": "assistant", "reasoning": [{"type": "reasoning", "id": "rs", "encrypted_content": "a" * 4000}]}
        self.assertEqual(reasoning_input_tokens(unreported), 1000)

    def test_reasoning_since_the_last_user_message_goes_back_before_the_calls_it_led_to(self):
        earlier = {"role": "assistant", "content": "old note", "reasoning": [{"type": "reasoning", "id": "rs_0", "encrypted_content": "OLD"}]}
        step = {"role": "assistant", "content": "Looking.", "reasoning_tokens": 40,
                "reasoning": [{"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": ENCRYPTED}],
                "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}]}
        history = [{"role": "user", "content": "first"}, earlier, {"role": "user", "content": "go"}, step,
                   {"role": "tool", "tool_call_id": "call_1", "content": "file text"}]
        items = to_responses_input(history)
        self.assertEqual([item.get("type", item.get("role")) for item in items],
                         ["user", "assistant", "user", "reasoning", "assistant", "function_call", "function_call_output"])
        self.assertEqual(items[3], {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": ENCRYPTED})
        self.assertFalse(any(item.get("encrypted_content") == "OLD" for item in items))  # before the last user message: left out

    def test_malformed_reasoning_entries_are_skipped(self):
        message = {"role": "assistant", "reasoning": ["junk", {"type": "reasoning", "id": 3}, {"type": "reasoning", "id": "ok", "encrypted_content": "E"}]}
        self.assertEqual(reasoning_items(message), [{"type": "reasoning", "id": "ok", "summary": [], "encrypted_content": "E"}])


class EstimateTests(TestCase):
    def test_encrypted_reasoning_is_counted_as_the_tokens_it_stands_for_not_its_ciphertext(self):
        plain = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "ok", "tool_calls": []}]
        carrying = [plain[0], {**plain[1], "reasoning": [{"type": "reasoning", "id": "rs", "encrypted_content": "x" * 30_000}], "reasoning_tokens": 300}]
        self.assertEqual(message_input_tokens(carrying), message_input_tokens(plain) + 300)
        added = conservative_input_tokens(carrying, []) - conservative_input_tokens(plain, [])
        self.assertTrue(300 <= added <= 300 + 15, added)  # the 300 tokens, and at most the 5% fitting margin on them
        stripped, tokens = without_reasoning(carrying)
        self.assertEqual((stripped, tokens), (plain, 300))
        self.assertIs(without_reasoning(plain)[0][1], plain[1])  # untouched messages are the same objects

    def test_the_cost_bound_of_a_request_is_not_inflated_by_the_ciphertext(self):
        model = luna("high")
        plain = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "ok"}]
        carrying = [plain[0], {**plain[1], "reasoning": [{"type": "reasoning", "id": "rs", "encrypted_content": "x" * 200_000}], "reasoning_tokens": 500}]
        difference = model.maximum_request_cost(carrying, []) - model.maximum_request_cost(plain, [])
        self.assertEqual(difference, RATES.cost(500, 0))


class CompactionStabilityTests(TestCase):
    def conversation(self, count, size=2000):
        messages = [{"role": "developer", "content": "rules"}, {"role": "user", "content": "brief"}]
        for index in range(count):
            messages.append({"role": "assistant", "content": None, "tool_calls": [{"id": f"t{index}"}]})
            messages.append({"role": "tool", "tool_call_id": f"t{index}", "content": RESULT_PREFIX + json.dumps(
                {"tool": "read", "status": "ok", "data": {"content": "x" * size}, "evidence_ids": [f"e{index}"], "artifact_ids": []})})
        return messages

    @staticmethod
    def stubs(messages):
        return [m["tool_call_id"] for m in messages if m["role"] == "tool" and "compacted" in m["content"]]

    def test_the_stubs_of_the_previous_request_are_kept_even_when_the_conversation_fits_again(self):
        messages = self.conversation(8)
        limit = conversation_tokens(messages) - 500
        first = compact_messages(messages, limit)
        floor = len(self.stubs(first))
        self.assertGreater(floor, 0)
        self.assertEqual(self.stubs(compact_messages(messages, 10_000_000, floor=floor)), self.stubs(first))
        self.assertIs(compact_messages(messages, 10_000_000), messages)  # without a floor, nothing is touched

    def test_a_run_in_the_compaction_regime_sends_the_same_prefix_for_several_steps(self):
        messages = self.conversation(8)
        limit = conversation_tokens(messages) - 500
        previous = compact_messages(messages, limit)
        floor, extended, recompacted = len(self.stubs(previous)), 0, 0
        for step in range(6):
            messages = messages + [{"role": "assistant", "content": None, "tool_calls": [{"id": f"n{step}"}]},
                                   {"role": "tool", "tool_call_id": f"n{step}", "content": RESULT_PREFIX + json.dumps({"tool": "read", "status": "ok", "data": {"content": "y" * 1200}})}]
            sent = compact_messages(messages, limit, floor=floor)
            self.assertLessEqual(conversation_tokens(sent), limit)
            if sent[:len(previous)] == previous:
                extended += 1  # the previous request, byte for byte, then the new pair: what the provider's cache matches on
            else:
                recompacted += 1
                self.assertGreater(len(self.stubs(sent)), floor)  # only ever because more had to be stubbed
            previous, floor = sent, len(self.stubs(sent))
        # The slack bought by compacting to the target carries the following steps unchanged; without it every step would
        # have stubbed one more result and changed the history the provider had cached.
        self.assertGreaterEqual(extended, 2 * recompacted)
        self.assertGreater(recompacted, 0)  # and the conversation did keep growing past the budget meanwhile

    def test_the_floor_never_touches_the_unread_batch_or_pinned_reads(self):
        messages = self.conversation(6)
        messages.append({"role": "assistant", "content": None, "tool_calls": [{"id": "fresh"}]})
        messages.append({"role": "tool", "tool_call_id": "fresh", "content": RESULT_PREFIX + json.dumps({"tool": "read", "status": "ok", "data": {"content": "z" * 3000}})})
        squeezed = compact_messages(messages, 10_000_000, keep_recent=0, floor=50)
        self.assertNotIn("fresh", self.stubs(squeezed))
        self.assertEqual(len(self.stubs(squeezed)), 6)

    def test_the_target_leaves_room_below_the_limit(self):
        messages = self.conversation(10)
        limit = conversation_tokens(messages) - 300
        compacted = compact_messages(messages, limit)
        self.assertLessEqual(conversation_tokens(compacted), int(limit * COMPACTION_TARGET))
        self.assertTrue(0 < COMPACTION_TARGET < 1)

    def test_the_hard_pass_drops_earlier_reasoning_but_keeps_the_latest_steps(self):
        messages = [{"role": "developer", "content": "rules"}, {"role": "user", "content": "brief"}]
        for index in range(3):
            messages.append({"role": "assistant", "content": None, "reasoning_tokens": 5000,
                             "reasoning": [{"type": "reasoning", "id": f"rs{index}", "encrypted_content": "E" * 100}],
                             "tool_calls": [{"id": f"t{index}", "type": "function", "function": {"name": "read", "arguments": "{}"}}]})
            messages.append({"role": "tool", "tool_call_id": f"t{index}", "content": RESULT_PREFIX + json.dumps({"tool": "read", "status": "ok", "data": {"content": "x" * 50}})})
        squeezed = compact_messages(messages, 1, keep_recent=0)
        assistants = [m for m in squeezed if m["role"] == "assistant"]
        self.assertEqual(["reasoning" in m for m in assistants], [False, False, True])
        self.assertEqual([m["tool_calls"][0]["id"] for m in assistants], ["t0", "t1", "t2"])
        self.assertIn("reasoning", messages[2])  # the stored conversation is never modified


class RuntimeLedgerTests(IsolatedAsyncioTestCase):
    async def drive(self, model, gateway, ledger):
        reservation = NS(id=uuid4(), run_id=uuid4())
        return await ChildRuntime().run(
            definition=load_builtin("agent", AGENT_VERSION), ctx=NS(task_id=uuid4()),
            scope=NS(budget=Budget(), allowed_tools=("read_repo_file",)), model=model, gateway=gateway,
            budgets=ledger, reservation=reservation,
            messages=[{"role": "developer", "content": "rules"}, {"role": "user", "content": "go"}],
            resources=NS(), reauthorize=AsyncMock())

    async def test_the_billing_split_is_recorded_with_every_model_call(self):
        measured = ModelUsage(1200, 80, Decimal("0.001"), cached_tokens=900, cache_write_tokens=200, reasoning_tokens=50)
        model = ScriptedModel([ModelResponse({"role": "assistant", "content": "done"}, measured, "m", "stop")])
        ledger = Ledger()
        await self.drive(model, FakeGateway(), ledger)
        recorded = ledger.usage[0][2]
        self.assertEqual((recorded["input_tokens"], recorded["cached_tokens"], recorded["cache_write_tokens"], recorded["reasoning_tokens"]), (1200, 900, 200, 50))

    async def test_a_fixture_model_without_the_split_records_zeros(self):
        ledger = Ledger()
        await self.drive(ScriptedModel([text("done")]), FakeGateway(), ledger)
        self.assertEqual((ledger.usage[0][2]["cached_tokens"], ledger.usage[0][2]["reasoning_tokens"]), (0, 0))

    async def test_stubs_are_kept_from_one_request_to_the_next_within_a_run(self):
        turns = [calls(("read_repo_file", {"path": f"{i}.py"})) for i in range(16)] + [text("ok")]
        model = ScriptedModel(turns, context_budget_tokens=12_000)
        await self.drive(model, FakeGateway(outcome=lambda n, r: sized()), Ledger())
        counts = [sum('"compacted": true' in m["content"] for m in request["messages"] if m["role"] == "tool") for request in model.seen]
        self.assertEqual(counts, sorted(counts))  # a result stubbed once stays stubbed
        first_compaction = next(index for index, count in enumerate(counts) if count)
        # The step after the first compaction reuses its stubs rather than adding to them.
        self.assertEqual(counts[first_compaction + 1], counts[first_compaction])

    async def test_a_reply_carrying_reasoning_is_kept_whole_in_the_history_and_judged_without_it(self):
        heavy = {"role": "assistant", "content": None, "reasoning_tokens": 10, "reasoning": [{"type": "reasoning", "id": "rs", "encrypted_content": "E" * 500_000}],
                 "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "read_repo_file", "arguments": "{}"}}]}
        model = ScriptedModel([heavy, text("done")])
        result = await self.drive(model, FakeGateway(), Ledger())
        self.assertEqual(result.summary, "done")
        self.assertIn("reasoning", [m for m in model.seen[1]["messages"] if m["role"] == "assistant"][0])


class TelemetryTests(IsolatedAsyncioTestCase):
    async def test_the_model_span_carries_the_billing_split(self):
        from contextlib import contextmanager
        from agentforge.telemetry.runtime import ObservedModel
        attributes = {}
        @contextmanager
        def recording_span(name, kind=None):
            yield NS(set_attribute=lambda key, value: attributes.__setitem__(key, value))
        class Inner:
            model_id = "m"
            async def complete(self, messages, tools):
                return ModelResponse({"role": "assistant", "content": "hi"}, ModelUsage(700, 30, Decimal("0.1"), cached_tokens=600, cache_write_tokens=50, reasoning_tokens=20), "m", "stop")
        store = NS(progress=lambda *a: None, lease=NS(task_id="t"))
        await ObservedModel(Inner(), NS(span=recording_span), store).complete([], [])
        self.assertEqual((attributes["agentforge.llm.cached_tokens"], attributes["agentforge.llm.cache_write_tokens"], attributes["agentforge.llm.reasoning_tokens"]), (600, 50, 20))


class SettingsTests(TestCase):
    def test_the_cache_rates_reach_the_adapter_and_the_long_tier_falls_back_to_its_input_rate(self):
        from pydantic import SecretStr
        from agentforge.llm.router import ModelRouter
        settings = NS(llm_provider="openai", llm_model="gpt-6-luna", llm_api_key=SecretStr("k"), llm_context_window_tokens=400_000,
                      llm_input_usd_per_million=Decimal("0.10"), llm_output_usd_per_million=Decimal("0.50"),
                      llm_cached_input_usd_per_million=Decimal("0.01"), llm_cache_write_usd_per_million=Decimal("0.125"),
                      llm_long_context_input_usd_per_million=Decimal("0.20"), llm_long_context_output_usd_per_million=Decimal("0.75"),
                      llm_supports_tools=True, llm_supports_json=True, llm_max_output_tokens=16384, llm_timeout_sec=180,
                      llm_parallel_tool_calls=True, llm_strict_tools=False, llm_max_retries=4)
        model = ModelRouter.from_settings(settings).model
        self.assertEqual((model.rates.cached_input_per_million, model.rates.cache_write_per_million), (Decimal("0.01"), Decimal("0.125")))
        self.assertIsNone(model.long_context_rates.cached_input_per_million)
        self.assertEqual(model.long_context_rates.cost(1_000_000, 0, cached_tokens=1_000_000), Decimal("0.20"))

    def test_the_settings_default_to_no_cache_rates(self):
        from agentforge.settings import Settings
        fields = Settings.model_fields
        self.assertIsNone(fields["llm_cached_input_usd_per_million"].default)
        self.assertIsNone(fields["llm_cache_write_usd_per_million"].default)
