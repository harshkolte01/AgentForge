"""Durable worker: a few claim/execute loops per process (WORKER_CONCURRENCY). No migrations or framework setup on startup.

Run explicitly with: uv run python -m agentforge.workers.runner
It can make paid configured provider calls for queued owner-authorized tasks.
"""
import argparse
import asyncio
import logging
import signal
from uuid import uuid4
from sqlalchemy.orm import sessionmaker
from agentforge.bootstrap import compose_runtime
from agentforge.contracts.tasks import TERMINAL_STATUSES
from agentforge.database.engine import create_db_engine
from agentforge.llm.models import ModelConfigurationError
from agentforge.orchestration.agent_turn import pin_repository, run_agent_turn
from agentforge.orchestration.store import WorkflowStore
from agentforge.repositories.jobs import JobRepository, LeaseLost, TaskCancelled
from pydantic import ValidationError
from agentforge.settings import get_settings, validation_summary
from agentforge.subagents.budget import BudgetService
from agentforge.telemetry.neatlogs_setup import Telemetry
from agentforge.workers.asyncio_run import run_async
from .leases import supervise, transient
from .reconciliation import reconcile

logger = logging.getLogger(__name__)

PRESENCE_SEC = 10   # how often a worker process says it is alive (see repositories/workers.py)


async def task_authority(settings, runtime, store, jobs, lease):
    await jobs.authority(lease)
    # Public and local APIs can share this worker. Recheck any owner-bound client
    # even when the worker itself was launched in local-token mode.
    if callable(getattr(getattr(runtime, "repository_github", None), "authorize", None)):
        current = await asyncio.to_thread(store.task)
        repository = current.permissions_json.get("repository")
        if repository:
            # Cheap when repeated: the client keeps a successful check for a minute (integrations/github_app.py).
            await runtime.repository_github.authorize(repository["repo_id"])
            await jobs.authority(lease)


async def execute(settings, sessions, telemetry, jobs, lease):
    store = WorkflowStore(sessions, lease)
    runtime = None
    try:
        await asyncio.to_thread(jobs.begin_execution, lease)
        task = store.task()
        if task.status in TERMINAL_STATUSES:
            jobs.complete(lease)
            return
        ambiguous = reconcile(store, BudgetService(sessions))
        task = store.task()
        if task.cancel_requested_at is not None:
            store.finish("CANCELLED", "Stopped.", error_code="USER_CANCELLED")
        elif ambiguous:
            store.finish("FAILED", "The worker stopped in the middle of an action, so this run was ended instead of repeating it. Send your message again to retry.", error_code="UNKNOWN_REMOTE_OUTCOME")
        else:
            async def authority():
                # Cached workspace content never bypasses current source rights.
                await task_authority(settings, runtime, store, jobs, lease)
            config = {"configurable": {"thread_id": task.thread_id},
                "callbacks": telemetry.callbacks(),
                "metadata": {"task_id": str(task.id), "lease_generation": lease.generation}}
            async def invoke():
                nonlocal runtime
                # Resolving the repository can take a while (GitHub), so it runs under the lease heartbeat too.
                if not await pin_repository(store=store, settings=settings, sessions=sessions, jobs=jobs, lease=lease):
                    return  # the selected repository could not be reached; the task has ended with the reason
                runtime = compose_runtime(settings, sessions, telemetry, store)
                from agentforge.workers.conversation_context import prepare_turn_context
                from agentforge.workers.conversation_title import name_conversation
                # Optional and fenced: the sidebar shows the generated title as soon as it exists, and a failure keeps the first-message title.
                await name_conversation(store, runtime, settings)
                await prepare_turn_context(store, runtime, settings, authority)
                await run_agent_turn(store=store, service=runtime.service, authority=authority, config=config)
            invoke = telemetry.decorate(invoke, kind="WORKFLOW", name="agentforge_task")
            await supervise(invoke, jobs, lease, store, settings.worker_heartbeat_sec)
        jobs.complete(lease)
    except TaskCancelled:
        store.finish("CANCELLED", "Stopped.", error_code="USER_CANCELLED")
        jobs.complete(lease)
    except LeaseLost:
        # The new generation owns recovery; the stale process cannot finalize.
        logger.warning("Worker lease lost; task_id=%s", lease.task_id)
    except ModelConfigurationError as exc:
        store.finish("FAILED", f"The model is not configured correctly: {exc}", error_code="MODEL_PROVIDER_ERROR")
        jobs.complete(lease)
    except asyncio.CancelledError:
        # supervise has awaited all child cancellation before reaching this path.
        try:
            store.finish("FAILED", "The worker was restarted while this was running. Send your message again to retry.", error_code="UNKNOWN_REMOTE_OUTCOME")
            jobs.complete(lease)
        except LeaseLost:
            pass
        raise
    except Exception:
        # Internal errors can include private request/connection details, so the person is told only that the run failed.
        # Attempt a truthful receipt; if DB unavailable retain the lease/job for
        # later reconciliation rather than pretending persistence succeeded.
        # The traceback goes only to the operator's log, never to the task or its events; without it a
        # failure that happens before any child starts is undiagnosable (it left no run or tool rows).
        logger.error("Worker task failed; task_id=%s", lease.task_id, exc_info=True)
        try:
            reconcile(store, BudgetService(sessions))
            store.finish("FAILED", "Something went wrong while working on this. Please try again.", error_code="CHILD_RUNTIME_ERROR")
            jobs.complete(lease)
        except Exception:
            logger.error("Task recovery receipt unavailable; task_id=%s", lease.task_id)
    finally:
        if runtime is not None:
            await runtime.close()


async def work(worker_id, settings, sessions, telemetry, jobs, stopped, *, once=False):
    """One claim/execute loop. A conversation never runs two tasks at once whatever the number of loops: a conversation
    only gets a task while it has no active one (SessionRepository._dispatch), and each task has one job."""
    from agentforge.repositories.sessions import SessionRepository
    while not stopped.is_set():
        try:
            await asyncio.to_thread(SessionRepository(sessions).dispatch_ready)
            lease = await asyncio.to_thread(jobs.claim, worker_id)
        except Exception as error:
            # A dropped connection is waited out; anything else (missing tables, bad configuration) stops the worker.
            if once or not transient(error):
                raise
            logger.warning("Worker could not reach the database; retrying in %ss", settings.worker_poll_sec)
            lease = None
        else:
            if lease is not None and stopped.is_set() and await asyncio.to_thread(jobs.release, lease):
                return  # claimed as the worker began stopping: another worker runs it instead
            if lease is not None:
                await execute(settings, sessions, telemetry, jobs, lease)
        if once:
            return
        if lease is None:
            try:
                await asyncio.wait_for(stopped.wait(), timeout=settings.worker_poll_sec)
            except TimeoutError:
                pass


async def presence(sessions, process_id):
    """Says this process is alive every few seconds, for GET /configuration. Never fails the worker."""
    from agentforge.repositories.workers import record_worker
    from importlib.metadata import PackageNotFoundError, version
    try:
        release = version("agentforge-api")
    except PackageNotFoundError:
        release = None
    while True:
        try:
            await asyncio.to_thread(record_worker, sessions, process_id, version=release)
        except Exception:
            logger.warning("Worker presence could not be recorded", exc_info=True)
        await asyncio.sleep(PRESENCE_SEC)


async def run(*, once=False, stopped=None):
    """`stopped`: set by SIGINT/SIGTERM (or by the caller) to drain and stop."""
    settings = get_settings()
    if settings.worker_heartbeat_sec * 3 >= settings.worker_lease_sec:
        raise ValueError("worker heartbeat must be shorter than one-third of lease")
    concurrency = 1 if once else settings.worker_concurrency
    # A running task can hold a connection in its model/tool path while its heartbeat holds another.
    engine = create_db_engine(settings.database_url, pool_size=max(settings.database_pool_size, concurrency * 2 + 1),
        max_overflow=settings.database_max_overflow)
    sessions = sessionmaker(engine, expire_on_commit=False)
    jobs = JobRepository(sessions, lease_sec=settings.worker_lease_sec)
    telemetry = Telemetry(settings)
    stopped = stopped or asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stopped.set)
        except NotImplementedError:
            pass
    process_id = f"worker-{uuid4()}"
    loops = [asyncio.create_task(work(f"{process_id}-{index}", settings, sessions, telemetry, jobs, stopped, once=once))
        for index in range(concurrency)]
    alive = asyncio.create_task(presence(sessions, process_id))

    async def drain():
        # Stopping: no new claims; running tasks get worker_drain_sec to finish, then are cancelled so each is
        # recorded as interrupted (execute's CancelledError path) instead of being left to a lease timeout.
        await stopped.wait()
        _, pending = await asyncio.wait(loops, timeout=settings.worker_drain_sec)
        if pending:
            logger.warning("Worker stopping; cancelling %d running task(s)", len(pending))
        for running in pending:
            running.cancel()
    drainer = asyncio.create_task(drain())

    def failed(finished):
        # One loop failing stops the process (as the single loop did before); the others are drained first.
        if not finished.cancelled() and finished.exception() is not None:
            stopped.set()
    for each in loops:
        each.add_done_callback(failed)
    try:
        results = await asyncio.gather(*loops, return_exceptions=True)
        failure = next((result for result in results if isinstance(result, Exception)), None)
        if failure is not None:
            raise failure
    finally:
        for task in (drainer, alive, *loops):
            task.cancel()
        await asyncio.gather(drainer, alive, *loops, return_exceptions=True)
        try:
            telemetry.shutdown()
        finally:
            engine.dispose()


def main():
    parser = argparse.ArgumentParser(description="Run the PostgreSQL-backed research worker.")
    parser.add_argument("--once", action="store_true", help="Claim at most one task (can incur configured provider costs).")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    try:
        run_async(run(once=args.once))
    except KeyboardInterrupt:
        pass
    except ValidationError as error:
        # Pydantic's own message repeats rejected input, which can include secrets from .env.
        raise SystemExit("Invalid configuration (values hidden):\n" + validation_summary(error)) from None
    except Exception:
        raise SystemExit("Worker startup failed. Check configuration and application migrations; private details suppressed.") from None


if __name__ == "__main__":
    main()
