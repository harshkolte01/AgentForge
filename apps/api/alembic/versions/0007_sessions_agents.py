"""Durable conversation turns and immutable message receipts.
Revision ID: 0007_sessions_agents
Revises: 0006_identity_github
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
revision = "0007_sessions_agents"
down_revision = "0006_identity_github"
branch_labels = None
depends_on = None

def _id():
    return sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()"))
def _time(name="created_at"):
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())
def upgrade():
    op.create_table("conversations", _id(),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("title", sa.Text(), nullable=False), sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("active_task_id", sa.Uuid(), sa.ForeignKey("tasks.id"), nullable=True, unique=True),
        sa.Column("last_sequence", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("summary_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        _time(), _time("updated_at"), sa.CheckConstraint("mode IN ('research','general','coding')", name="mode"),
        sa.CheckConstraint("last_sequence >= 0", name="sequence"))
    op.create_index("conversations_owner_updated_idx", "conversations", ["owner_id", "updated_at"])
    op.create_table("conversation_messages", _id(),
        sa.Column("session_id", sa.Uuid(), sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False), sa.Column("role", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("task_id", sa.Uuid(), sa.ForeignKey("tasks.id"), nullable=False), _time(),
        sa.UniqueConstraint("session_id", "sequence"), sa.UniqueConstraint("task_id", "role"),
        sa.CheckConstraint("role IN ('user','assistant')", name="role"), sa.CheckConstraint("sequence > 0", name="sequence"))
    op.create_index("ix_conversation_messages_session_id", "conversation_messages", ["session_id"])
    op.create_table("session_turns", _id(),
        sa.Column("session_id", sa.Uuid(), sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column("idempotency_key", sa.Uuid(), nullable=False), sa.Column("request_sha256", sa.Text(), nullable=False),
        sa.Column("task_id", sa.Uuid(), sa.ForeignKey("tasks.id"), nullable=False, unique=True),
        sa.Column("user_message_id", sa.Uuid(), sa.ForeignKey("conversation_messages.id"), nullable=False, unique=True),
        _time(), sa.UniqueConstraint("session_id", "idempotency_key"))
    op.create_index("ix_session_turns_session_id", "session_turns", ["session_id"])
    op.execute("""CREATE FUNCTION agentforge_conversation_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'conversation receipt is immutable'; END; $$""")
    for table in ("conversation_messages", "session_turns"):
        op.execute(f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION agentforge_conversation_immutable()")

def downgrade():
    for table in ("session_turns", "conversation_messages", "conversations"):
        op.drop_table(table)
    op.execute("DROP FUNCTION agentforge_conversation_immutable()")
