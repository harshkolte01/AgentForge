"""OAuth sign-in for plugins: grants (tokens), sign-ins in progress, and this app's registrations with authorization servers.

Revision ID: 0019_plugin_oauth
Revises: 0018_merge_plugins_deliveries
"""
from alembic import op
import sqlalchemy as sa

revision = "0019_plugin_oauth"
down_revision = "0018_merge_plugins_deliveries"
branch_labels = depends_on = None


def upgrade():
    op.drop_constraint(op.f("ck_plugin_connections_auth_kind"), "plugin_connections", type_="check")
    op.create_check_constraint("auth_kind", "plugin_connections", "auth_kind IN ('none','bearer','header','oauth')")
    op.drop_constraint(op.f("ck_plugin_connections_status"), "plugin_connections", type_="check")
    op.create_check_constraint("status", "plugin_connections", "status IN ('connected','error','reauthorize')")

    op.create_table("plugin_oauth_grants",
        sa.Column("connection_id", sa.Uuid(), sa.ForeignKey("plugin_connections.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("issuer", sa.Text(), nullable=False),
        sa.Column("token_endpoint", sa.Text(), nullable=False),
        sa.Column("revocation_endpoint", sa.Text(), nullable=True),
        sa.Column("resource", sa.Text(), nullable=False),
        sa.Column("client_id", sa.Text(), nullable=False),
        sa.Column("client_secret_encrypted", sa.Text(), nullable=True),
        sa.Column("client_auth_method", sa.Text(), nullable=False, server_default="none"),
        sa.Column("registration_kind", sa.Text(), nullable=False, server_default="dynamic"),
        sa.Column("scope", sa.Text(), nullable=True),
        sa.Column("access_token_encrypted", sa.Text(), nullable=False),
        sa.Column("refresh_token_encrypted", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")))

    op.create_table("plugin_oauth_flows",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("state_hash", sa.Text(), nullable=False),
        sa.Column("binding_hash", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("connection_id", sa.Uuid(), sa.ForeignKey("plugin_connections.id", ondelete="CASCADE"), nullable=True),
        sa.Column("plugin_key", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("server_url", sa.Text(), nullable=False),
        sa.Column("resource", sa.Text(), nullable=False),
        sa.Column("issuer", sa.Text(), nullable=False),
        sa.Column("token_endpoint", sa.Text(), nullable=False),
        sa.Column("revocation_endpoint", sa.Text(), nullable=True),
        sa.Column("iss_supported", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("client_id", sa.Text(), nullable=False),
        sa.Column("client_secret_encrypted", sa.Text(), nullable=True),
        sa.Column("client_auth_method", sa.Text(), nullable=False, server_default="none"),
        sa.Column("registration_kind", sa.Text(), nullable=False),
        sa.Column("redirect_uri", sa.Text(), nullable=False),
        sa.Column("scope", sa.Text(), nullable=True),
        sa.Column("verifier_encrypted", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("state_hash"))
    op.create_index("plugin_oauth_flows_expiry_idx", "plugin_oauth_flows", ["expires_at"])

    op.create_table("plugin_oauth_clients",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("issuer", sa.Text(), nullable=False),
        sa.Column("redirect_uri", sa.Text(), nullable=False),
        sa.Column("client_id", sa.Text(), nullable=False),
        sa.Column("client_secret_encrypted", sa.Text(), nullable=True),
        sa.Column("client_auth_method", sa.Text(), nullable=False, server_default="none"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("issuer", "redirect_uri"))


def downgrade():
    op.drop_table("plugin_oauth_clients")
    op.drop_index("plugin_oauth_flows_expiry_idx", table_name="plugin_oauth_flows")
    op.drop_table("plugin_oauth_flows")
    op.drop_table("plugin_oauth_grants")
    # A signed-in connection cannot exist without its grant: those connections go with it.
    op.execute("DELETE FROM plugin_connections WHERE auth_kind = 'oauth'")
    op.execute("UPDATE plugin_connections SET status = 'error' WHERE status = 'reauthorize'")
    op.drop_constraint(op.f("ck_plugin_connections_status"), "plugin_connections", type_="check")
    op.create_check_constraint("status", "plugin_connections", "status IN ('connected','error')")
    op.drop_constraint(op.f("ck_plugin_connections_auth_kind"), "plugin_connections", type_="check")
    op.create_check_constraint("auth_kind", "plugin_connections", "auth_kind IN ('none','bearer','header')")
