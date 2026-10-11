"""Durable queued turns, private context provenance and incremental summaries.

Revision ID: 0011_conversation_delivery
Revises: 0010_task_stream_revision
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0011_conversation_delivery"
down_revision = "0010_task_stream_revision"
branch_labels = depends_on = None


def col(name, target=None, **kw):
    return sa.Column(name, sa.Uuid(), *([sa.ForeignKey(target)] if target else []), **kw)


def upgrade():
    op.add_column("conversations", sa.Column("queue_paused", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.add_column("conversations", sa.Column("revision", sa.BigInteger(), nullable=False, server_default=sa.text("0")))
    op.add_column("conversations", sa.Column("dispatch_after", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_conversations_dispatch_after", "conversations", ["dispatch_after"])
    for name in ("execution_started_at", "execution_deadline"):
        op.add_column("tasks", sa.Column(name, sa.DateTime(timezone=True), nullable=True))
    op.execute("CREATE INDEX conversation_messages_search_idx ON conversation_messages USING gin (to_tsvector('simple'::regconfig, content))")
    op.create_table("session_turn_executions",
        col("turn_id", "session_turns.id", primary_key=True), col("session_id", "conversations.id", nullable=False),
        sa.Column("ordinal", sa.BigInteger(), nullable=False), col("task_id", "tasks.id", nullable=True, unique=True),
        col("assistant_message_id", "conversation_messages.id", nullable=True, unique=True),
        sa.Column("state", sa.Text(), nullable=False, server_default=sa.text("'queued'")),
        sa.Column("error_code", sa.Text(), nullable=True), sa.Column("context_json", JSONB(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("session_id", "ordinal"),
        sa.CheckConstraint("state IN ('queued','running','completed','partial','failed','cancelled','blocked')", name="state"))
    op.create_index("session_turn_executions_queue_idx", "session_turn_executions", ["session_id", "state", "ordinal"])
    op.create_table("session_turn_inputs", col("turn_id", "session_turns.id", primary_key=True),
        sa.Column("request_json", JSONB(), nullable=False), sa.Column("automatic_json", JSONB(), nullable=True))
    op.create_table("conversation_changes", col("session_id", "conversations.id", primary_key=True),
        sa.Column("sequence", sa.BigInteger(), primary_key=True), sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("payload_json", JSONB(), nullable=False))
    op.create_table("session_creation_receipts", col("owner_id", "users.id", primary_key=True),
        col("idempotency_key", primary_key=True), col("session_id", "conversations.id", nullable=False, unique=True),
        sa.Column("request_sha256", sa.Text(), nullable=False))
    op.create_table("conversation_summaries", col("id", primary_key=True),
        col("session_id", "conversations.id", nullable=False), sa.Column("through_ordinal", sa.BigInteger(), nullable=False),
        col("previous_id", "conversation_summaries.id", nullable=True), sa.Column("content_json", JSONB(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False), sa.Column("version", sa.BigInteger(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("session_id", "through_ordinal"))
    op.create_index("ix_conversation_summaries_session_id", "conversation_summaries", ["session_id"])
    op.create_table("conversation_summary_attempts", col("run_id", primary_key=True),
        col("task_id", "tasks.id", nullable=False, unique=True), sa.Column("status", sa.Text(), nullable=False))
    op.create_table("provider_inputs", sa.Column("operation_key", sa.Text(), primary_key=True),
        col("task_id", "tasks.id", nullable=False), sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("payload_json", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
    op.create_index("ix_provider_inputs_task_id", "provider_inputs", ["task_id"])
    op.create_table("tool_result_snapshots", col("id", primary_key=True), col("task_id", "tasks.id", nullable=False),
        sa.Column("operation_key", sa.Text(), nullable=False, unique=True), sa.Column("content", sa.Text(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False))
    op.create_index("ix_tool_result_snapshots_task_id", "tool_result_snapshots", ["task_id"])
    # Historical task-free receipts used adjacency. Resolve it ONCE at migration,
    # never at runtime after queued intake can interleave with assistant messages.
    op.execute("""INSERT INTO session_turn_executions(turn_id,session_id,ordinal,task_id,assistant_message_id,state)
        SELECT t.id,t.session_id,u.sequence,t.task_id,a.id,
        CASE WHEN a.id IS NOT NULL THEN COALESCE(lower(k.status),'completed') ELSE 'running' END
        FROM session_turns t JOIN conversation_messages u ON u.id=t.user_message_id
        LEFT JOIN tasks k ON k.id=t.task_id
        LEFT JOIN conversation_messages a ON a.session_id=t.session_id AND a.role='assistant'
          AND ((t.task_id IS NOT NULL AND a.task_id=t.task_id) OR
               (t.task_id IS NULL AND a.sequence=u.sequence+1))""")
    for table in ("session_turn_inputs", "session_creation_receipts", "conversation_summaries", "provider_inputs", "tool_result_snapshots", "conversation_changes"):
        op.execute(f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION agentforge_conversation_immutable()")


def downgrade():
    # Accepted queued work cannot be represented by the previous schema.
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT 1 FROM session_turn_inputs LIMIT 1")).first():
        raise RuntimeError("Restore a reviewed backup rather than discard accepted conversation work")
    for table in ("tool_result_snapshots", "provider_inputs", "conversation_summary_attempts", "conversation_summaries", "session_creation_receipts", "conversation_changes", "session_turn_inputs", "session_turn_executions"):
        op.drop_table(table)
    for name in ("execution_started_at", "execution_deadline"):
        op.drop_column("tasks", name)
    op.drop_index("conversation_messages_search_idx", table_name="conversation_messages")
    op.drop_index("ix_conversations_dispatch_after", table_name="conversations")
    for name in ("dispatch_after", "revision", "queue_paused"):
        op.drop_column("conversations", name)
