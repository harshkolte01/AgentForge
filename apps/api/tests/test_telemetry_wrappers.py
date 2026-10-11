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
        self.assertEqual(attributes, {"task_id": "t", "spawn_id": "r", "tool.name": "web_search", "status": "error"})


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
