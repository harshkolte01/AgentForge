"""Observed wrappers: every tool call that starts also finishes on the timeline, even when it is rejected."""
from contextlib import contextmanager
from decimal import Decimal
from types import SimpleNamespace as NS
from unittest import IsolatedAsyncioTestCase

from agentforge.llm.models import ModelResponse, ModelUsage
from agentforge.telemetry.runtime import ObservedModel, observed_tool
from agentforge.tools.contracts import FetchWebpageArgs, ToolOutcome
from agentforge.tools.errors import ToolError


@contextmanager
def span(*args, **kwargs):
    yield None


def harness():
    events = []
    store = NS(progress=lambda kind, data: events.append((kind, data)), lease=NS(task_id="t"))
    return events, store, NS(span=span)


class ObservedToolTests(IsolatedAsyncioTestCase):
    """The wrapper traces only; the gateway reports lifecycle events (tests/test_tool_lifecycle.py)."""

    async def test_a_rejected_call_propagates_and_emits_no_event(self):
        events, store, telemetry = harness()
        async def reject(args, ctx):
            raise ToolError("FILE_NOT_FOUND", "nope")
        wrapped = observed_tool(NS(name="read_repo_file", handler=reject), telemetry, store)
        with self.assertRaises(ToolError):
            await wrapped(NS(path="a.py"), NS(agent_run_id="r"))
        self.assertEqual(events, [])

    async def test_a_successful_call_returns_the_outcome_unchanged_and_emits_no_event(self):
        events, store, telemetry = harness()
        outcome = ToolOutcome(status="ok", cost_usd=Decimal(0), evidence_ids=("e1",))
        async def fine(args, ctx):
            return outcome
        wrapped = observed_tool(NS(name="fetch_webpage", handler=fine), telemetry, store)
        result = await wrapped(FetchWebpageArgs(source_url="https://example.com/x"), NS(agent_run_id="r"))
        self.assertIs(result, outcome)
        self.assertEqual(events, [])

    async def test_the_span_carries_the_task_run_and_tool_names(self):
        attributes = {}
        @contextmanager
        def recording_span(name, kind=None):
            yield NS(set_attribute=lambda key, value: attributes.__setitem__(key, value))
        async def fine(args, ctx):
            return ToolOutcome(status="error", error_code="X", cost_usd=Decimal(0))
        store = NS(progress=lambda *a: None, lease=NS(task_id="t"))
        wrapped = observed_tool(NS(name="web_search", handler=fine), NS(span=recording_span), store)
        await wrapped(NS(query="q"), NS(agent_run_id="r"))
        self.assertEqual({key: attributes[key] for key in ("task_id", "spawn_id", "tool.name", "status")},
                         {"task_id": "t", "spawn_id": "r", "tool.name": "web_search", "status": "error"})
        # A tool that returns an error instead of raising still ends the span failed, with the tool's own code.
        self.assertEqual(attributes["agentforge.error.code"], "X")
        self.assertEqual((attributes["agentforge.tool.cost_usd"], attributes["agentforge.tool.evidence_count"]), (0.0, 0))

    async def test_a_successful_call_is_not_marked_failed(self):
        attributes, statuses = {}, []
        @contextmanager
        def recording_span(name, kind=None):
            yield NS(set_attribute=lambda key, value: attributes.__setitem__(key, value), set_status=statuses.append,
                     record_exception=lambda error: None)
        async def fine(args, ctx):
            return ToolOutcome(status="ok", cost_usd=Decimal("0.02"), evidence_ids=("e1", "e2"))
        wrapped = observed_tool(NS(name="web_search", handler=fine), NS(span=recording_span), NS(progress=lambda *a: None, lease=NS(task_id="t")))
        await wrapped(NS(query="q"), NS(agent_run_id="r"))
        self.assertEqual(statuses, [])
        self.assertNotIn("agentforge.error.code", attributes)
        self.assertEqual((attributes["agentforge.tool.cost_usd"], attributes["agentforge.tool.evidence_count"]), (0.02, 2))

    async def test_tool_text_is_recorded_only_when_content_capture_is_on(self):
        for capture in (False, True):
            attributes = {}
            @contextmanager
            def recording_span(name, kind=None):
                yield NS(set_attribute=lambda key, value: attributes.__setitem__(key, value))
            async def fine(args, ctx):
                return ToolOutcome(status="ok", data={"hits": ["private"]}, cost_usd=Decimal(0))
            telemetry = NS(span=recording_span, capture_content=capture)
            wrapped = observed_tool(NS(name="web_search", handler=fine), telemetry, NS(progress=lambda *a: None, lease=NS(task_id="t")))
            await wrapped(NS(model_dump=lambda mode=None: {"query": "private"}), NS(agent_run_id="r"))
            self.assertEqual("input.value" in attributes and "output.value" in attributes, capture)


class ObservedModelTests(IsolatedAsyncioTestCase):
    async def test_forced_tool_choice_and_streaming_are_forwarded_only_when_given(self):
        events, store, telemetry = harness()
        seen = []
        class Inner:
            model_id = "m"
            context_budget_tokens = 1234
            max_input_tokens = 5678
            strict_tools = True
            def maximum_request_cost(self, messages, tools): return Decimal(0)
            async def complete(self, messages, tools, **kwargs):
                seen.append(kwargs)
                return ModelResponse({"role": "assistant", "content": ""}, ModelUsage(1, 1, Decimal(0)), "m", "stop")
        model = ObservedModel(Inner(), telemetry, store)
        self.assertEqual((model.context_budget_tokens, model.max_input_tokens, model.strict_tools), (1234, 5678, True))
        await model.complete([], [])
        await model.complete([], [], on_text=print, tool_choice={"type": "function"})
        self.assertEqual(seen, [{}, {"on_text": print, "tool_choice": {"type": "function"}}])

    async def test_the_model_span_carries_model_usage_cost_and_finish_reason(self):
        attributes = {}
        @contextmanager
        def recording_span(name, kind=None):
            yield NS(set_attribute=lambda key, value: attributes.__setitem__(key, value))
        class Inner:
            model_id = "m"
            async def complete(self, messages, tools):
                return ModelResponse({"role": "assistant", "content": "hi", "tool_calls": [{"id": "1"}]}, ModelUsage(7, 3, Decimal("0.5")), "m-2", "tool_calls")
        store = NS(progress=lambda *a: None, lease=NS(task_id="t"))
        await ObservedModel(Inner(), NS(span=recording_span), store).complete([{"role": "user", "content": "private"}], [{"name": "x"}])
        self.assertEqual((attributes["llm.model_name"], attributes["llm.provider"]), ("m", "openai"))
        self.assertEqual((attributes["gen_ai.usage.input_tokens"], attributes["gen_ai.usage.output_tokens"], attributes["llm.token_count.total"]), (7, 3, 10))
        self.assertEqual((attributes["cost_usd"], attributes["neatlogs.llm.cost_usd"], attributes["agentforge.llm.cost_known"]), (0.5, 0.5, True))
        self.assertEqual((attributes["neatlogs.llm.choices.0.finish_reason"], attributes["agentforge.llm.reported_model"]), ("tool_calls", "m-2"))
        self.assertEqual((attributes["agentforge.llm.messages"], attributes["agentforge.llm.tools_offered"], attributes["agentforge.llm.tool_calls"]), (1, 1, 1))
        # Message text is not recorded unless content capture is on.
        self.assertFalse([key for key in attributes if "input_messages" in key or "output_messages" in key])

    async def test_an_unknown_cost_is_said_so_not_reported_as_zero(self):
        attributes = {}
        @contextmanager
        def recording_span(name, kind=None):
            yield NS(set_attribute=lambda key, value: attributes.__setitem__(key, value))
        class Inner:
            model_id = "m"
            async def complete(self, messages, tools):
                return ModelResponse({"role": "assistant", "content": ""}, ModelUsage(1, 1, None), "m", "stop")
        await ObservedModel(Inner(), NS(span=recording_span), NS(progress=lambda *a: None, lease=NS(task_id="t"))).complete([], [])
        self.assertIs(attributes["agentforge.llm.cost_known"], False)
        self.assertNotIn("cost_usd", attributes)
        self.assertNotIn("neatlogs.llm.cost_usd", attributes)

    async def test_failed_provider_attempts_are_counted_on_the_span_only_when_the_model_reports_them(self):
        attributes = {}
        @contextmanager
        def recording_span(name, kind=None):
            yield NS(set_attribute=lambda key, value: attributes.__setitem__(key, value))
        class Reporting:
            model_id = "m"
            async def complete(self, messages, tools, on_attempt_failed=None):
                on_attempt_failed("RATE_LIMIT")
                on_attempt_failed("RATE_LIMIT")
                on_attempt_failed("TIMEOUT")
                return ModelResponse({"role": "assistant", "content": "x"}, ModelUsage(1, 1, Decimal(0)), "m", "stop")
        class Plain:
            model_id = "m"
            async def complete(self, messages, tools):
                return ModelResponse({"role": "assistant", "content": "x"}, ModelUsage(1, 1, Decimal(0)), "m", "stop")
        store = NS(progress=lambda *a: None, lease=NS(task_id="t"))
        await ObservedModel(Reporting(), NS(span=recording_span), store).complete([], [])
        self.assertEqual((attributes["agentforge.llm.retries"], attributes["agentforge.llm.retry_reasons"]), (3, "RATE_LIMIT,TIMEOUT"))
        attributes.clear()
        await ObservedModel(Plain(), NS(span=recording_span), store).complete([], [])
        self.assertNotIn("agentforge.llm.retries", attributes)

    async def test_the_hook_is_not_offered_to_a_model_when_tracing_is_off(self):
        seen = []
        class Reporting:
            model_id = "m"
            async def complete(self, messages, tools, on_attempt_failed=None):
                seen.append(on_attempt_failed)
                return ModelResponse({"role": "assistant", "content": "x"}, ModelUsage(1, 1, Decimal(0)), "m", "stop")
        events, store, telemetry = harness()
        await ObservedModel(Reporting(), telemetry, store).complete([], [])
        self.assertEqual(seen, [None])

    async def test_paid_stream_retries_reach_the_runtime_usage_ledger(self):
        events, store, telemetry = harness()
        retries = []
        class Inner:
            model_id = "m"
            async def complete(self, messages, tools, on_retry=None):
                on_retry()
                return ModelResponse({"role": "assistant", "content": "done"}, ModelUsage(1, 1, Decimal(0)), "m", "stop")
        await ObservedModel(Inner(), telemetry, store).complete([], [], on_retry=lambda: retries.append(True))
        self.assertEqual(retries, [True])
