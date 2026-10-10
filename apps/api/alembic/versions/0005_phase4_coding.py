"""Durable binary-safe coding workspaces and immutable objective evidence.
Revision ID: 0005_phase4_coding
Revises: 0004_phase3_research
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
revision = "0005_phase4_coding"
down_revision = "0004_phase3_research"
branch_labels = None
depends_on = None

def _id():
    return sa.Column("id", sa.Uuid(), primary_key=True)
def _task():
    return sa.Column("task_id", sa.Uuid(), sa.ForeignKey("tasks.id"), nullable=False)
def _time():
    return sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())
def _text(name):
    return sa.Column(name, sa.Text(), nullable=False)
def _revision():
    return sa.Column("revision", sa.CHAR(64), nullable=False)

def upgrade():
    op.create_table("coding_sessions", _id(), _task(), _text("repo_id"),
        sa.Column("commit_sha", sa.CHAR(40), nullable=False),
        sa.Column("baseline_revision", sa.CHAR(64), nullable=False), _revision(), _time(), sa.UniqueConstraint("task_id"))
    op.create_table("coding_snapshots", _id(), _task(),
        sa.Column("session_id", sa.Uuid(), sa.ForeignKey("coding_sessions.id"), nullable=False),
        _revision(), sa.Column("files_json", postgresql.JSONB(), nullable=False), _time(),
        sa.UniqueConstraint("session_id", "revision"))
    op.create_table("coding_operations", _id(), _task(), _text("operation_key"), _text("kind"),
        sa.Column("input_sha256", sa.CHAR(64), nullable=False), _revision(), _text("status"),
        sa.Column("result_json", postgresql.JSONB(), nullable=True), _time(),
        sa.UniqueConstraint("task_id", "operation_key"),
        sa.CheckConstraint("status IN ('INTENT','SUCCEEDED','UNKNOWN')", name="status"))
    op.create_table("coding_evidence", _id(), _task(),
        sa.Column("session_id", sa.Uuid(), sa.ForeignKey("coding_sessions.id"), nullable=False),
        _text("operation_key"), _text("kind"), _revision(),
        sa.Column("evidence_json", postgresql.JSONB(), nullable=False), _time(),
        sa.UniqueConstraint("task_id", "operation_key"))
    op.create_index("coding_evidence_task_idx", "coding_evidence", ["task_id"])
    op.create_table("coding_artifacts", _id(), _task(), _text("operation_key"), _text("kind"),
        _text("filename"), _text("mime_type"), sa.Column("storage_key", sa.Text(), nullable=False, unique=True),
        sa.Column("sha256", sa.CHAR(64), nullable=False), sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(), nullable=False), _time(),
        sa.UniqueConstraint("task_id", "operation_key"),
        sa.CheckConstraint("byte_size >= 0 AND byte_size <= 2000000", name="byte_size"))
    op.execute("""CREATE FUNCTION agentforge_coding_immutable() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'coding records are immutable'; END; $$""")
    for table in ("coding_snapshots", "coding_evidence", "coding_artifacts"):
        op.execute(f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION agentforge_coding_immutable()")
    op.execute("""CREATE FUNCTION agentforge_coding_baseline_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN IF NEW.task_id IS DISTINCT FROM OLD.task_id OR NEW.repo_id IS DISTINCT FROM OLD.repo_id OR
        NEW.commit_sha IS DISTINCT FROM OLD.commit_sha OR NEW.baseline_revision IS DISTINCT FROM OLD.baseline_revision
        THEN RAISE EXCEPTION 'coding baseline is immutable'; END IF; RETURN NEW; END; $$""")
    op.execute("CREATE TRIGGER coding_baseline_immutable BEFORE UPDATE ON coding_sessions FOR EACH ROW EXECUTE FUNCTION agentforge_coding_baseline_immutable()")

def downgrade():
    for table in ("coding_artifacts", "coding_evidence", "coding_operations", "coding_snapshots", "coding_sessions"):
        op.drop_table(table)
    op.execute("DROP FUNCTION agentforge_coding_immutable()")
    op.execute("DROP FUNCTION agentforge_coding_baseline_immutable()")
