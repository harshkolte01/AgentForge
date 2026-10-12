"""Remove the CFO.ai finance tables that an earlier build of 0020 created.

CFO.ai is now an ordinary plugin (MCP) with no finance-specific storage. A database that ran the earlier 0020 still has these
tables; one that never did has nothing to drop. They held only workspace/scenario choices and write receipts of that build.

Revision ID: 0022_drop_finance_tables
Revises: 0021_reasoning_effort
"""
from alembic import op

revision = "0022_drop_finance_tables"
down_revision = "0021_reasoning_effort"
branch_labels = depends_on = None


def upgrade():
    for name in ("finance_operations", "finance_contexts", "finance_connections"):
        op.execute(f"DROP TABLE IF EXISTS {name}")


def downgrade():
    # The feature is gone; there is nothing to restore.
    pass
