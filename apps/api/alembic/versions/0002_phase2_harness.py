"""Add Phase 2 durable runs, audit intent and budget accounting.

Revision ID: 0002_phase2
Revises: 0001_phase1
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002_phase2"
down_revision = "0001_phase1"
branch_labels = None
depends_on = None


def _identity():
    return [sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())]


def upgrade() -> None:
    op.create_table("budget_reservations",
        *_identity(),
        sa.Column("task_id", sa.Uuid(), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False, unique=True),
        sa.Column("amount_usd", sa.Numeric(12, 4), nullable=False),
        sa.Column("max_steps", sa.Integer(), nullable=False),
        sa.Column("max_tool_calls", sa.Integer(), nullable=False),
        sa.Column("depth", sa.Integer(), nullable=False),
        sa.Column("deadline", sa.DateTime(timezone=True), nullable=False),
        sa.Column("settled_at", sa.DateTime(timezone=True)),
        sa.Column("charged_usd", sa.Numeric(12, 4)),
        sa.CheckConstraint("amount_usd > 0", name="amount"),
        sa.CheckConstraint("max_steps > 0 AND max_tool_calls > 0", name="limits"),
        sa.CheckConstraint("depth = 1", name="depth"),
    )
    op.create_index("budget_reservations_task_idx", "budget_reservations", ["task_id"])
    op.create_table("agent_runs",
        *_identity(),
        sa.Column("task_id", sa.Uuid(), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("definition_version_id", sa.Uuid(), sa.ForeignKey("agent_definition_versions.id"), nullable=False),
        sa.Column("definition_sha256", sa.CHAR(64), nullable=False),
        sa.Column("definition_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("brief_sha256", sa.CHAR(64), nullable=False),
        sa.Column("effective_permissions_json", postgresql.JSONB(), nullable=False),
        sa.Column("effective_budget_json", postgresql.JSONB(), nullable=False),
        sa.Column("budget_reservation_id", sa.Uuid(), sa.ForeignKey("budget_reservations.id"), nullable=False, unique=True),
        sa.Column("reserved_cost_usd", sa.Numeric(12, 4), nullable=False),
        sa.Column("measured_cost_usd", sa.Numeric(12, 4)),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("result_json", postgresql.JSONB()),
        sa.Column("error_code", sa.Text()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("status IN ('RUNNING', 'COMPLETED', 'PARTIAL', 'FAILED', 'BLOCKED', 'CANCELLED')", name="status"),
        sa.CheckConstraint("reserved_cost_usd > 0", name="reserved_cost"),
    )
    op.create_index("agent_runs_task_started_idx", "agent_runs", ["task_id", "started_at"])
    op.create_table("tool_calls",
        *_identity(),
        sa.Column("task_id", sa.Uuid(), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("agent_run_id", sa.Uuid(), sa.ForeignKey("agent_runs.id"), nullable=False),
        sa.Column("tool_name", sa.Text(), nullable=False),
        sa.Column("operation_key", sa.Text(), nullable=False),
        sa.Column("args_digest", sa.CHAR(64), nullable=False),
        sa.Column("intent_json_sanitized", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("result_json", postgresql.JSONB()),
        sa.Column("error_code", sa.Text()),
        sa.Column("evidence_ids", postgresql.JSONB(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("agent_run_id", "operation_key"),
        sa.CheckConstraint("status IN ('INTENT', 'SUCCEEDED', 'FAILED', 'BLOCKED', 'UNKNOWN')", name="status"),
    )
    op.create_index("tool_calls_task_started_idx", "tool_calls", ["task_id", "started_at"])
    op.create_table("budget_operations",
        *_identity(),
        sa.Column("reservation_id", sa.Uuid(), sa.ForeignKey("budget_reservations.id"), nullable=False),
        sa.Column("operation_key", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("max_cost_usd", sa.Numeric(12, 4), nullable=False),
        sa.Column("cost_usd", sa.Numeric(12, 4)),
        sa.Column("usage_json", postgresql.JSONB()),
        sa.Column("recorded_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("reservation_id", "operation_key"),
        sa.CheckConstraint("kind IN ('model', 'tool')", name="kind"),
        sa.CheckConstraint("max_cost_usd >= 0 AND (cost_usd IS NULL OR cost_usd >= 0)", name="cost"),
    )
    op.create_table("budget_transactions",
        *_identity(),
        sa.Column("task_id", sa.Uuid(), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("reservation_id", sa.Uuid(), sa.ForeignKey("budget_reservations.id"), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("amount_usd", sa.Numeric(12, 4), nullable=False),
        sa.UniqueConstraint("reservation_id", "kind"),
        sa.CheckConstraint("kind IN ('RESERVE', 'CHARGE', 'RELEASE')", name="kind"),
        sa.CheckConstraint("amount_usd >= 0", name="amount"),
    )
    op.create_index("budget_transactions_task_idx", "budget_transactions", ["task_id"])


def downgrade() -> None:
    op.drop_index("budget_transactions_task_idx", table_name="budget_transactions")
    op.drop_table("budget_transactions")
    op.drop_table("budget_operations")
    op.drop_index("tool_calls_task_started_idx", table_name="tool_calls")
    op.drop_table("tool_calls")
    op.drop_index("agent_runs_task_started_idx", table_name="agent_runs")
    op.drop_table("agent_runs")
    op.drop_index("budget_reservations_task_idx", table_name="budget_reservations")
    op.drop_table("budget_reservations")
