"""Deliveries: a conversation's branch, pushed commits and pull request.

Revision ID: 0017_deliveries
Revises: 0016_tool_stream
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0017_deliveries"
down_revision = "0016_tool_stream"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "conversation_deliveries",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("conversation_id", sa.Uuid(), sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("repo_id", sa.Text(), nullable=False),
        sa.Column("base_branch", sa.Text(), nullable=True),
        sa.Column("base_sha", sa.Text(), nullable=True),
        sa.Column("branch", sa.Text(), nullable=True),
        sa.Column("head_sha", sa.Text(), nullable=True),
        sa.Column("state", sa.Text(), nullable=False, server_default="none"),
        sa.Column("pr_number", sa.Integer(), nullable=True),
        sa.Column("pr_url", sa.Text(), nullable=True),
        sa.Column("pr_node_id", sa.Text(), nullable=True),
        sa.Column("pr_json", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("unpublished_json", JSONB(), nullable=True),
        sa.Column("operations_json", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("pending_op_json", JSONB(), nullable=True),
        sa.Column("sync_error", sa.Text(), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.BigInteger(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("state IN ('none','branch','pull_request')", name="ck_conversation_deliveries_state"),
        sa.CheckConstraint("status IN ('active','archived')", name="ck_conversation_deliveries_status"),
    )
    op.create_index("ix_conversation_deliveries_conversation_id", "conversation_deliveries", ["conversation_id"])
    op.create_index("conversation_deliveries_active_idx", "conversation_deliveries", ["conversation_id"], unique=True,
                    postgresql_where=sa.text("status = 'active'"))
    op.create_index("conversation_deliveries_branch_idx", "conversation_deliveries", ["repo_id", "branch"])
    op.create_table(
        "delivery_commits",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("delivery_id", sa.Uuid(), sa.ForeignKey("conversation_deliveries.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sha", sa.Text(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("files", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("additions", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("deletions", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("task_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("delivery_id", "sha", name="uq_delivery_commits_delivery_id"),
    )
    op.create_index("ix_delivery_commits_delivery_id", "delivery_commits", ["delivery_id"])


def downgrade():
    op.drop_table("delivery_commits")
    op.drop_table("conversation_deliveries")
