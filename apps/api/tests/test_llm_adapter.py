"""Model adapter: request shape, retries that cost nothing, error codes that carry the provider's own message, streaming."""
from decimal import Decimal
from types import SimpleNamespace as NS
from unittest import IsolatedAsyncioTestCase, TestCase

import httpx
import openai

from agentforge.llm import models
from agentforge.llm.models import ModelContextLimit, ModelProviderError, ModelRates, provider_detail, redact
from agentforge.llm.openai_adapter import BASE_RETRY_WAIT_SEC, MAX_RETRY_WAIT_SEC, OpenAIAdapter

REQUEST = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
TOOL = {"type": "function", "function": {"name": "read_repo_file", "parameters": {"type": "object"}}}
MESSAGES = [{"role": "user", "content": "hi"}]
PROVIDER_TEXT = "You exceeded your current quota, please check your plan and billing details."


def status_error(cls, status, body=None, headers=None, text=PROVIDER_TEXT):
    return cls(text, response=httpx.Response(status, request=REQUEST, headers=headers or {}), body=body)


def frame_error(error, text="The server had an error while processing your request."):
    """What `openai._streaming` raises for an error frame inside a stream: a bare APIError built from the frame's `error`."""
    return openai.APIError(text, REQUEST, body=error)


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

    async def test_without_tools_the_reply_is_plain_text_not_a_json_object(self):
        model, completions, _ = adapter([completion()])
        await model.complete(MESSAGES, [])
        self.assertNotIn("response_format", completions.requests[0])
        self.assertNotIn("tools", completions.requests[0])

    async def test_a_forced_tool_choice_and_a_disabled_parallel_flag_are_passed_through(self):
        model, completions, _ = adapter([completion()], parallel_tool_calls=False)
        forced = {"type": "function", "function": {"name": "read_repo_file"}}
        await model.complete(MESSAGES, [TOOL], tool_choice=forced)
        request = completions.requests[0]
        self.assertEqual((request["tool_choice"], request["parallel_tool_calls"]), (forced, False))

    async def test_only_documented_message_fields_are_replayed(self):
        model, _, _ = adapter([completion()])
        response = await model.complete(MESSAGES, [TOOL])
        self.assertEqual(response.message, {"role": "assistant", "content": "ok"})
        self.assertEqual(response.usage.cost_usd, Decimal("0.00002"))

    def test_the_context_budget_is_half_of_the_usable_window_in_estimated_tokens(self):
        model, _, _ = adapter([])
        self.assertEqual(model.max_input_tokens, 100_000 - 4096)
        self.assertEqual(model.context_budget_tokens, (100_000 - 4096) // 2)


class RetryTests(IsolatedAsyncioTestCase):
    async def test_a_rate_limit_is_retried_with_backoff_then_succeeds(self):
        model, completions, sleeps = adapter([status_error(openai.RateLimitError, 429), completion()])
        response = await model.complete(MESSAGES, [TOOL])
        self.assertEqual(response.message["content"], "ok")
        self.assertEqual(len(completions.requests), 2)
        self.assertEqual(len(sleeps), 1)
        self.assertLessEqual(sleeps[0], MAX_RETRY_WAIT_SEC)

    async def test_without_retry_after_the_backoff_grows_with_equal_jitter(self):
        model, _, sleeps = adapter([status_error(openai.RateLimitError, 429)] * 3 + [completion()], max_retries=3)
        await model.complete(MESSAGES, [TOOL])
        for attempt, waited in enumerate(sleeps):
            base = BASE_RETRY_WAIT_SEC * 2 ** attempt
            self.assertTrue(base / 2 <= waited <= base, (attempt, waited))

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

    async def test_retries_stop_at_the_limit_with_a_stable_code_and_the_providers_own_message(self):
        model, completions, _ = adapter([status_error(openai.RateLimitError, 429)] * 3, max_retries=2)
        with self.assertRaises(ModelProviderError) as caught:
            await model.complete(MESSAGES, [TOOL])
        self.assertEqual(len(completions.requests), 3)
        self.assertEqual((caught.exception.code, caught.exception.retriable), ("RATE_LIMIT", True))
        self.assertEqual(str(caught.exception), "model rate limited")  # the stable text stays free of provider content
        self.assertEqual(caught.exception.detail, f"429: {PROVIDER_TEXT}")
        self.assertEqual(caught.exception.shown, f"429: {PROVIDER_TEXT}")

    async def test_retries_can_be_switched_off(self):
        model, completions, _ = adapter([status_error(openai.RateLimitError, 429)], max_retries=0)
        with self.assertRaises(ModelProviderError):
            await model.complete(MESSAGES, [TOOL])
        self.assertEqual(len(completions.requests), 1)

    async def test_a_timeout_is_retried_since_failing_the_run_costs_more_than_one_more_request(self):
        model, completions, _ = adapter([openai.APITimeoutError(request=REQUEST), completion()])
        await model.complete(MESSAGES, [TOOL])
        self.assertEqual(len(completions.requests), 2)

    async def test_permanent_failures_are_not_retried(self):
        cases = [
            (status_error(openai.AuthenticationError, 401), "AUTH_ERROR"),
            (status_error(openai.RateLimitError, 429, body={"code": "insufficient_quota"}), "MODEL_QUOTA_EXCEEDED"),
            (RuntimeError("anything else"), "MODEL_PROVIDER_ERROR"),
        ]
        for failure, code in cases:
            model, completions, _ = adapter([failure, completion()])
            with self.subTest(code=code), self.assertRaises(ModelProviderError) as caught:
                await model.complete(MESSAGES, [TOOL])
            self.assertEqual((caught.exception.code, len(completions.requests)), (code, 1))

    async def test_a_context_overflow_becomes_the_context_limit_error_with_the_providers_words(self):
        overflow = "This model's maximum context length is 128000 tokens. However, your messages resulted in 131000 tokens."
        model, _, _ = adapter([status_error(openai.BadRequestError, 400, body={"code": "context_length_exceeded"}, text=overflow)])
        with self.assertRaises(ModelContextLimit) as caught:
            await model.complete(MESSAGES, [TOOL])
        self.assertEqual(str(caught.exception), f"400: {overflow}")

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
    async def test_text_beside_tool_calls_streams_as_narration_and_tool_arguments_never_stream(self):
        stream = FakeStream([
            chunk("Let me look. "),
            chunk(calls=fragment(0, "c1", "read_repo_file", '{"path":')),
            chunk(calls=fragment(0, None, None, '"a"}')),
            chunk(finish="tool_calls"),
        ])
        model, _, _ = adapter([stream])
        reply, narration = [], []
        response = await model.complete(MESSAGES, [TOOL], on_text=reply.append, on_narration=narration.append)
        self.assertEqual((reply, narration), ([], ["Let me look. "]))  # tools were offered: content is a note, not the answer
        self.assertEqual(response.message["tool_calls"][0]["function"]["arguments"], '{"path":"a"}')
        self.assertEqual(response.message["content"], "Let me look. ")

    async def test_without_tools_the_text_streams_as_the_reply(self):
        stream = FakeStream([chunk("Hel"), chunk("lo"), chunk(finish="stop")])
        model, _, _ = adapter([stream])
        reply, narration = [], []
        response = await model.complete(MESSAGES, [], on_text=reply.append, on_narration=narration.append)
        self.assertEqual((reply, narration), (["Hel", "lo"], []))
        self.assertEqual(response.message["content"], "Hello")

    async def test_a_rate_limit_before_any_output_is_retried(self):
        good = FakeStream([chunk('{"a":1}'), chunk(finish="stop")])
        model, completions, _ = adapter([status_error(openai.RateLimitError, 429), good])
        retried = []
        response = await model.complete(MESSAGES, [], on_text=lambda _: None, on_retry=lambda: retried.append(True))
        self.assertEqual(response.message["content"], '{"a":1}')
        self.assertEqual(len(completions.requests), 2)
        self.assertEqual(retried, [])  # nothing had been shown, so there is nothing to take back

    async def test_a_stream_that_breaks_midway_is_requested_again_after_the_caller_clears_it(self):
        broken = FakeStream([chunk("Hel"), chunk("lo")], fail_at=1, error=openai.APIConnectionError(request=REQUEST))
        model, completions, _ = adapter([broken, FakeStream([chunk("Hello"), chunk(finish="stop")])])
        shown, retried = [], []
        response = await model.complete(MESSAGES, [], on_text=shown.append, on_retry=lambda: retried.append(len(shown)))
        self.assertEqual(response.message["content"], "Hello")
        self.assertEqual(len(completions.requests), 2)
        self.assertEqual(retried, [1])  # told after the first attempt had shown "Hel"
        self.assertTrue(broken.closed)

    async def test_a_transient_error_frame_midway_is_requested_again_after_the_caller_clears_it(self):
        broken = FakeStream([chunk("Hel"), chunk("lo")], fail_at=1, error=frame_error({"type": "server_error", "code": None}))
        model, completions, sleeps = adapter([broken, FakeStream([chunk("Hello"), chunk(finish="stop")])])
        retried = []
        response = await model.complete(MESSAGES, [], on_text=lambda _: None, on_retry=lambda: retried.append(True))
        self.assertEqual((response.message["content"], len(completions.requests), retried), ("Hello", 2, [True]))
        self.assertEqual(len(sleeps), 1)

    async def test_a_stream_that_ends_without_a_finish_reason_is_requested_again(self):
        cut = FakeStream([chunk("Hel"), chunk("lo")])  # closed cleanly, but the reply never finished
        model, completions, _ = adapter([cut, FakeStream([chunk("Hello"), chunk(finish="stop")])])
        retried = []
        response = await model.complete(MESSAGES, [], on_text=lambda _: None, on_retry=lambda: retried.append(True))
        self.assertEqual((response.message["content"], len(completions.requests), retried), ("Hello", 2, [True]))

    async def test_a_stream_that_keeps_ending_early_fails_as_a_retriable_provider_error(self):
        model, completions, _ = adapter([FakeStream([chunk("x")]) for _ in range(3)], max_retries=2)
        with self.assertRaises(ModelProviderError) as caught:
            await model.complete(MESSAGES, [], on_text=lambda _: None)
        self.assertEqual((caught.exception.code, caught.exception.retriable, len(completions.requests)), ("MODEL_PROVIDER_ERROR", True, 3))

    async def test_a_stream_with_no_output_and_no_finish_reason_is_still_not_retried(self):
        model, completions, _ = adapter([FakeStream([]), FakeStream([chunk(finish="stop")])])
        with self.assertRaises(ModelProviderError):
            await model.complete(MESSAGES, [], on_text=lambda _: None)
        self.assertEqual(len(completions.requests), 1)

    async def test_a_permanent_failure_midway_is_not_repeated(self):
        broken = FakeStream([chunk("{"), chunk("}")], fail_at=1, error=status_error(openai.AuthenticationError, 401))
        model, completions, _ = adapter([broken, FakeStream([chunk(finish="stop")])])
        with self.assertRaises(ModelProviderError) as caught:
            await model.complete(MESSAGES, [], on_text=lambda _: None)
        self.assertEqual((caught.exception.code, len(completions.requests)), ("AUTH_ERROR", 1))


class ClassifyDetailTests(TestCase):
    """What a person reads when the provider refuses: the provider's own words, with credentials removed."""

    def classify(self, failure, **kwargs):
        return OpenAIAdapter._classify(failure, **kwargs)

    def test_every_provider_failure_carries_the_providers_message_as_detail(self):
        cases = [
            (status_error(openai.RateLimitError, 429, body={"code": "insufficient_quota"}), "MODEL_QUOTA_EXCEEDED"),
            (status_error(openai.RateLimitError, 429), "RATE_LIMIT"),
            (status_error(openai.AuthenticationError, 401, text="Incorrect API key provided."), "AUTH_ERROR"),
            (status_error(openai.PermissionDeniedError, 403, text="Country not supported."), "AUTH_ERROR"),
            (status_error(openai.InternalServerError, 500, text="The server had an error."), "MODEL_PROVIDER_ERROR"),
            (status_error(openai.NotFoundError, 404, text="The model `gpt-9` does not exist."), "MODEL_PROVIDER_ERROR"),
        ]
        for failure, code in cases:
            with self.subTest(code=code, status=failure.status_code):
                error = self.classify(failure)
                self.assertIsInstance(error, ModelProviderError)
                self.assertEqual(error.code, code)
                self.assertEqual(error.detail, f"{failure.status_code}: {failure.message}")
                self.assertEqual(error.shown, error.detail)

    def test_a_failure_without_a_status_still_shows_what_it_said(self):
        error = self.classify(RuntimeError("connection reset by peer"))
        self.assertEqual((error.code, error.detail), ("MODEL_PROVIDER_ERROR", "connection reset by peer"))
        timeout = self.classify(openai.APITimeoutError(request=REQUEST))
        self.assertEqual(timeout.code, "MODEL_PROVIDER_ERROR")
        self.assertTrue(timeout.shown)

    def test_an_echoed_credential_is_removed_from_the_detail(self):
        error = self.classify(status_error(openai.AuthenticationError, 401, text="Incorrect API key provided: sk-proj-abcdefgh12345678."))
        self.assertNotIn("sk-proj", error.detail)
        self.assertIn("Incorrect API key provided", error.detail)

    def test_transient_failures_are_retriable_and_permanent_ones_are_not(self):
        for transient in (status_error(openai.RateLimitError, 429), status_error(openai.InternalServerError, 500),
                          openai.APITimeoutError(request=REQUEST), openai.APIConnectionError(request=REQUEST)):
            self.assertTrue(self.classify(transient).retriable, transient)
        for permanent in (status_error(openai.AuthenticationError, 401),
                          status_error(openai.RateLimitError, 429, body={"code": "insufficient_quota"}), RuntimeError("x")):
            self.assertFalse(self.classify(permanent).retriable, permanent)

    def test_an_error_frame_sent_mid_stream_is_judged_by_its_code_and_type(self):
        cases = [
            ({"type": "server_error", "code": None}, "MODEL_PROVIDER_ERROR", True),
            ({"type": "server_error", "code": "overloaded"}, "MODEL_PROVIDER_ERROR", True),
            ({"type": "service_unavailable"}, "MODEL_PROVIDER_ERROR", True),
            ({"message": "An error occurred"}, "MODEL_PROVIDER_ERROR", True),  # no kind at all: assumed transient
            ("not a mapping", "MODEL_PROVIDER_ERROR", True),
            ({"type": "tokens", "code": "rate_limit_exceeded"}, "RATE_LIMIT", True),
            ({"type": "insufficient_quota", "code": "insufficient_quota"}, "MODEL_QUOTA_EXCEEDED", False),
            ({"type": "invalid_request_error", "code": "invalid_value"}, "MODEL_PROVIDER_ERROR", False),
        ]
        for body, code, retriable in cases:
            with self.subTest(body=body):
                error = self.classify(frame_error(body))
                self.assertEqual((error.code, error.retriable), (code, retriable))
                self.assertEqual(error.detail, "The server had an error while processing your request.")

    def test_our_own_errors_pass_through_unchanged(self):
        own = ModelProviderError("model returned no completion", detail="empty")
        self.assertIs(self.classify(own), own)
        limit = ModelContextLimit("too long")
        self.assertIs(self.classify(limit), limit)

    def test_shown_falls_back_to_the_message_without_detail(self):
        self.assertEqual(ModelProviderError("model provider request failed").shown, "model provider request failed")
        self.assertEqual(ModelProviderError("stable", detail="429: from the provider").shown, "429: from the provider")


class ProviderDetailTests(TestCase):
    def test_status_and_message_are_joined_and_whitespace_is_collapsed(self):
        failure = status_error(openai.RateLimitError, 429, text="Too   many\n requests.\tSlow down.")
        self.assertEqual(provider_detail(failure), "429: Too many requests. Slow down.")

    def test_a_plain_exception_uses_its_string_and_an_empty_one_gives_nothing(self):
        self.assertEqual(provider_detail(ValueError("bad value")), "bad value")
        self.assertEqual(provider_detail(RuntimeError("")), "")

    def test_the_detail_is_bounded(self):
        self.assertEqual(len(provider_detail(RuntimeError("x" * 5000))), models.DETAIL_CHARS)

    def test_redact_removes_credentials_and_keeps_everything_else(self):
        for secret in ("sk-abcdefgh12345678", "sk-proj_abc-12345678", "Bearer abcdefgh.12345-xyz", "api_key=abcdefghijklmnop",
                       'api-key: "abcdefghijklmnop"', "https://user:hunter2@example.com/path"):
            with self.subTest(secret=secret):
                cleaned = redact(f"failed with {secret} while calling")
                self.assertIn("[redacted]", cleaned)
                self.assertNotIn("hunter2", cleaned)
                self.assertNotIn("abcdefgh", cleaned)
                self.assertTrue(cleaned.startswith("failed with") or "failed with" in cleaned)
        plain = "The model `gpt-9` does not exist or you do not have access to it (requested 4096 tokens)."
        self.assertEqual(redact(plain), plain)
