"""File tools: typed errors the model can act on, exact edits, scoped search and depth-limited listings."""
import hashlib
from decimal import Decimal
from types import SimpleNamespace as NS
from unittest import IsolatedAsyncioTestCase, TestCase

from agentforge.tools.catalog import ToolCatalog
from agentforge.tools.contracts import (
    RepoFilesArgs, RepoSearchArgs, SandboxEditArgs, SandboxReadArgs, SandboxWriteArgs,
)
from agentforge.tools.errors import ToolError
from agentforge.tools.files import overview, require_directory, require_file, require_text
from agentforge.tools.repository_read import register_repository_read
from agentforge.tools.sandbox_files import register_sandbox_files
from agentforge.tools.text_ranges import read_lines

SANDBOX = "sandbox-1"
REPO = "owner/name"
SHA = "0" * 40


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class FakeRepository:
    """The slice of CodingRepository the file tools use, held in memory with the same CAS rule."""

    def __init__(self, files):
        self.working = dict(files)
        self.baseline = dict(files)
        self.writes = []
        self.evidence = []

    def files(self, ctx, *, revision=None, baseline=False):
        return dict(sorted((self.baseline if baseline else self.working).items()))

    def session(self, ctx):
        return {"revision": "r1", "commit_sha": SHA, "id": SANDBOX, "repo_id": REPO}

    def record_evidence(self, ctx, **kwargs):
        self.evidence.append(kwargs)
        return {"evidence_id": f"ev-{len(self.evidence)}", "artifact_id": "art"}

    def write_file(self, ctx, *, operation_key, path, content, expected_sha256):
        data = content.encode()
        prior = self.working.get(path)
        if (sha(prior) if prior is not None else None) != expected_sha256:
            raise AssertionError("handler must satisfy CAS before writing")
        self.working[path] = data
        self.writes.append(path)
        return {"path": path, "sha256": sha(data), "revision": "r2"}


def tools(files):
    repository = FakeRepository(files)
    catalog = ToolCatalog()
    register_sandbox_files(catalog, repository)
    register_repository_read(catalog, repository, NS())
    execution = NS(task=NS(), operation_key="op", authorize_current=None)
    return repository, catalog, execution


async def call(catalog, execution, name, args):
    return await catalog.get(name).handler(args, execution)


class FileHelperTests(TestCase):
    def test_a_missing_file_names_the_closest_real_paths(self):
        files = {"src/app/main.py": b"x", "src/app/util.py": b"y", "README.md": b"z"}
        with self.assertRaises(ToolError) as caught:
            require_file(files, "src/main.py")
        self.assertEqual(caught.exception.code, "FILE_NOT_FOUND")
        self.assertIn("src/app/main.py", caught.exception.detail)

    def test_binary_content_is_a_typed_error_not_a_crash(self):
        with self.assertRaises(ToolError) as caught:
            require_text({"logo.png": b"\x89PNG\x00\x01"}, "logo.png")
        self.assertEqual(caught.exception.code, "BINARY_FILE")
        with self.assertRaises(ToolError):
            require_text({"bad.txt": b"\xff\xfe"}, "bad.txt")
        self.assertEqual(require_text({"ok.txt": "héllo".encode()}, "ok.txt"), "héllo")

    def test_unknown_directories_suggest_similar_names(self):
        files = {"src/api/a.py": b"", "docs/b.md": b""}
        with self.assertRaises(ToolError) as caught:
            require_directory(files, "src/apis")
        self.assertIn("api", caught.exception.detail)
        require_directory(files, "src/api")
        require_directory(files, None)

    def test_depth_limited_overview_collapses_folders_with_counts(self):
        paths = ["README.md", "src/a.py", "src/pkg/b.py", "src/pkg/c.py", "docs/x.md"]
        entries = overview(paths, ".", 1)
        self.assertEqual(entries, ["README.md", "src/ (3 files)", "docs/ (1 files)"])
        self.assertEqual(overview(paths, ".", None), paths)
        nested = overview(["src/a.py", "src/pkg/b.py", "src/pkg/c.py"], "src", 1)
        self.assertEqual(nested, ["src/a.py", "src/pkg/ (2 files)"])

    def test_line_range_errors_stay_value_errors_but_carry_codes_and_detail(self):
        with self.assertRaisesRegex(ValueError, "read_line_start_out_of_range") as caught:
            read_lines("a\nb\n", line_start=9)
        self.assertEqual(caught.exception.code, "INVALID_RANGE")
        self.assertIn("2 lines", caught.exception.detail)


class SandboxFileToolTests(IsolatedAsyncioTestCase):
    async def test_reading_a_missing_file_returns_a_recoverable_error(self):
        _, catalog, execution = tools({"app.py": b"print(1)\n"})
        with self.assertRaises(ToolError) as caught:
            await call(catalog, execution, "read_sandbox_file", SandboxReadArgs(sandbox_id=SANDBOX, path="apps.py"))
        self.assertEqual(caught.exception.code, "FILE_NOT_FOUND")
        self.assertIn("app.py", caught.exception.detail)

    async def test_edit_replaces_exactly_one_match_without_needing_a_hash(self):
        repository, catalog, execution = tools({"app.py": b"def f():\n    return 1\n\ndef g():\n    return 2\n"})
        outcome = await call(catalog, execution, "edit_sandbox_file", SandboxEditArgs(
            sandbox_id=SANDBOX, path="app.py", old_string="def f():\n    return 1", new_string="def f():\n    return 10"))
        self.assertEqual(outcome.status, "ok")
        self.assertEqual(outcome.data["replacements"], 1)
        self.assertEqual(repository.working["app.py"], b"def f():\n    return 10\n\ndef g():\n    return 2\n")

    async def test_edit_rejects_ambiguous_missing_and_noop_changes_with_distinct_codes(self):
        repository, catalog, execution = tools({"app.py": b"x = 1\nx = 1\ny = 2\n"})
        def edit(**kwargs):
            return call(catalog, execution, "edit_sandbox_file", SandboxEditArgs(sandbox_id=SANDBOX, path="app.py", **kwargs))
        with self.assertRaises(ToolError) as ambiguous:
            await edit(old_string="x = 1", new_string="x = 3")
        self.assertEqual(ambiguous.exception.code, "EDIT_AMBIGUOUS")
        self.assertIn("2 places", ambiguous.exception.detail)
        with self.assertRaises(ToolError) as missing:
            await edit(old_string="z = 9", new_string="z = 8")
        self.assertEqual(missing.exception.code, "EDIT_NO_MATCH")
        with self.assertRaises(ToolError) as noop:
            await edit(old_string="y = 2", new_string="y = 2")
        self.assertEqual(noop.exception.code, "INVALID_ARGUMENT")
        self.assertEqual(repository.writes, [])  # every rejection happened before any write
        replaced = await edit(old_string="x = 1", new_string="x = 3", replace_all=True)
        self.assertEqual(replaced.data["replacements"], 2)
        self.assertEqual(repository.working["app.py"], b"x = 3\nx = 3\ny = 2\n")

    async def test_edit_tells_the_model_when_only_whitespace_differs_and_handles_crlf_files(self):
        _, catalog, execution = tools({"a.py": b"if x:\n    run()\n"})
        with self.assertRaises(ToolError) as caught:
            await call(catalog, execution, "edit_sandbox_file", SandboxEditArgs(
                sandbox_id=SANDBOX, path="a.py", old_string="if x:\n  run()", new_string="pass"))
        self.assertIn("whitespace", caught.exception.detail)
        repository, catalog, execution = tools({"w.txt": b"one\r\ntwo\r\nthree\r\n"})
        outcome = await call(catalog, execution, "edit_sandbox_file", SandboxEditArgs(
            sandbox_id=SANDBOX, path="w.txt", old_string="one\ntwo", new_string="1\n2"))
        self.assertEqual(outcome.status, "ok")
        self.assertEqual(repository.working["w.txt"], b"1\r\n2\r\nthree\r\n")

    async def test_edit_of_a_protected_file_succeeds_but_warns(self):
        _, catalog, execution = tools({"tests/test_app.py": b"assert True\n"})
        outcome = await call(catalog, execution, "edit_sandbox_file", SandboxEditArgs(
            sandbox_id=SANDBOX, path="tests/test_app.py", old_string="True", new_string="False"))
        self.assertIn("disqualifies", outcome.data["warning"])

    async def test_write_distinguishes_existing_stale_and_missing_files(self):
        repository, catalog, execution = tools({"a.txt": b"old"})
        def write(path, content="new", expected=None):
            return call(catalog, execution, "write_sandbox_file",
                        SandboxWriteArgs(sandbox_id=SANDBOX, path=path, content=content, expected_sha256=expected))
        with self.assertRaises(ToolError) as exists:
            await write("a.txt")
        self.assertEqual(exists.exception.code, "FILE_EXISTS")
        with self.assertRaises(ToolError) as stale:
            await write("a.txt", expected="f" * 64)
        self.assertEqual(stale.exception.code, "STALE_WRITE")
        self.assertNotIn(sha(b"old"), stale.exception.detail)  # the current hash must not be handed over
        with self.assertRaises(ToolError) as phantom:
            await write("b.txt", expected="f" * 64)
        self.assertEqual(phantom.exception.code, "FILE_NOT_FOUND")
        with self.assertRaises(ToolError) as binary:
            await write("c.txt", content="a\x00b")
        self.assertEqual(binary.exception.code, "INVALID_ARGUMENT")
        self.assertEqual(repository.writes, [])
        created = await write("b.txt", content="fresh")
        updated = await write("a.txt", content="newer", expected=sha(b"old"))
        self.assertEqual((created.status, updated.status), ("ok", "ok"))
        self.assertEqual(repository.writes, ["b.txt", "a.txt"])


class RepositoryToolTests(IsolatedAsyncioTestCase):
    FILES = {
        "src/api/routes.py": b"def route():\n    # TODO fix\n    return 1\n",
        "src/api/models.py": b"class Todo:\n    pass\n",
        "docs/guide.md": b"todo: write docs\n",
        "logo.png": b"\x89PNG\x00",
    }

    async def test_search_is_scoped_case_aware_and_returns_context(self):
        _, catalog, execution = tools(self.FILES)
        def search(**kwargs):
            return call(catalog, execution, "search_repo", RepoSearchArgs(repo_id=REPO, ref=SHA, query="todo", **kwargs))
        everywhere = await search()
        self.assertEqual({m["path"] for m in everywhere.data["matches"]}, {"src/api/routes.py", "src/api/models.py", "docs/guide.md"})
        scoped = await search(path_prefix="src/api")
        self.assertEqual({m["path"] for m in scoped.data["matches"]}, {"src/api/routes.py", "src/api/models.py"})
        exact = await search(case_sensitive=True)
        self.assertEqual([m["path"] for m in exact.data["matches"]], ["docs/guide.md"])
        context = await search(path_prefix="src/api/routes.py", context_lines=1)
        match = context.data["matches"][0]
        self.assertEqual((match["line"], match["before"], match["after"]), (2, ["def route():"], ["    return 1"]))
        capped = await search(max_results=1)
        self.assertEqual((len(capped.data["matches"]), capped.data["truncated"]), (1, True))
        with self.assertRaises(ToolError) as caught:
            await search(path_prefix="src/apis")
        self.assertEqual(caught.exception.code, "FILE_NOT_FOUND")

    async def test_listing_summarises_deep_folders_and_rejects_unknown_directories(self):
        _, catalog, execution = tools(self.FILES)
        top = await call(catalog, execution, "list_repo_files", RepoFilesArgs(repo_id=REPO, ref=SHA, max_depth=1))
        self.assertEqual(top.data["paths"], ["docs/ (1 files)", "logo.png", "src/ (2 files)"])
        full = await call(catalog, execution, "list_repo_files", RepoFilesArgs(repo_id=REPO, ref=SHA, path="src"))
        self.assertEqual(full.data["paths"], ["src/api/models.py", "src/api/routes.py"])
        with self.assertRaises(ToolError):
            await call(catalog, execution, "list_repo_files", RepoFilesArgs(repo_id=REPO, ref=SHA, path="nope"))

    def test_search_prefix_is_normalised(self):
        for given, expected in (("src/api/", "src/api"), ("./src", "src"), (".", None), ("  ", None)):
            self.assertEqual(RepoSearchArgs(repo_id=REPO, ref=SHA, query="x", path_prefix=given).path_prefix, expected)
