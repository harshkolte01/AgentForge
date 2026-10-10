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
    async def test_a_rejected_call_still_emits_a_finished_event_and_propagates(self):
        events, store, telemetry = harness()
        async def reject(args, ctx):
            raise ToolError("FILE_NOT_FOUND", "nope")
        wrapped = observed_tool(NS(name="read_repo_file", handler=reject), telemetry, store)
        with self.assertRaises(ToolError):
            await wrapped(NS(path="a.py"), NS(agent_run_id="r"))
        self.assertEqual([kind for kind, _ in events], ["tool.started", "tool.finished"])
        self.assertEqual(events[1][1]["status"], "error")

    async def test_a_successful_call_reports_its_status_and_ids(self):
        events, store, telemetry = harness()
        async def fine(args, ctx):
            return ToolOutcome(status="ok", cost_usd=Decimal(0), evidence_ids=("e1",))
        wrapped = observed_tool(NS(name="fetch_webpage", handler=fine), telemetry, store)
        await wrapped(FetchWebpageArgs(source_url="https://example.com/x"), NS(agent_run_id="r"))
        self.assertEqual(events[1], ("tool.finished", {"tool": "fetch_webpage", "status": "ok", "evidence_ids": ["e1"], "artifact_ids": []}))


class ObservedModelTests(IsolatedAsyncioTestCase):
    async def test_forced_tool_choice_and_streaming_are_forwarded_only_when_given(self):
        events, store, telemetry = harness()
        seen = []
        class Inner:
            model_id = "m"
            context_budget_bytes = 1234
            strict_tools = True
            def maximum_request_cost(self, messages, tools): return Decimal(0)
            async def complete(self, messages, tools, **kwargs):
                seen.append(kwargs)
                return ModelResponse({"role": "assistant", "content": ""}, ModelUsage(1, 1, Decimal(0)), "m", "stop")
        model = ObservedModel(Inner(), telemetry, store)
        self.assertEqual((model.context_budget_bytes, model.strict_tools), (1234, True))
        await model.complete([], [])
        await model.complete([], [], on_text=print, tool_choice={"type": "function"})
        self.assertEqual(seen, [{}, {"on_text": print, "tool_choice": {"type": "function"}}])
