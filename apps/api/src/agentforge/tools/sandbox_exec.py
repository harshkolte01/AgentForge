"""One execution seam for model tools and parent-required objective checks."""
import asyncio
from datetime import datetime, timezone
from dataclasses import asdict
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5
from agentforge.subagents.budget import BudgetService
from agentforge.subagents.scope import ScopeDenied
from agentforge.sandbox.workspace import workspace_revision
from .contracts import ToolOutcome
from .test_tools import observed_test_count, qualification


class CodingExecution:
    def __init__(self, repository, runner, *, max_cost_usd, configured=True, timeout_sec=330):
        self.repository, self.runner = repository, runner
        self.max_cost_usd, self.configured = max_cost_usd, configured
        self.timeout_sec = timeout_sec

    async def execute(self, ctx, profile_id, *, operation_key, authority, kind="test"):
        if not self.configured:
            return ToolOutcome(status="error", error_code="SANDBOX_CONFIGURATION_REQUIRED", cost_usd=Decimal(0))
        await authority()
        state = self.repository.session(ctx)
        revision = state["revision"]
        saved = self.repository.begin_operation(ctx, operation_key=operation_key,
            kind=kind, inputs={"profile_id": profile_id}, revision=revision)
        if saved is not None:
            return self._outcome(saved, saved["payload"])
        files = self.repository.files(ctx, revision=revision)
        result = await self.runner.run(files, profile_id, operation_id=operation_key, authority=authority)
        await authority()
        if result.revision != workspace_revision(files) or result.profile_id != profile_id:
            raise ScopeDenied("sandbox result revision or profile mismatch")
        if result.cost_usd is not None and result.cost_usd > self.max_cost_usd:
            raise ValueError("sandbox actual cost exceeded authorized bound")
        payload = asdict(result)
        payload["argv"] = list(result.argv)
        payload["cost_usd"] = str(result.cost_usd) if result.cost_usd is not None else None
        payload["observed_test_count"] = observed_test_count(profile_id, result.stdout, result.stderr) if kind == "test" else None
        payload.update(qualification(files, self.repository.files(ctx, baseline=True), payload))
        log = (f"# Actual {kind} execution\n\nProfile: {profile_id}\nRevision: {revision}\n"
            f"Provider: {result.provider}\nArgv: {result.argv!r}\nExit: {result.exit_code}\n"
            f"Timed out: {result.timed_out}\nCleanup confirmed: {result.cleanup_confirmed}\n"
            f"Output truncated: {result.output_truncated}\n\n## stdout\n\n{result.stdout}\n\n## stderr\n\n{result.stderr}\n").encode()
        saved = self.repository.record_evidence(ctx, operation_key=operation_key, kind=kind,
            revision=revision, payload=payload, artifact_content=log, filename=f"{profile_id}.log", mime_type="text/plain; charset=utf-8")
        return self._outcome(saved, payload)

    @staticmethod
    def _outcome(saved, payload):
        # An executed failed test is still a successful tool response; its actual
        # nonzero exit and qualification fields remain visible to the parent.
        preview = {key: value for key, value in payload.items() if key not in {"stdout", "stderr"}}
        preview.update(stdout=payload.get("stdout", "")[:4000], stderr=payload.get("stderr", "")[:4000])
        return ToolOutcome(status="ok", data=preview, evidence_ids=(saved["evidence_id"],),
            artifact_ids=(saved["artifact_id"],) if saved.get("artifact_id") else (),
            cost_usd=Decimal(payload["cost_usd"]) if payload.get("cost_usd") is not None else None)

    async def parent_check(self, ctx, profile_id, *, operation_key, authority, kind="test"):
        # The parent uses the same durable operation and budget boundary as tools.
        # A saved exact operation is reused before reserving or any provider call.
        await authority()
        prior = self.repository.operation(ctx, operation_key)
        if prior is not None:
            if prior["status"] != "SUCCEEDED":
                from agentforge.tools.gateway import UnknownToolOutcome
                raise UnknownToolOutcome("parent test outcome is unknown")
            if (prior["revision"] != self.repository.session(ctx)["revision"] or prior["kind"] != kind
                    or prior["result"]["payload"]["profile_id"] != profile_id):
                raise ScopeDenied("parent test operation revision, kind or profile mismatch")
            return self._outcome(prior["result"], prior["result"]["payload"])
        if not self.configured:
            return ToolOutcome(status="error", error_code="SANDBOX_CONFIGURATION_REQUIRED", cost_usd=Decimal(0))
        await authority()
        budgets = BudgetService(self.repository.session_factory, max_task_spawns=32)
        reservation = budgets.reserve(task_id=ctx.task_id, run_id=uuid5(NAMESPACE_URL, f"agentforge:{ctx.task_id}:{operation_key}"),
            amount_usd=self.max_cost_usd, owner_id=ctx.user_id, project_id=ctx.project_id,
            max_steps=1, max_tool_calls=1, max_wall_time_sec=min(ctx.task_budget.max_wall_time_sec, int(self.timeout_sec)))
        authorized = False
        try:
            budgets.authorize_operation(reservation.id, operation_key, self.max_cost_usd, kind="tool")
            authorized = True
            remaining = max(0, (reservation.deadline - datetime.now(timezone.utc)).total_seconds())
            async with asyncio.timeout(remaining):
                outcome = await self.execute(ctx, profile_id, operation_key=operation_key, authority=authority, kind=kind)
            budgets.record_usage(reservation.id, operation_key, outcome.cost_usd)
            authorized = False
            return outcome
        finally:
            if authorized:
                budgets.record_usage(reservation.id, operation_key, None)
            budgets.settle(reservation.id)


def register_sandbox_exec(catalog, execution):
    async def command(args, context):
        if args.args:
            raise ScopeDenied("command profiles do not accept model-provided arguments")
        outcome = await execution.execute(context.task, args.command_id, operation_key=context.operation_key,
            authority=context.authorize_current, kind="build" if args.command_id == "node_build" else "test")
        if outcome.status == "ok" and outcome.data.get("cleanup_confirmed") is not True:
            from agentforge.tools.gateway import UnknownToolOutcome
            raise UnknownToolOutcome("sandbox cleanup outcome is unknown")
        return outcome
    async def tests(args, context):
        if args.test_profile == "node_build":
            raise ScopeDenied("build profile is not a test profile")
        outcome = await execution.execute(context.task, args.test_profile, operation_key=context.operation_key,
            authority=context.authorize_current, kind="test")
        if outcome.status == "ok" and outcome.data.get("cleanup_confirmed") is not True:
            from agentforge.tools.gateway import UnknownToolOutcome
            raise UnknownToolOutcome("sandbox cleanup outcome is unknown")
        return outcome
    catalog.register("run_sandbox_command", command, max_cost_usd=execution.max_cost_usd, timeout_sec=execution.timeout_sec)
    catalog.register("run_tests", tests, max_cost_usd=execution.max_cost_usd, timeout_sec=execution.timeout_sec)
