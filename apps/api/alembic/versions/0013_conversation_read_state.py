"""Remember how much of each conversation its owner has seen, so the sidebar can flag a finished reply they have not read.

Revision ID: 0013_conversation_read_state
Revises: 0012_agent_sandbox_approvals
"""
from alembic import op
import sqlalchemy as sa

revision = "0013_conversation_read_state"
down_revision = "0012_agent_sandbox_approvals"
branch_labels = depends_on = None


def upgrade():
    op.add_column("conversations", sa.Column("read_sequence", sa.BigInteger(), nullable=False, server_default="0"))
    # Everything that exists now counts as seen: only replies that arrive from here on can be unread. updated_at is
    # deliberately left alone, so the sidebar order does not change.
    op.execute("UPDATE conversations SET read_sequence = last_sequence")


def downgrade():
    op.drop_column("conversations", "read_sequence")
