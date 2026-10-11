"""Postgres SKIP LOCKED queue with generation fencing and no long transactions."""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID
from sqlalchemy import and_, func, or_, select
from agentforge.contracts.tasks import TERMINAL_STATUSES
from agentforge.database.models import Task, TaskJob
from agentforge.subagents.scope import ScopeDenied
from .runs import SessionFactory


class LeaseLost(ScopeDenied):
    pass


class TaskCancelled(ScopeDenied):
    pass


@dataclass(frozen=True)
class Lease:
    job_id: UUID
    task_id: UUID
    owner: str
    generation: int
    reclaimed: bool


def assert_lease(session, task: Task, lease: Lease, *, allow_cancel=False) -> TaskJob:
    """Task -> job lock order for all parent writes; DB clock is lease authority."""
    job = session.scalar(select(TaskJob).where(TaskJob.id == lease.job_id).with_for_update())
    now = session.scalar(select(func.clock_timestamp()))
    if (job is None or job.task_id != task.id or job.status != "RUNNING"
        or job.lease_owner != lease.owner or job.lease_generation != lease.generation
        or job.lease_expires_at is None or job.lease_expires_at <= now):
        raise LeaseLost("worker_lease_lost")
    if task.cancel_requested_at is not None and not allow_cancel:
        raise TaskCancelled("task_cancelled")
    return job


class JobRepository:
    def __init__(self, session_factory: SessionFactory, *, lease_sec: int = 90):
        self.session_factory, self.lease_sec = session_factory, lease_sec

    def begin_execution(self, lease: Lease) -> None:
        """Start new conversation deadlines once; queue waiting is not execution."""
        from agentforge.database.sessions import TurnExecution, TurnInput
        with self.session_factory() as session, session.begin():
            execution = session.scalar(select(TurnExecution.turn_id).join(TurnInput, TurnInput.turn_id == TurnExecution.turn_id)
                .where(TurnExecution.task_id == lease.task_id))
            if execution is None:
                return
            task = session.scalar(select(Task).where(Task.id == lease.task_id).with_for_update())
            if task is None:
                raise LeaseLost("task_missing")
            assert_lease(session, task, lease, allow_cancel=True)
            if task.execution_started_at is None:
                task.execution_started_at = session.scalar(select(func.clock_timestamp()))
                task.execution_deadline = task.execution_started_at + timedelta(seconds=task.max_wall_time_sec)

    def claim(self, worker_id: str) -> Lease | None:
        with self.session_factory() as session, session.begin():
            now = session.scalar(select(func.clock_timestamp()))
            job = session.scalar(select(TaskJob).where(or_(
                and_(TaskJob.status == "QUEUED", TaskJob.available_at <= now),
                and_(TaskJob.status == "RUNNING", TaskJob.lease_expires_at <= now),
            )).order_by(TaskJob.available_at, TaskJob.id).with_for_update(skip_locked=True).limit(1))
            if job is None:
                return None
            reclaimed = job.status == "RUNNING"
            job.status, job.lease_owner = "RUNNING", worker_id
            job.lease_generation += 1
            job.attempts += 1
            job.heartbeat_at, job.lease_expires_at = now, now + timedelta(seconds=self.lease_sec)
            return Lease(job.id, job.task_id, worker_id, job.lease_generation, reclaimed)

    def release(self, lease: Lease) -> bool:
        """Hands a job claimed but never started back to the queue (the worker is stopping), so another worker runs it
        rather than this one starting and then cancelling it. Only a fresh claim, never a reclaimed one: recovering a
        dead worker's job relies on the reclaim being remembered."""
        if lease.reclaimed:
            return False
        with self.session_factory() as session, session.begin():
            job = session.scalar(select(TaskJob).where(TaskJob.id == lease.job_id).with_for_update())
            if job is None or job.status != "RUNNING" or job.lease_owner != lease.owner or job.lease_generation != lease.generation:
                return False
            job.status, job.lease_owner, job.lease_expires_at = "QUEUED", None, None
            job.attempts = max(0, job.attempts - 1)
            return True

    def heartbeat(self, lease: Lease) -> None:
        with self.session_factory() as session, session.begin():
            task = session.scalar(select(Task).where(Task.id == lease.task_id).with_for_update())
            if task is None:
                raise LeaseLost("task_missing")
            job = assert_lease(session, task, lease, allow_cancel=True)
            now = session.scalar(select(func.clock_timestamp()))
            job.heartbeat_at, job.lease_expires_at = now, now + timedelta(seconds=self.lease_sec)

    async def authority(self, lease: Lease, *, allow_cancel=False) -> None:
        # Called before every model step and tool call: off the event loop, so other tasks in the worker keep moving.
        await asyncio.to_thread(self._authority, lease, allow_cancel)

    def _authority(self, lease: Lease, allow_cancel: bool) -> None:
        with self.session_factory() as session, session.begin():
            task = session.scalar(select(Task).where(Task.id == lease.task_id).with_for_update())
            if task is None:
                raise LeaseLost("task_missing")
            assert_lease(session, task, lease, allow_cancel=allow_cancel)
            if task.status in TERMINAL_STATUSES:
                raise LeaseLost("task_already_terminal")

    def complete(self, lease: Lease) -> None:
        with self.session_factory() as session, session.begin():
            task = session.scalar(select(Task).where(Task.id == lease.task_id).with_for_update())
            if task is None:
                raise LeaseLost("task_missing")
            job = assert_lease(session, task, lease, allow_cancel=True)
            if task.status not in TERMINAL_STATUSES:
                raise ValueError("task_not_terminal")
            job.status, job.finished_at = "COMPLETED", datetime.now(timezone.utc)
            job.lease_owner, job.lease_expires_at = None, None
