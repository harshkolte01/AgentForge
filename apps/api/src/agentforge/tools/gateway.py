"""Validate and reauthorize every call, commit intent, then execute a bounded adapter.

Beyond authorization the gateway is what makes calls precise: it binds identifiers the task already
fixes (repository, ref, sandbox), tells the model exactly which field of a call was wrong, and lets
independent read-only calls and delegations run side by side while other effectful ones stay strictly serial. It does not
count, cap or deduplicate calls: how many a run makes is the model's decision.
"""

import asyncio
import hashlib
import json
import re
import time
from collections.abc import Awaitable, Callable
from contextlib import nullcontext
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from pydantic import ValidationError

from agentforge.repositories.runs import ToolCallRepository
from agentforge.security.policies import PublicURL, authorize_tool
from agentforge.telemetry.neatlogs_setup import set_attributes
from agentforge.subagents.budget import BudgetExceeded, BudgetService
from agentforge.subagents.resources import ResourceResolver
from agentforge.subagents.scope import EffectiveScope, ScopeDenied, TaskContext

from .catalog import (
    BINDABLE_FIELDS, EFFECT_TOOLS, ToolCatalog, ToolUnavailable, activity_detail, approval_summary, safe_arguments,
)
from .contracts import ARGUMENT_MODELS, ToolOutcome
from .delegation import delegated_run_id
from .errors import ToolError, render_issues, validation_issues
from .lifecycle import CallLifecycle, LifecycleSink, execution_labels, outcome_from_row

READ_CONCURRENCY = 4
# The most a call's JSON arguments may hold; larger calls are refused before they run (see `_run`).
MAX_ARGUMENT_BYTES = 131072
# The tools whose output is shown while they run: a command can take minutes, the others finish at once.
STREAMED_OUTPUT = frozenset({"bash"})
# Effect tools that do not wait for the run's other effects. A delegation runs under its own gateway, ledger and approvals, so
# the assignments of one turn work side by side; `ChildRuntime` starts them together when a turn asks for several.
CONCURRENT_EFFECTS = frozenset({"delegate_task"})


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
    # Where a running tool may send its output as it arrives (a command's lines); None: nothing is shown before it ends.
    output: Callable[[str], None] | None = None


class ToolGateway:
    def __init__(self, *, catalog: ToolCatalog, calls: ToolCallRepository,
                 budgets: BudgetService, resources: ResourceResolver,
                 reauthorize: Callable[[], Awaitable[None]], ctx: TaskContext,
                 scope: EffectiveScope, run_id: UUID, reservation_id: UUID,
                 strict_tools: bool = False, lifecycle: LifecycleSink | None = None, approvals=None, live=None,
                 trace=None) -> None:
        self.catalog, self.calls, self.budgets, self.resources = catalog, calls, budgets, resources
        # Opens a tracing span (`Telemetry.span`): approvals and refused calls show up in the trace as guardrail checks.
        # None: nothing is traced here.
        self.trace = trace
        # Asks the person before a tool that changes something runs (see tools/approvals.py); None: nothing asks.
        self.approvals = approvals
        # Receives tool.started / tool.finished (see tools/lifecycle.py); without one the gateway reports nothing.
        self.lifecycle = lifecycle
        # The live view of the run (`ReplyStream`): shows a call being written until its row starts, and a command's
        # output while it runs. None: only the saved events show anything.
        self.live = live
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

    def _changes_things(self, name: str) -> bool:
        try:
            return self.catalog.get(name).has_effects
        except ToolUnavailable:
            return name in EFFECT_TOOLS

    def is_parallel_safe(self, name: str) -> bool:
        """Read-only and free, so it may run alongside other reads. A delegation may too: it only ever touches its own run."""
        return name in self.scope.allowed_tools and (name in CONCURRENT_EFFECTS or (not self._changes_things(name) and not self._is_paid(name)))

    # --- argument handling ------------------------------------------------------------------
    @staticmethod
    def _loads(raw_args: str):
        """The JSON object a model meant: empty arguments are `{}`, and a Markdown code fence around it is ignored.
        Anything else is not guessed at (two objects run together may be two calls); validation still applies."""
        text = raw_args.strip()
        if not text:
            return {}
        try:
            return json.loads(text)
        except ValueError:
            return json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", text))

    def _parse(self, name: str, tool, raw_args: str):
        try:
            data = self._loads(raw_args)
        except ValueError as exc:
            # Where the parser gave up, never the text around it: the arguments may hold file content.
            where = f" ({exc.msg} at character {exc.pos})" if isinstance(exc, json.JSONDecodeError) else ""
            raise ToolError("SCHEMA_INVALID", f"The arguments are not valid JSON{where}. Send one JSON object.") from None
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

    # --- tracing ----------------------------------------------------------------------------
    def _check(self, name: str):
        """A guardrail span, or a no-op without tracing."""
        return self.trace(name, kind="GUARDRAIL") if self.trace is not None else nullcontext(None)

    def _record_refusal(self, tool: str, code: str) -> None:
        """A call that was refused before it ran, as a failed guardrail check in the trace. Never raises."""
        if self.trace is None or code == "DECLINED":   # a declined call already has its approval check
            return
        try:
            with self._check("tool_policy") as check:
                set_attributes(check, {"neatlogs.guardrail.name": "tool_policy", "neatlogs.guardrail.passed": False,
                    "neatlogs.guardrail.reason": code, "tool.name": tool, "agentforge.error.code": code,
                    "task_id": str(self.ctx.task_id), "spawn_id": str(self.run_id)})
        except Exception:  # noqa: BLE001
            pass

    # --- execution --------------------------------------------------------------------------
    async def execute(self, name: str, raw_args: str, provider_call_id: str) -> ToolOutcome:
        if name in CONCURRENT_EFFECTS:
            return await self._run(name, raw_args, provider_call_id)
        if self._changes_things(name) or self._is_paid(name):
            async with self._lock:
                return await self._run(name, raw_args, provider_call_id)
        async with self._reads:
            return await self._run(name, raw_args, provider_call_id)

    async def _run(self, name: str, raw_args: str, provider_call_id: str) -> ToolOutcome:
        started = time.monotonic()
        self._attempts += 1
        if not provider_call_id or len(provider_call_id) > 100:
            raise ValueError("invalid_provider_call_id")
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
        if name == "delegate_task":
            # Names the child so the chat can put its progress on this call's card (the child run id derives from the call).
            events.extra = {"child_run_id": str(delegated_run_id(operation_key))}
        authorized = False
        entered_adapter = False
        tool = None
        arguments: dict = {}
        live_call = events.ids["call_id"] if self.live is not None else None
        try:
            await self.reauthorize()
            self.budgets.check_active(self.reservation_id)
            tool = self.catalog.get(name)
            if name not in self.scope.allowed_tools:
                raise ScopeDenied("tool is not authorized for this run")
            if len(raw_args.encode("utf-8")) > MAX_ARGUMENT_BYTES:
                # Refused like any other bad argument, before anything runs, so the model learns to send less at once.
                raise ToolError("INVALID_ARGUMENT", f"The arguments are larger than {MAX_ARGUMENT_BYTES // 1024} KB, so the call was "
                                "not run. Split the work into parts: write the first part of a large file, then add the rest "
                                "with further edits, or use several smaller files.")
            args = self._parse(name, tool, raw_args)
            arguments = safe_arguments(args)
            plugin = tool.plugin
            events.detail = activity_detail(name, args) if plugin is None else (plugin.title or plugin.remote_name)[:120]
            web_url, web_hosts = None, ()
            if name in ("fetch_webpage", "browser_public_page"):
                resolver = getattr(self.resources, "resolve_web_url", None)
                if resolver is None:
                    raise ScopeDenied("trusted network resource resolver is unavailable")
                async with asyncio.timeout(min(15, tool.timeout_sec)):
                    web_url, web_hosts = await resolver(self.ctx, name, args, self.scope)
            # A plugin's arguments are its server's business: the built-in tools' path, repository and URL rules do not
            # apply to them (a plugin tool may legitimately take a "path" or "url"), and it is reached only through the
            # allowlist above, never through the sandbox or the repository.
            if plugin is None:
                authorize_tool(name, args, self.ctx, self.scope, resolved_web_url=web_url)
            await self.resources.verify_tool_inputs(self.ctx, name, args)
            if self.approvals is not None and tool.asks_before:
                extra = ""
                asks = getattr(self.approvals, "asks", None)
                if tool.preview is not None and (asks is None or await asks(self.ctx)):
                    try:
                        async with asyncio.timeout(60):
                            extra = await tool.preview(args, ToolExecutionContext(
                                self.ctx, self.run_id, operation_key, self.scope, web_url, web_hosts, self.reauthorize))
                    except ToolError:
                        raise
                    except Exception:
                        extra = "(The list of changed files could not be read.)"
                summary, truncated = approval_summary(name, args, extra, plugin)
                # A subagent's card says whose it is: several can be waiting at once, with commands that look alike.
                whose = {"agent_run_id": str(self.run_id)} if getattr(self.ctx, "parent_run_id", None) is not None else {}
                with self._check("tool_approval") as check:
                    allowed = await self.approvals.allows(self.ctx, operation_key=operation_key, tool=name, summary=summary,
                        truncated=truncated, detail=arguments, check=self.reauthorize, **whose)
                    set_attributes(check, {"neatlogs.guardrail.name": "tool_approval", "neatlogs.guardrail.passed": bool(allowed),
                        "neatlogs.guardrail.reason": "APPROVED" if allowed else "DECLINED", "tool.name": name,
                        "task_id": str(self.ctx.task_id), "spawn_id": str(self.run_id)})
                if not allowed:
                    raise ToolError("DECLINED", "The person declined this action, so nothing was run. Do not do it another way "
                                    "without asking: say what you were about to do and ask how they want to continue.")
                # A call's duration is how long it ran, not how long it waited for the person.
                started = time.monotonic()
            # Announced just before the budget is claimed so a failed event write costs nothing; a refusal
            # from here on shows as a blocked step.
            events.start()
            self.budgets.authorize_operation(self.reservation_id, operation_key,
                                            tool.max_cost_usd, kind="tool")
            authorized = True
            entered_adapter = True
            output = None
            if live_call is not None:
                # The call's own row now stands in for it being written. A command's output follows live, under that row.
                if name in STREAMED_OUTPUT:
                    output = self.live.output(live_call, name)
                else:
                    self.live.settle(live_call)
            async with asyncio.timeout(tool.timeout_sec):
                raw_result = await tool.handler(args, ToolExecutionContext(
                    self.ctx, self.run_id, operation_key, self.scope, web_url, web_hosts, self.reauthorize, output))
            # Revalidate model_copy/model_construct output from trusted
            # adapter code before handing any data or IDs to the child.
            if not isinstance(raw_result, ToolOutcome):
                raise ValueError("invalid_adapter_result")
            result = ToolOutcome.model_validate_json(raw_result.model_dump_json())
            stored_result = False
            saver = getattr(self.calls, "store_result", None)
            if saver is not None and name != "read_context_resource":
                reference = saver(self.ctx.task_id, operation_key, result)
                stored_result = True
                result = result.model_copy(update={"data": {**result.data, "resource_id": reference}})
            self.budgets.record_usage(self.reservation_id, operation_key, result.cost_usd)
            authorized = False  # usage was durably recorded
            if result.cost_usd is not None and result.cost_usd > tool.max_cost_usd:
                raise UnknownToolOutcome("adapter_cost_bound_exceeded")
            await self.resources.verify_references(self.ctx, evidence_ids=result.evidence_ids,
                                                   artifact_ids=result.artifact_ids)
            if len(json.dumps(result.data, ensure_ascii=False).encode()) > 16000 and not (result.evidence_ids or result.artifact_ids or stored_result):
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
            if not entered_adapter and not cancelled:
                self._record_refusal(stored_name, code)
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
        finally:
            # After the outcome is saved and reported, so the output never goes while the row still says "running". Also
            # when the call never started (declined, refused): whatever showed it being written goes with it.
            if live_call is not None:
                self.live.settle(live_call)
