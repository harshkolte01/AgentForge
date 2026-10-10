"""Internal composition seam for validated, finite child invocations.

This service is intentionally not an unauthenticated HTTP endpoint. TaskContext
must be assembled by a trusted parent from server-owned grants and current user.
"""

import asyncio
import hashlib
import json
from dataclasses import replace
from collections.abc import Awaitable, Callable
from decimal import Decimal
from uuid import UUID, uuid4

from langchain_core.runnables import RunnableConfig

from agentforge.contracts.subagent import SpawnRequest, SubagentResult
from agentforge.contracts.agent_definition import Budget
from agentforge.llm.models import ModelConfigurationError, ModelContextLimit, ModelProviderError
from agentforge.llm.router import ModelRouter
from agentforge.repositories.runs import AgentRunRepository, ToolCallRepository
from agentforge.security.policies import AuthorizationService
from agentforge.tools.catalog import ToolCatalog
from agentforge.tools.gateway import ToolGateway, UnknownToolOutcome

from .budget import BudgetExceeded, BudgetService
from .prompt_builder import compile_prompt
from .registry import AgentRegistry
from .resources import DenyUnknownResources, ResourceResolver
from .runtime import ChildRuntime
from .scope import ScopeDenied, TaskContext, intersect_and_validate


class SpawnService:
    def __init__(self, *, registry: AgentRegistry, authorizer: AuthorizationService,
                 budgets: BudgetService, runs: AgentRunRepository,
                 calls: ToolCallRepository, catalog: ToolCatalog, models: ModelRouter,
                 resources: ResourceResolver | None = None,
                 runtime: ChildRuntime | None = None) -> None:
        self.registry, self.authorizer, self.budgets = registry, authorizer, budgets
        self.runs, self.calls, self.catalog, self.models = runs, calls, catalog, models
        self.resources = resources if resources is not None else DenyUnknownResources()
        self.runtime = runtime if runtime is not None else ChildRuntime()

    async def spawn_subagent(self, request: SpawnRequest, ctx: TaskContext, *,
                             config: RunnableConfig | None = None,
                             run_id: UUID | None = None,
                             authority: Callable[[], Awaitable[None]] | None = None,
                             stream=None) -> SubagentResult:
        # Reject model_construct/model_copy bypasses at this trust boundary too.
        request = SpawnRequest.model_validate_json(request.model_dump_json())
        if authority is not None:
            await authority()
        await self.authorizer.check_context(ctx)
        resolved = await self.registry.resolve_for_task(request.type, request.version, ctx)
        scope = intersect_and_validate(resolved.definition, ctx, request.context_refs)
        available = self.catalog.available(scope.allowed_tools)
        if not available:
            return SubagentResult(status="BLOCKED", summary="No authorized capability adapters are installed for this child.")
        async with asyncio.timeout(min(30, scope.budget.max_wall_time_sec)):
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
            messages = compile_prompt(resolved.definition, request, ctx, scope, context,
                                      tuple(tool.name for tool in available))
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
                run_id=run_id, reservation_id=reservation.id)
            result = await self.runtime.run(
                definition=resolved.definition, ctx=ctx, scope=scope, model=model, gateway=gateway,
                budgets=self.budgets, reservation=reservation, messages=messages,
                resources=self.resources, reauthorize=reauthorize, config=config,
                **({"stream": stream} if stream is not None else {}),
            )
            terminal_status = {"PASS": "COMPLETED", "FAIL": "FAILED", "PARTIAL": "PARTIAL", "BLOCKED": "BLOCKED"}[result.status]
        except asyncio.CancelledError:
            terminal_status, error_code = "CANCELLED", "USER_CANCELLED"
            result = SubagentResult(status="BLOCKED", summary="Child invocation was cancelled.")
            # Sync short transactions below execute even though CancelledError
            # is a BaseException. Preserve cancellation to the parent afterwards.
            raise
        except (ScopeDenied, BudgetExceeded, TimeoutError, ModelContextLimit,
                ModelProviderError, ModelConfigurationError, UnknownToolOutcome) as exc:
            if isinstance(exc, ScopeDenied):
                error_code = "SCOPE_DENIED"
            elif isinstance(exc, BudgetExceeded):
                error_code = "BUDGET_EXCEEDED"
            elif isinstance(exc, TimeoutError):
                error_code = "TOOL_TIMEOUT"
            elif isinstance(exc, ModelContextLimit):
                error_code = "MODEL_CONTEXT_LIMIT"
            elif isinstance(exc, UnknownToolOutcome):
                error_code = "UNKNOWN_REMOTE_OUTCOME"
            else:
                error_code = "MODEL_PROVIDER_ERROR"
            terminal_status = "BLOCKED"
            result = SubagentResult(status="BLOCKED", summary=f"Child invocation stopped: {error_code}.")
        except Exception:
            error_code, terminal_status = "CHILD_RUNTIME_ERROR", "FAILED"
            result = SubagentResult(status="FAIL", summary="Child invocation failed before a verified handoff.")
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
        return SubagentResult.model_validate_json(json.dumps(result.model_dump(
            mode="json", include=set(SubagentResult.model_fields))))
