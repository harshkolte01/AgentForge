"""Exact, batched activity reads: per-message summaries, attachment names and inspectable tool call details.

Everything here is a grouped query over task ids (never one query per task), so a 100 message page costs a handful of
statements however many calls the tasks made. Counts use the same visibility rules as the live timeline
(`tools/visibility.py`); details are allowlisted fields and bounded previews of evidence and artifacts the task owns.
Raw arguments, intent records and operation keys are never read into a response.
"""
import json
import re
from collections.abc import Callable, Iterable
from typing import Any
from uuid import UUID

from sqlalchemy import and_, bindparam, func, or_, select, text
from sqlalchemy.orm import Session

from agentforge.contracts.activity import (
    ActivitySummary, AttachmentView, MessageActivity, ToolCallDetail, ToolDetailArtifact, ToolDetailExecution,
    ToolDetailFile, ToolDetailText)
from agentforge.contracts.tasks import TERMINAL_STATUSES
from agentforge.database.coding import CodingEvidence
from agentforge.database.models import Project, Task, TaskEvent, ToolCall
from agentforge.database.research_models import ResearchEvidence, ResearchSource
from agentforge.database.sessions import ToolApproval, ToolResultSnapshot
from agentforge.integrations.artifact_storage import ArtifactStorage
from agentforge.repositories.coding import CodingRepository
from agentforge.repositories.research import ResearchRepository
from agentforge.subagents.scope import ScopeDenied
from agentforge.tools.lifecycle import EXECUTION_TOOLS, outcome_from_row
from agentforge.tools.visibility import HIDDEN_TOOLS, SELF_CORRECTION_CODES, is_visible_call

PREVIEW_BYTES = 24 * 1024
# The list endpoints cap what they return; the counts that gate fetching them must not promise more.
SOURCE_LIST_LIMIT, ARTIFACT_LIST_LIMIT = 1000, 100
MAX_DETAIL_FILES = 200
ARTIFACT_READ_BYTES = 2_000_000

TOOL_DETAIL_KIND = {"bash": "execution", "run_tests": "execution", "run_sandbox_command": "execution", "get_test_results": "execution",
    "git_diff": "diff", "create_task_artifact": "artifact", "read_task_artifact": "artifact"}
assert {name for name, kind in TOOL_DETAIL_KIND.items() if kind == "execution"} == EXECUTION_TOOLS

_ERROR_CODE = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
_HUNK = re.compile(r"^@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@")


class ToolCallNotFound(LookupError):
    """The call does not exist in this task (including one that belongs to another task)."""


class ToolDetailUnavailable(LookupError):
    """The call exists but its tool has no inspectable detail (or is not shown as activity at all)."""


def _visible():
    """SQL form of `is_visible_call`."""
    return and_(ToolCall.tool_name.notin_(HIDDEN_TOOLS),
        or_(ToolCall.error_code.is_(None), ToolCall.error_code.notin_(SELF_CORRECTION_CODES)))


def action_counts(session: Session, task_ids: Iterable[UUID]) -> dict[UUID, int]:
    """Exact visible actions per task. Finished calls, plus calls still at INTENT: a running call is activity, and one
    left at INTENT by a task that already ended is shown as interrupted (never dropped from the count)."""
    ids = list(task_ids)
    if not ids:
        return {}
    return dict(session.execute(select(ToolCall.task_id, func.count()).where(
        ToolCall.task_id.in_(ids), _visible()).group_by(ToolCall.task_id)).all())


def _grouped(session: Session, statement) -> dict[UUID, Any]:
    return {key: value for key, value in session.execute(statement).all()}


def activity_for_tasks(session: Session, task_ids: Iterable[UUID]) -> dict[UUID, MessageActivity]:
    """MessageActivity per existing task id, in a fixed number of grouped statements."""
    ids = list(dict.fromkeys(task_ids))
    if not ids:
        return {}
    tasks = session.execute(select(Task.id, Task.status, Task.error_code, Task.event_sequence, Task.execution_started_at)
        .where(Task.id.in_(ids))).all()
    found = [row.id for row in tasks]
    if not found:
        return {}
    actions = action_counts(session, found)
    notes = _grouped(session, select(TaskEvent.task_id, func.count()).where(
        TaskEvent.task_id.in_(found), TaskEvent.event_type == "assistant.message",
        TaskEvent.payload["text"].astext != "").group_by(TaskEvent.task_id))
    pending = set(session.scalars(select(ToolApproval.task_id).where(
        ToolApproval.task_id.in_(found), ToolApproval.state == "PENDING").distinct()))
    finished = _grouped(session, select(TaskEvent.task_id, func.max(TaskEvent.created_at)).where(
        TaskEvent.task_id.in_(found), TaskEvent.event_type == "task.finished").group_by(TaskEvent.task_id))
    sources = _grouped(session, select(ResearchSource.task_id, func.count()).where(
        ResearchSource.task_id.in_(found)).group_by(ResearchSource.task_id))
    artifacts = _artifact_counts(session, found)
    result = {}
    for row in tasks:
        action_count, note_count = actions.get(row.id, 0), notes.get(row.id, 0)
        result[row.id] = MessageActivity(
            action_count=action_count, note_count=note_count, available=action_count > 0 or note_count > 0,
            event_watermark=row.event_sequence, terminal=row.status in TERMINAL_STATUSES, status=row.status,
            error_code=row.error_code, approval_pending=row.id in pending,
            duration_ms=(max(0, int((finished[row.id] - row.execution_started_at).total_seconds() * 1000))
                if row.id in finished and row.execution_started_at is not None else None),
            source_count=min(sources.get(row.id, 0), SOURCE_LIST_LIMIT),
            artifact_count=min(artifacts.get(row.id, 0), ARTIFACT_LIST_LIMIT))
    return result


def _artifact_counts(session: Session, ids: list[UUID]) -> dict[UUID, int]:
    from agentforge.database.coding import CodingArtifact
    from agentforge.database.research_models import ResearchArtifact
    counts: dict[UUID, int] = {}
    for model in (ResearchArtifact, CodingArtifact):
        for task_id, count in session.execute(select(model.task_id, func.count()).where(
                model.task_id.in_(ids)).group_by(model.task_id)).all():
            counts[task_id] = counts.get(task_id, 0) + count
    return counts


def terminal_sequences(session: Session, task_ids: Iterable[UUID]) -> dict[UUID, int]:
    ids = list(task_ids)
    if not ids:
        return {}
    return _grouped(session, select(TaskEvent.task_id, func.max(TaskEvent.sequence)).where(
        TaskEvent.task_id.in_(ids), TaskEvent.event_type == "task.finished").group_by(TaskEvent.task_id))


_ATTACHMENTS = text("""
SELECT t.id AS task_id, left(a.value->>'name', 120) AS name, char_length(a.value->>'content') AS characters
FROM tasks t
CROSS JOIN LATERAL jsonb_array_elements(CASE WHEN jsonb_typeof(t.permissions_json->'attachments') = 'array'
    THEN t.permissions_json->'attachments' ELSE '[]'::jsonb END) WITH ORDINALITY AS a(value, ord)
WHERE t.id IN :ids AND jsonb_typeof(a.value) = 'object'
ORDER BY t.id, a.ord
""").bindparams(bindparam("ids", expanding=True))


def attachments_for_tasks(session: Session, task_ids: Iterable[UUID]) -> dict[UUID, list[AttachmentView]]:
    """Attachment names and sizes of each task's turn, never their content (the content column is only measured)."""
    ids = list(dict.fromkeys(task_ids))
    result: dict[UUID, list[AttachmentView]] = {}
    if not ids:
        return result
    for row in session.execute(_ATTACHMENTS, {"ids": ids}):
        if row.name:
            result.setdefault(row.task_id, []).append(AttachmentView(name=row.name, characters=row.characters))
    return result


def _clip(text_value: Any, limit: int = PREVIEW_BYTES) -> ToolDetailText | None:
    """A bounded preview: `bytes` is the full size, `truncated` says the text is a prefix of it."""
    if not isinstance(text_value, str):
        return None
    data = text_value.encode("utf-8")
    if len(data) <= limit:
        return ToolDetailText(text=text_value, truncated=False, bytes=len(data))
    return ToolDetailText(text=data[:limit].decode("utf-8", errors="ignore"), truncated=True, bytes=len(data))


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _signed_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _uuids(values: Any) -> list[UUID]:
    """Ids recorded in JSON are references to be checked, never trusted: anything malformed is dropped."""
    found: list[UUID] = []
    for value in values if isinstance(values, list) else []:
        try:
            parsed = UUID(str(value))
        except (ValueError, TypeError, AttributeError):
            continue
        if str(parsed) == str(value) and parsed not in found:
            found.append(parsed)
    return found


def _diff_path(raw: str) -> str:
    """The path of a `--- a/x` / `+++ b/x` / `diff --git` operand (git quotes unusual names as JSON strings)."""
    raw = raw.strip()
    if raw.startswith('"'):
        try:
            raw = json.loads(raw)
        except ValueError:
            pass
    return raw[2:] if raw[:2] in ("a/", "b/") else raw


def diff_files(diff: str) -> list[ToolDetailFile]:
    """Changed files with added and removed line counts, read from a stored unified diff.

    Hunk headers say how many old and new lines follow, so a removed line that itself starts with `--` is counted as
    content rather than mistaken for the next file's header.
    """
    files: list[dict[str, Any]] = []
    lines = diff.splitlines()
    old_left = new_left = 0
    index = 0
    while index < len(lines):
        line = lines[index]
        index += 1
        if old_left or new_left:
            if line.startswith("+"):
                files[-1]["additions"] += 1
                new_left -= 1
            elif line.startswith("-"):
                files[-1]["deletions"] += 1
                old_left -= 1
            elif line.startswith("\\"):
                pass
            else:
                old_left, new_left = old_left - 1, new_left - 1
            continue
        hunk = _HUNK.match(line)
        if hunk and files:
            old_left, new_left = int(hunk.group(1) or 1), int(hunk.group(2) or 1)
        elif line.startswith("--- ") and index < len(lines) and lines[index].startswith("+++ "):
            path = _diff_path(lines[index][4:])
            if path == "/dev/null":
                path = _diff_path(line[4:])
            files.append({"path": path[:500], "additions": 0, "deletions": 0})
            index += 1
        elif line.startswith("diff --git "):
            # Header-only entries: an empty file created or deleted has no hunks to count.
            operands = line[len("diff --git "):]
            half = operands.rfind(' "b/') if operands.startswith('"') else operands.find(" b/")
            files.append({"path": _diff_path(operands[:half] if half > 0 else operands)[:500], "additions": 0, "deletions": 0})
        elif line.startswith("Binary files "):
            match = re.match(r"Binary files a/(.+) and b/.+ differ$", line)
            if match:
                files.append({"path": match.group(1)[:500], "additions": 0, "deletions": 0})
    # An empty-file header and an unified diff never describe the same path twice, so no merging is needed.
    return [ToolDetailFile(**item) for item in files[:MAX_DETAIL_FILES]]


class ActivityRepository:
    def __init__(self, session_factory: Callable[[], Session], storage: ArtifactStorage | None = None) -> None:
        self.session_factory = session_factory
        self.storage = storage

    # -- summaries ---------------------------------------------------------------------------------

    def summary(self, task_id: UUID, owner_id: UUID) -> ActivitySummary:
        from .tasks import TaskNotFound
        with self.session_factory() as session:
            owned = session.scalar(select(Task.id).join(Project, Project.id == Task.project_id).where(
                Task.id == task_id, Task.created_by == owner_id, Project.owner_id == owner_id))
            if owned is None:
                raise TaskNotFound("task_not_found")
            activity = activity_for_tasks(session, [task_id])[task_id]
            terminal = terminal_sequences(session, [task_id]).get(task_id) if activity.terminal else None
            return ActivitySummary(task_id=task_id, terminal_sequence=terminal, **activity.model_dump())

    # -- tool call detail --------------------------------------------------------------------------

    def tool_call_detail(self, *, task_id: UUID, tool_call_id: UUID, owner_id: UUID, project_id: UUID) -> ToolCallDetail:
        with self.session_factory() as session:
            # Ownership is re-checked here (task and project owner) even though the route has already loaded the task.
            task = ResearchRepository._owned(session, task_id=task_id, owner_id=owner_id, project_id=project_id)
            call = session.scalar(select(ToolCall).where(ToolCall.id == tool_call_id, ToolCall.task_id == task.id))
            if call is None:
                raise ToolCallNotFound("tool_call_not_found")
            kind = TOOL_DETAIL_KIND.get(call.tool_name)
            if kind is None or not is_visible_call(call.tool_name, call.error_code):
                raise ToolDetailUnavailable("tool_detail_unavailable")
            result = call.result_json if isinstance(call.result_json, dict) else {}
            detail: dict[str, Any] = dict(task_id=task.id, tool_call_id=call.id, tool=call.tool_name, kind=kind,
                outcome=self._outcome(call, task), started_at=call.started_at, finished_at=call.finished_at,
                error_code=call.error_code if call.error_code and _ERROR_CODE.fullmatch(call.error_code) else None,
                duration_ms=self._duration(call, result))
            scope = dict(task_id=task.id, owner_id=owner_id, project_id=project_id)
            if kind == "execution":
                detail.update(self._execution(session, call, result, scope))
            elif kind == "diff":
                detail.update(self._diff(session, call, result, scope))
            else:
                detail.update(self._artifact(session, call, result, scope))
            return ToolCallDetail(**detail)

    @staticmethod
    def _outcome(call: ToolCall, task: Task) -> str:
        if call.status != "INTENT":
            return outcome_from_row(call.status, call.error_code)
        if task.status not in TERMINAL_STATUSES:
            return "running"
        # Same rule as termination reconciliation: never a success, cancelled only when the task was.
        return "cancelled" if task.status == "CANCELLED" or task.error_code == "USER_CANCELLED" else "unknown"

    @staticmethod
    def _duration(call: ToolCall, result: dict) -> int | None:
        recorded = _int(result.get("duration_ms"))
        if recorded is not None:
            return recorded
        if call.finished_at is not None and call.started_at is not None:
            return max(0, int((call.finished_at - call.started_at).total_seconds() * 1000))
        return None

    @staticmethod
    def _missing_reason(call: ToolCall, unavailable: str) -> str:
        """Why there is nothing to show for a call: not done yet, nothing recorded, or recorded but now unreadable."""
        if call.status == "INTENT":
            return "not_finished"
        return unavailable if call.status == "SUCCEEDED" or call.evidence_ids else "no_output_recorded"

    def _command(self, session: Session, call: ToolCall) -> dict[str, Any]:
        """The exit code and output of a `bash` call, from the result stored when it ran."""
        content = session.scalar(select(ToolResultSnapshot.content).where(
            ToolResultSnapshot.task_id == call.task_id, ToolResultSnapshot.operation_key == call.operation_key))
        try:
            data = json.loads(content)["data"] if content else None
        except (ValueError, KeyError, TypeError):
            data = None
        if not isinstance(data, dict):
            return dict(unavailable_reason=self._missing_reason(call, "output_unavailable"))
        return dict(execution=ToolDetailExecution(exit_code=_signed_int(data.get("exit_code")), timed_out=_bool(data.get("timed_out")),
            output_truncated=_bool(data.get("truncated"))), stdout=_clip(data.get("output")))

    def _execution(self, session: Session, call: ToolCall, result: dict, scope: dict) -> dict[str, Any]:
        if call.tool_name == "bash":
            return self._command(session, call)
        coding = CodingRepository(self.session_factory)
        ids = _uuids(call.evidence_ids)
        row = session.scalar(select(CodingEvidence).where(
            CodingEvidence.task_id == scope["task_id"], CodingEvidence.id.in_(ids),
            CodingEvidence.kind.in_(("test", "build"))).order_by(CodingEvidence.created_at).limit(1)) if ids else None
        items, reason = self._resolve(coding, _uuids(result.get("artifact_ids")), scope, session)
        detail: dict[str, Any] = dict(artifacts=[self._entry(item, scope) for item in items])
        if row is None:
            detail["unavailable_reason"] = self._missing_reason(call, "evidence_unavailable")
            return detail
        payload = row.evidence_json if isinstance(row.evidence_json, dict) else {}
        profile = payload.get("profile_id")
        detail.update(
            execution=ToolDetailExecution(profile_id=profile[:80] if isinstance(profile, str) else None,
                exit_code=_signed_int(payload.get("exit_code")), timed_out=_bool(payload.get("timed_out")),
                qualified=_bool(payload.get("qualified")), output_truncated=_bool(payload.get("output_truncated")),
                observed_test_count=_int(payload.get("observed_test_count"))),
            # Output comes from the evidence record, never from tool arguments.
            stdout=_clip(payload.get("stdout")), stderr=_clip(payload.get("stderr")), unavailable_reason=reason)
        return detail

    def _diff(self, session: Session, call: ToolCall, result: dict, scope: dict) -> dict[str, Any]:
        coding = CodingRepository(self.session_factory)
        ids = _uuids(call.evidence_ids)
        row = session.scalar(select(CodingEvidence).where(
            CodingEvidence.task_id == scope["task_id"], CodingEvidence.id.in_(ids), CodingEvidence.kind == "diff")
            .order_by(CodingEvidence.created_at).limit(1)) if ids else None
        items, reason = self._resolve(coding, _uuids(result.get("artifact_ids")), scope, session)
        files: list[ToolDetailFile] = []
        diff: ToolDetailText | None = None
        if items:
            data = self._read(items[0])
            if data is None:
                reason = "artifact_unavailable"
            else:
                body = data.decode("utf-8", errors="replace")
                diff, files = _clip(body), diff_files(body)
        if not files and row is not None and isinstance(row.evidence_json, dict):
            # No readable diff body: the changed paths recorded with the evidence still say what was touched.
            paths = row.evidence_json.get("changed_paths")
            files = [ToolDetailFile(path=path[:500]) for path in (paths if isinstance(paths, list) else [])[:MAX_DETAIL_FILES]
                if isinstance(path, str) and path]
        if row is None and not items:
            reason = self._missing_reason(call, "evidence_unavailable")
        return dict(files=files, diff=diff, artifacts=[self._entry(item, scope) for item in items], unavailable_reason=reason)

    def _artifact(self, session: Session, call: ToolCall, result: dict, scope: dict) -> dict[str, Any]:
        research = ResearchRepository(self.session_factory)
        ids = _uuids(result.get("artifact_ids"))
        if not ids and call.evidence_ids:
            # read_task_artifact leaves a read receipt rather than an artifact id; the receipt names what was read.
            for evidence in session.scalars(select(ResearchEvidence).where(
                    ResearchEvidence.task_id == scope["task_id"], ResearchEvidence.id.in_(_uuids(call.evidence_ids)),
                    ResearchEvidence.kind == "document_read")):
                resource = (evidence.evidence_json or {}).get("resource")
                if isinstance(resource, str) and resource.startswith("artifact:"):
                    ids = _uuids([resource.removeprefix("artifact:")])
                    break
        if not ids and isinstance(result.get("arguments"), dict):
            ids = _uuids([result["arguments"].get("artifact_id")])
        if not ids:
            return dict(unavailable_reason=self._missing_reason(call, "artifact_missing"))
        items, reason = self._resolve(research, ids, scope, session)
        preview = None
        if items and items[0]["mime_type"].startswith("text/"):
            data = self._read(items[0])
            if data is None:
                reason = "artifact_unavailable"
            else:
                preview = _clip(data.decode("utf-8", errors="replace"))
        return dict(artifacts=[self._entry(item, scope) for item in items], preview=preview, unavailable_reason=reason)

    @staticmethod
    def _resolve(repository, ids: list[UUID], scope: dict, session: Session) -> tuple[list[dict[str, Any]], str | None]:
        """Artifact records that really belong to this task and its project owner; any other id becomes a reason.

        Read in the detail's own session: opening a second one while holding the first could exhaust the pool."""
        items, reason = [], None
        for artifact_id in ids[:20]:
            try:
                items.append(repository.get_artifact(str(artifact_id), **scope, session=session))
            except (ScopeDenied, ValueError):
                reason = "artifact_missing"
        return items, reason

    @staticmethod
    def _entry(item: dict[str, Any], scope: dict) -> ToolDetailArtifact:
        return ToolDetailArtifact(id=item["id"], filename=item["filename"], mime_type=item["mime_type"],
            byte_size=item["byte_size"], download_url=f"/api/v1/tasks/{scope['task_id']}/artifacts/{item['id']}")

    def _read(self, item: dict[str, Any]) -> bytes | None:
        if self.storage is None:
            return None
        try:
            return self.storage.read(item["storage_key"], item["sha256"], max_bytes=ARTIFACT_READ_BYTES)
        except (ScopeDenied, ValueError, OSError):
            return None
