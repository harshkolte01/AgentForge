"""A persistent sandbox per conversation, inline tool approvals and the approval mode.

Revision ID: 0012_agent_sandbox_approvals
Revises: 0011_conversation_delivery
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0012_agent_sandbox_approvals"
down_revision = "0011_conversation_delivery"
branch_labels = depends_on = None


def upgrade():
    op.add_column("conversations", sa.Column("sandbox_id", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("approval_mode", sa.Text(), nullable=False, server_default="ask"))
    op.create_check_constraint("ck_users_approval_mode", "users", "approval_mode IN ('ask','auto')")
    op.create_table(
        "tool_approvals",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("task_id", sa.Uuid(), sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("operation_key", sa.Text(), nullable=False),
        sa.Column("tool", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("detail_json", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("state", sa.Text(), nullable=False, server_default="PENDING"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("state IN ('PENDING','APPROVED','DENIED','CANCELLED')", name="ck_tool_approvals_state"),
        sa.UniqueConstraint("task_id", "operation_key", name="uq_tool_approvals_task_id"),
    )
    op.create_index("ix_tool_approvals_task_id", "tool_approvals", ["task_id"])


def downgrade():
    op.drop_table("tool_approvals")
    op.drop_constraint("ck_users_approval_mode", "users", type_="check")
    op.drop_column("users", "approval_mode")
    op.drop_column("conversations", "sandbox_id")
