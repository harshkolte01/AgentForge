"""No provider calls or repository execution: exercise the injected SDK boundary."""

import asyncio
import ast
import gzip
import hashlib
import io
import json
import tarfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agentforge.integrations.e2b_client import E2BConfig, E2BRunner, redact_output
from agentforge.sandbox.contracts import SandboxUnavailable
from agentforge.sandbox.profiles import PROFILES
from agentforge.sandbox.workspace import (build_diff, import_archive, read_text,
    validate_workspace, workspace_revision, write_text)


class WorkspaceTests(unittest.TestCase):
    def test_revision_order_independent_and_binary_preserved(self):
        left = {"b.png": b"\x00\xff", "a.py": b"hello"}
        self.assertEqual(workspace_revision(left), workspace_revision(dict(reversed(list(left.items())))))
        self.assertNotEqual(workspace_revision(left), workspace_revision({**left, "a.py": b"bye"}))
        self.assertEqual(validate_workspace(left)["b.png"], b"\x00\xff")
        with self.assertRaisesRegex(ValueError, "binary"):
            read_text(left, "b.png")

    def test_sensitive_traversal_and_collisions(self):
        for path in ("../x", "/x", "x//y", "x/./y", "x\\y", ".git/config", ".env",
                     "nested/.env.local", ".ssh/id_rsa", "x\ny", "C:x", "e\u0301.py"):
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

    def test_template_profiles_match_without_executing_template_code(self):
        launcher = Path(__file__).resolve().parents[3] / "infra/e2b/run_profile.py"
        module = ast.parse(launcher.read_text())
        literal = next(statement.value for statement in module.body if isinstance(statement, ast.Assign)
                       and any(isinstance(target, ast.Name) and target.id == "PROFILES" for target in statement.targets))
        self.assertEqual(ast.literal_eval(literal), {name: profile.argv for name, profile in PROFILES.items()})
        self.assertIn("libc.prctl(38, 1, 0, 0, 0)", launcher.read_text())

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

    def test_redaction(self):
        redacted = redact_output("api_key=abc password: xyz\nAuthorization: Bearer token\nmy-real-key", secrets=("my-real-key",))
        for secret in ("abc", "xyz", "token", "my-real-key"):
            self.assertNotIn(secret, redacted)


class FakeSandbox:
    sandbox_id = "sandbox-fixture"

    def __init__(self, behavior="ok", *, kill_failure=False):
        self.behavior, self.kill_failure = behavior, kill_failure
        self.calls, self.writes, self.kills = [], [], 0
        self.files = SimpleNamespace(write=self.write)
        self.commands = SimpleNamespace(run=self.command)
        self.started = asyncio.Event()

    async def write(self, path, data, **kwargs):
        self.writes.append((path, data, kwargs))

    async def command(self, command, **kwargs):
        from e2b import CommandExitException, TimeoutException

        self.calls.append((command, kwargs))
        if "prepare_workspace.py" in command:
            revision = json.loads(self.writes[0][1])["revision"]
            if self.behavior == "prepare_fail":
                raise CommandExitException(stderr="sensitive", stdout="", exit_code=3, error=None)
            return SimpleNamespace(exit_code=0, stdout=f"prepared:{revision}\n", stderr="")
        self.started.set()
        if self.behavior == "wait":
            await asyncio.Event().wait()
        if self.behavior == "timeout":
            raise TimeoutException("private error")
        if self.behavior == "unknown":
            raise OSError("secret remote diagnostic")
        if self.behavior == "overflow":
            kwargs["on_stdout"]("x" * 2048)
        kwargs["on_stdout"]("stdout api_key=fixture-secret\n")
        kwargs["on_stderr"]("stderr\n")
        if self.behavior == "fail":
            raise CommandExitException(stderr="stderr", stdout="stdout", exit_code=7, error=None)
        return SimpleNamespace(exit_code=0, stdout="stdout", stderr="stderr")

    async def kill(self, **kwargs):
        self.kills += 1
        if self.kill_failure:
            raise OSError("private kill diagnostic")
        return False  # already gone is confirmed cleanup


class FakeFactory:
    def __init__(self, sandbox=None, error=None):
        self.sandbox, self.error, self.calls = sandbox or FakeSandbox(), error, []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.sandbox


class ProviderTests(unittest.IsolatedAsyncioTestCase):
    def make_runner(self, behavior="ok", *, kill_failure=False, **config):
        sandbox = FakeSandbox(behavior, kill_failure=kill_failure)
        factory = FakeFactory(sandbox)
        runner = E2BRunner(E2BConfig(api_key="fixture-api-key", template_id="fixed-template",
            template_qualified=True, **config), sandbox_factory=factory)
        return runner, sandbox, factory

    async def authority(self):
        pass

    async def test_real_exit_and_credential_network_user_contract(self):
        runner, sandbox, factory = self.make_runner("fail")
        files = {"tests/test_x.py": b"test source", "image.png": b"\x00\xff"}
        result = await runner.run(files, "python_pytest", operation_id="op1", authority=self.authority)
        self.assertEqual(result.exit_code, 7)
        self.assertTrue(result.cleanup_confirmed)
        self.assertEqual(result.revision, workspace_revision(files))
        self.assertEqual(result.argv, PROFILES["python_pytest"].argv)
        self.assertNotIn("fixture-secret", result.stdout)
        self.assertIsNone(result.cost_usd)
        self.assertEqual(sandbox.kills, 1)
        create = factory.calls[0]
        self.assertFalse(create["allow_internet_access"])
        self.assertFalse(create["network"]["allow_public_traffic"])
        self.assertEqual(create["network"]["deny_out"], ["0.0.0.0/0"])
        self.assertTrue(create["secure"])
        self.assertEqual(create["envs"], {})
        self.assertEqual(create["lifecycle"], {"on_timeout": "kill", "auto_resume": False})
        self.assertEqual(create["retries"], 0)
        self.assertEqual(sandbox.calls[-1][1]["user"], "root")
        self.assertEqual(sandbox.calls[-1][1]["cwd"], "/")
        self.assertEqual(sandbox.calls[0][1]["user"], "root")
        self.assertEqual(sandbox.writes[0][2]["user"], "root")
        self.assertNotIn("fixture-api-key", sandbox.writes[0][1])

    async def test_timeout_output_limit_and_cleanup_failure_cannot_pass(self):
        for behavior in ("timeout", "overflow"):
            runner, sandbox, _ = self.make_runner(behavior, max_output_bytes=1024)
            result = await runner.run({}, "python_unittest", operation_id=behavior, authority=self.authority)
            self.assertIsNone(result.exit_code)
            self.assertEqual(result.timed_out, behavior == "timeout")
            self.assertEqual(result.output_truncated, behavior == "overflow")
            self.assertLessEqual(len(result.stdout.encode()) + len(result.stderr.encode()), 1024)
            self.assertEqual(sandbox.kills, 1)
        runner, _, _ = self.make_runner(kill_failure=True)
        result = await runner.run({}, "node_test", operation_id="cleanup", authority=self.authority)
        self.assertFalse(result.cleanup_confirmed)

    async def test_cancellation_terminates_entire_sandbox(self):
        runner, sandbox, _ = self.make_runner("wait")
        task = asyncio.create_task(runner.run({}, "python_unittest", operation_id="cancel", authority=self.authority))
        await sandbox.started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(sandbox.kills, 1)

    async def test_failures_are_safe_and_never_retry_allocation(self):
        for behavior in ("unknown", "prepare_fail"):
            runner, sandbox, factory = self.make_runner(behavior)
            with self.assertRaises(SandboxUnavailable) as caught:
                await runner.run({}, "python_unittest", operation_id=behavior, authority=self.authority)
            self.assertNotIn("secret", str(caught.exception))
            self.assertEqual(len(factory.calls), 1)
            self.assertEqual(sandbox.kills, 1)
        factory = FakeFactory(error=OSError("private allocation"))
        runner = E2BRunner(E2BConfig(api_key="key", template_id="template", template_qualified=True), sandbox_factory=factory)
        with self.assertRaisesRegex(SandboxUnavailable, "ALLOCATION_UNKNOWN"):
            await runner.run({}, "python_unittest", operation_id="unknown", authority=self.authority)
        self.assertEqual(len(factory.calls), 1)

    async def test_authority_rechecked_before_command(self):
        runner, sandbox, _ = self.make_runner()
        calls = 0
        async def revoked():
            nonlocal calls
            calls += 1
            if calls == 3:
                raise PermissionError("revoked")
        with self.assertRaises(SandboxUnavailable):
            await runner.run({}, "python_pytest", operation_id="revoked", authority=revoked)
        self.assertEqual(len(sandbox.calls), 1)
        self.assertEqual(sandbox.kills, 1)

    async def test_bad_config_profile_and_endpoint_fail_before_allocation(self):
        runner, _, factory = self.make_runner()
        with self.assertRaises(ValueError):
            await runner.run({}, "arbitrary_shell", operation_id="bad", authority=self.authority)
        with patch.dict("os.environ", {"E2B_SANDBOX_URL": "http://localhost:49983"}):
            with self.assertRaisesRegex(SandboxUnavailable, "UNSAFE_ENDPOINT"):
                await runner.run({}, "python_pytest", operation_id="bad", authority=self.authority)
        self.assertEqual(factory.calls, [])
        with self.assertRaisesRegex(SandboxUnavailable, "NOT_CONFIGURED"):
            await E2BRunner(E2BConfig()).run({}, "python_pytest", operation_id="no-key", authority=self.authority)


if __name__ == "__main__":
    unittest.main()
