"""SQLAlchemy engine helpers for the API process."""

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine


def create_db_engine(database_url: str, *, pool_size: int = 10, max_overflow: int = 10) -> Engine:
    """Create a pooled engine. pool_pre_ping avoids stale connections on health checks. hide_parameters keeps bound values (message
    text, token hashes, credentials) out of the SQL a database error carries, so a logged exception never repeats them."""
    return create_engine(
        database_url,
        pool_pre_ping=True,
        hide_parameters=True,
        pool_size=pool_size,
        max_overflow=max_overflow,
        connect_args={"connect_timeout": 5},
    )


def ping_database(engine: Engine) -> None:
    """Run SELECT 1. Raises on connection or execution failure."""
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
