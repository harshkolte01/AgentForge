"""Read-only access to the logical workspaces and execution evidence of legacy coding tasks.

Nothing writes these tables any more (the agent works in a conversation sandbox, see tools/sandbox_shell.py); the
readers stay so older conversations still render their artifacts, evidence and continuation workspaces.
"""
import base64
from uuid import UUID
from sqlalchemy import select
from agentforge.database.models import Project, Task
from agentforge.database.coding import CodingSession, CodingEvidence, CodingArtifact
from agentforge.sandbox.workspace import validate_workspace
from agentforge.subagents.scope import ScopeDenied


def _decode(files):
    return validate_workspace({path: base64.b64decode(value, validate=True) for path, value in files.items()})

class CodingRepository:
    def __init__(self, session_factory):
        self.session_factory = session_factory

    def _owned(self, session, *, task_id, owner_id, project_id):
        task = session.scalar(select(Task).join(Project, Project.id == Task.project_id).where(Task.id == task_id,
            Task.project_id == project_id, Task.created_by == owner_id, Project.owner_id == owner_id))
        if task is None:
            raise ScopeDenied("coding task ownership denied")
        return task

    @staticmethod
    def _view(row):
        return dict(id=str(row.id), task_id=str(row.task_id), repo_id=row.repo_id,
                    commit_sha=row.commit_sha, revision=row.revision, baseline_revision=row.baseline_revision)

    @staticmethod
    def _artifact(row, internal=False):
        value = dict(id=str(row.id), task_id=str(row.task_id), kind=row.kind, filename=row.filename,
            mime_type=row.mime_type, byte_size=row.byte_size, sha256=row.sha256, created_at=row.created_at.isoformat())
        if internal:
            value.update(storage_key=row.storage_key, metadata=row.metadata_json)
        return value

    def list_evidence(self, *, task_id, owner_id, project_id):
        with self.session_factory() as session:
            self._owned(session, task_id=task_id, owner_id=owner_id, project_id=project_id)
            rows = session.scalars(select(CodingEvidence).where(CodingEvidence.task_id == task_id)
                .order_by(CodingEvidence.created_at, CodingEvidence.id).limit(200))
            return [{**{key: (value[:2000] if key in {"stdout", "stderr"} and isinstance(value, str) else value)
                   for key, value in r.evidence_json.items()}, "id": str(r.id), "kind": r.kind,
                   "revision": r.revision, "logs_preview": True} for r in rows]

    def list_artifacts(self, *, task_id, owner_id, project_id):
        with self.session_factory() as session:
            self._owned(session, task_id=task_id, owner_id=owner_id, project_id=project_id)
            return [self._artifact(row) for row in session.scalars(select(CodingArtifact).where(
                CodingArtifact.task_id == task_id).order_by(CodingArtifact.created_at).limit(100))]

    def get_artifact(self, artifact_id, *, task_id, owner_id, project_id, session=None):
        """`session`: a caller's open session to read in, so it never holds one connection while waiting for another."""
        if session is None:
            with self.session_factory() as session:
                return self.get_artifact(artifact_id, task_id=task_id, owner_id=owner_id, project_id=project_id, session=session)
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
