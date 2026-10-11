"""Validate and reauthorize every call, commit intent, then execute a bounded adapter.

Beyond authorization the gateway is what makes calls precise: it binds identifiers the task already
fixes (repository, ref, sandbox), tells the model exactly which field of a call was wrong, and lets
independent read-only calls run side by side while effectful ones stay strictly serial. It does not
count, cap or deduplicate calls: how many a run makes is the model's decision.
"""

import asyncio
import hashlib
import json
import time
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

from .catalog import (
    ASKS_BEFORE, BINDABLE_FIELDS, EFFECT_TOOLS, ToolCatalog, ToolUnavailable, activity_detail, approval_summary, safe_arguments,
)
from .contracts import ARGUMENT_MODELS, ToolOutcome
from .errors import ToolError, render_issues, validation_issues
from .lifecycle import CallLifecycle, LifecycleSink, execution_labels, outcome_from_row

READ_CONCURRENCY = 4


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
                 scope: EffectiveScope, run_id: UUID, reservation_id: UUID,
                 strict_tools: bool = False, lifecycle: LifecycleSink | None = None, approvals=None) -> None:
        self.catalog, self.calls, self.budgets, self.resources = catalog, calls, budgets, resources
        # Asks the person before a tool that changes something runs (see tools/approvals.py); None: nothing asks.
        self.approvals = approvals
        # Receives tool.started / tool.finished (see tools/lifecycle.py); without one the gateway reports nothing.
        self.lifecycle = lifecycle
        self.reauthorize, self.ctx, self.scope = reauthorize, ctx, scope
        self.run_id, self.reservation_id = run_id, reservation_id
        self.strict_tools = strict_tools
        self._attempts = 0
        # Effectful calls are serialized; independent reads may overlap up to READ_CONCURRENCY.
        self._lock = asyncio.Lock()
        self._reads = asyncio.Semaphore(READ_CONCURRENCY)

    # --- what the model sees --------------------------------------------------------------
    def bindings(self, name: str) -> dict[str, str]:
        """Identifiers the task fixes for this tool; the model neither supplies nor sees them."""
        model = ARGUMENT_MODELS.get(name)
        if model is None:
            return {}
        values: dict[str, str] = {}
        resolver = getattr(self.resources, "bindings", None)
        if resolver is not None:
            try:
                values.update(resolver(self.ctx, name))
            except ScopeDenied:
                values = {}
        if "task_id" in model.model_fields:
            values["task_id"] = str(self.ctx.task_id)
        return {key: value for key, value in values.items()
                if key in model.model_fields and key in BINDABLE_FIELDS and isinstance(value, str) and value}

    def schemas(self) -> list[dict]:
        return [tool.provider_schema(hide=frozenset(self.bindings(tool.name)), strict=self.strict_tools)
                for tool in self.catalog.available(self.scope.allowed_tools)]

    def _is_paid(self, name: str) -> bool:
        try:
            return self.catalog.get(name).max_cost_usd > 0
        except ToolUnavailable:
            return False

    def is_parallel_safe(self, name: str) -> bool:
        """Read-only and free, so it may run alongside other reads."""
        return name in self.scope.allowed_tools and name not in EFFECT_TOOLS and not self._is_paid(name)

    # --- argument handling ------------------------------------------------------------------
    def _parse(self, name: str, tool, raw_args: str):
        try:
            data = json.loads(raw_args)
        except ValueError:
            raise ToolError("SCHEMA_INVALID", "The arguments are not valid JSON. Send one JSON object.") from None
        if not isinstance(data, dict):
            raise ToolError("SCHEMA_INVALID", "The arguments must be one JSON object.")
        fields = tool.input_model.model_fields
        for key in [key for key, value in data.items() if value is None and key in fields and not fields[key].is_required()]:
            del data[key]  # a strict-schema model sends null for an omitted optional field
        for key, value in self.bindings(name).items():
            if key in data and data[key] != value:
                raise ToolError("SCOPE_DENIED", f"{key} is fixed for this task and not yours to choose; omit it.")
            data[key] = value
        try:
            return tool.input_model.model_validate_json(json.dumps(data))
        except ValidationError as exc:
            issues = validation_issues(exc)
            raise ToolError("SCHEMA_INVALID", render_issues(issues), issues=issues) from None

    # --- execution --------------------------------------------------------------------------
    async def execute(self, name: str, raw_args: str, provider_call_id: str) -> ToolOutcome:
        if name in EFFECT_TOOLS or self._is_paid(name):
            async with self._lock:
                return await self._run(name, raw_args, provider_call_id)
        async with self._reads:
            return await self._run(name, raw_args, provider_call_id)

    async def _run(self, name: str, raw_args: str, provider_call_id: str) -> ToolOutcome:
        started = time.monotonic()
        self._attempts += 1
        if not provider_call_id or len(provider_call_id) > 100:
            raise ValueError("invalid_provider_call_id")
        if len(raw_args.encode("utf-8")) > 131072:
            raise ValueError("tool_arguments_too_large")
        attempt = self._attempts
        operation_key = f"{self.run_id}:tool:{provider_call_id}"
        # Private argument content is represented by an immutable digest;
        # no source code, .env data or prompt fragments enter audit metadata.
        digest = hashlib.sha256(raw_args.encode("utf-8")).hexdigest()
        # A name the run may not call is stored (and shown) as "unknown": the model's text never becomes a tool name.
        stored_name = name if name in self.scope.allowed_tools else "unknown"
        call_id = self.calls.record_intent(
            task_id=self.ctx.task_id, run_id=self.run_id, tool_name=stored_name,
            args_digest=digest, intent_json_sanitized={"arguments_sha256": digest},
            operation_key=operation_key,
        )
        events = CallLifecycle(self.lifecycle, tool=stored_name, tool_call_id=call_id,
                               agent_run_id=self.run_id, operation_key=operation_key)
        authorized = False
        entered_adapter = False
        tool = None
        arguments: dict = {}
        try:
            await self.reauthorize()
            self.budgets.check_active(self.reservation_id)
            tool = self.catalog.get(name)
            if name not in self.scope.allowed_tools:
                raise ScopeDenied("tool is not authorized for this run")
            args = self._parse(name, tool, raw_args)
            arguments = safe_arguments(args)
            events.detail = activity_detail(name, args)
            web_url, web_hosts = None, ()
            if name in ("fetch_webpage", "browser_public_page"):
                resolver = getattr(self.resources, "resolve_web_url", None)
                if resolver is None:
                    raise ScopeDenied("trusted network resource resolver is unavailable")
                async with asyncio.timeout(min(15, tool.timeout_sec)):
                    web_url, web_hosts = await resolver(self.ctx, name, args, self.scope)
            authorize_tool(name, args, self.ctx, self.scope, resolved_web_url=web_url)
            await self.resources.verify_tool_inputs(self.ctx, name, args)
            if self.approvals is not None and name in ASKS_BEFORE:
                if not await self.approvals.allows(self.ctx, operation_key=operation_key, tool=name,
                        summary=approval_summary(name, args), detail=arguments, check=self.reauthorize):
                    raise ToolError("DECLINED", "The person declined this action, so nothing was run. Do not do it another way "
                                    "without asking: say what you were about to do and ask how they want to continue.")
            # Announced just before the budget is claimed so a failed event write costs nothing; a refusal
            # from here on shows as a blocked step.
            events.start()
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
            saver = getattr(self.calls, "store_result", None)
            if saver is not None and name != "read_context_resource":
                reference = saver(self.ctx.task_id, operation_key, result)
                result = result.model_copy(update={"data": {**result.data, "resource_id": reference}})
            self.budgets.record_usage(self.reservation_id, operation_key, result.cost_usd)
            authorized = False  # usage was durably recorded
            if result.cost_usd is not None and result.cost_usd > tool.max_cost_usd:
                raise UnknownToolOutcome("adapter_cost_bound_exceeded")
            await self.resources.verify_references(self.ctx, evidence_ids=result.evidence_ids,
                                                   artifact_ids=result.artifact_ids)
            if len(json.dumps(result.data, ensure_ascii=False).encode()) > 16000 and not (result.evidence_ids or result.artifact_ids):
                raise ValueError("large_adapter_output_requires_durable_reference")
            duration_ms = int((time.monotonic() - started) * 1000)
            labels = execution_labels(name, result.data)
            status = "SUCCEEDED" if result.status == "ok" else "FAILED"
            self.calls.record_outcome(
                call_id=call_id, status=status,
                result_json={"status": result.status,
                             "data_sha256": hashlib.sha256(json.dumps(result.data, sort_keys=True).encode()).hexdigest(),
                             "artifact_ids": list(result.artifact_ids), "attempt": attempt,
                             "arguments": arguments, "duration_ms": duration_ms,
                             # Exit code, timeout and qualification only (ints and bools), never output text.
                             **({"execution": labels} if labels else {})},
                error_code=result.error_code, evidence_ids=result.evidence_ids,
            )
            events.finish(outcome_from_row(status, result.error_code), evidence_ids=result.evidence_ids,
                          artifact_ids=result.artifact_ids, error_code=result.error_code,
                          duration_ms=duration_ms, labels=labels)
            return result
        except BaseException as exc:
            definite = isinstance(exc, ToolError)
            if authorized:
                # Cancellation/timeouts cannot be treated as free remote work; a typed adapter
                # rejection happened before any effect and is free.
                self.budgets.record_usage(self.reservation_id, operation_key, Decimal(0) if definite else None)
            cancelled = isinstance(exc, asyncio.CancelledError)
            unknown = entered_adapter and (tool is not None and tool.has_effects) and not definite
            detail = None
            issues = None
            if isinstance(exc, ToolError):
                code, detail, issues = exc.code, exc.detail, exc.issues
            elif isinstance(exc, ScopeDenied):
                code = "SCOPE_DENIED"
            elif isinstance(exc, ToolUnavailable):
                code = "TOOL_UNAVAILABLE"
            elif isinstance(exc, (ValidationError, ValueError)) and not isinstance(exc, BudgetExceeded):
                # Bad arguments are the model's to correct; an adapter that rejects its own result is a failure.
                code = "TOOL_FAILED" if entered_adapter else "SCHEMA_INVALID"
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
            duration_ms = int((time.monotonic() - started) * 1000)
            status = "UNKNOWN" if unknown else ("BLOCKED" if not entered_adapter else "FAILED")
            self.calls.record_outcome(call_id=call_id, status=status, error_code=code,
                result_json={"attempt": attempt, "arguments": arguments, "issues": issues or [],
                             "duration_ms": duration_ms})
            # Reported only once the row is durable, including when the run is being cancelled (best effort).
            events.finish(outcome_from_row(status, code), error_code=code, duration_ms=duration_ms)
            if not isinstance(exc, Exception) or isinstance(exc, (BudgetExceeded, UnknownToolOutcome)):
                raise
            if unknown:
                raise UnknownToolOutcome(code) from None
            # No exception string or provider content crosses the boundary: detail is text our own
            # code wrote for the model, and validation issues describe constraints, never values.
            return ToolOutcome(
                status="error", data={"issues": issues} if issues else {},
                cost_usd=Decimal(0) if (not entered_adapter or definite) else None,
                error_code=code, error_detail=detail)
