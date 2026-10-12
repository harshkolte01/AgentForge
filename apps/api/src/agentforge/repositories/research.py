"""Immutable research persistence with fresh task/project ownership on every read."""

from collections.abc import Callable
import hashlib
import re
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from agentforge.database.models import AgentRun, Project, Task
from agentforge.database.research_models import ResearchArtifact, ResearchEvidence, ResearchSource
from agentforge.subagents.scope import ScopeDenied, TaskContext

# A web address as people paste it into a message; trailing sentence punctuation is trimmed below.
_MESSAGE_URL = re.compile(r"https?://[^\s<>\"'`]+", re.IGNORECASE)
_TRAILING = ".,;:!?*_'\""
_CLOSERS = {")": "(", "]": "[", "}": "{", ">": "<"}
MAX_MESSAGE_URLS = 20


def normalized_url(url: str) -> str | None:
    """The form search results are stored in (`str(httpx.URL(...))`), or None for anything that is not a web address."""
    try:
        value = str(httpx.URL(url))
    except (httpx.InvalidURL, TypeError, ValueError):
        return None
    parts = urlsplit(value)
    return value if parts.scheme in ("http", "https") and parts.hostname else None


def message_urls(text: str | None) -> frozenset[str]:
    """The exact web addresses written in a message, normalized, at most MAX_MESSAGE_URLS.

    A closing bracket stays only when the address opened it (Wikipedia-style "(disambiguation)" links), so
    "(see https://x.dev/a)." yields https://x.dev/a.
    """
    found: set[str] = set()
    for match in _MESSAGE_URL.finditer(text or ""):
        candidate = match.group(0)
        while candidate:
            last = candidate[-1]
            if last in _TRAILING or (last in _CLOSERS and candidate.count(_CLOSERS[last]) < candidate.count(last)):
                candidate = candidate[:-1]
            else:
                break
        url = normalized_url(candidate)
        if url is not None:
            found.add(url)
            if len(found) >= MAX_MESSAGE_URLS:
                break
    return frozenset(found)


def _uuid(value: str | UUID) -> UUID:
    try:
        parsed = UUID(str(value))
        if str(parsed) != str(value):
            raise ValueError("noncanonical reference")
        return parsed
    except (ValueError, TypeError, AttributeError):
        raise ScopeDenied("invalid research reference") from None


class ResearchRepository:
    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self.session_factory = session_factory

    @staticmethod
    def _owned(session: Session, *, task_id: UUID, owner_id: UUID, project_id: UUID) -> Task:
        task = session.scalar(select(Task).join(Project, Project.id == Task.project_id).where(
            Task.id == task_id, Task.project_id == project_id, Task.created_by == owner_id,
            Project.owner_id == owner_id))
        if task is None:
            raise ScopeDenied("research task ownership is not authorized")
        return task

    @staticmethod
    def _scope(ctx: TaskContext) -> dict[str, UUID]:
        return {"task_id": ctx.task_id, "owner_id": ctx.user_id, "project_id": ctx.project_id}

    @staticmethod
    def _writable(task: Task) -> None:
        if task.cancel_requested_at is not None or task.status in {"COMPLETED", "PARTIAL", "FAILED", "CANCELLED"}:
            raise ScopeDenied("research task no longer accepts new evidence")

    @staticmethod
    def _source(row: ResearchSource, *, full: bool = False) -> dict[str, Any]:
        value = {"id": str(row.id), "task_id": str(row.task_id), "url": row.url,
                 "title": row.title, "excerpt": row.content[:2000], "content_sha256": row.content_sha256,
                 "source_type": row.source_type, "fetch_status": row.fetch_status,
                 "retrieved_at": row.retrieved_at.isoformat(), "error_code": row.error_code}
        if full:
            value["content"] = row.content
        return value

    @staticmethod
    def _preview_columns() -> tuple:
        return (ResearchSource.id, ResearchSource.task_id, ResearchSource.url,
                ResearchSource.title, func.substr(ResearchSource.content, 1, 2000).label("excerpt"),
                ResearchSource.content_sha256, ResearchSource.source_type,
                ResearchSource.fetch_status, ResearchSource.retrieved_at, ResearchSource.error_code)

    @staticmethod
    def _source_preview(row) -> dict[str, Any]:
        return {"id": str(row["id"]), "task_id": str(row["task_id"]), "url": row["url"],
                "title": row["title"], "excerpt": row["excerpt"],
                "content_sha256": row["content_sha256"], "source_type": row["source_type"],
                "fetch_status": row["fetch_status"], "retrieved_at": row["retrieved_at"].isoformat(),
                "error_code": row["error_code"]}

    @staticmethod
    def _artifact(row: ResearchArtifact, *, internal: bool = False) -> dict[str, Any]:
        value = {"id": str(row.id), "task_id": str(row.task_id), "kind": row.kind,
                 "filename": row.filename, "mime_type": row.mime_type, "byte_size": row.byte_size,
                 "sha256": row.sha256, "created_at": row.created_at.isoformat()}
        if internal:
            value["storage_key"] = row.storage_key
            value["metadata"] = row.metadata_json
        return value

    def add_source(self, ctx: TaskContext, *, run_id: UUID, operation_key: str, ordinal: int,
                   url: str, title: str, content: str, source_type: str,
                   fetch_status: str, error_code: str | None = None) -> dict[str, Any]:
        if len(content.encode()) > 2_000_000 or len(url) > 2048 or len(title) > 1000:
            raise ValueError("research_source_size_limit")
        with self.session_factory() as session, session.begin():
            self._writable(self._owned(session, **self._scope(ctx)))
            run = session.scalar(select(AgentRun.id).where(AgentRun.id == run_id, AgentRun.task_id == ctx.task_id))
            if run is None:
                raise ScopeDenied("research run is outside the task")
            existing = session.scalar(select(ResearchSource).where(ResearchSource.task_id == ctx.task_id,
                ResearchSource.operation_key == operation_key, ResearchSource.ordinal == ordinal))
            if existing is not None:
                if (existing.agent_run_id != run_id or existing.url != url or existing.title != title or
                    existing.content != content or existing.source_type != source_type or
                    existing.fetch_status != fetch_status or existing.error_code != error_code):
                    raise ScopeDenied("immutable source operation does not match its original inputs")
                return self._source(existing)
            row = ResearchSource(id=uuid4(), task_id=ctx.task_id, agent_run_id=run_id,
                operation_key=operation_key, ordinal=ordinal, url=url, title=title,
                content=content, content_sha256=hashlib.sha256(content.encode()).hexdigest(),
                source_type=source_type, fetch_status=fetch_status, error_code=error_code)
            session.add(row)
            session.flush()
            # Every source ID is independently acceptable as a source reference;
            # this evidence record records what retrieval actually establishes.
            session.add(ResearchEvidence(id=uuid4(), task_id=ctx.task_id, source_id=row.id,
                kind="source_retrieval", validation_state=fetch_status,
                evidence_json={"content_sha256": row.content_sha256, "fetch_status": fetch_status}))
            return self._source(row)

    def list_sources(self, *, task_id: UUID, owner_id: UUID, project_id: UUID) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            self._owned(session, task_id=task_id, owner_id=owner_id, project_id=project_id)
            rows = session.execute(select(*self._preview_columns()).where(
                ResearchSource.task_id == task_id).order_by(ResearchSource.retrieved_at, ResearchSource.id).limit(1000)).mappings()
            return [self._source_preview(row) for row in rows]

    def read_source(self, source_id: str, *, task_id: UUID, owner_id: UUID, project_id: UUID) -> dict[str, Any]:
        with self.session_factory() as session:
            self._owned(session, task_id=task_id, owner_id=owner_id, project_id=project_id)
            row = session.scalar(select(ResearchSource).where(ResearchSource.id == _uuid(source_id),
                                                            ResearchSource.task_id == task_id))
            if row is None:
                raise ScopeDenied("source reference is outside the task")
            return self._source(row, full=True)

    def sources_for_url(self, ctx: TaskContext, url: str) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            self._owned(session, **self._scope(ctx))
            rows = session.execute(select(*self._preview_columns()).where(
                ResearchSource.task_id == ctx.task_id, ResearchSource.url == url,
                ResearchSource.fetch_status.in_(("SEARCH_ONLY", "FETCHED"))).order_by(ResearchSource.retrieved_at).limit(1000)).mappings()
            return [self._source_preview(row) for row in rows]

    def message_urls(self, ctx: TaskContext) -> frozenset[str]:
        """The web addresses the person wrote in this task's own message (its goal), never text the model produced."""
        with self.session_factory() as session:
            return message_urls(self._owned(session, **self._scope(ctx)).goal)

    def verify_references(self, ctx: TaskContext, *, evidence_ids: tuple[str, ...], artifact_ids: tuple[str, ...]) -> None:
        with self.session_factory() as session:
            self._owned(session, **self._scope(ctx))
            evidence = {_uuid(value) for value in evidence_ids}
            artifacts = {_uuid(value) for value in artifact_ids}
            found = set(session.scalars(select(ResearchSource.id).where(
                ResearchSource.task_id == ctx.task_id, ResearchSource.id.in_(evidence))))
            found.update(session.scalars(select(ResearchEvidence.id).where(
                ResearchEvidence.task_id == ctx.task_id, ResearchEvidence.id.in_(evidence))))
            found_artifacts = set(session.scalars(select(ResearchArtifact.id).where(
                ResearchArtifact.task_id == ctx.task_id, ResearchArtifact.id.in_(artifacts))))
            if found != evidence or found_artifacts != artifacts:
                raise ScopeDenied("research reference is outside the task")

    def load_reference(self, ctx: TaskContext, reference: str) -> str:
        with self.session_factory() as session:
            self._owned(session, **self._scope(ctx))
            source = session.scalar(select(ResearchSource).where(ResearchSource.id == _uuid(reference),
                                                                ResearchSource.task_id == ctx.task_id))
            if source is not None:
                preview = source.content.encode("utf-8")[:4000].decode("utf-8", errors="ignore")
                return f"UNTRUSTED SOURCE {source.id}\nURL: {source.url}\nStatus: {source.fetch_status}\n{preview}"
            evidence = session.scalar(select(ResearchEvidence).where(ResearchEvidence.id == _uuid(reference),
                                                                    ResearchEvidence.task_id == ctx.task_id))
            if evidence is not None and evidence.source_id is not None:
                source = session.scalar(select(ResearchSource).where(ResearchSource.id == evidence.source_id,
                                                                    ResearchSource.task_id == ctx.task_id))
                if source is not None:
                    preview = source.content.encode("utf-8")[:4000].decode("utf-8", errors="ignore")
                    return f"UNTRUSTED EVIDENCE {evidence.id}\nSource: {source.id}\nStatus: {source.fetch_status}\n{preview}"
            raise ScopeDenied("context reference is not a readable task source")

    def goal(self, ctx: TaskContext) -> str:
        with self.session_factory() as session:
            return self._owned(session, **self._scope(ctx)).goal

    def list_artifacts(self, *, task_id: UUID, owner_id: UUID, project_id: UUID) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            self._owned(session, task_id=task_id, owner_id=owner_id, project_id=project_id)
            return [self._artifact(row) for row in session.scalars(select(ResearchArtifact).where(
                ResearchArtifact.task_id == task_id).order_by(ResearchArtifact.created_at, ResearchArtifact.id).limit(100))]

    def get_artifact(self, artifact_id: str, *, task_id: UUID, owner_id: UUID, project_id: UUID, session=None) -> dict[str, Any]:
        """`session`: a caller's open session to read in, so it never holds one connection while waiting for another."""
        if session is None:
            with self.session_factory() as session:
                return self.get_artifact(artifact_id, task_id=task_id, owner_id=owner_id, project_id=project_id, session=session)
        self._owned(session, task_id=task_id, owner_id=owner_id, project_id=project_id)
        row = session.scalar(select(ResearchArtifact).where(ResearchArtifact.id == _uuid(artifact_id),
                                                            ResearchArtifact.task_id == task_id))
        if row is None:
            raise ScopeDenied("artifact reference is outside the task")
        return self._artifact(row, internal=True)

    def artifact_for_operation(self, ctx: TaskContext, operation_key: str) -> dict[str, Any] | None:
        with self.session_factory() as session:
            self._owned(session, **self._scope(ctx))
            row = session.scalar(select(ResearchArtifact).where(ResearchArtifact.task_id == ctx.task_id,
                ResearchArtifact.operation_key == operation_key))
            return self._artifact(row, internal=True) if row else None

    def add_artifact(self, ctx: TaskContext, *, artifact_id: UUID, operation_key: str,
                     storage_key: str, sha256: str, byte_size: int, metadata: dict[str, Any],
                     kind: str = "research_report", filename: str = "research-report.md",
                     mime_type: str = "text/markdown; charset=utf-8") -> dict[str, Any]:
        if not storage_key.startswith(f"{ctx.task_id}/"):
            raise ScopeDenied("artifact storage key is outside the task")
        with self.session_factory() as session, session.begin():
            task = self._owned(session, **self._scope(ctx))
            session.scalar(select(Task).where(Task.id == task.id).with_for_update())
            self._writable(task)
            if kind.startswith("general_"):
                count, total = session.execute(select(func.count(ResearchArtifact.id), func.coalesce(func.sum(ResearchArtifact.byte_size), 0)).where(
                    ResearchArtifact.task_id == ctx.task_id, ResearchArtifact.kind.like("general_%"))).one()
                if count >= 20 or total + byte_size > 1_000_000:
                    raise ScopeDenied("general artifact aggregate limit exceeded")
            row = ResearchArtifact(id=artifact_id, task_id=ctx.task_id, operation_key=operation_key,
                kind=kind, filename=filename, mime_type=mime_type,
                storage_key=storage_key, sha256=sha256, byte_size=byte_size, metadata_json=metadata)
            session.add(row)
            session.flush()
            return self._artifact(row, internal=True)
