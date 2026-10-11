"""Internal composition seam for validated, finite child invocations.

This service is intentionally not an unauthenticated HTTP endpoint. TaskContext
must be assembled by a trusted parent from server-owned grants and current user.
"""

import asyncio
import hashlib
import json
import logging
from dataclasses import replace
from collections.abc import Awaitable, Callable
from decimal import Decimal
from uuid import UUID, uuid4

from langchain_core.runnables import RunnableConfig

from agentforge.contracts.subagent import SpawnRequest, SubagentResult
from agentforge.contracts.agent_definition import Budget
from agentforge.contracts.tasks import MAX_ANSWER_CHARS
from agentforge.llm.models import ModelConfigurationError, ModelContextLimit, ModelProviderError
from agentforge.llm.router import ModelRouter
from agentforge.repositories.runs import AgentRunRepository, ToolCallRepository
from agentforge.security.policies import AuthorizationService
from agentforge.tools.catalog import ToolCatalog
from agentforge.tools.gateway import ToolGateway, UnknownToolOutcome
from agentforge.tools.lifecycle import LifecycleSink

from .budget import BudgetExceeded, BudgetService
from .environment import turn_environment
from .prompt_builder import compile_chat
from .registry import AgentRegistry
from .resources import DenyUnknownResources, ResourceResolver
from .runtime import ChildRuntime
from .scope import ScopeDenied, TaskContext, intersect_and_validate


log = logging.getLogger(__name__)

# What a person reads when a run breaks for a reason that is ours rather than the model provider's. Provider failures
# are shown with the provider's own message instead (see `_failure_text`).
STOPPED_SUMMARIES = {
    "SCOPE_DENIED": "This run stopped because it needed access it was not granted.",
    "TOOL_TIMEOUT": "A tool did not respond in time, so the run stopped.",
    "UNKNOWN_REMOTE_OUTCOME": "This run stopped because an action's outcome could not be confirmed. Nothing was repeated automatically.",
}
GENERIC_STOPPED_SUMMARY = "Something went wrong while working on this. Please try again."


def _failure_text(exc: BaseException) -> tuple[str, str]:
    """(error code, text a person reads) for a failed run. Provider errors say what the provider said."""
    if isinstance(exc, ModelProviderError):
        return exc.code, exc.shown
    if isinstance(exc, ModelContextLimit):
        return "MODEL_CONTEXT_LIMIT", str(exc)
    if isinstance(exc, ModelConfigurationError):
        return "MODEL_PROVIDER_ERROR", f"The model is not configured correctly: {exc}"
    code = ("SCOPE_DENIED" if isinstance(exc, ScopeDenied) else "BUDGET_EXCEEDED" if isinstance(exc, BudgetExceeded)
            else "TOOL_TIMEOUT" if isinstance(exc, TimeoutError) else "UNKNOWN_REMOTE_OUTCOME")
    return code, STOPPED_SUMMARIES.get(code, GENERIC_STOPPED_SUMMARY)


class SpawnService:
    def __init__(self, *, registry: AgentRegistry, authorizer: AuthorizationService,
                 budgets: BudgetService, runs: AgentRunRepository,
                 calls: ToolCallRepository, catalog: ToolCatalog, models: ModelRouter,
                 resources: ResourceResolver | None = None,
                 runtime: ChildRuntime | None = None,
                 lifecycle: LifecycleSink | None = None, approvals=None, sessions=None) -> None:
        self.registry, self.authorizer, self.budgets = registry, authorizer, budgets
        # Reads the facts of each turn for the prompt's Environment block (subagents/environment.py); None: none shown.
        self.sessions = sessions
        # Asks the person before a tool that changes something runs (tools/approvals.py); None: nothing asks.
        self.approvals = approvals
        self.runs, self.calls, self.catalog, self.models = runs, calls, catalog, models
        self.resources = resources if resources is not None else DenyUnknownResources()
        self.runtime = runtime if runtime is not None else ChildRuntime()
        # Where each gateway reports tool.started / tool.finished (the task's event log in production).
        self.lifecycle = lifecycle

    async def spawn_subagent(self, request: SpawnRequest, ctx: TaskContext, *,
                             config: RunnableConfig | None = None,
                             run_id: UUID | None = None,
                             authority: Callable[[], Awaitable[None]] | None = None,
                             stream=None,
                             conversation: list[dict] | None = None) -> SubagentResult:
        """Runs one agent on `conversation` (chat messages: earlier turns, then the person's message) and returns
        its plain-text answer, or the reason the run broke."""
        # Reject model_construct/model_copy bypasses at this trust boundary too.
        request = SpawnRequest.model_validate_json(request.model_dump_json())
        if authority is not None:
            await authority()
        await self.authorizer.check_context(ctx)
        resolved = await self.registry.resolve_for_task(request.type, request.version, ctx)
        scope = intersect_and_validate(resolved.definition, ctx, request.context_refs)
        available = self.catalog.available(scope.allowed_tools)
        if not available:
            return SubagentResult(status="FAIL", error_code="TOOL_UNAVAILABLE", summary="No tools are available for this agent, so it cannot run.")
        async with asyncio.timeout(30):
            context = await self.resources.load_context(ctx, scope.context_refs)
        model = self.models.choose(resolved.definition.model_profile)
        run_id = run_id or uuid4()
        amount = Decimal(str(scope.budget.max_llm_cost_usd))
        if request.requested_budget_usd is not None:
            amount = min(amount, Decimal(str(request.requested_budget_usd)))
        if authority is not None:
            await authority()
        reservation = self.budgets.reserve(
            task_id=ctx.task_id, run_id=run_id, amount_usd=amount,
            owner_id=ctx.user_id, project_id=ctx.project_id,
            max_steps=scope.budget.max_steps, max_tool_calls=scope.budget.max_tool_calls,
            max_wall_time_sec=scope.budget.max_wall_time_sec, depth=ctx.depth + 1,
        )
        started = False
        result: SubagentResult | None = None
        terminal_status, error_code = "FAILED", None
        try:
            # Reservation can narrow to task remaining. Tell the child the real
            # cap and revalidate before storing the effective security snapshot.
            scope = replace(scope, budget=Budget.model_validate({
                **scope.budget.model_dump(), "max_llm_cost_usd": float(reservation.amount_usd)}))
            names = tuple(tool.name for tool in available)
            environment = None
            if self.sessions is not None:
                try:
                    environment = await asyncio.to_thread(turn_environment, self.sessions, task_id=ctx.task_id,
                                                          user_id=ctx.user_id, tools=names)
                except Exception:
                    log.warning("turn environment unavailable: task_id=%s", ctx.task_id, exc_info=True)
            messages = compile_chat(resolved.definition, scope, context, names,
                                    conversation or [{"role": "user", "content": request.prompt}], environment)
            self.runs.record_start(
                run_id=run_id, task_id=ctx.task_id, owner_id=ctx.user_id, project_id=ctx.project_id,
                definition_version_id=resolved.version_id, definition_sha256=resolved.sha256,
                definition_snapshot=resolved.definition.model_dump(mode="json"),
                brief_sha256=hashlib.sha256(json.dumps(messages, sort_keys=True,
                                                       ensure_ascii=False, allow_nan=False).encode()).hexdigest(),
                effective_permissions={**scope.permissions.model_dump(mode="json"),
                                       "allowed_tools": list(scope.allowed_tools),
                                       "available_tools": [tool.name for tool in available]},
                effective_budget=scope.budget.model_dump(mode="json"), reservation_id=reservation.id,
            )
            started = True

            async def reauthorize() -> None:
                if authority is not None:
                    await authority()
                await self.authorizer.check_context(ctx)
                current = await self.registry.resolve_for_task(request.type, request.version, ctx)
                if current.version_id != resolved.version_id or current.sha256 != resolved.sha256:
                    raise ScopeDenied("the selected immutable definition is no longer authorized")

            gateway = ToolGateway(catalog=self.catalog, calls=self.calls, budgets=self.budgets,
                resources=self.resources, reauthorize=reauthorize, ctx=ctx, scope=scope,
                run_id=run_id, reservation_id=reservation.id,
                strict_tools=bool(getattr(model, "strict_tools", False)), lifecycle=self.lifecycle,
                approvals=self.approvals)
            result = await self.runtime.run(
                definition=resolved.definition, ctx=ctx, scope=scope, model=model, gateway=gateway,
                budgets=self.budgets, reservation=reservation, messages=messages,
                resources=self.resources, reauthorize=reauthorize, config=config,
                **({"stream": stream} if stream is not None else {}),
            )
            terminal_status = "COMPLETED"
        except asyncio.CancelledError:
            terminal_status, error_code = "CANCELLED", "USER_CANCELLED"
            result = SubagentResult(status="FAIL", error_code=error_code, summary="Stopped.")
            # Sync short transactions below execute even though CancelledError
            # is a BaseException. Preserve cancellation to the parent afterwards.
            raise
        except (ScopeDenied, BudgetExceeded, TimeoutError, ModelContextLimit,
                ModelProviderError, ModelConfigurationError, UnknownToolOutcome) as exc:
            error_code, text = _failure_text(exc)
            terminal_status = "FAILED"
            result = SubagentResult(status="FAIL", error_code=error_code, summary=text[:MAX_ANSWER_CHARS] or GENERIC_STOPPED_SUMMARY)
        except Exception:
            # The person is told only that the run failed; the cause belongs in the server log (no model or user content).
            log.warning("agent run failed: run_id=%s task_id=%s type=%s", run_id, ctx.task_id, request.type, exc_info=True)
            error_code, terminal_status = "CHILD_RUNTIME_ERROR", "FAILED"
            result = SubagentResult(status="FAIL", error_code=error_code, summary=GENERIC_STOPPED_SUMMARY)
        finally:
            # No network/await occurs inside these repository transactions. A DB
            # failure propagates instead of returning a falsely persisted success.
            try:
                if started and result is not None:
                    self.runs.record_finish(run_id=run_id, status=terminal_status,
                        result_json=result.model_dump(mode="json"), error_code=error_code)
            finally:
                self.budgets.settle(reservation.id)
        if result is None:
            raise RuntimeError("child execution produced no handoff")
        return result
