"""Task-owned logical sandboxes, immutable snapshots, operations and evidence."""
from datetime import datetime
from typing import Any
from uuid import UUID
from sqlalchemy import CHAR, BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Text, UniqueConstraint, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base

class CodingSession(Base):
    __tablename__ = "coding_sessions"
    __table_args__ = (UniqueConstraint("task_id"),)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    task_id: Mapped[UUID] = mapped_column(ForeignKey("tasks.id"))
    repo_id: Mapped[str] = mapped_column(Text)
    commit_sha: Mapped[str] = mapped_column(CHAR(40))
    baseline_revision: Mapped[str] = mapped_column(CHAR(64))
    revision: Mapped[str] = mapped_column(CHAR(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

class CodingSnapshot(Base):
    __tablename__ = "coding_snapshots"
    __table_args__ = (UniqueConstraint("session_id", "revision"),)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    task_id: Mapped[UUID] = mapped_column(ForeignKey("tasks.id"))
    session_id: Mapped[UUID] = mapped_column(ForeignKey("coding_sessions.id"))
    revision: Mapped[str] = mapped_column(CHAR(64))
    files_json: Mapped[dict[str, str]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

class CodingOperation(Base):
    __tablename__ = "coding_operations"
    __table_args__ = (UniqueConstraint("task_id", "operation_key"),
        CheckConstraint("status IN ('INTENT','SUCCEEDED','UNKNOWN')", name="status"))
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    task_id: Mapped[UUID] = mapped_column(ForeignKey("tasks.id"))
    operation_key: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)
    input_sha256: Mapped[str] = mapped_column(CHAR(64))
    revision: Mapped[str] = mapped_column(CHAR(64))
    status: Mapped[str] = mapped_column(Text)
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

class CodingEvidence(Base):
    __tablename__ = "coding_evidence"
    __table_args__ = (UniqueConstraint("task_id", "operation_key"), Index("coding_evidence_task_idx", "task_id"))
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    task_id: Mapped[UUID] = mapped_column(ForeignKey("tasks.id"))
    session_id: Mapped[UUID] = mapped_column(ForeignKey("coding_sessions.id"))
    operation_key: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)
    revision: Mapped[str] = mapped_column(CHAR(64))
    evidence_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

class CodingArtifact(Base):
    __tablename__ = "coding_artifacts"
    __table_args__ = (UniqueConstraint("task_id", "operation_key"),
        CheckConstraint("byte_size >= 0 AND byte_size <= 2000000", name="byte_size"))
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    task_id: Mapped[UUID] = mapped_column(ForeignKey("tasks.id"))
    operation_key: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)
    filename: Mapped[str] = mapped_column(Text)
    mime_type: Mapped[str] = mapped_column(Text)
    storage_key: Mapped[str] = mapped_column(Text, unique=True)
    sha256: Mapped[str] = mapped_column(CHAR(64))
    byte_size: Mapped[int] = mapped_column(BigInteger)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
