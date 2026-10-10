"""Immutable snapshot handoff into a fresh task with newly admitted grants.

Carries files and the original trusted baseline, never old tool permissions,
execution evidence, leases, approvals, budgets, or completion claims.
"""
from uuid import UUID, uuid4
from sqlalchemy import select
from agentforge.database.models import Task
from agentforge.database.coding import CodingSession, CodingSnapshot
from agentforge.contracts.tasks import TERMINAL_STATUSES
from agentforge.subagents.scope import ScopeDenied
from agentforge.sandbox.workspace import workspace_revision
from .coding import _decode


def capture_continuation(session, owner_id, project_id, previous_task_id, selection):
    task = session.scalar(select(Task).where(Task.id == previous_task_id,
        Task.created_by == owner_id, Task.project_id == project_id))
    workspace = session.scalar(select(CodingSession).where(CodingSession.task_id == previous_task_id))
    if task is None or workspace is None or task.status not in TERMINAL_STATUSES:
        raise ScopeDenied("continuation_requires_owned_terminal_workspace")
    if workspace.repo_id != selection.repo_id or workspace.commit_sha != selection.ref:
        raise ScopeDenied("continuation_repository_baseline_mismatch")
    for revision in (workspace.baseline_revision, workspace.revision):
        snapshot = session.scalar(select(CodingSnapshot).where(CodingSnapshot.session_id == workspace.id,
            CodingSnapshot.revision == revision, CodingSnapshot.task_id == task.id))
        if snapshot is None or workspace_revision(_decode(snapshot.files_json)) != revision:
            raise ScopeDenied("continuation_snapshot_integrity_failure")
    return {"task_id": str(task.id), "session_id": str(workspace.id), "repo_id": workspace.repo_id,
        "commit_sha": workspace.commit_sha, "baseline_revision": workspace.baseline_revision,
        "revision": workspace.revision}


def seed_continuation(repository, ctx, receipt):
    with repository.session_factory() as session, session.begin():
        task = repository._task(session, ctx, True)
        previous = session.get(Task, UUID(receipt["task_id"]))
        if previous is None or previous.created_by != task.created_by or previous.project_id != task.project_id or previous.status not in TERMINAL_STATUSES:
            raise ScopeDenied("continuation_ownership_changed")
        selection = task.permissions_json["coding"]["repository"]
        if selection != {"repo_id": receipt["repo_id"], "ref": receipt["commit_sha"]}:
            raise ScopeDenied("continuation_selection_changed")
        existing = session.scalar(select(CodingSession).where(CodingSession.task_id == task.id))
        if existing is not None:
            if existing.baseline_revision != receipt["baseline_revision"] or existing.repo_id != receipt["repo_id"] or existing.commit_sha != receipt["commit_sha"]:
                raise ScopeDenied("continuation_workspace_conflict")
            return repository._view(existing)
        old_workspace = session.get(CodingSession, UUID(receipt["session_id"]))
        if old_workspace is None or old_workspace.task_id != previous.id or old_workspace.baseline_revision != receipt["baseline_revision"]:
            raise ScopeDenied("continuation_source_workspace_changed")
        row = CodingSession(id=uuid4(), task_id=task.id, repo_id=receipt["repo_id"], commit_sha=receipt["commit_sha"],
            baseline_revision=receipt["baseline_revision"], revision=receipt["revision"])
        session.add(row)
        session.flush()
        for revision in dict.fromkeys((receipt["baseline_revision"], receipt["revision"])):
            snapshot = session.scalar(select(CodingSnapshot).where(CodingSnapshot.session_id == old_workspace.id,
                CodingSnapshot.task_id == previous.id, CodingSnapshot.revision == revision))
            if snapshot is None or workspace_revision(_decode(snapshot.files_json)) != revision:
                raise ScopeDenied("continuation_snapshot_integrity_failure")
            session.add(CodingSnapshot(id=uuid4(), task_id=task.id, session_id=row.id, revision=revision,
                files_json=dict(snapshot.files_json)))
        session.flush()
        return repository._view(row)
