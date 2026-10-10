"""Model adapter: request shape, retries that cost nothing, safe error codes and streamed results."""
from decimal import Decimal
from types import SimpleNamespace as NS
from unittest import IsolatedAsyncioTestCase, TestCase

import httpx
import openai

from agentforge.llm.models import SUBMIT_RESULT, ModelContextLimit, ModelProviderError, ModelRates
from agentforge.llm.openai_adapter import MAX_RETRY_WAIT_SEC, OpenAIAdapter

REQUEST = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
TOOL = {"type": "function", "function": {"name": "read_repo_file", "parameters": {"type": "object"}}}
MESSAGES = [{"role": "user", "content": "hi"}]


def status_error(cls, status, body=None, headers=None):
    return cls("provider text that must never surface", response=httpx.Response(status, request=REQUEST, headers=headers or {}), body=body)


def completion(message=None, finish="stop"):
    message = message or NS(model_dump=lambda exclude_none=True: {"role": "assistant", "content": "ok", "extra": 1})
    return NS(choices=[NS(message=message, finish_reason=finish)], usage=NS(prompt_tokens=10, completion_tokens=5), model="reported")


class FakeCompletions:
    def __init__(self, outcomes):
        self.outcomes, self.requests = list(outcomes), []

    async def create(self, **kwargs):
        self.requests.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def adapter(outcomes, **options):
    completions = FakeCompletions(outcomes)
    sleeps = []
    async def sleep(seconds):
        sleeps.append(seconds)
    instance = OpenAIAdapter(api_key="k", model_id="gpt-test", rates=ModelRates(Decimal(1), Decimal(2)),
                             context_window_tokens=100_000, max_output_tokens=4096, sleep=sleep, **options)
    instance._client = NS(chat=NS(completions=completions))
    return instance, completions, sleeps


class RequestShapeTests(IsolatedAsyncioTestCase):
    async def test_with_tools_json_mode_is_off_and_the_parallel_flag_is_sent(self):
        model, completions, _ = adapter([completion()])
        await model.complete(MESSAGES, [TOOL])
        request = completions.requests[0]
        self.assertNotIn("response_format", request)
        self.assertEqual((request["tool_choice"], request["parallel_tool_calls"]), ("auto", True))
        self.assertIs(request["store"], False)

    async def test_without_tools_the_reply_is_requested_as_a_json_object(self):
        model, completions, _ = adapter([completion()])
        await model.complete(MESSAGES, [])
        self.assertEqual(completions.requests[0]["response_format"], {"type": "json_object"})
        self.assertNotIn("tools", completions.requests[0])

    async def test_a_forced_tool_choice_and_a_disabled_parallel_flag_are_passed_through(self):
        model, completions, _ = adapter([completion()], parallel_tool_calls=False)
        forced = {"type": "function", "function": {"name": SUBMIT_RESULT}}
        await model.complete(MESSAGES, [TOOL], tool_choice=forced)
        request = completions.requests[0]
        self.assertEqual((request["tool_choice"], request["parallel_tool_calls"]), (forced, False))

    async def test_only_documented_message_fields_are_replayed(self):
        model, _, _ = adapter([completion()])
        response = await model.complete(MESSAGES, [TOOL])
        self.assertEqual(response.message, {"role": "assistant", "content": "ok"})
        self.assertEqual(response.usage.cost_usd, Decimal("0.00002"))

    def test_the_context_budget_is_half_of_the_usable_window(self):
        model, _, _ = adapter([])
        self.assertEqual(model.context_budget_bytes, (100_000 - 4096) // 2)


class RetryTests(IsolatedAsyncioTestCase):
    async def test_a_rate_limit_is_retried_with_backoff_then_succeeds(self):
        model, completions, sleeps = adapter([status_error(openai.RateLimitError, 429), completion()])
        response = await model.complete(MESSAGES, [TOOL])
        self.assertEqual(response.message["content"], "ok")
        self.assertEqual(len(completions.requests), 2)
        self.assertEqual(len(sleeps), 1)
        self.assertLessEqual(sleeps[0], MAX_RETRY_WAIT_SEC)

    async def test_retry_after_is_honoured_but_capped(self):
        model, _, sleeps = adapter([status_error(openai.RateLimitError, 429, headers={"retry-after": "3"}),
                                    status_error(openai.RateLimitError, 429, headers={"retry-after": "600"}), completion()])
        await model.complete(MESSAGES, [TOOL])
        self.assertEqual(sleeps, [3.0, MAX_RETRY_WAIT_SEC])

    async def test_server_errors_and_refused_connections_are_retried(self):
        model, completions, _ = adapter([status_error(openai.InternalServerError, 503),
                                         openai.APIConnectionError(request=REQUEST), completion()])
        await model.complete(MESSAGES, [TOOL])
        self.assertEqual(len(completions.requests), 3)

    async def test_retries_stop_at_the_limit_with_a_stable_code_and_no_provider_text(self):
        model, completions, _ = adapter([status_error(openai.RateLimitError, 429)] * 3, max_retries=2)
        with self.assertRaises(ModelProviderError) as caught:
            await model.complete(MESSAGES, [TOOL])
        self.assertEqual(len(completions.requests), 3)
        self.assertEqual((caught.exception.code, caught.exception.retriable), ("RATE_LIMIT", True))
        self.assertNotIn("provider text", str(caught.exception))

    async def test_retries_can_be_switched_off(self):
        model, completions, _ = adapter([status_error(openai.RateLimitError, 429)], max_retries=0)
        with self.assertRaises(ModelProviderError):
            await model.complete(MESSAGES, [TOOL])
        self.assertEqual(len(completions.requests), 1)

    async def test_failures_that_may_have_been_billed_or_are_permanent_are_not_retried(self):
        cases = [
            (openai.APITimeoutError(request=REQUEST), "MODEL_PROVIDER_ERROR"),
            (status_error(openai.AuthenticationError, 401), "AUTH_ERROR"),
            (status_error(openai.RateLimitError, 429, body={"code": "insufficient_quota"}), "MODEL_QUOTA_EXCEEDED"),
            (RuntimeError("anything else"), "MODEL_PROVIDER_ERROR"),
        ]
        for failure, code in cases:
            model, completions, _ = adapter([failure, completion()])
            with self.subTest(code=code), self.assertRaises(ModelProviderError) as caught:
                await model.complete(MESSAGES, [TOOL])
            self.assertEqual((caught.exception.code, len(completions.requests)), (code, 1))

    async def test_a_context_overflow_becomes_the_context_limit_error(self):
        model, _, _ = adapter([status_error(openai.BadRequestError, 400, body={"code": "context_length_exceeded"})])
        with self.assertRaises(ModelContextLimit):
            await model.complete(MESSAGES, [TOOL])

    async def test_an_empty_completion_is_a_provider_error(self):
        model, _, _ = adapter([NS(choices=[], usage=None, model="m")])
        with self.assertRaises(ModelProviderError):
            await model.complete(MESSAGES, [TOOL])

    def test_the_retry_count_is_bounded(self):
        with self.assertRaises(Exception):
            adapter([], max_retries=9)


class FakeStream:
    def __init__(self, chunks, fail_at=None, error=None):
        self.chunks, self.fail_at, self.error, self.closed = chunks, fail_at, error, False

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for index, item in enumerate(self.chunks):
            if self.fail_at == index:
                raise self.error
            yield item

    async def close(self):
        self.closed = True


def chunk(content=None, *, calls=None, finish=None, usage=None):
    delta = NS(content=content, tool_calls=calls)
    return NS(choices=[NS(delta=delta, finish_reason=finish)], usage=usage, model="reported")


def fragment(index, ident=None, name=None, arguments=None):
    return [NS(index=index, id=ident, function=NS(name=name, arguments=arguments))]


class StreamingTests(IsolatedAsyncioTestCase):
    async def test_submit_result_arguments_stream_as_the_reply_but_narration_does_not(self):
        stream = FakeStream([
            chunk("Let me think. "),
            chunk(calls=fragment(0, "s1", SUBMIT_RESULT, '{"summary":"He')),
            chunk(calls=fragment(0, None, None, 'llo"}')),
            chunk(finish="tool_calls"),
        ])
        model, _, _ = adapter([stream])
        shown = []
        response = await model.complete(MESSAGES, [TOOL], on_text=shown.append)
        self.assertEqual(shown, ['{"summary":"He', 'llo"}'])  # tools were offered: content is narration
        self.assertEqual(response.message["tool_calls"][0]["function"]["arguments"], '{"summary":"Hello"}')
        self.assertEqual(response.message["content"], "Let me think. ")

    async def test_other_tool_arguments_are_never_shown_as_the_reply(self):
        stream = FakeStream([chunk(calls=fragment(0, "c1", "read_repo_file", '{"path":"a"}')), chunk(finish="tool_calls")])
        model, _, _ = adapter([stream])
        shown = []
        await model.complete(MESSAGES, [TOOL], on_text=shown.append)
        self.assertEqual(shown, [])

    async def test_a_rate_limit_before_any_output_is_retried_but_a_midstream_failure_is_not(self):
        early, later = status_error(openai.RateLimitError, 429), status_error(openai.RateLimitError, 429)
        good = FakeStream([chunk('{"a":1}'), chunk(finish="stop")])
        model, completions, _ = adapter([early, good])
        response = await model.complete(MESSAGES, [], on_text=lambda _: None)
        self.assertEqual(response.message["content"], '{"a":1}')
        self.assertEqual(len(completions.requests), 2)
        broken = FakeStream([chunk("{"), chunk("}")], fail_at=1, error=later)
        model, completions, _ = adapter([broken, FakeStream([chunk(finish="stop")])])
        with self.assertRaises(ModelProviderError) as caught:
            await model.complete(MESSAGES, [], on_text=lambda _: None)
        self.assertEqual(len(completions.requests), 1)  # output had begun, so it is not repeated
        self.assertFalse(caught.exception.retriable)
        self.assertTrue(broken.closed)
