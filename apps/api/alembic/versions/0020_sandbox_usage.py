"""Sandbox usage evidence: signed E2B lifecycle events (api/routes_sandbox_usage.py)."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '0020_sandbox_usage'
down_revision = '0019_plugin_oauth'
branch_labels = depends_on = None


def upgrade():
    op.create_table('sandbox_usage_events',
        sa.Column('event_id', sa.Text(), primary_key=True), sa.Column('sandbox_id', sa.Text(), nullable=False),
        sa.Column('event_type', sa.Text(), nullable=False), sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('payload_json', JSONB, nullable=False),
        sa.Column('received_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
    op.create_index('ix_sandbox_usage_events_sandbox_id', 'sandbox_usage_events', ['sandbox_id'])


def downgrade():
    op.drop_table('sandbox_usage_events')
