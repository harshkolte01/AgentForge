"""One reusable LangGraph agent loop for built-in and custom definitions.

How an agent works:
- The model gets the conversation, a system prompt and its tools. It calls tools until it has what it needs and then
  answers in plain text. That text, exactly as written, is the answer: nothing wraps it, grades it or turns it into a
  status.
- A tool error is returned to the model as a normal tool result naming what was wrong, so it can correct itself.
- Independent read-only calls in one turn run side by side; anything with an effect runs alone.
- Old tool results are compacted when the conversation outgrows the model's window. If it still cannot fit, the
  provider's own limit is what the person is told.
- Nothing here caps steps, tool calls, cost or time. A person stops a run with Stop; provider errors are raised as they are.
"""

import asyncio
import inspect
import json
from collections.abc import Awaitable, Callable
from typing import Any, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from agentforge.contracts.agent_definition import AgentDefinition
from agentforge.contracts.subagent import SubagentResult
from agentforge.contracts.tasks import MAX_ANSWER_CHARS
from agentforge.llm.models import ModelContextLimit, ModelProviderError, ToolCallingModel, conservative_input_tokens
from agentforge.tools.catalog import error_guidance
from agentforge.tools.gateway import ToolGateway

from .budget import BudgetService, Reservation
from .context import KEEP_RECENT_RESULTS, RESULT_PREFIX, compact_messages
from .resources import ResourceResolver
from .scope import EffectiveScope, TaskContext

# A reply cut off by the output limit while it was calling tools is asked for again, shorter, this many times.
MAX_CUTOFF_RETRIES = 2
CUTOFF_NOTICE = ("Your last reply was cut off by the output length limit, so none of it was used. Write it again, shorter: "
                 "summarise instead of listing everything, and keep any file content within about 3000 words.")
EMPTY_NOTICE = "Your last reply was empty. Reply to the user now."
CUT_OFF_NOTE = "\n\n[The model's reply was cut off by its output length limit.]"
# A final answer that reached the output limit is continued this many times before it is shown with CUT_OFF_NOTE.
MAX_CONTINUATIONS = 2
CONTINUE_NOTICE = "Your reply reached the output length limit. Continue it exactly where it stopped, without repeating anything."
# A reply that ends by announcing a next step ("Let me check the tests:") but makes no tool call has stopped too early.
STALLED_NOTICE = ("Your last message announced a next step but made no tool call, so nothing happened. Do it now, or give your "
                  "final answer if you are done.")
RESULT_PREVIEW_BYTES = 16000
# A rendered tool result is capped near 16 KB, which is about this many tokens. A turn may only request as
# many results as fit in the room left in the window.
RESULT_TOKENS = RESULT_PREVIEW_BYTES + 1000  # includes dense Unicode, JSON escaping and tool-message overhead
# LangGraph needs a finite recursion limit; this is far beyond any real run.
RECURSION_LIMIT = 1_000_000


class ChildState(TypedDict):
    messages: list[dict[str, Any]]
    pending: list[dict[str, Any]]
    steps: int
    cuts: int      # replies cut off by the output limit so far
    empties: int   # empty replies so far
    again: bool    # the last reply must be asked for again
    answer: str | None
    partial: str   # the start of an answer that reached the output limit and is being continued
    conts: int     # continuations asked for so far
    nudged: bool   # a stalled reply was already sent back once


def stalled(text: str) -> bool:
    """The reply ends by introducing something that never came: its last line ends with a colon."""
    last = text.rstrip().rsplit("\n", 1)[-1].strip()
    return last.endswith(":") and not last.startswith(("|", "#", "-", "*", ">")) and len(last) < 300


class ChildRuntime:
    async def run(self, *, definition: AgentDefinition, ctx: TaskContext,
                  scope: EffectiveScope, model: ToolCallingModel, gateway: ToolGateway,
                  budgets: BudgetService, reservation: Reservation,
                  messages: list[dict[str, Any]], resources: ResourceResolver,
                  reauthorize: Callable[[], Awaitable[None]],
                  config: RunnableConfig | None = None, stream=None) -> SubagentResult:
        schemas = gateway.schemas()

        def drop_live() -> None:
            """Whatever the sink shows is not going to be used (an attempt thrown away, a run that failed): clear it."""
            discard = getattr(stream, "discard", None)
            if discard is not None:
                discard()

        # Models that retry a broken stream themselves say so by taking `on_retry`; others are simply not told.
        accepts_retry = "on_retry" in inspect.signature(model.complete).parameters
        context_budget = getattr(model, "context_budget_tokens", None)
        max_input = getattr(model, "max_input_tokens", None)

        def room_left(conversation: list[dict[str, Any]]) -> int | None:
            """Input tokens still free once every result the model has already read is stubbed out."""
            if not max_input:
                return None
            return max_input - conservative_input_tokens(compact_messages(conversation, 1, 0), schemas)

        def fit(conversation: list[dict[str, Any]]):
            """The messages to send and their cost bound: compact gently, then harder, until the request fits."""
            for keep in (KEEP_RECENT_RESULTS, 1, 0):
                sent = compact_messages(conversation, context_budget if keep == KEEP_RECENT_RESULTS else 1, keep)
                try:
                    return sent, model.maximum_request_cost(sent, schemas)
                except ModelContextLimit:
                    continue
            return None

        async def call_model(state: ChildState) -> dict[str, Any]:
            await reauthorize()
            budgets.check_active(reservation.id)
            steps = state["steps"]
            conversation = state["messages"]
            fitted = fit(conversation)
            if fitted is None:
                raise ModelContextLimit("The conversation no longer fits in the model's context window, even with old tool results removed.")
            sent, bound = fitted
            stubbed = [kept.get("tool_call_id") for original, kept in zip(conversation, sent)
                       if original is not kept and kept.get("role") == "tool"]
            operation_key = f"{reservation.run_id}:model:{steps}"
            auditor = getattr(resources, "record_provider_input", None)
            if auditor is not None:
                await auditor(ctx, operation_key, sent, schemas)
            budgets.authorize_operation(reservation.id, operation_key, bound, kind="model")
            extra: dict[str, Any] = {}
            partial = state["partial"]
            broken = 0

            def restart() -> None:
                # A new attempt (or a retried, broken one) starts from the part of the answer already written.
                stream.begin()
                if partial:
                    stream.feed(partial)

            def retried() -> None:
                # A stream that broke midway was billed for what it produced, which the provider never reported, so the
                # ledger records that attempt as its own operation of unknown cost before the request is sent again.
                nonlocal broken
                broken += 1
                key = f"{operation_key}:broken:{broken}"
                budgets.authorize_operation(reservation.id, key, bound, kind="model")
                budgets.record_usage(reservation.id, key, None)
                if stream is not None:
                    restart()
            if accepts_retry:
                extra["on_retry"] = retried
            if stream is not None:
                extra["on_text"] = stream.feed
                extra["on_narration"] = stream.feed_narration
                restart()
            try:
                response = await model.complete(sent, schemas, **extra)
                if stream is not None:
                    stream.end()
            except BaseException:
                budgets.record_usage(reservation.id, operation_key, None)
                drop_live()
                raise
            budgets.record_usage(reservation.id, operation_key, response.usage.cost_usd,
                usage={"input_tokens": response.usage.input_tokens,
                       "output_tokens": response.usage.output_tokens, "compacted_results": len(stubbed),
                       "configured_model": model.model_id, "reported_model": response.model_id})
            message = response.message
            if len(json.dumps(message, allow_nan=False).encode()) > 4 * MAX_ANSWER_CHARS or message.get("role") != "assistant":
                raise ModelProviderError("invalid model response envelope")
            pending = message.get("tool_calls") or []
            if not isinstance(pending, list):
                raise ModelProviderError("invalid model tool calls")
            if len({call.get("id") for call in pending if isinstance(call, dict)}) != len(pending):
                raise ModelProviderError("duplicate model tool call identifiers")
            reason = response.finish_reason
            if reason == "length" and pending:
                # Cut off while writing tool calls: their arguments are unusable. Ask again, shorter.
                drop_live()
                if state["cuts"] >= MAX_CUTOFF_RETRIES:
                    raise ModelProviderError("The model kept running out of output space while calling tools.", detail="finish_reason: length")
                return {"messages": [*conversation, {"role": "user", "content": CUTOFF_NOTICE}], "pending": [],
                        "steps": steps + 1, "cuts": state["cuts"] + 1, "again": True}
            if reason not in ("stop", "tool_calls", "length"):
                drop_live()
                raise ModelProviderError(f"The model stopped before finishing its reply (finish reason: {reason}).", detail=f"finish_reason: {reason}")
            if pending:
                # Text next to tool calls was a note for the person: keep it in the run's history, in order.
                if stream is not None:
                    stream.commit_narration()
                return {"messages": [*conversation, message], "pending": pending, "steps": steps + 1, "again": False,
                        "partial": "", "conts": 0}
            written = message.get("content") or ""
            if not written.strip() and not partial:
                drop_live()
                if state["empties"] >= 1:
                    raise ModelProviderError("The model returned an empty reply.", detail="empty completion")
                return {"messages": [*conversation, {"role": "user", "content": EMPTY_NOTICE}], "pending": [],
                        "steps": steps + 1, "empties": state["empties"] + 1, "again": True}
            if reason == "length" and state["conts"] < MAX_CONTINUATIONS and len(partial) + len(written) < MAX_ANSWER_CHARS:
                # The answer ran into the output limit: keep what was written and ask for the rest.
                return {"messages": [*conversation, message, {"role": "user", "content": CONTINUE_NOTICE}], "pending": [],
                        "steps": steps + 1, "partial": partial + written, "conts": state["conts"] + 1, "again": True}
            text = (partial + written).strip()
            if schemas and not partial and not state["nudged"] and reason == "stop" and stalled(text):
                drop_live()
                return {"messages": [*conversation, message, {"role": "user", "content": STALLED_NOTICE}], "pending": [],
                        "steps": steps + 1, "nudged": True, "again": True}
            if reason == "length":
                text += CUT_OFF_NOTE
            if stream is not None:
                stream.commit_reply()
            return {"messages": [*conversation, message], "pending": [], "steps": steps + 1, "answer": text[:MAX_ANSWER_CHARS], "again": False}

        async def run_tool(call: dict[str, Any]):
            return await gateway.execute(call["function"]["name"], call["function"]["arguments"], call["id"])

        def render(call: dict[str, Any], outcome) -> dict[str, Any]:
            name = call["function"]["name"]
            payload = {"tool": name, **outcome.model_dump(mode="json", exclude={"cost_usd", "error_detail"})}
            if outcome.status == "error":
                if outcome.error_detail:
                    payload["detail"] = outcome.error_detail
                payload["guidance"] = error_guidance(outcome.error_code)
            rendered = json.dumps(payload, ensure_ascii=False, allow_nan=False)
            # Full result is bounded by ToolOutcome. Use an explicitly marked preview when needed, keeping reference ids.
            if len(rendered.encode()) > RESULT_PREVIEW_BYTES:
                preview = json.dumps(outcome.data, ensure_ascii=False)
                low, high = 0, min(len(preview), 8000)
                while low < high:
                    middle = (low + high + 1) // 2
                    candidate = {**payload, "data": {"preview_truncated": True, "preview": preview[:middle],
                        **({"resource_id": outcome.data["resource_id"]} if "resource_id" in outcome.data else {})}}
                    if len(json.dumps(candidate, ensure_ascii=False, allow_nan=False).encode()) <= RESULT_PREVIEW_BYTES:
                        low = middle
                    else:
                        high = middle - 1
                payload["data"] = {"preview_truncated": True, "preview": preview[:low],
                    **({"resource_id": outcome.data["resource_id"]} if "resource_id" in outcome.data else {})}
                rendered = json.dumps(payload, ensure_ascii=False, allow_nan=False)
            return {"role": "tool", "tool_call_id": call["id"], "content": RESULT_PREFIX + rendered}

        def deferred(call: dict[str, Any], allowed: int) -> dict[str, Any]:
            detail = (f"Not run: this turn's results would not all fit in the context window. Request at most {allowed} per turn and ask "
                      "for this one in a later turn." if allowed else
                      "Not run: the context window has no room left for more results. Answer with what you have.")
            payload = {"tool": call["function"]["name"], "status": "error", "error_code": "DEFERRED", "detail": detail,
                       "guidance": error_guidance("DEFERRED")}
            return {"role": "tool", "tool_call_id": call["id"], "content": RESULT_PREFIX + json.dumps(payload, ensure_ascii=False)}

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
            replies: dict[str, dict[str, Any]] = {}
            # Results the model has not read can never be stubbed, so a turn may only run as many calls as
            # will fit in the room left. The rest are answered as deferred, not run, so nothing counts as read
            # that the model never saw.
            room = room_left(state["messages"])
            allowed = len(calls) if room is None else min(len(calls), max(0, (room - 400 * len(calls)) // RESULT_TOKENS))
            for call in calls[allowed:]:
                replies[call["id"]] = deferred(call, allowed)
            worked = calls[:allowed]
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
                replies[call["id"]] = render(call, outcome)
            return {"messages": [*state["messages"], *(replies[call["id"]] for call in calls)], "pending": []}

        graph = StateGraph(ChildState)
        graph.add_node("model", call_model)
        graph.add_node("tools", execute_tools)
        graph.add_edge(START, "model")
        graph.add_conditional_edges("model", lambda s: END if s["answer"] is not None else ("tools" if s["pending"] else "model"))
        graph.add_edge("tools", "model")
        child = graph.compile()
        invoke_config: RunnableConfig = dict(config or {})
        invoke_config["recursion_limit"] = RECURSION_LIMIT
        # Scope a standalone child thread to the unique run. The parent task thread is metadata, never reused as a
        # cross-child conversation key.
        configurable = dict(invoke_config.get("configurable") or {})
        configurable["thread_id"] = f"{ctx.task_id}:child:{reservation.run_id}"
        invoke_config["configurable"] = configurable
        try:
            output = await child.ainvoke({"messages": messages, "pending": [], "steps": 0, "cuts": 0, "empties": 0,
                                          "again": False, "answer": None, "partial": "", "conts": 0, "nudged": False},
                                         invoke_config)
        except BaseException:
            # An error or a stop request ends the run without a reply: nothing may stay on screen.
            drop_live()
            raise
        return SubagentResult(status="PASS", summary=output["answer"])
