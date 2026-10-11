"""Public conversation grants and lower-trust input bounds."""
import unittest
from uuid import uuid4
from agentforge.contracts.sessions import SessionTurnCreate
from agentforge.contracts.tasks import TaskCreate
from agentforge.api.routes_agents import validate_management
from agentforge.subagents.registry import load_builtin
from fastapi import HTTPException

class SessionContractTests(unittest.TestCase):
    def test_project_identity_and_unknown_grants_forbidden(self):
        request = {"idempotency_key": uuid4(), "goal": "Research concrete task with evidence"}
        self.assertIsNone(SessionTurnCreate(**request).project_id)
        for extra in ({"project_id": uuid4()}, {"owner_id": uuid4()}, {"github_write": True}):
            with self.assertRaises(ValueError):
                SessionTurnCreate(**request, **extra)

    def test_attachment_utf8_aggregate_and_custom_version_bounds(self):
        base = {"project_id": uuid4(), "kind": "general", "goal": "Research concrete task with evidence"}
        TaskCreate(**base, attachments=[{"name": "notes.txt", "content": "hello"}])
        with self.assertRaises(ValueError):
            TaskCreate(**base, attachments=[{"name": "notes.txt", "content": "界" * 8000}])
        with self.assertRaises(ValueError):
            TaskCreate(**base, agent_key=f"project/{uuid4()}/docs-writer")
        with self.assertRaises(ValueError):
            TaskCreate(**base, attachments=[{"name": "x", "content": "y"}] * 5)

    def test_a_selected_repository_ref_must_be_safe_to_resolve(self):
        from pydantic import ValidationError
        from agentforge.contracts.coding import RepoSelection
        with self.assertRaises(ValidationError):
            RepoSelection(repo_id="owner/repo", ref="../main")
        with self.assertRaises(ValidationError):
            RepoSelection(repo_id="not a repo", ref="main")
        self.assertEqual(RepoSelection(repo_id="owner/repo").ref, "main")
        self.assertEqual(RepoSelection(repo_id="owner/repo", ref="feature/x").ref, "feature/x")

    def test_custom_agent_management_denies_execution_and_unbounded_grants(self):
        builtin = load_builtin("agent", 1)
        with self.assertRaises(HTTPException):  # a built-in name can never be managed
            validate_management(builtin)
        # The built-in agent's own tools and grants (sandbox, repository, shell) are not available to custom agents.
        copy = builtin.model_copy(update={"name": "private-agent"})
        with self.assertRaises(HTTPException):
            validate_management(copy)
        with self.assertRaises(HTTPException):
            validate_management(copy.model_copy(update={"allowed_tools": ("web_search",)}))


class MessageActivityContractTests(unittest.TestCase):
    """The additive message metadata keeps old payloads valid and never carries attachment content."""

    def message(self, **extra):
        from datetime import datetime, timezone
        from agentforge.contracts.sessions import MessageView
        return MessageView(id=uuid4(), session_id=uuid4(), sequence=2, role="assistant", content="done", task_id=uuid4(),
            created_at=datetime.now(timezone.utc), **extra)

    def test_legacy_fields_stay_and_new_ones_default_empty(self):
        shown = self.message().model_dump(mode="json")
        self.assertEqual((shown["steps"], shown["activity"], shown["attachments"]), ([], None, []))

    def test_activity_and_attachments_serialize_additively(self):
        from agentforge.contracts.activity import AttachmentView, MessageActivity
        shown = self.message(steps=[{"tool": "web_search", "status": "done"}], attachments=[AttachmentView(name="n.txt", characters=9)],
            activity=MessageActivity(action_count=61, note_count=0, available=True, terminal=True, status="COMPLETED")).model_dump(mode="json")
        self.assertEqual(shown["activity"]["action_count"], 61)
        self.assertEqual(shown["attachments"], [{"name": "n.txt", "characters": 9}])
        self.assertEqual(shown["steps"], [{"tool": "web_search", "status": "done"}])

    def test_attachment_views_have_no_content_field(self):
        from agentforge.contracts.activity import AttachmentView
        self.assertEqual(set(AttachmentView.model_fields), {"name", "characters"})
        with self.assertRaises(ValueError):
            AttachmentView(name="n.txt", characters=1, content="secret")

    def test_the_steps_list_and_the_counts_share_one_suppression_rule(self):
        from agentforge.repositories import sessions
        from agentforge.tools import visibility
        self.assertIs(sessions.SELF_CORRECTION_CODES, visibility.SELF_CORRECTION_CODES)
