"""The real Neatlogs SDK exporting to a local OTLP receiver: what a hosted project would actually be sent.

No network beyond 127.0.0.1 and no key that works anywhere. These tests are the check that the export mask keeps what the
dashboard reads (tokens, cost, model, session, status) and still drops what must not leave (prompts, answers, exception text).
"""
import asyncio
import unittest
from decimal import Decimal
from types import SimpleNamespace as NS

from pydantic import SecretStr

from agentforge.contracts.subagent import SpawnRequest, SubagentResult
from agentforge.llm.models import ModelProviderError, ModelResponse, ModelUsage
from agentforge.settings import Settings
from agentforge.telemetry.neatlogs_setup import Telemetry, annotate, stage
from agentforge.telemetry.runtime import ObservedModel, observed_agent, observed_tool
from agentforge.tools.contracts import ToolOutcome
from agentforge.tools.errors import ToolError
from otlp_fakes import Receiver

SECRET = "sk-live-abcdefghijklmnopqrstuvwx"
PRIVATE_GOAL = "refactor the payroll module for ACME"


def settings_for(url, **overrides):
    return Settings(_env_file=None, app_env="test", neatlogs_api_key=SecretStr("nl_test_key_for_local_receiver"),
        neatlogs_endpoint=url, neatlogs_workflow_name="agentforge-test", neatlogs_tags="baseline,prompt-v1",
        llm_model="test-model", llm_api_key=SecretStr(SECRET), **overrides)


class FakeModel:
    model_id = "test-model"

    def __init__(self, failure=None, retries=0):
        self.failure, self.retries = failure, retries

    def maximum_request_cost(self, messages, tools):
        return Decimal(0)

    async def complete(self, messages, tools, on_text=None, tool_choice=None, on_narration=None, on_retry=None,
                       on_attempt_failed=None):
        for _ in range(self.retries):
            on_attempt_failed("RATE_LIMIT")
        if self.failure:
            raise self.failure
        message = {"role": "assistant", "content": f"Done. My key is {SECRET}",
            "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "web_search", "arguments": '{"query": "x"}'}}]}
        return ModelResponse(message, ModelUsage(120, 30, Decimal("0.0042")), "test-model-2026", "tool_calls")


async def one_task(telemetry, *, model=None, outcome=None, tool_error=None, agent_result=None):
    """A miniature task: root -> agent -> model call, a tool, and (when asked) a failing tool and a failed agent."""
    store = NS(progress=lambda *a: None, lease=NS(task_id="task-1"))
    observed = ObservedModel(model or FakeModel(), telemetry, store)

    async def search(arguments, execution):
        if tool_error:
            raise tool_error
        return outcome or ToolOutcome(status="ok", cost_usd=Decimal("0.01"), data={"hits": [PRIVATE_GOAL]})
    tool = observed_tool(NS(name="web_search", handler=search), telemetry, store)

    async def spawn(request, ctx, **kwargs):
        try:
            await observed.complete([{"role": "user", "content": PRIVATE_GOAL}], [{"name": "web_search"}])
        except ModelProviderError:
            return SubagentResult(status="FAIL", error_code="MODEL_PROVIDER_ERROR", summary="x")   # the runtime reports it so
        try:
            await tool(NS(model_dump=lambda mode=None: {"query": PRIVATE_GOAL}), NS(agent_run_id="run-1"))
        except ToolError:
            pass
        return agent_result or SubagentResult(status="PASS", summary="ok")

    agent = observed_agent(spawn, telemetry)

    async def root():
        with stage("prepare_context"):
            pass
        await agent(SpawnRequest(type="builtin/agent", version=1, prompt="p"), NS(task_id="task-1", depth=0, parent_run_id=None), run_id="run-1")
        annotate({"agentforge.task.status": "COMPLETED"})
    return telemetry.decorate(root, kind="WORKFLOW", name="agentforge_task", session_id="conversation-9", end_user_id="user-7",
        attributes={"task_id": "task-1"})


def run(telemetry, **kwargs):
    async def go():
        await (await one_task(telemetry, **kwargs))()
    asyncio.run(go())


class WireTests(unittest.TestCase):
    def setUp(self):
        self.receiver = Receiver()
        self.addCleanup(self.receiver.close)

    def traced(self, *, content=False, **kwargs):
        telemetry = Telemetry(settings_for(self.receiver.url, neatlogs_capture_content=content))
        self.assertTrue(telemetry.enabled)
        try:
            run(telemetry, **kwargs)
        finally:
            telemetry.shutdown()
        return telemetry

    def test_the_model_span_carries_what_the_dashboard_reads(self):
        self.traced()
        span = self.receiver.named("model_request")[0]
        self.assertEqual(span.attributes["neatlogs.span.kind"], "llm")
        self.assertEqual(span.attributes["neatlogs.llm.model_name"], "test-model")
        self.assertEqual(span.attributes["neatlogs.llm.provider"], "openai")
        self.assertEqual((span.attributes["neatlogs.llm.token_count.prompt"], span.attributes["neatlogs.llm.token_count.completion"],
                          span.attributes["neatlogs.llm.token_count.total"]), (120, 30, 150))
        self.assertAlmostEqual(span.attributes["neatlogs.llm.cost_usd"], 0.0042)
        self.assertEqual(span.attributes["neatlogs.llm.choices.0.finish_reason"], "tool_calls")
        self.assertEqual(span.attributes["agentforge.llm.tool_calls"], 1)

    def test_the_trace_is_one_tree_in_one_session_with_tags(self):
        self.traced()
        root = self.receiver.named("agentforge_task")[0]
        self.assertEqual(root.parent_id, "")
        self.assertEqual(root.attributes["neatlogs.session.id"], "conversation-9")
        self.assertEqual(root.attributes["neatlogs.end_user.id"], "user-7")
        self.assertEqual(root.attributes["agentforge.task.status"], "COMPLETED")
        self.assertIn("baseline", root.attributes["neatlogs.tags"])
        self.assertIn("env:test", root.attributes["neatlogs.tags"])
        self.assertIn("model:test-model", root.attributes["neatlogs.tags"])
        self.assertEqual(root.attributes["neatlogs.user.id"], "agentforge-worker")
        self.assertEqual(len({span.trace_id for span in self.receiver.spans}), 1)
        agent = self.receiver.named("agent:builtin/agent")[0]
        self.assertEqual(agent.parent_id, root.span_id)
        self.assertEqual(agent.attributes["spawn_id"], "run-1")
        self.assertEqual(self.receiver.named("model_request")[0].parent_id, agent.span_id)
        self.assertEqual(self.receiver.named("web_search")[0].parent_id, agent.span_id)
        self.assertEqual(self.receiver.named("prepare_context")[0].parent_id, root.span_id)
        for span in self.receiver.spans:
            self.assertEqual(span.resource["neatlogs.workflow_name"], "agentforge-test")

    def test_a_clean_run_has_no_failed_span(self):
        self.traced()
        self.assertEqual({span.status for span in self.receiver.spans if span.name != "model_request"} - {0, 1}, set())
        self.assertNotIn(2, {span.status for span in self.receiver.spans})

    def test_a_tool_that_raises_ends_failed_with_its_code_but_not_its_text(self):
        self.traced(tool_error=ToolError("FILE_NOT_FOUND", f"missing {PRIVATE_GOAL}"))
        span = self.receiver.named("web_search")[0]
        self.assertEqual(span.status, 2)
        self.assertEqual(span.attributes["agentforge.error.code"], "FILE_NOT_FOUND")
        self.assertEqual(span.attributes["agentforge.error.type"], "ToolError")
        self.assertEqual(len(span.events), 1)
        self.assertEqual(span.events[0].attributes["exception.type"], "FILE_NOT_FOUND")   # the failure's code, not a class path
        self.assertEqual(set(span.events[0].attributes), {"exception.type", "exception.escaped"})
        self.assertEqual(span.status_message, "")
        # The next steps of the run still happened and the run itself finished.
        self.assertEqual(self.receiver.named("agent:builtin/agent")[0].status, 1)

    def test_a_tool_that_returns_an_error_outcome_ends_failed(self):
        self.traced(outcome=ToolOutcome(status="error", error_code="TOOL_FAILED", cost_usd=Decimal(0)))
        span = self.receiver.named("web_search")[0]
        self.assertEqual(span.status, 2)
        self.assertEqual(span.attributes["agentforge.error.code"], "TOOL_FAILED")
        self.assertEqual(span.attributes["status"], "error")
        self.assertEqual([event.attributes["exception.type"] for event in span.events], ["TOOL_FAILED"])

    def test_a_failed_agent_run_ends_failed_even_though_it_returned_a_result(self):
        self.traced(agent_result=SubagentResult(status="FAIL", error_code="BUDGET_EXCEEDED", summary="x"))
        agent = self.receiver.named("agent:builtin/agent")[0]
        self.assertEqual(agent.status, 2)
        self.assertEqual(agent.attributes["agentforge.error.code"], "BUDGET_EXCEEDED")
        self.assertEqual(agent.attributes["agentforge.run.status"], "FAIL")

    def test_a_failed_model_call_records_the_provider_code_and_the_retries(self):
        self.traced(model=FakeModel(failure=ModelProviderError("down", code="RATE_LIMIT", detail="secret detail"), retries=2))
        span = self.receiver.named("model_request")[0]
        self.assertEqual(span.status, 2)
        self.assertEqual(span.attributes["agentforge.error.code"], "RATE_LIMIT")
        self.assertEqual(span.attributes["agentforge.llm.retries"], 2)
        self.assertEqual(span.attributes["agentforge.llm.retry_reasons"], "RATE_LIMIT")
        self.assertNotIn("secret detail", repr(span.__dict__))

    def test_metadata_mode_sends_no_prompt_answer_tool_data_or_secret(self):
        self.traced()
        everything = repr([span.__dict__ for span in self.receiver.spans])
        for private in (PRIVATE_GOAL, SECRET, "Done. My key", "hits"):
            self.assertNotIn(private, everything)
        model = self.receiver.named("model_request")[0]
        self.assertFalse([key for key in model.attributes if "input_messages" in key or "output_messages" in key])

    def test_content_mode_sends_prompts_and_answers_with_secrets_masked(self):
        self.traced(content=True)
        model = self.receiver.named("model_request")[0]
        self.assertEqual(model.attributes["neatlogs.llm.input_messages.0.content"], PRIVATE_GOAL)
        answer = model.attributes["neatlogs.llm.output_messages.0.content"]
        self.assertTrue(answer.startswith("Done. My key is "))
        self.assertNotIn(SECRET, answer)
        tool = self.receiver.named("web_search")[0]
        self.assertIn(PRIVATE_GOAL, tool.attributes["input.value"])
        self.assertIn("hits", tool.attributes["output.value"])
        self.assertNotIn(SECRET, repr([span.__dict__ for span in self.receiver.spans]))

    def test_content_mode_still_drops_exception_stacks(self):
        self.traced(content=True, tool_error=ToolError("FILE_NOT_FOUND", "missing"))
        span = self.receiver.named("web_search")[0]
        self.assertEqual(span.status, 2)
        self.assertNotIn("exception.stacktrace", span.events[0].attributes)
        self.assertEqual(span.events[0].attributes["exception.message"], "missing")


class GraphTests(unittest.TestCase):
    """The LangGraph callback handler records graph state; the mask is what keeps that state from leaving."""

    def setUp(self):
        self.receiver = Receiver()
        self.addCleanup(self.receiver.close)

    def graph_run(self, content):
        from typing import TypedDict

        from langgraph.graph import END, START, StateGraph

        class State(TypedDict):
            goal: str

        async def work(state):
            return {"goal": state["goal"] + " (done)"}
        builder = StateGraph(State)
        builder.add_node("work", work)
        builder.add_edge(START, "work")
        builder.add_edge("work", END)
        graph = builder.compile()
        telemetry = Telemetry(settings_for(self.receiver.url, neatlogs_capture_content=content))
        self.assertTrue(telemetry.enabled and telemetry.callbacks())

        async def root():
            with telemetry.span("agent:builtin/agent", kind="AGENT"):
                await graph.ainvoke({"goal": PRIVATE_GOAL}, config={"callbacks": telemetry.callbacks()})
        try:
            asyncio.run(telemetry.decorate(root, kind="WORKFLOW", name="agentforge_task", session_id="c1")())
        finally:
            telemetry.shutdown()

    def test_graph_nodes_nest_under_the_agent_in_one_trace_and_keep_no_state(self):
        self.graph_run(content=False)
        node = [span for span in self.receiver.spans if span.attributes.get("neatlogs.langgraph.node") == "work"]
        self.assertTrue(node, [span.name for span in self.receiver.spans])
        self.assertEqual(len({span.trace_id for span in self.receiver.spans}), 1)
        by_id = {span.span_id: span for span in self.receiver.spans}
        chain, parent = [], by_id[node[0].parent_id]
        while parent is not None:
            chain.append(parent.name)
            parent = by_id.get(parent.parent_id)
        self.assertIn("agent:builtin/agent", chain)
        self.assertEqual(chain[-1], "agentforge_task")
        self.assertNotIn(PRIVATE_GOAL, repr([span.__dict__ for span in self.receiver.spans]))

    def test_graph_state_is_sent_only_in_content_mode(self):
        self.graph_run(content=True)
        self.assertIn(PRIVATE_GOAL, repr([span.__dict__ for span in self.receiver.spans]))


if __name__ == "__main__":
    unittest.main()
