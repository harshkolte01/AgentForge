"""Read pages expose file tails without partial UTF-8 lines or provider calls."""
import unittest
from types import SimpleNamespace
from uuid import uuid4

from pydantic import TypeAdapter, ValidationError
from agentforge.tools.catalog import ToolCatalog
from agentforge.tools.contracts import RepoFileArgs, SandboxReadArgs, SandboxWriteArgs, ToolRequest
from agentforge.tools.repository_read import register_repository_read
from agentforge.tools.sandbox_files import register_sandbox_files
from agentforge.tools.text_ranges import read_lines


class RangeTests(unittest.TestCase):
    def test_pagination_reassembles_exact_text(self):
        text = "".join(f"line {number}\n" for number in range(1, 502))
        cursor, chunks = 1, []
        while cursor is not None:
            page = read_lines(text, line_start=cursor)
            self.assertLessEqual(len(page["content"].encode()), 10000)
            self.assertEqual(page["total_lines"], 501)
            chunks.append(page["content"])
            cursor = page["next_line"]
        self.assertEqual("".join(chunks), text)
        self.assertFalse(page["truncated"])

    def test_byte_limit_applies_to_utf8_and_cursor_advances(self):
        line = "é" * 2499 + "\n"
        page = read_lines(line * 4)
        self.assertEqual(page["content"], line * 2)
        self.assertEqual(page["next_line"], 3)
        self.assertTrue(page["has_more"])
        self.assertLessEqual(len(page["content"].encode()), 10000)
        self.assertEqual(read_lines(line * 4, line_start=3)["content"], line * 2)

    def test_overlong_line_fails_explicitly(self):
        with self.assertRaisesRegex(ValueError, "read_line_exceeds_10000_byte_limit"):
            read_lines("é" * 5001)
        page = read_lines("short\n" + "x" * 10001)
        self.assertEqual(page["next_line"], 2)
        with self.assertRaisesRegex(ValueError, "read_line_exceeds_10000_byte_limit"):
            read_lines("short\n" + "x" * 10001, line_start=page["next_line"])

    def test_empty_file_final_line_and_bad_ranges(self):
        self.assertEqual(read_lines("")["total_lines"], 0)
        self.assertEqual(read_lines("a\nb", line_start=2)["content"], "b")
        self.assertIsNone(read_lines("a\nb", line_start=3)["next_line"])
        for kwargs in ({"line_start": 0}, {"line_start": True}, {"line_start": 4}, {"max_lines": 201}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                read_lines("a\nb", **kwargs)

    def test_read_schema_defaults_and_write_schema_stays_narrow(self):
        repo = RepoFileArgs(repo_id="owner/repo", ref="a" * 40, path="app.py")
        self.assertEqual((repo.line_start, repo.max_lines), (1, 200))
        request = TypeAdapter(ToolRequest).validate_python({"name": "read_sandbox_file", "args": {
            "sandbox_id": "session", "path": "app.py", "line_start": 201, "max_lines": 50}})
        self.assertIsInstance(request.args, SandboxReadArgs)
        with self.assertRaises(ValidationError):
            SandboxWriteArgs(sandbox_id="session", path="app.py", content="a", line_start=1)
        for kwargs in ({"line_start": 0}, {"max_lines": 201}, {"line_start": True}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValidationError):
                SandboxReadArgs(sandbox_id="session", path="app.py", **kwargs)


class AdapterTests(unittest.IsolatedAsyncioTestCase):
    def repository(self, content):
        return SimpleNamespace(files=lambda *args, **kwargs: {"app.py": content},
            session=lambda *args: {"revision": "r", "commit_sha": "a" * 40},
            record_evidence=lambda *args, **kwargs: {"evidence_id": str(uuid4())})

    async def test_both_adapters_return_requested_tail_and_full_file_hash(self):
        text = "".join(f"line {number}\n" for number in range(1, 301))
        repository, catalog = self.repository(text.encode()), ToolCatalog()
        register_repository_read(catalog, repository, None)
        register_sandbox_files(catalog, repository)
        execution = SimpleNamespace(task=None, operation_key="read")
        repo_args = RepoFileArgs(repo_id="owner/repo", ref="a" * 40, path="app.py", line_start=299)
        sandbox_args = SandboxReadArgs(sandbox_id="session", path="app.py", line_start=299)
        repo = await catalog.get("read_repo_file").handler(repo_args, execution)
        sandbox = await catalog.get("read_sandbox_file").handler(sandbox_args, execution)
        for result in (repo, sandbox):
            self.assertEqual(result.data["content"], "line 299\nline 300\n")
            self.assertEqual(result.data["total_lines"], 300)
            self.assertFalse(result.data["truncated"])
            self.assertIsNone(result.data["next_line"])
        self.assertEqual(repo.data["sha256"], sandbox.data["sha256"])
        default_args = SandboxReadArgs(sandbox_id="session", path="app.py")
        first = await catalog.get("read_sandbox_file").handler(default_args, execution)
        self.assertEqual(first.data["next_line"], 201)

    async def test_binary_still_rejected_before_evidence(self):
        repository, catalog = self.repository(b"\x00binary"), ToolCatalog()
        register_repository_read(catalog, repository, None)
        register_sandbox_files(catalog, repository)
        execution = SimpleNamespace(task=None, operation_key="read")
        for name, args in (("read_repo_file", RepoFileArgs(repo_id="owner/repo", ref="a" * 40, path="app.py")),
                           ("read_sandbox_file", SandboxReadArgs(sandbox_id="session", path="app.py"))):
            with self.subTest(name=name), self.assertRaises(ValueError):
                await catalog.get(name).handler(args, execution)


if __name__ == "__main__":
    unittest.main()
