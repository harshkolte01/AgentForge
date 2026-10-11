"""Sequential durable worker. No migrations or framework setup on startup.

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
from agentforge.settings import get_settings
from agentforge.subagents.budget import BudgetService
from agentforge.telemetry.neatlogs_setup import Telemetry
from agentforge.workers.asyncio_run import run_async
from .leases import supervise
from .reconciliation import reconcile

logger = logging.getLogger(__name__)


async def task_authority(settings, runtime, store, jobs, lease):
    await jobs.authority(lease)
    # Public and local APIs can share this worker. Recheck any owner-bound client
    # even when the worker itself was launched in local-token mode.
    if callable(getattr(getattr(runtime, "repository_github", None), "authorize", None)):
        current = store.task()
        repository = current.permissions_json.get("repository")
        if repository:
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
        elif not await pin_repository(store=store, settings=settings, sessions=sessions, jobs=jobs, lease=lease):
            pass  # the selected repository could not be reached; the task has ended with the reason
        else:
            runtime = compose_runtime(settings, sessions, telemetry, store)
            async def authority():
                # Cached workspace content never bypasses current source rights.
                await task_authority(settings, runtime, store, jobs, lease)
            config = {"configurable": {"thread_id": task.thread_id},
                "callbacks": telemetry.callbacks(),
                "metadata": {"task_id": str(task.id), "lease_generation": lease.generation}}
            async def invoke():
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


async def run(*, once=False):
    settings = get_settings()
    if settings.worker_heartbeat_sec * 3 >= settings.worker_lease_sec:
        raise ValueError("worker heartbeat must be shorter than one-third of lease")
    engine = create_db_engine(settings.database_url)
    sessions = sessionmaker(engine, expire_on_commit=False)
    jobs = JobRepository(sessions, lease_sec=settings.worker_lease_sec)
    telemetry = Telemetry(settings)
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stopped.set)
        except NotImplementedError:
            pass
    worker_id = f"worker-{uuid4()}"
    try:
        while not stopped.is_set():
            from agentforge.repositories.sessions import SessionRepository
            await asyncio.to_thread(SessionRepository(sessions).dispatch_ready)
            lease = await asyncio.to_thread(jobs.claim, worker_id)
            if lease is not None:
                await execute(settings, sessions, telemetry, jobs, lease)
            if once:
                return
            if lease is None:
                try:
                    await asyncio.wait_for(stopped.wait(), timeout=settings.worker_poll_sec)
                except TimeoutError:
                    pass
    finally:
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
    except Exception:
        raise SystemExit("Worker startup failed. Check configuration, application migrations and explicit checkpoint setup; private details suppressed.") from None


if __name__ == "__main__":
    main()
