"""Tool lifecycle events (`tool.started` / `tool.finished`), owned by the tool gateway.

The gateway is the one place that knows when a call really entered its adapter and what outcome was durably recorded,
so it reports both; the telemetry wrapper only traces. Payload keys are the allowlist in `repositories/events.py`.
Nothing here carries tool arguments or output text: `detail` comes from `catalog.activity_detail` and the execution
labels are ints and bools copied from an already validated result.
"""

import logging
from collections.abc import Callable, Mapping, Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import select

from agentforge.database.models import TaskEvent, ToolCall
from agentforge.repositories.events import append_event

from .visibility import is_visible_call

log = logging.getLogger(__name__)

# `store.progress` fits: it appends the event in its own fenced transaction.
LifecycleSink = Callable[[str, dict[str, Any]], None]

# Tools whose result says whether code actually passed. "The tool returned" never means "the tests passed".
EXECUTION_TOOLS = frozenset({"bash", "run_tests", "run_sandbox_command", "get_test_results"})

_OUTCOME_BY_STATUS = {"SUCCEEDED": "completed", "FAILED": "failed", "BLOCKED": "blocked", "UNKNOWN": "unknown"}


def legacy_call_id(operation_key: str) -> str:
    """The id older clients match a finish to its start by: the tail of the gateway's operation key."""
    return operation_key[-100:]


def outcome_from_row(status: str, error_code: str | None) -> str:
    """The lifecycle outcome for a durable tool-call status. Cancellation wins over the status it was stored as."""
    if error_code == "USER_CANCELLED" and status != "UNKNOWN":
        return "cancelled"
    return _OUTCOME_BY_STATUS[status]


def execution_labels(tool: str, data: Mapping[str, Any] | None) -> dict[str, Any]:
    """Exit code, timeout and qualification of an execution tool, as ints and bools only (never output text)."""
    if tool not in EXECUTION_TOOLS or not isinstance(data, Mapping):
        return {}
    labels: dict[str, Any] = {}
    exit_code = data.get("exit_code")
    if isinstance(exit_code, int) and not isinstance(exit_code, bool):
        labels["exit_code"] = exit_code
    for key in ("timed_out", "qualified"):
        if isinstance(data.get(key), bool):
            labels[key] = data[key]
    return labels


def finished_payload(*, tool: str, outcome: str, call_id: str, tool_call_id: str, agent_run_id: str,
                     evidence_ids: Sequence[str] = (), artifact_ids: Sequence[str] = (),
                     error_code: str | None = None, duration_ms: int | None = None,
                     labels: Mapping[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"tool": tool, "status": "ok" if outcome == "completed" else "error", "outcome": outcome,
        "evidence_ids": list(evidence_ids), "artifact_ids": list(artifact_ids),
        "call_id": call_id, "tool_call_id": tool_call_id, "agent_run_id": agent_run_id}
    if error_code:
        payload["error_code"] = error_code[:64]
    if duration_ms is not None:
        payload["duration_ms"] = max(0, duration_ms)
    payload.update(labels or {})
    return payload


class CallLifecycle:
    """Start and finish events of one gateway call. Best effort: a lost event never changes the call's result,
    because the durable `tool_calls` row is the record and termination reconciles any start left open."""

    def __init__(self, sink: LifecycleSink | None, *, tool: str, tool_call_id: UUID, agent_run_id: UUID,
                 operation_key: str) -> None:
        self.sink, self.tool = sink, tool
        self.ids = {"call_id": legacy_call_id(operation_key), "tool_call_id": str(tool_call_id),
                    "agent_run_id": str(agent_run_id)}
        self.started = self.finished = False
        # Set by the gateway once the arguments are validated; shown on the start event.
        self.detail: str | None = None
        # Further ids the start event carries (a delegation names its child run). Keys are in the events allowlist.
        self.extra: dict[str, str] = {}

    def _send(self, event_type: str, payload: dict[str, Any]) -> bool:
        try:
            self.sink(event_type, payload)
            return True
        except Exception:
            log.warning("tool lifecycle event not recorded: type=%s tool=%s", event_type, self.tool, exc_info=True)
            return False

    def start(self, *, error_code: str | None = None) -> bool:
        if self.sink is None or not is_visible_call(self.tool, error_code):
            return False
        if not self.started:
            self.started = self._send("tool.started", {"tool": self.tool, **({"detail": self.detail} if self.detail else {}), **self.ids, **self.extra})
        return self.started

    def finish(self, outcome: str, *, evidence_ids: Sequence[str] = (), artifact_ids: Sequence[str] = (),
               error_code: str | None = None, duration_ms: int | None = None,
               labels: Mapping[str, Any] | None = None) -> None:
        """Call after the outcome is persisted. A refusal nobody saw start is shown as a start plus a finish."""
        if self.sink is None or self.finished:
            return
        if not self.started and not self.start(error_code=error_code):
            return
        self.finished = self._send("tool.finished", finished_payload(tool=self.tool, outcome=outcome,
            evidence_ids=evidence_ids, artifact_ids=artifact_ids, error_code=error_code,
            duration_ms=duration_ms, labels=labels, **self.ids))


def reconcile_open_calls(session, task, *, final_status: str, error_code: str | None) -> int:
    """Finish every `tool.started` that never got its `tool.finished`, from the durable `tool_calls` rows.

    The caller holds the task row lock and appends `task.finished` right after, so these land before it and a
    reader that reaches the terminal sequence has seen every call settled. Idempotent: a settled start is skipped.
    A row still at INTENT is never reported as a success: the task was cancelled, or the effect is unknown. The row
    itself is left alone, because an adapter that is somehow still running must be able to record its own outcome.
    Legacy starts (no `tool_call_id`) cannot be matched safely and are ignored.
    """
    events = session.execute(select(TaskEvent.event_type, TaskEvent.payload).where(
        TaskEvent.task_id == task.id, TaskEvent.event_type.in_(("tool.started", "tool.finished")))
        .order_by(TaskEvent.sequence)).all()
    open_calls: dict[str, dict[str, Any]] = {}
    for event_type, payload in events:
        key = payload.get("tool_call_id")
        if not isinstance(key, str):
            continue
        if event_type == "tool.started":
            open_calls.setdefault(key, payload)
        else:
            open_calls.pop(key, None)
    if not open_calls:
        return 0
    ids = []
    for key in open_calls:
        try:
            ids.append(UUID(key))
        except ValueError:
            continue
    rows = {str(row.id): row for row in session.scalars(select(ToolCall).where(
        ToolCall.task_id == task.id, ToolCall.id.in_(ids)))} if ids else {}
    cancelled = final_status == "CANCELLED" or error_code == "USER_CANCELLED"
    for key, started in open_calls.items():
        row = rows.get(key)
        result = (row.result_json if row is not None else None) or {}
        if row is not None and row.status != "INTENT":
            outcome, code = outcome_from_row(row.status, row.error_code), row.error_code
        else:
            outcome, code = ("cancelled", "USER_CANCELLED") if cancelled else ("unknown", "INTERRUPTED")
        artifact_ids = [item for item in result.get("artifact_ids", []) if isinstance(item, str)]
        duration = result.get("duration_ms")
        append_event(session, task, "tool.finished", finished_payload(
            tool=started.get("tool") or (row.tool_name if row is not None else "unknown"), outcome=outcome,
            call_id=started.get("call_id") or "", tool_call_id=key,
            agent_run_id=started.get("agent_run_id") or (str(row.agent_run_id) if row is not None else ""),
            evidence_ids=list(row.evidence_ids or []) if row is not None else [], artifact_ids=artifact_ids,
            error_code=code, duration_ms=duration if isinstance(duration, int) else None,
            labels={**execution_labels(started.get("tool") or "", result.get("execution")),
                **({"parent_run_id": started["parent_run_id"]} if started.get("parent_run_id") else {})}))
    return len(open_calls)
