"""Cached private coding work remains subject to current source authorization."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from agentforge.workers.runner import task_authority
from agentforge.integrations.github_client import GitHubError

class WorkerAuthorityTests(unittest.IsolatedAsyncioTestCase):
    async def test_cached_workspace_requires_fresh_authority_and_fence(self):
        github = SimpleNamespace(authorize=AsyncMock())
        runtime = SimpleNamespace(coding=SimpleNamespace(github=github))
        jobs = SimpleNamespace(authority=AsyncMock())
        store = SimpleNamespace(task=lambda: SimpleNamespace(permissions_json={"coding": {"repository": {"repo_id": "owner/repo"}}}))
        await task_authority(SimpleNamespace(auth_mode="github_app"), runtime, store, jobs, "lease")
        github.authorize.assert_awaited_once_with("owner/repo")
        self.assertEqual(jobs.authority.await_count, 2)
        github.authorize.side_effect = GitHubError("github_access_denied")
        with self.assertRaises(GitHubError):
            await task_authority(SimpleNamespace(auth_mode="github_app"), runtime, store, jobs, "lease")

    async def test_local_research_does_not_require_github(self):
        jobs = SimpleNamespace(authority=AsyncMock())
        await task_authority(SimpleNamespace(auth_mode="local_single_user"), SimpleNamespace(coding=None), None, jobs, "lease")
        jobs.authority.assert_awaited_once_with("lease")
