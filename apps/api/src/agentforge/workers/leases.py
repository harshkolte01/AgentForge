"""Async heartbeat supervision; lease release follows child cancellation."""
import asyncio
from agentforge.repositories.jobs import TaskCancelled


# A stop request is noticed within about a second; the lease heartbeat keeps its own, slower rhythm.
CANCEL_POLL_SEC = 1.0


async def monitor(jobs, lease, store, heartbeat_sec):
    since_heartbeat = 0.0
    while True:
        pause = min(CANCEL_POLL_SEC, heartbeat_sec)
        await asyncio.sleep(pause)
        since_heartbeat += pause
        if since_heartbeat >= heartbeat_sec:
            jobs.heartbeat(lease)
            since_heartbeat = 0.0
        if store.task().cancel_requested_at is not None:
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
