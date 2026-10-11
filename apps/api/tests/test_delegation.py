"""Delegation permissions, context isolation, truthful results and cancellation."""
import asyncio
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace as NS
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock
from uuid import uuid4

from pydantic import ValidationError
from agentforge.contracts.subagent import SubagentResult
from agentforge.subagents.registry import AGENT_VERSION, load_builtin
from agentforge.subagents.scope import ScopeDenied, intersect_and_validate
from agentforge.tools.catalog import ToolCatalog
from agentforge.tools.contracts import DelegateTaskArgs
from agentforge.tools.delegation import DelegatedStream, delegated_run_id, delegation_context, register_delegation
from test_spawn_service import context


def execution():
    ctx = context()
    scope = intersect_and_validate(load_builtin("agent", AGENT_VERSION), ctx, ())
    return NS(task=ctx, scope=scope, agent_run_id=uuid4(), operation_key="parent:tool:call-1", authorize_current=AsyncMock())


class ScopeTests(TestCase):
    def test_research_cannot_write_execute_publish_or_delegate(self):
        parent = execution()
        child = delegation_context(parent, "research")
        scope = intersect_and_validate(load_builtin("agent", AGENT_VERSION), child, ())
        self.assertTrue(scope.permissions.web)
        self.assertFalse(scope.permissions.sandbox_exec)
        self.assertFalse(scope.permissions.sandbox_write)
        self.assertTrue(set(scope.allowed_tools).issubset(parent.scope.allowed_tools))
        self.assertFalse(set(scope.allowed_tools) & {"write", "edit", "bash", "create_pull_request", "delegate_task", "create_task_artifact"})
        self.assertEqual((scope.budget.max_steps, scope.budget.max_tool_calls, scope.budget.max_wall_time_sec), (40, 80, 600))
        self.assertEqual(scope.budget.max_llm_cost_usd, 5)

    def test_parent_tool_restrictions_are_not_reexpanded(self):
        parent = execution()
        parent.scope = replace(parent.scope, allowed_tools=("web_search", "delegate_task"))
        scope = intersect_and_validate(load_builtin("agent", AGENT_VERSION), delegation_context(parent, "work"), ())
        self.assertEqual(scope.allowed_tools, ("web_search",))

    def test_nested_and_forged_contexts_are_rejected(self):
        parent = execution()
        with self.assertRaises(ScopeDenied):
            intersect_and_validate(load_builtin("agent", AGENT_VERSION), replace(parent.task, depth=1), ())
        parent.task = delegation_context(parent, "work")
        with self.assertRaises(ScopeDenied):
            delegation_context(parent, "work")

    def test_whitespace_and_extra_authority_fields_are_rejected(self):
        for values in ({"title": "   ", "prompt": "specific assignment"},
                       {"title": "Review", "prompt": "specific assignment", "parent_run_id": str(uuid4())}):
            with self.assertRaises(ValidationError):
                DelegateTaskArgs(**values)

    def test_child_notes_are_scoped_bounded_and_retries_discard_them(self):
        events = []
        stream = DelegatedStream(lambda kind, data: events.append((kind, data)), uuid4(), uuid4())
        stream.feed("abandoned")
        stream.begin()
        stream.feed("x" * 9000)
        stream.commit_narration()
        self.assertEqual(len(events), 1)
        self.assertEqual(len(events[0][1]["text"]), 3000)
        self.assertIn("parent_run_id", events[0][1])
        stream.feed("final")
        stream.commit_reply()
        self.assertEqual(len(events), 1)


class DelegateTests(IsolatedAsyncioTestCase):
    async def call(self, service, parent=None):
        catalog = ToolCatalog()
        register_delegation(catalog, service)
        self.assertTrue(catalog.get("delegate_task").has_effects)
        return await catalog.get("delegate_task").handler(
            DelegateTaskArgs(title="Review code", prompt="Review the auth module"), parent or execution())

    async def test_result_and_authority_return_without_conversation_leak(self):
        service = NS(spawn_subagent=AsyncMock(return_value=SubagentResult(status="PASS", summary="Observed result")), lifecycle=None)
        parent = execution()
        result = await self.call(service, parent)
        kwargs = service.spawn_subagent.await_args.kwargs
        self.assertEqual(kwargs["run_id"], delegated_run_id(parent.operation_key))
        self.assertIs(kwargs["authority"], parent.authorize_current)
        self.assertEqual(len(kwargs["conversation"]), 1)
        self.assertIn("Review the auth module", kwargs["conversation"][0]["content"])
        self.assertEqual(result.data["summary"], "Observed result")
        self.assertEqual(result.cost_usd, Decimal(0))

    async def test_failure_is_not_reported_as_success(self):
        service = NS(spawn_subagent=AsyncMock(return_value=SubagentResult(status="FAIL", summary="Timed out", error_code="SUBAGENT_TIMEOUT")), lifecycle=None)
        result = await self.call(service)
        self.assertEqual((result.status, result.error_code), ("error", "SUBAGENT_TIMEOUT"))

    async def test_a_child_that_stopped_early_hands_back_what_it_had_written(self):
        async def spawn(request, ctx, *, stream, **kwargs):
            for note in ("Read the auth module.", "Found the session check in middleware.py.", "Next: compare with the tests."):
                stream.begin()
                stream.feed(note)
                stream.commit_narration()
            return SubagentResult(status="FAIL", summary="The subagent reached its time limit.", error_code="SUBAGENT_TIMEOUT")
        service = NS(spawn_subagent=spawn, lifecycle=None)
        result = await self.call(service)
        self.assertEqual((result.status, result.error_code), ("error", "SUBAGENT_TIMEOUT"))
        self.assertEqual(result.data["progress_notes"][-1], "Next: compare with the tests.")
        self.assertEqual(len(result.data["progress_notes"]), 3)

    async def test_progress_notes_are_bounded_and_only_sent_with_a_failure(self):
        stream = DelegatedStream(None, uuid4(), uuid4())
        for number in range(12):
            stream.begin()
            stream.feed(f"note {number} " + "x" * 2000)
            stream.commit_narration()
        self.assertEqual(len(stream.notes), 5)
        self.assertTrue(stream.notes[0].startswith("note 7 "))
        self.assertTrue(all(len(note) <= 600 for note in stream.notes))
        async def spawn(request, ctx, *, stream, **kwargs):
            stream.begin()
            stream.feed("A note")
            stream.commit_narration()
            return SubagentResult(status="PASS", summary="All done.")
        passed = await self.call(NS(spawn_subagent=spawn, lifecycle=None))
        self.assertNotIn("progress_notes", passed.data)

    async def test_a_research_child_is_left_no_plugin_tool_that_changes_something(self):
        # The catalog decides what has an effect: a plugin tool not marked read-only does, whatever its name.
        from agentforge.tools.catalog import PluginTool
        catalog = ToolCatalog()
        async def handler(args, ctx):
            return None
        catalog.register_plugin("tracker__create_issue", handler, PluginTool("p", "Tracker", "create_issue", "", "", {"type": "object"}, False), timeout_sec=5)
        catalog.register_plugin("tracker__search", handler, PluginTool("p", "Tracker", "search", "", "", {"type": "object"}, True), timeout_sec=5)
        catalog.register("use_skill", handler, max_cost_usd=Decimal(0))
        service = NS(spawn_subagent=AsyncMock(return_value=SubagentResult(status="PASS", summary="ok")), lifecycle=None)
        register_delegation(catalog, service)
        parent = execution()
        extras = ("tracker__create_issue", "tracker__search", "use_skill")
        parent.scope = replace(parent.scope, allowed_tools=(*parent.scope.allowed_tools, *extras))
        for mode, kept in (("research", {"tracker__search", "use_skill"}), ("work", set(extras))):
            await catalog.get("delegate_task").handler(DelegateTaskArgs(title="Look", prompt="Find the open issues", mode=mode), parent)
            child = service.spawn_subagent.await_args.args[1]
            with self.subTest(mode=mode):
                self.assertEqual(set(child.parent_tools) & set(extras), kept)
                self.assertEqual("write" in child.parent_tools, mode == "work")

    async def test_stop_propagates_to_parent(self):
        service = NS(spawn_subagent=AsyncMock(side_effect=asyncio.CancelledError()), lifecycle=None)
        with self.assertRaises(asyncio.CancelledError):
            await self.call(service)

    async def test_uncertain_child_effect_stops_parent_instead_of_inviting_a_replay(self):
        from agentforge.tools.gateway import UnknownToolOutcome
        service = NS(spawn_subagent=AsyncMock(return_value=SubagentResult(status="FAIL", summary="Unconfirmed action", error_code="UNKNOWN_REMOTE_OUTCOME")), lifecycle=None)
        with self.assertRaises(UnknownToolOutcome):
            await self.call(service)
