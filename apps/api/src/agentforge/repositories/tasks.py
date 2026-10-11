"""Owner-scoped task reads and atomic task/plan/event writes."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4
from sqlalchemy import select
from agentforge.contracts.tasks import (
    TaskCreate, TaskStepView, TaskView, TERMINAL_STATUSES, UNLIMITED_COST_USD, UNLIMITED_WALL_TIME_SEC,
)
from agentforge.database.models import AgentRun, Project, Task, TaskJob, TaskStep
from .events import append_event
from .runs import SessionFactory


class TaskNotFound(LookupError):
    pass


class TaskConflict(ValueError):
    pass


class TaskRepository:
    def __init__(self, session_factory: SessionFactory):
        self.session_factory = session_factory

    @staticmethod
    def _owned(session, task_id: UUID, owner_id: UUID, *, lock=False) -> Task:
        query = select(Task).join(Project).where(Task.id == task_id,
            Task.created_by == owner_id, Project.owner_id == owner_id)
        row = session.scalar(query.with_for_update(of=Task) if lock else query)
        if row is None:
            raise TaskNotFound("task_not_found")
        return row

    def authorize_project(self, owner_id: UUID, project_id: UUID) -> None:
        with self.session_factory() as session:
            if session.scalar(select(Project.id).where(Project.id == project_id, Project.owner_id == owner_id)) is None:
                raise TaskNotFound("project_not_found")

    def create(self, owner_id: UUID, request: TaskCreate) -> TaskView:
        with self.session_factory() as session, session.begin():
            return self.create_in_session(session, owner_id, request)

    def create_in_session(self, session, owner_id: UUID, request: TaskCreate, *, context: dict | None = None, automatic: dict | None = None) -> TaskView:
        request = TaskCreate.model_validate_json(request.model_dump_json())
        if request.coding is not None:
            import re
            if not re.fullmatch(r"[0-9a-f]{40}", request.coding.repository.ref):
                raise TaskConflict("github_immutable_commit_required")
        from agentforge.repositories.admission import admit_task
        admit_task(session, owner_id)
        project = session.scalar(select(Project).where(Project.id == request.project_id,
            Project.owner_id == owner_id).with_for_update())
        if project is None:
            raise TaskNotFound("project_not_found")
        if request.agent_key:
            from agentforge.database.models import AgentDefinition, AgentDefinitionVersion
            from agentforge.subagents.registry import canonical_snapshot
            from agentforge.contracts.agent_definition import AgentDefinition as DefinitionContract
            import json
            prefix = f"project/{project.id}/"
            if not request.agent_key.startswith(prefix):
                raise TaskConflict("agent_not_authorized")
            definition = session.scalar(select(AgentDefinition).where(AgentDefinition.project_id == project.id,
                AgentDefinition.namespace == "project", AgentDefinition.agent_key == request.agent_key[len(prefix):],
                AgentDefinition.disabled_at.is_(None)))
            version = session.scalar(select(AgentDefinitionVersion).where(
                AgentDefinitionVersion.definition_id == definition.id,
                AgentDefinitionVersion.version == request.agent_version)) if definition else None
            if version is None:
                raise TaskConflict("agent_version_not_available")
            parsed = DefinitionContract.model_validate_json(json.dumps(version.definition_json))
            snapshot, digest = canonical_snapshot(parsed)
            if digest != version.sha256 or snapshot != version.definition_json:
                raise TaskConflict("agent_definition_integrity_failed")
        continuation = None
        if request.continue_from_task_id:
            from agentforge.repositories.coding_continuation import capture_continuation
            from agentforge.subagents.scope import ScopeDenied
            try:
                continuation = capture_continuation(session, owner_id, project.id, request.continue_from_task_id, request.coding.repository)
            except ScopeDenied as exc:
                raise TaskConflict(str(exc)) from None
        task_id = uuid4()
        row = Task(id=task_id, created_by=owner_id, project_id=project.id,
            goal=request.goal, status="CREATED", thread_id=str(task_id),
            max_cost_usd=UNLIMITED_COST_USD, max_wall_time_sec=UNLIMITED_WALL_TIME_SEC,
            event_sequence=0, permissions_json={"kind": request.kind, "web": request.kind in {"research", "general"},
                "allowed_web_hosts": list(request.allowed_web_hosts),
                "attachments": [item.model_dump() for item in request.attachments],
                **({"agent_key": request.agent_key, "agent_version": request.agent_version} if request.agent_key else {}),
                **({"session_context": context} if context else {}),
                **({"automatic": automatic} if automatic else {}),
                **({"continuation": continuation} if continuation else {}),
                **({"coding": request.coding.model_dump(mode="json")} if request.coding else {})})
        session.add(row)
        session.flush()
        session.add(TaskJob(task_id=row.id))
        append_event(session, row, "task.created", {"status": "CREATED"})
        session.flush()
        return TaskView.model_validate(row)

    def get(self, task_id: UUID, owner_id: UUID) -> TaskView:
        with self.session_factory() as session:
            return TaskView.model_validate(self._owned(session, task_id, owner_id))

    def list(self, owner_id: UUID, project_id: UUID | None = None, limit: int = 50, offset: int = 0) -> list[TaskView]:
        if not 1 <= limit <= 100 or not 0 <= offset <= 10000:
            raise ValueError("invalid_task_limit")
        with self.session_factory() as session:
            query = select(Task).join(Project).where(Task.created_by == owner_id,
                Project.owner_id == owner_id)
            if project_id is not None:
                query = query.where(Task.project_id == project_id)
            return [TaskView.model_validate(row) for row in session.scalars(
                query.order_by(Task.created_at.desc(), Task.id).offset(offset).limit(limit))]

    def steps(self, task_id: UUID, owner_id: UUID) -> list[TaskStepView]:
        with self.session_factory() as session:
            self._owned(session, task_id, owner_id)
            return [TaskStepView.model_validate(row) for row in session.scalars(
                select(TaskStep).where(TaskStep.task_id == task_id).order_by(TaskStep.created_at, TaskStep.id))]

    def runs(self, task_id: UUID, owner_id: UUID) -> list[dict]:
        with self.session_factory() as session:
            self._owned(session, task_id, owner_id)
            # Exclude brief, prompt and permission snapshots from public history.
            return [{"id": row.id, "status": row.status, "started_at": row.started_at,
                "finished_at": row.finished_at, "reserved_cost_usd": row.reserved_cost_usd,
                "measured_cost_usd": row.measured_cost_usd, "error_code": row.error_code,
                "definition_version_id": row.definition_version_id,
                "agent_type": row.definition_snapshot.get("name"),
                "definition_version": row.definition_snapshot.get("version"),
                "parent_task_id": row.task_id, "budget": row.effective_budget_json,
                "effective_tool_names": row.effective_permissions_json.get("available_tools", [])}
                for row in session.scalars(select(AgentRun).where(AgentRun.task_id == task_id)
                    .order_by(AgentRun.started_at).limit(100))]

    def cancel(self, task_id: UUID, owner_id: UUID) -> TaskView:
        with self.session_factory() as session, session.begin():
            from .sessions import lock_task_conversation
            from agentforge.database.sessions import Conversation
            conversation_id = lock_task_conversation(session, task_id)
            task = self._owned(session, task_id, owner_id, lock=True)
            if task.status in TERMINAL_STATUSES:
                raise TaskConflict("task_already_terminal")
            if task.cancel_requested_at is None:
                task.cancel_requested_at = datetime.now(timezone.utc)
                append_event(session, task, "task.cancel_requested", {"status": task.status})
            if conversation_id:
                conversation = session.get(Conversation, conversation_id, with_for_update=True)
                conversation.queue_paused = True
                from .sessions import bump_conversation
                bump_conversation(session, conversation, "queue.paused")
            if task.status == "AWAITING_APPROVAL":
                # Its worker job has already finished; no worker remains to
                # observe the flag and settle the task. Keep executing approvals
                # recoverable because an effect may already have been sent.
                from agentforge.database.identity import Approval
                for approval in session.scalars(select(Approval).where(Approval.task_id == task.id).with_for_update()):
                    if approval.state in {"PENDING", "APPROVED"}:
                        approval.state = "INVALIDATED"
                task.status = "CANCELLED"
                task.final_summary = "Task cancelled. GitHub operations already sent may still require reconciliation."
                append_event(session, task, "task.finished", {"status": task.status})
                from .sessions import complete_task_conversation
                complete_task_conversation(session, task.id)
            session.flush()
            return TaskView.model_validate(task)
