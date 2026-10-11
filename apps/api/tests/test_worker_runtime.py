"""The worker process: concurrent claim loops, draining on stop, heartbeat resilience and presence. DB- and provider-free."""
import asyncio
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

from sqlalchemy.exc import OperationalError, ProgrammingError

from agentforge.repositories.jobs import TaskCancelled
from agentforge.workers import leases, runner


def operational():
    return OperationalError("SELECT 1", {}, Exception("server closed the connection"))


class FastSleep:
    """Replaces asyncio.sleep inside the worker modules: each pause advances a fake clock instead of waiting."""

    def __init__(self):
        self.now = 0.0
        self.real = asyncio.sleep

    async def __call__(self, seconds):
        self.now += seconds
        await self.real(0)


class HeartbeatTests(unittest.IsolatedAsyncioTestCase):
    async def monitor(self, jobs, store, heartbeat_sec=2):
        sleep = FastSleep()
        with patch.object(leases.asyncio, "sleep", sleep), patch.object(leases.time, "monotonic", lambda: sleep.now):
            await leases.monitor(jobs, NS(task_id="t"), store, heartbeat_sec)

    async def test_a_transient_database_error_is_retried_while_the_lease_has_time(self):
        beats, polls = [], []

        def heartbeat(lease):
            beats.append(1)
            if len(beats) <= 3:
                raise operational()

        def task():
            polls.append(1)
            return NS(cancel_requested_at=object() if len(beats) >= 5 else None)
        with self.assertRaises(TaskCancelled):
            await self.monitor(NS(heartbeat=heartbeat, lease_sec=60), NS(task=task))
        self.assertGreaterEqual(len(beats), 5)

    async def test_a_database_outage_longer_than_half_the_lease_fails_the_run(self):
        attempts = []

        def heartbeat(lease):
            attempts.append(1)
            raise operational()
        with self.assertRaises(OperationalError):
            await self.monitor(NS(heartbeat=heartbeat, lease_sec=30), NS(task=lambda: NS(cancel_requested_at=None)))
        self.assertGreater(len(attempts), 1)
        self.assertLessEqual(len(attempts), 15)  # about one attempt a second for 15 seconds

    async def test_other_errors_are_not_retried(self):
        def heartbeat(lease):
            raise ProgrammingError("SELECT 1", {}, Exception("no such table"))
        with self.assertRaises(ProgrammingError):
            await self.monitor(NS(heartbeat=heartbeat, lease_sec=60), NS(task=lambda: NS(cancel_requested_at=None)))

    async def test_database_calls_run_off_the_event_loop(self):
        import threading
        threads = set()

        def task():
            threads.add(threading.get_ident())
            return NS(cancel_requested_at=object())
        with self.assertRaises(TaskCancelled):
            await self.monitor(NS(heartbeat=lambda lease: None, lease_sec=60), NS(task=task))
        self.assertNotIn(threading.get_ident(), threads)


class WorkerProcessTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.settings = NS(worker_heartbeat_sec=10, worker_lease_sec=90, worker_concurrency=3, worker_poll_sec=0.01,
            worker_drain_sec=0.2, database_url="postgresql://unused", database_pool_size=2, database_max_overflow=1)

    async def start(self, execute, claims, *, once=False):
        """Runs `runner.run` with the database, telemetry and presence replaced. Returns (task, claimed, pool)."""
        claimed, pool, stopped_flags = [], {}, []
        self.released = []
        self.stop_on_claim = getattr(self, "stop_on_claim", False)
        loop = asyncio.get_running_loop()
        engine = NS(dispose=lambda: None)

        def create_engine(url, **sizes):
            pool.update(sizes)
            return engine

        def claim(worker_id):
            if not claims:
                return None
            claimed.append(worker_id)
            if self.stop_on_claim:
                # SIGTERM arrives while the claim is in flight (claim runs in a thread; the signal handler on the loop).
                loop.call_soon_threadsafe(self.stopped.set)
            return NS(task_id=claims.pop(0))

        def release(lease):
            self.released.append(lease.task_id)
            return True

        async def presence(sessions, process_id):
            await asyncio.Event().wait()
        patches = [patch.object(runner, "get_settings", lambda: self.settings),
            patch.object(runner, "create_db_engine", create_engine),
            patch.object(runner, "sessionmaker", lambda engine, **kw: "sessions"),
            patch.object(runner, "JobRepository", lambda sessions, lease_sec: NS(claim=claim, release=release)),
            patch.object(runner, "Telemetry", lambda settings: NS(shutdown=lambda: None)),
            patch.object(runner, "execute", execute), patch.object(runner, "presence", presence),
            patch("agentforge.repositories.sessions.SessionRepository.dispatch_ready", lambda self: None)]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        self.stopped = asyncio.Event()  # what SIGTERM sets
        task = asyncio.create_task(runner.run(once=once, stopped=self.stopped))
        return task, claimed, pool

    async def test_a_job_claimed_as_the_worker_starts_stopping_is_handed_back_not_started(self):
        self.stop_on_claim = True
        ran = []

        async def execute(settings, sessions, telemetry, jobs, lease):
            ran.append(lease.task_id)
        task, claimed, _ = await self.start(execute, ["a"])
        await asyncio.wait_for(task, 5)
        self.assertEqual((ran, self.released), ([], ["a"]))

    async def test_several_tasks_run_at_once_each_claimed_by_its_own_loop(self):
        running, release = set(), asyncio.Event()

        async def execute(settings, sessions, telemetry, jobs, lease):
            running.add(lease.task_id)
            await release.wait()
        task, claimed, pool = await self.start(execute, ["a", "b", "c", "d"])
        for _ in range(50):
            await asyncio.sleep(0.01)
            if len(running) == 3:
                break
        self.assertEqual(running, {"a", "b", "c"})  # a task waiting (on an approval, say) does not hold up the others
        self.assertEqual(len(set(claimed)), 3)       # distinct worker ids
        self.assertGreaterEqual(pool["pool_size"], 2 * self.settings.worker_concurrency)
        release.set()
        for _ in range(50):
            await asyncio.sleep(0.01)
            if "d" in running:
                break
        self.assertIn("d", running)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def test_stopping_lets_running_tasks_finish_within_the_drain_time(self):
        finished = []

        async def execute(settings, sessions, telemetry, jobs, lease):
            await asyncio.sleep(0.05)
            finished.append(lease.task_id)
        self.settings.worker_drain_sec = 5
        task, claimed, _ = await self.start(execute, ["a"])
        await asyncio.sleep(0.02)
        self.stopped.set()
        await asyncio.wait_for(task, 2)
        self.assertEqual(finished, ["a"])

    async def test_a_task_still_running_after_the_drain_time_is_cancelled(self):
        cancelled = []

        async def execute(settings, sessions, telemetry, jobs, lease):
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.append(lease.task_id)  # execute records the run as interrupted on this path
                raise
        task, claimed, _ = await self.start(execute, ["a", "b"])
        await asyncio.sleep(0.05)
        self.stopped.set()
        await asyncio.wait_for(task, 2)
        self.assertEqual(sorted(cancelled), ["a", "b"])

    async def test_once_claims_at_most_one_task(self):
        ran = []

        async def execute(settings, sessions, telemetry, jobs, lease):
            ran.append(lease.task_id)
        task, claimed, _ = await self.start(execute, ["a", "b"], once=True)
        await asyncio.wait_for(task, 2)
        self.assertEqual(ran, ["a"])


class PinRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_repository_is_resolved_under_the_heartbeat(self):
        order = []
        store = NS(task=lambda: NS(status="RUNNING", cancel_requested_at=None, thread_id="t", id="id"),
            finish=lambda *a, **k: order.append("finish"))
        jobs = NS(begin_execution=lambda lease: None, complete=lambda lease: order.append("complete"))

        async def supervise(invoke, jobs, lease, store, heartbeat_sec):
            order.append("supervised")
            await invoke()

        async def pin_repository(**kwargs):
            order.append("pin")
            return False  # the repository could not be reached: the task has already ended with the reason
        with patch.object(runner, "WorkflowStore", lambda sessions, lease: store), \
             patch.object(runner, "reconcile", lambda store, budgets: False), \
             patch.object(runner, "BudgetService", lambda sessions: None), \
             patch.object(runner, "supervise", supervise), patch.object(runner, "pin_repository", pin_repository), \
             patch.object(runner, "compose_runtime", lambda *a: self.fail("no runtime for an unreachable repository")):
            telemetry = NS(callbacks=lambda: [], decorate=lambda function, **kw: function)
            await runner.execute(NS(worker_heartbeat_sec=10), None, telemetry, jobs, NS(task_id="t", generation=1))
        self.assertEqual(order, ["supervised", "pin", "complete"])


if __name__ == "__main__":
    unittest.main()
