"""Owner-scoped conversations; immutable sequenced messages link durable tasks."""
from datetime import datetime
from typing import Any
from uuid import UUID
from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Index, Text, UniqueConstraint, Uuid, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (CheckConstraint("mode IN ('research','general','coding')", name="mode"),
        CheckConstraint("last_sequence >= 0", name="sequence"), Index("conversations_owner_updated_idx", "owner_id", "updated_at"))
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, server_default=text("gen_random_uuid()"))
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id"))
    title: Mapped[str] = mapped_column(Text)
    mode: Mapped[str] = mapped_column(Text)
    archived: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    active_task_id: Mapped[UUID | None] = mapped_column(ForeignKey("tasks.id"), unique=True)
    last_sequence: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    summary_json: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    repository_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # The persistent sandbox the agent works in for this conversation (created on first use).
    sandbox_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    repository_since_sequence: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    queue_paused: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    revision: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    # The highest message sequence its owner has seen; a finished reply beyond it is "unread" (see SessionRepository.activities).
    read_sequence: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    dispatch_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    @property
    def repository(self):
        return self.repository_json


class ConversationMessage(Base):
    __tablename__ = "conversation_messages"
    __table_args__ = (UniqueConstraint("session_id", "sequence"), UniqueConstraint("task_id", "role"),
        CheckConstraint("role IN ('user','assistant')", name="role"), CheckConstraint("sequence > 0", name="sequence"))
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, server_default=text("gen_random_uuid()"))
    session_id: Mapped[UUID] = mapped_column(ForeignKey("conversations.id"), index=True)
    sequence: Mapped[int] = mapped_column(BigInteger)
    role: Mapped[str] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text)
    task_id: Mapped[UUID | None] = mapped_column(ForeignKey("tasks.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SessionTurn(Base):
    __tablename__ = "session_turns"
    __table_args__ = (UniqueConstraint("session_id", "idempotency_key"),)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, server_default=text("gen_random_uuid()"))
    session_id: Mapped[UUID] = mapped_column(ForeignKey("conversations.id"), index=True)
    idempotency_key: Mapped[UUID] = mapped_column(Uuid)
    request_sha256: Mapped[str] = mapped_column(Text)
    task_id: Mapped[UUID | None] = mapped_column(ForeignKey("tasks.id"), unique=True, nullable=True)
    user_message_id: Mapped[UUID] = mapped_column(ForeignKey("conversation_messages.id"), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


Index("conversation_messages_search_idx", func.to_tsvector(text("'simple'::regconfig"), ConversationMessage.content), postgresql_using="gin")


class TurnExecution(Base):
    """Mutable delivery state beside the immutable admission receipt."""
    __tablename__ = "session_turn_executions"
    __table_args__ = (UniqueConstraint("session_id", "ordinal"),
        CheckConstraint("state IN ('queued','running','completed','partial','failed','cancelled','blocked')", name="state"),
        Index("session_turn_executions_queue_idx", "session_id", "state", "ordinal"))
    turn_id: Mapped[UUID] = mapped_column(ForeignKey("session_turns.id"), primary_key=True)
    session_id: Mapped[UUID] = mapped_column(ForeignKey("conversations.id"))
    ordinal: Mapped[int] = mapped_column(BigInteger)
    task_id: Mapped[UUID | None] = mapped_column(ForeignKey("tasks.id"), unique=True, nullable=True)
    assistant_message_id: Mapped[UUID | None] = mapped_column(ForeignKey("conversation_messages.id"), unique=True, nullable=True)
    state: Mapped[str] = mapped_column(Text, server_default=text("'queued'"))
    error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    context_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class TurnInput(Base):
    __tablename__ = "session_turn_inputs"
    turn_id: Mapped[UUID] = mapped_column(ForeignKey("session_turns.id"), primary_key=True)
    request_json: Mapped[dict] = mapped_column(JSONB)
    automatic_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class ConversationChange(Base):
    __tablename__ = "conversation_changes"
    session_id: Mapped[UUID] = mapped_column(ForeignKey("conversations.id"), primary_key=True)
    sequence: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    event_type: Mapped[str] = mapped_column(Text)
    payload_json: Mapped[dict] = mapped_column(JSONB)


class SessionCreationReceipt(Base):
    __tablename__ = "session_creation_receipts"
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    idempotency_key: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    session_id: Mapped[UUID] = mapped_column(ForeignKey("conversations.id"), unique=True)
    request_sha256: Mapped[str] = mapped_column(Text)


class ConversationSummary(Base):
    __tablename__ = "conversation_summaries"
    __table_args__ = (UniqueConstraint("session_id", "through_ordinal"),)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    session_id: Mapped[UUID] = mapped_column(ForeignKey("conversations.id"), index=True)
    through_ordinal: Mapped[int] = mapped_column(BigInteger)
    previous_id: Mapped[UUID | None] = mapped_column(ForeignKey("conversation_summaries.id"), nullable=True)
    content_json: Mapped[dict] = mapped_column(JSONB)
    sha256: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(BigInteger, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SummaryAttempt(Base):
    __tablename__ = "conversation_summary_attempts"
    run_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    task_id: Mapped[UUID] = mapped_column(ForeignKey("tasks.id"), unique=True)
    status: Mapped[str] = mapped_column(Text)


class ProviderInput(Base):
    """Private exact input audit, never included in public activity/telemetry."""
    __tablename__ = "provider_inputs"
    operation_key: Mapped[str] = mapped_column(Text, primary_key=True)
    task_id: Mapped[UUID] = mapped_column(ForeignKey("tasks.id"), index=True)
    sha256: Mapped[str] = mapped_column(Text)
    payload_json: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ToolResultSnapshot(Base):
    __tablename__ = "tool_result_snapshots"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    task_id: Mapped[UUID] = mapped_column(ForeignKey("tasks.id"), index=True)
    operation_key: Mapped[str] = mapped_column(Text, unique=True)
    content: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(Text)


class ToolApproval(Base):
    """One 'may I?' the agent put to the person before a tool that changes something (see tools/approvals.py)."""
    __tablename__ = "tool_approvals"
    __table_args__ = (CheckConstraint("state IN ('PENDING','APPROVED','DENIED','CANCELLED')", name="state"),
        UniqueConstraint("task_id", "operation_key"))
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, server_default=text("gen_random_uuid()"))
    task_id: Mapped[UUID] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    operation_key: Mapped[str] = mapped_column(Text)
    tool: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text)
    detail_json: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    state: Mapped[str] = mapped_column(Text, server_default=text("'PENDING'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
