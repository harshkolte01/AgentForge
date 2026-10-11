"""Immutable snapshot handoff into a fresh task with newly admitted grants.

Carries files and the original trusted baseline, never old tool permissions,
execution evidence, leases, approvals, budgets, or completion claims.
"""
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
