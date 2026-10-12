"""How a finished or broken agent run becomes a result: the answer as written, or the reason a person should read."""
import asyncio
from decimal import Decimal
from types import SimpleNamespace as NS
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock
from uuid import uuid4

from agentforge.contracts.subagent import SpawnRequest, SubagentResult
from agentforge.contracts.tasks import MAX_ANSWER_CHARS
from agentforge.llm.models import ModelConfigurationError, ModelContextLimit, ModelProviderError
from agentforge.orchestration.context import agent_context
from agentforge.subagents.budget import BudgetExceeded
from agentforge.subagents.registry import AGENT_KEY, AGENT_VERSION, ResolvedDefinition, load_builtin
from agentforge.subagents.scope import ScopeDenied
from agentforge.subagents.spawn_service import GENERIC_STOPPED_SUMMARY, STOPPED_SUMMARIES, SpawnService
from agentforge.tools.catalog import ToolCatalog
from agentforge.tools.contracts import ToolOutcome
from agentforge.tools.gateway import UnknownToolOutcome

PROVIDER_TEXT = "429: You exceeded your current quota, please check your plan and billing details."


class Recorder:
    """The ledger and run rows the service writes."""

    def __init__(self):
        self.started, self.finished, self.settled, self.reserved = [], [], [], []

    def reserve(self, **kwargs):
        self.reserved.append(kwargs)
        return NS(id=uuid4(), run_id=kwargs["run_id"], amount_usd=kwargs["amount_usd"])

    def settle(self, reservation_id): self.settled.append(reservation_id)
    def record_start(self, **kwargs): self.started.append(kwargs)
    def record_finish(self, **kwargs): self.finished.append(kwargs)


def service_with(run, *, tools=("web_search",)):
    definition = load_builtin("agent", AGENT_VERSION)
    recorder = Recorder()
    catalog = ToolCatalog()
    async def handler(args, ctx):
        return ToolOutcome(status="ok", cost_usd=Decimal(0))
    for name in tools:
        catalog.register(name, handler, max_cost_usd=Decimal(0))
    model = NS(model_id="m", strict_tools=False)
    service = SpawnService(
        registry=NS(resolve_for_task=AsyncMock(return_value=ResolvedDefinition(uuid4(), uuid4(), AGENT_KEY, definition, "a" * 64))),
        authorizer=NS(check_context=AsyncMock()), budgets=recorder, runs=recorder, calls=NS(), catalog=catalog,
        models=NS(choose=lambda profile: model), resources=NS(load_context=AsyncMock(return_value={})),
        runtime=NS(run=run))
    return service, recorder


def context():
    return agent_context(NS(id=uuid4(), created_by=uuid4(), project_id=uuid4(), permissions_json={}))


def request(prompt="hello"):
    return SpawnRequest(type=AGENT_KEY, version=AGENT_VERSION, prompt=prompt)


class OutcomeTests(IsolatedAsyncioTestCase):
    async def spawn(self, run, **kwargs):
        service, recorder = service_with(run)
        return await service.spawn_subagent(request(), context(), **kwargs), recorder

    async def test_the_answer_comes_back_exactly_as_the_agent_wrote_it(self):
        answer = SubagentResult(status="PASS", summary="## Hi\n\nAll **done**.")
        result, recorder = await self.spawn(AsyncMock(return_value=answer))
        self.assertEqual(result, answer)
        self.assertEqual([(row["status"], row["error_code"]) for row in recorder.finished], [("COMPLETED", None)])
        self.assertEqual(recorder.finished[0]["result_json"]["summary"], "## Hi\n\nAll **done**.")
        self.assertEqual(len(recorder.settled), 1)

    async def test_a_provider_error_becomes_a_failed_result_carrying_the_providers_own_message(self):
        failure = ModelProviderError("model quota exhausted", code="MODEL_QUOTA_EXCEEDED", detail=PROVIDER_TEXT)
        result, recorder = await self.spawn(AsyncMock(side_effect=failure))
        self.assertEqual((result.status, result.error_code, result.summary), ("FAIL", "MODEL_QUOTA_EXCEEDED", PROVIDER_TEXT))
        self.assertEqual([(row["status"], row["error_code"]) for row in recorder.finished], [("FAILED", "MODEL_QUOTA_EXCEEDED")])
        self.assertEqual(recorder.finished[0]["result_json"]["summary"], PROVIDER_TEXT)
        self.assertEqual(len(recorder.settled), 1)  # spend is settled even though the run broke

    async def test_a_provider_error_without_a_provider_message_shows_its_own_plain_text(self):
        result, _ = await self.spawn(AsyncMock(side_effect=ModelProviderError("The model returned an empty reply.", detail="empty completion")))
        self.assertEqual(result.summary, "empty completion")
        result, _ = await self.spawn(AsyncMock(side_effect=ModelProviderError("model provider unreachable")))
        self.assertEqual((result.error_code, result.summary), ("MODEL_PROVIDER_ERROR", "model provider unreachable"))

    async def test_a_provider_context_error_is_shown_as_the_provider_said_it(self):
        text = "400: This model's maximum context length is 128000 tokens."
        result, _ = await self.spawn(AsyncMock(side_effect=ModelContextLimit(text)))
        self.assertEqual((result.status, result.error_code, result.summary), ("FAIL", "MODEL_CONTEXT_LIMIT", text))

    async def test_a_model_that_is_not_configured_says_so(self):
        result, _ = await self.spawn(AsyncMock(side_effect=ModelConfigurationError("no API key")))
        self.assertEqual(result.error_code, "MODEL_PROVIDER_ERROR")
        self.assertIn("no API key", result.summary)

    async def test_a_provider_message_longer_than_a_saved_message_is_cut_not_rejected(self):
        result, _ = await self.spawn(AsyncMock(side_effect=ModelProviderError("failed", detail="x" * (MAX_ANSWER_CHARS + 50))))
        self.assertEqual(len(result.summary), MAX_ANSWER_CHARS)

    async def test_our_own_stop_reasons_get_plain_sentences_that_do_not_leak_the_cause(self):
        cases = [(ScopeDenied("tool secret detail"), "SCOPE_DENIED"), (TimeoutError("secret"), "TOOL_TIMEOUT"),
                 (UnknownToolOutcome("secret"), "UNKNOWN_REMOTE_OUTCOME")]
        for failure, code in cases:
            with self.subTest(code=code):
                result, recorder = await self.spawn(AsyncMock(side_effect=failure))
                self.assertEqual((result.status, result.error_code, result.summary), ("FAIL", code, STOPPED_SUMMARIES[code]))
                self.assertEqual(recorder.finished[0]["status"], "FAILED")
        result, _ = await self.spawn(AsyncMock(side_effect=BudgetExceeded("task_not_active")))
        self.assertEqual((result.error_code, result.summary), ("BUDGET_EXCEEDED", GENERIC_STOPPED_SUMMARY))

    async def test_an_unexpected_exception_is_a_generic_failure_that_hides_its_text(self):
        with self.assertLogs("agentforge.subagents.spawn_service", level="WARNING"):
            result, recorder = await self.spawn(AsyncMock(side_effect=RuntimeError("password=hunter2 at /srv/app.py")))
        self.assertEqual((result.status, result.error_code, result.summary), ("FAIL", "CHILD_RUNTIME_ERROR", GENERIC_STOPPED_SUMMARY))
        self.assertNotIn("hunter2", str(recorder.finished))

    async def test_a_stop_request_is_recorded_as_cancelled_and_passed_on(self):
        service, recorder = service_with(AsyncMock(side_effect=asyncio.CancelledError()))
        with self.assertRaises(asyncio.CancelledError):
            await service.spawn_subagent(request(), context())
        self.assertEqual([(row["status"], row["error_code"]) for row in recorder.finished], [("CANCELLED", "USER_CANCELLED")])
        self.assertEqual(len(recorder.settled), 1)

    async def test_a_result_is_never_partial(self):
        for run in (AsyncMock(return_value=SubagentResult(status="PASS", summary="ok")),
                    AsyncMock(side_effect=ModelProviderError("down", detail="503: overloaded")),
                    AsyncMock(side_effect=ScopeDenied("x"))):
            result, _ = await self.spawn(run)
            self.assertIn(result.status, ("PASS", "FAIL"))


class SetupTests(IsolatedAsyncioTestCase):
    async def test_the_conversation_follows_the_platform_rules_and_reaches_the_loop_as_given(self):
        run = AsyncMock(return_value=SubagentResult(status="PASS", summary="ok"))
        service, recorder = service_with(run)
        conversation = [{"role": "user", "content": "first"}, {"role": "assistant", "content": "reply"}, {"role": "user", "content": "second"}]
        await service.spawn_subagent(request("second"), context(), conversation=conversation)
        messages = run.await_args.kwargs["messages"]
        self.assertEqual(messages[0]["role"], "developer")
        # The rules for the tools offered (web_search here) are in the prompt; the tool names themselves travel as schemas.
        self.assertIn("## Research", messages[0]["content"])
        self.assertNotIn("Tools: web_search", messages[0]["content"])
        self.assertEqual(messages[1:], conversation)

    async def test_without_a_conversation_the_request_prompt_is_the_one_user_message(self):
        run = AsyncMock(return_value=SubagentResult(status="PASS", summary="ok"))
        service, _ = service_with(run)
        await service.spawn_subagent(request("just this"), context())
        self.assertEqual(run.await_args.kwargs["messages"][1:], [{"role": "user", "content": "just this"}])

    async def test_the_stream_is_handed_to_the_loop_only_when_there_is_one(self):
        run = AsyncMock(return_value=SubagentResult(status="PASS", summary="ok"))
        service, _ = service_with(run)
        await service.spawn_subagent(request(), context())
        self.assertNotIn("stream", run.await_args.kwargs)
        live = object()
        await service.spawn_subagent(request(), context(), stream=live)
        self.assertIs(run.await_args.kwargs["stream"], live)

    async def test_the_run_is_recorded_before_it_starts_and_the_reservation_does_not_limit_it(self):
        run = AsyncMock(return_value=SubagentResult(status="PASS", summary="ok"))
        service, recorder = service_with(run)
        run_id = uuid4()
        await service.spawn_subagent(request(), context(), run_id=run_id)
        self.assertEqual(recorder.started[0]["run_id"], run_id)
        self.assertEqual(recorder.started[0]["definition_sha256"], "a" * 64)
        reserved = recorder.reserved[0]
        self.assertGreaterEqual(reserved["amount_usd"], 100_000)
        self.assertGreaterEqual(reserved["max_steps"], 1_000_000)
        self.assertGreaterEqual(reserved["max_tool_calls"], 1_000_000)

    async def test_an_agent_with_no_available_tools_fails_before_any_money_is_reserved(self):
        run = AsyncMock()
        service, recorder = service_with(run, tools=())
        result = await service.spawn_subagent(request(), context())
        self.assertEqual((result.status, result.error_code), ("FAIL", "TOOL_UNAVAILABLE"))
        run.assert_not_awaited()
        self.assertEqual((recorder.reserved, recorder.started, recorder.finished), ([], [], []))

    async def test_the_current_authority_is_checked_before_anything_starts(self):
        run = AsyncMock()
        service, recorder = service_with(run)
        authority = AsyncMock(side_effect=PermissionError("lease lost"))
        with self.assertRaises(PermissionError):
            await service.spawn_subagent(request(), context(), authority=authority)
        run.assert_not_awaited()
        self.assertEqual(recorder.reserved, [])


    async def test_a_delegated_child_is_offered_only_the_extra_tools_its_parent_left_it(self):
        from dataclasses import replace
        from agentforge.tools.catalog import PluginTool
        run = AsyncMock(return_value=SubagentResult(status="PASS", summary="ok"))
        service, recorder = service_with(run, tools=("web_search", "use_skill"))
        async def handler(args, ctx):
            return ToolOutcome(status="ok", cost_usd=Decimal(0))
        service.catalog.register_plugin("tracker__create_issue", handler, PluginTool("p", "Tracker", "create_issue", "", "", {"type": "object"}, False), timeout_sec=5)
        service.catalog.register_plugin("tracker__search", handler, PluginTool("p", "Tracker", "search", "", "", {"type": "object"}, True), timeout_sec=5)
        await service.spawn_subagent(request(), context())
        self.assertTrue({"use_skill", "tracker__create_issue", "tracker__search"} <= set(run.await_args.kwargs["scope"].allowed_tools))
        child = replace(context(), depth=1, parent_run_id=uuid4(), parent_tools=("web_search", "use_skill", "tracker__search"))
        await service.spawn_subagent(request(), child)
        allowed = run.await_args.kwargs["scope"].allowed_tools
        self.assertEqual(set(allowed), {"web_search", "use_skill", "tracker__search"})
        self.assertEqual(recorder.started[-1]["effective_permissions"]["allowed_tools"], list(allowed))


class BuiltinResolutionTests(IsolatedAsyncioTestCase):
    """The built-in is re-resolved before every step; the shared definition row is not touched each time."""

    async def test_a_resolved_built_in_is_reused_for_a_minute(self):
        from unittest.mock import patch
        from agentforge.subagents import registry as module
        resolved = ResolvedDefinition(uuid4(), uuid4(), AGENT_KEY, load_builtin("agent", AGENT_VERSION), "a" * 64)
        reads, now = [], [0.0]
        reg = module.AgentRegistry(None, NS(check_context=AsyncMock()))
        module._builtin_cache.clear()
        self.addCleanup(module._builtin_cache.clear)
        with patch.object(module.AgentRegistry, "_resolve_builtin", lambda self, *args: reads.append(args) or resolved), \
             patch.object(module.time, "monotonic", lambda: now[0]):
            for _ in range(3):
                self.assertIs(await reg.resolve_for_task(AGENT_KEY, AGENT_VERSION, NS()), resolved)
            self.assertEqual(len(reads), 1)
            self.assertEqual(reg.authorizer.check_context.await_count, 3)  # ownership is still checked every time
            now[0] += module.BUILTIN_CACHE_SEC + 1
            await reg.resolve_for_task(AGENT_KEY, AGENT_VERSION, NS())
            self.assertEqual(len(reads), 2)
