"""Durable identities, encrypted credentials and exact external effect receipts."""
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4
from sqlalchemy import CHAR, BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Text, UniqueConstraint, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base

class IdentityRecord:
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

class AuthSession(IdentityRecord, Base):
    __tablename__ = "auth_sessions"
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(CHAR(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

class OAuthTransaction(IdentityRecord, Base):
    __tablename__ = "oauth_transactions"
    state_hash: Mapped[str] = mapped_column(CHAR(64), unique=True)
    binding_hash: Mapped[str] = mapped_column(CHAR(64))
    verifier_encrypted: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

class GitHubCredential(IdentityRecord, Base):
    __tablename__ = "github_credentials"
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), unique=True)
    access_encrypted: Mapped[str] = mapped_column(Text)
    refresh_encrypted: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    refresh_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    refresh_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

class GitHubInstallation(Base):
    __tablename__ = "github_installations"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    account_id: Mapped[int] = mapped_column(BigInteger)
    account_login: Mapped[str] = mapped_column(Text)
    permissions_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    repository_selection: Mapped[str] = mapped_column(Text)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

class GitHubRepositoryGrant(IdentityRecord, Base):
    __tablename__ = "github_repository_grants"
    __table_args__ = (UniqueConstraint("user_id", "repository_id"),)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    installation_id: Mapped[int] = mapped_column(ForeignKey("github_installations.id"))
    repository_id: Mapped[int] = mapped_column(BigInteger)
    full_name: Mapped[str] = mapped_column(Text)
    permissions_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

class GitHubWebhookDelivery(Base):
    __tablename__ = "github_webhook_deliveries"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    event: Mapped[str] = mapped_column(Text)
    body_sha256: Mapped[str] = mapped_column(CHAR(64))
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

class Approval(IdentityRecord, Base):
    __tablename__ = "approvals"
    __table_args__ = (CheckConstraint("state IN ('PENDING','APPROVED','DENIED','EXPIRED','EXECUTING','CONSUMED','INVALIDATED')", name="state"),
        UniqueConstraint("task_id", "action_hash"), Index("approvals_user_state_idx", "user_id", "state"))
    task_id: Mapped[UUID] = mapped_column(ForeignKey("tasks.id"))
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    action_hash: Mapped[str] = mapped_column(CHAR(64))
    action_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    state: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    execution_token: Mapped[str | None] = mapped_column(Text)
    execution_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

class ExternalOperation(IdentityRecord, Base):
    __tablename__ = "external_operations"
    __table_args__ = (CheckConstraint("status IN ('PREPARED','SENT','CONFIRMED','UNKNOWN','FAILED')", name="status"),)
    task_id: Mapped[UUID] = mapped_column(ForeignKey("tasks.id"))
    approval_id: Mapped[UUID] = mapped_column(ForeignKey("approvals.id"))
    operation_key: Mapped[str] = mapped_column(Text, unique=True)
    kind: Mapped[str] = mapped_column(Text)
    input_sha256: Mapped[str] = mapped_column(CHAR(64))
    status: Mapped[str] = mapped_column(Text)
    request_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    response_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
