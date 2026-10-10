"""The token estimate that bounds requests: high enough to be safe, close enough to be usable."""
import json
from decimal import Decimal
from unittest import TestCase

from agentforge.llm.models import conservative_input_tokens, estimate_tokens
from agentforge.subagents.spawn_service import GENERIC_STOPPED_SUMMARY, STOPPED_SUMMARIES


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
    def test_every_stop_reason_a_person_can_see_has_a_plain_explanation_without_internal_codes(self):
        for code in ("MODEL_CONTEXT_LIMIT", "RATE_LIMIT", "MODEL_PROVIDER_ERROR", "AUTH_ERROR", "BUDGET_EXCEEDED",
                     "TOOL_TIMEOUT", "SCOPE_DENIED", "UNKNOWN_REMOTE_OUTCOME"):
            text = STOPPED_SUMMARIES[code]
            self.assertTrue(text.endswith("."), code)
            self.assertNotIn(code, text)
            self.assertNotIn("Child invocation", text)
        self.assertNotIn("Child invocation", GENERIC_STOPPED_SUMMARY)
