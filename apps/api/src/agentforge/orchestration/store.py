"""Fenced and idempotent parent writes with transactionally sequenced events."""
from uuid import UUID, uuid4
from sqlalchemy import select
from agentforge.contracts.tasks import TERMINAL_STATUSES
from agentforge.database.models import AgentRun, Task, TaskStep
from agentforge.repositories.events import append_event
from agentforge.repositories.jobs import Lease, assert_lease
from agentforge.repositories.runs import SessionFactory


class WorkflowStore:
    def __init__(self, sessions: SessionFactory, lease: Lease):
        self.sessions, self.lease = sessions, lease

    def locked_task(self, session, *, allow_cancel=False):
        row = session.scalar(select(Task).where(Task.id == self.lease.task_id).with_for_update())
        if row is None:
            raise ValueError("task_missing")
        assert_lease(session, row, self.lease, allow_cancel=allow_cancel)
        return row

    def task(self):
        with self.sessions() as session:
            row = session.get(Task, self.lease.task_id)
            if row is None:
                raise ValueError("task_missing")
            session.expunge(row)
            return row

    def transition(self, status: str):
        allowed = {"CREATED": {"PLANNING"}, "PLANNING": {"RUNNING"},
            "RUNNING": {"VERIFYING"}, "VERIFYING": {"RUNNING"}}
        with self.sessions() as session, session.begin():
            task = self.locked_task(session)
            if task.status == status:
                return
            if status not in allowed.get(task.status, set()):
                raise ValueError("invalid_task_transition")
            previous, task.status = task.status, status
            append_event(session, task, "task.status", {"status": status, "previous_status": previous})

    def ensure_research_plan(self, key: str, version: int):
        with self.sessions() as session, session.begin():
            task = self.locked_task(session)
            existing = session.scalar(select(TaskStep).where(TaskStep.task_id == task.id).limit(1))
            if existing is not None:
                return
            session.add(TaskStep(task_id=task.id, step_key="research", agent_type=key,
                definition_version=version, objective=task.goal, depends_on=[],
                acceptance_criteria=["Use task-owned fetched public sources for cited claims.",
                    "Label unsupported or uncertain claims explicitly.",
                    "Report limitations; do not equate citation existence with truth."], status="PENDING"))
            append_event(session, task, "task.plan.created", {"type": key, "version": version})

    def steps(self):
        with self.sessions() as session:
            rows = list(session.scalars(select(TaskStep).where(TaskStep.task_id == self.lease.task_id)
                .order_by(TaskStep.created_at, TaskStep.id)))
            for row in rows:
                session.expunge(row)
            return rows

    def begin_step(self, step_id: UUID):
        with self.sessions() as session, session.begin():
            task = self.locked_task(session)
            step = session.get(TaskStep, step_id)
            if step is None or step.task_id != task.id:
                raise ValueError("step_missing")
            if step.agent_run_id is None:
                step.agent_run_id = uuid4()
            if step.status == "PENDING":
                step.status = "RUNNING"
                append_event(session, task, "task.step.updated", {"step_id": str(step.id), "status": step.status})
                append_event(session, task, "subagent.started", {"step_id": str(step.id),
                    "spawn_id": str(step.agent_run_id), "type": step.agent_type, "version": step.definition_version})
            session.flush()
            session.expunge(step)
            return step

    def completed_run(self, run_id: UUID) -> dict | None:
        with self.sessions() as session:
            run = session.get(AgentRun, run_id)
            if run and run.task_id == self.lease.task_id and run.finished_at and run.result_json:
                return run.result_json
            return None

    def finish_step(self, step_id: UUID, result: dict):
        with self.sessions() as session, session.begin():
            task = self.locked_task(session)
            step = session.get(TaskStep, step_id)
            if step is None or step.task_id != task.id:
                raise ValueError("step_missing")
            if step.result_json is not None:
                if step.result_json != result:
                    raise ValueError("step_result_conflict")
                return
            step.result_json = result
            step.status = {"PASS": "COMPLETED", "FAIL": "FAILED", "PARTIAL": "PARTIAL", "BLOCKED": "BLOCKED"}[result["status"]]
            append_event(session, task, "subagent.finished", {"step_id": str(step.id),
                "spawn_id": str(step.agent_run_id), "status": step.status})
            append_event(session, task, "task.step.updated", {"step_id": str(step.id), "status": step.status})

    def finish(self, status: str, summary: str, result: dict | None = None, error_code: str | None = None):
        if status not in TERMINAL_STATUSES:
            raise ValueError("invalid_terminal_status")
        with self.sessions() as session, session.begin():
            task = self.locked_task(session, allow_cancel=True)
            if task.status in TERMINAL_STATUSES:
                return
            # A cancel racing a final node wins unless already durably terminal.
            if task.cancel_requested_at is not None:
                status, summary, error_code = "CANCELLED", "Task cancelled; in-flight usage remains accounted for.", "USER_CANCELLED"
            task.status, task.final_summary = status, summary[:8000]
            task.final_result, task.error_code = result, error_code
            append_event(session, task, "task.finished", {"status": status, "error_code": error_code})

    def verified(self, verification: dict, research: dict):
        with self.sessions() as session, session.begin():
            task = self.locked_task(session)
            delivery = {"verification": verification, "research": research}
            if task.final_result == delivery:
                return
            task.final_result = delivery
            append_event(session, task, "task.verified", {"status": verification["status"],
                "checks": len(verification.get("checks", []))})

    def progress(self, event_type: str, payload: dict):
        with self.sessions() as session, session.begin():
            task = self.locked_task(session)
            append_event(session, task, event_type, payload)

    def ensure_coding_step(self, key, agent_type, *, objective, depends_on=()):
        with self.sessions() as session, session.begin():
            task = self.locked_task(session)
            step = session.scalar(select(TaskStep).where(TaskStep.task_id == task.id, TaskStep.step_key == key))
            if step is None:
                step = TaskStep(task_id=task.id, step_key=key, agent_type=agent_type,
                    definition_version=2, objective=objective[:8000], depends_on=list(depends_on),
                    acceptance_criteria=["Preserve repository scope and baseline tests/configuration.",
                        "Require actual fresh revision-matching test execution and independent review."], status="PENDING")
                session.add(step)
                session.flush()
                append_event(session, task, "task.plan.created", {"step_key": key, "type": agent_type, "version": 2})
            session.expunge(step)
            return step

    def coding_delivery(self, delivery):
        with self.sessions() as session, session.begin():
            task = self.locked_task(session)
            task.final_result = delivery
            append_event(session, task, "task.verified", {"status": delivery.get("verification", {}).get("status", "PARTIAL")})
