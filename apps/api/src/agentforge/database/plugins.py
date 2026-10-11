"""A person's connected plugins: MCP servers whose tools the agent may use, with their encrypted credential."""
from datetime import datetime
from typing import Any
from uuid import UUID
from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Text, UniqueConstraint, Uuid, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base


class PluginConnection(Base):
    __tablename__ = "plugin_connections"
    __table_args__ = (UniqueConstraint("user_id", "slug"),
        CheckConstraint("auth_kind IN ('none','bearer','header','oauth')", name="auth_kind"),
        CheckConstraint("status IN ('connected','error','reauthorize')", name="status"),
        Index("plugin_connections_user_idx", "user_id", "created_at"))
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, server_default=text("gen_random_uuid()"))
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    # The catalog entry this came from ("custom" for a server the person typed in).
    plugin_key: Mapped[str] = mapped_column(Text)
    # Prefix of every tool name this plugin offers the model (`slug__tool`); unique per person and never changed.
    slug: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    server_url: Mapped[str] = mapped_column(Text)
    auth_kind: Mapped[str] = mapped_column(Text, server_default=text("'none'"))
    auth_header: Mapped[str | None] = mapped_column(Text, nullable=True)
    credential_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Used by new conversations; each conversation keeps its own choice (conversations.plugins_json).
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    status: Mapped[str] = mapped_column(Text, server_default=text("'connected'"))
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    server_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    # What the server offered when it was last read: [{name, title, description, input_schema, read_only}].
    tools_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    # The server's own names of tools the person switched off; the agent never sees them.
    disabled_tools: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    tools_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class PluginOAuthGrant(Base):
    """The OAuth sign-in behind a connection (auth_kind "oauth"): which authorization server issued it, the client it was issued
    to, and the tokens, encrypted. One per connection; it goes when the connection does."""
    __tablename__ = "plugin_oauth_grants"
    connection_id: Mapped[UUID] = mapped_column(ForeignKey("plugin_connections.id", ondelete="CASCADE"), primary_key=True)
    # The authorization server's issuer and token endpoint, as validated when the person signed in.
    issuer: Mapped[str] = mapped_column(Text)
    token_endpoint: Mapped[str] = mapped_column(Text)
    revocation_endpoint: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The RFC 8707 resource the tokens were requested for (the server's own identifier of itself).
    resource: Mapped[str] = mapped_column(Text)
    client_id: Mapped[str] = mapped_column(Text)
    client_secret_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    # none | client_secret_post | client_secret_basic
    client_auth_method: Mapped[str] = mapped_column(Text, server_default=text("'none'"))
    # preregistered | metadata_document | dynamic
    registration_kind: Mapped[str] = mapped_column(Text, server_default=text("'dynamic'"))
    scope: Mapped[str | None] = mapped_column(Text, nullable=True)
    access_token_encrypted: Mapped[str] = mapped_column(Text)
    refresh_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class PluginOAuthFlow(Base):
    """A sign-in in progress: what the callback needs to finish it. Single use, short-lived, and tied to the person and the
    browser that started it (the state value and a cookie), so a callback arriving from anywhere else is refused."""
    __tablename__ = "plugin_oauth_flows"
    __table_args__ = (UniqueConstraint("state_hash"), Index("plugin_oauth_flows_expiry_idx", "expires_at"))
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, server_default=text("gen_random_uuid()"))
    state_hash: Mapped[str] = mapped_column(Text)
    binding_hash: Mapped[str] = mapped_column(Text)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    # Set when an existing connection is being signed in again (its tools and choices are kept).
    connection_id: Mapped[UUID | None] = mapped_column(ForeignKey("plugin_connections.id", ondelete="CASCADE"), nullable=True)
    plugin_key: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    server_url: Mapped[str] = mapped_column(Text)
    resource: Mapped[str] = mapped_column(Text)
    issuer: Mapped[str] = mapped_column(Text)
    token_endpoint: Mapped[str] = mapped_column(Text)
    revocation_endpoint: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Whether the server's metadata said it adds `iss` to authorization responses (RFC 9207).
    iss_supported: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    client_id: Mapped[str] = mapped_column(Text)
    client_secret_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    client_auth_method: Mapped[str] = mapped_column(Text, server_default=text("'none'"))
    registration_kind: Mapped[str] = mapped_column(Text)
    redirect_uri: Mapped[str] = mapped_column(Text)
    scope: Mapped[str | None] = mapped_column(Text, nullable=True)
    verifier_encrypted: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PluginOAuthClient(Base):
    """This app's registration (Dynamic Client Registration) with one authorization server, for one redirect URI. Shared by every
    person, since it identifies the app and not a person; kept per issuer, as a client id is only valid where it was issued."""
    __tablename__ = "plugin_oauth_clients"
    __table_args__ = (UniqueConstraint("issuer", "redirect_uri"),)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, server_default=text("gen_random_uuid()"))
    issuer: Mapped[str] = mapped_column(Text)
    redirect_uri: Mapped[str] = mapped_column(Text)
    client_id: Mapped[str] = mapped_column(Text)
    client_secret_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    client_auth_method: Mapped[str] = mapped_column(Text, server_default=text("'none'"))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
