"""Phase 1 application tables from implementation plan section 14.4."""

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
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


Index(
    "tasks_project_status_created_idx",
    Task.project_id,
    Task.status,
    Task.created_at.desc(),
)
