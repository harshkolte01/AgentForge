"""Trace actual manual model/tool calls without capturing their content."""
import inspect


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

    async def complete(self, messages, tools, on_text=None, tool_choice=None, on_narration=None, on_retry=None):
        self.store.progress("model.started", {"model": self.model_id})
        with self.telemetry.span("model_request", kind="LLM") as span:
            if span is not None:
                span.set_attribute("task_id", str(self.store.lease.task_id))
                span.set_attribute("llm.model", self.model_id)
            extra = {key: value for key, value in (('on_text', on_text), ('tool_choice', tool_choice),
                                                   ('on_narration', on_narration)) if value is not None}
            if on_retry is not None and "on_retry" in inspect.signature(self.model.complete).parameters:
                extra["on_retry"] = on_retry
            response = await self.model.complete(messages, tools, **extra)
            if span is not None:
                span.set_attribute("gen_ai.usage.input_tokens", response.usage.input_tokens)
                span.set_attribute("gen_ai.usage.output_tokens", response.usage.output_tokens)
                if response.usage.cost_usd is not None:
                    span.set_attribute("cost_usd", float(response.usage.cost_usd))
        self.store.progress("model.finished", {"model": self.model_id,
            "input_tokens": response.usage.input_tokens, "output_tokens": response.usage.output_tokens})
        return response


def observed_tool(tool, telemetry, store):
    """Trace one tool call. The gateway, not this wrapper, reports its start and finish (tools/lifecycle.py)."""
    async def call(arguments, execution):
        with telemetry.span(tool.name, kind="TOOL") as span:
            if span is not None:
                span.set_attribute("task_id", str(store.lease.task_id))
                span.set_attribute("spawn_id", str(execution.agent_run_id))
                span.set_attribute("tool.name", tool.name)
            outcome = await tool.handler(arguments, execution)
            if span is not None:
                span.set_attribute("status", outcome.status)
            return outcome
    return call
