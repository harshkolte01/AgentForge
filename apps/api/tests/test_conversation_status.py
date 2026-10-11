"""The sidebar status of a conversation: which state wins, what counts as unseen, and the read receipt's limits.

Real queries, the migration and the read position's invariants are checked by examples/conversation_status_smoke.py.
"""
from datetime import datetime, timezone
from types import SimpleNamespace as NS
from unittest import TestCase
from uuid import uuid4

from pydantic import ValidationError

from agentforge.contracts.sessions import ReadReceipt, SessionView
from agentforge.repositories.sessions import SessionRepository


class FakeDB:
    """Answers the two questions `activities` may ask; `asked` records which it did."""
    def __init__(self, approvals=(), latest=()):
        self.approvals, self.latest, self.asked = list(approvals), list(latest), []

    def scalars(self, query):
        self.asked.append("approvals")
        return iter(self.approvals)

    def execute(self, query):
        self.asked.append("latest")
        return iter(self.latest)


def chat(*, task=None, paused=False, read=0):
    return NS(id=uuid4(), active_task_id=task, queue_paused=paused, read_sequence=read)


def states(rows, counts=None, **db):
    fake = FakeDB(**db)
    result = SessionRepository.activities(fake, rows, counts or {})
    return [result[row.id] for row in rows], fake


class LiveStateTests(TestCase):
    def test_an_active_task_is_running_unless_a_tool_call_waits_for_the_person(self):
        working, asking = chat(task=uuid4()), chat(task=uuid4())
        result, _ = states([working, asking], approvals=[asking.active_task_id])
        self.assertEqual(result, ["running", "waiting_approval"])

    def test_an_approval_only_counts_on_the_conversations_own_active_task(self):
        working = chat(task=uuid4())
        result, _ = states([working], approvals=[uuid4()])
        self.assertEqual(result, ["running"])

    def test_the_moment_between_two_messages_is_still_running(self):
        waiting = chat()
        result, _ = states([waiting], {waiting.id: (1, 0)})
        self.assertEqual(result, ["running"])

    def test_a_queue_the_person_paused_is_not_running(self):
        paused = chat(paused=True)
        result, _ = states([paused], {paused.id: (2, 0)})
        self.assertEqual(result, ["idle"])

    def test_a_message_that_could_not_start_is_failed(self):
        stuck = chat(paused=True)
        result, _ = states([stuck], {stuck.id: (0, 1)})
        self.assertEqual(result, ["failed"])

    def test_a_live_state_needs_no_question_about_finished_turns(self):
        _, fake = states([chat(task=uuid4())])
        self.assertEqual(fake.asked, ["approvals"])
        _, fake = states([chat()], latest=[])
        self.assertEqual(fake.asked, ["latest"])


class FinishedTurnTests(TestCase):
    def outcome(self, state, reply, ordinal=3, read=0):
        row = chat(read=read)
        result, _ = states([row], latest=[(row.id, state, ordinal, reply)])
        return result[0]

    def test_a_reply_beyond_the_read_position_is_unread(self):
        self.assertEqual(self.outcome("completed", reply=4, read=3), "unread")
        self.assertEqual(self.outcome("partial", reply=4, read=0), "unread")

    def test_a_reply_that_was_seen_shows_nothing(self):
        self.assertEqual(self.outcome("completed", reply=4, read=4), "idle")
        self.assertEqual(self.outcome("failed", reply=4, read=9), "idle")

    def test_a_failed_reply_that_was_not_seen_is_failed(self):
        self.assertEqual(self.outcome("failed", reply=4, read=1), "failed")

    def test_a_stop_is_the_persons_own_act_and_shows_nothing(self):
        self.assertEqual(self.outcome("cancelled", reply=4, read=0), "idle")

    def test_a_turn_with_no_reply_is_judged_by_its_own_message(self):
        self.assertEqual(self.outcome("failed", reply=None, ordinal=7, read=3), "failed")
        self.assertEqual(self.outcome("failed", reply=None, ordinal=7, read=7), "idle")

    def test_a_conversation_with_no_finished_turn_is_idle(self):
        result, _ = states([chat()], latest=[])
        self.assertEqual(result, ["idle"])

    def test_each_conversation_is_judged_by_its_own_turn(self):
        seen, fresh = chat(read=4), chat(read=0)
        result, _ = states([seen, fresh], latest=[(seen.id, "completed", 3, 4), (fresh.id, "completed", 3, 4)])
        self.assertEqual(result, ["idle", "unread"])


class ContractTests(TestCase):
    def view(self, **extra):
        now = datetime.now(timezone.utc)
        return SessionView(id=uuid4(), project_id=uuid4(), title="t", mode="general", archived=False, active_task_id=None,
                           last_sequence=0, created_at=now, updated_at=now, **extra)

    def test_an_older_row_without_a_status_is_idle_and_unread_nothing(self):
        view = self.view()
        self.assertEqual((view.status, view.read_sequence), ("idle", 0))

    def test_only_known_statuses_are_accepted(self):
        for status in ("idle", "running", "waiting_approval", "failed", "unread"):
            self.assertEqual(self.view(status=status).status, status)
        with self.assertRaises(ValidationError):
            self.view(status="done")

    def test_a_read_receipt_is_a_bounded_non_negative_position_and_nothing_else(self):
        self.assertEqual(ReadReceipt(sequence=0).sequence, 0)
        for bad in ({"sequence": -1}, {"sequence": 3_000_000}, {"sequence": "x"}, {}, {"sequence": 1, "task": "x"}):
            with self.assertRaises(ValidationError, msg=str(bad)):
                ReadReceipt(**bad)
