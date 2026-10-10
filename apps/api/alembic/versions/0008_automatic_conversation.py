"""Optional conversation repositories and task-free natural message receipts.

Revision ID: 0008_automatic_conversation
Revises: 0007_sessions_agents
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0008_automatic_conversation"
down_revision = "0007_sessions_agents"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint(op.f("ck_research_sources_source_type"), "research_sources", type_="check")
    op.create_check_constraint("source_type", "research_sources", "source_type IN ('search', 'webpage', 'repository')")
    op.add_column("conversations", sa.Column("repository_json", postgresql.JSONB(), nullable=True))
    op.add_column("conversations", sa.Column("repository_since_sequence", sa.BigInteger(), nullable=False, server_default=sa.text("0")))
    op.alter_column("conversation_messages", "task_id", existing_type=sa.Uuid(), nullable=True)
    op.alter_column("session_turns", "task_id", existing_type=sa.Uuid(), nullable=True)
    # Before repository selection existed, explicit coding turns carried the only
    # selection. Recover that context without altering immutable history/tasks.
    op.execute("""UPDATE conversations AS conversation SET repository_json = latest.repository
        FROM (SELECT DISTINCT ON (turn.session_id) turn.session_id,
            task.permissions_json->'coding'->'repository' AS repository
            FROM session_turns AS turn JOIN tasks AS task ON task.id = turn.task_id
            WHERE task.permissions_json->>'kind' = 'coding'
            AND task.permissions_json->'coding'->'repository' IS NOT NULL
            ORDER BY turn.session_id, turn.created_at DESC, turn.id DESC) AS latest
        WHERE conversation.id = latest.session_id""")


def downgrade():
    op.drop_constraint(op.f("ck_research_sources_source_type"), "research_sources", type_="check")
    op.create_check_constraint("source_type", "research_sources", "source_type IN ('search', 'webpage')")
    # Task-free receipts are immutable user history; never delete them to downgrade.
    op.alter_column("session_turns", "task_id", existing_type=sa.Uuid(), nullable=False)
    op.alter_column("conversation_messages", "task_id", existing_type=sa.Uuid(), nullable=False)
    op.drop_column("conversations", "repository_json")
    op.drop_column("conversations", "repository_since_sequence")
