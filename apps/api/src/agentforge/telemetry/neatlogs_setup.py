"""One SDK generation per worker, graph-bound callbacks, export masking and error-aware spans.

Source: https://docs.neatlogs.com/sdk/python and the published 1.4.26 wheel (checked 2026-10-12).

What leaves the process is decided by the global `mask`, which the SDK runs on every span after it has normalised the
attributes (so the mask sees `neatlogs.llm.token_count.*`, not the `gen_ai.*` names a caller set). It is required either way:
disabling custom decorator capture alone does not stop LangGraph callbacks from recording state or provider messages.

* metadata (default): structure, timing, tokens, cost, model, session, status and our own `agentforge.*` labels. No prompt,
  answer, tool argument or result, URL, exception text, event or log ever leaves.
* content (`NEATLOGS_CAPTURE_CONTENT=true`): the same, plus input/output values, with known secret shapes and the
  configured secrets masked and long values cut. A private conversation then reaches Neatlogs; this is the operator's choice.

Tracing is never allowed to stop a run: a failing SDK is logged and the run goes on untraced.
"""
import asyncio
import contextvars
import inspect
import logging
import re
import sys
from contextlib import contextmanager, nullcontext
from decimal import Decimal
from functools import wraps
from typing import Any, Mapping

logger = logging.getLogger(__name__)

_SCALAR = (str, int, float, bool, type(None))
_FLUSH_MS = 10_000      # the SDK documents 10 s for both; a longer wait would only delay stopping a worker
MAX_VALUE_CHARS = 6000  # content mode: one attribute value is cut here
MAX_TAGS = 10
SERVICE_IDENTITY = "agentforge-worker"

_STRUCTURE = {"signal", "trace_id", "span_id", "parent_span_id", "parent_id", "name", "kind", "span_kind",
    "start_time", "end_time", "start_time_unix_nano", "end_time_unix_nano", "duration_ms",
    "status_code", "status", "workflow_name"}
# Attribute names as the SDK exports them. Anything not listed (or under `agentforge.`) is dropped in metadata mode.
_ATTRIBUTES = {"task_id", "spawn_id", "tool.name", "llm.model", "gen_ai.request.model", "agent.type", "definition.version",
    "gen_ai.usage.input_tokens", "gen_ai.usage.output_tokens", "input_tokens", "output_tokens", "cost_usd", "status",
    "lease_generation", "user.id",
    "neatlogs.span.kind", "openinference.span.kind", "neatlogs.workflow.name", "neatlogs.workflow_name",
    "neatlogs.internal", "neatlogs.auto_root", "neatlogs.trace.complete", "neatlogs.trace.interrupted",
    "neatlogs.trace.termination.reason", "neatlogs.instrumentation.name", "neatlogs.metrics.duration_ms",
    "neatlogs.session.id", "neatlogs.end_user.id", "neatlogs.user.id", "neatlogs.tags", "neatlogs.task.id",
    "neatlogs.tool.name", "neatlogs.agent.name", "neatlogs.langgraph.node",
    "neatlogs.llm.model_name", "neatlogs.llm.provider", "neatlogs.llm.system", "neatlogs.llm.cost_usd",
    "neatlogs.llm.token_count.prompt", "neatlogs.llm.token_count.completion", "neatlogs.llm.token_count.total",
    "neatlogs.llm.token_count.reasoning", "neatlogs.llm.token_count.cache_read", "neatlogs.llm.token_count.cache_write",
    "neatlogs.guardrail.name", "neatlogs.guardrail.passed", "neatlogs.guardrail.reason", "neatlogs.guardrail.action",
    "neatlogs.guardrail.triggered"}
_ATTRIBUTE_PATTERNS = (re.compile(r"^neatlogs\.llm\.choices\.\d+\.finish_reason$"),)
_OWN_PREFIX = "agentforge."
_RESOURCE = {"service.name", "service.version", "service.instance.id", "neatlogs.workflow_name", "neatlogs.tags", "user.id"}
# An exception event keeps what kind of failure it was, never its text or stack (which can hold paths, URLs and content).
_EXCEPTION_EVENT = {"exception.type", "exception.escaped"}


def _allowed(key: str) -> bool:
    return key in _ATTRIBUTES or key.startswith(_OWN_PREFIX) or any(pattern.match(key) for pattern in _ATTRIBUTE_PATTERNS)


def _status(span: Mapping[str, Any]) -> dict[str, Any]:
    # SDK 1.4.26 `_masked_span` keeps the original error description when none is given, so it is replaced explicitly.
    status = span.get("status", {})
    code = status.get("code") if isinstance(status, Mapping) else None
    return {"code": code if code in {"OK", "ERROR", "UNSET"} else "UNSET", "description": None}


def _resource(span: Mapping[str, Any]) -> dict[str, Any]:
    resource = span.get("resource", {})
    attributes = resource.get("attributes", {}) if isinstance(resource, Mapping) else {}
    if not isinstance(attributes, Mapping):
        return {"attributes": {}}
    return {"attributes": {key: value for key, value in attributes.items()
        if key in _RESOURCE and isinstance(value, (str, int, float, bool))}}


def _structure(span: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in span.items() if key in _STRUCTURE and isinstance(value, _SCALAR)}


def _error_code(span: Mapping[str, Any]) -> str | None:
    attributes = span.get("attributes")
    code = attributes.get("agentforge.error.code") if isinstance(attributes, Mapping) else None
    return code if isinstance(code, str) and code else None


def _exception_events(span: Mapping[str, Any], keep: frozenset = frozenset(_EXCEPTION_EVENT), clean=lambda value: value) -> list[dict[str, Any]]:
    """Exception events reduced to `keep`. The failure's own code (a tool's FILE_NOT_FOUND, a provider's RATE_LIMIT) is shown as
    the exception type, so a dashboard names the failure instead of a Python class path."""
    events = span.get("events", [])
    code = _error_code(span)
    kept = []
    for event in events if isinstance(events, list) else []:
        if isinstance(event, Mapping) and event.get("name") == "exception":
            attributes = event.get("attributes", {})
            attributes = {key: clean(value) for key, value in attributes.items()
                if key in keep and isinstance(value, _SCALAR)} if isinstance(attributes, Mapping) else {}
            if code:
                attributes["exception.type"] = code
            kept.append({"name": "exception", "timestamp": event.get("timestamp"), "attributes": attributes})
    return kept


def metadata_only(span: dict[str, Any]) -> dict[str, Any]:
    """Keep structure, timing, usage, status and our own labels; drop prompts, answers, URLs, exception text and logs."""
    sanitized = _structure(span)
    attributes = span.get("attributes", {})
    sanitized["attributes"] = {key: value for key, value in attributes.items()
        if _allowed(key) and isinstance(value, _SCALAR)} if isinstance(attributes, Mapping) else {}
    sanitized["status"] = _status(span)
    sanitized["resource"] = _resource(span)
    sanitized["events"], sanitized["links"] = _exception_events(span), []
    return sanitized


# --- content mode -------------------------------------------------------------------------------------------------------
_SECRET_SHAPES = (
    re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|$)"),
    re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    re.compile(r"\btvly-[A-Za-z0-9_\-]{10,}"),
    re.compile(r"\be2b_[A-Za-z0-9]{16,}"),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"),
    re.compile(r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=\-]{12,}"),
)
_KEYED_SECRET = re.compile(r"(?i)\b(api[_-]?key|secret|token|passw(?:or)?d|authorization|credentials?)(\"?\s*[:=]\s*\"?)[^\s\"',;]{6,}")
_URL_CREDENTIALS = re.compile(r"\b([a-z][a-z0-9+.\-]*://)[^\s:/@]+:[^\s@/]+@")
REDACTED = "[redacted]"


def redact(text: str, secrets: tuple[str, ...] = ()) -> str:
    """Best effort: known secret shapes and the configured secret values. It lowers the risk of content mode; it does not
    make private text safe to share."""
    for secret in secrets:
        text = text.replace(secret, REDACTED)
    for shape in _SECRET_SHAPES:
        text = shape.sub(REDACTED, text)
    text = _URL_CREDENTIALS.sub(lambda match: match.group(1) + REDACTED + "@", text)
    return _KEYED_SECRET.sub(lambda match: match.group(1) + match.group(2) + REDACTED, text)


def content_mask(secrets: tuple[str, ...] = ()):
    """The mask for content mode: everything the metadata mask keeps, plus attribute text with secrets masked and cut."""
    secrets = tuple(sorted({value for value in secrets if value and len(value) >= 8}, key=len, reverse=True))

    def clean(value):
        if not isinstance(value, str):
            return value
        # Bounded before the patterns run (graph state can be megabytes and the SDK gives a mask seconds), cut after.
        cut = len(value) > MAX_VALUE_CHARS
        value = redact(value[:MAX_VALUE_CHARS * 2], secrets)
        return value[:MAX_VALUE_CHARS] + " …[truncated]" if cut or len(value) > MAX_VALUE_CHARS else value

    def mask(span: dict[str, Any]) -> dict[str, Any]:
        sanitized = _structure(span)
        attributes = span.get("attributes", {})
        sanitized["attributes"] = {key: clean(value) for key, value in attributes.items()
            if isinstance(value, _SCALAR)} if isinstance(attributes, Mapping) else {}
        sanitized["status"] = _status(span)
        sanitized["resource"] = _resource(span)
        sanitized["events"] = _exception_events(span, frozenset(_EXCEPTION_EVENT | {"exception.message"}), clean)
        sanitized["links"] = []
        return sanitized
    return mask


def configured_secrets(settings) -> tuple[str, ...]:
    """Every secret value in the settings, so content mode can mask them wherever they appear."""
    from pydantic import SecretStr
    values = []
    for name in type(settings).model_fields:
        value = getattr(settings, name, None)
        if isinstance(value, SecretStr) and value.get_secret_value():
            values.append(value.get_secret_value())
    return tuple(values)


# --- spans --------------------------------------------------------------------------------------------------------------
def span_value(value):
    """An attribute value OpenTelemetry accepts, or None (skipped)."""
    if value is None:
        return None
    if isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, Decimal):
        return float(value)
    return str(value)


def set_attributes(span, attributes: Mapping[str, Any] | None) -> None:
    """Best effort: telemetry never changes what a run does."""
    if span is None or not attributes:
        return
    for key, value in attributes.items():
        value = span_value(value)
        if value is None:
            continue
        try:
            span.set_attribute(key, value)
        except Exception:  # noqa: BLE001
            logger.debug("span attribute not set: %s", key, exc_info=True)


def mark_error(span, error: BaseException | None = None, *, code: str | None = None) -> None:
    """Marks a span failed: status ERROR, the kind of error, and the code if the error (or caller) has one. The SDK's
    `trace()` does not record exceptions itself, so without this a failed step would look successful."""
    if span is None:
        return
    attributes: dict[str, Any] = {}
    if error is not None:
        attributes["agentforge.error.type"] = type(error).__name__
        code = code or getattr(error, "code", None)
        if isinstance(error, asyncio.CancelledError):
            attributes["agentforge.cancelled"] = True
    if isinstance(code, str) and code:
        attributes["agentforge.error.code"] = code[:64]
    set_attributes(span, attributes)
    try:
        from opentelemetry.trace import Status, StatusCode
        span.set_status(Status(StatusCode.ERROR))
        if error is not None:
            span.record_exception(error)
        elif isinstance(code, str) and code:
            # A tool that returned an error result raised nothing; the event is what gives the backend an error type.
            span.add_event("exception", {"exception.type": code[:64], "exception.escaped": False})
    except Exception:  # noqa: BLE001
        logger.debug("span error not recorded", exc_info=True)


def _settle(span) -> None:
    """A step that raised nothing and was not marked failed is OK."""
    try:
        from opentelemetry.trace import Status, StatusCode
        current = getattr(span, "status", None)
        if current is None or current.status_code is StatusCode.UNSET:
            span.set_status(Status(StatusCode.OK))
    except Exception:  # noqa: BLE001
        pass


_current: contextvars.ContextVar = contextvars.ContextVar("agentforge_current_span", default=None)
_active: "Telemetry | None" = None


def annotate(attributes: Mapping[str, Any] | None = None, *, failed: str | bool | None = None) -> None:
    """Sets attributes on the innermost span a `Telemetry` opened in this context (a no-op without one). `failed` also marks
    it ERROR; a string is its error code. Used where the outcome is only known late, such as a task's final status."""
    span = _current.get()
    set_attributes(span, attributes)
    if failed:
        mark_error(span, code=failed if isinstance(failed, str) else None)


def current_trace_id() -> str | None:
    """The 32-hex id of the trace being recorded, which is what finds it in the Neatlogs dashboard."""
    span = _current.get()
    try:
        return format(span.get_span_context().trace_id, "032x") if span is not None else None
    except Exception:  # noqa: BLE001
        return None


def stage(name: str, *, kind: str = "CHAIN", attributes: Mapping[str, Any] | None = None):
    """A child span for one phase of a run, on whichever `Telemetry` is live in this process (a no-op without one)."""
    return _active.span(name, kind=kind, attributes=attributes) if _active is not None else nullcontext(None)


def parse_tags(raw: str | None) -> list[str]:
    tags = []
    for item in (raw or "").split(","):
        item = item.strip()[:64]
        if item and item not in tags:
            tags.append(item)
    return tags[:MAX_TAGS]


def release_tag() -> str | None:
    from importlib.metadata import PackageNotFoundError, version
    try:
        return f"release:{version('agentforge-api')}"
    except PackageNotFoundError:
        return None


def init_options(settings) -> dict[str, Any]:
    """The `neatlogs.init` arguments for these settings. Pure, so it can be checked without a key or a network."""
    capture_content = bool(getattr(settings, "neatlogs_capture_content", False))
    tags = [f"env:{settings.app_env}"]
    if (release := release_tag()):
        tags.append(release)
    if getattr(settings, "llm_model", None):
        tags.append(f"model:{settings.llm_model}".replace(",", "_")[:64])
    tags += [tag for tag in parse_tags(getattr(settings, "neatlogs_tags", "")) if tag not in tags]
    options: dict[str, Any] = {
        "api_key": settings.neatlogs_api_key.get_secret_value(),
        "workflow_name": settings.neatlogs_workflow_name,
        "tags": tags,
        "user_id": SERVICE_IDENTITY,
        "sample_rate": float(getattr(settings, "neatlogs_sample_rate", 1.0)),
        "capture_logs": False,
        "mask": content_mask(configured_secrets(settings)) if capture_content else metadata_only,
        # The worker's own signal handling drains tasks first and then calls shutdown() once.
        "register_shutdown_handlers": False,
    }
    endpoint = getattr(settings, "neatlogs_endpoint", None)
    if endpoint is not None:
        options["endpoint"] = str(endpoint).rstrip("/")
    return options


class Telemetry:
    def __init__(self, settings):
        global _active
        self.sdk = None
        self.handler = None
        self.capture_content = False
        if settings.neatlogs_api_key is None:
            return
        sdk = None
        try:
            import neatlogs
            sdk = neatlogs
            neatlogs.init(**init_options(settings))
            self.handler = neatlogs.langchain_handler()
            self.sdk = neatlogs
            self.capture_content = bool(getattr(settings, "neatlogs_capture_content", False))
        except Exception:  # noqa: BLE001
            # A tracing fault must not stop the worker: the runs go on untraced and the operator is told why.
            logger.error("Neatlogs tracing is off: the SDK could not be started. Check NEATLOGS_API_KEY, NEATLOGS_ENDPOINT "
                         "and that `neatlogs` is installed.", exc_info=True)
            self.sdk = self.handler = None
            if sdk is not None:
                try:
                    sdk.shutdown(timeout_millis=1000)   # init may have got as far as starting its exporter
                except Exception:  # noqa: BLE001
                    pass
            return
        _active = self
        if self.capture_content:
            logger.warning("NEATLOGS_CAPTURE_CONTENT is on: prompts, answers and tool data are sent to Neatlogs (secrets masked).")

    @property
    def enabled(self) -> bool:
        return self.sdk is not None

    def callbacks(self):
        return [self.handler] if self.handler is not None else []

    @contextmanager
    def _trace(self, name: str, kind: str, identity: dict[str, str]):
        """`neatlogs.trace`, yielding None when the SDK cannot open the span (the run then simply goes untraced)."""
        try:
            manager = self.sdk.trace(name, kind=kind, **identity)
            active = manager.__enter__()
        except Exception:  # noqa: BLE001
            logger.warning("Neatlogs span not opened: %s", name, exc_info=True)
            yield None
            return
        try:
            yield active
        except BaseException:
            try:
                manager.__exit__(*sys.exc_info())
            except Exception:  # noqa: BLE001
                logger.warning("Neatlogs span not closed cleanly: %s", name, exc_info=True)
            raise
        else:
            try:
                manager.__exit__(None, None, None)
            except Exception:  # noqa: BLE001
                logger.warning("Neatlogs span not closed cleanly: %s", name, exc_info=True)

    @contextmanager
    def span(self, name: str, *, kind: str = "CHAIN", session_id=None, end_user_id=None,
             attributes: Mapping[str, Any] | None = None):
        """One span. Yields the SDK span (None when tracing is off). An exception marks it failed and propagates unchanged;
        `session_id`/`end_user_id` group a trace root into its conversation (see docs/neatlogs.md)."""
        if self.sdk is None:
            yield None
            return
        identity = {key: str(value) for key, value in (("session_id", session_id), ("end_user_id", end_user_id)) if value}
        with self._trace(name, kind, identity) as active:
            token = _current.set(active) if active is not None else None
            try:
                set_attributes(active, attributes)
                yield active
                if active is not None:
                    _settle(active)
            except BaseException as error:
                mark_error(active, error)
                raise
            finally:
                if token is not None:
                    try:
                        _current.reset(token)
                    except ValueError:   # closed from another context than it was opened in; the value just stays until then
                        pass

    def decorate(self, function, *, kind: str, name: str, session_id=None, end_user_id=None,
                 attributes: Mapping[str, Any] | None = None):
        """`function` run inside a span of this kind and name. Its arguments and result are never captured."""
        if self.sdk is None:
            return function
        if inspect.iscoroutinefunction(function):
            @wraps(function)
            async def traced(*args, **kwargs):
                with self.span(name, kind=kind, session_id=session_id, end_user_id=end_user_id, attributes=attributes):
                    return await function(*args, **kwargs)
        else:
            @wraps(function)
            def traced(*args, **kwargs):
                with self.span(name, kind=kind, session_id=session_id, end_user_id=end_user_id, attributes=attributes):
                    return function(*args, **kwargs)
        return traced

    def shutdown(self):
        """Once, after the last task: drains what is queued, then closes the SDK. Bounded, and never raises."""
        global _active
        if _active is self:
            _active = None
        if self.sdk is None:
            return
        for step in (self.sdk.flush, self.sdk.shutdown):
            try:
                if step(timeout_millis=_FLUSH_MS) is False:
                    logger.warning("Neatlogs %s did not finish within %ss; the last traces may be lost.", step.__name__, _FLUSH_MS // 1000)
            except Exception:  # noqa: BLE001
                logger.warning("Neatlogs %s failed; the last traces may be lost.", step.__name__, exc_info=True)
