"""Validate and reauthorize every call, commit intent, then execute a bounded adapter.

Beyond authorization the gateway is what makes calls precise: it binds identifiers the task already
fixes (repository, ref, sandbox), tells the model exactly which field of a call was wrong, refuses a
repeat of a call that already succeeded, tracks consecutive failures so the runtime can stop a loop,
and lets independent read-only calls run side by side while effectful ones stay strictly serial.
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

from .catalog import BINDABLE_FIELDS, EFFECT_TOOLS, ToolCatalog, ToolUnavailable, safe_arguments
from .contracts import ARGUMENT_MODELS, ToolOutcome
from .errors import ToolError, render_issues, validation_issues

READ_CONCURRENCY = 4
MAX_CONSECUTIVE_ERRORS = 5
REPEATED_FAILURE_NOTICE = 3
# Authorization refusals that mean "this run cannot afford the call" rather than "the run is over".
SOFT_BUDGET_CODES = frozenset({"child_budget_exceeded", "tool_limit_exceeded"})


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
                 strict_tools: bool = False) -> None:
        self.catalog, self.calls, self.budgets, self.resources = catalog, calls, budgets, resources
        self.reauthorize, self.ctx, self.scope = reauthorize, ctx, scope
        self.run_id, self.reservation_id = run_id, reservation_id
        self.strict_tools = strict_tools
        self._attempts = 0
        # Effectful calls are serialized; independent reads may overlap up to READ_CONCURRENCY.
        self._lock = asyncio.Lock()
        self._reads = asyncio.Semaphore(READ_CONCURRENCY)
        # A successful read is remembered until something could have changed what it returns.
        self._epoch = 0
        self._seen: dict[tuple[str, str, int], int] = {}
        self._by_call: dict[str, tuple[str, str, int]] = {}
        self.consecutive_errors = 0
        self.failures: dict[str, int] = {}
        # Set when a tool call was refused for lack of budget; the runtime then wraps the run up.
        self.budget_exhausted = False

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
        """Read-only and free. A paid call holds its full cost cap against the run's budget while it is in
        flight, so several at once can overdraw a budget that the same calls made one by one would not."""
        return name in self.scope.allowed_tools and name not in EFFECT_TOOLS and not self._is_paid(name)

    @property
    def max_tool_cost(self) -> Decimal:
        """The largest cost cap among the tools this run may call."""
        return max((tool.max_cost_usd for tool in self.catalog.available(self.scope.allowed_tools)), default=Decimal(0))

    @property
    def calls_left(self) -> int:
        return max(0, self.scope.budget.max_tool_calls - self._attempts)

    def forget(self, provider_call_ids) -> None:
        """The conversation no longer holds these calls' results (they were compacted), so the same call
        may be made again to fetch them back."""
        for call_id in provider_call_ids:
            key = self._by_call.pop(call_id, None)
            if key is not None:
                self._seen.pop(key, None)

    def repeated_failure_notice(self, name: str) -> str | None:
        count = self.failures.get(name, 0)
        if count >= REPEATED_FAILURE_NOTICE:
            return (f"{name} has failed {count} times in a row. Stop retrying it: change approach, "
                    "or finish with what you have and record the gap.")
        return None

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
                try:
                    return await self._run(name, raw_args, provider_call_id)
                finally:
                    if name in EFFECT_TOOLS:
                        self._epoch += 1  # whatever a write or run touched, earlier reads are stale
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
        call_id = self.calls.record_intent(
            task_id=self.ctx.task_id, run_id=self.run_id,
            tool_name=name if name in self.scope.allowed_tools else "unknown",
            args_digest=digest, intent_json_sanitized={"arguments_sha256": digest},
            operation_key=operation_key,
        )
        if attempt > self.scope.budget.max_tool_calls:
            outcome = ToolOutcome(status="error", cost_usd=Decimal(0), error_code="TOOL_LIMIT_REACHED",
                error_detail="No tool calls are left for this run. Call submit_result now with the best honest result.")
            self.calls.record_outcome(call_id=call_id, status="BLOCKED", error_code="TOOL_LIMIT_REACHED",
                result_json={"attempt": attempt})
            return self._note(name, outcome)
        authorized = False
        entered_adapter = False
        tool = None
        reserved_key: tuple[str, str, int] | None = None
        arguments: dict = {}
        try:
            await self.reauthorize()
            self.budgets.check_active(self.reservation_id)
            tool = self.catalog.get(name)
            if name not in self.scope.allowed_tools:
                raise ScopeDenied("tool is not authorized for this run")
            args = self._parse(name, tool, raw_args)
            arguments = safe_arguments(args)
            web_url, web_hosts = None, ()
            if name in ("fetch_webpage", "browser_public_page"):
                resolver = getattr(self.resources, "resolve_web_url", None)
                if resolver is None:
                    raise ScopeDenied("trusted network resource resolver is unavailable")
                async with asyncio.timeout(min(15, tool.timeout_sec)):
                    web_url, web_hosts = await resolver(self.ctx, name, args, self.scope)
            authorize_tool(name, args, self.ctx, self.scope, resolved_web_url=web_url)
            await self.resources.verify_tool_inputs(self.ctx, name, args)
            if not tool.has_effects:
                key = (name, args.model_dump_json(), self._epoch)
                if key in self._seen:
                    raise ToolError("DUPLICATE_CALL", f"This is identical to your call number {self._seen[key]}, "
                                    "which succeeded. Reuse its result or change the arguments.")
                self._seen[key] = attempt
                self._by_call[provider_call_id] = reserved_key = key
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
            if result.status != "ok" and reserved_key is not None:
                self._seen.pop(reserved_key, None)  # a failed read may be retried
            self.calls.record_outcome(
                call_id=call_id, status="SUCCEEDED" if result.status == "ok" else "FAILED",
                result_json={"status": result.status,
                             "data_sha256": hashlib.sha256(json.dumps(result.data, sort_keys=True).encode()).hexdigest(),
                             "artifact_ids": list(result.artifact_ids), "attempt": attempt,
                             "arguments": arguments, "duration_ms": int((time.monotonic() - started) * 1000)},
                error_code=result.error_code, evidence_ids=result.evidence_ids,
            )
            return self._note(name, result)
        except BaseException as exc:
            definite = isinstance(exc, ToolError)
            if reserved_key is not None:
                self._seen.pop(reserved_key, None)
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
                error_code=code,
                result_json={"attempt": attempt, "arguments": arguments, "issues": issues or [],
                             "duration_ms": int((time.monotonic() - started) * 1000)})
            soft_budget = isinstance(exc, BudgetExceeded) and str(exc) in SOFT_BUDGET_CODES and not entered_adapter
            if soft_budget:
                self.budget_exhausted = True
                detail = "This run's remaining budget cannot cover the call. Submit what you have."
            elif not isinstance(exc, Exception) or isinstance(exc, (BudgetExceeded, UnknownToolOutcome)):
                raise
            if unknown:
                raise UnknownToolOutcome(code) from None
            # No exception string or provider content crosses the boundary: detail is text our own
            # code wrote for the model, and validation issues describe constraints, never values.
            return self._note(name, ToolOutcome(
                status="error", data={"issues": issues} if issues else {},
                cost_usd=Decimal(0) if (not entered_adapter or definite) else None,
                error_code=code, error_detail=detail))

    def _note(self, name: str, outcome: ToolOutcome) -> ToolOutcome:
        if outcome.status == "ok":
            self.consecutive_errors = 0
            self.failures[name] = 0
        elif outcome.error_code != "DUPLICATE_CALL":
            self.consecutive_errors += 1
            self.failures[name] = self.failures.get(name, 0) + 1
        return outcome
