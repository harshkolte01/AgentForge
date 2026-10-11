"""Worker presence: each worker loop says it is alive, so a queued message never waits on nothing in silence."""
from datetime import timedelta
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from agentforge.database.phase3 import WorkerHeartbeat

ONLINE_SEC = 30            # a worker seen this recently is running
PRUNE_AFTER = timedelta(days=1)


def record_worker(session_factory, worker_id: str, *, version: str | None = None) -> None:
    """Upsert this loop's row and prune rows of workers gone for a day."""
    with session_factory() as session, session.begin():
        statement = insert(WorkerHeartbeat).values(worker_id=worker_id, version=version)
        session.execute(statement.on_conflict_do_update(index_elements=[WorkerHeartbeat.worker_id],
            set_={"seen_at": func.now(), "version": statement.excluded.version}))
        session.execute(delete(WorkerHeartbeat).where(WorkerHeartbeat.seen_at < func.now() - PRUNE_AFTER))


def worker_presence(session_factory) -> dict:
    """{"status": "online" | "offline", "last_seen_at": iso or None}. A database error reads as offline."""
    try:
        with session_factory() as session:
            # Compared on the database clock, the same clock that wrote seen_at.
            seen, online = session.execute(select(func.max(WorkerHeartbeat.seen_at),
                func.coalesce(func.max(WorkerHeartbeat.seen_at) >= func.now() - timedelta(seconds=ONLINE_SEC), False))).one()
    except Exception:
        return {"status": "offline", "last_seen_at": None}
    return {"status": "online" if online else "offline", "last_seen_at": seen.isoformat() if seen else None}
