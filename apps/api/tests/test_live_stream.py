"""Recoverable live text: kinds, revisions, resets, fencing, the snapshot endpoint and the SSE frames.

The writer runs against the in-memory `task_streams` of `test_streaming.FakeSessions`, which obeys the SQL the writer
issues (upsert, epoch+revision fenced update, clear). The same SQL is exercised on PostgreSQL by
`examples/phase8_streaming_smoke.py`.
"""
import asyncio
import json
from types import SimpleNamespace as NS
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException
from langgraph.errors import NodeCancelledError

from agentforge.api import routes_events
from agentforge.contracts.activity import LiveStreamSnapshot
from agentforge.llm.models import ModelProviderError
from agentforge.orchestration.stream import FLUSH_SECONDS, ReplyStream, clear_stream
from agentforge.repositories.events import EventRepository, LiveRead
from agentforge.repositories.tasks import TaskNotFound

from test_child_runtime import FakeGateway, ScriptedModel, calls, cut_off, run, text
from test_streaming import FakeSessions


class WriterTests(TestCase):
    def stream(self, sessions=None, **kwargs):
        self.now, self.events = 0.0, []
        self.sessions = sessions or FakeSessions()
        emit = lambda kind, payload: self.events.append((kind, payload, dict(self.sessions.row or {})))
        return ReplyStream(self.sessions, "task", emit=emit, clock=lambda: self.now, **kwargs)

    def row(self):
        return self.sessions.row

    # --- kinds and revisions ------------------------------------------------------------------
    def test_text_is_published_as_progress_until_it_is_committed_as_the_reply(self):
        stream = self.stream()
        stream.begin()
        stream.feed_narration("I'll search.")
        self.assertEqual(self.row(), {"epoch": 1, "revision": 0, "kind": "progress", "text": "I'll search."})
        stream = self.stream()
        stream.begin()
        stream.feed("Done")
        self.assertEqual(self.row(), {"epoch": 1, "revision": 0, "kind": "progress", "text": "Done"})
        stream.end()
        stream.commit_reply()
        self.assertEqual(self.row(), {"epoch": 1, "revision": 1, "kind": "answer", "text": "Done"})

    def test_plain_growth_keeps_the_revision(self):
        stream = self.stream()
        stream.begin()
        for piece in ("a", "b", "c"):
            stream.feed_narration(piece)
            self.now += FLUSH_SECONDS
        stream.end()
        self.assertEqual(self.row(), {"epoch": 1, "revision": 0, "kind": "progress", "text": "abc"})

    def test_text_becoming_the_reply_is_a_new_revision_and_written_at_once(self):
        stream = self.stream()
        stream.begin()
        stream.feed_narration("The answer")
        stream.feed_narration(" is 42.")  # inside the throttle window: held back as growth
        self.assertEqual(self.row()["text"], "The answer")
        stream.commit_reply()  # a change of kind is a replacement, and a replacement is never held back
        self.assertEqual(self.row(), {"epoch": 1, "revision": 1, "kind": "answer", "text": "The answer is 42."})

    def test_the_reply_keeps_its_kind_while_it_grows(self):
        stream = self.stream()
        stream.begin()
        stream.feed("Hello")
        stream.commit_reply()
        self.now += FLUSH_SECONDS
        stream.feed(" world")
        self.assertEqual(self.row(), {"epoch": 1, "revision": 1, "kind": "answer", "text": "Hello world"})

    def test_a_restarted_attempt_clears_what_the_last_one_showed_even_if_nothing_new_is_written(self):
        stream = self.stream()
        stream.begin()
        stream.feed_narration("First attempt text")
        stream.begin()  # the retry never produces any text
        self.assertEqual(self.row(), {"epoch": 1, "revision": 1, "kind": "progress", "text": ""})

    def test_the_text_of_a_restarted_attempt_opens_a_new_epoch(self):
        stream = self.stream()
        stream.begin()
        stream.feed_narration("First")
        stream.begin()
        stream.feed_narration("Second")
        self.assertEqual((self.row()["epoch"], self.row()["text"]), (2, "Second"))
        self.assertGreater(self.row()["revision"], 0)

    def test_nothing_is_written_when_there_is_nothing_to_clear(self):
        stream = self.stream()
        stream.begin()
        stream.begin()
        stream.discard()
        stream.commit_narration()
        stream.commit_reply()
        self.assertEqual(self.sessions.writes, [])
        self.assertEqual(self.events, [])

    def test_a_clear_is_written_once(self):
        stream = self.stream()
        stream.begin()
        stream.feed_narration("Words")
        stream.discard()
        stream.discard()
        stream.begin()
        self.assertEqual(self.sessions.writes, ["INSERT", "UPDATE"])
        self.assertEqual(self.row(), {"epoch": 1, "revision": 1, "kind": "progress", "text": ""})

    def test_discarding_a_reply_is_an_empty_reset_of_kind_progress(self):
        stream = self.stream()
        stream.begin()
        stream.feed("Half an answer")
        stream.commit_reply()
        stream.discard()  # the run failed: its words are not the reply
        self.assertEqual(self.row(), {"epoch": 1, "revision": 2, "kind": "progress", "text": ""})

    def test_a_discarded_stream_does_not_bring_old_text_back(self):
        stream = self.stream()
        stream.begin()
        stream.feed("Words")
        stream.discard()
        stream.end()
        stream.commit_reply()
        self.assertEqual(self.row()["text"], "")

    # --- notes ----------------------------------------------------------------------------------
    def test_a_note_is_saved_while_the_live_copy_still_shows_it_and_the_clear_is_a_reset(self):
        stream = self.stream()
        stream.begin()
        stream.feed_narration("I'll read the file.")
        stream.commit_narration()
        kind, payload, row_at_emit = self.events[0]
        self.assertEqual((kind, payload), ("assistant.message", {"text": "I'll read the file.", "epoch": 1}))
        self.assertEqual(row_at_emit["text"], "I'll read the file.")  # saved first, cleared after
        self.assertEqual(self.row(), {"epoch": 1, "revision": 1, "kind": "progress", "text": ""})

    def test_the_reply_after_a_note_moves_to_a_new_epoch(self):
        stream = self.stream()
        stream.begin()
        stream.feed_narration("Let me check one more thing.")
        stream.end()
        stream.commit_narration()
        stream.begin()
        stream.feed("The answer")
        stream.end()
        stream.commit_reply()
        self.assertEqual(self.events[0][1]["epoch"], 1)
        self.assertEqual((self.row()["epoch"], self.row()["kind"], self.row()["text"]), (2, "answer", "The answer"))
        self.assertEqual(self.sessions.history[-1], self.row())

    def test_a_failing_event_leaves_the_live_copy_for_the_run_to_discard(self):
        stream = self.stream()
        stream.emit = lambda kind, payload: (_ for _ in ()).throw(RuntimeError("lost lease"))
        stream.begin()
        stream.feed_narration("A note")
        with self.assertRaises(RuntimeError):
            stream.commit_narration()
        self.assertEqual(self.row()["text"], "A note")
        stream.discard()
        self.assertEqual(self.row()["text"], "")

    # --- fencing --------------------------------------------------------------------------------
    def test_a_stale_epoch_cannot_overwrite_a_newer_one(self):
        sessions = FakeSessions()
        old = self.stream(sessions)
        old.begin()
        old.feed_narration("Old attempt")
        new = ReplyStream(sessions, "task", emit=lambda *a: None, clock=lambda: 0.0)
        new.begin()
        new.feed_narration("New attempt")
        self.assertEqual(sessions.row["epoch"], 2)
        self.now += 1
        old.feed_narration(" keeps going")  # a worker that lost its lease writes late
        self.assertEqual(sessions.row["text"], "New attempt")
        self.assertTrue(old.disabled)

    def test_an_older_revision_cannot_bring_back_cleared_text(self):
        stream = self.stream()
        stream.begin()
        stream.feed_narration("Words")
        self.sessions.row["revision"] = 5  # a newer clear landed in the same epoch
        self.now += 1
        stream.feed_narration(" more")
        self.assertEqual(self.sessions.row["text"], "Words")
        self.assertTrue(stream.disabled)

    def test_the_terminal_clear_fences_out_a_late_writer(self):
        stream = self.stream()
        stream.begin()
        stream.feed_narration("Words")
        clear_stream(self.sessions, "task")
        self.assertEqual(self.row(), {"epoch": 1, "revision": 1, "kind": "progress", "text": ""})
        self.now += 1
        stream.feed_narration(" late")
        self.assertEqual(self.row()["text"], "")

    # --- throttling and failure -----------------------------------------------------------------
    def test_growth_is_throttled_but_a_clear_is_not(self):
        stream = self.stream()
        stream.begin()
        stream.feed_narration("a")
        stream.feed_narration("b")
        stream.feed_narration("c")
        self.assertEqual(self.sessions.writes, ["INSERT"])
        stream.discard()  # same instant: still written
        self.assertEqual(self.sessions.writes, ["INSERT", "UPDATE"])
        self.assertEqual(self.row()["text"], "")

    def test_end_publishes_the_throttled_tail(self):
        stream = self.stream()
        stream.begin()
        stream.feed_narration("a")
        stream.feed_narration("bc")
        stream.end()
        self.assertEqual(self.row()["text"], "abc")

    def test_a_database_failure_switches_streaming_off_and_never_raises(self):
        stream = self.stream(FakeSessions(fail=True))
        stream.begin()
        stream.feed_narration("hello")
        stream.discard()
        stream.end()
        self.assertTrue(stream.disabled)
        clear_stream(FakeSessions(fail=True), "task")

    def test_clearing_a_task_that_never_streamed_writes_nothing(self):
        sessions = FakeSessions()
        clear_stream(sessions, "task")
        self.assertIsNone(sessions.row)


class ScriptedRows:
    """A session factory whose `execute` answers come from a script, for repository reads."""

    def __init__(self, *results):
        self.results = list(results)
        self.fail = False

    def __call__(self):
        outer = self

        class Session:
            def __enter__(self_inner): return self_inner
            def __exit__(self_inner, *args): return False
            def execute(self_inner, statement):
                if outer.fail:
                    raise RuntimeError("database down")
                return NS(first=lambda value=outer.results.pop(0): value)
        return Session()


class RepositoryTests(TestCase):
    def snapshot(self, row):
        return EventRepository(ScriptedRows(row)).stream_snapshot(uuid4(), uuid4())

    def test_a_live_row_is_returned_whole(self):
        row = NS(epoch=3, revision=2, kind="answer", text="Hello", status="RUNNING")
        self.assertEqual(self.snapshot(row), (3, 2, "answer", "Hello", None))

    def test_a_live_tool_call_is_returned_with_what_it_is(self):
        row = NS(epoch=3, revision=2, kind="tool", text="ls -la", tool="bash", call_id="run:tool:call_1", detail=None, status="RUNNING")
        self.assertEqual(self.snapshot(row), (3, 2, "tool", "ls -la", ("bash", "run:tool:call_1", None)))

    def test_no_row_and_database_errors_are_none(self):
        self.assertIsNone(self.snapshot(None))
        failing = ScriptedRows()
        failing.fail = True
        self.assertIsNone(EventRepository(failing).stream_snapshot(uuid4(), uuid4()))

    def test_text_left_on_a_terminal_task_is_reported_as_cleared(self):
        row = NS(epoch=3, revision=2, kind="answer", text="stale", status="FAILED")
        self.assertEqual(self.snapshot(row), (3, 3, "progress", "", None))

    def test_an_already_cleared_terminal_task_is_reported_as_it_is(self):
        row = NS(epoch=3, revision=3, kind="progress", text="", status="COMPLETED")
        self.assertEqual(self.snapshot(row), (3, 3, "progress", "", None))

    def since(self, row, state=None, sent=0):
        return EventRepository(ScriptedRows(row)).stream_since(uuid4(), uuid4(), state, sent)

    def test_a_pass_reads_status_watermark_and_the_live_text_together(self):
        row = NS(status="RUNNING", event_sequence=12, epoch=2, revision=1, kind="answer", length=11, text=" world")
        self.assertEqual(self.since(row, (2, 1), 5), LiveRead(False, 12, (2, 1, "answer", 11, " world")))

    def test_a_task_without_a_live_row_has_no_live_text(self):
        row = NS(status="COMPLETED", event_sequence=4, epoch=None, revision=None, kind=None, length=None, text=None)
        self.assertEqual(self.since(row), LiveRead(True, 4, None))

    def test_text_left_on_a_terminal_task_is_read_as_cleared(self):
        row = NS(status="FAILED", event_sequence=4, epoch=3, revision=2, kind="answer", length=5, text="stale")
        self.assertEqual(self.since(row), LiveRead(True, 4, (3, 3, "progress", 0, "")))

    def test_a_failed_or_foreign_read_never_ends_the_stream(self):
        self.assertEqual(self.since(None), LiveRead(False, None, None))
        failing = ScriptedRows()
        failing.fail = True
        self.assertEqual(EventRepository(failing).stream_since(uuid4(), uuid4()), LiveRead(False, None, None))

    def test_the_rest_of_the_text_is_cut_by_postgresql_in_characters(self):
        from sqlalchemy.dialects import postgresql
        captured = []

        class Capture(ScriptedRows):
            def __call__(self):
                session = super().__call__()
                original = session.execute
                session.execute = lambda statement: (captured.append(statement), original(statement))[1]
                return session
        EventRepository(Capture(None)).stream_since(uuid4(), uuid4(), (2, 1), 5)
        sql = str(captured[0].compile(dialect=postgresql.dialect()))
        self.assertIn("substr(task_streams.text", sql)
        self.assertIn("length(task_streams.text)", sql)
        self.assertIn("LEFT OUTER JOIN task_streams", sql)

    def test_the_snapshot_carries_the_watermark_and_terminal_flag(self):
        task = NS(event_sequence=42, status="RUNNING")
        live = NS(epoch=2, revision=1, kind="progress", text="Working", status="RUNNING")
        task_id = uuid4()
        snapshot = EventRepository(ScriptedRows(live, task)).live_snapshot(task_id, uuid4())
        self.assertEqual(snapshot, LiveStreamSnapshot(task_id=task_id, epoch=2, revision=1, kind="progress", text="Working",
                                                      event_sequence=42, terminal=False))

    def test_a_task_without_a_live_row_has_an_empty_snapshot(self):
        snapshot = EventRepository(ScriptedRows(None, NS(event_sequence=7, status="COMPLETED"))).live_snapshot(uuid4(), uuid4())
        self.assertEqual((snapshot.epoch, snapshot.revision, snapshot.text, snapshot.terminal), (0, 0, "", True))

    def test_a_foreign_or_missing_task_is_not_found(self):
        with self.assertRaises(TaskNotFound):
            EventRepository(ScriptedRows(None, None)).live_snapshot(uuid4(), uuid4())


class SnapshotEndpointTests(TestCase):
    def client(self, repository=None, user=None):
        from fastapi.testclient import TestClient
        from agentforge.auth.session import current_user
        from agentforge.main import create_app
        from agentforge.settings import Settings
        app = create_app(Settings(_env_file=None, app_env="test", auth_mode="disabled", llm_api_key=None, tavily_api_key=None, neatlogs_api_key=None))
        app.state.engine = None  # never touched: the repository is replaced
        self.user = user or NS(id=uuid4())
        def revoked():
            raise HTTPException(401, "session_required")
        app.dependency_overrides[current_user] = revoked if user is False else (lambda: self.user)
        self.patch = patch.object(routes_events, "EventRepository", lambda factory: repository)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        return TestClient(app)

    def test_the_owner_gets_the_snapshot_uncached(self):
        task_id = uuid4()
        calls = []

        class Repository:
            def live_snapshot(self, tid, owner):
                calls.append((tid, owner))
                return LiveStreamSnapshot(task_id=tid, epoch=2, revision=1, kind="answer", text="Hi", event_sequence=9, terminal=False)
        response = self.client(Repository()).get(f"/api/v1/tasks/{task_id}/stream-snapshot")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"task_id": str(task_id), "epoch": 2, "revision": 1, "kind": "answer", "text": "Hi",
                                           "tool": None, "call_id": None, "detail": None, "event_sequence": 9, "terminal": False})
        self.assertEqual(calls, [(task_id, self.user.id)])
        self.assertEqual(response.headers["cache-control"], "private, no-store")
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")

    def test_a_foreign_or_missing_task_is_a_404(self):
        class Repository:
            def live_snapshot(self, tid, owner):
                raise TaskNotFound("task_not_found")
        response = self.client(Repository()).get(f"/api/v1/tasks/{uuid4()}/stream-snapshot")
        self.assertEqual((response.status_code, response.json()["detail"]), (404, "task_not_found"))
        self.assertEqual(response.headers["cache-control"], "no-store")  # errors are never cached either

    def test_a_revoked_or_missing_session_is_refused_before_anything_is_read(self):
        class Repository:
            def live_snapshot(self, tid, owner):
                raise AssertionError("must not be reached")
        response = self.client(Repository(), user=False).get(f"/api/v1/tasks/{uuid4()}/stream-snapshot")
        self.assertEqual((response.status_code, response.json()["detail"]), (401, "session_required"))

    def test_a_malformed_task_id_is_rejected(self):
        self.assertEqual(self.client(NS()).get("/api/v1/tasks/not-a-uuid/stream-snapshot").status_code, 422)


class FakeRequest:
    """Disconnects once the script of snapshots is used up (at the end of the pass that used the last one); records nothing else."""

    def __init__(self, repository):
        self.repository, self.app = repository, NS(state=NS(engine=None))

    async def is_disconnected(self):
        return self.repository.done


class StreamRepository:
    """Scripted live rows, given whole as (epoch, revision, kind, text); `stream_since` answers as the SQL does, with
    only the unsent rest of the text when the reader's (epoch, revision) still matches. `terminal` scripts the task's
    status and watermark per pass ((terminal, event_sequence)); by default the task keeps running. The live text is read first
    in a pass, so using up the script only ends the stream once that pass is over (`finished`, called between passes)."""

    def __init__(self, snapshots, pages=(), terminal=()):
        self.snapshots, self.pages, self.terminal, self.done = list(snapshots), list(pages), list(terminal), False
        self.reads, self.last_pass = [], False

    def finished(self):
        self.done = self.done or self.last_pass

    def list_after(self, task_id, owner_id, after_sequence=0, limit=100):
        return self.pages.pop(0) if self.pages else []

    def stream_since(self, task_id, owner_id, state=None, sent=0):
        self.reads.append((state, sent))
        terminal, sequence = self.terminal.pop(0) if self.terminal else (False, 100)
        if not self.snapshots:
            self.last_pass = True
            return LiveRead(terminal, sequence, None)
        value = self.snapshots.pop(0)
        if not self.snapshots:
            self.last_pass = True  # the loop ends after this pass
        if value is None:
            return LiveRead(terminal, sequence, None)
        epoch, revision, kind, text = value
        same = state == (epoch, revision) and len(text) >= sent
        return LiveRead(terminal, sequence, (epoch, revision, kind, len(text), text[sent:] if same else text))


def event(sequence, kind="tool.started"):
    return NS(sequence=sequence, event_type=kind, model_dump_json=lambda: json.dumps({"sequence": sequence, "event_type": kind}))


class StreamTests(IsolatedAsyncioTestCase):
    task_id = uuid4()

    async def open(self, repository, *, authenticate=None, clock=None, sleeps=None, event_sequence=100, **params):
        user = NS(id=uuid4())
        tasks = NS(get=lambda task_id, owner_id: NS(event_sequence=event_sequence))
        real_sleep = asyncio.sleep

        async def fast(seconds):
            if sleeps is not None:
                sleeps.append(seconds)
            repository.finished()
            await real_sleep(0)
        patches = [patch.object(routes_events, "EventRepository", lambda factory: repository),
                   patch.object(routes_events, "sessions", lambda request: None),
                   patch.object(routes_events, "current_user", authenticate(user) if authenticate else (lambda request: user)),
                   patch.object(routes_events.asyncio, "sleep", fast)]
        if clock is not None:
            patches.append(patch.object(routes_events, "time", NS(monotonic=clock)))
        for item in patches:
            item.start()
        try:
            response = await routes_events.events(self.task_id, FakeRequest(repository), user=user, repository=tasks,
                                                  after=params.get("after", 0), last_event_id=params.get("last_event_id"))
            frames = [frame async for frame in response.body_iterator]
        finally:
            for item in reversed(patches):
                item.stop()
        return response, frames

    def deltas(self, frames):
        return [json.loads(frame.split("data: ", 1)[1]) for frame in frames if frame.startswith("event: message.delta")]

    async def test_a_new_connection_starts_with_a_full_state_reset_then_pure_appends(self):
        repository = StreamRepository([(2, 1, "answer", "Hello"), (2, 1, "answer", "Hello wor"), (2, 1, "answer", "Hello world")])
        _, frames = await self.open(repository)
        self.assertEqual(self.deltas(frames), [
            {"task_id": str(self.task_id), "epoch": 2, "revision": 1, "kind": "answer", "offset": 0, "text": "Hello", "reset": True},
            {"task_id": str(self.task_id), "epoch": 2, "revision": 1, "kind": "answer", "offset": 5, "text": " wor", "reset": False},
            {"task_id": str(self.task_id), "epoch": 2, "revision": 1, "kind": "answer", "offset": 9, "text": "ld", "reset": False}])

    async def test_the_frame_is_a_single_well_formed_sse_event(self):
        _, frames = await self.open(StreamRepository([(1, 0, "progress", "Look: é\nnext")]))
        frame = [f for f in frames if f.startswith("event: message.delta")][0]
        self.assertTrue(frame.endswith("\n\n"))
        self.assertEqual(frame.count("\n"), 3)  # event line, data line (line breaks escaped in JSON), blank line
        self.assertEqual(json.loads(frame.split("data: ", 1)[1])["text"], "Look: é\nnext")

    async def test_every_field_is_always_present(self):
        _, frames = await self.open(StreamRepository([(1, 0, "progress", "x")]))
        self.assertEqual(set(self.deltas(frames)[0]), {"task_id", "epoch", "revision", "kind", "offset", "text", "reset"})

    async def test_a_new_revision_or_epoch_is_sent_whole_as_a_reset(self):
        repository = StreamRepository([(1, 0, "progress", "I'll search."), (1, 1, "answer", "The answer"), (2, 2, "answer", "Fresh")])
        _, frames = await self.open(repository)
        deltas = self.deltas(frames)
        self.assertEqual([(d["epoch"], d["revision"], d["kind"], d["offset"], d["text"], d["reset"]) for d in deltas], [
            (1, 0, "progress", 0, "I'll search.", True), (1, 1, "answer", 0, "The answer", True), (2, 2, "answer", 0, "Fresh", True)])

    async def test_a_cleared_row_is_sent_as_an_empty_reset(self):
        repository = StreamRepository([(1, 0, "progress", "A note"), (1, 1, "progress", "")])
        _, frames = await self.open(repository)
        last = self.deltas(frames)[-1]
        self.assertEqual((last["revision"], last["offset"], last["text"], last["reset"]), (1, 0, "", True))

    async def test_a_terminal_clear_is_delivered(self):
        repository = StreamRepository([(3, 4, "answer", "Almost"), (3, 5, "progress", "")])
        _, frames = await self.open(repository)
        self.assertEqual([(d["revision"], d["text"], d["reset"]) for d in self.deltas(frames)], [(4, "Almost", True), (5, "", True)])

    async def test_a_reconnect_starts_again_at_offset_zero_with_a_reset(self):
        state = (2, 3, "answer", "Hello world")
        _, first = await self.open(StreamRepository([state]))
        _, second = await self.open(StreamRepository([state]), last_event_id="17")
        self.assertEqual(self.deltas(first), self.deltas(second))
        self.assertEqual((self.deltas(second)[0]["offset"], self.deltas(second)[0]["reset"]), (0, True))

    async def test_a_reconnect_onto_a_cleared_row_clears_the_clients_stale_text(self):
        _, frames = await self.open(StreamRepository([(2, 4, "progress", "")]))
        self.assertEqual([(d["text"], d["reset"]) for d in self.deltas(frames)], [("", True)])

    async def test_nothing_is_sent_for_a_task_that_never_streamed(self):
        _, frames = await self.open(StreamRepository([None, None]))
        self.assertEqual(self.deltas(frames), [])

    async def test_an_unchanged_row_is_not_resent(self):
        _, frames = await self.open(StreamRepository([(1, 0, "progress", "Same")] * 3))
        self.assertEqual(len(self.deltas(frames)), 1)

    async def test_the_end_of_a_task_reaches_the_reader_before_its_live_text_is_cleared(self):
        # The worker commits task.finished, then clears the live copy. Here it does so right after the first read of a pass,
        # between that pass's two reads: whatever the order of the reads, the reader must learn that the task ended before it
        # sees the text go, or it takes the clear for text thrown away mid-run and blanks the answer until the saved one loads.
        class Racing:
            def __init__(self):
                self.reads, self.done, self.passes = 0, False, 0

            def finished(self):
                self.passes += 1
                self.done = self.passes >= 3

            def over(self):
                self.reads += 1
                return self.reads > 1  # the worker finishes right after the first read

            def stream_since(self, task_id, owner_id, state=None, sent=0):
                if self.over():
                    return LiveRead(True, 9, (2, 2, "progress", 0, ""))
                return LiveRead(False, 8, (2, 1, "answer", 5, "Done."))

            def list_after(self, task_id, owner_id, after_sequence=0, limit=100):
                return [event(9, "task.finished")] if self.over() and after_sequence < 9 else []
        _, frames = await self.open(Racing(), after=8)
        finished = [i for i, frame in enumerate(frames) if "event: task.finished" in frame]
        cleared = [i for i, frame in enumerate(frames) if frame.startswith("event: message.delta") and '"text": ""' in frame]
        self.assertTrue(finished and cleared, frames)
        self.assertLess(finished[0], cleared[0], "the end of the task is sent before the cleared text")

    async def test_durable_events_keep_their_sequence_as_the_sse_id(self):
        repository = StreamRepository([(1, 0, "progress", "Hi")], pages=[[event(7), event(8, "tool.finished")]])
        _, frames = await self.open(repository, after=6)
        durable = [frame for frame in frames if frame.startswith("id: ")]
        self.assertEqual([f.split("\n")[0] for f in durable], ["id: 7", "id: 8"])
        self.assertIn("event: tool.finished", durable[1])

    async def test_the_headers_that_keep_the_stream_unbuffered_are_unchanged(self):
        response, frames = await self.open(StreamRepository([None]))
        self.assertEqual(frames[0], "retry: 3000\n\n")
        self.assertEqual(response.media_type, "text/event-stream")
        self.assertEqual(response.headers["cache-control"], "no-cache, no-store, no-transform")
        self.assertEqual(response.headers["x-accel-buffering"], "no")
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")

    async def test_a_quiet_stream_sends_a_keepalive_every_ten_seconds(self):
        ticks = iter(range(0, 1000, 6))  # six "seconds" pass on every reading
        _, frames = await self.open(StreamRepository([None] * 6), clock=lambda: next(ticks))
        self.assertGreaterEqual(frames.count(": keepalive\n\n"), 1)

    async def test_polling_is_fast_only_while_text_is_being_written(self):
        sleeps = []
        await self.open(StreamRepository([(1, 0, "progress", "Writing"), (1, 1, "progress", ""), None]), sleeps=sleeps)
        self.assertEqual(sleeps[:3], [0.15, 0.5, 0.5])

    async def test_the_session_is_checked_again_every_ten_seconds_and_revocation_closes_the_stream(self):
        calls = []
        now = [0.0]

        def clock():
            now[0] += 2.5  # every reading of the clock moves it on
            return now[0]

        def authenticate(user):
            def check(request):
                calls.append(now[0])
                if len(calls) > 2:
                    raise HTTPException(401, "session_required")
                return user
            return check
        repository = StreamRepository([(1, 0, "progress", "A")] * 30)
        _, frames = await self.open(repository, authenticate=authenticate, clock=clock)
        self.assertEqual(len(calls), 3)
        self.assertTrue(all(later - earlier >= routes_events.REAUTHENTICATE_SEC for earlier, later in zip(calls, calls[1:])))
        self.assertLess(len(calls), len(repository.reads))  # not once per pass
        self.assertEqual(frames[-1], "event: session.closed\ndata: {}\n\n")
        self.assertEqual(len(self.deltas(frames)), 1)  # nothing further is read after revocation

    async def test_only_the_unsent_rest_of_the_text_is_read(self):
        repository = StreamRepository([(2, 1, "answer", "Hello"), (2, 1, "answer", "Hello world"), (2, 2, "answer", "New")])
        await self.open(repository)
        self.assertEqual(repository.reads, [(None, 0), ((2, 1), 5), ((2, 1), 11)])

    async def test_a_finished_task_whose_events_were_all_sent_ends_the_stream(self):
        repository = StreamRepository([(1, 3, "progress", "")] * 5, pages=[[event(7), event(8, "task.finished")]],
                                      terminal=[(True, 8)] * 5)
        _, frames = await self.open(repository, after=6)
        self.assertEqual(frames[-1], "retry: 60000\n\n")
        self.assertEqual([f.split("\n")[0] for f in frames if f.startswith("id: ")], ["id: 7", "id: 8"])
        self.assertEqual(len(repository.reads), 2)  # the page with the last events, then one pass that finds nothing more

    async def test_a_finished_task_with_events_still_unsent_keeps_streaming(self):
        repository = StreamRepository([None] * 3, terminal=[(True, 9)] * 3)
        _, frames = await self.open(repository, after=6)
        self.assertNotIn("retry: 60000\n\n", frames)
        self.assertEqual(len(repository.reads), 3)

    async def test_an_idle_pass_reads_no_event_page(self):
        class Counting(StreamRepository):
            def list_after(self, *args, **kwargs):
                self.listed = getattr(self, "listed", 0) + 1
                return super().list_after(*args, **kwargs)
        # Watermark 6 with the cursor at 6: nothing new can be in the event log, so the page is never read.
        repository = Counting([(1, 0, "progress", "A")] * 3, terminal=[(False, 6)] * 3)
        await self.open(repository, after=6)
        self.assertEqual((getattr(repository, "listed", 0), len(repository.reads)), (0, 3))
        # A newer watermark reads the page, once per pass until the events are sent.
        repository = Counting([None] * 2, pages=[[event(7)]], terminal=[(False, 7)] * 2)
        _, frames = await self.open(repository, after=6)
        self.assertEqual(repository.listed, 1)
        self.assertEqual([f.split("\n")[0] for f in frames if f.startswith("id: ")], ["id: 7"])

    async def test_a_failed_or_unowned_read_still_reads_the_page_and_closes_the_stream(self):
        class Gone(StreamRepository):
            def list_after(self, *args, **kwargs):
                raise TaskNotFound("task_not_found")
        _, frames = await self.open(Gone([None], terminal=[(False, None)]), after=6)
        self.assertEqual(frames[-1], "event: session.closed\ndata: {}\n\n")

    async def test_a_cursor_ahead_of_the_task_is_refused(self):
        with self.assertRaises(HTTPException) as caught:
            await self.open(StreamRepository([None]), event_sequence=3, after=9)
        self.assertEqual((caught.exception.status_code, caught.exception.detail), (409, "event_cursor_ahead_of_task"))

    def test_a_malformed_last_event_id_is_refused(self):
        for header in ("abc", "-1", "1" * 19, "9007199254740992"):
            with self.assertRaises(HTTPException, msg=header):
                routes_events.cursor_value(header, 0)
        self.assertEqual(routes_events.cursor_value("12", 0), 12)
        self.assertEqual(routes_events.cursor_value(None, 5), 5)


class StreamingModel(ScriptedModel):
    """A scripted model that can also write some text out through the callbacks and then fail.

    A turn is anything ScriptedModel plays, or (streamed text, one of those) to stream something first.
    """

    async def complete(self, messages, tools, on_text=None, tool_choice=None, on_narration=None):
        turn = self.turns[0]
        if isinstance(turn, tuple) and isinstance(turn[0], str) and turn[0] not in ("length", "stop", "tool_calls"):
            streamed, rest = turn
            self.turns[0] = rest
            if on_narration is not None:
                on_narration(streamed)
        return await super().complete(messages, tools, on_text=on_text, tool_choice=tool_choice, on_narration=on_narration)


class RuntimeLiveTextTests(IsolatedAsyncioTestCase):
    """Whatever a run stops using is cleared from the live row; the reply it keeps stays."""

    def stream(self):
        self.sessions, self.events = FakeSessions(), []
        return ReplyStream(self.sessions, "task", emit=lambda kind, payload: self.events.append((kind, payload)),
                           clock=lambda: 0.0)

    def texts(self):
        return [(row["epoch"], row["revision"], row["kind"], row["text"]) for row in self.sessions.history]

    async def test_the_reply_is_shown_as_an_answer_and_stays_live_until_the_task_ends(self):
        result = await run(StreamingModel([text("All done.")]), FakeGateway(), stream=self.stream())
        self.assertEqual(result.status, "PASS")
        self.assertEqual((self.sessions.row["kind"], self.sessions.row["text"]), ("answer", "All done."))
        self.assertEqual(self.events, [])  # the final text is the answer, never a saved note
        clear_stream(self.sessions, "task")
        self.assertEqual(self.sessions.row["text"], "")

    async def test_a_reply_cut_off_while_calling_tools_is_cleared_before_the_retry(self):
        model = StreamingModel([cut_off(calls(("read_repo_file", {"path": "a"}), content="Let me re")), text("All done.")])
        await run(model, FakeGateway(), stream=self.stream())
        self.assertEqual([t for *_, t in self.texts()], ["Let me re", "", "All done.", "All done."])
        self.assertEqual(self.texts()[1][:2], (1, 1))  # an empty reset in the same epoch, then the retry opens a new one
        self.assertEqual(self.texts()[2][0], 2)
        self.assertEqual(self.events, [])  # the cut-off words were never saved as a note

    async def test_an_empty_reply_leaves_nothing_on_screen(self):
        await run(StreamingModel([text(""), text("Here.")]), FakeGateway(), stream=self.stream())
        self.assertEqual([(kind, t) for _, _, kind, t in self.texts()][0], ("progress", "Here."))

    async def test_a_reply_stopped_by_the_provider_is_cleared_and_the_run_fails(self):
        model = StreamingModel([("Partial words", ("content_filter", text("Partial words")))])
        with self.assertRaises(ModelProviderError):
            await run(model, FakeGateway(), stream=self.stream())
        self.assertEqual(self.sessions.row["text"], "")

    async def test_a_model_error_while_writing_leaves_no_text_behind(self):
        model = StreamingModel([("Half of an ans", ModelProviderError("provider failed"))])
        with self.assertRaises(ModelProviderError):
            await run(model, FakeGateway(), stream=self.stream())
        self.assertEqual(self.sessions.row["text"], "")
        self.assertEqual(self.texts()[-1][2:], ("progress", ""))

    async def test_a_stop_request_while_writing_leaves_no_text_behind(self):
        model = StreamingModel([("Half of an ans", asyncio.CancelledError())])
        with self.assertRaises((asyncio.CancelledError, NodeCancelledError)):
            await run(model, FakeGateway(), stream=self.stream())
        self.assertEqual(self.sessions.row["text"], "")

    async def test_a_note_next_to_tool_calls_is_saved_and_cleared_and_the_final_reply_is_an_answer(self):
        turn = calls(("read_repo_file", {"path": "a.py"}), content="I'll read the file first.")
        stream = self.stream()
        await run(StreamingModel([turn, text("All done.")]), FakeGateway(), stream=stream)
        self.assertEqual(self.events, [("assistant.message", {"text": "I'll read the file first.", "epoch": 1})])
        self.assertEqual([kind for _, _, kind, _ in self.texts()], ["progress", "progress", "progress", "answer"])
        self.assertEqual([t for *_, t in self.texts()], ["I'll read the file first.", "", "All done.", "All done."])
        self.assertEqual((self.sessions.row["epoch"], self.sessions.row["kind"], self.sessions.row["text"]), (2, "answer", "All done."))
