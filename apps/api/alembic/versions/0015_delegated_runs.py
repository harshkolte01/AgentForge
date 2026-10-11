"""Link delegated runs to their parent without changing existing run identities."""
from alembic import op
import sqlalchemy as sa

revision = "0015_delegated_runs"
down_revision = "0014_worker_presence_indexes"
branch_labels = depends_on = None


def upgrade():
    op.add_column("agent_runs", sa.Column("parent_run_id", sa.Uuid(), sa.ForeignKey("agent_runs.id"), nullable=True))
    op.add_column("agent_runs", sa.Column("assignment_title", sa.Text(), nullable=True))
    op.add_column("agent_runs", sa.Column("assignment_prompt", sa.Text(), nullable=True))
    op.create_index("ix_agent_runs_parent_run_id", "agent_runs", ["parent_run_id"])
    op.drop_constraint(op.f("ck_budget_reservations_depth"), "budget_reservations", type_="check")
    op.create_check_constraint("depth", "budget_reservations", "depth IN (1, 2)")


def downgrade():
    # Refuse a lossy downgrade while persisted child reservations still exist.
    op.drop_constraint(op.f("ck_budget_reservations_depth"), "budget_reservations", type_="check")
    op.create_check_constraint("depth", "budget_reservations", "depth = 1")
    op.drop_index("ix_agent_runs_parent_run_id", table_name="agent_runs")
    op.drop_column("agent_runs", "assignment_prompt")
    op.drop_column("agent_runs", "assignment_title")
    op.drop_column("agent_runs", "parent_run_id")
