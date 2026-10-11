"""The token estimate that bounds requests: high enough to be safe, close enough to be usable."""
import json
from decimal import Decimal
from unittest import TestCase

from agentforge.llm.models import (ModelConfigurationError, ModelContextLimit, ModelProviderError, conservative_input_tokens,
                                   estimate_tokens)
from agentforge.subagents.budget import BudgetExceeded
from agentforge.subagents.scope import ScopeDenied
from agentforge.subagents.spawn_service import GENERIC_STOPPED_SUMMARY, STOPPED_SUMMARIES, _failure_text
from agentforge.tools.gateway import UnknownToolOutcome


class EstimateTests(TestCase):
    def test_ascii_text_is_charged_three_characters_per_token_rounding_up(self):
        self.assertEqual([estimate_tokens("a" * n) for n in (0, 1, 3, 4, 300)], [0, 1, 1, 2, 100])

    def test_non_ascii_characters_are_charged_two_tokens_each(self):
        self.assertEqual(estimate_tokens("界" * 10), 20)
        self.assertEqual(estimate_tokens("abc界"), 1 + 2)

    def test_the_estimate_stays_above_real_usage_measured_on_this_system(self):
        # Real runs measured about 3.5 bytes per token (a 13 KB conversation was 3.7K tokens).
        measured_bytes_per_token = 3.5
        for size in (4_000, 13_000, 120_000):
            self.assertGreaterEqual(estimate_tokens("x" * size), size / measured_bytes_per_token)

    def test_but_it_is_no_longer_one_token_per_byte(self):
        messages = [{"role": "user", "content": "x" * 30_000}]
        estimate = conservative_input_tokens(messages, [])
        self.assertLess(estimate, 13_000)  # it used to report 30,000+ here, and wrecked small windows
        self.assertGreater(estimate, 10_000)

    def test_overhead_grows_with_the_number_of_messages_and_tools_count(self):
        one = conservative_input_tokens([{"role": "user", "content": "hi"}], [])
        many = conservative_input_tokens([{"role": "user", "content": "hi"}] * 10, [])
        tools = conservative_input_tokens([{"role": "user", "content": "hi"}], [{"type": "function", "function": {"name": "t" * 900}}])
        self.assertGreater(many, one)
        self.assertGreater(tools, one)


class StoppedSummaryTests(TestCase):
    def test_every_stop_reason_of_our_own_has_a_plain_explanation_without_internal_codes(self):
        # Provider failures are not in this table: a person reads what the provider itself said.
        self.assertEqual(set(STOPPED_SUMMARIES), {"SCOPE_DENIED", "TOOL_TIMEOUT", "UNKNOWN_REMOTE_OUTCOME"})
        for code, text in STOPPED_SUMMARIES.items():
            self.assertTrue(text.endswith("."), code)
            self.assertNotIn(code, text)
            self.assertNotIn("Child invocation", text)
        self.assertTrue(GENERIC_STOPPED_SUMMARY.endswith("."))
        self.assertNotIn("Child invocation", GENERIC_STOPPED_SUMMARY)

    def test_provider_failures_show_the_providers_own_message_and_keep_their_code(self):
        quota = ModelProviderError("model quota exhausted", code="MODEL_QUOTA_EXCEEDED", detail="429: You exceeded your current quota.")
        self.assertEqual(_failure_text(quota), ("MODEL_QUOTA_EXCEEDED", "429: You exceeded your current quota."))
        bare = ModelProviderError("model provider unreachable")
        self.assertEqual(_failure_text(bare), ("MODEL_PROVIDER_ERROR", "model provider unreachable"))
        limit = ModelContextLimit("400: This model's maximum context length is 128000 tokens.")
        self.assertEqual(_failure_text(limit), ("MODEL_CONTEXT_LIMIT", "400: This model's maximum context length is 128000 tokens."))
        self.assertEqual(_failure_text(ModelConfigurationError("no key"))[0], "MODEL_PROVIDER_ERROR")

    def test_our_own_failures_get_a_plain_sentence_and_never_leak_the_exception_text(self):
        for exc, code in ((ScopeDenied("secret detail"), "SCOPE_DENIED"), (TimeoutError("secret detail"), "TOOL_TIMEOUT"),
                          (UnknownToolOutcome("secret detail"), "UNKNOWN_REMOTE_OUTCOME"), (BudgetExceeded("secret detail"), "BUDGET_EXCEEDED")):
            with self.subTest(code=code):
                got_code, text = _failure_text(exc)
                self.assertEqual(got_code, code)
                self.assertNotIn("secret", text)
                self.assertTrue(text.endswith("."))
        self.assertEqual(_failure_text(BudgetExceeded("x"))[1], GENERIC_STOPPED_SUMMARY)
