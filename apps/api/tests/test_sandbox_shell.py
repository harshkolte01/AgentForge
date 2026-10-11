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
from agentforge.delivery.service import DeliveryError
from agentforge.tools.catalog import EFFECT_TOOLS, ToolCatalog
from agentforge.tools.contracts import (
    BashArgs, FileEditArgs, FileReadArgs, FileWriteArgs, FindArgs, GrepArgs, LsArgs, PullRequestArgs,
)
from agentforge.tools.errors import ToolError
from agentforge.tools.sandbox_shell import BASE_TAG, MARKER, ConversationSandbox, register_sandbox_shell

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

    def __init__(self, answer=None, *, repository=None, delivery=None, github=None):
        self.task_id, self.session_id, self.user_id = uuid4(), uuid4(), uuid4()
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
        self.conversation.owner = lambda task_id: (self.session_id, self.user_id)
        register_sandbox_shell(self.catalog, self.conversation, delivery=delivery)
        self.execution = NS(task=NS(task_id=self.task_id), operation_key="op", authorize_current=None, output=None)

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
        self.assertEqual(Env(delivery=NS()).catalog.get("create_pull_request").name, "create_pull_request")

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
        env = Env(delivery=NS())
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

    async def test_only_a_file_that_is_not_there_is_reported_missing(self):
        from e2b import FileNotFoundException, InvalidArgumentException, TimeoutException
        for failure, code in ((FileNotFoundException("no such file"), "FILE_NOT_FOUND"),
                              (InvalidArgumentException("path is a directory"), "FILE_NOT_FOUND"),
                              (IsADirectoryError("src"), "FILE_NOT_FOUND"),
                              (TimeoutException("deadline"), "SANDBOX_UNAVAILABLE"),
                              (RuntimeError("502: sandbox unavailable"), "SANDBOX_UNAVAILABLE")):
            for tool, args in (("read", FileReadArgs(path="app.py")), ("edit", FileEditArgs(path="app.py", old_string="a", new_string="b"))):
                env = Env()
                await env.call("ls", LsArgs(path="."))  # the sandbox is open
                env.put("app.py", "a\n")
                env.sandbox.files.read = AsyncMock(side_effect=failure)
                with self.subTest(failure=type(failure).__name__, tool=tool), self.assertRaises(ToolError) as caught:
                    await env.call(tool, args)
                self.assertEqual(caught.exception.code, code)
                self.assertEqual(env.sandbox.written, {})
                # A sandbox that did not answer is reconnected on the next call; a missing file says nothing about the sandbox.
                self.assertEqual(env.conversation._workspace is None, code == "SANDBOX_UNAVAILABLE")

    async def test_a_stop_while_reading_is_not_turned_into_an_error_result(self):
        import asyncio
        env = Env()
        env.sandbox.files.read = AsyncMock(side_effect=asyncio.CancelledError())
        with self.assertRaises(asyncio.CancelledError):
            await env.call("read", FileReadArgs(path="app.py"))

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


class FakeDelivery:
    """Stands in for the DeliveryService: records what the tool hands it, answers or fails as told."""

    def __init__(self, error=None):
        self.calls, self.error = [], error

    async def publish_for_agent(self, conversation_id, user_id, workspace, **kwargs):
        self.calls.append((conversation_id, user_id, workspace, kwargs))
        if self.error:
            raise self.error
        return {"pr_url": "https://github.com/owner/repo/pull/5", "pr_number": 5, "branch": "agentforge/fix-abc123", "head_sha": "3" * 40}


class PullRequestToolTests(IsolatedAsyncioTestCase):
    def env(self, delivery=None, **kwargs):
        delivery = delivery or FakeDelivery()
        reader = NS(snapshot=AsyncMock(return_value=NS(commit_sha=SHA, files={"README.md": b"hi"})))
        env = Env(repository=REPO, delivery=delivery, github=reader, **kwargs)
        env.put(MARKER, f"owner/repo@{SHA}")
        return env, delivery

    async def test_the_tool_publishes_through_the_delivery_with_this_operations_key_and_this_conversation(self):
        env, delivery = self.env()
        outcome = await env.call("create_pull_request", PullRequestArgs(title="Fix the bug", body="Because"))
        self.assertEqual((outcome.data["pr_url"], outcome.data["pr_number"], outcome.data["branch"]),
                         ("https://github.com/owner/repo/pull/5", 5, "agentforge/fix-abc123"))
        conversation_id, user_id, workspace, kwargs = delivery.calls[0]
        self.assertEqual((conversation_id, user_id), (env.session_id, env.user_id))
        self.assertIs(workspace, env.workspace)
        self.assertEqual((kwargs["key"], kwargs["title"], kwargs["body"], kwargs["task_id"]), ("op", "Fix the bug", "Because", env.task_id))
        self.assertNotIn("token", str(env.sandbox.ran).lower())  # nothing credential-like was sent into the sandbox

    async def test_the_approval_preview_lists_every_changed_file_and_flags_ci_configuration(self):
        env, _ = self.env()
        raw = ":100644 100644 " + "a" * 40 + " " + "b" * 40 + " M\0.github/workflows/ci.yml\0:100644 100644 " + "c" * 40 + " " + "d" * 40 + " M\0src/a.py\0"
        numstat = "4\t2\t.github/workflows/ci.yml\x002\t1\tsrc/a.py\x00"
        env.answers = lambda command: (0, f"@@AF:baseline:agentforge-base@@\n{raw}\n@@AF:numstat@@\n{numstat}\n@@AF:patch@@\n", "") if "@@AF:numstat@@" in command else None
        preview = await env.catalog.get("create_pull_request").preview(PullRequestArgs(title="t"), env.execution)
        self.assertIn("src/a.py", preview)
        self.assertIn(".github/workflows/ci.yml", preview)
        self.assertIn("CI configuration", preview)
        self.assertNotIn("git reset", env.sandbox.ran[-1][0])  # a private index is used: the agent's own staging is never touched

    async def test_without_a_selected_repository_there_is_nothing_to_open_against(self):
        env, delivery = self.env()
        env.repository = None
        with self.assertRaises(ToolError) as caught:
            await env.call("create_pull_request", PullRequestArgs(title="t"))
        self.assertEqual((caught.exception.code, delivery.calls), ("NO_REPOSITORY", []))

    async def test_each_delivery_refusal_reaches_the_model_as_a_typed_error_it_can_act_on(self):
        cases = {
            "nothing_to_publish": ("NOTHING_TO_COMMIT", ""),
            "delivery_blocked_paths:.env,link": ("UNSUPPORTED_CHANGE", ".env,link"),
            "github_path_conflict:src/a.py": ("GITHUB_CONFLICT", "src/a.py"),
            "delivery_no_repository": ("NO_REPOSITORY", ""),
            "github_branch_moved": ("GITHUB_BASE_CHANGED", ""),
            "github_branch_missing": ("GITHUB_BASE_CHANGED", ""),
            "delivery_outcome_unknown": ("GITHUB_WRITE_FAILED", "may already exist"),
        }
        for code, (expected, fragment) in cases.items():
            env, _ = self.env(FakeDelivery(DeliveryError(code)))
            with self.subTest(code=code), self.assertRaises(ToolError) as caught:
                await env.call("create_pull_request", PullRequestArgs(title="t"))
            self.assertEqual(caught.exception.code, expected)
            self.assertIn(fragment, caught.exception.detail)

    async def test_refusals_before_any_write_say_truthfully_that_nothing_was_published(self):
        from agentforge.tools.sandbox_shell import NOT_PUBLISHED
        for code in NOT_PUBLISHED:
            env, delivery = self.env(FakeDelivery(DeliveryError(code)))
            with self.subTest(code=code), self.assertRaises(ToolError) as caught:
                await env.call("create_pull_request", PullRequestArgs(title="t"))
            self.assertEqual(caught.exception.code, "GITHUB_UNAVAILABLE")
            self.assertIn("Nothing was published", caught.exception.detail)
            self.assertNotIn("may already exist", caught.exception.detail)

    async def test_an_uncertain_write_is_never_described_as_not_having_happened(self):
        env, _ = self.env(FakeDelivery(DeliveryError("delivery_outcome_unknown")))
        with self.assertRaises(ToolError) as caught:
            await env.call("create_pull_request", PullRequestArgs(title="t"))
        self.assertIn("do not retry blindly", caught.exception.detail)
        self.assertNotIn("Nothing was opened", caught.exception.detail)
