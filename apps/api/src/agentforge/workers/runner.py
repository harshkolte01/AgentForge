"""Sequential durable worker. No migrations or framework setup on startup.

Run explicitly with: uv run python -m agentforge.workers.runner
It can make paid configured provider calls for queued owner-authorized tasks.
"""
import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import logging
import signal
from uuid import uuid4
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy.orm import sessionmaker
from agentforge.bootstrap import compose_runtime
from agentforge.contracts.tasks import TERMINAL_STATUSES
from agentforge.database.engine import create_db_engine
from agentforge.llm.models import ModelConfigurationError
from agentforge.orchestration.graph import build_graph
from agentforge.orchestration.store import WorkflowStore
from agentforge.orchestration.verification import VerificationService
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
    if settings.auth_mode == "github_app" and runtime.coding is not None:
        current = store.task()
        selection = current.permissions_json["coding"]["repository"]
        await runtime.coding.github.authorize(selection["repo_id"])
        await jobs.authority(lease)
    elif settings.auth_mode == "github_app" and getattr(runtime, "repository_github", None) is not None:
        current = store.task()
        await runtime.repository_github.authorize(current.permissions_json["repository"]["repo_id"])
        await jobs.authority(lease)


async def execute(settings, sessions, saver, telemetry, jobs, lease):
    store = WorkflowStore(sessions, lease)
    runtime = None
    try:
        task = store.task()
        if task.status in TERMINAL_STATUSES:
            jobs.complete(lease)
            return
        ambiguous = reconcile(store, BudgetService(sessions))
        task = store.task()
        if task.cancel_requested_at is not None:
            store.finish("CANCELLED", "Task cancelled before further work.", error_code="USER_CANCELLED")
        elif ambiguous:
            store.finish("PARTIAL", "The previous worker stopped during a child invocation. Unknown paid operations were charged at their authorized bounds and were not repeated.", error_code="UNKNOWN_REMOTE_OUTCOME")
        elif datetime.now(timezone.utc) >= task.created_at + timedelta(seconds=task.max_wall_time_sec):
            store.finish("PARTIAL", "The task reached its wall-time limit before safe continuation.", error_code="BUDGET_EXCEEDED")
        else:
            runtime = compose_runtime(settings, sessions, telemetry, store)
            async def authority():
                # Cached workspace content never bypasses current source rights.
                await task_authority(settings, runtime, store, jobs, lease)
            config = {"configurable": {"thread_id": task.thread_id},
                "callbacks": telemetry.callbacks(), "recursion_limit": 40,
                "metadata": {"task_id": str(task.id), "lease_generation": lease.generation}}
            async def invoke():
                nonlocal runtime
                automatic = store.task().permissions_json.get("automatic")
                if automatic and not automatic.get("resolved"):
                    from agentforge.orchestration.automatic import route_turn
                    from agentforge.integrations.github_client import GitHubClient
                    if settings.auth_mode == "github_app":
                        from agentforge.integrations.github_app import AuthorizedGitHubClient
                        github = AuthorizedGitHubClient(sessions, settings, task.created_by)
                    else:
                        github = GitHubClient()
                    await route_turn(store=store, service=runtime.service, settings=settings,
                        github=github, authority=authority, config=config)
                    if store.task().status in TERMINAL_STATUSES:
                        return
                    # No child remains active when resolved grants are persisted.
                    # A fresh composition binds tools/context to the resolved kind.
                    await runtime.close()
                    runtime = compose_runtime(settings, sessions, telemetry, store)
                if runtime.coding is not None:
                    from agentforge.orchestration.coding_graph import build_coding_graph
                    graph = build_coding_graph(store=store, service=runtime.service, coding=runtime.coding,
                        github=runtime.coding.github, authority=authority, checkpointer=saver)
                else:
                    graph = build_graph(store=store, service=runtime.service,
                        verifier=VerificationService(sessions), authority=authority, checkpointer=saver)
                snapshot = await graph.aget_state(config)
                # Existing pending checkpoints resume with None. Supplying
                # initial input would restart logic and potentially paid calls.
                if snapshot.next:
                    graph_input = None
                elif snapshot.values:
                    store.finish("PARTIAL", "The saved workflow ended without a terminal task receipt; no new provider calls were made.", error_code="UNKNOWN_REMOTE_OUTCOME")
                    return
                else:
                    graph_input = {"task_id": str(task.id)}
                return await graph.ainvoke(graph_input, config)
            invoke = telemetry.decorate(invoke, kind="WORKFLOW", name="agentforge_task")
            remaining = max(0, (task.created_at + timedelta(seconds=task.max_wall_time_sec)
                - datetime.now(timezone.utc)).total_seconds())
            async with asyncio.timeout(remaining):
                await supervise(invoke, jobs, lease, store, settings.worker_heartbeat_sec)
        jobs.complete(lease)
    except TaskCancelled:
        store.finish("CANCELLED", "Task cancelled; in-flight usage remains accounted for.", error_code="USER_CANCELLED")
        jobs.complete(lease)
    except LeaseLost:
        # The new generation owns recovery; the stale process cannot finalize.
        logger.warning("Worker lease lost; task_id=%s", lease.task_id)
    except ModelConfigurationError:
        store.finish("PARTIAL", "The model configuration is incomplete or incompatible. Review model feature flags, reasoning settings and pricing before a new task.", error_code="MODEL_PROVIDER_ERROR")
        jobs.complete(lease)
    except TimeoutError:
        # asyncio.timeout waits for supervise's cancellation/finally accounting.
        store.finish("PARTIAL", "The task reached its wall-time limit before verified delivery.", error_code="BUDGET_EXCEEDED")
        jobs.complete(lease)
    except asyncio.CancelledError:
        # supervise has awaited all child cancellation before reaching this path.
        try:
            store.finish("PARTIAL", "Worker stopped during the task; any unknown usage remains conservatively accounted for.", error_code="UNKNOWN_REMOTE_OUTCOME")
            jobs.complete(lease)
        except LeaseLost:
            pass
        raise
    except Exception:
        # Provider/DB errors can include private request/connection details.
        # Attempt a truthful receipt; if DB unavailable retain the lease/job for
        # later reconciliation rather than pretending persistence succeeded.
        # The traceback goes only to the operator's log, never to the task or its events; without it a
        # failure that happens before any child starts is undiagnosable (it left no run or tool rows).
        logger.error("Worker task failed; task_id=%s", lease.task_id, exc_info=True)
        try:
            reconcile(store, BudgetService(sessions))
            store.finish("PARTIAL", "The workflow stopped before verified delivery. Review task history before starting another task.", error_code="CHILD_RUNTIME_ERROR")
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
        async with AsyncPostgresSaver.from_conn_string(settings.langgraph_checkpoint_db_url) as saver:
            # Read readiness before claiming any job. Missing checkpoint tables
            # require the separate explicit setup CLI, never automatic DDL.
            await saver.aget_tuple({"configurable": {"thread_id": "00000000-0000-0000-0000-000000000000"}})
            while not stopped.is_set():
                lease = jobs.claim(worker_id)
                if lease is not None:
                    await execute(settings, sessions, saver, telemetry, jobs, lease)
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
