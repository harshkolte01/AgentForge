"""Signed E2B lifecycle events kept as sandbox usage evidence (api/routes_sandbox_usage.py)."""
from datetime import datetime
from typing import Any
from sqlalchemy import DateTime, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base


class SandboxUsageEvent(Base):
    __tablename__ = 'sandbox_usage_events'
    event_id: Mapped[str] = mapped_column(Text, primary_key=True)
    sandbox_id: Mapped[str] = mapped_column(Text, index=True)
    event_type: Mapped[str] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
