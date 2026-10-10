"""Live reply streaming: field extraction, adapter assembly, the best-effort writer and runtime wiring."""
import asyncio
import json
import random
from decimal import Decimal
from types import SimpleNamespace as NS
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock
from uuid import uuid4

from agentforge.contracts.agent_definition import Budget
from agentforge.llm.models import ModelProviderError, ModelRates, ModelResponse, ModelUsage
from agentforge.llm.openai_adapter import OpenAIAdapter
from agentforge.llm.streaming import JsonStringFieldStream
from agentforge.orchestration.stream import FLUSH_SECONDS, MAX_CHARS, ReplyStream, clear_stream
from agentforge.subagents.registry import load_builtin
from agentforge.subagents.runtime import ChildRuntime

REPLY = 'Hi "there"!\nSecond line \\ back é 😀 done'
DOC = {"status": "PASS", "summary": "internal \"note\"", "evidence_ids": ["a", "response"], "intent": "conversation",
       "response": REPLY, "nested": {"response": "NOT THIS"}, "use_repository": False}


def read(text, field="response", size=None):
    reader, out = JsonStringFieldStream(field), ""
    if size is None:
        return reader.feed(text)
    for start in range(0, len(text), size):
        out += reader.feed(text[start:start + size])
    return out


class FieldExtractionTests(TestCase):
    def test_decodes_the_target_field_exactly_however_the_stream_is_chunked(self):
        for encoded in (json.dumps(DOC), json.dumps(DOC, ensure_ascii=False)):
            self.assertEqual(read(encoded), REPLY)
            for size in (1, 2, 3, 5, 11):
                self.assertEqual(read(encoded, size=size), REPLY, size)
        generator = random.Random(7)
        encoded = json.dumps(DOC)
        for _ in range(200):
            self.assertEqual(read(encoded, size=generator.randint(1, 9)), REPLY)

    def test_only_the_top_level_target_field_is_ever_emitted(self):
        self.assertEqual(read('{"summary":"private","response":"public"}'), "public")
        self.assertEqual(read('{"summary":"mine","response":"theirs"}', "summary"), "mine")
        self.assertEqual(read('{"a":{"summary":"deep"},"summary":"top"}', "summary"), "top")
        self.assertEqual(read('{"x":"response","response":"v"}'), "v")
        self.assertEqual(read('{"evidence_ids":["response"],"intent":"research"}'), "")

    def test_a_null_or_non_string_value_streams_nothing(self):
        self.assertEqual(read('{"response":null,"summary":"x"}'), "")
        self.assertEqual(read('{"response": 5, "summary":"x"}'), "")

    def test_partial_input_yields_what_is_complete_so_far(self):
        reader = JsonStringFieldStream("response")
        self.assertEqual(reader.feed('{"intent":"conversation","respo'), "")
        self.assertEqual(reader.feed('nse":"Hel'), "Hel")
        self.assertEqual(reader.feed('lo \\u00'), "lo ")
        self.assertEqual(reader.feed('e9"}'), "é")
        self.assertTrue(reader.done)


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
        stream = FakeStream([chunk('{"response":"He'), chunk('llo"}'), chunk(finish="stop"),
                             chunk(choices=False, usage=NS(prompt_tokens=10, completion_tokens=5))])
        seen = []
        response = await self.adapter(stream).complete([{"role": "user", "content": "x"}], [], on_text=seen.append)
        self.assertEqual(seen, ['{"response":"He', 'llo"}'])
        self.assertEqual(response.message, {"role": "assistant", "content": '{"response":"Hello"}'})
        self.assertEqual((response.finish_reason, response.model_id), ("stop", "reported"))
        self.assertEqual(response.usage, ModelUsage(10, 5, Decimal("0.00002")))
        self.assertTrue(stream.closed)
        self.assertTrue(self.kwargs["stream"] and self.kwargs["stream_options"] == {"include_usage": True})
        self.assertEqual(self.kwargs["response_format"], {"type": "json_object"})
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

    async def test_a_failing_progress_callback_never_fails_the_call(self):
        def broken(_):
            raise RuntimeError("ui failure")
        response = await self.adapter(FakeStream([chunk("{}"), chunk(finish="stop")])).complete([], [], on_text=broken)
        self.assertEqual(response.message["content"], "{}")
        self.assertEqual(response.usage.cost_usd, None)  # no usage reported: cost stays unknown, never guessed

    async def test_a_dropped_connection_is_a_safe_provider_error_and_the_stream_is_closed(self):
        stream = FakeStream([chunk("{"), chunk("}")], fail_at=1)
        with self.assertRaises(ModelProviderError):
            await self.adapter(stream).complete([], [], on_text=lambda _: None)
        self.assertTrue(stream.closed)

    async def test_a_stream_that_never_finishes_is_rejected(self):
        with self.assertRaises(ModelProviderError):
            await self.adapter(FakeStream([chunk("{}")])).complete([], [], on_text=lambda _: None)


class FakeSessions:
    """Records each write; mimics `with sessions() as s, s.begin()` and returns the epoch for the upsert."""
    def __init__(self, fail=False):
        self.writes, self.epoch, self.fail = [], 0, False
        self.fail = fail

    def __call__(self):
        outer = self
        class Session:
            def __enter__(self_inner): return self_inner
            def __exit__(self_inner, *args): return False
            def begin(self_inner): return self_inner
            def execute(self_inner, statement):
                if outer.fail:
                    raise RuntimeError("relation task_streams does not exist")
                text = str(statement)
                outer.writes.append(text.split()[0])
                if text.startswith("INSERT"):
                    outer.epoch += 1
                    return NS(scalar_one=lambda: outer.epoch)
                outer.last_values = statement.compile().params
                return NS()
        return Session()


class ReplyStreamTests(TestCase):
    def stream(self, **kw):
        self.now = 0.0
        sessions = FakeSessions(**kw)
        return ReplyStream(sessions, uuid4(), "response", clock=lambda: self.now), sessions

    def test_first_text_opens_an_epoch_and_later_text_is_throttled(self):
        stream, sessions = self.stream()
        stream.feed('{"intent":"conversation","response":"Hel')
        self.assertEqual(sessions.writes, ["INSERT", "UPDATE"])            # epoch opened, first text flushed
        self.now += FLUSH_SECONDS / 4
        stream.feed("lo ")
        self.assertEqual(len(sessions.writes), 2)                           # within the window: buffered only
        self.now += FLUSH_SECONDS
        stream.feed('there"}')
        self.assertEqual(len(sessions.writes), 3)
        self.assertEqual(sessions.last_values["text"], "Hello there")
        stream.end()
        self.assertEqual(len(sessions.writes), 3)                           # nothing left unpublished

    def test_end_publishes_the_buffered_tail(self):
        stream, sessions = self.stream()
        stream.feed('{"response":"ab')
        self.now += 0.01
        stream.feed('cd"}')
        stream.end()
        self.assertEqual(sessions.last_values["text"], "abcd")

    def test_a_new_attempt_starts_a_new_epoch_and_forgets_old_text(self):
        stream, sessions = self.stream()
        stream.feed('{"response":"first')
        stream.begin()
        self.now += 1
        stream.feed('{"response":"second"}')
        stream.end()
        self.assertEqual(sessions.writes.count("INSERT"), 2)
        self.assertEqual(sessions.epoch, 2)
        self.assertEqual(sessions.last_values["text"], "second")

    def test_nothing_is_written_when_the_field_is_null_or_a_call_only_uses_tools(self):
        stream, sessions = self.stream()
        stream.feed('{"intent":"research","response":null}')
        stream.end()
        self.assertEqual(sessions.writes, [])

    def test_text_is_capped_like_the_saved_message(self):
        stream, sessions = self.stream()
        stream.feed('{"response":"' + "x" * (MAX_CHARS + 500))
        stream.end()
        self.assertEqual(len(sessions.last_values["text"]), MAX_CHARS)

    def test_any_database_failure_switches_streaming_off_without_raising(self):
        stream, sessions = self.stream(fail=True)
        stream.feed('{"response":"hello"}')
        stream.end()
        self.assertTrue(stream.disabled)
        stream.feed('{"response":"more"}')  # still silent
        clear_stream(FakeSessions(fail=True), uuid4())  # cleanup never raises either


class RuntimeStreamingTests(IsolatedAsyncioTestCase):
    async def test_tokens_flow_to_the_sink_per_call_and_the_result_is_unchanged(self):
        payload = {"status": "PASS", "summary": "Chose to answer directly.", "evidence_ids": ["e1"], "intent": "conversation",
                   "response": "Hello there", "use_repository": False}
        text = json.dumps(payload)

        class Model:
            model_id = "m"
            def maximum_request_cost(self, messages, tools): return Decimal("0.001")
            async def complete(self, messages, tools, on_text=None):
                for start in range(0, len(text), 6):
                    on_text(text[start:start + 6])
                return ModelResponse({"role": "assistant", "content": text}, ModelUsage(1, 1, Decimal("0.0001")), "m", "stop")

        events = []
        sink = NS(begin=lambda: events.append("begin"), feed=lambda piece: events.append(piece), end=lambda: events.append("end"))
        budget = Budget(max_steps=4, max_tool_calls=4, max_llm_cost_usd=0.2, max_wall_time_sec=60)
        scope = NS(budget=budget, allowed_tools=("read_task_input",))
        budgets = NS(check_active=lambda _id: None, authorize_operation=lambda *a, **k: None, record_usage=lambda *a, **k: None)
        reservation = NS(id=uuid4(), run_id=uuid4(), deadline=__import__("datetime").datetime.now(__import__("datetime").timezone.utc) + __import__("datetime").timedelta(seconds=60))
        result = await ChildRuntime().run(definition=load_builtin("intent-router", 3), ctx=NS(task_id=uuid4()), scope=scope,
            model=Model(), gateway=NS(schemas=lambda: []), budgets=budgets, reservation=reservation,
            messages=[{"role": "user", "content": "x"}], resources=NS(verify_references=AsyncMock()), reauthorize=AsyncMock(), stream=sink)
        self.assertEqual(events[0], "begin")
        self.assertEqual(events[-1], "end")
        self.assertEqual(read("".join(events[1:-1])), "Hello there")
        self.assertEqual(result.status, "PASS")

    async def test_models_without_streaming_support_still_work_when_no_sink_is_given(self):
        class Plain:
            model_id = "m"
            def maximum_request_cost(self, messages, tools): return Decimal("0.001")
            async def complete(self, messages, tools):  # note: no on_text parameter
                return ModelResponse({"role": "assistant", "content": json.dumps({"status": "PARTIAL", "summary": "ok"})}, ModelUsage(1, 1, Decimal("0")), "m", "stop")
        import datetime
        budget = Budget(max_steps=4, max_tool_calls=4, max_llm_cost_usd=0.2, max_wall_time_sec=60)
        reservation = NS(id=uuid4(), run_id=uuid4(), deadline=datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=60))
        result = await ChildRuntime().run(definition=load_builtin("verifier", 3).model_copy(update={"output_schema_id": "research_result_v1"}),
            ctx=NS(task_id=uuid4()), scope=NS(budget=budget, allowed_tools=()), model=Plain(), gateway=NS(schemas=lambda: []),
            budgets=NS(check_active=lambda _i: None, authorize_operation=lambda *a, **k: None, record_usage=lambda *a, **k: None),
            reservation=reservation, messages=[], resources=NS(verify_references=AsyncMock()), reauthorize=AsyncMock())
        self.assertEqual(result.status, "PARTIAL")
