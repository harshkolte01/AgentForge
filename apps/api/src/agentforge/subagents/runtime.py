"""One reusable, bounded LangGraph child loop for built-in and custom definitions.

The graph is compiled per invocation with default per-invocation persistence.
Standalone Phase 2 calls are not restart durable. Phase 3 must invoke this graph
inside its parent/checkpointer and add operation reconciliation before resuming.
"""

import asyncio
import json
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from agentforge.contracts.agent_definition import AgentDefinition
from agentforge.contracts.subagent import (
    ExecutionResult, ResearchResult, ReviewResult, SubagentResult, parse_result,
)
from agentforge.llm.models import ModelProviderError, ToolCallingModel
from agentforge.security.policies import validate_relative_path
from agentforge.tools.gateway import ToolGateway

from .budget import BudgetExceeded, BudgetService, Reservation
from .resources import ResourceResolver
from .scope import EffectiveScope, ScopeDenied, TaskContext


class ChildState(TypedDict):
    messages: list[dict[str, Any]]
    pending: list[dict[str, Any]]
    steps: int
    formatting_retry: bool
    result: dict[str, Any] | None


async def validate_result(raw: str, definition: AgentDefinition, ctx: TaskContext,
                          resources: ResourceResolver) -> SubagentResult:
    result = parse_result(definition.output_schema_id, raw)
    evidence = set(result.evidence_ids)
    artifacts = set(result.artifact_ids)
    if isinstance(result, ResearchResult):
        for claim in result.claims:
            evidence.update(claim.source_ids)
        if result.status == "PASS" and not result.claims:
            raise ValueError("research PASS requires evidence-linked claims")
    elif isinstance(result, ExecutionResult):
        evidence.update(result.test_evidence_ids)
        for path in result.changed_paths:
            validate_relative_path(path)
        if result.status == "PASS" and not result.test_evidence_ids:
            raise ValueError("execution PASS requires test evidence")
    elif isinstance(result, ReviewResult):
        for finding in result.findings:
            evidence.update(finding.evidence_ids)
            if finding.path is not None:
                validate_relative_path(finding.path)
        if result.status == "PASS" and not result.acceptance_checks:
            raise ValueError("review PASS requires identified acceptance checks")
    if result.status == "PASS" and not evidence:
        raise ValueError("PASS requires external or deterministic evidence")
    await resources.verify_references(ctx, evidence_ids=tuple(sorted(evidence)), artifact_ids=tuple(sorted(artifacts)))
    # Preserve every specialized reference in the common envelope delivered to
    # the parent; the full specialized result is separately persisted by spawn.
    payload = result.model_dump(mode="json")
    payload["evidence_ids"] = sorted(evidence)
    payload["artifact_ids"] = sorted(artifacts)
    return parse_result(definition.output_schema_id, payload)


class ChildRuntime:
    async def run(self, *, definition: AgentDefinition, ctx: TaskContext,
                  scope: EffectiveScope, model: ToolCallingModel, gateway: ToolGateway,
                  budgets: BudgetService, reservation: Reservation,
                  messages: list[dict[str, Any]], resources: ResourceResolver,
                  reauthorize: Callable[[], Awaitable[None]],
                  config: RunnableConfig | None = None) -> SubagentResult:
        schemas = gateway.schemas()

        async def call_model(state: ChildState) -> dict[str, Any]:
            await reauthorize()
            budgets.check_active(reservation.id)
            if state["steps"] >= scope.budget.max_steps:
                raise BudgetExceeded("step_limit_exceeded")
            tools = [] if state["formatting_retry"] else schemas
            bound = model.maximum_request_cost(state["messages"], tools)
            operation_key = f"{reservation.run_id}:model:{state['steps']}"
            budgets.authorize_operation(reservation.id, operation_key, bound, kind="model")
            try:
                response = await model.complete(state["messages"], tools)
            except BaseException:
                budgets.record_usage(reservation.id, operation_key, None)
                raise
            budgets.record_usage(reservation.id, operation_key, response.usage.cost_usd,
                usage={"input_tokens": response.usage.input_tokens,
                       "output_tokens": response.usage.output_tokens,
                       "configured_model": model.model_id, "reported_model": response.model_id})
            if response.usage.cost_usd is not None and response.usage.cost_usd > bound:
                raise BudgetExceeded("model_cost_bound_exceeded")
            if response.finish_reason not in ("stop", "tool_calls"):
                # A response can contain syntactically valid JSON and still be
                # truncated or filtered; it cannot become a complete handoff.
                return {"result": SubagentResult(status="PARTIAL",
                    summary="Model response was incomplete or filtered; child verification is incomplete.").model_dump(mode="json"),
                    "pending": [], "steps": state["steps"] + 1}
            message = response.message
            if len(json.dumps(message, allow_nan=False).encode()) > 262144 or message.get("role") != "assistant":
                raise ModelProviderError("invalid model response envelope")
            pending = message.get("tool_calls") or []
            if not isinstance(pending, list) or len(pending) > scope.budget.max_tool_calls:
                raise ModelProviderError("invalid model tool calls")
            if state["formatting_retry"] and pending:
                raise ModelProviderError("formatting retry attempted tool execution")
            if len({call.get("id") for call in pending if isinstance(call, dict)}) != len(pending):
                raise ModelProviderError("duplicate model tool call identifiers")
            return {"messages": [*state["messages"], message], "pending": pending,
                    "steps": state["steps"] + 1}

        async def execute_tools(state: ChildState) -> dict[str, Any]:
            replies: list[dict[str, Any]] = []
            for call in state["pending"]:
                if not isinstance(call, dict) or call.get("type") != "function":
                    raise ModelProviderError("invalid tool call type")
                function = call.get("function")
                if not isinstance(function, dict) or not isinstance(function.get("name"), str) or not isinstance(function.get("arguments"), str):
                    raise ModelProviderError("invalid function call")
                call_id = call.get("id")
                if not isinstance(call_id, str):
                    raise ModelProviderError("invalid tool call identifier")
                outcome = await gateway.execute(function["name"], function["arguments"], call_id)
                payload = outcome.model_dump(mode="json", exclude={"cost_usd"})
                rendered = json.dumps(payload, ensure_ascii=False, allow_nan=False)
                # Full result is bounded by ToolOutcome. Use an explicitly marked
                # preview when needed, preserving ownership-checked reference IDs.
                if len(rendered.encode()) > 16000:
                    payload["data"] = {"preview_truncated": True,
                                       "preview": json.dumps(outcome.data, ensure_ascii=False)[:8000]}
                    rendered = json.dumps(payload, ensure_ascii=False, allow_nan=False)
                replies.append({"role": "tool", "tool_call_id": call_id,
                                "content": "UNTRUSTED TOOL RESULT DATA:\n" + rendered})
            return {"messages": [*state["messages"], *replies], "pending": []}

        async def finalize(state: ChildState) -> dict[str, Any]:
            await reauthorize()
            budgets.check_active(reservation.id)
            raw = state["messages"][-1].get("content")
            try:
                if not isinstance(raw, str):
                    raise ValueError("model result is missing JSON")
                result = await validate_result(raw, definition, ctx, resources)
                return {"result": result.model_dump(mode="json")}
            except (ValidationError, ValueError, ScopeDenied):
                if state["formatting_retry"]:
                    result = SubagentResult(status="PARTIAL", summary="Child output failed its result schema or evidence ownership checks.")
                    return {"result": result.model_dump(mode="json")}
                return {"formatting_retry": True,
                        "messages": [*state["messages"], {"role": "user", "content":
                            "Return valid JSON for the supplied result schema. No tools are available for this formatting-only retry. "
                            "Use only previously returned task-owned evidence IDs. Missing evidence requires PARTIAL or BLOCKED."}]}

        graph = StateGraph(ChildState)
        graph.add_node("model", call_model)
        graph.add_node("tools", execute_tools)
        graph.add_node("finalize", finalize)
        graph.add_edge(START, "model")
        graph.add_conditional_edges("model", lambda s: END if s["result"] is not None else ("tools" if s["pending"] else "finalize"))
        graph.add_edge("tools", "model")
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
                                         "formatting_retry": False, "result": None}, invoke_config)
        result_payload = output["result"]
        # A schema retry failure is a common PARTIAL envelope; successful
        # specialized payloads retain their typed fields for durable history.
        if set(result_payload).issubset(SubagentResult.model_fields):
            return SubagentResult.model_validate_json(json.dumps(result_payload))
        return parse_result(definition.output_schema_id, result_payload)
