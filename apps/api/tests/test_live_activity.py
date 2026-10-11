"""What a person sees while an agent works: its notes between tool calls, steps as they start, and runs that recover.

Covers the live text channel (notes saved as ordered events), the model adapter's narration callback, the timeline
events' identity and the worker's stop monitor.
"""
import asyncio
import json
from decimal import Decimal
from types import SimpleNamespace as NS
from unittest import IsolatedAsyncioTestCase, TestCase

from agentforge.llm.models import ModelRates, ModelResponse, ModelUsage
from agentforge.llm.openai_adapter import OpenAIAdapter
from agentforge.orchestration.stream import FLUSH_SECONDS, ReplyStream
from agentforge.telemetry.runtime import ObservedModel
from agentforge.tools.general import NUMBER

from test_streaming import FakeSessions, FakeStream, chunk


def tool_call(name="read_repo_file", args=None, ident="c1"):
    return NS(index=0, id=ident, function=NS(name=name, arguments=json.dumps(args or {})))


class NarrationStreamTests(TestCase):
    def stream(self, emit=True):
        self.now, self.events = 0.0, []
        self.sessions = FakeSessions()
        sink = (lambda kind, payload: self.events.append((kind, payload))) if emit else None
        return ReplyStream(self.sessions, "task", emit=sink, clock=lambda: self.now)

    def test_a_note_is_published_live_then_saved_as_an_event_and_cleared(self):
        stream = self.stream()
        stream.begin()
        stream.feed_narration("I'll search ")
        self.now += FLUSH_SECONDS
        stream.feed_narration("for the announcement.")
        stream.end()
        self.assertEqual(self.sessions.last_values["text"], "I'll search for the announcement.")
        stream.commit_narration()
        self.assertEqual(self.events, [("assistant.message", {"text": "I'll search for the announcement.", "epoch": 1})])
        self.assertEqual(self.sessions.last_values["text"], "")  # the live copy no longer repeats it

    def test_the_reply_after_a_note_starts_a_new_epoch_so_it_is_never_ignored(self):
        stream = self.stream()
        stream.begin()
        stream.feed_narration("Let me check one more thing.")
        stream.end()
        stream.commit_narration()
        stream.begin()
        stream.feed_narration("The answer")
        stream.end()
        stream.commit_reply()
        self.assertEqual(self.events[0][1]["epoch"], 1)
        self.assertEqual(self.sessions.epoch, 2)  # a reader that took the note at epoch 1 still accepts the reply
        self.assertEqual((self.sessions.row["kind"], self.sessions.row["text"]), ("answer", "The answer"))

    def test_the_note_is_trimmed_and_blank_text_is_not_an_event(self):
        stream = self.stream()
        stream.begin()
        stream.feed_narration("  \n Reading the file.  \n")
        stream.commit_narration()
        self.assertEqual(self.events, [("assistant.message", {"text": "Reading the file.", "epoch": 1})])
        stream = self.stream()
        stream.begin()
        stream.feed_narration("   ")
        stream.commit_narration()
        self.assertEqual(self.events, [])

    def test_discarding_drops_a_calls_text_without_an_event(self):
        stream = self.stream()
        stream.begin()
        stream.feed_narration("Here you go")
        stream.discard()
        stream.commit_narration()
        self.assertEqual(self.events, [])
        self.assertEqual(self.sessions.last_values["text"], "")

    def test_a_stream_without_an_event_sink_shows_notes_live_but_saves_none(self):
        stream = self.stream(emit=False)
        self.assertFalse(stream.narrates)
        stream.begin()
        stream.feed_narration("hello")
        self.assertEqual(self.sessions.last_values["text"], "hello")
        stream.commit_narration()
        self.assertEqual(self.sessions.last_values["text"], "")

    def test_the_note_is_saved_even_when_the_live_copy_is_switched_off(self):
        events = []
        stream = ReplyStream(FakeSessions(fail=True), "task", emit=lambda k, p: events.append((k, p)), clock=lambda: 0.0)
        stream.begin()
        stream.feed_narration("I'll read the file.")
        self.assertTrue(stream.disabled)
        stream.commit_narration()
        self.assertEqual(events, [("assistant.message", {"text": "I'll read the file."})])

    def test_discard_blanks_whatever_is_live(self):
        stream = self.stream()
        stream.begin()
        stream.feed("Hello")
        stream.discard()
        self.assertEqual(self.sessions.last_values["text"], "")


class AdapterNarrationTests(IsolatedAsyncioTestCase):
    def adapter(self, stream):
        adapter = OpenAIAdapter(api_key="k", model_id="m", rates=ModelRates(Decimal(1), Decimal(2)),
            context_window_tokens=100_000, max_output_tokens=1000)
        async def create(**kwargs):
            return stream
        adapter._client = NS(chat=NS(completions=NS(create=create)))
        return adapter

    async def test_content_next_to_tool_calls_goes_to_the_narration_callback_only(self):
        stream = FakeStream([chunk("I'll "), chunk("search."), chunk(calls=[tool_call("web_search")]), chunk(finish="tool_calls")])
        replies, notes = [], []
        tools = [{"type": "function", "function": {"name": "web_search", "parameters": {}}}]
        response = await self.adapter(stream).complete([{"role": "user", "content": "x"}], tools, on_text=replies.append, on_narration=notes.append)
        self.assertEqual(("".join(notes), replies), ("I'll search.", []))
        self.assertEqual(response.message["content"], "I'll search.")
        self.assertEqual(response.message["tool_calls"][0]["function"]["name"], "web_search")

    async def test_narration_alone_is_enough_to_stream(self):
        stream = FakeStream([chunk("Working on it."), chunk(finish="stop")])
        notes = []
        tools = [{"type": "function", "function": {"name": "web_search", "parameters": {}}}]
        await self.adapter(stream).complete([], tools, on_narration=notes.append)
        self.assertEqual(notes, ["Working on it."])

    async def test_without_tools_content_is_still_the_reply(self):
        stream = FakeStream([chunk("Hi there"), chunk(finish="stop")])
        replies, notes = [], []
        await self.adapter(stream).complete([], [], on_text=replies.append, on_narration=notes.append)
        self.assertEqual((replies, notes), (["Hi there"], []))

    async def test_a_failing_narration_callback_never_fails_the_call(self):
        def broken(_):
            raise RuntimeError("ui")
        stream = FakeStream([chunk("hi"), chunk(finish="stop")])
        tools = [{"type": "function", "function": {"name": "web_search", "parameters": {}}}]
        response = await self.adapter(stream).complete([], tools, on_narration=broken)
        self.assertEqual(response.message["content"], "hi")


def harness():
    events = []
    store = NS(progress=lambda kind, data: events.append((kind, data)), lease=NS(task_id="t"))
    from contextlib import contextmanager

    @contextmanager
    def span(*args, **kwargs):
        yield None
    return events, store, NS(span=span)


class TimelineEventTests(IsolatedAsyncioTestCase):
    # Tool start/finish events are emitted by the gateway: see tests/test_tool_lifecycle.py.
    async def test_narration_callback_is_forwarded_only_when_given(self):
        events, store, telemetry = harness()
        seen = []

        class Inner:
            model_id = "m"
            def maximum_request_cost(self, messages, tools): return Decimal(0)
            async def complete(self, messages, tools, **kwargs):
                seen.append(sorted(kwargs))
                return ModelResponse({"role": "assistant", "content": ""}, ModelUsage(1, 1, Decimal(0)), "m", "stop")
        model = ObservedModel(Inner(), telemetry, store)
        await model.complete([], [])
        await model.complete([], [], on_narration=print)
        self.assertEqual(seen, [[], ["on_narration"]])


class SpreadsheetEscapingTests(TestCase):
    def test_numbers_are_not_mistaken_for_formulas(self):
        for cell in ("-5", "+3.5", "-0.25", "1e-3", "-.5"):
            self.assertTrue(NUMBER.fullmatch(cell), cell)
        for cell in ("-5+2", "=1+1", "+cmd", "-A1", "@sum"):
            self.assertFalse(NUMBER.fullmatch(cell), cell)


class CancelMonitorTests(IsolatedAsyncioTestCase):
    async def test_a_stop_request_is_noticed_between_heartbeats(self):
        from agentforge.repositories.jobs import TaskCancelled
        from agentforge.workers import leases
        beats, polls = [], []
        jobs = NS(heartbeat=lambda lease: beats.append(1))
        store = NS(task=lambda: (polls.append(1), NS(cancel_requested_at=object() if len(polls) >= 3 else None))[1])
        real_sleep = asyncio.sleep

        async def fast(_seconds):
            await real_sleep(0)
        original, leases.asyncio.sleep = leases.asyncio.sleep, fast
        try:
            with self.assertRaises(TaskCancelled):
                await leases.monitor(jobs, NS(), store, heartbeat_sec=10)
        finally:
            leases.asyncio.sleep = original
        self.assertEqual((len(polls), len(beats)), (3, 0))  # three polls, still before the first heartbeat is due


class ResponseHeaderTests(TestCase):
    """The event stream must stay uncompressed end to end, or live updates are held back until the stream ends."""

    def app(self):
        from fastapi.responses import StreamingResponse
        from agentforge.main import create_app
        from agentforge.settings import Settings
        app = create_app(Settings(_env_file=None, app_env="test", auth_mode="disabled", llm_api_key=None, tavily_api_key=None, neatlogs_api_key=None))
        app.add_api_route("/api/v1/_probe/stream", lambda: StreamingResponse(iter([b"retry: 1\n\n"]), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache, no-store, no-transform"}))
        app.add_api_route("/api/v1/_probe/plain", lambda: {"ok": True})
        return app

    def test_a_route_that_forbids_transforms_keeps_that_policy(self):
        from fastapi.testclient import TestClient
        response = TestClient(self.app()).get("/api/v1/_probe/stream")
        cache = response.headers["cache-control"]
        self.assertIn("no-transform", cache)
        self.assertIn("no-store", cache)
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")

    def test_other_api_responses_are_still_never_cacheable(self):
        from fastapi.testclient import TestClient
        response = TestClient(self.app()).get("/api/v1/_probe/plain")
        self.assertEqual(response.headers["cache-control"], "no-store")
