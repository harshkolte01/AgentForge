"""The GitHub side of a conversation's repository work: its branch, pushed commits and pull request (see docs/delivery.md)."""
from datetime import datetime
from typing import Any
from uuid import UUID
from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Integer, Text, UniqueConstraint, Uuid, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base


class ConversationDelivery(Base):
    """One per conversation and repository while `status` is 'active'; 'reset' archives it and the next publish starts afresh."""
    __tablename__ = "conversation_deliveries"
    __table_args__ = (
        CheckConstraint("state IN ('none','branch','pull_request')", name="state"),
        CheckConstraint("status IN ('active','archived')", name="status"),
        Index("conversation_deliveries_active_idx", "conversation_id", unique=True, postgresql_where=text("status = 'active'")),
        Index("conversation_deliveries_branch_idx", "repo_id", "branch"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, server_default=text("gen_random_uuid()"))
    conversation_id: Mapped[UUID] = mapped_column(ForeignKey("conversations.id"), index=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(Text, server_default=text("'active'"))
    repo_id: Mapped[str] = mapped_column(Text)
    base_branch: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The commit the sandbox's repository was loaded at: every change is expressed on top of it.
    base_sha: Mapped[str | None] = mapped_column(Text, nullable=True)
    branch: Mapped[str | None] = mapped_column(Text, nullable=True)
    head_sha: Mapped[str | None] = mapped_column(Text, nullable=True)
    state: Mapped[str] = mapped_column(Text, server_default=text("'none'"))
    pr_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pr_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    pr_node_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    # What GitHub last said about the pull request (title, state, head, mergeable, checks, reviews, merge methods).
    pr_json: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    # Changes in the workspace not yet pushed, as last measured: {files, additions, deletions, diff_version, updated_at}.
    unpublished_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # Idempotency: the outcome of recent operations by key, and the one write whose outcome is being confirmed.
    operations_json: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    pending_op_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    sync_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(BigInteger, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class DeliveryCommit(Base):
    """A commit AgentForge pushed to the delivery's branch."""
    __tablename__ = "delivery_commits"
    __table_args__ = (UniqueConstraint("delivery_id", "sha"),)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, server_default=text("gen_random_uuid()"))
    delivery_id: Mapped[UUID] = mapped_column(ForeignKey("conversation_deliveries.id", ondelete="CASCADE"), index=True)
    sha: Mapped[str] = mapped_column(Text)
    message: Mapped[str] = mapped_column(Text)
    files: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    additions: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    deletions: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    task_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
