"""Reasoning effort: which efforts a model offers, how a person's choice is kept, and how a request at an effort is sent.

gpt-6-luna takes function calls on Chat Completions only without reasoning, so any other effort goes through the Responses
endpoint: these tests pin the request that endpoint is sent, how the history is translated, and how its replies and streams
come back as the chat-format message the runtime already understands."""
from decimal import Decimal
from types import SimpleNamespace as NS
from unittest import IsolatedAsyncioTestCase, TestCase
from uuid import uuid4

import httpx
import openai

from agentforge.llm import effort as efforts
from agentforge.llm.models import ModelConfigurationError, ModelContextLimit, ModelProviderError, ModelRates
from agentforge.llm.openai_adapter import (OpenAIAdapter, message_from_response, to_responses_input, to_responses_tool_choice,
                                           to_responses_tools)

RATES = ModelRates(Decimal("0.10"), Decimal("0.50"))
TOOL = {"type": "function", "function": {"name": "read_file", "description": "Reads a file.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}}
HISTORY = [{"role": "system", "content": "Be brief."}, {"role": "user", "content": "hi"}]


def luna(effort="high", **options):
    return OpenAIAdapter(api_key="k", model_id="gpt-6-luna", rates=RATES, context_window_tokens=32_000, max_output_tokens=4096,
                         timeout_sec=100, reasoning_effort=effort, **options)


def text_item(text):
    return NS(type="message", content=[NS(type="output_text", text=text)])


def call_item(call_id, name, arguments):
    return NS(type="function_call", call_id=call_id, name=name, arguments=arguments)


def reply(output, status="completed", reason=None, input_tokens=10, output_tokens=5):
    return NS(status=status, output=output, error=None, incomplete_details=NS(reason=reason) if reason else None, model="reported",
              usage=NS(input_tokens=input_tokens, output_tokens=output_tokens))


class Responses:
    def __init__(self, outcomes):
        self.outcomes, self.requests = list(outcomes), []

    async def create(self, **kwargs):
        self.requests.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class Chat:
    def __init__(self):
        self.requests = []

    async def create(self, **kwargs):
        self.requests.append(kwargs)
        return NS(choices=[NS(message=NS(model_dump=lambda exclude_none=True: {"role": "assistant", "content": "ok"}), finish_reason="stop")],
                  usage=NS(prompt_tokens=1, completion_tokens=1), model="m")


class Stream:
    def __init__(self, events, error=None):
        self.events, self.error, self.closed = events, error, False

    def __aiter__(self):
        async def run():
            for event in self.events:
                yield event
            if self.error:
                raise self.error
        return run()

    async def close(self):
        self.closed = True


def attached(model, responses=None, chat=None):
    model._client = NS(responses=responses, chat=NS(completions=chat))
    return model


class EffortRulesTests(TestCase):
    def test_luna_offers_every_effort_it_documents_and_the_default_is_high(self):
        self.assertEqual(efforts.efforts_for("gpt-6-luna"), ("none", "low", "medium", "high", "xhigh", "max"))
        self.assertEqual(efforts.default_for("gpt-6-luna"), "high")
        self.assertEqual(efforts.efforts_for("gpt-6-luna-2026"), efforts.efforts_for("gpt-6-luna"))

    def test_a_model_nobody_verified_offers_no_control(self):
        for model in ("gpt-4o", None, ""):
            self.assertEqual((efforts.efforts_for(model), efforts.default_for(model), efforts.resolve(model, "high")), ((), None, None))

    def test_a_choice_the_model_does_not_take_falls_back_to_the_default(self):
        self.assertEqual(efforts.resolve("gpt-6-luna", "xhigh"), "xhigh")
        self.assertEqual(efforts.resolve("gpt-6-luna", None), "high")
        self.assertEqual(efforts.resolve("gpt-6-luna", "minimal"), "high")

    def test_only_an_asked_for_effort_leaves_chat_completions(self):
        self.assertFalse(efforts.uses_responses("gpt-6-luna", None))
        self.assertFalse(efforts.uses_responses("gpt-6-luna", "none"))
        self.assertTrue(efforts.uses_responses("gpt-6-luna", "low"))
        self.assertFalse(efforts.uses_responses("gpt-4o", "high"))

    def test_longer_thinking_is_given_longer_to_start_answering_up_to_a_ceiling(self):
        self.assertEqual([efforts.read_timeout(100, e) for e in ("none", "low", "medium", "high", "xhigh", "max")], [100, 100, 100, 200, 300, 400])
        self.assertEqual(efforts.read_timeout(900, "max"), efforts.MAX_WAIT_SEC)


class ConfigurationTests(TestCase):
    def test_an_effort_the_model_does_not_list_is_refused(self):
        for bad in ("minimal", "bogus"):
            with self.assertRaises(ModelConfigurationError):
                luna(bad)

    def test_switching_effort_copies_the_adapter_and_shares_the_client(self):
        base = luna(None)
        high = base.with_reasoning_effort("high")
        self.assertIsNone(base.reasoning_effort)
        self.assertEqual(high.reasoning_effort, "high")
        self.assertIs(high._client, base._client)
        self.assertIs(high.with_reasoning_effort("high"), high)
        with self.assertRaises(ModelConfigurationError):
            base.with_reasoning_effort("minimal")

    def test_small_side_requests_never_think(self):
        self.assertEqual(luna("max").with_output_limit(64).reasoning_effort, "none")
        plain = OpenAIAdapter(api_key="k", model_id="gpt-4o", rates=RATES, context_window_tokens=32_000, max_output_tokens=4096)
        self.assertIsNone(plain.with_output_limit(64).reasoning_effort)


class RequestShapeTests(IsolatedAsyncioTestCase):
    async def test_no_effort_or_none_keeps_chat_completions_and_says_none_to_luna(self):
        for effort in (None, "none"):
            chat = Chat()
            await attached(luna(effort), chat=chat).complete(HISTORY, [TOOL])
            self.assertEqual(chat.requests[0]["reasoning_effort"], "none")

    async def test_an_effort_goes_to_the_responses_endpoint_with_tools_in_the_flat_shape(self):
        responses = Responses([reply([text_item("done")])])
        model = attached(luna("high"), responses=responses)
        answer = await model.complete(HISTORY, [TOOL], tool_choice={"type": "function", "function": {"name": "read_file"}})
        request = responses.requests[0]
        self.assertEqual(request["reasoning"], {"effort": "high"})
        self.assertEqual((request["store"], request["max_output_tokens"], request["parallel_tool_calls"]), (False, 4096, True))
        self.assertEqual(request["tools"], [{"type": "function", "name": "read_file", "parameters": TOOL["function"]["parameters"], "strict": False, "description": "Reads a file."}])
        self.assertEqual(request["tool_choice"], {"type": "function", "name": "read_file"})
        self.assertEqual(request["input"][0], {"role": "developer", "content": "Be brief."})
        self.assertEqual(answer.message, {"role": "assistant", "content": "done"})
        self.assertEqual((answer.finish_reason, answer.model_id), ("stop", "reported"))
        self.assertEqual(answer.usage.cost_usd, Decimal("0.0000035"))

    async def test_the_wait_for_a_reply_grows_with_the_effort(self):
        for effort, seconds in (("low", 100), ("high", 200), ("max", 400)):
            responses = Responses([reply([text_item("x")])])
            await attached(luna(effort), responses=responses).complete(HISTORY, [])
            self.assertEqual(responses.requests[0]["timeout"].read, seconds)

    async def test_without_tools_no_tool_fields_are_sent(self):
        responses = Responses([reply([text_item("x")])])
        await attached(luna("low"), responses=responses).complete(HISTORY, [])
        for key in ("tools", "tool_choice", "parallel_tool_calls"):
            self.assertNotIn(key, responses.requests[0])


class TranslationTests(TestCase):
    def test_tool_calls_and_their_results_become_items_tied_by_call_id_with_no_provider_ids(self):
        history = [{"role": "user", "content": "go"},
                   {"role": "assistant", "content": "Looking.", "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "read_file", "arguments": '{"path":"a"}'}}]},
                   {"role": "tool", "tool_call_id": "call_1", "content": "file text"},
                   {"role": "assistant", "content": ""}]
        self.assertEqual(to_responses_input(history), [
            {"role": "user", "content": "go"},
            {"role": "assistant", "content": "Looking."},
            {"type": "function_call", "call_id": "call_1", "name": "read_file", "arguments": '{"path":"a"}'},
            {"type": "function_call_output", "call_id": "call_1", "output": "file text"}])

    def test_attachments_keep_their_data(self):
        parts = [{"type": "text", "text": "see"}, {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA", "detail": "high"}},
                 {"type": "file", "file": {"filename": "a.pdf", "file_data": "data:application/pdf;base64,AA"}}]
        self.assertEqual(to_responses_input([{"role": "user", "content": parts}])[0]["content"], [
            {"type": "input_text", "text": "see"}, {"type": "input_image", "image_url": "data:image/png;base64,AA", "detail": "high"},
            {"type": "input_file", "filename": "a.pdf", "file_data": "data:application/pdf;base64,AA"}])
        with self.assertRaises(ModelConfigurationError):
            to_responses_input([{"role": "user", "content": [{"type": "input_audio"}]}])

    def test_strictness_is_always_stated_because_the_endpoint_defaults_it_on(self):
        strict = {"type": "function", "function": {"name": "s", "parameters": {"type": "object"}, "strict": True}}
        self.assertEqual([tool["strict"] for tool in to_responses_tools([TOOL, strict])], [False, True])
        self.assertEqual((to_responses_tool_choice(None), to_responses_tool_choice("required")), ("auto", "required"))


class ReplyTests(TestCase):
    def test_text_and_calls_become_one_chat_message(self):
        message, finish = message_from_response(reply([NS(type="reasoning"), text_item("Note."), call_item("c1", "read_file", "{}"), call_item("c2", "read_file", "{}")]))
        self.assertEqual(finish, "tool_calls")
        self.assertEqual(message["content"], "Note.")
        self.assertEqual([call["id"] for call in message["tool_calls"]], ["c1", "c2"])
        self.assertEqual(message["tool_calls"][0], {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}})

    def test_a_refusal_is_the_reply(self):
        refusal = NS(type="message", content=[NS(type="refusal", refusal="I can't help with that.")])
        self.assertEqual(message_from_response(reply([refusal])), ({"role": "assistant", "content": "I can't help with that."}, "stop"))

    def test_running_out_of_output_is_length_and_a_filter_is_reported_as_one(self):
        self.assertEqual(message_from_response(reply([text_item("part")], "incomplete", "max_output_tokens"))[1], "length")
        self.assertEqual(message_from_response(reply([call_item("c", "t", '{"a')], "incomplete", "max_output_tokens"))[1], "length")
        self.assertEqual(message_from_response(reply([text_item("x")], "incomplete", "content_filter"))[1], "content_filter")

    def test_thinking_that_used_the_whole_allowance_says_so_and_names_the_fix(self):
        with self.assertRaises(ModelProviderError) as caught:
            message_from_response(reply([NS(type="reasoning")], "incomplete", "max_output_tokens"))
        self.assertIn("lower effort", caught.exception.shown)
        self.assertFalse(caught.exception.retriable)

    def test_a_failed_response_is_classified_like_any_provider_failure(self):
        failed = reply([], "failed")
        failed.error = NS(code="rate_limit_exceeded", message="slow down")
        with self.assertRaises(openai.APIError) as caught:
            message_from_response(failed)
        self.assertTrue(OpenAIAdapter._classify(caught.exception).retriable)
        failed.error = NS(code="context_length_exceeded", message="too long")
        with self.assertRaises(ModelContextLimit):
            message_from_response(failed)


def delta(text):
    return NS(type="response.output_text.delta", delta=text)


def added(index, call_id, name):
    return NS(type="response.output_item.added", output_index=index, item=call_item(call_id, name, ""))


def args(index, piece):
    return NS(type="response.function_call_arguments.delta", output_index=index, delta=piece)


def finished(response, kind="response.completed"):
    return NS(type=kind, response=response)


class StreamingTests(IsolatedAsyncioTestCase):
    async def run_stream(self, events, tools=True, error=None, **callbacks):
        stream = Stream(events, error)
        responses = Responses([stream])
        model = attached(luna("high", max_retries=0), responses=responses)
        return await model.complete(HISTORY, [TOOL] if tools else [], **callbacks), responses, stream

    async def test_text_beside_calls_is_narration_and_calls_are_drafted_as_written(self):
        narration, drafted = [], []
        final = reply([text_item("Reading."), call_item("c1", "read_file", '{"path":"a"}')])
        events = [NS(type="response.created"), NS(type="response.reasoning_summary_text.delta", delta="hidden"), delta("Read"), delta("ing."),
                  added(1, "c1", "read_file"), args(1, '{"path"'), args(1, ':"a"}'), finished(final)]
        answer, responses, stream = await self.run_stream(events, on_text=lambda t: self.fail("not the reply"),
                                                          on_narration=narration.append, on_tool=lambda *piece: drafted.append(piece))
        self.assertEqual(narration, ["Read", "ing."])
        self.assertEqual(drafted, [(0, "c1", "read_file", ""), (0, "c1", "read_file", '{"path"'), (0, "c1", "read_file", ':"a"}')])
        self.assertEqual((answer.finish_reason, answer.message["tool_calls"][0]["function"]["arguments"]), ("tool_calls", '{"path":"a"}'))
        self.assertTrue(stream.closed)
        self.assertIs(responses.requests[0]["stream"], True)

    async def test_without_tools_text_is_the_reply(self):
        seen = []
        answer, _, _ = await self.run_stream([delta("Hel"), delta("lo"), finished(reply([text_item("Hello")]))], tools=False, on_text=seen.append)
        self.assertEqual((seen, answer.message["content"]), (["Hel", "lo"], "Hello"))

    async def test_an_incomplete_response_still_ends_the_stream_with_length(self):
        answer, _, _ = await self.run_stream([delta("x"), finished(reply([text_item("x")], "incomplete", "max_output_tokens"), "response.incomplete")], on_narration=lambda t: None)
        self.assertEqual(answer.finish_reason, "length")

    async def test_a_failed_or_error_event_is_a_classified_failure(self):
        failed = reply([], "failed")
        failed.error = NS(code="insufficient_quota", message="no quota")
        with self.assertRaises(ModelProviderError) as caught:
            await self.run_stream([finished(failed, "response.failed")], on_text=lambda t: None)
        self.assertEqual(caught.exception.code, "MODEL_QUOTA_EXCEEDED")
        with self.assertRaises(ModelProviderError) as caught:
            await self.run_stream([NS(type="error", code="invalid_prompt", message="bad")], on_text=lambda t: None)
        self.assertFalse(caught.exception.retriable)

    async def test_a_stream_that_ends_without_a_completed_event_is_requested_again_after_it_is_cleared(self):
        cleared = []
        stream_a, stream_b = Stream([delta("par")]), Stream([delta("full"), finished(reply([text_item("full")]))])
        responses = Responses([stream_a, stream_b])
        async def sleep(_): pass
        model = attached(luna("high", max_retries=1, sleep=sleep), responses=responses)
        answer = await model.complete(HISTORY, [], on_text=lambda t: None, on_retry=lambda: cleared.append(1))
        self.assertEqual((answer.message["content"], len(responses.requests), cleared), ("full", 2, [1]))

    async def test_a_broken_connection_midway_is_requested_again(self):
        responses = Responses([Stream([delta("a")], error=httpx.ReadError("reset")), Stream([finished(reply([text_item("ok")]))])])
        async def sleep(_): pass
        model = attached(luna("high", max_retries=1, sleep=sleep), responses=responses)
        answer = await model.complete(HISTORY, [], on_text=lambda t: None)
        self.assertEqual((answer.message["content"], len(responses.requests)), ("ok", 2))

    async def test_a_failing_progress_callback_never_fails_the_call(self):
        def boom(*_): raise RuntimeError("sink")
        answer, _, _ = await self.run_stream([delta("x"), added(0, "c", "read_file"), args(0, "{}"), finished(reply([call_item("c", "read_file", "{}")]))],
                                             on_narration=boom, on_tool=boom)
        self.assertEqual(answer.finish_reason, "tool_calls")


class PreferenceTests(TestCase):
    """The stored choice, against a fake session factory that records the statements."""

    def sessions(self, stored):
        outer = self
        class Db:
            def __enter__(self_inner): return self_inner
            def __exit__(self_inner, *a): return False
            def begin(self_inner): return self_inner
            def scalar(self_inner, _): return outer.stored
            def execute(self_inner, statement): outer.written = statement.compile().params
        self.stored, self.written = stored, None
        return lambda: Db()

    def test_nothing_stored_means_the_default_and_a_stored_choice_wins(self):
        from agentforge.repositories.model_preferences import effort_choices
        user = uuid4()
        self.assertEqual(effort_choices(self.sessions(None), user, "gpt-6-luna"),
                         {"model": "gpt-6-luna", "efforts": ["none", "low", "medium", "high", "xhigh", "max"], "effort": "high", "default": "high"})
        self.assertEqual(effort_choices(self.sessions("low"), user, "gpt-6-luna")["effort"], "low")
        self.assertEqual(effort_choices(self.sessions("max"), user, "gpt-4o"), {"model": "gpt-4o", "efforts": [], "effort": None, "default": None})

    def test_only_an_effort_the_model_takes_is_saved(self):
        from agentforge.repositories.model_preferences import UnsupportedEffort, set_effort
        sessions = self.sessions(None)
        set_effort(sessions, uuid4(), "gpt-6-luna", "xhigh")
        self.assertEqual(self.written["reasoning_effort"], "xhigh")
        self.written = None
        for model, effort in (("gpt-6-luna", "minimal"), ("gpt-4o", "high")):
            with self.assertRaises(UnsupportedEffort):
                set_effort(sessions, uuid4(), model, effort)
        self.assertIsNone(self.written)


class RunHandoffTests(TestCase):
    """A run starts with the model thinking as its owner chose."""

    def setUp(self):
        self.users = PreferenceTests()

    def test_the_owners_choice_is_applied_and_the_default_covers_an_unreadable_one(self):
        from agentforge.bootstrap import _thinking_as_chosen
        settings = NS(llm_model="gpt-6-luna")
        base = luna(None)
        self.assertEqual(_thinking_as_chosen(base, settings, self.users.sessions("low"), uuid4()).reasoning_effort, "low")
        self.assertEqual(_thinking_as_chosen(base, settings, self.users.sessions(None), uuid4()).reasoning_effort, "high")
        def broken():
            raise RuntimeError("database down")
        self.assertEqual(_thinking_as_chosen(base, settings, broken, uuid4()).reasoning_effort, "high")

    def test_a_model_without_the_choice_is_left_alone(self):
        from agentforge.bootstrap import _thinking_as_chosen
        plain = OpenAIAdapter(api_key="k", model_id="gpt-4o", rates=RATES, context_window_tokens=32_000, max_output_tokens=4096)
        self.assertIs(_thinking_as_chosen(plain, NS(llm_model="gpt-4o"), self.users.sessions("max"), uuid4()), plain)
        fixture = NS(model_id="fixture")
        self.assertIs(_thinking_as_chosen(fixture, NS(llm_model="gpt-6-luna"), self.users.sessions("max"), uuid4()), fixture)


class RouteTests(IsolatedAsyncioTestCase):
    """The endpoints, called directly with a stand-in request."""

    def request(self, factory, model="gpt-6-luna"):
        # `sessions(request)` builds a factory from the app's engine; here the stand-in is the factory itself.
        from unittest.mock import patch
        patcher = patch("agentforge.api.routes_model.sessions", lambda _request: factory)
        patcher.start()
        self.addCleanup(patcher.stop)
        return NS(app=NS(state=NS(settings=NS(llm_model=model))))

    async def test_a_database_that_is_behind_this_version_answers_503_not_500(self):
        from fastapi import HTTPException
        from sqlalchemy.exc import ProgrammingError
        from agentforge.api import routes_model
        def behind():
            raise ProgrammingError("select", {}, Exception("column users.reasoning_effort does not exist"))
        user = NS(id=uuid4())
        for call in (lambda: routes_model.read_effort(self.request(behind), user),
                     lambda: routes_model.write_effort(routes_model.ReasoningEffortBody(effort="low"), self.request(behind), user)):
            with self.assertRaises(HTTPException) as caught:
                await call()
            self.assertEqual((caught.exception.status_code, caught.exception.detail), (503, "reasoning_effort_unavailable"))

    async def test_an_effort_the_model_does_not_take_is_422(self):
        from fastapi import HTTPException
        from agentforge.api import routes_model
        with self.assertRaises(HTTPException) as caught:
            await routes_model.write_effort(routes_model.ReasoningEffortBody(effort="minimal"), self.request(PreferenceTests().sessions(None)), NS(id=uuid4()))
        self.assertEqual(caught.exception.status_code, 422)
