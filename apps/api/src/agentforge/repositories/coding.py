"""Atomic, fenced logical workspaces and append-only actual execution evidence.

Repository source is never placed on the API host filesystem. Snapshots contain
bounded binary-safe maps; the trusted baseline lives outside sandbox-controlled
.git state. External operation intents survive crashes and prohibit paid retry.
"""
import base64
import difflib
import hashlib
import json
from uuid import UUID, NAMESPACE_URL, uuid4, uuid5
from sqlalchemy import select, func
from agentforge.database.models import Project, Task
from agentforge.database.coding import CodingSession, CodingSnapshot, CodingOperation, CodingEvidence, CodingArtifact
from agentforge.integrations.artifact_storage import ArtifactStorage
from agentforge.repositories.jobs import assert_lease
from agentforge.sandbox.workspace import validate_workspace, workspace_revision, build_diff
from agentforge.security.policies import validate_relative_path
from agentforge.subagents.scope import ScopeDenied


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

def _encode(files):
    return {path: base64.b64encode(content).decode("ascii") for path, content in files.items()}

def _decode(files):
    return validate_workspace({path: base64.b64decode(value, validate=True) for path, value in files.items()})

class CodingRepository:
    def __init__(self, session_factory, storage=None, *, lease=None):
        self.session_factory, self.storage, self.lease = session_factory, storage, lease

    def _owned(self, session, *, task_id, owner_id, project_id, writable=False):
        query = select(Task).join(Project, Project.id == Task.project_id).where(Task.id == task_id,
            Task.project_id == project_id, Task.created_by == owner_id, Project.owner_id == owner_id)
        task = session.scalar(query.with_for_update(of=Task) if writable else query)
        if task is None:
            raise ScopeDenied("coding task ownership denied")
        if writable:
            if self.lease is None:
                raise ScopeDenied("coding write requires worker lease")
            assert_lease(session, task, self.lease)
            if task.status in {"COMPLETED", "PARTIAL", "FAILED", "CANCELLED"}:
                raise ScopeDenied("coding task is terminal")
        return task

    @staticmethod
    def _scope(ctx):
        return dict(task_id=ctx.task_id, owner_id=ctx.user_id, project_id=ctx.project_id)

    def _task(self, session, ctx, writable=False):
        return self._owned(session, **self._scope(ctx), writable=writable)

    @staticmethod
    def _view(row):
        return dict(id=str(row.id), task_id=str(row.task_id), repo_id=row.repo_id,
                    commit_sha=row.commit_sha, revision=row.revision, baseline_revision=row.baseline_revision)

    def ensure_session(self, ctx, *, repo_id, commit_sha, files):
        import re
        if not re.fullmatch(r"[a-f0-9]{40}", commit_sha):
            raise ScopeDenied("repository must be pinned to a full commit SHA")
        files = validate_workspace(files)
        revision = workspace_revision(files)
        with self.session_factory() as session, session.begin():
            task = self._task(session, ctx, True)
            spec = task.permissions_json.get("coding", {}).get("repository", {})
            if spec != {"repo_id": repo_id, "ref": commit_sha}:
                raise ScopeDenied("repository differs from task selection")
            existing = session.scalar(select(CodingSession).where(CodingSession.task_id == ctx.task_id))
            if existing:
                if existing.repo_id != repo_id or existing.commit_sha != commit_sha or existing.baseline_revision != revision:
                    raise ScopeDenied("workspace baseline conflict")
                return self._view(existing)
            row = CodingSession(id=uuid4(), task_id=ctx.task_id, repo_id=repo_id, commit_sha=commit_sha,
                baseline_revision=revision, revision=revision)
            session.add(row)
            session.flush()
            session.add(CodingSnapshot(id=uuid4(), task_id=ctx.task_id, session_id=row.id,
                revision=revision, files_json=_encode(files)))
            session.flush()
            return self._view(row)

    def session(self, ctx):
        with self.session_factory() as session:
            self._task(session, ctx)
            row = session.scalar(select(CodingSession).where(CodingSession.task_id == ctx.task_id))
            return self._view(row) if row else None

    def files(self, ctx, *, revision=None, baseline=False):
        with self.session_factory() as session:
            self._task(session, ctx)
            row = session.scalar(select(CodingSession).where(CodingSession.task_id == ctx.task_id))
            if row is None:
                raise ScopeDenied("coding workspace missing")
            selected = row.baseline_revision if baseline else (revision or row.revision)
            snap = session.scalar(select(CodingSnapshot).where(CodingSnapshot.session_id == row.id,
                                                              CodingSnapshot.revision == selected))
            if snap is None:
                raise ScopeDenied("workspace revision missing")
            files = _decode(snap.files_json)
            if workspace_revision(files) != selected:
                raise ScopeDenied("workspace integrity failure")
            return files

    def write_file(self, ctx, *, operation_key, path, content, expected_sha256):
        validate_relative_path(path)
        data = content.encode("utf-8")
        if b"\x00" in data:
            raise ScopeDenied("binary file writes are not supported")
        inputs = _digest(dict(path=path, content=content, expected_sha256=expected_sha256))
        with self.session_factory() as session, session.begin():
            self._task(session, ctx, True)
            previous = session.scalar(select(CodingOperation).where(CodingOperation.task_id == ctx.task_id,
                CodingOperation.operation_key == operation_key))
            if previous:
                if previous.input_sha256 != inputs or previous.kind != "write":
                    raise ScopeDenied("coding operation identity conflict")
                return previous.result_json
            row = session.scalar(select(CodingSession).where(CodingSession.task_id == ctx.task_id).with_for_update())
            snap = session.scalar(select(CodingSnapshot).where(CodingSnapshot.session_id == row.id,
                CodingSnapshot.revision == row.revision))
            files = _decode(snap.files_json)
            prior = files.get(path)
            actual = hashlib.sha256(prior).hexdigest() if prior is not None else None
            if actual != expected_sha256:
                raise ScopeDenied("file compare-and-swap mismatch")
            if prior is not None:
                try:
                    prior.decode("utf-8")
                except UnicodeDecodeError:
                    raise ScopeDenied("binary file writes are not supported") from None
                if b"\x00" in prior:
                    raise ScopeDenied("binary file writes are not supported")
            files[path] = data
            files = validate_workspace(files)
            revision = workspace_revision(files)
            found = session.scalar(select(CodingSnapshot.id).where(CodingSnapshot.session_id == row.id,
                CodingSnapshot.revision == revision))
            if not found:
                # Hard aggregate ceiling prevents free CAS calls from multiplying
                # a large map into unbounded database storage.
                snapshots = list(session.scalars(select(CodingSnapshot).where(CodingSnapshot.session_id == row.id)))
                encoded = _encode(files)
                cumulative = sum(len(json.dumps(item.files_json).encode()) for item in snapshots)
                if len(snapshots) >= 50 or cumulative + len(json.dumps(encoded).encode()) > 100_000_000:
                    raise ScopeDenied("workspace snapshot storage budget exceeded")
                session.add(CodingSnapshot(id=uuid4(), task_id=ctx.task_id, session_id=row.id,
                    revision=revision, files_json=_encode(files)))
            row.revision = revision
            result = dict(path=path, sha256=hashlib.sha256(data).hexdigest(), revision=revision)
            session.add(CodingOperation(id=uuid4(), task_id=ctx.task_id, operation_key=operation_key,
                kind="write", input_sha256=inputs, revision=revision, status="SUCCEEDED", result_json=result))
            return result

    def begin_operation(self, ctx, *, operation_key, kind, inputs, revision):
        from agentforge.tools.gateway import UnknownToolOutcome
        digest = _digest(inputs)
        with self.session_factory() as session, session.begin():
            self._task(session, ctx, True)
            row = session.scalar(select(CodingOperation).where(CodingOperation.task_id == ctx.task_id,
                CodingOperation.operation_key == operation_key))
            if row:
                if row.input_sha256 != digest or row.kind != kind or row.revision != revision:
                    raise ScopeDenied("coding operation identity conflict")
                if row.status == "SUCCEEDED":
                    return row.result_json
                raise UnknownToolOutcome("coding operation has unknown remote outcome")
            current = session.scalar(select(CodingSession).where(CodingSession.task_id == ctx.task_id))
            if current is None or current.revision != revision:
                raise ScopeDenied("coding operation revision changed")
            session.add(CodingOperation(id=uuid4(), task_id=ctx.task_id, operation_key=operation_key,
                kind=kind, input_sha256=digest, revision=revision, status="INTENT"))
            return None

    @staticmethod
    def _artifact(row, internal=False):
        value = dict(id=str(row.id), task_id=str(row.task_id), kind=row.kind, filename=row.filename,
            mime_type=row.mime_type, byte_size=row.byte_size, sha256=row.sha256, created_at=row.created_at.isoformat())
        if internal:
            value.update(storage_key=row.storage_key, metadata=row.metadata_json)
        return value

    def record_evidence(self, ctx, *, operation_key, kind, revision, payload, artifact_content=None,
                        filename="execution-log.md", mime_type="text/markdown; charset=utf-8"):
        # UUID derived from operation identity makes file creation safely reusable
        # after a crash between durable file fsync and database commit.
        evidence_id = uuid5(NAMESPACE_URL, f"agentforge:{ctx.task_id}:{operation_key}:evidence")
        artifact_id = uuid5(NAMESPACE_URL, f"agentforge:{ctx.task_id}:{operation_key}:artifact")
        storage = None
        if artifact_content is not None:
            if self.storage is None:
                raise ScopeDenied("coding artifact storage unavailable")
            digest = hashlib.sha256(artifact_content).hexdigest()
            try:
                key, digest = self.storage.write(task_id=ctx.task_id, artifact_id=artifact_id, content=artifact_content)
            except FileExistsError:
                key = f"{ctx.task_id}/{artifact_id}.md"
                self.storage.read(key, digest)
            storage = (key, digest)
        with self.session_factory() as session, session.begin():
            self._task(session, ctx, True)
            workspace = session.scalar(select(CodingSession).where(CodingSession.task_id == ctx.task_id))
            if workspace is None or workspace.revision != revision:
                raise ScopeDenied("evidence does not match current revision")
            existing = session.scalar(select(CodingEvidence).where(CodingEvidence.id == evidence_id))
            if existing:
                if existing.evidence_json != payload or existing.kind != kind or existing.revision != revision:
                    raise ScopeDenied("immutable coding evidence conflict")
                return dict(evidence_id=str(evidence_id), artifact_id=str(artifact_id) if storage else None,
                    revision=revision)
            session.add(CodingEvidence(id=evidence_id, task_id=ctx.task_id, session_id=workspace.id,
                operation_key=operation_key, kind=kind, revision=revision, evidence_json=payload))
            if storage:
                session.add(CodingArtifact(id=artifact_id, task_id=ctx.task_id, operation_key=operation_key,
                    kind=kind, filename=filename, mime_type=mime_type, storage_key=storage[0], sha256=storage[1],
                    byte_size=len(artifact_content), metadata_json=dict(revision=revision)))
            result = dict(evidence_id=str(evidence_id), artifact_id=str(artifact_id) if storage else None, revision=revision)
            operation = session.scalar(select(CodingOperation).where(CodingOperation.task_id == ctx.task_id,
                CodingOperation.operation_key == operation_key))
            if operation:
                operation.status, operation.result_json = "SUCCEEDED", {**result, "payload": payload}
            return result

    def diff(self, ctx):
        base, current = self.files(ctx, baseline=True), self.files(ctx)
        changed = sorted(path for path in base.keys() | current.keys() if base.get(path) != current.get(path))
        full = build_diff(base, current).encode()
        if len(full) > 2_000_000:
            raise ScopeDenied("diff exceeds artifact limit")
        return changed, full

    def list_evidence(self, *, task_id, owner_id, project_id):
        with self.session_factory() as session:
            self._owned(session, task_id=task_id, owner_id=owner_id, project_id=project_id)
            rows = session.scalars(select(CodingEvidence).where(CodingEvidence.task_id == task_id)
                .order_by(CodingEvidence.created_at, CodingEvidence.id).limit(200))
            return [{**{key: (value[:2000] if key in {"stdout", "stderr"} and isinstance(value, str) else value)
                   for key, value in r.evidence_json.items()}, "id": str(r.id), "kind": r.kind,
                   "revision": r.revision, "logs_preview": True} for r in rows]

    def evidence(self, ctx, evidence_id):
        with self.session_factory() as session:
            self._task(session, ctx)
            row = session.scalar(select(CodingEvidence).where(CodingEvidence.id == UUID(evidence_id),
                CodingEvidence.task_id == ctx.task_id))
            if row is None:
                raise ScopeDenied("coding evidence outside task")
            return {**row.evidence_json, "id": str(row.id), "kind": row.kind, "revision": row.revision}

    def task_inputs(self, ctx):
        """Complete immutable inputs for this owned task, separate from short planning previews."""
        with self.session_factory() as session:
            task = self._task(session, ctx)
            grants = task.permissions_json or {}
            return {"untrusted_task_inputs": True, "goal": task.goal,
                    "attachments": grants.get("attachments", []),
                    "history": grants.get("session_context", {})}

    def verify_references(self, ctx, *, evidence_ids, artifact_ids):
        with self.session_factory() as session:
            self._task(session, ctx)
            for model, values in ((CodingEvidence, evidence_ids), (CodingArtifact, artifact_ids)):
                ids = {UUID(value) for value in values}
                found = set(session.scalars(select(model.id).where(model.task_id == ctx.task_id, model.id.in_(ids))))
                if found != ids:
                    raise ScopeDenied("coding reference outside task")

    def list_artifacts(self, *, task_id, owner_id, project_id):
        with self.session_factory() as session:
            self._owned(session, task_id=task_id, owner_id=owner_id, project_id=project_id)
            return [self._artifact(row) for row in session.scalars(select(CodingArtifact).where(
                CodingArtifact.task_id == task_id).order_by(CodingArtifact.created_at).limit(100))]

    def get_artifact(self, artifact_id, *, task_id, owner_id, project_id):
        with self.session_factory() as session:
            self._owned(session, task_id=task_id, owner_id=owner_id, project_id=project_id)
            row = session.scalar(select(CodingArtifact).where(CodingArtifact.id == UUID(artifact_id), CodingArtifact.task_id == task_id))
            if row is None:
                raise ScopeDenied("coding artifact outside task")
            return self._artifact(row, internal=True)

    def get_session(self, *, task_id, owner_id, project_id):
        with self.session_factory() as session:
            self._owned(session, task_id=task_id, owner_id=owner_id, project_id=project_id)
            row = session.scalar(select(CodingSession).where(CodingSession.task_id == task_id))
            return self._view(row) if row else None

    def operation(self, ctx, operation_key):
        with self.session_factory() as session:
            self._task(session, ctx)
            row = session.scalar(select(CodingOperation).where(CodingOperation.task_id == ctx.task_id,
                CodingOperation.operation_key == operation_key))
            return dict(status=row.status, result=row.result_json, revision=row.revision, kind=row.kind) if row else None

    def evidence_for_operation(self, ctx, operation_key):
        with self.session_factory() as session:
            self._task(session, ctx)
            row = session.scalar(select(CodingEvidence).where(CodingEvidence.task_id == ctx.task_id,
                CodingEvidence.operation_key == operation_key))
            return {**row.evidence_json, "id": str(row.id), "kind": row.kind, "revision": row.revision} if row else None

    def has_unknown_operations(self, ctx):
        from agentforge.database.models import ToolCall
        with self.session_factory() as session:
            self._task(session, ctx)
            return bool(session.scalar(select(CodingOperation.id).where(CodingOperation.task_id == ctx.task_id,
                CodingOperation.status.in_(("INTENT", "UNKNOWN"))).limit(1)) or
                session.scalar(select(ToolCall.id).where(ToolCall.task_id == ctx.task_id, ToolCall.status == "UNKNOWN").limit(1)))

    def review_consumed(self, ctx, run_id, revision, test_ids):
        from agentforge.database.models import ToolCall
        with self.session_factory() as session:
            self._task(session, ctx)
            calls = list(session.scalars(select(ToolCall).where(ToolCall.task_id == ctx.task_id,
                ToolCall.agent_run_id == run_id, ToolCall.status == "SUCCEEDED")))
            names = {row.tool_name for row in calls}
            test_refs = {ref for row in calls if row.tool_name == "get_test_results" for ref in row.evidence_ids}
            diff_refs = {UUID(ref) for row in calls if row.tool_name == "git_diff" for ref in row.evidence_ids}
            matching_diff = session.scalar(select(CodingEvidence.id).where(CodingEvidence.task_id == ctx.task_id,
                CodingEvidence.id.in_(diff_refs), CodingEvidence.kind == "diff", CodingEvidence.revision == revision))
            return bool({"read_sandbox_file", "git_diff", "get_test_results"}.issubset(names)
                and set(test_ids).issubset(test_refs) and matching_diff)
