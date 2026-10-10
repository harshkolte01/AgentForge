"""Append-only research retrieval/evidence and private artifact references.

Revision ID: 0004_phase3_research
Revises: 0003_phase3
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0004_phase3_research"
down_revision = "0003_phase3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("research_sources",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("task_id", sa.Uuid(), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("agent_run_id", sa.Uuid(), sa.ForeignKey("agent_runs.id"), nullable=True),
        sa.Column("operation_key", sa.Text(), nullable=False),
        sa.Column("ordinal", sa.BigInteger(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.CHAR(64), nullable=False),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("fetch_status", sa.Text(), nullable=False),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("source_type IN ('search', 'webpage')", name="source_type"),
        sa.CheckConstraint("fetch_status IN ('SEARCH_ONLY', 'FETCHED', 'FAILED')", name="fetch_status"),
        sa.UniqueConstraint("task_id", "operation_key", "ordinal"))
    op.create_index("research_sources_task_time_idx", "research_sources", ["task_id", "retrieved_at"])
    op.create_table("research_evidence",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("task_id", sa.Uuid(), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("source_id", sa.Uuid(), sa.ForeignKey("research_sources.id"), nullable=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("validation_state", sa.Text(), nullable=False),
        sa.Column("evidence_json", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_index("research_evidence_task_idx", "research_evidence", ["task_id"])
    op.create_table("research_artifacts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("task_id", sa.Uuid(), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("operation_key", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("mime_type", sa.Text(), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False, unique=True),
        sa.Column("sha256", sa.CHAR(64), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("byte_size >= 0 AND byte_size <= 2000000", name="byte_size"),
        sa.UniqueConstraint("task_id", "operation_key"))
    op.create_index("research_artifacts_task_time_idx", "research_artifacts", ["task_id", "created_at"])
    # ORM conventions are insufficient: these retrieval snapshots must not be
    # silently rewritten by a later worker or SQL update. Explicit downgrade is
    # the only schema-level removal path in this migration.
    op.execute("""CREATE FUNCTION agentforge_research_immutable() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'research records are immutable'; END; $$""")
    for table in ("research_sources", "research_evidence", "research_artifacts"):
        op.execute(f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
                   "FOR EACH ROW EXECUTE FUNCTION agentforge_research_immutable()")


def downgrade() -> None:
    for table in ("research_artifacts", "research_evidence", "research_sources"):
        op.drop_table(table)
    op.execute("DROP FUNCTION agentforge_research_immutable()")
