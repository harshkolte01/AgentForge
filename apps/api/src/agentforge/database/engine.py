"""SQLAlchemy engine helpers for the API process."""

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine


def create_db_engine(database_url: str, *, pool_size: int = 10, max_overflow: int = 10) -> Engine:
    """Create a pooled engine. pool_pre_ping avoids stale connections on health checks."""
    return create_engine(
        database_url,
        pool_pre_ping=True,
        pool_size=pool_size,
        max_overflow=max_overflow,
        connect_args={"connect_timeout": 5},
    )


def ping_database(engine: Engine) -> None:
    """Run SELECT 1. Raises on connection or execution failure."""
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
