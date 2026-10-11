"""Durable parent workflow state, append-only events and fenced queue leases."""

from datetime import datetime
from typing import Any
from uuid import UUID
from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Integer, Text, UniqueConstraint, Uuid, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base


class TaskStream(Base):
    """Live copy of the text being written by the model. Ephemeral: saved notes and the saved message are authoritative.

    epoch changes whenever a new model attempt starts writing, so a stale worker cannot overwrite it. revision changes
    on every write that is not plain growth (a clear, a restart, a kind change), so a reader can tell "more text" from
    "different text", and an older writer cannot bring back text a newer revision cleared.

    kind `tool` is a tool call the model is writing (`text` is its command or file content so far) and `output` one that is
    running (`text` is the end of what it printed). `tool` is the call's name, `call_id` the id its `tool.started` event
    carries and `detail` what it is about (a path). They change only together with a new revision.
    """
    __tablename__ = "task_streams"
    __table_args__ = (CheckConstraint("kind IN ('progress','answer','tool','output')", name="kind"),)
    task_id: Mapped[UUID] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True)
    epoch: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    revision: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    kind: Mapped[str] = mapped_column(Text, server_default=text("'progress'"))
    text: Mapped[str] = mapped_column(Text, server_default=text("''"))
    tool: Mapped[str | None] = mapped_column(Text, nullable=True)
    call_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TaskStep(Base):
    __tablename__ = "task_steps"
    __table_args__ = (
        UniqueConstraint("task_id", "step_key"),
        CheckConstraint("status IN ('PENDING','RUNNING','COMPLETED','PARTIAL','FAILED','BLOCKED','CANCELLED')", name="status"),
        CheckConstraint("definition_version >= 1 AND attempt >= 1 AND attempt <= 3", name="bounds"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, server_default=text("gen_random_uuid()"))
    task_id: Mapped[UUID] = mapped_column(ForeignKey("tasks.id"), index=True)
    step_key: Mapped[str] = mapped_column(Text)
    agent_type: Mapped[str] = mapped_column(Text)
    definition_version: Mapped[int] = mapped_column(Integer)
    objective: Mapped[str] = mapped_column(Text)
    acceptance_criteria: Mapped[list[str]] = mapped_column(JSONB)
    depends_on: Mapped[list[str]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(Text, server_default=text("'PENDING'"))
    # Allocated BEFORE reservation/run creation, so intentionally no FK to runs.
    agent_run_id: Mapped[UUID | None] = mapped_column(Uuid, unique=True)
    attempt: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    @property
    def result(self) -> dict[str, Any] | None:
        return self.result_json


class TaskEvent(Base):
    __tablename__ = "task_events"
    __table_args__ = (CheckConstraint("sequence > 0", name="sequence"),)
    task_id: Mapped[UUID] = mapped_column(ForeignKey("tasks.id"), primary_key=True)
    sequence: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    event_type: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TaskJob(Base):
    __tablename__ = "task_jobs"
    __table_args__ = (
        CheckConstraint("status IN ('QUEUED','RUNNING','COMPLETED')", name="status"),
        CheckConstraint("lease_generation >= 0 AND attempts >= 0", name="bounds"),
        Index("task_jobs_claim_idx", "status", "available_at", "lease_expires_at"),
        # Only claimable rows, in claim order: finished jobs (nearly all of them) are never scanned by `claim`.
        Index("task_jobs_claimable_idx", "available_at", "id", postgresql_where=text("status IN ('QUEUED','RUNNING')")),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, server_default=text("gen_random_uuid()"))
    task_id: Mapped[UUID] = mapped_column(ForeignKey("tasks.id"), unique=True)
    status: Mapped[str] = mapped_column(Text, server_default=text("'QUEUED'"))
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    lease_owner: Mapped[str | None] = mapped_column(Text)
    lease_generation: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WorkerHeartbeat(Base):
    """One row per running worker loop, refreshed every few seconds, so the API can tell whether anything will pick up
    a queued message. Rows of workers that stopped are pruned after a day."""
    __tablename__ = "worker_heartbeats"
    worker_id: Mapped[str] = mapped_column(Text, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    version: Mapped[str | None] = mapped_column(Text, nullable=True)
