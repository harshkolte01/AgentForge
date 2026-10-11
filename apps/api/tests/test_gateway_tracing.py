"""The gateway shows approvals and refused calls in the trace as guardrail checks; calls that simply run add nothing."""
import json
from contextlib import contextmanager
from types import SimpleNamespace as NS
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from test_tool_gateway import args, make


class GatewayTracingTests(IsolatedAsyncioTestCase):
    def setUp(self):
        patcher = patch("agentforge.tools.gateway.authorize_tool")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.spans = []

    def gateway(self, *, allowed=True):
        gateway, calls, _ = make({})
        spans = self.spans

        @contextmanager
        def trace(name, kind=None):
            attributes = {}
            spans.append((name, kind, attributes))
            yield NS(set_attribute=attributes.__setitem__)
        gateway.trace = trace
        gateway.approvals = NS(allows=AsyncMock(return_value=allowed))
        return gateway

    async def test_an_approved_change_shows_a_passed_approval_check(self):
        outcome = await self.gateway(allowed=True).execute("write", args(path="a.txt", content="x"), "c1")
        self.assertEqual(outcome.status, "ok")
        [(name, kind, attributes)] = self.spans
        self.assertEqual((name, kind), ("tool_approval", "GUARDRAIL"))
        self.assertEqual((attributes["neatlogs.guardrail.passed"], attributes["neatlogs.guardrail.reason"], attributes["tool.name"]),
                         (True, "APPROVED", "write"))

    async def test_a_declined_change_shows_one_blocked_approval_check(self):
        outcome = await self.gateway(allowed=False).execute("write", args(path="a.txt", content="x"), "c1")
        self.assertEqual(outcome.error_code, "DECLINED")
        [(name, _, attributes)] = self.spans   # the decline is not reported a second time as a policy refusal
        self.assertEqual(name, "tool_approval")
        self.assertEqual((attributes["neatlogs.guardrail.passed"], attributes["neatlogs.guardrail.reason"]), (False, "DECLINED"))

    async def test_a_refused_call_shows_a_failed_policy_check_with_its_code_but_not_its_arguments(self):
        outcome = await self.gateway().execute("read_repo_file", args(path="a.py", line_start="SECRET-VALUE"), "c1")
        self.assertEqual(outcome.error_code, "SCHEMA_INVALID")
        [(name, kind, attributes)] = self.spans
        self.assertEqual((name, kind), ("tool_policy", "GUARDRAIL"))
        self.assertEqual((attributes["neatlogs.guardrail.passed"], attributes["neatlogs.guardrail.reason"],
                          attributes["agentforge.error.code"], attributes["tool.name"]), (False, "SCHEMA_INVALID", "SCHEMA_INVALID", "read_repo_file"))
        self.assertNotIn("SECRET-VALUE", json.dumps(attributes, default=str))

    async def test_a_tool_the_run_may_not_use_is_recorded_as_unknown_never_by_the_models_text(self):
        outcome = await self.gateway().execute("rm_everything", args(), "c1")
        self.assertEqual(outcome.status, "error")
        [(_, _, attributes)] = self.spans
        self.assertEqual(attributes["tool.name"], "unknown")

    async def test_a_call_that_just_runs_adds_nothing(self):
        outcome = await self.gateway().execute("read_repo_file", args(path="a.py"), "c1")
        self.assertEqual(outcome.status, "ok")
        self.assertEqual(self.spans, [])

    async def test_a_tracer_that_fails_never_changes_the_calls_result(self):
        gateway = self.gateway()
        def broken(name, kind=None):
            raise RuntimeError("tracing is down")
        gateway.trace = broken
        outcome = await gateway.execute("read_repo_file", args(path="a.py", line_start="x"), "c1")
        self.assertEqual((outcome.status, outcome.error_code), ("error", "SCHEMA_INVALID"))

    async def test_without_a_tracer_nothing_changes(self):
        gateway, _, _ = make({})
        self.assertIsNone(gateway.trace)
        self.assertEqual((await gateway.execute("read_repo_file", args(path="a.py"), "c1")).status, "ok")
        self.assertEqual((await gateway.execute("read_repo_file", args(path="a.py", line_start="x"), "c2")).error_code, "SCHEMA_INVALID")
