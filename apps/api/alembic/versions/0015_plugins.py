"""Plugins: a person's connected MCP servers, and the plugins each conversation may use.

Revision ID: 0015_plugins
Revises: 0014_worker_presence_indexes
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0015_plugins"
down_revision = "0014_worker_presence_indexes"
branch_labels = depends_on = None


def upgrade():
    op.create_table("plugin_connections",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("plugin_key", sa.Text(), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("server_url", sa.Text(), nullable=False),
        sa.Column("auth_kind", sa.Text(), nullable=False, server_default="none"),
        sa.Column("auth_header", sa.Text(), nullable=True),
        sa.Column("credential_encrypted", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("status", sa.Text(), nullable=False, server_default="connected"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("server_name", sa.Text(), nullable=True),
        sa.Column("tools_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("disabled_tools", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("tools_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("user_id", "slug"),
        sa.CheckConstraint("auth_kind IN ('none','bearer','header')", name="auth_kind"),
        sa.CheckConstraint("status IN ('connected','error')", name="status"))
    op.create_index("plugin_connections_user_idx", "plugin_connections", ["user_id", "created_at"])
    op.add_column("conversations", sa.Column("plugins_json", postgresql.JSONB(), nullable=True))


def downgrade():
    op.drop_column("conversations", "plugins_json")
    op.drop_index("plugin_connections_user_idx", table_name="plugin_connections")
    op.drop_table("plugin_connections")
