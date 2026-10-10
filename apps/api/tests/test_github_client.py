"""Offline public-source and task-submission boundaries; no database/provider use."""
import gzip
import io
import tarfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
from fastapi import HTTPException
from agentforge.contracts.tasks import TaskCreate
from agentforge.integrations.github_client import GitHubClient, GitHubError, unpack_archive, ResolvedRepository
from agentforge.api.routes_tasks import create_task
from agentforge.repositories.tasks import TaskNotFound

SHA = "a" * 40


def archive(entries):
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as tar:
        for name, data, kind in entries:
            member = tarfile.TarInfo(name)
            member.type = kind
            member.size = len(data) if kind == tarfile.REGTYPE else 0
            member.linkname = "../../outside" if kind in (tarfile.SYMTYPE, tarfile.LNKTYPE) else ""
            tar.addfile(member, io.BytesIO(data) if kind == tarfile.REGTYPE else None)
    return gzip.compress(raw.getvalue())


class ArchiveTests(unittest.TestCase):
    def test_binary_source_is_preserved(self):
        value = unpack_archive(archive([("repo-sha/app.py", b"print('hi')\n", tarfile.REGTYPE),
            ("repo-sha/image.png", b"\x89PNG\0\xff", tarfile.REGTYPE)]))
        self.assertEqual(value["image.png"], b"\x89PNG\0\xff")
        self.assertEqual(set(value), {"app.py", "image.png"})

    def test_unsafe_archives_fail_as_a_whole(self):
        cases = [("root/../outside", tarfile.REGTYPE), ("root/link", tarfile.SYMTYPE),
            ("root/link", tarfile.LNKTYPE), ("root/fifo", tarfile.FIFOTYPE),
            ("root/.git/config", tarfile.REGTYPE), ("root/.env.local", tarfile.REGTYPE),
            ("root/.ssh/config", tarfile.REGTYPE), ("root/a\\b", tarfile.REGTYPE)]
        for name, kind in cases:
            with self.subTest(name=name, kind=kind), self.assertRaises(GitHubError):
                unpack_archive(archive([("root/ok", b"ok", tarfile.REGTYPE), (name, b"bad", kind)]))

    def test_collisions_and_limits_rejected(self):
        with self.assertRaises(GitHubError):
            unpack_archive(archive([("root/A", b"1", tarfile.REGTYPE), ("root/a", b"2", tarfile.REGTYPE)]))
        with self.assertRaises(GitHubError):
            unpack_archive(archive([("root/file", b"x" * 1_000_001, tarfile.REGTYPE)]))
        with self.assertRaises(GitHubError):
            unpack_archive(archive([("root/a", b"1", tarfile.REGTYPE), ("root/a/b", b"2", tarfile.REGTYPE)]))
        with patch("agentforge.integrations.github_client.MAX_ARCHIVE_BYTES", 1000), self.assertRaises(GitHubError):
            unpack_archive(gzip.compress(b"x" * 1001))
        with patch("agentforge.integrations.github_client.MAX_FILES", 1), self.assertRaises(GitHubError):
            unpack_archive(archive([("root/a", b"1", tarfile.REGTYPE), ("root/b", b"2", tarfile.REGTYPE)]))


class PublicClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_pinned_public_snapshot_no_credentials_and_official_hosts(self):
        seen = []
        async def respond(request):
            seen.append(request)
            if request.url.host == "codeload.github.com":
                return httpx.Response(200, content=archive([("repo-sha/a", b"abc", tarfile.REGTYPE)]))
            if "/commits/" in request.url.path:
                return httpx.Response(200, json={"sha": SHA})
            return httpx.Response(200, json={"private": False, "full_name": "owner/repo", "default_branch": "main"})
        result = await GitHubClient(transport=httpx.MockTransport(respond)).snapshot("owner/repo", SHA)
        self.assertEqual(result.files, {"a": b"abc"})
        self.assertEqual(result.commit_sha, SHA)
        self.assertTrue(all("authorization" not in request.headers and "cookie" not in request.headers for request in seen))
        self.assertEqual({request.url.host for request in seen}, {"api.github.com", "codeload.github.com"})

    async def test_private_redirect_and_invalid_slug_rejected(self):
        for status, payload in [(200, {"private": True}), (302, {})]:
            requests = []
            def respond(request):
                requests.append(request)
                return httpx.Response(status, json=payload, headers={"location": "https://evil.invalid"})
            with self.subTest(status=status), self.assertRaises(GitHubError):
                await GitHubClient(transport=httpx.MockTransport(respond)).resolve("owner/repo", "main")
            self.assertEqual(len(requests), 1)
        with self.assertRaises(GitHubError):
            await GitHubClient().resolve("../repo", "main")
        with self.assertRaises(GitHubError):
            await GitHubClient().snapshot("owner/repo", "main")

    async def test_issue_actual_content(self):
        def respond(request):
            if "/issues/" in request.url.path:
                return httpx.Response(200, json={"number": 12, "title": "Fix", "body": "Expected behavior", "state": "open"})
            return httpx.Response(200, json={"private": False})
        issue = await GitHubClient(transport=httpx.MockTransport(respond)).get_issue("owner/repo", 12)
        self.assertEqual(issue["body"], "Expected behavior")
        self.assertEqual(issue["url"], "https://github.com/owner/repo/issues/12")


class SubmissionTests(unittest.IsolatedAsyncioTestCase):
    def request(self):
        return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=SimpleNamespace(
            e2b_api_key="test-only", e2b_template_id="test-template", e2b_template_qualified=True))))

    def body(self, **changes):
        return TaskCreate(project_id=uuid4(), goal="Implement a bounded test change", kind="coding", coding={
            "repository": {"repo_id": "owner/repo", "ref": "main"}, "test_profiles": ["python_unittest"]}, **changes)

    async def test_owner_is_checked_before_github_and_pin_before_queue(self):
        owner = uuid4()
        repository = SimpleNamespace(authorize_project=lambda **kw: None, create=lambda **kw: kw)
        with patch("agentforge.api.routes_tasks.GitHubClient.resolve", AsyncMock(return_value=ResolvedRepository("owner/repo", SHA, "main"))):
            result = await create_task(self.body(), self.request(), SimpleNamespace(id=owner), repository)
        self.assertEqual(result["owner_id"], owner)
        self.assertEqual(result["request"].coding.repository.ref, SHA)
        def deny(**kw):
            raise TaskNotFound()
        repository.authorize_project = deny
        with patch("agentforge.api.routes_tasks.GitHubClient.resolve", AsyncMock()) as github:
            with self.assertRaises(HTTPException) as error:
                await create_task(self.body(), self.request(), SimpleNamespace(id=owner), repository)
            self.assertEqual(error.exception.status_code, 404)
            github.assert_not_called()

    async def test_missing_sandbox_does_not_queue(self):
        request = self.request()
        request.app.state.settings.e2b_api_key = None
        create = unittest.mock.Mock()
        repository = SimpleNamespace(authorize_project=lambda **kw: None, create=create)
        with self.assertRaises(HTTPException) as error:
            await create_task(self.body(), request, SimpleNamespace(id=uuid4()), repository)
        self.assertEqual(error.exception.status_code, 503)
        create.assert_not_called()

    def test_research_default_remains_compatible_and_identity_is_forbidden(self):
        body = TaskCreate(project_id=uuid4(), goal="Research bounded evidence sources")
        self.assertEqual(body.kind, "research")
        self.assertIsNone(body.coding)
        with self.assertRaises(ValueError):
            TaskCreate(project_id=uuid4(), goal=body.goal, created_by=uuid4())
        with self.assertRaises(ValueError):
            TaskCreate(project_id=uuid4(), goal=body.goal, kind="coding")


class ReadBoundaryTests(unittest.TestCase):
    def test_coding_download_uses_owned_metadata_and_fixed_filename(self):
        from agentforge.api.routes_artifacts import download
        task_id, artifact_id, user_id, project_id = uuid4(), uuid4(), uuid4(), uuid4()
        task = SimpleNamespace(kind="coding", project_id=project_id)
        repository = SimpleNamespace(get=lambda **kw: task)
        storage = unittest.mock.Mock()
        storage.read.return_value = b"diff --git a/a b/a\n"
        coding = unittest.mock.Mock()
        coding.get_artifact.return_value = {"kind": "diff", "filename": 'untrusted\r\nheader.patch',
            "storage_key": "private-key", "sha256": "a" * 64}
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=SimpleNamespace(research_storage_path="unused"))))
        with patch("agentforge.api.routes_artifacts.sessions", return_value=None), patch(
                "agentforge.api.routes_artifacts.CodingRepository", return_value=coding), patch(
                "agentforge.api.routes_artifacts.ArtifactStorage", return_value=storage):
            result = download(task_id, artifact_id, request, SimpleNamespace(id=user_id), repository)
        self.assertEqual(result.headers["content-disposition"], 'attachment; filename="coding.patch"')
        self.assertEqual(result.body, storage.read.return_value)
        coding.get_artifact.assert_called_once_with(artifact_id=str(artifact_id), task_id=task_id, owner_id=user_id, project_id=project_id)

    def test_configuration_exposes_presence_not_credentials(self):
        from agentforge.api.routes_configuration import configuration
        settings = SimpleNamespace(llm_model="test", llm_api_key="private-model-secret", llm_context_window_tokens=10000,
            llm_input_usd_per_million=1, llm_output_usd_per_million=1, llm_supports_tools=True, llm_supports_json=True,
            tavily_api_key="private-search-secret", neatlogs_api_key=None, e2b_api_key="private-sandbox-secret",
            e2b_template_id=None, e2b_template_qualified=False)
        result = configuration(SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=settings))), SimpleNamespace())
        self.assertTrue(result["research_configured"])
        self.assertFalse(result["coding_configured"])
        self.assertEqual(result["coding_missing_settings"], ["E2B_TEMPLATE_ID", "E2B_TEMPLATE_QUALIFIED=true"])
        self.assertNotIn("private-", str(result))


if __name__ == "__main__":
    unittest.main()
