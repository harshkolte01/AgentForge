"""The task's GitHub adapter: repeated authorization checks are cheap, repository loading is concurrent but bounded.

Offline: GitHub is an httpx.MockTransport and the identity/database side of `_authorize` is replaced.
"""
import asyncio
import base64
import hashlib
import unittest
from types import SimpleNamespace as NS

import httpx
from pydantic import SecretStr

from agentforge.integrations.github_app import SNAPSHOT_CONCURRENCY, AuthorizedGitHubClient, GitHubAppClient
from agentforge.integrations.github_client import GitHubError
from agentforge.settings import Settings

SHA = "a" * 40


def settings():
    return Settings(_env_file=None, github_app_id=123, github_client_id="client", github_client_secret=SecretStr("fixture"))


class AuthorizationCacheTests(unittest.IsolatedAsyncioTestCase):
    def client(self, outcomes):
        self.now, self.checks = 0.0, []
        github = AuthorizedGitHubClient(None, settings(), "user", clock=lambda: self.now)

        async def check(repo_id, *, write=False, repository_id=None):
            self.checks.append((repo_id, write))
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        github._authorize = check
        return github

    def granted(self, ident=7):
        return (NS(token="ghu_x"), {"id": ident, "full_name": "Owner/Repo"}, {"id": 1})

    async def test_a_successful_check_is_reused_for_a_minute_then_made_again(self):
        first, second = self.granted(), self.granted()
        github = self.client([first, second])
        self.assertIs((await github.authorize("owner/repo"))[0], first[0])
        self.now += 59
        self.assertIs((await github.authorize("Owner/Repo"))[0], first[0])  # names compare case-insensitively
        self.assertEqual(len(self.checks), 1)
        self.now += 2
        self.assertIs((await github.authorize("owner/repo"))[0], second[0])
        self.assertEqual(len(self.checks), 2)

    async def test_a_failed_check_is_never_kept(self):
        github = self.client([GitHubError("github_access_denied"), self.granted()])
        with self.assertRaises(GitHubError):
            await github.authorize("owner/repo")
        await github.authorize("owner/repo")
        self.assertEqual(len(self.checks), 2)

    async def test_a_revoked_repository_is_noticed_once_the_minute_is_over(self):
        denied = GitHubError("github_repository_not_authorized")
        github = self.client([self.granted(), denied, denied])
        await github.authorize("owner/repo")
        self.now += 61
        for _ in range(2):  # the failure did not leave the old grant behind either
            with self.assertRaises(GitHubError):
                await github.authorize("owner/repo")
        self.assertEqual(len(self.checks), 3)

    async def test_a_write_check_is_always_made_afresh(self):
        github = self.client([self.granted(), self.granted()])
        await github.authorize("owner/repo")
        await github.authorize("owner/repo", write=True)
        self.assertEqual(self.checks, [("owner/repo", False), ("owner/repo", True)])

    async def test_a_cached_grant_still_enforces_the_repository_identity(self):
        github = self.client([self.granted(ident=7)])
        await github.authorize("owner/repo")
        with self.assertRaisesRegex(GitHubError, "identity_changed"):
            await github.authorize("owner/repo", repository_id=8)
        await github.authorize("owner/repo", repository_id=7)
        self.assertEqual(len(self.checks), 1)

    async def test_concurrent_checks_share_the_one_in_flight(self):
        gate = asyncio.Event()
        github = AuthorizedGitHubClient(None, settings(), "user")
        calls = []

        async def check(repo_id, **kwargs):
            calls.append(repo_id)
            await gate.wait()
            return self.granted()
        github._authorize = check
        waiting = [asyncio.create_task(github.authorize("owner/repo")) for _ in range(5)]
        await asyncio.sleep(0)
        gate.set()
        results = await asyncio.gather(*waiting)
        self.assertEqual(len(calls), 1)
        self.assertTrue(all(result[0] is results[0][0] for result in results))


def blob(data):
    return {"encoding": "base64", "content": base64.b64encode(data).decode()}


def git_sha(data):
    return hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()


class SnapshotTests(unittest.IsolatedAsyncioTestCase):
    async def snapshot(self, files, *, corrupt=None, delay=0.0, executable=()):
        entries = [{"path": path, "type": "blob", "mode": "100755" if path in executable else "100644", "size": len(data),
            "sha": git_sha(data)} for path, data in files.items()]
        by_sha = {git_sha(data): data for data in files.values()}
        state = {"open": 0, "peak": 0, "blobs": 0}

        async def respond(request):
            path = request.url.path
            if "/git/trees/" in path:
                return httpx.Response(200, json={"tree": [{"path": "src", "type": "tree"}, *entries]})
            sha = path.rsplit("/", 1)[1]
            state["open"] += 1
            state["peak"] = max(state["peak"], state["open"])
            await asyncio.sleep(delay)
            state["open"] -= 1
            state["blobs"] += 1
            return httpx.Response(200, json=blob(b"tampered" if sha == corrupt else by_sha[sha]))
        app = GitHubAppClient(settings(), token="ghu_x", transport=httpx.MockTransport(respond))
        github = AuthorizedGitHubClient(None, settings(), "user")

        async def authorize(repo_id, **kwargs):
            return app, {"id": 1, "full_name": "owner/repo"}, {"id": 1}
        github.authorize = authorize
        result = await github.snapshot("owner/repo", SHA)
        return result, state

    async def test_files_arrive_in_tree_order_with_bounded_concurrency(self):
        files = {f"src/file{index:02}.py": f"print({index})\n".encode() for index in range(30)}
        result, state = await self.snapshot(files, delay=0.01)
        self.assertEqual(list(result.files), list(files))
        self.assertEqual(result.files, files)
        self.assertGreater(state["peak"], 1)
        self.assertLessEqual(state["peak"], SNAPSHOT_CONCURRENCY)

    async def test_executable_files_keep_their_mode(self):
        files = {"run.sh": b"#!/bin/sh\necho hi\n", "README.md": b"hi\n"}
        result, _ = await self.snapshot(files, executable={"run.sh"})
        self.assertEqual(result.modes, {"run.sh": 0o755, "README.md": 0o644})

    async def test_a_blob_that_fails_its_integrity_check_fails_the_snapshot(self):
        files = {f"f{index}.txt": f"{index}".encode() for index in range(20)}
        with self.assertRaisesRegex(GitHubError, "integrity"):
            await self.snapshot(files, corrupt=git_sha(b"3"))

    async def test_files_that_cannot_be_loaded_are_skipped_and_never_fetched(self):
        fetched = []
        tree = [{"path": "ok.txt", "type": "blob", "mode": "100644", "size": 1, "sha": git_sha(b"x")},
                {"path": ".env", "type": "blob", "mode": "100644", "size": 1, "sha": SHA},
                {"path": "keys/server.pem", "type": "blob", "mode": "100644", "size": 1, "sha": SHA},
                {"path": "link", "type": "blob", "mode": "120000", "size": 1, "sha": SHA},
                {"path": "vendor/lib", "type": "commit", "mode": "160000", "sha": SHA},
                {"path": "big.bin", "type": "blob", "mode": "100644", "size": 1_000_001, "sha": SHA},
                {"path": "Ok.txt", "type": "blob", "mode": "100644", "size": 1, "sha": SHA},
                {"path": "src", "type": "tree", "mode": "040000", "sha": SHA}]

        async def respond(request):
            if "/git/trees/" in request.url.path:
                return httpx.Response(200, json={"tree": tree})
            fetched.append(request.url.path)
            return httpx.Response(200, json=blob(b"x"))
        app = GitHubAppClient(settings(), token="ghu_x", transport=httpx.MockTransport(respond))
        github = AuthorizedGitHubClient(None, settings(), "user")

        async def authorize(repo_id, **kwargs):
            return app, {"id": 1, "full_name": "owner/repo"}, {"id": 1}
        github.authorize = authorize
        result = await github.snapshot("owner/repo", SHA)
        self.assertEqual(list(result.files), ["ok.txt"])
        self.assertEqual(dict(result.skipped), {".env": "sensitive", "keys/server.pem": "sensitive", "link": "symlink",
            "vendor/lib": "submodule", "big.bin": "too_large", "Ok.txt": "case_clash"})
        self.assertEqual(len(fetched), 1)  # only the one file that is kept was downloaded

    async def test_requests_share_one_client_and_keep_the_token(self):
        seen = []

        def respond(request):
            seen.append(request.headers.get("authorization"))
            return httpx.Response(200, json={"ok": True})
        app = GitHubAppClient(settings(), token="ghu_x", transport=httpx.MockTransport(respond))
        async with app.http() as http:
            self.assertEqual(await app.request("GET", "/a", http=http), {"ok": True})
        self.assertEqual(await app.request("GET", "/b"), {"ok": True})
        self.assertEqual(seen, ["Bearer ghu_x", "Bearer ghu_x"])


if __name__ == "__main__":
    unittest.main()
