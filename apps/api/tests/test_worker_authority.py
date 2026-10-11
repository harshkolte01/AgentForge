"""A selected private repository stays subject to current source authorization on every model call and tool call."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from agentforge.workers.runner import task_authority
from agentforge.integrations.github_client import GitHubError

class WorkerAuthorityTests(unittest.IsolatedAsyncioTestCase):
    async def test_local_runs_only_check_the_lease(self):
        jobs = SimpleNamespace(authority=AsyncMock())
        await task_authority(SimpleNamespace(auth_mode="local_single_user"), SimpleNamespace(repository_github=None), None, jobs, "lease")
        jobs.authority.assert_awaited_once_with("lease")

    async def test_selected_repository_rechecks_private_source_rights_with_a_fence_on_both_sides(self):
        github = SimpleNamespace(authorize=AsyncMock())
        runtime = SimpleNamespace(repository_github=github)
        jobs = SimpleNamespace(authority=AsyncMock())
        store = SimpleNamespace(task=lambda: SimpleNamespace(permissions_json={"repository": {"repo_id": "owner/repo"}}))
        await task_authority(SimpleNamespace(auth_mode="github_app"), runtime, store, jobs, "lease")
        github.authorize.assert_awaited_once_with("owner/repo")
        self.assertEqual(jobs.authority.await_count, 2)
        github.authorize.side_effect = GitHubError("github_access_denied")
        with self.assertRaises(GitHubError):
            await task_authority(SimpleNamespace(auth_mode="github_app"), runtime, store, jobs, "lease")

    async def test_a_conversation_without_a_repository_asks_github_nothing(self):
        github = SimpleNamespace(authorize=AsyncMock())
        jobs = SimpleNamespace(authority=AsyncMock())
        store = SimpleNamespace(task=lambda: SimpleNamespace(permissions_json={}))
        await task_authority(SimpleNamespace(auth_mode="github_app"), SimpleNamespace(repository_github=github), store, jobs, "lease")
        github.authorize.assert_not_awaited()
        jobs.authority.assert_awaited_once_with("lease")

    async def test_a_lost_lease_stops_the_check_before_github_is_asked(self):
        github = SimpleNamespace(authorize=AsyncMock())
        jobs = SimpleNamespace(authority=AsyncMock(side_effect=RuntimeError("lease lost")))
        store = SimpleNamespace(task=lambda: SimpleNamespace(permissions_json={"repository": {"repo_id": "owner/repo"}}))
        with self.assertRaises(RuntimeError):
            await task_authority(SimpleNamespace(auth_mode="github_app"), SimpleNamespace(repository_github=github), store, jobs, "lease")
        github.authorize.assert_not_awaited()

    async def test_github_owner_rechecked_even_in_local_worker_mode(self):
        github = SimpleNamespace(authorize=AsyncMock())
        jobs = SimpleNamespace(authority=AsyncMock())
        store = SimpleNamespace(task=lambda: SimpleNamespace(permissions_json={"repository": {"repo_id": "owner/repo"}}))
        await task_authority(SimpleNamespace(auth_mode="local_single_user"), SimpleNamespace(repository_github=github), store, jobs, "lease")
        github.authorize.assert_awaited_once_with("owner/repo")
        self.assertEqual(jobs.authority.await_count,2)

class RepositoryIdentityTests(unittest.TestCase):
    def test_task_owner_selects_client_in_a_shared_worker(self):
        from contextlib import contextmanager
        from agentforge.bootstrap import repository_client
        from agentforge.integrations.github_app import AuthorizedGitHubClient
        from agentforge.integrations.github_client import GitHubClient
        for mode, identity, expected in [('local_single_user',123,AuthorizedGitHubClient),('local_single_user',-1,GitHubClient),('github_app',123,AuthorizedGitHubClient)]:
            @contextmanager
            def sessions(): yield SimpleNamespace(scalar=lambda query:identity)
            with self.subTest(mode=mode,identity=identity):
                self.assertIsInstance(repository_client(SimpleNamespace(auth_mode=mode),sessions,None),expected)
