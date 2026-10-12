"""Each person's reasoning effort: how much the model thinks before it answers.

Revision ID: 0021_reasoning_effort
Revises: 0020_sandbox_usage

NULL means the person never chose, so they get the default (see llm/effort.py) and a later change of that default reaches
them; only an explicit choice is stored.
"""
from alembic import op
import sqlalchemy as sa

revision = "0021_reasoning_effort"
down_revision = "0020_sandbox_usage"
branch_labels = depends_on = None


def upgrade():
    op.add_column("users", sa.Column("reasoning_effort", sa.Text(), nullable=True))
    op.create_check_constraint("ck_users_reasoning_effort", "users",
                               "reasoning_effort IS NULL OR reasoning_effort IN ('none','minimal','low','medium','high','xhigh','max')")


def downgrade():
    op.drop_constraint("ck_users_reasoning_effort", "users", type_="check")
    op.drop_column("users", "reasoning_effort")
