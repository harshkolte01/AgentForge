"""Delegation using the existing authorization, approvals and durable run ledger. Several assignments of one turn run together."""
from dataclasses import replace
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

from agentforge.contracts.agent_definition import Budget
from agentforge.contracts.subagent import SpawnRequest
from agentforge.subagents.budget import BudgetExceeded
from agentforge.subagents.registry import AGENT_KEY, AGENT_VERSION
from agentforge.subagents.scope import ScopeDenied
from .catalog import EFFECT_TOOLS
from .contracts import DelegateTaskArgs, ToolOutcome
from .errors import ToolError


# What a child that did not finish hands back of its own progress: its last few notes, so the parent can build on the work
# instead of starting over. Bounded, because the parent reads it as part of one tool result.
KEPT_NOTES = 5
KEPT_NOTE_CHARS = 600


class DelegatedStream:
    """Persist child progress notes without touching the main reply's live stream."""
    def __init__(self, emit, run_id, parent_run_id):
        self.emit, self.run_id, self.parent_run_id = emit, run_id, parent_run_id
        self.text = ""
        self.notes: list[str] = []

    def begin(self):
        self.text = ""

    def feed(self, text):
        self.text = (self.text + text)[:3000]

    feed_narration = feed

    def end(self):
        pass

    def commit_narration(self):
        if self.text.strip():
            self.notes = [*self.notes, self.text.strip()[:KEPT_NOTE_CHARS]][-KEPT_NOTES:]
        if self.emit is not None and self.text.strip():
            self.emit("assistant.message", {"text": self.text.strip(), "agent_run_id": str(self.run_id),
                "parent_run_id": str(self.parent_run_id)})
        self.text = ""

    def commit_reply(self):
        self.text = ""  # The complete final report is persisted on AgentRun.

    discard = begin


def delegated_run_id(operation_key: str):
    return uuid5(NAMESPACE_URL, f"agentforge:delegation:{operation_key}")


def delegation_context(execution, mode):
    if execution.task.depth or execution.task.parent_run_id is not None:
        raise ScopeDenied("subagents cannot delegate")
    permissions = execution.scope.permissions
    if mode == "research":
        permissions = permissions.model_copy(update={"sandbox_write": False, "sandbox_exec": False})
    limits = {"max_steps": 40, "max_tool_calls": 80, "max_wall_time_sec": 600, "max_llm_cost_usd": 5.0}
    budget = Budget.model_validate({name: min(value, getattr(execution.scope.budget, name)) for name, value in limits.items()})
    return replace(execution.task, depth=1, parent_run_id=execution.agent_run_id,
        parent_tools=tuple(name for name in execution.scope.allowed_tools if mode != "research" or name not in EFFECT_TOOLS),
        parent_permissions=permissions, parent_budget=budget)


def register_delegation(catalog, service):
    async def delegate(args: DelegateTaskArgs, execution):
        ctx = delegation_context(execution, args.mode)
        run_id = delegated_run_id(execution.operation_key)
        stream = DelegatedStream(service.lifecycle, run_id, execution.agent_run_id)
        try:
            result = await service.spawn_subagent(
                SpawnRequest(type=AGENT_KEY, version=AGENT_VERSION, prompt=args.prompt), ctx,
                run_id=run_id, authority=execution.authorize_current, assignment_title=args.title,
                stream=stream,
                conversation=[{"role": "user", "content": (
                    "You are a delegated subagent. Complete only the assignment below and return your findings to the parent agent. "
                    "Include evidence, changed paths, checks and any limitations. Do not address the person as if you own the whole task. "
                    "The workspace is shared, and other subagents may be working in it at the same time on other assignments: stay "
                    "within yours, preserve other work, and say so if you needed to change something outside it. "
                    "You cannot delegate or publish a pull request.\n\n" + args.prompt)}])
        except BudgetExceeded:
            raise ToolError("DELEGATION_LIMIT", "A subagent could not start: the delegation allowance is exhausted or another run is still active. Continue the task yourself.") from None
        except ScopeDenied:
            raise ToolError("SCOPE_DENIED", "The assignment has no tools within your current grants, or delegation is not authorized.") from None
        if result.error_code == "UNKNOWN_REMOTE_OUTCOME":
            from .gateway import UnknownToolOutcome
            raise UnknownToolOutcome("delegated_action_outcome_unknown")
        # The complete report remains on the child run; the tool snapshot also supports paged context retrieval.
        report = result.summary.encode("utf-8")[:60000].decode("utf-8", errors="ignore")
        report_reference = (service.calls.store_text_result(ctx.task_id, execution.operation_key + ":report", result.summary)
                            if report != result.summary else None)
        return ToolOutcome(status="ok" if result.status == "PASS" else "error", cost_usd=Decimal(0),
            error_code=(result.error_code or "CHILD_RUNTIME_ERROR") if result.status == "FAIL" else None,
            data={"run_id": str(run_id), "title": args.title, "status": result.status,
                  "summary": report, "truncated": report != result.summary,
                  **({"report_resource_id": report_reference} if report_reference else {}),
                  # A child that stopped early has no report; what it had written so far is still the parent's to use.
                  **({"progress_notes": stream.notes} if result.status == "FAIL" and stream.notes else {})})

    # The parent gateway waits while the child works; the child owns its model/tool charges.
    catalog.register("delegate_task", delegate, max_cost_usd=Decimal(0), timeout_sec=660)
