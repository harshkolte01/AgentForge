"""Trace actual manual model/tool calls without capturing their content."""
from agentforge.tools.catalog import activity_detail


class ObservedModel:
    def __init__(self, model, telemetry, store):
        self.model, self.telemetry, self.store = model, telemetry, store
        self.model_id = model.model_id
        # Harness hints some models expose; absent on fixture models.
        self.context_budget_bytes = getattr(model, 'context_budget_bytes', None)
        self.strict_tools = bool(getattr(model, 'strict_tools', False))

    def maximum_request_cost(self, messages, tools):
        return self.model.maximum_request_cost(messages, tools)

    async def complete(self, messages, tools, on_text=None, tool_choice=None):
        self.store.progress("model.started", {"model": self.model_id})
        with self.telemetry.span("model_request", kind="LLM") as span:
            if span is not None:
                span.set_attribute("task_id", str(self.store.lease.task_id))
                span.set_attribute("llm.model", self.model_id)
            extra = {key: value for key, value in (('on_text', on_text), ('tool_choice', tool_choice)) if value is not None}
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
    async def call(arguments, execution):
        detail = activity_detail(tool.name, arguments)
        store.progress("tool.started", {"tool": tool.name, **({"detail": detail} if detail else {})})
        with telemetry.span(tool.name, kind="TOOL") as span:
            if span is not None:
                span.set_attribute("task_id", str(store.lease.task_id))
                span.set_attribute("spawn_id", str(execution.agent_run_id))
                span.set_attribute("tool.name", tool.name)
            try:
                outcome = await tool.handler(arguments, execution)
            except Exception:
                # A typed rejection (missing file, ambiguous edit) is still a finished step for the timeline.
                store.progress("tool.finished", {"tool": tool.name, "status": "error", "evidence_ids": [], "artifact_ids": []})
                raise
            if span is not None:
                span.set_attribute("status", outcome.status)
        store.progress("tool.finished", {"tool": tool.name, "status": outcome.status,
            "evidence_ids": list(outcome.evidence_ids), "artifact_ids": list(outcome.artifact_ids)})
        return outcome
    return call
