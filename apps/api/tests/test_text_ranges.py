"""Read pages expose file tails without partial UTF-8 lines or provider calls."""
import unittest

from pydantic import TypeAdapter, ValidationError
from agentforge.tools.contracts import FileReadArgs, FileWriteArgs, RepoFileArgs, ToolRequest
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
        request = TypeAdapter(ToolRequest).validate_python({"name": "read", "args": {"path": "app.py", "offset": 201, "limit": 50}})
        self.assertIsInstance(request.args, FileReadArgs)
        workspace = FileReadArgs(path="app.py")
        self.assertEqual((workspace.offset, workspace.limit), (1, 400))
        with self.assertRaises(ValidationError):  # a write is a whole file; it takes no line range
            FileWriteArgs(path="app.py", content="a", offset=1)
        for kwargs in ({"offset": 0}, {"limit": 2001}, {"offset": True}, {"limit": 0}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValidationError):
                FileReadArgs(path="app.py", **kwargs)
        for kwargs in ({"line_start": 0}, {"max_lines": 201}, {"line_start": True}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValidationError):
                RepoFileArgs(repo_id="owner/repo", ref="a" * 40, path="app.py", **kwargs)


if __name__ == "__main__":
    unittest.main()
