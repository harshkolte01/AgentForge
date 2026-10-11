"""Worker presence (so the API can say whether anything will run a queued message) and two read-path indexes.

Revision ID: 0014_worker_presence_indexes
Revises: 0013_conversation_read_state
"""
from alembic import op
import sqlalchemy as sa

revision = "0014_worker_presence_indexes"
down_revision = "0013_conversation_read_state"
branch_labels = depends_on = None


def upgrade():
    op.create_table("worker_heartbeats",
        sa.Column("worker_id", sa.Text(), primary_key=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("version", sa.Text(), nullable=True))
    op.create_index("ix_worker_heartbeats_seen_at", "worker_heartbeats", ["seen_at"])
    # GET /tasks lists one owner's tasks newest first.
    op.create_index("tasks_owner_created_idx", "tasks", ["created_by", sa.literal_column("created_at DESC")])
    # The worker's claim query only ever wants queued or running jobs, in (available_at, id) order.
    op.create_index("task_jobs_claimable_idx", "task_jobs", ["available_at", "id"],
        postgresql_where=sa.text("status IN ('QUEUED','RUNNING')"))


def downgrade():
    op.drop_index("task_jobs_claimable_idx", table_name="task_jobs")
    op.drop_index("tasks_owner_created_idx", table_name="tasks")
    op.drop_index("ix_worker_heartbeats_seen_at", table_name="worker_heartbeats")
    op.drop_table("worker_heartbeats")
