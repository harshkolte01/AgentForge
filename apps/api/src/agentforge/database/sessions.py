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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ConversationMessage(Base):
    __tablename__ = "conversation_messages"
    __table_args__ = (UniqueConstraint("session_id", "sequence"), UniqueConstraint("task_id", "role"),
        CheckConstraint("role IN ('user','assistant')", name="role"), CheckConstraint("sequence > 0", name="sequence"))
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, server_default=text("gen_random_uuid()"))
    session_id: Mapped[UUID] = mapped_column(ForeignKey("conversations.id"), index=True)
    sequence: Mapped[int] = mapped_column(BigInteger)
    role: Mapped[str] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text)
    task_id: Mapped[UUID] = mapped_column(ForeignKey("tasks.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SessionTurn(Base):
    __tablename__ = "session_turns"
    __table_args__ = (UniqueConstraint("session_id", "idempotency_key"),)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, server_default=text("gen_random_uuid()"))
    session_id: Mapped[UUID] = mapped_column(ForeignKey("conversations.id"), index=True)
    idempotency_key: Mapped[UUID] = mapped_column(Uuid)
    request_sha256: Mapped[str] = mapped_column(Text)
    task_id: Mapped[UUID] = mapped_column(ForeignKey("tasks.id"), unique=True)
    user_message_id: Mapped[UUID] = mapped_column(ForeignKey("conversation_messages.id"), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
