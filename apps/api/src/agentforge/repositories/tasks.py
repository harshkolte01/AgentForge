"""Owner-scoped task reads and atomic task/plan/event writes."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4
from sqlalchemy import select
from agentforge.contracts.tasks import TaskCreate, TaskStepView, TaskView, TERMINAL_STATUSES
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
        request = TaskCreate.model_validate_json(request.model_dump_json())
        if request.coding is not None:
            import re
            if not re.fullmatch(r"[0-9a-f]{40}", request.coding.repository.ref):
                raise TaskConflict("github_immutable_commit_required")
        task_id = uuid4()
        with self.session_factory() as session, session.begin():
            from agentforge.repositories.admission import admit_task
            admit_task(session, owner_id, request.max_cost_usd)
            project = session.scalar(select(Project).where(Project.id == request.project_id,
                Project.owner_id == owner_id).with_for_update())
            if project is None:
                raise TaskNotFound("project_not_found")
            row = Task(id=task_id, created_by=owner_id, project_id=project.id,
                goal=request.goal, status="CREATED", thread_id=str(task_id),
                max_cost_usd=request.max_cost_usd, max_wall_time_sec=request.max_wall_time_sec,
                event_sequence=0, permissions_json={"kind": request.kind, "web": request.kind == "research",
                    "allowed_web_hosts": list(request.allowed_web_hosts),
                    **({"coding": request.coding.model_dump(mode="json")} if request.coding else {})})
            session.add(row)
            session.flush()
            session.add(TaskJob(task_id=row.id))
            append_event(session, row, "task.created", {"status": "CREATED"})
            session.flush()
            result = TaskView.model_validate(row)
        return result

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
                "effective_tool_names": row.effective_permissions_json.get("available_tools", [])}
                for row in session.scalars(select(AgentRun).where(AgentRun.task_id == task_id)
                    .order_by(AgentRun.started_at).limit(100))]

    def cancel(self, task_id: UUID, owner_id: UUID) -> TaskView:
        with self.session_factory() as session, session.begin():
            task = self._owned(session, task_id, owner_id, lock=True)
            if task.status in TERMINAL_STATUSES:
                raise TaskConflict("task_already_terminal")
            if task.cancel_requested_at is None:
                task.cancel_requested_at = datetime.now(timezone.utc)
                append_event(session, task, "task.cancel_requested", {"status": task.status})
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
            session.flush()
            return TaskView.model_validate(task)
