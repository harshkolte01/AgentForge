"""One reusable, bounded LangGraph child loop for built-in and custom definitions.

The graph is compiled per invocation with default per-invocation persistence.
Standalone Phase 2 calls are not restart durable. Phase 3 must invoke this graph
inside its parent/checkpointer and add operation reconciliation before resuming.

How a child works, and why it behaves predictably:
- It acts through typed tools and finishes by calling the synthetic `submit_result` tool, whose
  arguments are the typed result. A malformed result is returned to the model as a normal tool error
  naming the exact fields, so it can fix them (and still gather missing evidence) instead of guessing.
- It always knows its limits: every tool result reports the steps and tool calls left, and when a
  limit, the clock or a streak of failures ends the run, the last turn is forced to submit an honest
  PARTIAL result instead of the work being thrown away.
- Independent read-only calls in one turn run side by side; anything with an effect runs alone.
- Old tool results are compacted when the conversation outgrows its budget.
"""

import asyncio
import json
from decimal import Decimal
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from agentforge.contracts.agent_definition import AgentDefinition
from agentforge.contracts.subagent import (
    RESULT_MODELS, ExecutionResult, ResearchResult, ReviewResult, SubagentResult, parse_result,
)
from agentforge.llm.models import SUBMIT_RESULT, ModelContextLimit, ModelProviderError, ToolCallingModel, conservative_input_tokens
from agentforge.security.policies import validate_relative_path
from agentforge.tools.catalog import clean_schema, error_guidance
from agentforge.tools.errors import render_issues, validation_issues
from agentforge.tools.gateway import MAX_CONSECUTIVE_ERRORS, ToolGateway

from .budget import BudgetExceeded, BudgetService, Reservation
from .context import KEEP_RECENT_RESULTS, RESULT_PREFIX, compact_messages
from .resources import ResourceResolver
from .scope import EffectiveScope, ScopeDenied, TaskContext

MAX_RESULT_REPAIRS = 2
RESULT_PREVIEW_BYTES = 16000
# When this little wall-clock time remains, the next turn is the forced final answer.
WRAP_UP_SECONDS = 20
LOW_RESOURCE = 2
# A rendered tool result is capped near 16 KB, which is about this many tokens. A turn may only request as
# many results as fit in the room left in the window, and when not even one fits the run wraps up.
RESULT_TOKENS = 5400

SUBMIT_DESCRIPTION = (
    "Finish the assignment by submitting your final typed result. Call it once, by itself, when you have what you "
    "need or when told a limit was reached. The arguments are the result: status, summary and the fields your role "
    "describes. PASS needs evidence ids that tools returned in this run; if something is missing use PARTIAL or BLOCKED.")

LIMIT_NOTICES = {
    "steps": "STEP LIMIT REACHED. You have no further steps.",
    "tool_calls": "TOOL CALL LIMIT REACHED. You have no further tool calls.",
    "time": "TIME LIMIT NEARLY REACHED. There is no time for further work.",
    "errors": "TOOLS KEEP FAILING. Repeating them will not help.",
    "context": "THE CONTEXT WINDOW IS FULL. There is no room to read anything further.",
    "budget": "THE BUDGET IS NEARLY SPENT. There is not enough left for further tool calls.",
}
LIMIT_INSTRUCTION = (" Call submit_result now with the best honest result from what you have gathered. Use status PARTIAL "
                     "(or BLOCKED) if the assignment is not fully done, and say exactly what is missing.")


class ChildState(TypedDict):
    messages: list[dict[str, Any]]
    pending: list[dict[str, Any]]
    steps: int
    forced: bool
    repairs: int
    result: dict[str, Any] | None


class ResultRuleError(ValueError):
    """A result broke a platform evidence rule; the message is ours and safe to show the model."""


def submit_tool(definition: AgentDefinition) -> dict[str, Any]:
    schema = clean_schema(RESULT_MODELS[definition.output_schema_id].model_json_schema())
    return {"type": "function", "function": {"name": SUBMIT_RESULT, "description": SUBMIT_DESCRIPTION, "parameters": schema}}


def _without_nulls(raw: str, schema_id: str) -> str:
    """Models often send null for an optional field they mean to omit; treat that as omitted."""
    try:
        data = json.loads(raw)
    except ValueError:
        return raw
    if not isinstance(data, dict):
        return raw
    fields = RESULT_MODELS[schema_id].model_fields
    return json.dumps({key: value for key, value in data.items()
                       if not (value is None and key in fields and not fields[key].is_required())})


def result_feedback(exc: BaseException) -> str:
    """What to tell the model about a rejected result, without echoing any of its content."""
    if isinstance(exc, ValidationError):
        return render_issues(validation_issues(exc))
    if isinstance(exc, ResultRuleError):
        return str(exc)
    return ("An id in evidence_ids, artifact_ids, source_ids or test_evidence_ids is not one that a tool returned in this run. "
            "Remove ids you did not receive from a tool.")


async def validate_result(raw: str, definition: AgentDefinition, ctx: TaskContext,
                          resources: ResourceResolver) -> SubagentResult:
    result = parse_result(definition.output_schema_id, raw)
    evidence = set(result.evidence_ids)
    artifacts = set(result.artifact_ids)
    if isinstance(result, ResearchResult):
        for claim in result.claims:
            evidence.update(claim.source_ids)
        if result.status == "PASS" and not result.claims and definition.output_schema_id != "general_result_v1":
            raise ResultRuleError("A research PASS needs claims, each with an exact quotation and its source_ids. Add claims, or use PARTIAL.")
    elif isinstance(result, ExecutionResult):
        evidence.update(result.test_evidence_ids)
        for path in result.changed_paths:
            validate_relative_path(path)
        if result.status == "PASS" and not result.test_evidence_ids:
            raise ResultRuleError("An execution PASS needs test_evidence_ids from a run_tests result. Run the tests, or use PARTIAL.")
    elif isinstance(result, ReviewResult):
        for finding in result.findings:
            evidence.update(finding.evidence_ids)
            if finding.path is not None:
                validate_relative_path(finding.path)
        if result.status == "PASS" and not result.acceptance_checks:
            raise ResultRuleError("A review PASS needs acceptance_checks naming the checks you performed.")
    if result.status == "PASS" and not (evidence or artifacts):
        raise ResultRuleError("A PASS needs evidence: evidence_ids or artifact_ids that tools returned in this run. Use PARTIAL if you have none.")
    await resources.verify_references(ctx, evidence_ids=tuple(sorted(evidence)), artifact_ids=tuple(sorted(artifacts)))
    # Preserve every specialized reference in the common envelope delivered to
    # the parent; the full specialized result is separately persisted by spawn.
    payload = result.model_dump(mode="json")
    payload["evidence_ids"] = sorted(evidence)
    payload["artifact_ids"] = sorted(artifacts)
    return parse_result(definition.output_schema_id, payload)


def _partial(summary: str) -> dict[str, Any]:
    return SubagentResult(status="PARTIAL", summary=summary).model_dump(mode="json")


class ChildRuntime:
    async def run(self, *, definition: AgentDefinition, ctx: TaskContext,
                  scope: EffectiveScope, model: ToolCallingModel, gateway: ToolGateway,
                  budgets: BudgetService, reservation: Reservation,
                  messages: list[dict[str, Any]], resources: ResourceResolver,
                  reauthorize: Callable[[], Awaitable[None]],
                  config: RunnableConfig | None = None, stream=None) -> SubagentResult:
        submit = submit_tool(definition)
        schemas = [*gateway.schemas(), submit]
        context_budget = getattr(model, "context_budget_tokens", None)
        max_input = getattr(model, "max_input_tokens", None)
        max_steps = scope.budget.max_steps
        last_bound = {"cost": Decimal(0)}

        def limit_reason(steps: int) -> str | None:
            if steps >= max_steps - 1:
                return "steps"
            if getattr(gateway, "calls_left", 1) <= 0:
                return "tool_calls"
            if getattr(gateway, "consecutive_errors", 0) >= MAX_CONSECUTIVE_ERRORS:
                return "errors"
            remaining = getattr(budgets, "remaining_usd", None)
            if getattr(gateway, "budget_exhausted", False) or (remaining and last_bound["cost"] and
                    remaining(reservation.id) < 2 * last_bound["cost"] + getattr(gateway, "max_tool_cost", Decimal(0))):
                # Keep enough for the forced final answer (one model call, plus a margin) before the money is gone.
                return "budget"
            if (reservation.deadline - datetime.now(timezone.utc)).total_seconds() < min(WRAP_UP_SECONDS, scope.budget.max_wall_time_sec / 4):
                return "time"
            return None

        def room_left(conversation: list[dict[str, Any]], tools: list[dict[str, Any]]) -> int | None:
            """Input tokens still free once every result the model has already read is stubbed out."""
            if not max_input:
                return None
            return max_input - conservative_input_tokens(compact_messages(conversation, 1, 0), tools)

        def fit(conversation: list[dict[str, Any]], tools: list[dict[str, Any]]):
            """The messages to send and their cost bound: compact gently, then harder, until the request fits."""
            for keep in (KEEP_RECENT_RESULTS, 1, 0):
                sent = compact_messages(conversation, context_budget if keep == KEEP_RECENT_RESULTS else 1, keep)
                try:
                    return sent, model.maximum_request_cost(sent, tools)
                except ModelContextLimit:
                    continue
            return None

        async def call_model(state: ChildState) -> dict[str, Any]:
            await reauthorize()
            budgets.check_active(reservation.id)
            steps = state["steps"]
            if steps >= max_steps:
                raise BudgetExceeded("step_limit_exceeded")
            conversation = state["messages"]
            forced = state["forced"]
            reason = None if forced else limit_reason(steps)
            if reason is None and not forced:
                room = room_left(conversation, schemas)
                if room is not None and room < RESULT_TOKENS:
                    reason = "context"
            if reason is not None:
                forced = True
                conversation = [*conversation, {"role": "user", "content": LIMIT_NOTICES[reason] + LIMIT_INSTRUCTION}]
            tools = [submit] if forced else schemas
            extra: dict[str, Any] = {}
            if forced:
                extra["tool_choice"] = {"type": "function", "function": {"name": SUBMIT_RESULT}}
            if stream is not None:
                extra["on_text"] = stream.feed
            fitted = fit(conversation, tools)
            if fitted is None:
                # Even with every old result stubbed the instructions and brief alone fill the window.
                return {"result": _partial("This request needs more room than the model's context window allows, so it could not be answered."),
                        "pending": [], "steps": steps + 1}
            sent, bound = fitted
            last_bound["cost"] = bound
            stubbed = [kept.get("tool_call_id") for original, kept in zip(conversation, sent) if original is not kept]
            if stubbed:
                # A result that was stubbed out can be fetched again; an identical call is no longer a repeat.
                getattr(gateway, "forget", lambda _ids: None)(stubbed)
            operation_key = f"{reservation.run_id}:model:{steps}"
            budgets.authorize_operation(reservation.id, operation_key, bound, kind="model")
            if stream is not None:
                stream.begin()
            try:
                response = await model.complete(sent, tools, **extra)
                if stream is not None:
                    stream.end()
            except BaseException:
                budgets.record_usage(reservation.id, operation_key, None)
                raise
            budgets.record_usage(reservation.id, operation_key, response.usage.cost_usd,
                usage={"input_tokens": response.usage.input_tokens,
                       "output_tokens": response.usage.output_tokens, "compacted_results": len(stubbed),
                       "configured_model": model.model_id, "reported_model": response.model_id})
            if response.usage.cost_usd is not None and response.usage.cost_usd > bound:
                raise BudgetExceeded("model_cost_bound_exceeded")
            if response.finish_reason not in ("stop", "tool_calls"):
                # A response can contain syntactically valid JSON and still be
                # truncated or filtered; it cannot become a complete handoff.
                return {"result": _partial("Model response was incomplete or filtered; child verification is incomplete."),
                        "pending": [], "steps": steps + 1}
            message = response.message
            if len(json.dumps(message, allow_nan=False).encode()) > 262144 or message.get("role") != "assistant":
                raise ModelProviderError("invalid model response envelope")
            pending = message.get("tool_calls") or []
            if not isinstance(pending, list) or len(pending) > scope.budget.max_tool_calls:
                raise ModelProviderError("invalid model tool calls")
            if len({call.get("id") for call in pending if isinstance(call, dict)}) != len(pending):
                raise ModelProviderError("duplicate model tool call identifiers")
            if forced and any(not isinstance(call, dict) or (call.get("function") or {}).get("name") != SUBMIT_RESULT for call in pending):
                return {"result": _partial("The model kept calling tools after its limits were reached; the work is incomplete."),
                        "pending": [], "steps": steps + 1}
            return {"messages": [*conversation, message], "pending": pending, "steps": steps + 1, "forced": forced}

        async def run_tool(call: dict[str, Any]):
            return await gateway.execute(call["function"]["name"], call["function"]["arguments"], call["id"])

        def render(call: dict[str, Any], outcome, steps_left: int) -> dict[str, Any]:
            name = call["function"]["name"]
            payload = {"tool": name, **outcome.model_dump(mode="json", exclude={"cost_usd", "error_detail"})}
            if outcome.status == "error":
                if outcome.error_detail:
                    payload["detail"] = outcome.error_detail
                payload["guidance"] = error_guidance(outcome.error_code)
                notice = getattr(gateway, "repeated_failure_notice", lambda _name: None)(name)
                if notice:
                    payload["notice"] = notice
            calls_left = getattr(gateway, "calls_left", None)
            payload["limits"] = {"steps_left": steps_left, **({"tool_calls_left": calls_left} if calls_left is not None else {})}
            if steps_left <= LOW_RESOURCE or (calls_left is not None and calls_left <= LOW_RESOURCE):
                payload["notice"] = "You are nearly out of steps or tool calls. Finish soon: call submit_result with what you have."
            rendered = json.dumps(payload, ensure_ascii=False, allow_nan=False)
            # Full result is bounded by ToolOutcome. Use an explicitly marked
            # preview when needed, preserving ownership-checked reference IDs.
            if len(rendered.encode()) > RESULT_PREVIEW_BYTES:
                payload["data"] = {"preview_truncated": True, "preview": json.dumps(outcome.data, ensure_ascii=False)[:8000]}
                rendered = json.dumps(payload, ensure_ascii=False, allow_nan=False)
            return {"role": "tool", "tool_call_id": call["id"], "content": RESULT_PREFIX + rendered}

        def deferred(call: dict[str, Any], allowed: int, steps_left: int) -> dict[str, Any]:
            detail = (f"Not run: this turn's results would not all fit in the context window. Request at most {allowed} per turn and ask "
                      "for this one in a later turn." if allowed else
                      "Not run: the context window has no room left for more results. Call submit_result with what you have.")
            payload = {"tool": call["function"]["name"], "status": "error", "error_code": "DEFERRED", "detail": detail,
                       "guidance": error_guidance("DEFERRED"), "limits": {"steps_left": steps_left}}
            return {"role": "tool", "tool_call_id": call["id"], "content": RESULT_PREFIX + json.dumps(payload, ensure_ascii=False)}

        def reply(call: dict[str, Any], text: str) -> dict[str, Any]:
            return {"role": "tool", "tool_call_id": call["id"], "content": text}

        async def execute_tools(state: ChildState) -> dict[str, Any]:
            calls = state["pending"]
            for call in calls:
                if not isinstance(call, dict) or call.get("type") != "function":
                    raise ModelProviderError("invalid tool call type")
                function = call.get("function")
                if not isinstance(function, dict) or not isinstance(function.get("name"), str) or not isinstance(function.get("arguments"), str):
                    raise ModelProviderError("invalid function call")
                if not isinstance(call.get("id"), str):
                    raise ModelProviderError("invalid tool call identifier")
            worked = [call for call in calls if call["function"]["name"] != SUBMIT_RESULT]
            submits = [call for call in calls if call["function"]["name"] == SUBMIT_RESULT]
            steps_left = max(0, max_steps - state["steps"])
            replies: dict[str, dict[str, Any]] = {}
            # Results the model has not read can never be stubbed, so a turn may only run as many calls as
            # will fit in the room left. The rest are answered as deferred, not run, so nothing counts as read
            # that the model never saw.
            room = room_left(state["messages"], schemas)
            allowed = len(worked) if room is None else min(len(worked), max(0, room // RESULT_TOKENS))
            for call in worked[allowed:]:
                replies[call["id"]] = deferred(call, allowed, steps_left)
            worked = worked[:allowed]
            if len(worked) > 1 and all(getattr(gateway, "is_parallel_safe", lambda _n: False)(call["function"]["name"]) for call in worked):
                tasks = [asyncio.ensure_future(run_tool(call)) for call in worked]
                try:
                    outcomes = list(await asyncio.gather(*tasks))
                except BaseException:
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                    raise
            else:
                outcomes = [await run_tool(call) for call in worked]
            for call, outcome in zip(worked, outcomes):
                replies[call["id"]] = render(call, outcome, steps_left)
            result = None
            repairs = state["repairs"]
            if submits and (worked or len(submits) > 1):
                for call in submits:
                    replies[call["id"]] = reply(call, "submit_result must be called once, by itself, after you have seen every tool "
                                                      "result. Nothing was submitted. Call it again alone.")
            elif submits:
                call = submits[0]
                try:
                    accepted = await validate_result(_without_nulls(call["function"]["arguments"], definition.output_schema_id),
                                                     definition, ctx, resources)
                    result = accepted.model_dump(mode="json")
                except (ValidationError, ValueError, ScopeDenied) as exc:
                    repairs += 1
                    if repairs > MAX_RESULT_REPAIRS or steps_left <= 0:
                        result = _partial("Child output failed its result schema or evidence ownership checks.")
                    else:
                        replies[call["id"]] = reply(call, "RESULT REJECTED, nothing was submitted: " + result_feedback(exc) +
                                                    " Fix exactly this and call submit_result again.")
            messages = [*state["messages"], *(replies[call["id"]] for call in calls if call["id"] in replies)]
            return {"messages": messages, "pending": [], "repairs": repairs, "result": result}

        async def finalize(state: ChildState) -> dict[str, Any]:
            # The model answered in plain content instead of calling submit_result. Accept a valid JSON
            # result (models that ignore tools), otherwise force a typed submission once.
            await reauthorize()
            budgets.check_active(reservation.id)
            raw = state["messages"][-1].get("content")
            try:
                if not isinstance(raw, str):
                    raise ValueError("model result is missing JSON")
                result = await validate_result(_without_nulls(raw, definition.output_schema_id), definition, ctx, resources)
                return {"result": result.model_dump(mode="json")}
            except (ValidationError, ValueError, ScopeDenied) as exc:
                if state["forced"] or state["steps"] >= max_steps:
                    return {"result": _partial("Child output failed its result schema or evidence ownership checks.")}
                problem = (result_feedback(exc) if isinstance(exc, (ValidationError, ResultRuleError, ScopeDenied))
                           else "your reply was not a typed result")
                return {"forced": True, "messages": [*state["messages"], {"role": "user", "content":
                    f"Your last reply was not accepted ({problem}). Call submit_result now with the typed result. "
                    "Use only evidence ids that tools returned in this run; missing evidence means PARTIAL or BLOCKED."}]}

        graph = StateGraph(ChildState)
        graph.add_node("model", call_model)
        graph.add_node("tools", execute_tools)
        graph.add_node("finalize", finalize)
        graph.add_edge(START, "model")
        graph.add_conditional_edges("model", lambda s: END if s["result"] is not None else ("tools" if s["pending"] else "finalize"))
        graph.add_conditional_edges("tools", lambda s: END if s["result"] is not None else "model")
        graph.add_conditional_edges("finalize", lambda s: END if s["result"] is not None else "model")
        child = graph.compile()
        invoke_config: RunnableConfig = dict(config or {})
        invoke_config["recursion_limit"] = scope.budget.max_steps * 3 + 5
        # Scope a standalone child thread to the unique run. The parent task
        # thread is metadata, never reused as a cross-child conversation key.
        configurable = dict(invoke_config.get("configurable") or {})
        configurable["thread_id"] = f"{ctx.task_id}:child:{reservation.run_id}"
        invoke_config["configurable"] = configurable
        timeout_sec = max(0, (reservation.deadline - datetime.now(timezone.utc)).total_seconds())
        async with asyncio.timeout(timeout_sec):
            output = await child.ainvoke({"messages": messages, "pending": [], "steps": 0,
                                         "forced": False, "repairs": 0, "result": None}, invoke_config)
        result_payload = output["result"]
        # A schema retry failure is a common PARTIAL envelope; successful
        # specialized payloads retain their typed fields for durable history.
        if set(result_payload).issubset(SubagentResult.model_fields):
            return SubagentResult.model_validate_json(json.dumps(result_payload))
        return parse_result(definition.output_schema_id, result_payload)
