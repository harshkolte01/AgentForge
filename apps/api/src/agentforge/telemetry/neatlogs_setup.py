"""One SDK generation per worker, graph-bound callbacks and export allowlisting.

Source: https://docs.neatlogs.com/sdk/python (2026-10-10).
The global mask is required: disabling custom decorator capture alone does not
protect LangGraph callbacks from recording state or provider messages.
"""
from contextlib import nullcontext
from typing import Any

_STRUCTURE = {"signal", "trace_id", "span_id", "parent_span_id", "parent_id", "name", "kind", "span_kind",
    "start_time", "end_time", "start_time_unix_nano", "end_time_unix_nano", "duration_ms",
    "status_code", "status", "workflow_name"}
_ATTRIBUTES = {"task_id", "spawn_id", "tool.name", "llm.model", "gen_ai.request.model",
    "gen_ai.usage.input_tokens", "gen_ai.usage.output_tokens", "input_tokens", "output_tokens",
    "cost_usd", "status", "lease_generation", "agent.type", "definition.version",
    "neatlogs.span.kind", "openinference.span.kind", "neatlogs.workflow.name",
    "neatlogs.internal", "neatlogs.auto_root", "neatlogs.trace.complete",
    "neatlogs.trace.interrupted", "neatlogs.trace.termination.reason"}


def metadata_only(span: dict[str, Any]) -> dict[str, Any]:
    """Drop prompts, answers, URLs, exceptions, events, source content and logs."""
    sanitized = {key: value for key, value in span.items() if key in _STRUCTURE
                 and isinstance(value, (str, int, float, bool, type(None)))}
    attributes = span.get("attributes", {})
    sanitized["attributes"] = {key: value for key, value in attributes.items()
        if key in _ATTRIBUTES and isinstance(value, (str, int, float, bool, type(None)))} if isinstance(attributes, dict) else {}
    # SDK1.4.26 _masked_span retains original error status when status is absent.
    # Replace its description explicitly; context/parent/timing are copied by SDK.
    status = span.get("status", {})
    code = status.get("code") if isinstance(status, dict) else None
    sanitized["status"] = {"code": code if code in {"OK", "ERROR", "UNSET"} else "UNSET", "description": None}
    resource = span.get("resource", {})
    resource_attrs = resource.get("attributes", {}) if isinstance(resource, dict) else {}
    sanitized["resource"] = {"attributes": {key: value for key, value in resource_attrs.items()
        if key in {"service.name", "service.version", "neatlogs.workflow_name"}
        and isinstance(value, (str, int, float, bool))}} if isinstance(resource_attrs, dict) else {"attributes": {}}
    sanitized["events"], sanitized["links"] = [], []
    return sanitized


class Telemetry:
    def __init__(self, settings):
        self.sdk = None
        self.handler = None
        if settings.neatlogs_api_key is not None:
            import neatlogs
            neatlogs.init(api_key=settings.neatlogs_api_key.get_secret_value(),
                workflow_name=settings.neatlogs_workflow_name, capture_logs=False,
                mask=metadata_only, register_shutdown_handlers=False)
            self.sdk = neatlogs
            self.handler = neatlogs.langchain_handler()

    def callbacks(self):
        return [self.handler] if self.handler is not None else []

    def span(self, name: str, *, kind: str = "CHAIN"):
        return self.sdk.trace(name, kind=kind) if self.sdk else nullcontext(None)

    def decorate(self, function, *, kind: str, name: str):
        if self.sdk is None:
            return function
        return self.sdk.span(kind=kind, name=name, capture_input=False, capture_output=False)(function)

    def shutdown(self):
        if self.sdk:
            self.sdk.flush()
            self.sdk.shutdown()
