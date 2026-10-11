"""Phase 1 entities and Phase 2 child audit/accounting tables."""

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CHAR,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class _UUIDTimestampMixin:
    id: Mapped[UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class User(_UUIDTimestampMixin, Base):
    __tablename__ = "users"

    github_user_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    display_name: Mapped[str] = mapped_column(Text)
    # "ask": the agent asks before anything that changes something. "auto": it never asks.
    approval_mode: Mapped[str] = mapped_column(Text, server_default=text("'ask'"))


class Project(_UUIDTimestampMixin, Base):
    __tablename__ = "projects"
    __table_args__ = (Index("projects_owner_idx", "owner_id"),)

    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(Text)


class AgentDefinition(_UUIDTimestampMixin, Base):
    __tablename__ = "agent_definitions"
    __table_args__ = (
        CheckConstraint("namespace IN ('builtin', 'project')", name="namespace"),
        CheckConstraint(
            "(namespace = 'builtin' AND project_id IS NULL) OR "
            "(namespace = 'project' AND project_id IS NOT NULL)",
            name="project_scope",
        ),
        CheckConstraint("current_version >= 1", name="current_version"),
        Index(
            "agent_defs_builtin_unique",
            "agent_key",
            unique=True,
            postgresql_where=text("namespace = 'builtin'"),
        ),
        Index(
            "agent_defs_project_unique",
            "project_id",
            "agent_key",
            unique=True,
            postgresql_where=text("namespace = 'project'"),
        ),
    )

    project_id: Mapped[UUID | None] = mapped_column(ForeignKey("projects.id"))
    namespace: Mapped[str] = mapped_column(Text)
    agent_key: Mapped[str] = mapped_column(Text)
    current_version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentDefinitionVersion(_UUIDTimestampMixin, Base):
    __tablename__ = "agent_definition_versions"
    __table_args__ = (
        CheckConstraint("version >= 1", name="version"),
        UniqueConstraint("definition_id", "version"),
    )

    definition_id: Mapped[UUID] = mapped_column(ForeignKey("agent_definitions.id"))
    version: Mapped[int] = mapped_column(Integer)
    definition_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    sha256: Mapped[str] = mapped_column(CHAR(64))
    created_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))


class Task(_UUIDTimestampMixin, Base):
    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint(
            "status IN ('CREATED', 'PLANNING', 'RUNNING', 'VERIFYING', 'RETRYING', "
            "'AWAITING_APPROVAL', 'COMPLETED', 'PARTIAL', 'FAILED', 'CANCELLED')",
            name="status",
        ),
        CheckConstraint("max_cost_usd > 0", name="max_cost_usd"),
        CheckConstraint("max_wall_time_sec > 0", name="max_wall_time_sec"),
    )

    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id"))
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    goal: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    thread_id: Mapped[str] = mapped_column(Text, unique=True)
    max_cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 4))
    max_wall_time_sec: Mapped[int] = mapped_column(Integer)
    execution_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    execution_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    event_sequence: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    permissions_json: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    final_summary: Mapped[str | None] = mapped_column(Text)
    final_result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error_code: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


Index(
    "tasks_project_status_created_idx",
    Task.project_id,
    Task.status,
    Task.created_at.desc(),
)
# GET /tasks: one owner's tasks, newest first.
Index("tasks_owner_created_idx", Task.created_by, Task.created_at.desc())


class BudgetReservation(_UUIDTimestampMixin, Base):
    """One durable child slice; created before its run to fail closed on crashes."""

    __tablename__ = "budget_reservations"
    __table_args__ = (
        CheckConstraint("amount_usd > 0", name="amount"),
        CheckConstraint("max_steps > 0 AND max_tool_calls > 0", name="limits"),
        CheckConstraint("depth IN (1, 2)", name="depth"),
        Index("budget_reservations_task_idx", "task_id"),
    )
    task_id: Mapped[UUID] = mapped_column(ForeignKey("tasks.id"))
    run_id: Mapped[UUID] = mapped_column(Uuid, unique=True)
    amount_usd: Mapped[Decimal] = mapped_column(Numeric(12, 4))
    max_steps: Mapped[int] = mapped_column(Integer)
    max_tool_calls: Mapped[int] = mapped_column(Integer)
    depth: Mapped[int] = mapped_column(Integer)
    deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    charged_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))


class AgentRun(_UUIDTimestampMixin, Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        CheckConstraint("status IN ('RUNNING', 'COMPLETED', 'PARTIAL', 'FAILED', 'BLOCKED', 'CANCELLED')", name="status"),
        CheckConstraint("reserved_cost_usd > 0", name="reserved_cost"),
        Index("agent_runs_task_started_idx", "task_id", "started_at"),
    )
    task_id: Mapped[UUID] = mapped_column(ForeignKey("tasks.id"))
    parent_run_id: Mapped[UUID | None] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    assignment_title: Mapped[str | None] = mapped_column(Text)
    assignment_prompt: Mapped[str | None] = mapped_column(Text)
    definition_version_id: Mapped[UUID] = mapped_column(ForeignKey("agent_definition_versions.id"))
    definition_sha256: Mapped[str] = mapped_column(CHAR(64))
    definition_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    brief_sha256: Mapped[str] = mapped_column(CHAR(64))
    effective_permissions_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    effective_budget_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    budget_reservation_id: Mapped[UUID] = mapped_column(ForeignKey("budget_reservations.id"), unique=True)
    reserved_cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 4))
    measured_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    status: Mapped[str] = mapped_column(Text)
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error_code: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ToolCall(_UUIDTimestampMixin, Base):
    __tablename__ = "tool_calls"
    __table_args__ = (
        UniqueConstraint("agent_run_id", "operation_key"),
        CheckConstraint("status IN ('INTENT', 'SUCCEEDED', 'FAILED', 'BLOCKED', 'UNKNOWN')", name="status"),
        Index("tool_calls_task_started_idx", "task_id", "started_at"),
    )
    task_id: Mapped[UUID] = mapped_column(ForeignKey("tasks.id"))
    agent_run_id: Mapped[UUID] = mapped_column(ForeignKey("agent_runs.id"))
    tool_name: Mapped[str] = mapped_column(Text)
    operation_key: Mapped[str] = mapped_column(Text)
    args_digest: Mapped[str] = mapped_column(CHAR(64))
    intent_json_sanitized: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(Text)
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error_code: Mapped[str | None] = mapped_column(Text)
    evidence_ids: Mapped[list[str]] = mapped_column(JSONB, default=list)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BudgetOperation(_UUIDTimestampMixin, Base):
    __tablename__ = "budget_operations"
    __table_args__ = (
        UniqueConstraint("reservation_id", "operation_key"),
        CheckConstraint("kind IN ('model', 'tool')", name="kind"),
        CheckConstraint("max_cost_usd >= 0 AND (cost_usd IS NULL OR cost_usd >= 0)", name="cost"),
    )
    reservation_id: Mapped[UUID] = mapped_column(ForeignKey("budget_reservations.id"))
    operation_key: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)
    max_cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 4))
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    usage_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    recorded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BudgetTransaction(_UUIDTimestampMixin, Base):
    __tablename__ = "budget_transactions"
    __table_args__ = (
        UniqueConstraint("reservation_id", "kind"),
        CheckConstraint("kind IN ('RESERVE', 'CHARGE', 'RELEASE')", name="kind"),
        CheckConstraint("amount_usd >= 0", name="amount"),
        Index("budget_transactions_task_idx", "task_id"),
    )
    task_id: Mapped[UUID] = mapped_column(ForeignKey("tasks.id"))
    reservation_id: Mapped[UUID] = mapped_column(ForeignKey("budget_reservations.id"))
    kind: Mapped[str] = mapped_column(Text)
    amount_usd: Mapped[Decimal] = mapped_column(Numeric(12, 4))


# Register split phase modules in the shared Alembic metadata.
from .phase3 import TaskEvent, TaskJob, TaskStep, TaskStream, WorkerHeartbeat  # noqa: E402,F401
from .research_models import ResearchArtifact, ResearchEvidence, ResearchSource  # noqa: E402,F401
from .coding import CodingSession, CodingSnapshot, CodingOperation, CodingEvidence, CodingArtifact  # noqa: E402,F401
from .identity import AuthSession, OAuthTransaction, GitHubCredential, GitHubInstallation, GitHubRepositoryGrant, GitHubWebhookDelivery, Approval, ExternalOperation  # noqa: E402,F401
from .sessions import Conversation, ConversationMessage, SessionTurn  # noqa: E402,F401
from .plugins import PluginConnection  # noqa: E402,F401
from .skills import SkillSetting, UserSkill  # noqa: E402,F401
from .delivery import ConversationDelivery, DeliveryCommit  # noqa: E402,F401
