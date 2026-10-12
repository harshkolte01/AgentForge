"""The persistent coding sandbox, with a fake SDK injected at its boundary: no provider calls, no real VM.

Covers the provider (create or resume one VM per conversation, no secrets inside, internet on), the workspace (commands,
files, redaction, bounded output, keep-alive) and the pure workspace helpers that still guard repository uploads.
"""
import asyncio
import gzip
import hashlib
import io
import json
import shlex
import tarfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

from agentforge.integrations.e2b_sandbox import (
    COMMAND_ENV, GRACE_SEC, JSON_BYTES, OPEN_ATTEMPTS, OUTPUT_CHARS, WORKSPACE, SandboxConfig, SandboxProvider, SandboxUnavailable, Workspace,
    bound_output, redact_output,
)
from agentforge.sandbox.workspace import (build_diff, import_archive, read_text, validate_workspace, workspace_revision, write_text)


class WorkspaceHelperTests(unittest.TestCase):
    def test_revision_order_independent_and_binary_preserved(self):
        left = {"b.png": b"\x00\xff", "a.py": b"hello"}
        self.assertEqual(workspace_revision(left), workspace_revision(dict(reversed(list(left.items())))))
        self.assertNotEqual(workspace_revision(left), workspace_revision({**left, "a.py": b"bye"}))
        self.assertEqual(validate_workspace(left)["b.png"], b"\x00\xff")
        with self.assertRaisesRegex(ValueError, "binary"):
            read_text(left, "b.png")

    def test_sensitive_traversal_and_collisions(self):
        for path in ("../x", "/x", "x//y", "x/./y", "x\\y", ".git/config", ".env",
                     "nested/.env.local", ".ssh/id_rsa", "x\ny", "C:x", "é.py"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                validate_workspace({path: b"x"})
        for files in ({"X": b"", "x": b""}, {"x": b"", "X/y": b""}):
            with self.assertRaises(ValueError):
                validate_workspace(files)
        validate_workspace({".env.example": b"EXAMPLE="})

    def test_cas_and_binary_edits(self):
        before = {"x.py": b"old"}
        with self.assertRaisesRegex(ValueError, "conflict"):
            write_text(before, "x.py", "new", None)
        after = write_text(before, "x.py", "new", hashlib.sha256(b"old").hexdigest())
        self.assertEqual(after, {"x.py": b"new"})
        self.assertEqual(before, {"x.py": b"old"})
        self.assertEqual(write_text({}, "new.py", "new", None), {"new.py": b"new"})
        with self.assertRaisesRegex(ValueError, "conflict"):
            write_text({}, "new.py", "new", "0" * 64)

    def test_archive_rejects_links_and_duplicate_paths(self):
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE):
            archive = io.BytesIO()
            with tarfile.open(fileobj=archive, mode="w") as tar:
                entry = tarfile.TarInfo("bad")
                entry.type, entry.linkname = kind, "/etc/passwd"
                tar.addfile(entry)
            with self.assertRaises(ValueError):
                import_archive(archive.getvalue())
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w") as tar:
            for _ in range(2):
                entry = tarfile.TarInfo("x.py")
                entry.size = 1
                tar.addfile(entry, io.BytesIO(b"x"))
        with self.assertRaisesRegex(ValueError, "duplicate"):
            import_archive(archive.getvalue())

    def test_archive_binary_roundtrip_and_size(self):
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w:gz") as tar:
            entry = tarfile.TarInfo("img.png")
            entry.size = 2
            tar.addfile(entry, io.BytesIO(b"\x00\xff"))
        self.assertEqual(import_archive(archive.getvalue()), {"img.png": b"\x00\xff"})
        with self.assertRaises(ValueError):
            validate_workspace({"huge": b"x" * 1_000_001})
        with self.assertRaisesRegex(ValueError, "expansion"):
            import_archive(gzip.compress(b"\0" * 12_000_001))

    def test_template_compatible_node_distribution_and_npm_paths(self):
        directory = Path(__file__).resolve().parents[3] / "infra/e2b"
        installer = (directory / "install_node.sh").read_text()
        self.assertIn("node_version=22.23.3", installer)
        self.assertIn("sha256sum --check --status", installer)
        self.assertIn("df450af89261115ef9f9e3830c3eeb2cc9213b63c720b1af623cb5dcbe2e02de", installer)
        self.assertIn("a44aeb94849a299b22df10b9e622ec2f605c2183501bc40590705131de7c740f", installer)
        self.assertIn("ln -s /usr/local/bin/node /usr/bin/node", installer)
        self.assertIn("ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/bin/npm", installer)
        recipe = (directory / "template.py").read_text()
        self.assertIn('.from_python_image("3.12-slim-bookworm")', recipe)
        self.assertIn('.copy("install_node.sh"', recipe)
        self.assertNotIn("nodejs npm", recipe)

    def test_diff_no_newline_is_valid_patch_serialization(self):
        self.assertEqual(build_diff({"x.py": b"old"}, {"x.py": b"new"}),
            "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-old\n\\ No newline at end of file\n+new\n\\ No newline at end of file\n")
        self.assertEqual(build_diff({}, {"x.py": b"new"}),
            "--- /dev/null\n+++ b/x.py\n@@ -0,0 +1 @@\n+new\n\\ No newline at end of file\n")
        self.assertEqual(build_diff({}, {"empty.py": b""}),
            'diff --git "a/empty.py" "b/empty.py"\nnew file mode 100644\nindex 0000000..e69de29\n')
        self.assertIn("deleted file mode 100644", build_diff({"empty.py": b""}, {}))


class OutputTests(unittest.TestCase):
    def test_credentials_are_removed_and_ordinary_text_is_not(self):
        redacted = redact_output("api_key=abc password: xyz\nAuthorization: Bearer token\nmy-real-key", secrets=("my-real-key",))
        for secret in ("abc", "xyz", "token", "my-real-key"):
            self.assertNotIn(secret, redacted)
        text = "Ran 12 tests in 0.31s\nOK"
        self.assertEqual(redact_output(text), text)

    def test_provider_tokens_and_private_keys_are_removed(self):
        text = ("pushing with ghp_abcdefghijklmnopqrstuvwxyz0123 and github_pat_11ABCDEF_xyz and sk-abcdefghijklmnopqrstuv\n"
                "-----BEGIN RSA PRIVATE KEY-----\nMIIEow\n-----END RSA PRIVATE KEY-----\nAKIAABCDEFGHIJKLMNOP")
        cleaned = redact_output(text)
        for secret in ("ghp_abcdef", "github_pat_11", "sk-abcdef", "MIIEow", "AKIAABCDEF"):
            self.assertNotIn(secret, cleaned)

    def test_short_output_is_kept_whole(self):
        self.assertEqual(bound_output("hello"), ("hello", False))

    def test_long_output_keeps_the_start_and_the_end_where_the_error_usually_is(self):
        text = "START" + "m" * 50_000 + "THE-ERROR"
        shown, cut = bound_output(text)
        self.assertTrue(cut)
        self.assertTrue(shown.startswith("START"))
        self.assertTrue(shown.endswith("THE-ERROR"))
        self.assertIn("characters omitted", shown)
        self.assertLessEqual(len(shown), OUTPUT_CHARS + 100)

    def test_escaping_heavy_output_still_fits_the_json_budget(self):
        shown, cut = bound_output('"\\\n' * 20_000)
        self.assertTrue(cut)
        self.assertLessEqual(len(json.dumps(shown, ensure_ascii=False).encode()), JSON_BYTES)


class FakeSandbox:
    """The slice of E2B's AsyncSandbox the workspace uses. `responder(command)` returns (exit_code, stdout, stderr) or raises."""

    def __init__(self, responder=None, sandbox_id="sbx-1"):
        self.sandbox_id, self.responder = sandbox_id, responder or (lambda command: (0, "", ""))
        self.ran, self.written, self.timeouts, self.killed = [], {}, [], 0
        self.stored: dict[str, str | bytes] = {}
        self.commands = NS(run=self._run)
        self.files = NS(read=self._read, write=self._write)

    async def _run(self, command, **kwargs):
        from e2b import CommandExitException
        self.ran.append((command, kwargs))
        code, out, err = self.responder(command)
        if out and kwargs.get("on_stdout"):
            kwargs["on_stdout"](out)
        if err and kwargs.get("on_stderr"):
            kwargs["on_stderr"](err)
        if code:
            raise CommandExitException(stderr=err, stdout=out, exit_code=code, error=None)
        return NS(exit_code=0, stdout=out, stderr=err)

    async def _read(self, path, format="text", **kwargs):
        if path not in self.stored:
            raise FileNotFoundError(path)
        value = self.stored[path]
        return value.encode() if format == "bytes" and isinstance(value, str) else value

    async def _write(self, path, data, **kwargs):
        self.written[path] = data
        self.stored[path] = data

    async def set_timeout(self, seconds, **kwargs):
        self.timeouts.append(seconds)

    async def kill(self, **kwargs):
        self.killed += 1


class FakeSDK:
    """Stands in for `AsyncSandbox`: `create` and `connect` return the prepared sandbox or raise the prepared error."""

    def __init__(self, sandbox=None, *, create_error=None, connect_error=None):
        self.sandbox, self.create_error, self.connect_error = sandbox or FakeSandbox(), create_error, connect_error
        self.created, self.connected = [], []

    async def create(self, **kwargs):
        self.created.append(kwargs)
        if self.create_error:
            raise self.create_error
        return self.sandbox

    async def connect(self, sandbox_id, **kwargs):
        self.connected.append((sandbox_id, kwargs))
        if self.connect_error:
            raise self.connect_error
        return self.sandbox


def config(**changes):
    return SandboxConfig(api_key="fixture-api-key", template="tpl-1", ttl_sec=900, request_timeout_sec=20, **changes)


class ProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_without_an_api_key_nothing_is_attempted(self):
        sdk = FakeSDK()
        with self.assertRaises(SandboxUnavailable) as caught:
            await SandboxProvider(SandboxConfig(), factory=sdk).open(None, metadata={})
        self.assertEqual(caught.exception.code, "SANDBOX_NOT_CONFIGURED")
        self.assertEqual((sdk.created, sdk.connected), ([], []))

    async def test_a_new_sandbox_has_internet_no_secrets_and_pauses_instead_of_dying(self):
        sdk = FakeSDK()
        workspace, created = await SandboxProvider(config(), factory=sdk).open(None, metadata={"agentforge_task": "t1"})
        self.assertTrue(created)
        request, = sdk.created
        self.assertEqual(request["envs"], {})  # nothing from the host environment, no GitHub token
        self.assertTrue(request["allow_internet_access"])
        self.assertTrue(request["secure"])
        self.assertEqual(request["lifecycle"], {"on_timeout": "pause", "auto_resume": True})
        self.assertEqual((request["template"], request["timeout"], request["metadata"]), ("tpl-1", 900, {"agentforge_task": "t1"}))
        self.assertNotIn("github", json.dumps({k: v for k, v in request.items() if k != "api_key"}).lower())
        self.assertEqual(workspace.sandbox_id, "sbx-1")
        self.assertIn(f"mkdir -p {WORKSPACE}", workspace.sandbox.ran[0][0])  # the workspace folder exists from the start

    async def test_an_existing_sandbox_is_resumed_not_recreated(self):
        sdk = FakeSDK()
        workspace, created = await SandboxProvider(config(), factory=sdk).open("sbx-old", metadata={})
        self.assertFalse(created)
        self.assertEqual(sdk.created, [])
        self.assertEqual(sdk.connected[0][0], "sbx-old")
        self.assertEqual(sdk.connected[0][1]["timeout"], 900)

    async def test_a_sandbox_that_no_longer_exists_is_replaced(self):
        from e2b import NotFoundException
        sdk = FakeSDK(connect_error=NotFoundException("gone"))
        workspace, created = await SandboxProvider(config(), factory=sdk).open("sbx-old", metadata={})
        self.assertTrue(created)
        self.assertEqual(len(sdk.created), 1)

    async def test_any_other_connect_failure_is_retried_reported_and_never_replaces_the_sandbox(self):
        sdk = FakeSDK(connect_error=OSError("private diagnostic"))
        with patch("agentforge.integrations.e2b_sandbox.OPEN_RETRY_SEC", 0), self.assertRaises(SandboxUnavailable) as caught:
            await SandboxProvider(config(), factory=sdk).open("sbx-old", metadata={})
        self.assertEqual(caught.exception.code, "SANDBOX_CONNECT_FAILED")
        self.assertNotIn("private", str(caught.exception))
        self.assertEqual(len(sdk.connected), OPEN_ATTEMPTS)
        self.assertEqual(sdk.created, [])  # the person's files are not abandoned for a temporary error

    async def test_attaching_resumes_the_conversations_sandbox_and_never_creates_one(self):
        from e2b import NotFoundException
        sdk = FakeSDK()
        workspace = await SandboxProvider(config(), factory=sdk).attach("sbx-old")
        self.assertEqual((workspace.sandbox_id, sdk.connected[0][0], sdk.created), ("sbx-1", "sbx-old", []))
        # No sandbox yet, or one that is gone: nothing to look at, and nothing is started (or paid for) just to look.
        self.assertIsNone(await SandboxProvider(config(), factory=sdk).attach(None))
        gone = FakeSDK(connect_error=NotFoundException("gone"))
        self.assertIsNone(await SandboxProvider(config(), factory=gone).attach("sbx-old"))
        self.assertEqual(gone.created, [])

    async def test_attaching_reports_other_failures_safely_and_without_a_key_does_nothing(self):
        sdk = FakeSDK(connect_error=OSError("private diagnostic"))
        with patch("agentforge.integrations.e2b_sandbox.OPEN_RETRY_SEC", 0), self.assertRaises(SandboxUnavailable) as caught:
            await SandboxProvider(config(), factory=sdk).attach("sbx-old")
        self.assertEqual(caught.exception.code, "SANDBOX_CONNECT_FAILED")
        self.assertNotIn("private", str(caught.exception))
        self.assertEqual((len(sdk.connected), sdk.created), (OPEN_ATTEMPTS, []))
        keyless = FakeSDK()
        with self.assertRaises(SandboxUnavailable) as none:
            await SandboxProvider(SandboxConfig(), factory=keyless).attach("sbx-old")
        self.assertEqual((none.exception.code, keyless.connected), ("SANDBOX_NOT_CONFIGURED", []))

    async def test_a_replaced_sandbox_is_marked_as_recreated(self):
        from e2b import NotFoundException
        workspace, created = await SandboxProvider(config(), factory=FakeSDK(connect_error=NotFoundException("gone"))).open(
            "sbx-old", metadata={})
        self.assertTrue(created and workspace.recreated)
        fresh, _ = await SandboxProvider(config(), factory=FakeSDK()).open(None, metadata={})
        self.assertFalse(fresh.recreated)

    async def test_a_failed_creation_is_reported_safely(self):
        sdk = FakeSDK(create_error=OSError("private allocation detail"))
        with self.assertRaises(SandboxUnavailable) as caught:
            await SandboxProvider(config(), factory=sdk).open(None, metadata={})
        self.assertEqual(caught.exception.code, "SANDBOX_CREATE_FAILED")
        self.assertNotIn("private", str(caught.exception))
        self.assertEqual(len(sdk.created), 1)  # allocation is never retried behind the person's back

    async def test_a_refused_creation_is_retried_because_nothing_was_allocated(self):
        from e2b import ServiceBusyException
        sdk = FakeSDK(create_error=ServiceBusyException())
        with patch("agentforge.integrations.e2b_sandbox.CREATE_RETRY_SEC", 0), self.assertRaises(SandboxUnavailable) as caught:
            await SandboxProvider(config(), factory=sdk).open(None, metadata={})
        self.assertEqual(caught.exception.code, "SANDBOX_BUSY")
        self.assertEqual(len(sdk.created), 3)

    async def test_a_rejected_api_key_is_named_and_not_retried(self):
        from e2b import AuthenticationException
        created = FakeSDK(create_error=AuthenticationException("bad key"))
        with self.assertRaises(SandboxUnavailable) as caught:
            await SandboxProvider(config(), factory=created).open(None, metadata={})
        self.assertEqual((caught.exception.code, len(created.created)), ("SANDBOX_AUTH_FAILED", 1))
        connected = FakeSDK(connect_error=AuthenticationException("bad key"))
        with self.assertRaises(SandboxUnavailable) as again:
            await SandboxProvider(config(), factory=connected).open("sbx-old", metadata={})
        self.assertEqual((again.exception.code, len(connected.connected), connected.created), ("SANDBOX_AUTH_FAILED", 1, []))

    async def test_a_new_sandbox_that_cannot_run_its_first_command_is_stopped_not_leaked(self):
        sdk = FakeSDK(FakeSandbox(lambda command: (_ for _ in ()).throw(OSError("down"))))
        with patch("agentforge.integrations.e2b_sandbox.OPEN_RETRY_SEC", 0), self.assertRaises(SandboxUnavailable):
            await SandboxProvider(config(), factory=sdk).open(None, metadata={})
        self.assertEqual(sdk.sandbox.killed, 1)

    async def test_kill_stops_the_vm_and_swallows_errors(self):
        sdk = FakeSDK()
        provider = SandboxProvider(config(), factory=sdk)
        workspace, _ = await provider.open(None, metadata={})
        await provider.kill(workspace)
        self.assertEqual(sdk.sandbox.killed, 1)
        async def broken(**kwargs):
            raise OSError("already gone")
        workspace.sandbox.kill = broken
        await provider.kill(workspace)


class WorkspaceTests(unittest.IsolatedAsyncioTestCase):
    def workspace(self, responder=None, clock=None):
        sandbox = FakeSandbox(responder)
        kwargs = {"clock": clock} if clock else {}
        return Workspace(sandbox, config(), **kwargs), sandbox

    async def test_a_command_runs_in_bash_in_the_workspace_without_secrets_or_a_terminal(self):
        workspace, sandbox = self.workspace(lambda command: (0, "hello\n", ""))
        outcome = await workspace.run("echo 'hi there' && ls")
        command, kwargs = sandbox.ran[0]
        inner = shlex.split(shlex.split(command)[2])
        # bash -c "… && exec timeout -k 5 120 bash -c <the command, untouched>"
        self.assertEqual(shlex.split(command)[:2], ["bash", "-c"])
        self.assertIn("exec timeout -k 5 120 bash -c", shlex.split(command)[2])
        self.assertEqual(inner[-1], "echo 'hi there' && ls")
        self.assertEqual((kwargs["cwd"], kwargs["envs"], kwargs["timeout"]), (WORKSPACE, COMMAND_ENV, 120 + GRACE_SEC))
        self.assertTrue(all(not any(word in key.upper() for word in ("KEY", "TOKEN", "SECRET")) for key in kwargs["envs"]))
        self.assertEqual((outcome.exit_code, outcome.output, outcome.timed_out, outcome.truncated), (0, "hello\n", False, False))

    async def test_a_command_its_own_deadline_ended_is_reported_as_timed_out_with_its_output(self):
        outcome = await self.workspace(lambda command: (124, "step 1\n", ""))[0].run("make", timeout_sec=5)
        self.assertEqual((outcome.exit_code, outcome.timed_out), (None, True))
        self.assertIn("step 1", outcome.output)
        self.assertIn("stopped after 5 seconds", outcome.output)

    async def test_a_lost_connection_or_a_stop_ends_the_commands_process_group_in_the_vm(self):
        from e2b import TimeoutException
        def slow(command):
            if "kill -TERM" in command:
                return 0, "", ""
            raise TimeoutException("deadline")
        workspace, sandbox = self.workspace(slow)
        await workspace.run("sleep 999", timeout_sec=1)
        self.assertIn("kill -TERM -- -$p", sandbox.ran[-1][0])

    async def test_a_failing_command_is_a_normal_outcome_with_its_output(self):
        workspace, _ = self.workspace(lambda command: (2, "partial\n", "boom: no such file"))
        outcome = await workspace.run("cat missing")
        self.assertEqual(outcome.exit_code, 2)
        self.assertEqual(outcome.output, "partial\nboom: no such file")

    async def test_a_command_that_runs_too_long_is_reported_as_timed_out(self):
        from e2b import TimeoutException
        def slow(command):
            raise TimeoutException("deadline")
        outcome = await self.workspace(slow)[0].run("sleep 999", timeout_sec=1)
        self.assertEqual((outcome.exit_code, outcome.timed_out), (None, True))

    async def test_a_vm_that_is_gone_is_not_reported_as_a_command_that_ran_too_long(self):
        from e2b import SandboxNotRunningException, SandboxUnreachableException
        for failure in (SandboxNotRunningException("paused"), SandboxUnreachableException("down")):
            def gone(command, failure=failure):
                raise failure
            with self.subTest(failure=type(failure).__name__), self.assertRaises(SandboxUnavailable) as caught:
                await self.workspace(gone)[0].run("make")
            self.assertEqual(caught.exception.code, "SANDBOX_COMMAND_FAILED")

    async def test_a_large_upload_gets_a_longer_request_timeout_than_a_small_call(self):
        calls = []
        workspace, sandbox = self.workspace()
        original = sandbox.files.write
        async def spy(path, data, **kwargs):
            calls.append(kwargs["request_timeout"])
            await original(path, data, **kwargs)
        sandbox.files = NS(read=sandbox.files.read, write=spy)
        await workspace.write("a.txt", "x")
        await workspace.write("big.tgz", b"x", timeout_sec=180)
        await workspace.write("short.txt", "x", timeout_sec=1)
        self.assertEqual(calls, [20, 180, 20])  # never shorter than the configured timeout

    async def test_a_broken_sandbox_is_an_error_that_does_not_leak_its_cause(self):
        def broken(command):
            raise OSError("private connection detail")
        with self.assertRaises(SandboxUnavailable) as caught:
            await self.workspace(broken)[0].run("ls")
        self.assertEqual(caught.exception.code, "SANDBOX_COMMAND_FAILED")
        self.assertNotIn("private", str(caught.exception))

    async def test_a_stop_request_is_not_turned_into_a_sandbox_error(self):
        def stopped(command):
            raise asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.workspace(stopped)[0].run("ls")

    async def test_output_is_redacted_and_bounded_before_the_model_sees_it(self):
        secret = "ghp_abcdefghijklmnopqrstuvwxyz0123"
        workspace, _ = self.workspace(lambda command: (0, f"token={secret}\nfixture-api-key\n" + "x" * 40_000 + "\nTHE END", ""))
        outcome = await workspace.run("env")
        self.assertNotIn(secret, outcome.output)
        self.assertNotIn("fixture-api-key", outcome.output)
        self.assertTrue(outcome.truncated)
        self.assertTrue(outcome.output.endswith("THE END"))

    async def test_very_long_output_is_cut_to_its_real_end_not_wherever_collection_stopped(self):
        from agentforge.integrations import e2b_sandbox
        with patch.object(e2b_sandbox, "COLLECT_CHARS", 30_000), patch.object(e2b_sandbox, "TAIL_CHARS", 5_000):
            workspace, _ = self.workspace(lambda command: (1, "start\n" + "x" * 80_000 + "\nlast line", "y" * 50_000 + "\nERROR: the real failure"))
            outcome = await workspace.run("make")
            self.assertTrue(outcome.truncated)
            self.assertTrue(outcome.output.startswith("start\n"))
            self.assertTrue(outcome.output.endswith("ERROR: the real failure"))
            # Each stream keeps its own head and its own real end.
            collector = e2b_sandbox._Collector(tail=True)
            for _ in range(4_000):
                collector.add("abcdefghij")  # 40 000 characters in small pieces
            collector.add("THE END")
            text = collector.text()
            self.assertTrue(text.startswith("abcdefghij" * 3) and text.endswith("abcdefghijTHE END"))
            self.assertIn("[… 5007 characters omitted …]", text)
            self.assertEqual(len(text.split("\n")[-1]), 5_000)
            # Output that only just passes the head cap loses nothing.
            exact = e2b_sandbox._Collector(tail=True)
            exact.add("a" * 30_000 + "tail")
            self.assertEqual(exact.text(), "a" * 30_000 + "tail")

    async def test_capture_keeps_only_the_head_of_output_past_the_cap(self):
        from agentforge.integrations import e2b_sandbox
        with patch.object(e2b_sandbox, "COLLECT_CHARS", 1_000):
            workspace, _ = self.workspace(lambda command: (0, "z" * 3_000, ""))
            self.assertEqual(await workspace.capture("git diff"), (0, "z" * 1_000))

    async def test_capture_returns_exact_output_for_the_backends_own_use(self):
        secret = "ghp_abcdefghijklmnopqrstuvwxyz0123"
        workspace, sandbox = self.workspace(lambda command: (0, secret + "y" * 30_000, ""))
        code, out = await workspace.capture("git diff")
        self.assertEqual((code, out), (0, secret + "y" * 30_000))
        failing = self.workspace(lambda command: (128, "fatal", ""))[0]
        self.assertEqual(await failing.capture("git diff"), (128, "fatal"))

    async def test_files_are_read_and_written_under_the_workspace(self):
        workspace, sandbox = self.workspace()
        await workspace.write("src/a.py", "print(1)\n")
        await workspace.write("/tmp/outside.tgz", b"\x1f\x8b")
        self.assertEqual(list(sandbox.written), [f"{WORKSPACE}/src/a.py", "/tmp/outside.tgz"])
        self.assertEqual(await workspace.read_text("src/a.py"), "print(1)\n")
        sandbox.stored[f"{WORKSPACE}/bin.dat"] = b"\x00\xff"
        self.assertEqual(await workspace.read_bytes("bin.dat"), b"\x00\xff")
        with self.assertRaises(FileNotFoundError):
            await workspace.read_text("missing.txt")

    async def test_the_idle_timer_is_pushed_out_at_most_once_a_minute(self):
        now = [1000.0]
        workspace, sandbox = self.workspace(clock=lambda: now[0])
        await workspace.keep_alive()
        self.assertEqual(sandbox.timeouts, [])
        now[0] += 61
        await workspace.keep_alive()
        await workspace.keep_alive()
        self.assertEqual(sandbox.timeouts, [900])
        now[0] += 61
        await workspace.keep_alive()
        self.assertEqual(sandbox.timeouts, [900, 900])

    async def test_a_failure_to_extend_the_lifetime_never_fails_the_call(self):
        now = [0.0]
        workspace, sandbox = self.workspace(clock=lambda: now[0])
        async def broken(seconds, **kwargs):
            raise OSError("down")
        sandbox.set_timeout = broken
        now[0] = 500
        await workspace.keep_alive()


if __name__ == "__main__":
    unittest.main()
