"""Durable task plans, sequenced events and generation-fenced worker leases.

Revision ID: 0003_phase3
Revises: 0002_phase2
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0003_phase3"
down_revision = "0002_phase2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("event_sequence", sa.BigInteger(), nullable=False, server_default=sa.text("0")))
    op.add_column("tasks", sa.Column("permissions_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")))
    op.add_column("tasks", sa.Column("final_summary", sa.Text()))
    op.add_column("tasks", sa.Column("final_result", postgresql.JSONB()))
    op.add_column("tasks", sa.Column("error_code", sa.Text()))
    op.create_table("task_steps",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("task_id", sa.Uuid(), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("step_key", sa.Text(), nullable=False),
        sa.Column("agent_type", sa.Text(), nullable=False),
        sa.Column("definition_version", sa.Integer(), nullable=False),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("acceptance_criteria", postgresql.JSONB(), nullable=False),
        sa.Column("depends_on", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'PENDING'")),
        sa.Column("agent_run_id", sa.Uuid(), unique=True),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("result_json", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("task_id", "step_key"),
        sa.CheckConstraint("status IN ('PENDING','RUNNING','COMPLETED','PARTIAL','FAILED','BLOCKED','CANCELLED')", name="status"),
        sa.CheckConstraint("definition_version >= 1 AND attempt >= 1 AND attempt <= 3", name="bounds"))
    op.create_index("ix_task_steps_task_id", "task_steps", ["task_id"])
    op.create_table("task_events",
        sa.Column("task_id", sa.Uuid(), sa.ForeignKey("tasks.id"), primary_key=True),
        sa.Column("sequence", sa.BigInteger(), primary_key=True),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("sequence > 0", name="sequence"))
    op.create_table("task_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("task_id", sa.Uuid(), sa.ForeignKey("tasks.id"), nullable=False, unique=True),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'QUEUED'")),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("lease_owner", sa.Text()),
        sa.Column("lease_generation", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("status IN ('QUEUED','RUNNING','COMPLETED')", name="status"),
        sa.CheckConstraint("lease_generation >= 0 AND attempts >= 0", name="bounds"))
    op.create_index("task_jobs_claim_idx", "task_jobs", ["status", "available_at", "lease_expires_at"])


def downgrade() -> None:
    op.drop_table("task_jobs")
    op.drop_table("task_events")
    op.drop_table("task_steps")
    for name in ("error_code", "final_result", "final_summary", "permissions_json", "event_sequence"):
        op.drop_column("tasks", name)
