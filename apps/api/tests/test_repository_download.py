"""Loading a repository by letting the sandbox download it: the VM-side scripts run for real (with the paths in a temporary folder),
the plan of what to leave out, the GitHub archive links, and how the load falls back to the snapshot upload."""
import asyncio
import json
import os
import pathlib
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

import httpx

from agentforge.integrations.e2b_sandbox import SandboxUnavailable
from agentforge.integrations.github_app import AuthorizedGitHubClient, GitHubAppClient
from agentforge.integrations.github_client import ArchiveLink, GitHubClient, GitHubError
from agentforge.sandbox import vm_repository, vm_scan
from agentforge.tools.sandbox_shell import BASE_TAG, MARKER

from test_github_app_client import SHA as APP_SHA, settings as app_settings
from test_sandbox_shell import Env, REPO, SHA

URL = "https://codeload.github.com/owner/repo/legacy.tar.gz/" + SHA + "?token=SECRETTOKEN"


class Folder(unittest.TestCase):
    """A temporary folder standing in for the VM's file system: every path constant of `vm_repository` points into it."""

    def setUp(self):
        self.root = pathlib.Path(tempfile.mkdtemp(prefix="repo-download-"))
        self.addCleanup(shutil.rmtree, self.root, True)
        paths = {"STAGE": self.root / "stage", "SCAN": self.root / "scan.py", "LIST": self.root / "list.json", "DROP": self.root / "drop.json",
                 "CURL_CONFIG": self.root / "curl", "ARCHIVE": self.root / "repo.tgz", "WORKSPACE": self.root / "workspace"}
        leftovers = tuple(str(paths[name]) for name in ("STAGE", "LIST", "DROP", "CURL_CONFIG", "ARCHIVE", "SCAN"))
        patcher = patch.multiple(vm_repository, PYTHON=sys.executable, LEFTOVERS=leftovers, **{key: str(value) for key, value in paths.items()})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.stage, self.workspace = paths["STAGE"], paths["WORKSPACE"]
        self.workspace.mkdir()

    def put(self, folder: pathlib.Path, files: dict[str, bytes]) -> None:
        for name, data in files.items():
            target = folder / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)

    def bash(self, script: str) -> subprocess.CompletedProcess:
        return subprocess.run(["bash", "-c", script], capture_output=True, text=True, cwd="/")


class ScanTests(Folder):
    def test_files_links_and_special_files_are_listed_with_their_kind_in_path_order(self):
        self.put(self.stage, {"b.txt": b"1", "src/app.py": b"2", "src/deep/x.py": b"3", ".github/ci.yml": b"4"})
        os.symlink("b.txt", self.stage / "link")
        os.mkfifo(self.stage / "pipe")
        (self.stage / "empty-folder").mkdir()
        listing = vm_scan.scan(str(self.stage))
        self.assertEqual([(item["k"], item["p"]) for item in listing], [
            ("f", ".github/ci.yml"), ("f", "b.txt"), ("l", "link"), ("x", "pipe"), ("f", "src/app.py"), ("f", "src/deep/x.py")])

    def test_a_name_that_is_not_text_is_listed_as_hex_and_can_still_be_removed(self):
        raw = os.fsencode(str(self.stage)) + b"/bad-\xff-name"
        self.stage.mkdir()
        try:
            open(raw, "wb").close()
        except OSError:
            self.skipTest("this file system refuses names that are not valid UTF-8")
        (entry,) = vm_scan.scan(str(self.stage))
        self.assertEqual(entry, {"k": "f", "h": b"bad-\xff-name".hex()})
        self.assertEqual(vm_repository.plan_skips([entry]).skipped[0][1], "unsafe_name")
        self.assertEqual(vm_scan.drop(str(self.stage), [{"h": entry["h"]}]), 1)
        self.assertEqual(os.listdir(self.stage), [])

    def test_drop_removes_only_what_is_named_and_never_leaves_the_folder_or_removes_a_folder(self):
        self.put(self.stage, {"keep.txt": b"1", "secret/.env": b"2", ".env": b"3"})
        outside = self.root / "outside.txt"
        outside.write_text("precious")
        removed = vm_scan.drop(str(self.stage), [{"p": ".env"}, {"p": "secret/.env"}, {"p": "missing"}, {"p": "../outside.txt"},
                                                  {"p": str(outside)}, {"p": "secret"}, {"p": "a//b"}, {"p": ""}])
        self.assertEqual(removed, 2)
        self.assertEqual(sorted(p.name for p in self.stage.rglob("*") if p.is_file()), ["keep.txt"])
        self.assertTrue(outside.exists() and (self.stage / "secret").is_dir())

    def test_the_script_runs_as_a_program_the_way_the_vm_runs_it(self):
        self.put(self.stage, {"a.txt": b"1", ".env": b"2"})
        listing, plan = self.root / "l.json", self.root / "p.json"
        done = subprocess.run([sys.executable, vm_scan.__file__, "list", str(self.stage), str(listing)], capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual([item["p"] for item in json.loads(listing.read_text())], [".env", "a.txt"])
        plan.write_text(json.dumps([{"p": ".env"}]))
        done = subprocess.run([sys.executable, vm_scan.__file__, "drop", str(self.stage), str(plan)], capture_output=True, text=True)
        self.assertEqual((done.returncode, os.listdir(self.stage)), (0, ["a.txt"]))
        self.assertEqual(subprocess.run([sys.executable, vm_scan.__file__, "nonsense"], capture_output=True).returncode, 2)


class PlanTests(unittest.TestCase):
    def test_each_kind_of_file_that_cannot_be_loaded_is_named_with_its_reason(self):
        listing = [{"k": "f", "p": path} for path in ("README.md", ".env.example", "src/a.py", ".env", ".env.local", "keys/server.pem",
                                                      ".git/config", ".ssh/config", "src/credentials/token.py", "a\\b", "C:x", "e\u0301.py")]
        listing += [{"k": "l", "p": "link"}, {"k": "x", "p": "pipe"}, {"k": "f", "h": b"\xff".hex()}]
        plan = vm_repository.plan_skips(listing)
        self.assertEqual(plan.kept, 3)
        self.assertEqual(dict(plan.skipped), {".env": "sensitive", ".env.local": "sensitive", "keys/server.pem": "sensitive", ".git/config": "sensitive",
            ".ssh/config": "sensitive", "src/credentials/token.py": "sensitive", "a\\b": "unsafe_name", "C:x": "unsafe_name", "e\u0301.py": "unsafe_name",
            "link": "symlink", "pipe": "special", "�": "unsafe_name"})
        self.assertEqual(len(plan.drop), len(plan.skipped))
        self.assertIn({"h": b"\xff".hex()}, plan.drop)

    def test_names_that_differ_only_by_letter_case_keep_the_first_and_drop_the_rest(self):
        plan = vm_repository.plan_skips([{"k": "f", "p": "A.txt"}, {"k": "f", "p": "a.txt"}, {"k": "f", "p": "b.txt"}])
        self.assertEqual((plan.kept, plan.skipped, plan.drop), (2, [("a.txt", "case_clash")], [{"p": "a.txt"}]))

    def test_there_is_no_limit_on_the_number_or_size_of_files(self):
        plan = vm_repository.plan_skips([{"k": "f", "p": f"src/f{n}.py"} for n in range(50_000)])
        self.assertEqual((plan.kept, plan.skipped), (50_000, []))

    def test_a_listing_that_is_not_what_the_script_writes_is_refused(self):
        for bad in ({}, "x", [1], [{"k": "z", "p": "a"}], [{"k": "f"}], [{"k": "f", "p": 3}], [{"k": "f", "h": "zz"}], [{"k": "f", "h": 5}]):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                vm_repository.plan_skips(bad)


class ConfigTests(unittest.TestCase):
    def test_the_address_goes_into_a_config_file_and_only_a_plain_https_address_is_accepted(self):
        self.assertEqual(vm_repository.curl_config(URL), f'url = "{URL}"\n')
        for bad in ("http://codeload.github.com/x", "https://x/ y", 'https://x/"; rm -rf /', "https://x/\\y", "https://x/\nurl=evil", "ftp://x", "",
                    "https://x/" + "a" * 5000):
            with self.subTest(bad=bad[:30]), self.assertRaises(ValueError):
                vm_repository.curl_config(bad)

    def test_no_command_names_the_address(self):
        for script in (vm_repository.fetch_script(), vm_repository.merge_script(), vm_repository.cleanup_script()):
            self.assertNotIn("codeload", script)
            self.assertNotIn("token", script.lower().replace("trap", ""))


class ScriptTests(Folder):
    def merge(self, listing_files: dict[str, bytes], *, existing: dict[str, bytes] | None = None):
        self.put(self.stage, listing_files)
        self.put(self.workspace, existing or {})
        pathlib.Path(vm_repository.SCAN).write_text(vm_repository.scan_source())
        plan = vm_repository.plan_skips(vm_scan.scan(str(self.stage)))
        pathlib.Path(vm_repository.DROP).write_text(json.dumps(plan.drop))
        return plan, self.bash(vm_repository.merge_script())

    def tree(self):
        return sorted(str(p.relative_to(self.workspace)) for p in self.workspace.rglob("*") if p.is_file())

    def test_what_is_left_out_never_reaches_the_workspace_and_everything_else_does_including_dotfiles_and_big_files(self):
        plan, done = self.merge({"README.md": b"hi", ".env": b"S=1", ".env.example": b"S=", ".github/ci.yml": b"x", "keys/a.pem": b"k",
                                 "data/big.bin": b"\0" * 3_000_000, "src/app.py": b"1"})
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.tree(), [".env.example", ".github/ci.yml", "README.md", "data/big.bin", "src/app.py"])
        self.assertEqual(sorted(path for path, _ in plan.skipped), [".env", "keys/a.pem"])

    def test_every_temporary_file_is_removed(self):
        self.merge({"a.txt": b"1"})
        self.assertEqual([p for p in vm_repository.LEFTOVERS if os.path.exists(p)], [])

    def test_a_workspace_that_already_has_files_gets_the_repository_added_to_it(self):
        _, done = self.merge({"a.txt": b"1", ".hidden": b"2"}, existing={"mine.txt": b"keep me"})
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.tree(), [".hidden", "a.txt", "mine.txt"])

    def test_the_executable_bit_survives(self):
        self.put(self.stage, {"run.sh": b"#!/bin/sh\n"})
        os.chmod(self.stage / "run.sh", 0o755)
        pathlib.Path(vm_repository.SCAN).write_text(vm_repository.scan_source())
        pathlib.Path(vm_repository.DROP).write_text("[]")
        self.assertEqual(self.bash(vm_repository.merge_script()).returncode, 0)
        self.assertTrue(os.access(self.workspace / "run.sh", os.X_OK))

    def test_the_download_script_stops_at_the_first_failure_and_leaves_nothing_behind(self):
        pathlib.Path(vm_repository.SCAN).write_text(vm_repository.scan_source())
        pathlib.Path(vm_repository.CURL_CONFIG).write_text('url = "https://127.0.0.1:9/none"\n')
        done = self.bash(vm_repository.fetch_script().replace("--retry 3", "--retry 0"))
        self.assertNotEqual(done.returncode, 0)
        self.assertFalse(os.path.exists(vm_repository.CURL_CONFIG) or os.path.exists(vm_repository.ARCHIVE))


class ArchiveLinkTests(unittest.IsolatedAsyncioTestCase):
    def app(self, handler):
        return GitHubAppClient(app_settings(), token="ghu_x", transport=httpx.MockTransport(handler))

    async def test_an_app_link_is_the_redirect_github_gives_and_it_is_not_followed(self):
        seen = []

        def respond(request):
            seen.append(request)
            return httpx.Response(302, headers={"location": URL})
        location = await self.app(respond).archive_location("owner/repo", APP_SHA)
        self.assertEqual(location, URL)
        self.assertEqual([r.url.path for r in seen], [f"/repos/owner/repo/tarball/{APP_SHA}"])  # one request, to the API only
        self.assertEqual(seen[0].headers["authorization"], "Bearer ghu_x")

    async def test_only_an_https_address_on_the_download_host_is_accepted_so_the_token_and_the_vm_go_nowhere_else(self):
        for bad in ("http://codeload.github.com/o/r/x", "https://evil.example/o/r/x", "https://codeload.github.com.evil.example/x",
                    "https://user:pw@codeload.github.com/x", "https://codeload.github.com:8443/x", "/relative/path", "", "https://[bad/x",
                    "https://codeload.github.com/" + "a" * 5000):
            with self.subTest(bad=bad[:40]), self.assertRaisesRegex(GitHubError, "response_invalid"):
                await self.app(lambda request, bad=bad: httpx.Response(302, headers={"location": bad})).archive_location("owner/repo", APP_SHA)

    async def test_failures_keep_their_meaning(self):
        cases = [(404, {}, "access_denied"), (401, {}, "access_denied"), (403, {"x-ratelimit-remaining": "0"}, "rate_limited"),
                 (429, {}, "rate_limited"), (200, {}, "unavailable"), (500, {}, "unavailable")]
        for status, headers, code in cases:
            with self.subTest(status=status), self.assertRaisesRegex(GitHubError, code):
                await self.app(lambda request, s=status, h=headers: httpx.Response(s, headers=h)).archive_location("owner/repo", APP_SHA)

        def broken(request):
            raise httpx.ConnectError("down")
        with self.assertRaisesRegex(GitHubError, "unavailable"):
            await self.app(broken).archive_location("owner/repo", APP_SHA)

    async def test_a_person_without_access_gets_no_link(self):
        github = AuthorizedGitHubClient(None, app_settings(), "user")
        github.authorize = AsyncMock(side_effect=GitHubError("github_repository_not_authorized"))
        with self.assertRaisesRegex(GitHubError, "not_authorized"):
            await github.archive_link("owner/repo", APP_SHA)
        with self.assertRaisesRegex(GitHubError, "immutable_commit"):
            await github.archive_link("owner/repo", "main")

    async def test_the_link_of_an_authorized_repository_names_its_canonical_name_and_the_pinned_commit(self):
        github = AuthorizedGitHubClient(None, app_settings(), "user")
        client = NS(archive_location=AsyncMock(return_value=URL))
        github.authorize = AsyncMock(return_value=(client, {"id": 1, "full_name": "Owner/Repo"}, {"id": 1}))
        link = await github.archive_link("owner/repo", APP_SHA)
        self.assertEqual((link.url, link.repo_id, link.commit_sha), (URL, "Owner/Repo", APP_SHA))
        client.archive_location.assert_awaited_once_with("Owner/Repo", APP_SHA)
        self.assertNotIn("SECRETTOKEN", repr(link))

    async def test_a_public_link_is_the_open_download_address_after_the_usual_checks(self):
        public = GitHubClient()
        public.resolve = AsyncMock(return_value=NS(repo_id="owner/repo", commit_sha=SHA))
        link = await public.archive_link("owner/repo", SHA)
        self.assertEqual(link.url, f"https://codeload.github.com/owner/repo/tar.gz/{SHA}")
        public.resolve = AsyncMock(return_value=NS(repo_id="owner/repo", commit_sha="b" * 40))
        with self.assertRaisesRegex(GitHubError, "commit_mismatch"):
            await public.archive_link("owner/repo", SHA)
        with self.assertRaisesRegex(GitHubError, "immutable_commit"):
            await public.archive_link("owner/repo", "HEAD")


def listing(*items):
    return json.dumps([{"k": kind, "p": path} for kind, path in items]).encode()


class LoadTests(unittest.IsolatedAsyncioTestCase):
    """`ConversationSandbox` against a fake VM: what it sends, in which order, and when it falls back to the upload."""

    def env(self, *, answer=None, files=(("f", "README.md"), ("f", "src/a.py")), link=None, snapshot=None, **kwargs):
        github = NS(archive_link=link or AsyncMock(return_value=ArchiveLink(URL, "owner/repo", SHA)),
                    snapshot=snapshot or AsyncMock(return_value=NS(commit_sha=SHA, files={"README.md": b"hi"}, skipped=())))

        def respond(command):
            return answer(command) if answer else None
        env = Env(respond, repository=REPO, github=github, **kwargs)
        env.sandbox.stored[vm_repository.LIST] = listing(*files)
        env.github = github
        return env

    def commands(self, env):
        return [shlex.split(command)[-1] if command.startswith("bash -c ") else command for command, _ in env.sandbox.ran]

    async def test_the_vm_downloads_the_repository_and_nothing_is_uploaded(self):
        env = self.env()
        await env.conversation.workspace(env.task_id)
        env.github.snapshot.assert_not_awaited()
        self.assertNotIn("/tmp/agentforge-repo.tgz", env.sandbox.written)
        self.assertEqual(env.sandbox.written[vm_repository.CURL_CONFIG], f'url = "{URL}"\n')
        self.assertIn("def scan(", env.sandbox.written[vm_repository.SCAN])
        scripts = self.commands(env)
        self.assertTrue(any("curl --fail" in s for s in scripts))
        final = scripts[-1]
        self.assertIn("scan.py drop", final)
        self.assertIn(f"owner/repo@{SHA}", final)
        self.assertIn(MARKER, final)
        self.assertIn(f"git tag -f {BASE_TAG}", final)

    async def test_the_signed_address_never_appears_in_a_command(self):
        env = self.env()
        await env.conversation.workspace(env.task_id)
        self.assertFalse(any("SECRETTOKEN" in command or "codeload" in command for command, _ in env.sandbox.ran))

    async def test_what_the_plan_leaves_out_is_sent_to_the_vm_and_told_to_the_agent(self):
        env = self.env(files=(("f", "README.md"), ("f", ".env"), ("l", "link"), ("f", "keys/a.pem")))
        await env.conversation.workspace(env.task_id)
        self.assertEqual(sorted(item["p"] for item in json.loads(env.sandbox.written[vm_repository.DROP])), [".env", "keys/a.pem", "link"])
        notice = env.conversation.take_notice()
        for text in (".env", "keys/a.pem", "link", "may hold secrets", "symbolic link"):
            self.assertIn(text, notice)

    async def test_no_notice_when_nothing_was_left_out(self):
        env = self.env()
        await env.conversation.workspace(env.task_id)
        self.assertIsNone(env.conversation.take_notice())

    async def test_a_failed_download_falls_back_to_the_upload_after_cleaning_up(self):
        env = self.env(answer=lambda command: (22, "", "curl: (22) The requested URL returned error: 404") if "curl --fail" in command else None)
        with self.assertLogs("agentforge.tools.sandbox_shell", "WARNING") as logs:
            await env.conversation.workspace(env.task_id)
        env.github.snapshot.assert_awaited_once_with("owner/repo", SHA)
        self.assertIn("/tmp/agentforge-repo.tgz", env.sandbox.written)
        scripts = self.commands(env)
        cleanup = next(i for i, s in enumerate(scripts) if s.startswith("rm -rf"))
        self.assertLess(cleanup, next(i for i, s in enumerate(scripts) if "tar -xzf /tmp/agentforge-repo.tgz" in s))
        self.assertNotIn("SECRETTOKEN", "\n".join(logs.output))

    async def test_each_way_the_download_can_go_wrong_falls_back_instead_of_failing(self):
        cases = {
            "no link": dict(link=AsyncMock(side_effect=GitHubError("github_access_denied"))),
            "unusable link": dict(link=AsyncMock(return_value=ArchiveLink("http://insecure/x", "owner/repo", SHA))),
            "timed out": dict(answer=lambda command: (124, "", "") if "curl --fail" in command else None),
            "listing is not json": dict(),
            "merge failed": dict(answer=lambda command: (1, "", "") if "scan.py drop" in command else None),
        }
        for name, kwargs in cases.items():
            with self.subTest(name):
                env = self.env(**kwargs)
                if name == "listing is not json":
                    env.sandbox.stored[vm_repository.LIST] = b"not json"
                with self.assertLogs("agentforge.tools.sandbox_shell", "WARNING"):
                    await env.conversation.workspace(env.task_id)
                env.github.snapshot.assert_awaited_once()
                self.assertIn("/tmp/agentforge-repo.tgz", env.sandbox.written)

    async def test_a_vm_that_stops_answering_is_not_retried_through_the_upload(self):
        def answer(command):
            if "curl --fail" in command:
                raise RuntimeError("connection lost")
        env = self.env(answer=answer)
        with self.assertRaises(SandboxUnavailable):
            await env.conversation.workspace(env.task_id)
        env.github.snapshot.assert_not_awaited()

    async def test_a_repository_with_nothing_loadable_is_empty_not_a_fallback(self):
        env = self.env(files=(("f", ".env"), ("l", "link")))
        with self.assertRaisesRegex(GitHubError, "repository_empty"):
            await env.conversation.workspace(env.task_id)
        env.github.snapshot.assert_not_awaited()
        self.assertTrue(any(s.startswith("rm -rf") for s in self.commands(env)))

    async def test_the_switch_and_a_github_without_links_use_the_upload_as_before(self):
        env = self.env()
        env.conversation.archive_load = False
        await env.conversation.workspace(env.task_id)
        env.github.archive_link.assert_not_awaited()
        env.github.snapshot.assert_awaited_once()
        reader = NS(snapshot=AsyncMock(return_value=NS(commit_sha=SHA, files={"a": b"1"}, skipped=())))
        plain = Env(repository=REPO, github=reader)
        await plain.conversation.workspace(plain.task_id)
        reader.snapshot.assert_awaited_once()

    async def test_a_repository_that_was_loaded_before_is_not_downloaded_again(self):
        env = self.env(answer=lambda command: (0, f"owner/repo@{SHA}", "") if f"cat {MARKER}" in command else None)
        env.conversation._lookup = lambda task_id: (env.session_id, "sbx-1", REPO)
        env.provider.open = AsyncMock(return_value=(env.workspace, False))
        await env.conversation.workspace(env.task_id)
        env.github.archive_link.assert_not_awaited()
        env.github.snapshot.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
