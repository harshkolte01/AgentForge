"""Live reply streaming: adapter assembly and the best-effort writer (plain text, no JSON field extraction)."""
from decimal import Decimal
from types import SimpleNamespace as NS
from unittest import IsolatedAsyncioTestCase, TestCase
from uuid import uuid4

from agentforge.contracts.tasks import MAX_ANSWER_CHARS
from agentforge.llm.models import ModelProviderError, ModelRates, ModelUsage
from agentforge.llm.openai_adapter import OpenAIAdapter
from agentforge.orchestration.stream import FLUSH_SECONDS, ReplyStream, clear_stream


def chunk(content=None, calls=None, finish=None, usage=None, choices=True, model="reported"):
    delta = NS(content=content, tool_calls=calls)
    return NS(choices=[NS(delta=delta, finish_reason=finish)] if choices else [], usage=usage, model=model)


class FakeStream:
    def __init__(self, items, fail_at=None):
        self.items, self.fail_at, self.closed = items, fail_at, False

    def __aiter__(self):
        async def run():
            for index, item in enumerate(self.items):
                if index == self.fail_at:
                    raise RuntimeError("connection reset")
                yield item
        return run()

    async def close(self):
        self.closed = True


class AdapterStreamingTests(IsolatedAsyncioTestCase):
    def adapter(self, stream):
        adapter = OpenAIAdapter(api_key="k", model_id="m", rates=ModelRates(Decimal(1), Decimal(2)),
            context_window_tokens=100_000, max_output_tokens=1000)
        async def create(**kwargs):
            self.kwargs = kwargs
            return stream
        adapter._client = NS(chat=NS(completions=NS(create=create)))
        return adapter

    async def test_content_streams_and_the_final_response_matches_the_non_streaming_shape(self):
        stream = FakeStream([chunk("He"), chunk("llo"), chunk(finish="stop"),
                             chunk(choices=False, usage=NS(prompt_tokens=10, completion_tokens=5))])
        seen = []
        response = await self.adapter(stream).complete([{"role": "user", "content": "x"}], [], on_text=seen.append)
        self.assertEqual(seen, ["He", "llo"])
        self.assertEqual(response.message, {"role": "assistant", "content": "Hello"})
        self.assertEqual((response.finish_reason, response.model_id), ("stop", "reported"))
        self.assertEqual(response.usage, ModelUsage(10, 5, Decimal("0.00002")))
        self.assertTrue(stream.closed)
        self.assertTrue(self.kwargs["stream"] and self.kwargs["stream_options"] == {"include_usage": True})
        self.assertNotIn("response_format", self.kwargs)  # the reply is plain text, never a JSON object
        self.assertIs(self.kwargs["store"], False)

    async def test_tool_calls_are_reassembled_from_fragments(self):
        call = lambda index, id=None, name=None, args=None: [NS(index=index, id=id, function=NS(name=name, arguments=args))]
        stream = FakeStream([chunk(calls=call(0, "c1", "read_task_input", '{"input_')), chunk(calls=call(0, None, None, 'kind":"goal"}')),
                             chunk(calls=call(1, "c2", "web_search", "{}")), chunk(finish="tool_calls"),
                             chunk(choices=False, usage=NS(prompt_tokens=3, completion_tokens=2))])
        response = await self.adapter(stream).complete([{"role": "user", "content": "x"}], [], on_text=lambda _: None)
        self.assertNotIn("content", response.message)
        self.assertEqual([item["id"] for item in response.message["tool_calls"]], ["c1", "c2"])
        self.assertEqual(response.message["tool_calls"][0]["function"], {"name": "read_task_input", "arguments": '{"input_kind":"goal"}'})
        self.assertEqual(response.finish_reason, "tool_calls")

    async def test_text_next_to_tool_calls_goes_to_narration_and_is_kept_in_the_message(self):
        tool = {"type": "function", "function": {"name": "web_search", "parameters": {"type": "object"}}}
        call = [NS(index=0, id="c1", function=NS(name="web_search", arguments="{}"))]
        stream = FakeStream([chunk("I'll search."), chunk(calls=call), chunk(finish="tool_calls")])
        reply, narration = [], []
        response = await self.adapter(stream).complete([{"role": "user", "content": "x"}], [tool],
                                                       on_text=reply.append, on_narration=narration.append)
        self.assertEqual((reply, narration), ([], ["I'll search."]))
        self.assertEqual(response.message["content"], "I'll search.")

    async def test_the_finish_reason_is_reported_as_the_provider_gave_it(self):
        for reason in ("stop", "length", "content_filter"):
            response = await self.adapter(FakeStream([chunk("partial"), chunk(finish=reason)])).complete([], [], on_text=lambda _: None)
            self.assertEqual(response.finish_reason, reason)

    async def test_a_failing_progress_callback_never_fails_the_call(self):
        def broken(_):
            raise RuntimeError("ui failure")
        response = await self.adapter(FakeStream([chunk("{}"), chunk(finish="stop")])).complete([], [], on_text=broken)
        self.assertEqual(response.message["content"], "{}")
        self.assertEqual(response.usage.cost_usd, None)  # no usage reported: cost stays unknown, never guessed

    async def test_a_dropped_connection_is_a_provider_error_with_the_failure_text_and_the_stream_is_closed(self):
        stream = FakeStream([chunk("{"), chunk("}")], fail_at=1)
        with self.assertRaises(ModelProviderError) as caught:
            await self.adapter(stream).complete([], [], on_text=lambda _: None)
        self.assertTrue(stream.closed)
        self.assertEqual(caught.exception.detail, "connection reset")
        self.assertFalse(caught.exception.retriable)  # output had begun

    async def test_a_stream_that_never_finishes_is_rejected(self):
        with self.assertRaises(ModelProviderError):
            await self.adapter(FakeStream([chunk("{}")])).complete([], [], on_text=lambda _: None)


class FakeSessions:
    """In-memory `task_streams` that obeys the statements ReplyStream issues (upsert, fenced update, clear) and records them.

    `row` is the live row ({"epoch", "revision", "kind", "text"}) once something was written; `writes` lists the
    statement verbs; `last_values` the parameters of the latest write; `history` the row after each change.
    """
    def __init__(self, fail=False):
        self.writes, self.fail, self.row, self.last_values, self.history = [], fail, None, {}, []

    @property
    def epoch(self):
        return self.row["epoch"] if self.row else 0

    def __call__(self):
        outer = self
        class Session:
            def __enter__(self_inner): return self_inner
            def __exit__(self_inner, *args): return False
            def begin(self_inner): return self_inner
            def execute(self_inner, statement):
                if outer.fail:
                    raise RuntimeError("relation task_streams does not exist")
                compiled = statement.compile()
                sql, params, row = str(compiled), compiled.params, outer.row
                outer.writes.append(sql.split()[0])
                outer.last_values = params
                # A live tool call also names itself (tool, call_id, detail); a row without one carries no such keys.
                named = {key: params[key] for key in ("tool", "call_id", "detail") if params.get(key) is not None}
                if sql.startswith("INSERT"):
                    outer.row = {"epoch": 1 if row is None else row["epoch"] + 1, "revision": 0 if row is None else row["revision"] + 1,
                                 "kind": params["kind"], "text": params["text"], **named}
                    outer.history.append(outer.row)
                    return NS(one=lambda: NS(epoch=outer.row["epoch"], revision=outer.row["revision"]))
                if row is None:
                    return NS(rowcount=0)
                plain = {key: value for key, value in row.items() if key not in ("tool", "call_id", "detail")}
                if "task_streams.revision +" in sql:  # clear_stream
                    outer.row = {**plain, "revision": row["revision"] + 1, "kind": params["kind"], "text": params["text"]}
                    outer.history.append(outer.row)
                    return NS(rowcount=1)
                if row["epoch"] != params["epoch_1"] or row["revision"] > params["revision_1"]:
                    return NS(rowcount=0)  # fenced out by a newer epoch or revision
                outer.row = {**plain, "revision": params["revision"], "kind": params["kind"], "text": params["text"], **named}
                outer.history.append(outer.row)
                return NS(rowcount=1)
        return Session()


class ReplyStreamTests(TestCase):
    def stream(self, **kw):
        self.now = 0.0
        sessions = FakeSessions(**kw)
        return ReplyStream(sessions, uuid4(), clock=lambda: self.now), sessions

    def test_first_text_opens_an_epoch_and_later_text_is_throttled(self):
        stream, sessions = self.stream()
        stream.feed("Hel")
        self.assertEqual(sessions.writes, ["INSERT"])                       # epoch opened, already holding the first text
        self.assertEqual(sessions.last_values["text"], "Hel")
        self.now += FLUSH_SECONDS / 4
        stream.feed("lo ")
        self.assertEqual(len(sessions.writes), 1)                           # within the window: buffered only
        self.now += FLUSH_SECONDS
        stream.feed("there")
        self.assertEqual(sessions.writes, ["INSERT", "UPDATE"])
        self.assertEqual(sessions.last_values["text"], "Hello there")
        stream.end()
        self.assertEqual(len(sessions.writes), 2)                           # nothing left unpublished

    def test_the_text_is_shown_exactly_as_written_with_no_json_decoding(self):
        stream, sessions = self.stream()
        stream.feed('{"summary": "not a field", "text": "a\\nb"}\nline two')
        self.assertEqual(sessions.last_values["text"], '{"summary": "not a field", "text": "a\\nb"}\nline two')

    def test_end_publishes_the_buffered_tail(self):
        stream, sessions = self.stream()
        stream.feed("ab")
        self.now += 0.01
        stream.feed("cd")
        stream.end()
        self.assertEqual(sessions.last_values["text"], "abcd")

    def test_a_new_attempt_starts_a_new_epoch_and_forgets_old_text(self):
        stream, sessions = self.stream()
        stream.feed("first")
        stream.begin()
        self.now += 1
        stream.feed("second")
        stream.end()
        self.assertEqual(sessions.writes.count("INSERT"), 2)
        self.assertEqual(sessions.epoch, 2)
        self.assertEqual(sessions.last_values["text"], "second")

    def test_nothing_is_written_for_empty_or_blank_text(self):
        stream, sessions = self.stream()
        stream.begin()
        stream.feed("")
        stream.feed("  \n")
        stream.end()
        self.assertEqual(sessions.writes, [])

    def test_text_is_capped_like_the_saved_message(self):
        stream, sessions = self.stream()
        stream.feed("x" * (MAX_ANSWER_CHARS + 500))
        stream.end()
        self.assertEqual(len(sessions.last_values["text"]), MAX_ANSWER_CHARS)

    def test_text_is_progress_until_the_call_turns_out_to_be_the_answer(self):
        stream, sessions = self.stream()
        stream.begin()
        stream.feed_narration("The answer")
        stream.end()
        self.assertEqual(sessions.row["kind"], "progress")
        stream.commit_reply()
        self.assertEqual((sessions.row["kind"], sessions.row["text"]), ("answer", "The answer"))

    def test_any_database_failure_switches_streaming_off_without_raising(self):
        stream, sessions = self.stream(fail=True)
        stream.feed("hello")
        stream.end()
        self.assertTrue(stream.disabled)
        stream.feed("more")  # still silent
        stream.commit_reply()
        stream.discard()
        clear_stream(FakeSessions(fail=True), uuid4())  # cleanup never raises either
