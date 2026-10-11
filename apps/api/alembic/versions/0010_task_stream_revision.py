"""Revision and kind on the live text row, so clients can order updates and recognise resets.

Revision ID: 0010_task_stream_revision
Revises: 0009_task_streams
"""
from alembic import op
import sqlalchemy as sa

revision = "0010_task_stream_revision"
down_revision = "0009_task_streams"
branch_labels = None
depends_on = None


def upgrade():
    # Additive: existing rows (transient copies) read as revision 0 of a progress note.
    op.add_column("task_streams", sa.Column("revision", sa.Integer(), server_default=sa.text("0"), nullable=False))
    op.add_column("task_streams", sa.Column("kind", sa.Text(), server_default=sa.text("'progress'"), nullable=False))
    op.create_check_constraint("kind", "task_streams", "kind IN ('progress','answer')")


def downgrade():
    op.drop_constraint(op.f("ck_task_streams_kind"), "task_streams", type_="check")
    op.drop_column("task_streams", "kind")
    op.drop_column("task_streams", "revision")
