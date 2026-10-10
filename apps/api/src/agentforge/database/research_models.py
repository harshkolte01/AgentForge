"""Append-only task-owned research sources, evidence and private artifacts."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import CHAR, BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Text, UniqueConstraint, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class ResearchSource(Base):
    __tablename__ = "research_sources"
    __table_args__ = (
        CheckConstraint("source_type IN ('search', 'webpage')", name="source_type"),
        CheckConstraint("fetch_status IN ('SEARCH_ONLY', 'FETCHED', 'FAILED')", name="fetch_status"),
        UniqueConstraint("task_id", "operation_key", "ordinal"),
        Index("research_sources_task_time_idx", "task_id", "retrieved_at"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    task_id: Mapped[UUID] = mapped_column(ForeignKey("tasks.id"))
    agent_run_id: Mapped[UUID | None] = mapped_column(ForeignKey("agent_runs.id"))
    operation_key: Mapped[str] = mapped_column(Text)
    ordinal: Mapped[int] = mapped_column(BigInteger)
    url: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text)
    content_sha256: Mapped[str] = mapped_column(CHAR(64))
    source_type: Mapped[str] = mapped_column(Text)
    fetch_status: Mapped[str] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(Text)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ResearchEvidence(Base):
    __tablename__ = "research_evidence"
    __table_args__ = (Index("research_evidence_task_idx", "task_id"),)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    task_id: Mapped[UUID] = mapped_column(ForeignKey("tasks.id"))
    source_id: Mapped[UUID | None] = mapped_column(ForeignKey("research_sources.id"))
    kind: Mapped[str] = mapped_column(Text)
    validation_state: Mapped[str] = mapped_column(Text)
    evidence_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ResearchArtifact(Base):
    __tablename__ = "research_artifacts"
    __table_args__ = (
        Index("research_artifacts_task_time_idx", "task_id", "created_at"),
        UniqueConstraint("task_id", "operation_key"),
        CheckConstraint("byte_size >= 0 AND byte_size <= 2000000", name="byte_size"),
    )
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
