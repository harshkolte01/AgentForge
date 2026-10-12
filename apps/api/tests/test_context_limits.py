"""Provider capacity, price boundaries and loss-aware bounded history."""
import json
from decimal import Decimal
from types import SimpleNamespace as NS
from unittest import TestCase
from uuid import uuid4

from agentforge.llm.limits import LUNA_MAX_INPUT, LUNA_WINDOW
from agentforge.llm.models import ModelConfigurationError, ModelContextLimit, ModelRates
from agentforge.llm.openai_adapter import OpenAIAdapter
from agentforge.repositories.sessions import MAX_CONTEXT_BUDGET, context_budget_chars, digest_history
from agentforge.subagents.context import RESULT_PREFIX, compact_messages


SHORT = ModelRates(Decimal("0.10"), Decimal("0.50"))
LONG = ModelRates(Decimal("0.20"), Decimal("0.75"))


def luna(**overrides):
    options = dict(api_key="fixture", model_id="gpt-6-luna", rates=SHORT, long_context_rates=LONG,
                   context_window_tokens=LUNA_WINDOW, max_output_tokens=8192, reasoning_effort="none")
    return OpenAIAdapter(**{**options, **overrides})


class CapacityTests(TestCase):
    def test_million_token_luna_preserves_the_provider_input_limit(self):
        model = luna()
        self.assertEqual(model.context_window_tokens, 1_050_000)
        self.assertEqual(model.max_input_tokens, LUNA_MAX_INPUT)
        self.assertLess(model.max_input_tokens + model.max_output_tokens, model.context_window_tokens)
        self.assertEqual(luna(context_window_tokens=32_000).max_input_tokens, 32_000 - 8192)

    def test_missing_long_prices_or_invalid_limits_fail_closed(self):
        for overrides in ({"long_context_rates": None}, {"context_window_tokens": 1_100_000},
                          {"reasoning_effort": "minimal"}, {"long_context_rates": ModelRates(Decimal("0.01"), Decimal("0.10"))}):
            with self.subTest(overrides=overrides), self.assertRaises(ModelConfigurationError):
                luna(**overrides)
        # Luna takes every effort it lists (the ones above none go through the Responses endpoint).
        for effort in ("none", "low", "medium", "high", "xhigh", "max"):
            self.assertEqual(luna(reasoning_effort=effort).reasoning_effort, effort)
        # Existing capped installations do not need new price configuration.
        self.assertEqual(luna(context_window_tokens=262_144, long_context_rates=None).context_window_tokens, 262_144)

    def test_long_context_pricing_applies_to_the_whole_request_strictly_above_the_boundary(self):
        model = luna()
        self.assertIs(model._rates_for(272_000), SHORT)
        self.assertIs(model._rates_for(272_001), LONG)
        self.assertEqual(model._rates_for(272_001).cost(272_001, 1000), Decimal("0.0551502"))

    def test_dense_input_cost_is_reserved_above_a_character_estimate(self):
        model = luna()
        text = "!@#$%^&*()[]{}" * 30_000
        bound = model.maximum_request_cost([{"role": "user", "content": text}], [])
        self.assertGreaterEqual(bound, LONG.cost(len(text.encode()), model.max_output_tokens))

    def test_the_provider_input_limit_is_enforced_even_when_the_total_window_has_room(self):
        with self.assertRaises(ModelContextLimit):
            luna().maximum_request_cost([{"role": "user", "content": "界" * (LUNA_MAX_INPUT // 2)}], [])


class HistoryTests(TestCase):
    def digest(self, texts, budget=MAX_CONTEXT_BUDGET):
        rows = [NS(id=uuid4(), sequence=i + 1, role="user" if i % 2 else "assistant", content=text) for i, text in enumerate(texts)]
        return digest_history(NS(scalars=lambda _query: iter(reversed(rows))), NS(id=uuid4()), budget)

    def test_configured_window_scales_history_without_unbounded_replay(self):
        settings = NS(llm_model="gpt-6-luna", llm_context_window_tokens=LUNA_WINDOW, llm_max_output_tokens=8192)
        self.assertEqual(context_budget_chars(settings), MAX_CONTEXT_BUDGET)
        settings.llm_context_window_tokens = 32_000
        self.assertLess(context_budget_chars(settings), MAX_CONTEXT_BUDGET)
        self.assertGreaterEqual(context_budget_chars(settings), 2000)

    def test_long_recent_messages_survive_in_full_and_older_decisions_still_have_space(self):
        digest = self.digest([f"request {i}: " + "x" * 7800 for i in range(20)])
        recent = digest["recent_messages"]
        self.assertGreaterEqual(len(recent), 4)
        self.assertTrue(all(not item["truncated"] for item in recent))
        self.assertTrue(digest["summary"]["excerpts"])
        self.assertEqual(recent[-1]["sequence"], 20)
        self.assertLessEqual(len(json.dumps(digest, ensure_ascii=False, separators=(",", ":"))), MAX_CONTEXT_BUDGET)

    def test_clipping_discloses_loss_and_retains_final_constraints(self):
        digest = self.digest(["Start with the original task. " + "x" * 20_000 + " Keep the final constraint."], budget=7500)
        item = digest["recent_messages"][0]
        self.assertTrue(item["truncated"])
        self.assertTrue(item["text"].startswith("Start with the original task."))
        self.assertTrue(item["text"].endswith("Keep the final constraint."))

    def test_serialized_bound_handles_escaped_and_multilingual_text(self):
        for text in ('"\\\n\t' * 8000, "界🙂" * 8000):
            with self.subTest(text=text[:4]):
                digest = self.digest([text] * 40, budget=2000)
                self.assertLessEqual(len(json.dumps(digest, ensure_ascii=False, separators=(",", ":")).encode("utf-8")), 2000)
                self.assertTrue(digest["recent_messages"][-1]["truncated"])

    def test_unclipped_whitespace_is_preserved(self):
        item = self.digest(["  exact text\n"])['recent_messages'][0]
        self.assertEqual(item["text"], "  exact text\n")
        self.assertFalse(item["truncated"])


class CompactionTests(TestCase):
    def test_old_read_keeps_identity_and_cursor_without_changing_the_original_or_unread_results(self):
        def call(ident, name):
            return {"role": "assistant", "tool_calls": [{"id": ident, "function": {"name": name}}]}
        def result(ident):
            return {"role": "tool", "tool_call_id": ident, "content": RESULT_PREFIX + json.dumps({
                "tool": "read_repo_file", "status": "ok", "evidence_ids": ["e1"],
                "data": {"path": "src/app.py", "next_line": 201, "content": "x" * 10_000}})}
        messages = [call("goal", "read_task_input"), result("goal"), call("old", "read_repo_file"), result("old"),
                    call("new", "read_repo_file"), result("new")]
        snapshot = json.dumps(messages)
        compacted = compact_messages(messages, 1, keep_recent=0)
        self.assertIs(compacted[1], messages[1])
        self.assertIs(compacted[-1], messages[-1])
        stub = json.loads(compacted[3]["content"][len(RESULT_PREFIX):])
        self.assertEqual(stub["data"], {"path": "src/app.py", "next_line": 201})
        self.assertEqual(stub["evidence_ids"], ["e1"])
        self.assertTrue(stub["compacted"])
        self.assertEqual(json.dumps(messages), snapshot)
