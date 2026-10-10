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

    def resolve_automatic(self, kind, *, repository=None, coding=None, agent=None):
        """Resolve once before execution; model output never supplies grant values."""
        if kind not in {"research", "general", "coding"}:
            raise ValueError("invalid_automatic_kind")
        with self.sessions() as session, session.begin():
            task = self.locked_task(session)
            grants = dict(task.permissions_json)
            automatic = dict(grants.get("automatic") or {})
            if not automatic or automatic.get("resolved"):
                raise ValueError("automatic_already_resolved")
            if session.scalar(select(TaskStep.id).where(TaskStep.task_id == task.id).limit(1)):
                raise ValueError("execution_already_started")
            automatic["resolved"] = True
            grants.update(kind=kind, web=kind != "coding", automatic=automatic)
            if repository and kind != "coding":
                grants["repository"] = repository.model_dump(mode="json")
            if coding:
                grants["coding"] = coding.model_dump(mode="json")
                grants["allowed_web_hosts"] = []
                previous = automatic.get("continue_from_task_id")
                if previous:
                    from agentforge.repositories.coding_continuation import capture_continuation
                    grants["continuation"] = capture_continuation(session, task.created_by,
                        task.project_id, UUID(previous), coding.repository)
            if agent:
                grants["agent_key"], grants["agent_version"] = agent
            task.permissions_json = grants
            append_event(session, task, "task.plan.created", {"type": kind, "reason": "automatic_intent_resolved"})

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

    def ensure_research_plan(self, key: str, version: int, *, objectives=None):
        with self.sessions() as session, session.begin():
            task = self.locked_task(session)
            existing = session.scalar(select(TaskStep).where(TaskStep.task_id == task.id).limit(1))
            if existing is not None:
                return
            for index, objective in enumerate(objectives or [task.goal]):
                session.add(TaskStep(task_id=task.id, step_key=f"research-{index + 1}", agent_type=key,
                    definition_version=version, objective=objective, depends_on=[],
                    acceptance_criteria=["Cover the complete original user goal and its explicit constraints.",
                        "Support all factual claims and clearly distinguish quotations, inference and uncertainty.",
                        "Assess conflicts across consulted evidence; do not equate citation existence with truth."], status="PENDING"))
            append_event(session, task, "task.plan.created", {"type": key, "version": version})

    def ensure_verification_step(self, criteria):
        with self.sessions() as session, session.begin():
            task = self.locked_task(session)
            step = session.scalar(select(TaskStep).where(TaskStep.task_id == task.id, TaskStep.step_key == "verification"))
            if step is None:
                step = TaskStep(task_id=task.id, step_key="verification", agent_type="builtin/verifier",
                    definition_version=3, objective="Independently inspect all owned inputs, sources and deliverables.",
                    depends_on=[], acceptance_criteria=list(criteria), status="PENDING")
                session.add(step)
                session.flush()
            session.expunge(step)
            return step

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
            from agentforge.repositories.sessions import complete_task_conversation
            complete_task_conversation(session, task.id)

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
