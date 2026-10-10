"""Ephemeral live reply text so the interface can stream a reply while it is written.

Revision ID: 0009_task_streams
Revises: 0008_automatic_conversation
"""
from alembic import op
import sqlalchemy as sa

revision = "0009_task_streams"
down_revision = "0008_automatic_conversation"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "task_streams",
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("epoch", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("text", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], name=op.f("fk_task_streams_task_id_tasks"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("task_id", name=op.f("pk_task_streams")),
    )


def downgrade():
    # Rows are transient copies of text that is also saved as the task's final message.
    op.drop_table("task_streams")
