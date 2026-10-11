"""Live tool calls on the live text row: the call being written (its arguments) or run (its output).

Revision ID: 0016_tool_stream
Revises: 0015_delegated_runs
"""
from alembic import op
import sqlalchemy as sa

revision = "0016_tool_stream"
down_revision = "0015_delegated_runs"
branch_labels = depends_on = None


def upgrade():
    # Additive: existing rows (transient copies of prose) keep reading as prose, with no tool.
    op.add_column("task_streams", sa.Column("tool", sa.Text(), nullable=True))
    op.add_column("task_streams", sa.Column("call_id", sa.Text(), nullable=True))
    op.add_column("task_streams", sa.Column("detail", sa.Text(), nullable=True))
    op.drop_constraint(op.f("ck_task_streams_kind"), "task_streams", type_="check")
    op.create_check_constraint("kind", "task_streams", "kind IN ('progress','answer','tool','output')")


def downgrade():
    # Rows are transient; a tool row has no meaning without the columns, so it is cleared rather than converted.
    op.execute("UPDATE task_streams SET kind = 'progress', text = '' WHERE kind IN ('tool', 'output')")
    op.drop_constraint(op.f("ck_task_streams_kind"), "task_streams", type_="check")
    op.create_check_constraint("kind", "task_streams", "kind IN ('progress','answer')")
    op.drop_column("task_streams", "detail")
    op.drop_column("task_streams", "call_id")
    op.drop_column("task_streams", "tool")
