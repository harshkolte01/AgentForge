"""Async heartbeat supervision; lease release follows child cancellation."""
import asyncio
import logging
import time
from sqlalchemy.exc import DBAPIError, OperationalError, TimeoutError as PoolTimeout
from agentforge.repositories.jobs import TaskCancelled

logger = logging.getLogger(__name__)

# A stop request is noticed within about a second; the lease heartbeat keeps its own, slower rhythm.
CANCEL_POLL_SEC = 1.0


def transient(error: BaseException) -> bool:
    """A dropped connection or a saturated pool: worth retrying while the lease still has time left."""
    return isinstance(error, (OperationalError, PoolTimeout)) or (
        isinstance(error, DBAPIError) and error.connection_invalidated)


async def monitor(jobs, lease, store, heartbeat_sec):
    # The database calls run in a thread: with several tasks per worker, one slow statement must not stall the others'
    # model streams and heartbeats. A transient database error is retried every poll until half the lease has gone
    # without a heartbeat; only then does it fail the run (well before another worker could claim the job).
    patience = getattr(jobs, "lease_sec", 90) / 2
    since_heartbeat, last_beat = 0.0, time.monotonic()
    while True:
        pause = min(CANCEL_POLL_SEC, heartbeat_sec)
        await asyncio.sleep(pause)
        since_heartbeat += pause
        try:
            if since_heartbeat >= heartbeat_sec:
                await asyncio.to_thread(jobs.heartbeat, lease)
                since_heartbeat, last_beat = 0.0, time.monotonic()
            task = await asyncio.to_thread(store.task)
        except Exception as error:
            if not transient(error) or time.monotonic() - last_beat >= patience:
                raise
            logger.warning("Worker heartbeat retrying after a database error; task_id=%s", getattr(lease, "task_id", None))
            continue
        if task.cancel_requested_at is not None:
            raise TaskCancelled("task_cancelled")


async def supervise(invoke, jobs, lease, store, heartbeat_sec):
    graph_task = asyncio.create_task(invoke())
    heartbeat = asyncio.create_task(monitor(jobs, lease, store, heartbeat_sec))
    try:
        done, _ = await asyncio.wait({graph_task, heartbeat}, return_when=asyncio.FIRST_COMPLETED)
        if heartbeat in done:
            error = heartbeat.exception()
            graph_task.cancel()
            # Do not release/requeue a lease while adapter cancellation/finally
            # accounting is still running. No fire-and-forget child work remains.
            try:
                await graph_task
            except BaseException:
                pass
            if error is not None:
                raise error
        return await graph_task
    finally:
        heartbeat.cancel()
        graph_task.cancel()
        await asyncio.gather(heartbeat, graph_task, return_exceptions=True)
