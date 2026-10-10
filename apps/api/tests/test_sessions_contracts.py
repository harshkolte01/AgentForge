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

    def test_custom_agent_management_denies_execution_and_unbounded_grants(self):
        definition = load_builtin("researcher", 1)
        with self.assertRaises(HTTPException):
            validate_management(definition)
        with self.assertRaises(HTTPException):
            validate_management(load_builtin("general-executor", 2))
