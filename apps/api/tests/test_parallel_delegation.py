"""Several delegations in one turn work side by side; every other effect still runs alone, in order.

The gateway and the loop are the real ones. A barrier inside the handlers makes serialized execution fail loudly: the first
assignment would wait for the others to start, and they could not."""
import asyncio
import json
from decimal import Decimal
from types import SimpleNamespace as NS
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from agentforge.contracts.agent_definition import Budget
from agentforge.tools.catalog import DESCRIPTIONS, EFFECT_TOOLS, ToolCatalog
from agentforge.tools.delegation import delegated_run_id
from agentforge.tools.gateway import ToolGateway

from test_child_runtime import ScriptedModel, body, calls, run, text, tool_messages
from test_tool_gateway import FakeBudgets, FakeCalls, FakeResources, ok

TOOLS = ("delegate_task", "bash", "write")


def brief(title):
    return {"title": title, "prompt": f"Look into {title} and report what you find."}


def build(handlers, *, ctx=None, approvals=None):
    catalog = ToolCatalog()
    for name in TOOLS:
        catalog.register(name, handlers[name], max_cost_usd=Decimal(0), timeout_sec=5)
    events = []
    gateway = ToolGateway(
        catalog=catalog, calls=FakeCalls(), budgets=FakeBudgets(), resources=FakeResources({}), reauthorize=AsyncMock(),
        ctx=ctx or NS(task_id=uuid4(), parent_run_id=None), scope=NS(allowed_tools=TOOLS, permissions=NS(), budget=Budget()),
        run_id=uuid4(), reservation_id=uuid4(), lifecycle=lambda kind, payload: events.append((kind, payload)), approvals=approvals)
    return gateway, events


class ParallelDelegationTests(IsolatedAsyncioTestCase):
    def setUp(self):
        patcher = patch("agentforge.tools.gateway.authorize_tool")
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_assignments_requested_together_all_start_before_any_finishes(self):
        started, everyone = [], asyncio.Event()

        async def delegate(args, ctx):
            started.append(args.title)
            if len(started) == 3:
                everyone.set()
            await asyncio.wait_for(everyone.wait(), 2)
            return ok(summary=f"report on {args.title}", title=args.title)

        gateway, _ = build({"delegate_task": delegate, "bash": AsyncMock(), "write": AsyncMock()})
        titles = ["Auth flow", "Billing", "Search index"]
        model = ScriptedModel([calls(*(("delegate_task", brief(title)) for title in titles)), text("Integrated all three reports.")])
        result = await run(model, gateway)
        self.assertEqual(result.summary, "Integrated all three reports.")
        self.assertEqual(sorted(started), sorted(titles))
        replies = tool_messages(model, 1)
        self.assertEqual([m["tool_call_id"] for m in replies], ["call-0-delegate_task", "call-1-delegate_task", "call-2-delegate_task"])
        self.assertEqual([body(m)["data"]["title"] for m in replies], titles)  # answers arrive in the order they were asked

    async def test_a_delegation_does_not_hold_up_the_others_when_one_is_slow(self):
        order = []

        async def delegate(args, ctx):
            await asyncio.sleep(0.15 if args.title == "Slow one" else 0.01)
            order.append(args.title)
            return ok(title=args.title)

        gateway, _ = build({"delegate_task": delegate, "bash": AsyncMock(), "write": AsyncMock()})
        model = ScriptedModel([calls(("delegate_task", brief("Slow one")), ("delegate_task", brief("Quick one"))), text("done")])
        await run(model, gateway)
        self.assertEqual(order, ["Quick one", "Slow one"])

    async def test_a_delegation_next_to_a_command_keeps_the_commands_serial_order(self):
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

        gateway, _ = build({"delegate_task": tracked("delegate"), "bash": tracked("bash"), "write": tracked("write")})
        turn = calls(("delegate_task", brief("Survey")), ("bash", {"command": "npm test"}), ("delegate_task", brief("Review")))
        await run(ScriptedModel([turn, text("ok")]), gateway)
        self.assertEqual(running["order"], ["delegate", "bash", "delegate"])
        self.assertEqual(running["peak"], 1)  # a turn that also changes something stays strictly in order

    async def test_commands_and_edits_still_never_overlap(self):
        running = {"now": 0, "peak": 0}

        async def handler(args, ctx):
            running["now"] += 1
            running["peak"] = max(running["peak"], running["now"])
            await asyncio.sleep(0.02)
            running["now"] -= 1
            return ok()

        gateway, _ = build({"delegate_task": AsyncMock(), "bash": handler, "write": handler})
        await asyncio.gather(gateway.execute("bash", json.dumps({"command": "make"}), "a"),
                             gateway.execute("write", json.dumps({"path": "x", "content": "y"}), "b"))
        self.assertEqual(running["peak"], 1)

    async def test_only_delegations_are_parallel_among_effects(self):
        gateway, _ = build({name: AsyncMock() for name in TOOLS})
        self.assertTrue(gateway.is_parallel_safe("delegate_task"))
        self.assertFalse(gateway.is_parallel_safe("bash"))
        self.assertFalse(gateway.is_parallel_safe("write"))
        self.assertIn("delegate_task", EFFECT_TOOLS)  # still an effect: an uncertain outcome stops the parent

    async def test_the_start_event_names_the_child_so_its_progress_lands_on_this_card(self):
        async def delegate(args, ctx):
            return ok()

        gateway, events = build({"delegate_task": delegate, "bash": AsyncMock(), "write": AsyncMock()})
        await gateway.execute("delegate_task", json.dumps(brief("Survey")), "provider-call")
        start = next(payload for kind, payload in events if kind == "tool.started")
        self.assertEqual(start["child_run_id"], str(delegated_run_id(f"{gateway.run_id}:tool:provider-call")))
        finish = next(payload for kind, payload in events if kind == "tool.finished")
        self.assertNotIn("child_run_id", finish)  # the finish is matched to the start by tool_call_id

    async def test_other_tools_start_events_carry_no_child(self):
        async def run_it(args, ctx):
            return ok()

        gateway, events = build({"delegate_task": AsyncMock(), "bash": run_it, "write": AsyncMock()})
        await gateway.execute("bash", json.dumps({"command": "ls"}), "c1")
        self.assertNotIn("child_run_id", next(payload for kind, payload in events if kind == "tool.started"))


class ApprovalAttributionTests(IsolatedAsyncioTestCase):
    def setUp(self):
        patcher = patch("agentforge.tools.gateway.authorize_tool")
        patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def approvals(asked):
        async def allows(ctx, **kwargs):
            asked.append(kwargs)
            return True
        return NS(allows=allows)

    async def test_a_subagents_approval_says_whose_it_is(self):
        asked = []
        ctx = NS(task_id=uuid4(), parent_run_id=uuid4())
        gateway, _ = build({name: AsyncMock(return_value=ok()) for name in TOOLS}, ctx=ctx, approvals=self.approvals(asked))
        await gateway.execute("write", json.dumps({"path": "a.txt", "content": "x"}), "w1")
        self.assertEqual(asked[0]["agent_run_id"], str(gateway.run_id))

    async def test_the_main_agents_own_approval_is_unchanged(self):
        asked = []
        gateway, _ = build({name: AsyncMock(return_value=ok()) for name in TOOLS}, approvals=self.approvals(asked))
        await gateway.execute("write", json.dumps({"path": "a.txt", "content": "x"}), "w1")
        self.assertNotIn("agent_run_id", asked[0])


class DescriptionTests(IsolatedAsyncioTestCase):
    async def test_the_model_is_told_to_launch_independent_assignments_together(self):
        description = DESCRIPTIONS["delegate_task"]
        self.assertIn("parallel", description)
        self.assertNotIn("sequentially", description)
        self.assertIn("different files", description)
