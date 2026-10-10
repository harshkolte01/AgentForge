"""Immutable, user-bound approvals and durable single-use external operations."""
import hashlib
from datetime import timedelta
from uuid import UUID, uuid4
from sqlalchemy import select
from agentforge.auth.durable import now
from agentforge.contracts.approvals import canonical_hash
from agentforge.database.models import Task, Project
from agentforge.database.coding import CodingSession, CodingSnapshot, CodingArtifact, CodingEvidence
from agentforge.database.identity import Approval, ExternalOperation
from agentforge.repositories.coding import _decode
from agentforge.repositories.events import append_event
from agentforge.sandbox.workspace import build_diff, workspace_revision

class ApprovalError(ValueError):
    pass

def view(row):
    return {"id": str(row.id), "task_id": str(row.task_id), "state": row.state,
        "action_hash": row.action_hash, "expires_at": row.expires_at.isoformat(),
        "action": row.action_json, "result": row.result_json}

class ApprovalRepository:
    def __init__(self, session_factory, settings):
        self.session_factory, self.settings = session_factory, settings
        self.execution_token = str(uuid4())

    @staticmethod
    def owned_task(session, task_id, owner_id, lock=False):
        query = select(Task).join(Project).where(Task.id == task_id, Task.created_by == owner_id, Project.owner_id == owner_id)
        row = session.scalar(query.with_for_update(of=Task) if lock else query)
        if row is None:
            raise ApprovalError("task_not_found")
        return row

    def _owned(self, session, approval_id, owner_id, lock=False):
        query = select(Approval).where(Approval.id == approval_id, Approval.user_id == owner_id)
        row = session.scalar(query)
        if row is None:
            raise ApprovalError("approval_not_found")
        self.owned_task(session, row.task_id, owner_id, lock)
        if lock:
            row = session.scalar(query.with_for_update().execution_options(populate_existing=True))
        if canonical_hash(row.action_json) != row.action_hash:
            raise ApprovalError("approval_integrity_failed")
        return row

    @staticmethod
    def frozen_workspace(session, task):
        delivery = (task.final_result or {}).get("coding", {})
        verification = (task.final_result or {}).get("verification", {})
        if task.status not in {"COMPLETED", "AWAITING_APPROVAL"} or verification.get("status") != "PASS" or not verification.get("fresh_revision") or verification.get("secret_check") is not True:
            raise ApprovalError("verified_coding_result_required")
        workspace = session.scalar(select(CodingSession).where(CodingSession.task_id == task.id))
        if workspace is None or delivery.get("revision") != workspace.revision or delivery.get("base_sha") != workspace.commit_sha:
            raise ApprovalError("coding_revision_changed")
        snapshots = {row.revision: row for row in session.scalars(select(CodingSnapshot).where(
            CodingSnapshot.session_id == workspace.id, CodingSnapshot.revision.in_([workspace.baseline_revision, workspace.revision])))}
        try:
            base, current = _decode(snapshots[workspace.baseline_revision].files_json), _decode(snapshots[workspace.revision].files_json)
        except (KeyError, ValueError):
            raise ApprovalError("coding_snapshot_unavailable") from None
        if workspace_revision(base) != workspace.baseline_revision or workspace_revision(current) != workspace.revision:
            raise ApprovalError("coding_snapshot_integrity_failed")
        diff = build_diff(base, current).encode()
        changed = sorted(path for path in base.keys() | current.keys() if base.get(path) != current.get(path))
        if not changed or len(changed) > 200 or len(diff) > 2_000_000:
            raise ApprovalError("github_diff_empty_or_too_large")
        # GitHub Actions and secrets/CI permission changes need a dedicated future
        # approval surface; this narrow delivery does not permit those writes.
        if any(path.startswith(".github/") for path in changed):
            raise ApprovalError("github_workflow_changes_not_supported")
        references = [*delivery.get("test_evidence_ids", []), *delivery.get("review_evidence_ids", [])]
        if not references or not delivery.get("test_evidence_ids") or len(delivery.get("review_evidence_ids", [])) != 2:
            raise ApprovalError("coding_verification_evidence_required")
        evidence = list(session.scalars(select(CodingEvidence).where(CodingEvidence.task_id == task.id,
            CodingEvidence.id.in_([UUID(item) for item in references]))))
        if len(evidence) != len(set(references)) or any(row.revision != workspace.revision or row.evidence_json.get("qualified") is not True for row in evidence):
            raise ApprovalError("coding_verification_evidence_required")
        artifact = session.scalar(select(CodingArtifact).where(CodingArtifact.task_id == task.id,
            CodingArtifact.id == UUID(delivery["diff_artifact_id"]), CodingArtifact.kind == "diff"))
        if artifact is None or artifact.sha256 != hashlib.sha256(diff).hexdigest():
            raise ApprovalError("coding_diff_integrity_failed")
        return workspace, base, current, changed, artifact

    def task_candidate(self, task_id, owner_id):
        with self.session_factory() as session:
            task = self.owned_task(session, task_id, owner_id)
            workspace, _, _, _, _ = self.frozen_workspace(session, task)
            return workspace.repo_id, workspace.commit_sha

    def prepare(self, task_id, owner_id, request, remote):
        repo, installation = remote
        stamp = now()
        with self.session_factory() as session, session.begin():
            task = self.owned_task(session, task_id, owner_id, True)
            workspace, _, _, changed, artifact = self.frozen_workspace(session, task)
            if workspace.repo_id != repo["full_name"]:
                raise ApprovalError("github_repository_identity_changed")
            branch = f"agentforge/task-{task.id.hex}"
            if branch == repo["default_branch"]:
                raise ApprovalError("github_unsafe_branch")
            expires = stamp + timedelta(seconds=self.settings.github_approval_ttl_sec)
            action = dict(action="github.create_draft_pr", repo_id=workspace.repo_id, repository_id=repo["id"],
                installation_id=installation["id"], base_branch=repo["default_branch"], base_sha=workspace.commit_sha,
                head_branch=branch, revision=workspace.revision, diff_artifact_id=str(artifact.id), diff_sha256=artifact.sha256,
                changed_paths=changed, title=request.title, body=request.body, requested_operations=["push_branch", "create_draft_pr"],
                approval_expires_at=expires.isoformat(), commit_message=f"{request.title}\n\nAgentForge task {task.id}",
                commit_date=stamp.replace(microsecond=0).isoformat())
            # Do not create a second approval while a first write may have run.
            previous = list(session.scalars(select(Approval).where(Approval.task_id == task.id).with_for_update()))
            if any(row.state in {"APPROVED", "EXECUTING", "CONSUMED"} for row in previous):
                raise ApprovalError("github_delivery_already_prepared")
            for row in previous:
                if row.state == "PENDING":
                    row.state = "INVALIDATED"
            row = Approval(id=uuid4(), task_id=task.id, user_id=owner_id, state="PENDING", action_json=action,
                action_hash=canonical_hash(action), expires_at=expires)
            session.add(row)
            task.status = "AWAITING_APPROVAL"
            append_event(session, task, "approval.required", {"approval_id": str(row.id), "action_type": action["action"]})
            session.flush()
            return view(row)

    def list(self, task_id, owner_id):
        with self.session_factory() as session, session.begin():
            task = self.owned_task(session, task_id, owner_id, True)
            rows = list(session.scalars(select(Approval).where(Approval.task_id == task_id, Approval.user_id == owner_id).order_by(Approval.created_at).with_for_update()))
            for row in rows:
                if row.state in {"PENDING", "APPROVED"} and row.expires_at <= now():
                    row.state = "EXPIRED"
            if task.status == "AWAITING_APPROVAL" and not any(row.state in {"PENDING", "APPROVED", "EXECUTING"} for row in rows):
                task.status = "COMPLETED"
            return [view(row) for row in rows]

    def decision(self, approval_id, owner_id, decision):
        with self.session_factory() as session, session.begin():
            row = self._owned(session, approval_id, owner_id, True)
            if row.state != "PENDING" or row.expires_at <= now():
                raise ApprovalError("approval_expired_or_already_decided")
            from secrets import compare_digest
            if not compare_digest(row.action_hash, decision.action_hash):
                raise ApprovalError("approval_hash_mismatch")
            row.state = "APPROVED" if decision.decision == "approve" else "DENIED"
            row.decided_at = now()
            task = self.owned_task(session, row.task_id, owner_id, True)
            if row.state == "DENIED":
                task.status = "COMPLETED"
            append_event(session, task, "approval.decided", {"approval_id": str(row.id), "state": row.state})
            return view(row)

    def load(self, approval_id, owner_id):
        with self.session_factory() as session:
            return view(self._owned(session, approval_id, owner_id))

    def workspace(self, approval_id, owner_id):
        with self.session_factory() as session:
            row = self._owned(session, approval_id, owner_id)
            task = self.owned_task(session, row.task_id, owner_id)
            workspace, base, current, changed, artifact = self.frozen_workspace(session, task)
            if (workspace.revision != row.action_json["revision"] or artifact.sha256 != row.action_json["diff_sha256"]
                    or changed != row.action_json["changed_paths"]):
                raise ApprovalError("approved_diff_changed")
            return base, current

    def begin(self, approval_id, owner_id):
        with self.session_factory() as session, session.begin():
            row = self._owned(session, approval_id, owner_id, True)
            if row.state == "CONSUMED":
                return view(row)
            if row.state not in {"APPROVED", "EXECUTING"}:
                raise ApprovalError("approval_not_approved")
            if row.state == "APPROVED" and row.expires_at <= now():
                raise ApprovalError("approval_expired")
            if row.execution_expires_at and row.execution_expires_at > now():
                raise ApprovalError("github_execution_in_progress")
            row.state = "EXECUTING"
            row.execution_token = self.execution_token
            row.execution_expires_at = now() + timedelta(seconds=300)
            return view(row)

    def operation(self, approval_id, owner_id, kind, payload):
        digest = canonical_hash(payload)
        key = f"github:{approval_id}:{kind}"
        with self.session_factory() as session, session.begin():
            row = self._owned(session, approval_id, owner_id, True)
            if row.state != "EXECUTING" or row.execution_token != self.execution_token or row.execution_expires_at <= now():
                raise ApprovalError("approval_not_executing")
            row.execution_expires_at = now() + timedelta(seconds=300)
            operation = session.scalar(select(ExternalOperation).where(ExternalOperation.operation_key == key).with_for_update())
            if operation is None:
                operation = ExternalOperation(task_id=row.task_id, approval_id=row.id, operation_key=key, kind=kind,
                    status="PREPARED", input_sha256=digest, request_json=payload)
                session.add(operation)
            if operation.input_sha256 != digest:
                raise ApprovalError("github_operation_input_changed")
            session.flush()
            return dict(id=operation.id, status=operation.status, response=operation.response_json)

    def operation_state(self, operation_id, status, response=None):
        with self.session_factory() as session, session.begin():
            operation = session.get(ExternalOperation, operation_id)
            approval = session.get(Approval, operation.approval_id)
            self.owned_task(session, approval.task_id, approval.user_id, True)
            approval = session.get(Approval, operation.approval_id, with_for_update=True, populate_existing=True)
            operation = session.get(ExternalOperation, operation_id, with_for_update=True, populate_existing=True)
            if approval.execution_token != self.execution_token or approval.execution_expires_at <= now():
                raise ApprovalError("github_execution_lease_lost")
            if operation.status == "CONFIRMED" and status != "CONFIRMED":
                raise ApprovalError("github_operation_already_confirmed")
            operation.status, operation.updated_at = status, now()
            if response is not None:
                operation.response_json = response

    def complete(self, approval_id, owner_id, result):
        with self.session_factory() as session, session.begin():
            row = self._owned(session, approval_id, owner_id, True)
            if row.state == "CONSUMED":
                return view(row)
            if row.state != "EXECUTING" or row.execution_token != self.execution_token:
                raise ApprovalError("approval_not_executing")
            row.state, row.result_json = "CONSUMED", result
            row.execution_token, row.execution_expires_at = None, None
            task = self.owned_task(session, row.task_id, owner_id, True)
            task.status, task.final_summary = "COMPLETED", "Verified coding result delivered as a GitHub draft pull request."
            task.final_result = {**(task.final_result or {}), "github": result}
            append_event(session, task, "github.delivery.confirmed", result)
            return view(row)

    def release(self, approval_id, owner_id):
        with self.session_factory() as session, session.begin():
            row = self._owned(session, approval_id, owner_id, True)
            if row.execution_token == self.execution_token:
                row.execution_token, row.execution_expires_at = None, None

    def require_unexpired(self, approval_id, owner_id):
        with self.session_factory() as session:
            row = self._owned(session, approval_id, owner_id)
            if row.execution_token != self.execution_token or row.expires_at <= now():
                raise ApprovalError("approval_expired_or_execution_lease_lost")
