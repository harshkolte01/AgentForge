"""The coding tools (bash, read, write, edit, grep, find, ls, create_pull_request) against a fake sandbox.

No VM and no GitHub: the SDK is a fake at the sandbox boundary and the GitHub client is a recorder. What matters here is
what each tool sends to the sandbox, what it returns to the model, which typed errors it gives, and that credentials
never reach the sandbox.
"""
import io
import shlex
import tarfile
from types import SimpleNamespace as NS
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock
from uuid import uuid4

from agentforge.integrations.e2b_sandbox import GRACE_SEC, WORKSPACE, SandboxUnavailable, Workspace, SandboxConfig
from agentforge.integrations.github_client import GitHubError
from agentforge.integrations.github_pr import MAX_CHANGED_FILES, open_draft_pull_request
from agentforge.tools.catalog import EFFECT_TOOLS, ToolCatalog
from agentforge.tools.contracts import (
    BashArgs, FileEditArgs, FileReadArgs, FileWriteArgs, FindArgs, GrepArgs, LsArgs, PullRequestArgs,
)
from agentforge.tools.errors import ToolError
from agentforge.tools.sandbox_shell import BASE_TAG, MARKER, ConversationSandbox, parse_raw_diff, register_sandbox_shell

from test_sandbox_provider import FakeSandbox

SHA = "a" * 40
REPO = {"repo_id": "owner/repo", "ref": SHA}


def path_of(command: str) -> str:
    """The path a `test -e <path>` command asks about."""
    return shlex.split(command)[-1]


def unwrap(command: str) -> str:
    """`bash -c <command>` as the tool wrote it, without the deadline wrapper the workspace puts around every command."""
    parts = shlex.split(command)
    if len(parts) == 3 and "exec timeout" in parts[2]:
        return "bash -c " + shlex.quote(shlex.split(parts[2])[-1])
    return command


class Env:
    """A catalog of the coding tools wired to a FakeSandbox whose commands are answered by `answer(command)`."""

    def __init__(self, answer=None, *, repository=None, pull_requests=None, github=None):
        self.task_id = uuid4()
        self.sandbox = FakeSandbox(self.respond)
        recorded = self.sandbox.commands.run
        async def run(command, **kwargs):
            return await recorded(unwrap(command), **kwargs)
        self.sandbox.commands = NS(run=run)
        self.answers = answer or (lambda command: None)
        self.workspace = Workspace(self.sandbox, SandboxConfig(api_key="key"))
        self.provider = NS(open=self.open, kill=self.kill)
        self.opened, self.killed, self.remembered = 0, 0, []
        self.repository = repository
        self.conversation = ConversationSandbox(None, self.provider, github)
        self.conversation._lookup = lambda task_id: (uuid4(), None, self.repository)
        self.conversation._remember = lambda session_id, sandbox_id: self.remembered.append(sandbox_id)
        self.conversation.repository = lambda task_id: self.repository
        self.catalog = ToolCatalog()
        register_sandbox_shell(self.catalog, self.conversation, pull_requests=pull_requests)
        self.execution = NS(task=NS(task_id=self.task_id), operation_key="op", authorize_current=None)

    def respond(self, command):
        scripted = self.answers(command)
        if scripted is not None:
            return scripted
        if command.startswith("bash -c 'test -e "):
            return (0 if f"{WORKSPACE}/{path_of(shlex.split(command)[-1])}" in self.sandbox.stored else 1, "", "")
        return (0, "", "")

    async def open(self, existing_id, metadata):
        self.opened += 1
        return self.workspace, True

    async def kill(self, workspace):
        self.killed += 1

    def call(self, name, args):
        return self.catalog.get(name).handler(args, self.execution)

    def put(self, path, content):
        self.sandbox.stored[f"{WORKSPACE}/{path}"] = content


class RegistrationTests(TestCase):
    def test_the_seven_workspace_tools_are_registered_without_a_pull_request_tool_when_github_cannot_write(self):
        env = Env()
        for name in ("bash", "read", "write", "edit", "grep", "find", "ls"):
            self.assertEqual(env.catalog.get(name).max_cost_usd, 0)
        with self.assertRaises(Exception):
            env.catalog.get("create_pull_request")

    def test_the_pull_request_tool_exists_only_with_a_writer(self):
        self.assertEqual(Env(pull_requests=NS()).catalog.get("create_pull_request").name, "create_pull_request")

    def test_everything_that_changes_something_is_an_effect_and_reads_are_not(self):
        self.assertTrue({"bash", "write", "edit", "create_pull_request"} <= EFFECT_TOOLS)
        self.assertFalse({"read", "grep", "find", "ls"} & EFFECT_TOOLS)


class BashTests(IsolatedAsyncioTestCase):
    async def test_the_command_runs_and_its_exit_code_and_output_come_back(self):
        env = Env(lambda command: (3, "tests failed\n", "AssertionError") if "pytest" in command else None)
        outcome = await env.call("bash", BashArgs(command="pytest -q", timeout_sec=300))
        self.assertEqual(outcome.status, "ok")  # the call worked even though the tests did not
        self.assertEqual((outcome.data["exit_code"], outcome.data["timed_out"]), (3, False))
        self.assertIn("tests failed", outcome.data["output"])
        self.assertIn("AssertionError", outcome.data["output"])
        self.assertEqual(env.sandbox.ran[-1][1]["timeout"], 300 + GRACE_SEC)  # the command's own deadline ends it first

    async def test_the_sandbox_is_opened_once_for_many_calls(self):
        env = Env()
        for _ in range(3):
            await env.call("bash", BashArgs(command="true"))
        self.assertEqual(env.opened, 1)

    async def test_a_sandbox_that_cannot_start_is_a_typed_error_the_model_can_report(self):
        env = Env()
        async def unavailable(existing_id, metadata):
            raise SandboxUnavailable("SANDBOX_CREATE_FAILED")
        env.provider.open = unavailable
        with self.assertRaises(ToolError) as caught:
            await env.call("bash", BashArgs(command="ls"))
        self.assertEqual(caught.exception.code, "SANDBOX_CREATE_FAILED")
        self.assertIn("sandbox", caught.exception.detail)

    async def test_an_unexpected_start_failure_never_leaks_its_text(self):
        env = Env()
        async def exploded(existing_id, metadata):
            raise RuntimeError("secret internal detail")
        env.provider.open = exploded
        with self.assertLogs("agentforge.tools.sandbox_shell", "WARNING"), self.assertRaises(ToolError) as caught:
            await env.call("bash", BashArgs(command="ls"))
        self.assertEqual(caught.exception.code, "SANDBOX_UNAVAILABLE")
        self.assertNotIn("secret", caught.exception.detail)


class RecoveryTests(IsolatedAsyncioTestCase):
    async def test_a_connection_lost_during_a_command_is_a_result_the_agent_can_act_on_and_the_next_call_reconnects(self):
        def flaky(command):
            if "flaky" in command:
                raise OSError("connection reset")
            return None
        env = Env(flaky)
        with self.assertRaises(ToolError) as caught:
            await env.call("bash", BashArgs(command="flaky"))
        self.assertEqual(caught.exception.code, "SANDBOX_DISCONNECTED")
        self.assertIn("may or may not have run", caught.exception.detail)
        await env.call("bash", BashArgs(command="true"))
        self.assertEqual(env.opened, 2)

    async def test_a_write_the_sandbox_did_not_confirm_says_so(self):
        env = Env()
        async def broken(path, data, **kwargs):
            raise OSError("reset")
        env.sandbox.files = NS(read=env.sandbox.files.read, write=broken)
        with self.assertRaises(ToolError) as caught:
            await env.call("write", FileWriteArgs(path="a.txt", content="x"))
        self.assertEqual(caught.exception.code, "SANDBOX_DISCONNECTED")

    async def test_the_agent_is_told_once_when_its_earlier_sandbox_was_replaced(self):
        env = Env()
        env.workspace.recreated = True
        first = await env.call("bash", BashArgs(command="ls"))
        second = await env.call("bash", BashArgs(command="ls"))
        self.assertIn("earlier sandbox had expired", first.data["notice"])
        self.assertNotIn("notice", second.data)

    def test_every_tool_deadline_leaves_room_to_start_the_sandbox_and_load_the_repository(self):
        env = Env(pull_requests=FakeGitHub())
        for name in ("bash", "read", "write", "edit", "grep", "find", "ls", "create_pull_request"):
            self.assertGreater(env.catalog.get(name).timeout_sec, 600, name)


class ReadTests(IsolatedAsyncioTestCase):
    async def test_lines_are_numbered_from_the_requested_offset(self):
        env = Env()
        env.put("app.py", "one\ntwo\nthree\nfour\n")
        outcome = await env.call("read", FileReadArgs(path="app.py", offset=2, limit=2))
        self.assertEqual(outcome.data["content"], "2\ttwo\n3\tthree")
        self.assertEqual((outcome.data["total_lines"], outcome.data["next_offset"]), (4, 4))

    async def test_the_last_page_has_no_next_offset(self):
        env = Env()
        env.put("app.py", "one\ntwo\n")
        outcome = await env.call("read", FileReadArgs(path="app.py"))
        self.assertEqual((outcome.data["content"], outcome.data["next_offset"]), ("1\tone\n2\ttwo", None))

    async def test_a_long_file_is_paged_by_size_and_every_line_is_reachable(self):
        env = Env()
        env.put("big.txt", "".join(f"{'x' * 100} line {number}\n" for number in range(1, 301)))
        seen, offset = [], 1
        while offset is not None:
            outcome = await env.call("read", FileReadArgs(path="big.txt", offset=offset, limit=2000))
            self.assertLessEqual(len(outcome.data["content"]), 12500)
            seen += [row.split("\t", 1)[0] for row in outcome.data["content"].splitlines()]
            offset = outcome.data["next_offset"]
        self.assertEqual(seen, [str(number) for number in range(1, 301)])

    async def test_a_missing_file_is_a_recoverable_error(self):
        with self.assertRaises(ToolError) as caught:
            await Env().call("read", FileReadArgs(path="nope.py"))
        self.assertEqual(caught.exception.code, "FILE_NOT_FOUND")
        self.assertIn("ls or find", caught.exception.detail)

    async def test_an_offset_past_the_end_is_an_invalid_range(self):
        env = Env()
        env.put("app.py", "one\ntwo\n")
        with self.assertRaises(ToolError) as caught:
            await env.call("read", FileReadArgs(path="app.py", offset=9))
        self.assertEqual(caught.exception.code, "INVALID_RANGE")
        self.assertIn("2 lines", caught.exception.detail)


class WriteTests(IsolatedAsyncioTestCase):
    async def test_a_new_file_is_created_and_reported_as_created(self):
        env = Env()
        outcome = await env.call("write", FileWriteArgs(path="src/new.py", content="héllo\n"))
        self.assertEqual(env.sandbox.written[f"{WORKSPACE}/src/new.py"], "héllo\n")
        self.assertEqual((outcome.data["created"], outcome.data["bytes"]), (True, len("héllo\n".encode())))

    async def test_an_existing_file_is_replaced_whole_and_reported_as_not_created(self):
        env = Env()
        env.put("a.txt", "old")
        outcome = await env.call("write", FileWriteArgs(path="a.txt", content="new"))
        self.assertEqual(env.sandbox.written[f"{WORKSPACE}/a.txt"], "new")
        self.assertFalse(outcome.data["created"])

    async def test_a_path_with_shell_characters_is_quoted_for_the_existence_check(self):
        env = Env()
        await env.call("write", FileWriteArgs(path="a b; rm -rf ~.txt", content="x"))
        check = next(command for command, _ in env.sandbox.ran if "test -e" in command)
        self.assertEqual(shlex.split(shlex.split(check)[-1])[-1], "a b; rm -rf ~.txt")


class EditTests(IsolatedAsyncioTestCase):
    def env(self, content):
        env = Env()
        env.put("app.py", content)
        return env

    async def test_exactly_one_match_is_replaced(self):
        env = self.env("def f():\n    return 1\n\ndef g():\n    return 2\n")
        outcome = await env.call("edit", FileEditArgs(path="app.py", old_string="return 1", new_string="return 10"))
        self.assertEqual(outcome.data["replacements"], 1)
        self.assertEqual(env.sandbox.written[f"{WORKSPACE}/app.py"], "def f():\n    return 10\n\ndef g():\n    return 2\n")

    async def test_an_ambiguous_match_changes_nothing_and_says_how_many_places(self):
        env = self.env("x = 1\nx = 1\n")
        with self.assertRaises(ToolError) as caught:
            await env.call("edit", FileEditArgs(path="app.py", old_string="x = 1", new_string="x = 2"))
        self.assertEqual(caught.exception.code, "EDIT_AMBIGUOUS")
        self.assertIn("2 places", caught.exception.detail)
        self.assertEqual(env.sandbox.written, {})

    async def test_replace_all_changes_every_match(self):
        env = self.env("x = 1\nx = 1\ny = 2\n")
        outcome = await env.call("edit", FileEditArgs(path="app.py", old_string="x = 1", new_string="x = 3", replace_all=True))
        self.assertEqual(outcome.data["replacements"], 2)
        self.assertEqual(env.sandbox.written[f"{WORKSPACE}/app.py"], "x = 3\nx = 3\ny = 2\n")

    async def test_text_that_is_not_there_changes_nothing(self):
        env = self.env("x = 1\n")
        with self.assertRaises(ToolError) as caught:
            await env.call("edit", FileEditArgs(path="app.py", old_string="z = 9", new_string="z = 8"))
        self.assertEqual(caught.exception.code, "EDIT_NO_MATCH")
        self.assertEqual(env.sandbox.written, {})

    async def test_a_missing_file_is_a_typed_error(self):
        with self.assertRaises(ToolError) as caught:
            await Env().call("edit", FileEditArgs(path="nope.py", old_string="a", new_string="b"))
        self.assertEqual(caught.exception.code, "FILE_NOT_FOUND")

    async def test_the_replacement_is_literal_and_may_be_empty(self):
        env = self.env("keep \\1 and drop-me\n")
        await env.call("edit", FileEditArgs(path="app.py", old_string=" and drop-me", new_string=""))
        self.assertEqual(env.sandbox.written[f"{WORKSPACE}/app.py"], "keep \\1\n")


class SearchToolTests(IsolatedAsyncioTestCase):
    async def test_grep_quotes_the_pattern_skips_noise_and_caps_results(self):
        env = Env(lambda command: (0, "src/a.py:3:TODO fix\nsrc/b.py:9:TODO later\n", "") if "grep" in command else None)
        outcome = await env.call("grep", GrepArgs(pattern="TODO; rm -rf /", path="src", glob="*.py", ignore_case=True, max_results=50))
        command = shlex.split(env.sandbox.ran[-1][0])[-1]
        self.assertIn("grep -rnIEi", command)
        self.assertIn("--include='*.py'", command)
        self.assertIn("-e 'TODO; rm -rf /'", command)  # one quoted argument, never run as a shell command
        self.assertIn("--exclude-dir=node_modules", command)
        self.assertIn("--exclude-dir=.git", command)
        self.assertTrue(command.endswith("head -n 50"))
        self.assertEqual(outcome.data["count"], 2)
        self.assertIn("src/a.py:3:TODO fix", outcome.data["matches"])

    async def test_grep_with_no_match_is_an_empty_result_not_an_error(self):
        env = Env(lambda command: (1, "", "") if "grep" in command else None)
        outcome = await env.call("grep", GrepArgs(pattern="zzz"))
        self.assertEqual((outcome.status, outcome.data["count"], outcome.data["matches"]), ("ok", 0, ""))

    async def test_find_matches_names_outside_noise_folders(self):
        env = Env(lambda command: (0, "./a/test_x.py\n./b/test_y.py\n", "") if "find" in command else None)
        outcome = await env.call("find", FindArgs(pattern="test_*.py", max_results=20))
        command = shlex.split(env.sandbox.ran[-1][0])[-1]
        self.assertIn("-name 'test_*.py'", command)
        self.assertIn("-not -path '*/node_modules/*'", command)
        self.assertTrue(command.endswith("head -n 20"))
        self.assertIn("./a/test_x.py", outcome.data["paths"])

    async def test_ls_lists_a_folder_and_names_a_missing_one(self):
        env = Env(lambda command: (0, "-rw-r--r-- 1 user user 5 Jan 1 a.txt\n", "") if "ls -lAh" in command else None)
        outcome = await env.call("ls", LsArgs(path="."))
        self.assertIn("a.txt", outcome.data["listing"])
        missing = Env(lambda command: (2, "ls: cannot access 'nope': No such file or directory\n", "") if "ls -lAh" in command else None)
        with self.assertRaises(ToolError) as caught:
            await missing.call("ls", LsArgs(path="nope"))
        self.assertEqual(caught.exception.code, "FILE_NOT_FOUND")

    async def test_a_broken_sandbox_during_a_search_is_a_typed_error(self):
        env = Env()
        def broken(command):
            raise OSError("boom")
        env.sandbox.responder = broken
        with self.assertRaises(ToolError) as caught:
            await env.call("grep", GrepArgs(pattern="x"))
        self.assertEqual(caught.exception.code, "SANDBOX_COMMAND_FAILED")


def tarball_files(data: bytes) -> dict[str, bytes]:
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        return {member.name: archive.extractfile(member).read() for member in archive.getmembers()}


class ConversationSandboxTests(IsolatedAsyncioTestCase):
    def snapshot(self, files=None):
        calls = []
        async def get(repo_id, ref):
            calls.append((repo_id, ref))
            return NS(commit_sha=ref, files=files if files is not None else {"README.md": b"hi", "src/a.py": b"print(1)\n"})
        return NS(snapshot=get, calls=calls)

    async def test_the_selected_repository_is_uploaded_and_becomes_a_tagged_git_checkout(self):
        github = self.snapshot()
        env = Env(repository=REPO, github=github)
        await env.conversation.workspace(env.task_id)
        self.assertEqual(github.calls, [("owner/repo", SHA)])
        uploaded = env.sandbox.written["/tmp/agentforge-repo.tgz"]
        self.assertEqual(tarball_files(uploaded), {"README.md": b"hi", "src/a.py": b"print(1)\n"})
        script = shlex.split(env.sandbox.ran[-1][0])[-1]
        self.assertIn(f"tar -xzf /tmp/agentforge-repo.tgz -C {WORKSPACE}", script)
        self.assertIn(f"owner/repo@{SHA}", script)
        self.assertIn(MARKER, script)
        self.assertIn("git init", script)
        self.assertIn(f"git tag -f {BASE_TAG}", script)

    async def test_the_new_sandbox_is_remembered_for_the_conversation(self):
        env = Env()
        await env.conversation.workspace(env.task_id)
        self.assertEqual(env.remembered, ["sbx-1"])

    async def test_nothing_is_uploaded_when_no_repository_is_selected(self):
        github = self.snapshot()
        env = Env(github=github)
        await env.conversation.workspace(env.task_id)
        self.assertEqual(github.calls, [])
        self.assertNotIn("/tmp/agentforge-repo.tgz", env.sandbox.written)

    async def test_the_workspace_is_opened_once_per_run(self):
        env = Env(repository=REPO, github=self.snapshot())
        first = await env.conversation.workspace(env.task_id)
        second = await env.conversation.workspace(env.task_id)
        self.assertIs(first, second)
        self.assertEqual(env.opened, 1)

    async def test_a_failed_checkout_is_reported(self):
        env = Env(lambda command: (1, "", "") if "tar -xzf" in command else None, repository=REPO, github=self.snapshot())
        with self.assertRaises(SandboxUnavailable) as caught:
            await env.conversation.workspace(env.task_id)
        self.assertEqual(caught.exception.code, "REPOSITORY_LOAD_FAILED")

    async def test_a_repository_cannot_be_loaded_without_a_github_reader(self):
        env = Env(repository=REPO, github=None)
        with self.assertRaises(SandboxUnavailable) as caught:
            await env.conversation.workspace(env.task_id)
        self.assertEqual(caught.exception.code, "REPOSITORY_UNAVAILABLE")

    async def test_a_resumed_sandbox_that_already_holds_the_repository_is_not_loaded_again(self):
        github = self.snapshot()
        env = Env(lambda command: (0, f"owner/repo@{SHA}", "") if f"cat {MARKER}" in command else None, repository=REPO, github=github)
        async def resumed(existing_id, metadata):
            return env.workspace, False
        env.provider.open = resumed
        await env.conversation.workspace(env.task_id)
        self.assertEqual(github.calls, [])

    async def test_switching_repositories_starts_a_fresh_sandbox_so_files_never_mix(self):
        github = self.snapshot()
        env = Env(lambda command: (0, f"other/project@{'b' * 40}", "") if f"cat {MARKER}" in command else None, repository=REPO, github=github)
        opens = []
        async def open_(existing_id, metadata):
            opens.append(existing_id)
            return env.workspace, len(opens) > 1
        env.provider.open = open_
        await env.conversation.workspace(env.task_id)
        self.assertEqual(opens, [None, None])
        self.assertEqual(env.killed, 1)
        self.assertEqual(github.calls, [("owner/repo", SHA)])


RAW = ":100644 100644 aaa bbb M\0src/a.py\0:000000 100644 000 ccc A\0new.txt\0:100644 000000 ddd 000 D\0gone.txt\0:160000 160000 eee fff M\0vendor/sub\0"


class RawDiffTests(TestCase):
    def test_changes_are_read_with_their_status_and_new_mode(self):
        self.assertEqual(parse_raw_diff(RAW), {"src/a.py": ("M", "100644"), "new.txt": ("A", "100644"),
                                               "gone.txt": ("D", "000000"), "vendor/sub": ("M", "160000")})

    def test_empty_and_odd_output_give_no_changes(self):
        self.assertEqual(parse_raw_diff(""), {})
        self.assertEqual(parse_raw_diff("\0"), {})


class FakeGitHub:
    """Records every call; answers the way GitHub's git data API does."""

    def __init__(self, *, pull=None):
        self.calls, self.pull = [], pull
        self.authorized = []
        self.client = NS(request=self.request)

    async def authorize(self, repo_id, **kwargs):
        self.authorized.append((repo_id, kwargs))
        return self.client, {"full_name": repo_id, "default_branch": "main"}, {"id": 7}

    async def request(self, method, path, payload=None, allow_missing=False):
        self.calls.append((method, path, payload))
        if "/branches/" in path:
            return {"commit": {"sha": SHA}}
        if "/git/ref/heads/" in path:
            return None
        if path.endswith(f"/git/commits/{SHA}"):
            return {"tree": {"sha": "1" * 40}}
        if path.endswith("/git/blobs"):
            return {"sha": f"{len(self.calls):040x}"}
        if path.endswith("/git/trees"):
            return {"sha": "2" * 40}
        if path.endswith("/git/commits"):
            return {"sha": "3" * 40}
        if path.endswith("/git/refs"):
            return {}
        if path.endswith("/pulls"):
            return self.pull or {"number": 5, "html_url": f"https://github.com/{path.split('/')[2]}/{path.split('/')[3]}/pull/5"}
        raise AssertionError(path)


class DraftPullRequestTests(IsolatedAsyncioTestCase):
    async def open(self, github=None, changes=None, **kwargs):
        github = github or FakeGitHub()
        result = await open_draft_pull_request(github, "owner/repo", base_sha=SHA, branch="agentforge/task-x",
            changes=changes if changes is not None else {"a.py": ("100644", b"x")}, title="Fix it", body="Why", **kwargs)
        return result, github

    async def test_blobs_tree_commit_branch_and_a_draft_pull_request_are_created_in_that_order(self):
        result, github = await self.open(changes={"a.py": ("100644", b"x"), "gone.py": None})
        self.assertEqual([(method, path.split("/git/")[-1].split("/repos/")[-1]) for method, path, _ in github.calls],
            [("GET", "owner/repo/branches/main"), ("GET", "ref/heads/agentforge/task-x"), ("GET", f"commits/{SHA}"), ("POST", "blobs"), ("POST", "trees"), ("POST", "commits"), ("POST", "refs"),
             ("POST", "owner/repo/pulls")])
        tree = github.calls[4][2]
        self.assertEqual(tree["base_tree"], "1" * 40)
        self.assertEqual([(entry["path"], entry["sha"] is None) for entry in tree["tree"]], [("a.py", False), ("gone.py", True)])
        self.assertEqual(github.calls[5][2]["parents"], [SHA])
        pull = github.calls[-1][2]
        self.assertEqual((pull["draft"], pull["head"], pull["base"], pull["maintainer_can_modify"]), (True, "agentforge/task-x", "main", False))
        self.assertIn("Opened by AgentForge as a draft.", pull["body"])
        self.assertEqual((result["pr_number"], result["pr_url"], result["commit_sha"]), (5, "https://github.com/owner/repo/pull/5", "3" * 40))

    async def test_only_the_backend_authorizes_for_writing(self):
        _, github = await self.open()
        self.assertEqual(github.authorized, [("owner/repo", {"write": True})])

    async def test_nothing_to_commit_and_oversized_changes_are_refused_before_any_call(self):
        github = FakeGitHub()
        with self.assertRaisesRegex(GitHubError, "github_nothing_to_commit"):
            await self.open(github, changes={})
        with self.assertRaisesRegex(GitHubError, "github_change_too_large"):
            await self.open(github, changes={f"f{index}": ("100644", b"x") for index in range(MAX_CHANGED_FILES + 1)})
        with self.assertRaisesRegex(GitHubError, "github_change_too_large"):
            await self.open(github, changes={"huge.bin": ("100644", b"x" * (20 * 1024 * 1024 + 1))})
        self.assertEqual((github.authorized, github.calls), ([], []))

    async def test_a_pull_request_that_does_not_match_what_was_asked_is_not_trusted(self):
        for pull in ({"number": 5, "html_url": "https://evil.example/owner/repo/pull/5"}, {"number": "5", "html_url": "x"},
                     {"number": 5, "html_url": "https://github.com/owner/repo/pull/6"}):
            with self.subTest(pull=pull), self.assertRaisesRegex(GitHubError, "confirmation_failed"):
                await self.open(FakeGitHub(pull=pull))


class PullRequestToolTests(IsolatedAsyncioTestCase):
    def env(self, raw=RAW, **kwargs):
        github = FakeGitHub()
        def answer(command):
            return (0, raw, "") if "git diff --cached --raw" in command else None
        reader = NS(snapshot=AsyncMock(return_value=NS(commit_sha=SHA, files={"README.md": b"hi"})))
        env = Env(answer, repository=REPO, pull_requests=github, github=reader, **kwargs)
        env.put(MARKER, f"owner/repo@{SHA}")
        for path in ("src/a.py", "new.txt"):
            env.put(path, f"content of {path}".encode())
        return env, github

    async def test_the_changes_since_the_pinned_commit_become_a_draft_pull_request(self):
        env, github = self.env(":100644 100644 aaa bbb M\0src/a.py\0:000000 100644 000 ccc A\0new.txt\0:100644 000000 ddd 000 D\0gone.txt\0")
        outcome = await env.call("create_pull_request", PullRequestArgs(title="Fix the bug", body="Because"))
        self.assertEqual((outcome.data["pr_url"], outcome.data["pr_number"], outcome.data["files"]), ("https://github.com/owner/repo/pull/5", 5, 3))
        self.assertEqual(outcome.data["branch"], f"agentforge/task-{env.task_id.hex}")
        diff_command = next(shlex.split(command)[-1] for command, _ in env.sandbox.ran if "git diff --cached" in command)
        self.assertTrue(diff_command.endswith(BASE_TAG))
        entries = {entry["path"]: entry for entry in next(p for m, path, p in github.calls if path.endswith("/git/trees"))["tree"]}
        self.assertIsNone(entries["gone.txt"]["sha"])  # a deleted file stays deleted
        self.assertEqual(github.authorized[0][1], {"write": True})
        self.assertNotIn("token", str(env.sandbox.ran).lower())  # nothing credential-like was sent into the sandbox

    async def test_the_approval_preview_lists_every_changed_file_and_flags_ci_configuration(self):
        stat = " .github/workflows/ci.yml | 4 ++--\n src/a.py | 2 +-\n 2 files changed, 3 insertions(+), 3 deletions(-)\n"
        env, _ = self.env()
        env.answers = lambda command: (0, stat, "") if "--stat" in command else None
        preview = await env.catalog.get("create_pull_request").preview(PullRequestArgs(title="t"), env.execution)
        self.assertIn("src/a.py", preview)
        self.assertIn(".github/workflows/ci.yml", preview)
        self.assertIn("CI configuration", preview)
        self.assertIn("git reset -q", env.sandbox.ran[-1][0])  # nothing stays staged by looking

    async def test_without_a_selected_repository_there_is_nothing_to_open_against(self):
        env, _ = self.env()
        env.repository = None
        with self.assertRaises(ToolError) as caught:
            await env.call("create_pull_request", PullRequestArgs(title="t"))
        self.assertEqual(caught.exception.code, "NO_REPOSITORY")

    async def test_an_unchanged_workspace_has_nothing_to_commit(self):
        env, github = self.env(raw="")
        with self.assertRaises(ToolError) as caught:
            await env.call("create_pull_request", PullRequestArgs(title="t"))
        self.assertEqual(caught.exception.code, "NOTHING_TO_COMMIT")
        self.assertEqual(github.calls, [])

    async def test_submodule_changes_are_refused_with_a_clear_reason(self):
        env, _ = self.env(raw=":160000 160000 eee fff M\0vendor/sub\0")
        with self.assertRaises(ToolError) as caught:
            await env.call("create_pull_request", PullRequestArgs(title="t"))
        self.assertEqual(caught.exception.code, "UNSUPPORTED_CHANGE")
        self.assertIn("vendor/sub", caught.exception.detail)

    async def test_a_workspace_that_is_not_a_checkout_cannot_be_compared(self):
        reader = NS(snapshot=AsyncMock(return_value=NS(commit_sha=SHA, files={"README.md": b"hi"})))
        env = Env(lambda command: (128, "", "") if "git diff --cached" in command else None, repository=REPO,
                  pull_requests=FakeGitHub(), github=reader)
        env.put(MARKER, f"owner/repo@{SHA}")
        with self.assertRaises(ToolError) as caught:
            await env.call("create_pull_request", PullRequestArgs(title="t"))
        self.assertEqual(caught.exception.code, "NO_REPOSITORY")

    async def test_a_github_failure_never_claims_an_uncertain_write_did_not_happen(self):
        env, github = self.env(raw=":100644 100644 aaa bbb M\0src/a.py\0")
        async def refuse(*args, **kwargs):
            raise GitHubError("github_forbidden")
        github.client = NS(request=refuse)
        with self.assertRaises(ToolError) as caught:
            await env.call("create_pull_request", PullRequestArgs(title="t"))
        self.assertEqual(caught.exception.code, "GITHUB_WRITE_FAILED")
        self.assertIn("may already exist", caught.exception.detail)
        self.assertNotIn("Nothing was opened", caught.exception.detail)

class PublicationGuardTests(IsolatedAsyncioTestCase):
    async def publish(self, github, **kwargs):
        return await open_draft_pull_request(github, 'owner/repo', base_sha=SHA,
            changes=kwargs.pop('changes', {'note.txt':('100644',b'hello')}),
            title='Add note',body='',branch='agentforge/task-test',**kwargs)

    async def test_selected_nondefault_branch_is_preserved(self):
        github=FakeGitHub()
        result=await self.publish(github,base_ref='release/test')
        self.assertEqual(result['base_branch'],'release/test')
        self.assertIn('/branches/release%2Ftest',github.calls[0][1])
        self.assertEqual(github.calls[-1][2]['base'],'release/test')

    async def test_moved_branch_is_refused_before_writing(self):
        github=FakeGitHub(); original=github.request
        async def request(method,path,**kwargs):
            if '/branches/' in path:
                return {'commit':{'sha':'b'*40}}
            return await original(method,path,**kwargs)
        github.client=NS(request=request)
        with self.assertRaisesRegex(GitHubError,'github_base_branch_changed'):
            await self.publish(github)
        self.assertFalse(any(method=='POST' for method,_,_ in github.calls))

    async def test_existing_publication_branch_is_not_overwritten_or_duplicated(self):
        github=FakeGitHub(); original=github.request
        async def request(method,path,**kwargs):
            if '/git/ref/heads/' in path:return {'object':{'sha':'c'*40}}
            return await original(method,path,**kwargs)
        github.client=NS(request=request)
        with self.assertRaisesRegex(GitHubError,'github_publish_branch_exists'):
            await self.publish(github)
        self.assertFalse(any(method=='POST' for method,_,_ in github.calls))

    async def test_sensitive_paths_and_symlinks_are_not_published(self):
        for path,mode in [('.env','100644'),('../secret.txt','100644'),('.agentforge/repository','100644'),('link.txt','120000')]:
            github=FakeGitHub()
            with self.subTest(path=path),self.assertRaises(GitHubError):
                await self.publish(github,changes={path:(mode,b'secret')})
            self.assertEqual(github.calls,[])

    async def test_old_workspace_base_is_used_even_when_current_turn_pin_is_newer(self):
        env,github=PullRequestToolTests().env(raw=':100644 100644 aaa bbb M\0src/a.py\0')
        env.repository={**REPO,'ref':'b'*40}
        await env.call('create_pull_request',PullRequestArgs(title='Fix'))
        self.assertTrue(any(path.endswith('/git/commits/'+SHA) for _,path,_ in github.calls))
