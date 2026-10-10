"""Validate and reauthorize every call, commit intent, then execute a bounded adapter."""

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from pydantic import ValidationError

from agentforge.repositories.runs import ToolCallRepository
from agentforge.security.policies import PublicURL, authorize_tool
from agentforge.subagents.budget import BudgetExceeded, BudgetService
from agentforge.subagents.resources import ResourceResolver
from agentforge.subagents.scope import EffectiveScope, ScopeDenied, TaskContext

from .catalog import ToolCatalog, ToolUnavailable
from .contracts import ToolOutcome


class UnknownToolOutcome(RuntimeError):
    """An effect may have occurred; stop rather than automatically repeating it."""


@dataclass(frozen=True)
class ToolExecutionContext:
    task: TaskContext
    agent_run_id: UUID
    operation_key: str
    # The adapter can use this key for external idempotency/reconciliation.
    scope: EffectiveScope
    authorized_web_url: PublicURL | None = None
    authorized_web_hosts: tuple[str, ...] = ()
    authorize_current: Callable[[], Awaitable[None]] | None = None


class ToolGateway:
    def __init__(self, *, catalog: ToolCatalog, calls: ToolCallRepository,
                 budgets: BudgetService, resources: ResourceResolver,
                 reauthorize: Callable[[], Awaitable[None]], ctx: TaskContext,
                 scope: EffectiveScope, run_id: UUID, reservation_id: UUID) -> None:
        self.catalog, self.calls, self.budgets, self.resources = catalog, calls, budgets, resources
        self.reauthorize, self.ctx, self.scope = reauthorize, ctx, scope
        self.run_id, self.reservation_id = run_id, reservation_id
        self._attempts = 0
        self._lock = asyncio.Lock()

    def schemas(self) -> list[dict]:
        return [tool.provider_schema() for tool in self.catalog.available(self.scope.allowed_tools)]

    async def execute(self, name: str, raw_args: str, provider_call_id: str) -> ToolOutcome:
        # Serializes mutations and limits within a child even if a provider
        # unexpectedly returns several calls despite parallel_tool_calls=False.
        async with self._lock:
            self._attempts += 1
            if self._attempts > self.scope.budget.max_tool_calls:
                raise BudgetExceeded("tool_limit_exceeded")
            if not provider_call_id or len(provider_call_id) > 100:
                raise ValueError("invalid_provider_call_id")
            if len(raw_args.encode("utf-8")) > 131072:
                raise ValueError("tool_arguments_too_large")
            operation_key = f"{self.run_id}:tool:{provider_call_id}"
            # Private argument content is represented by an immutable digest;
            # no source code, .env data or prompt fragments enter audit metadata.
            digest = hashlib.sha256(raw_args.encode("utf-8")).hexdigest()
            call_id = self.calls.record_intent(
                task_id=self.ctx.task_id, run_id=self.run_id,
                tool_name=name if name in self.scope.allowed_tools else "unknown",
                args_digest=digest, intent_json_sanitized={"arguments_sha256": digest},
                operation_key=operation_key,
            )
            authorized = False
            entered_adapter = False
            tool = None
            try:
                await self.reauthorize()
                self.budgets.check_active(self.reservation_id)
                tool = self.catalog.get(name)
                args = tool.input_model.model_validate_json(raw_args)
                if name not in self.scope.allowed_tools:
                    raise ScopeDenied("tool is not authorized for this run")
                web_url, web_hosts = None, ()
                if name in ("fetch_webpage", "browser_public_page"):
                    resolver = getattr(self.resources, "resolve_web_url", None)
                    if resolver is None:
                        raise ScopeDenied("trusted network resource resolver is unavailable")
                    async with asyncio.timeout(min(15, tool.timeout_sec)):
                        web_url, web_hosts = await resolver(self.ctx, name, args, self.scope)
                authorize_tool(name, args, self.ctx, self.scope, resolved_web_url=web_url)
                await self.resources.verify_tool_inputs(self.ctx, name, args)
                self.budgets.authorize_operation(self.reservation_id, operation_key,
                                                tool.max_cost_usd, kind="tool")
                authorized = True
                entered_adapter = True
                async with asyncio.timeout(tool.timeout_sec):
                    raw_result = await tool.handler(args, ToolExecutionContext(
                        self.ctx, self.run_id, operation_key, self.scope, web_url, web_hosts, self.reauthorize))
                # Revalidate model_copy/model_construct output from trusted
                # adapter code before handing any data or IDs to the child.
                if not isinstance(raw_result, ToolOutcome):
                    raise ValueError("invalid_adapter_result")
                result = ToolOutcome.model_validate_json(raw_result.model_dump_json())
                self.budgets.record_usage(self.reservation_id, operation_key, result.cost_usd)
                authorized = False  # usage was durably recorded
                if result.cost_usd is not None and result.cost_usd > tool.max_cost_usd:
                    raise UnknownToolOutcome("adapter_cost_bound_exceeded")
                await self.resources.verify_references(self.ctx, evidence_ids=result.evidence_ids,
                                                       artifact_ids=result.artifact_ids)
                if len(json.dumps(result.data, ensure_ascii=False).encode()) > 16000 and not (result.evidence_ids or result.artifact_ids):
                    raise ValueError("large_adapter_output_requires_durable_reference")
                self.calls.record_outcome(
                    call_id=call_id, status="SUCCEEDED" if result.status == "ok" else "FAILED",
                    result_json={"status": result.status,
                                 "data_sha256": hashlib.sha256(json.dumps(result.data, sort_keys=True).encode()).hexdigest(),
                                 "artifact_ids": list(result.artifact_ids)},
                    error_code=result.error_code, evidence_ids=result.evidence_ids,
                )
                return result
            except BaseException as exc:
                if authorized:
                    # Cancellation/timeouts cannot be treated as free remote work.
                    self.budgets.record_usage(self.reservation_id, operation_key, None)
                cancelled = isinstance(exc, asyncio.CancelledError)
                unknown = entered_adapter and (tool is not None and tool.has_effects)
                if isinstance(exc, ScopeDenied):
                    code = "SCOPE_DENIED"
                elif isinstance(exc, ToolUnavailable):
                    code = "TOOL_UNAVAILABLE"
                elif isinstance(exc, (ValidationError, ValueError)) and not isinstance(exc, BudgetExceeded):
                    code = "SCHEMA_INVALID"
                elif isinstance(exc, BudgetExceeded):
                    code = "BUDGET_EXCEEDED"
                elif isinstance(exc, TimeoutError):
                    code = "TOOL_TIMEOUT"
                elif cancelled:
                    code = "USER_CANCELLED"
                else:
                    code = "TOOL_FAILED"
                if unknown:
                    code = "UNKNOWN_REMOTE_OUTCOME"
                self.calls.record_outcome(call_id=call_id,
                    status="UNKNOWN" if unknown else ("BLOCKED" if not entered_adapter else "FAILED"),
                    error_code=code)
                if not isinstance(exc, Exception) or isinstance(exc, (BudgetExceeded, UnknownToolOutcome)):
                    raise
                if unknown:
                    raise UnknownToolOutcome(code) from None
                # No exception string or provider content crosses the boundary.
                return ToolOutcome(status="error", data={}, cost_usd=Decimal(0) if not entered_adapter else None,
                                   error_code=code)
