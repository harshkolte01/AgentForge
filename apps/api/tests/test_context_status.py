"""Context status: what a conversation holds of the model's window, from estimates and measured usage."""
from types import SimpleNamespace as NS
from unittest import TestCase
from unittest.mock import patch
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentforge.api.routes_context import router
from agentforge.auth.session import current_user
from agentforge.repositories.sessions import CONTEXT_BUDGET, bounded_context, digest_history
from agentforge.repositories.tasks import TaskNotFound
from agentforge.subagents.context_status import (
    EMPTY_USAGE, HIGH_PERCENT, NOTICE_PERCENT, build_status, entry_overhead_tokens, estimate_tokens, level_for,
)

WINDOW = 100_000


def status(usage=None, window=WINDOW, **kwargs):
    return build_status(model="m", provider="openai", window=window, max_output=4096,
                        usage=usage or EMPTY_USAGE, history_budget_chars=CONTEXT_BUDGET, **kwargs)


def usage(**overrides):
    return {"history_chars": 4000, "messages_total": 6, "messages_in_full": 6, "messages_condensed": 0, "last_run": None, **overrides}


class EstimateTests(TestCase):
    def test_tokens_are_estimated_at_four_characters_each_rounding_up(self):
        self.assertEqual([estimate_tokens(n) for n in (0, 1, 4, 5, 400)], [0, 1, 1, 2, 100])
        self.assertEqual(estimate_tokens(-5), 0)

    def test_every_turn_starts_with_a_real_but_modest_overhead(self):
        overhead = entry_overhead_tokens()
        self.assertGreater(overhead, 1000)  # platform rules, role prompt and tool schemas are not free
        self.assertLess(overhead, 14000)  # twenty tool schemas plus the prompts, estimated high on purpose; well inside a 32K window

    def test_levels_switch_at_the_documented_thresholds(self):
        self.assertEqual([level_for(p) for p in (0, NOTICE_PERCENT - 0.1, NOTICE_PERCENT, HIGH_PERCENT - 0.1, HIGH_PERCENT, 100)],
                         ["ok", "ok", "notice", "notice", "high", "high"])


class BuildStatusTests(TestCase):
    def test_a_new_conversation_shows_only_the_starting_overhead(self):
        result = status()
        self.assertEqual((result.history_tokens, result.messages_total, result.last_run), (0, 0, None))
        self.assertEqual((result.used_tokens, result.used_basis), (result.baseline_tokens, "next_turn"))
        self.assertEqual(result.level, "ok")
        self.assertEqual(result.history_budget_tokens, CONTEXT_BUDGET // 3)

    def test_history_adds_to_the_starting_context(self):
        result = status(usage(history_chars=4000))
        self.assertEqual(result.history_tokens, 1000)
        self.assertEqual(result.used_tokens, result.baseline_tokens + 1000)

    def test_the_latest_run_peak_is_separate_from_the_next_message(self):
        last = {"peak_input_tokens": 60_000, "model_calls": 9, "compacted_results": 2}
        result = status(usage(last_run=last))
        self.assertEqual((result.used_tokens, result.used_basis), (result.baseline_tokens + result.history_tokens, "next_turn"))
        self.assertEqual(result.last_run.peak_input_tokens, 60_000)
        self.assertEqual(result.level, "ok")
        self.assertEqual(result.last_run.compacted_results, 2)

    def test_the_measured_requests_of_the_run_are_reported_next_to_the_estimate_and_never_replace_it(self):
        last = {"peak_input_tokens": 139_172, "model_calls": 44, "compacted_results": 0, "latest_input_tokens": 139_172,
                "first_input_tokens": 36_773, "active": True}
        result = status(usage(last_run=last), window=1_050_000)
        self.assertEqual((result.last_run.latest_input_tokens, result.last_run.first_input_tokens, result.last_run.active), (139_172, 36_773, True))
        self.assertEqual((result.used_basis, result.used_tokens), ("next_turn", result.baseline_tokens + result.history_tokens))

    def test_a_server_that_does_not_measure_the_main_agent_still_gives_a_valid_last_run(self):
        result = status(usage(last_run={"peak_input_tokens": 10, "model_calls": 1, "compacted_results": 0}))
        self.assertEqual((result.last_run.latest_input_tokens, result.last_run.first_input_tokens, result.last_run.active), (None, None, False))

    def test_a_small_peak_never_hides_the_starting_context(self):
        result = status(usage(last_run={"peak_input_tokens": 10, "model_calls": 1, "compacted_results": 0}))
        self.assertEqual(result.used_basis, "next_turn")

    def test_percent_is_capped_and_high_levels_are_flagged(self):
        result = status(usage(history_tokens=250_000))
        self.assertEqual((result.percent, result.level), (100.0, "high"))

    def test_without_a_configured_window_the_status_is_unknown_not_zero_percent_ok(self):
        result = status(window=None)
        self.assertEqual((result.level, result.window_tokens, result.percent), ("unknown", None, 0.0))

    def test_messages_beyond_the_digest_are_counted_as_omitted(self):
        result = status(usage(messages_total=40, messages_in_full=12, messages_condensed=12))
        self.assertEqual((result.messages_in_full, result.messages_condensed, result.messages_omitted), (12, 12, 16))
        self.assertEqual(status(usage(messages_total=2, messages_in_full=3)).messages_omitted, 0)


class FakeSession:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self, query):
        return iter(self.rows)


def message(sequence, text, role="user"):
    return NS(id=uuid4(), sequence=sequence, role=role, content=text)


class DigestTests(TestCase):
    def rows(self, count, size=300):
        return [message(sequence, "x" * size, "user" if sequence % 2 else "assistant") for sequence in range(count, 0, -1)]

    def test_measuring_the_digest_does_not_touch_the_conversation(self):
        conversation = NS(id=uuid4(), summary_json="untouched")
        digest = digest_history(FakeSession(self.rows(6)), conversation)
        self.assertEqual(conversation.summary_json, "untouched")
        self.assertEqual(len(digest["recent_messages"]), 6)

    def test_building_the_turn_context_still_records_the_summary(self):
        conversation = NS(id=uuid4(), summary_json=None)
        digest = bounded_context(FakeSession(self.rows(6)), conversation)
        self.assertEqual(conversation.summary_json, digest["summary"])

    def test_long_conversations_keep_recent_turns_whole_and_condense_older_ones_within_budget(self):
        conversation = NS(id=uuid4(), summary_json=None)
        digest = digest_history(FakeSession(self.rows(40, size=1500)), conversation)
        self.assertTrue(digest["summary"]["excerpts"])
        self.assertLess(len(digest["recent_messages"]), 40)
        import json
        self.assertLessEqual(len(json.dumps(digest, ensure_ascii=False, separators=(",", ":"))), CONTEXT_BUDGET + 400)
        newest = max(item["sequence"] for item in digest["recent_messages"])
        self.assertEqual(newest, 40)


class RouteTests(TestCase):
    def client(self, **settings):
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[current_user] = lambda: NS(id=uuid4())
        app.state.engine = object()
        app.state.settings = NS(llm_model="gpt-test", llm_provider="openai", llm_context_window_tokens=WINDOW,
                                llm_max_output_tokens=8192, **settings)
        return TestClient(app)

    def test_without_a_session_it_reports_the_starting_context_of_a_new_conversation(self):
        body = self.client().get("/api/v1/context").json()
        self.assertEqual((body["model"], body["window_tokens"], body["messages_total"], body["used_basis"]),
                         ("gpt-test", WINDOW, 0, "next_turn"))
        self.assertGreater(body["used_tokens"], 1000)

    def test_with_a_session_it_uses_that_sessions_measured_usage(self):
        last = {"peak_input_tokens": 30_000, "model_calls": 4, "compacted_results": 0}
        with patch("agentforge.api.routes_context.SessionRepository") as repository:
            repository.return_value.context_usage.return_value = usage(last_run=last)
            body = self.client().get(f"/api/v1/context?session_id={uuid4()}").json()
        self.assertEqual(body["used_tokens"], body["baseline_tokens"] + body["history_tokens"])
        self.assertEqual(body["used_basis"], "next_turn")
        self.assertEqual(body["last_run"]["peak_input_tokens"], 30_000)
        self.assertEqual(body["last_run"]["model_calls"], 4)

    def test_a_run_that_is_going_on_is_reported_with_its_live_measured_request(self):
        last = {"peak_input_tokens": 90_000, "model_calls": 12, "compacted_results": 0, "latest_input_tokens": 88_000,
                "first_input_tokens": 36_000, "active": True}
        with patch("agentforge.api.routes_context.SessionRepository") as repository:
            repository.return_value.context_usage.return_value = usage(last_run=last)
            body = self.client().get(f"/api/v1/context?session_id={uuid4()}").json()
        self.assertEqual((body["last_run"]["active"], body["last_run"]["latest_input_tokens"], body["last_run"]["first_input_tokens"]), (True, 88_000, 36_000))

    def test_an_unknown_or_foreign_session_is_not_found(self):
        with patch("agentforge.api.routes_context.SessionRepository") as repository:
            repository.return_value.context_usage.side_effect = TaskNotFound("session_not_found")
            self.assertEqual(self.client().get(f"/api/v1/context?session_id={uuid4()}").status_code, 404)

    def test_a_malformed_session_id_is_rejected(self):
        self.assertEqual(self.client().get("/api/v1/context?session_id=nope").status_code, 422)

    def test_an_unconfigured_window_is_reported_as_unknown(self):
        client = self.client()
        client.app.state.settings.llm_context_window_tokens = None
        self.assertEqual(client.get("/api/v1/context").json()["level"], "unknown")
