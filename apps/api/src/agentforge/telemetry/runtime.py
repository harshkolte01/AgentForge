"""Trace actual manual model/tool calls: what ran, how long, what it cost and how it ended.

Prompt, answer and tool text is added only when the operator turned content capture on (`telemetry.capture_content`); the
export mask (`neatlogs_setup.py`) decides again what leaves the process, so a bug here cannot widen it.
"""
import inspect
import json

from .neatlogs_setup import MAX_VALUE_CHARS, mark_error, set_attributes

MAX_LOGGED_MESSAGES = 30   # content mode: the newest messages of a request


def _text(value) -> str:
    """A message part or tool value as text, cut to a size one attribute can carry."""
    if not isinstance(value, str):
        try:
            value = json.dumps(value, default=str, ensure_ascii=False)
        except (TypeError, ValueError):
            value = str(value)
    return value if len(value) <= MAX_VALUE_CHARS else value[:MAX_VALUE_CHARS] + " …[truncated]"


def request_content(messages) -> dict:
    """The newest messages of a model request as the SDK's indexed message attributes."""
    attributes = {}
    for index, message in enumerate(list(messages)[-MAX_LOGGED_MESSAGES:]):
        if not isinstance(message, dict):
            continue
        attributes[f"neatlogs.llm.input_messages.{index}.role"] = str(message.get("role", ""))
        if message.get("content") is not None:
            attributes[f"neatlogs.llm.input_messages.{index}.content"] = _text(message["content"])
    return attributes


def response_content(message) -> dict:
    """The model's reply (and any tool calls it made) as the SDK's output attributes."""
    attributes = {"neatlogs.llm.output_messages.0.role": "assistant"}
    if isinstance(message, dict) and message.get("content"):
        attributes["neatlogs.llm.output_messages.0.content"] = _text(message["content"])
    calls = message.get("tool_calls") if isinstance(message, dict) else None
    for index, call in enumerate(calls if isinstance(calls, list) else []):
        function = call.get("function", {}) if isinstance(call, dict) else {}
        attributes[f"llm.tool_calls.{index}.name"] = str(function.get("name", ""))
        attributes[f"llm.tool_calls.{index}.arguments"] = _text(function.get("arguments", ""))
        if isinstance(call, dict) and call.get("id"):
            attributes[f"llm.tool_calls.{index}.id"] = str(call["id"])[:100]
    return attributes


class ObservedModel:
    def __init__(self, model, telemetry, store):
        self.model, self.telemetry, self.store = model, telemetry, store
        self.model_id = model.model_id
        # Harness hints some models expose; absent on fixture models.
        self.context_budget_tokens = getattr(model, 'context_budget_tokens', None)
        self.max_input_tokens = getattr(model, 'max_input_tokens', None)
        self.strict_tools = bool(getattr(model, 'strict_tools', False))

    def maximum_request_cost(self, messages, tools):
        return self.model.maximum_request_cost(messages, tools)

    async def complete(self, messages, tools, on_text=None, tool_choice=None, on_narration=None, on_retry=None, on_tool=None):
        self.store.progress("model.started", {"model": self.model_id})
        capture = bool(getattr(self.telemetry, "capture_content", False))
        failed_attempts: list[str] = []
        with self.telemetry.span("model_request", kind="LLM") as span:
            if span is not None:
                set_attributes(span, {"task_id": str(self.store.lease.task_id), "llm.model": self.model_id,
                    "llm.model_name": self.model_id,
                    "agentforge.llm.reasoning_effort": getattr(self.model, "reasoning_effort", None), "llm.provider": getattr(self.model, "provider", "openai"),
                    "llm.system": getattr(self.model, "provider", "openai"),
                    "agentforge.llm.messages": len(messages), "agentforge.llm.tools_offered": len(tools or ())})
                if capture:
                    set_attributes(span, request_content(messages))
            extra = {key: value for key, value in (('on_text', on_text), ('tool_choice', tool_choice),
                                                   ('on_narration', on_narration)) if value is not None}
            parameters = inspect.signature(self.model.complete).parameters
            if on_retry is not None and "on_retry" in parameters:
                extra["on_retry"] = on_retry
            if on_tool is not None and "on_tool" in parameters:
                # Tool calls drafted as the model writes them (subagents/runtime.py offers this only when `complete` takes it).
                extra["on_tool"] = on_tool
            if span is not None and "on_attempt_failed" in parameters:
                # Every failed request that is sent again: rate limits and outages are the retries worth seeing in a trace.
                extra["on_attempt_failed"] = failed_attempts.append
            try:
                response = await self.model.complete(messages, tools, **extra)
            finally:
                if failed_attempts:
                    set_attributes(span, {"agentforge.llm.retries": len(failed_attempts),
                        "agentforge.llm.retry_reasons": ",".join(sorted(set(failed_attempts)))})
            if span is not None:
                usage = response.usage
                set_attributes(span, {
                    "gen_ai.usage.input_tokens": usage.input_tokens, "gen_ai.usage.output_tokens": usage.output_tokens,
                    "llm.token_count.total": usage.input_tokens + usage.output_tokens,
                    "agentforge.llm.cost_known": usage.cost_usd is not None,
                    "agentforge.llm.reported_model": getattr(response, "model_id", None),
                    "neatlogs.llm.choices.0.finish_reason": getattr(response, "finish_reason", None),
                    "agentforge.llm.tool_calls": len((response.message or {}).get("tool_calls") or ())})
                if usage.cost_usd is not None:
                    set_attributes(span, {"cost_usd": float(usage.cost_usd), "neatlogs.llm.cost_usd": float(usage.cost_usd)})
                if capture:
                    set_attributes(span, response_content(response.message))
        self.store.progress("model.finished", {"model": self.model_id,
            "input_tokens": response.usage.input_tokens, "output_tokens": response.usage.output_tokens})
        return response


def observed_agent(spawn, telemetry):
    """Trace each agent run (the main agent and every delegated subagent) as an AGENT span that ends failed when the run did.

    `spawn_subagent` reports a failed run as a returned result, not an exception, so the result is what decides the status.
    """
    async def call(request, ctx, **kwargs):
        with telemetry.span(f"agent:{request.type}", kind="AGENT") as span:
            set_attributes(span, {"task_id": str(ctx.task_id), "spawn_id": kwargs.get("run_id"), "agent.type": request.type,
                "neatlogs.agent.name": request.type, "definition.version": request.version,
                "agentforge.agent.depth": ctx.depth + 1, "agentforge.agent.parent_run_id": ctx.parent_run_id})
            result = await spawn(request, ctx, **kwargs)
            if span is not None:
                set_attributes(span, {"agentforge.run.status": result.status, "agentforge.error.code": result.error_code})
                if result.status != "PASS":
                    mark_error(span, code=result.error_code)
            return result
    return call


def observed_tool(tool, telemetry, store):
    """Trace one tool call. The gateway, not this wrapper, reports its start and finish (tools/lifecycle.py)."""
    async def call(arguments, execution):
        capture = bool(getattr(telemetry, "capture_content", False))
        with telemetry.span(tool.name, kind="TOOL") as span:
            if span is not None:
                set_attributes(span, {"task_id": str(store.lease.task_id), "spawn_id": str(execution.agent_run_id),
                    "tool.name": tool.name})
                if capture:
                    dump = getattr(arguments, "model_dump", None)
                    set_attributes(span, {"input.value": _text(dump(mode="json") if dump is not None else arguments)})
            outcome = await tool.handler(arguments, execution)
            if span is not None:
                set_attributes(span, {"status": outcome.status, "agentforge.tool.cost_usd": outcome.cost_usd,
                    "agentforge.tool.evidence_count": len(outcome.evidence_ids or ()),
                    "agentforge.tool.artifact_count": len(outcome.artifact_ids or ())})
                if outcome.status == "error":
                    # The tool returned instead of raising, so the span would read as a success without this.
                    mark_error(span, code=outcome.error_code)
                if capture:
                    set_attributes(span, {"output.value": _text(outcome.data)})
            return outcome
    return call
