"""What the agent is told each turn about the conversation's GitHub delivery (subagents/environment.py)."""
from contextlib import contextmanager
from types import SimpleNamespace as NS
from unittest import TestCase
from uuid import uuid4

from agentforge.subagents import environment
from agentforge.subagents.environment import delivery_description, turn_environment


def row(**values):
    base = dict(branch=None, head_sha=None, pr_number=None, pr_json={}, unpublished_json=None)
    return NS(**{**base, **values})


class DescriptionTests(TestCase):
    def test_nothing_published_and_nothing_waiting_says_nothing(self):
        self.assertIsNone(delivery_description(row()))

    def test_changes_waiting_to_be_pushed_are_mentioned(self):
        text = delivery_description(row(unpublished_json={"files": 3}))
        self.assertIn("3 changed files are not pushed yet", text)

    def test_a_pull_request_with_failing_checks_conflicts_and_requested_changes_is_described_with_how_to_continue(self):
        text = delivery_description(row(branch="agentforge/fix-abc123", head_sha="a" * 40, pr_number=7, pr_json={
            "state": "open", "checks": {"state": "failing", "failed": 2}, "mergeable": "conflicting",
            "reviews": {"decision": "changes_requested"}}))
        for part in ("agentforge/fix-abc123", "pull request #7", "open", "checks failing (2 failed)", "merge conflicts", "changes requested",
                     "get_pr_status", "never opens a second one", "you cannot"):
            self.assertIn(part, text)


class IsolationTests(TestCase):
    """A fault while reading the delivery must cost the turn only that one line, not its whole environment block."""

    def sessions(self, delivery):
        answers = iter([{"repository": {"repo_id": "owner/repo", "ref": "a" * 40}}, "sbx-1", delivery])

        class Db:
            rolled_back = False

            def scalar(self, statement):
                value = next(answers)
                if isinstance(value, Exception):
                    raise value
                return value

            def rollback(self):
                Db.rolled_back = True

        db = Db()

        @contextmanager
        def factory():
            yield db
        factory.db = db
        return factory

    def facts(self, delivery):
        sessions = self.sessions(delivery)
        # the approval mode is read through its own helper: keep this test about the delivery line only
        from unittest.mock import patch
        with patch.object(environment, "get_mode", return_value="ask"):
            return turn_environment(sessions, task_id=uuid4(), user_id=uuid4(), tools=("bash", "create_pull_request")), sessions.db

    def test_a_missing_table_leaves_every_other_fact_in_place(self):
        facts, db = self.facts(RuntimeError('relation "conversation_deliveries" does not exist'))
        self.assertNotIn("GitHub delivery", facts)
        self.assertIn("Workspace", facts)
        self.assertIn("owner/repo", facts["Repository"])
        self.assertIn("Approvals", facts)
        self.assertTrue(db.rolled_back)   # the failed statement does not poison the rest of the session

    def test_a_delivery_that_exists_adds_its_line(self):
        facts, _ = self.facts(row(branch="agentforge/x-111111", head_sha="b" * 40, unpublished_json={"files": 1}))
        self.assertIn("agentforge/x-111111", facts["GitHub delivery"])
