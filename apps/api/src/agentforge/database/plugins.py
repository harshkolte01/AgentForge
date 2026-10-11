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
        CheckConstraint("auth_kind IN ('none','bearer','header')", name="auth_kind"),
        CheckConstraint("status IN ('connected','error')", name="status"),
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
