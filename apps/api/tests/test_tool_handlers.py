"""Repository tools: typed errors the model can act on, scoped search, depth-limited listings, paged reads and scope checks.

(The workspace tools that read and change files in the sandbox are covered by test_sandbox_shell.py.)"""
from types import SimpleNamespace as NS
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from agentforge.subagents.scope import ScopeDenied
from agentforge.tools.catalog import ToolCatalog
from agentforge.tools.contracts import IssueArgs, RepoFileArgs, RepoFilesArgs, RepoSearchArgs
from agentforge.tools.errors import ToolError
from agentforge.tools.files import overview, require_directory, require_file, require_text
from agentforge.tools.repository_research import register_repository_research
from agentforge.tools.text_ranges import read_lines

REPO = "owner/name"
SHA = "0" * 40
SELECTION = {"repo_id": REPO, "ref": SHA}


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


class Sources:
    """Stands in for the research-input table: every source a tool saves becomes evidence the model can cite."""

    def __init__(self):
        self.saved = []

    def add_source(self, task, **kwargs):
        self.saved.append(kwargs)
        return {"id": f"00000000-0000-0000-0000-{len(self.saved):012d}"}


def tools(files, *, selection=SELECTION, snapshot_sha=SHA, issue=None):
    sources, catalog = Sources(), ToolCatalog()
    github = NS(snapshot=AsyncMock(return_value=NS(commit_sha=snapshot_sha, files=dict(sorted(files.items())))), authorize=AsyncMock(),
                get_issue=AsyncMock(return_value=issue or {"number": 7, "title": "Bug", "state": "open"}))
    resources = NS(verify_tool_inputs=AsyncMock())
    with patch("agentforge.tools.repository_research.ResearchInputRepository", return_value=sources):
        register_repository_research(catalog, None, selection, github, resources)
    execution = NS(task=NS(), agent_run_id=uuid4(), operation_key="op", authorize_current=AsyncMock())
    return NS(catalog=catalog, github=github, resources=resources, execution=execution, sources=sources)


def call(env, name, args):
    return env.catalog.get(name).handler(args, env.execution)


class RepositoryToolTests(IsolatedAsyncioTestCase):
    FILES = {
        "src/api/routes.py": b"def route():\n    # TODO fix\n    return 1\n",
        "src/api/models.py": b"class Todo:\n    pass\n",
        "docs/guide.md": b"todo: write docs\n",
        "logo.png": b"\x89PNG\x00",
    }

    async def test_search_is_scoped_case_aware_and_returns_context(self):
        env = tools(self.FILES)
        def search(**kwargs):
            return call(env, "search_repo", RepoSearchArgs(repo_id=REPO, ref=SHA, query="todo", **kwargs))
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

    async def test_every_file_with_a_match_is_saved_once_as_evidence_and_matches_point_to_it(self):
        env = tools(self.FILES)
        found = await call(env, "search_repo", RepoSearchArgs(repo_id=REPO, ref=SHA, query="todo"))
        self.assertEqual(len(env.sources.saved), len({m["path"] for m in found.data["matches"]}))
        self.assertEqual(set(found.evidence_ids), {m["source_id"] for m in found.data["matches"]})
        self.assertTrue(all(item["url"].startswith(f"https://github.com/{REPO}/blob/{SHA}/") for item in env.sources.saved))

    async def test_listing_summarises_deep_folders_and_rejects_unknown_directories(self):
        env = tools(self.FILES)
        top = await call(env, "list_repo_files", RepoFilesArgs(repo_id=REPO, ref=SHA, max_depth=1))
        self.assertEqual(top.data["paths"], ["docs/ (1 files)", "logo.png", "src/ (2 files)"])
        full = await call(env, "list_repo_files", RepoFilesArgs(repo_id=REPO, ref=SHA, path="src"))
        self.assertEqual(full.data["paths"], ["src/api/models.py", "src/api/routes.py"])
        with self.assertRaises(ToolError):
            await call(env, "list_repo_files", RepoFilesArgs(repo_id=REPO, ref=SHA, path="nope"))

    async def test_a_huge_listing_is_cut_to_fit_and_says_so(self):
        env = tools({f"dir/file_{index:05d}.py": b"" for index in range(2000)})
        listing = await call(env, "list_repo_files", RepoFilesArgs(repo_id=REPO, ref=SHA))
        self.assertTrue(listing.data["truncated"])
        self.assertEqual(listing.data["total_entries"], 2000)
        self.assertLess(len(listing.data["paths"]), 2000)

    async def test_reading_returns_the_requested_tail_and_saves_the_whole_file_as_evidence(self):
        text = "".join(f"line {number}\n" for number in range(1, 301))
        env = tools({"app.py": text.encode()})
        tail = await call(env, "read_repo_file", RepoFileArgs(repo_id=REPO, ref=SHA, path="app.py", line_start=299))
        self.assertEqual(tail.data["content"], "line 299\nline 300\n")
        self.assertEqual((tail.data["total_lines"], tail.data["truncated"], tail.data["next_line"]), (300, False, None))
        self.assertEqual(env.sources.saved[0]["content"], text)
        self.assertEqual(tail.evidence_ids, (tail.data["source_id"],))
        first = await call(env, "read_repo_file", RepoFileArgs(repo_id=REPO, ref=SHA, path="app.py"))
        self.assertEqual(first.data["next_line"], 201)

    async def test_reading_a_missing_or_binary_file_is_a_typed_error_before_any_evidence(self):
        env = tools({"app.py": b"one\n", "logo.png": b"\x89PNG\x00"})
        for path, code in (("apps.py", "FILE_NOT_FOUND"), ("logo.png", "BINARY_FILE")):
            with self.subTest(path=path), self.assertRaises(ToolError) as caught:
                await call(env, "read_repo_file", RepoFileArgs(repo_id=REPO, ref=SHA, path=path))
            self.assertEqual(caught.exception.code, code)
        self.assertEqual(env.sources.saved, [])

    async def test_reading_past_the_end_is_an_invalid_range_the_model_can_correct(self):
        env = tools({"app.py": b"one\n"})
        with self.assertRaises(ToolError) as caught:
            await call(env, "read_repo_file", RepoFileArgs(repo_id=REPO, ref=SHA, path="app.py", line_start=9))
        self.assertEqual(caught.exception.code, "INVALID_RANGE")
        self.assertIn("1 lines", caught.exception.detail)

    async def test_the_snapshot_is_fetched_once_and_access_is_rechecked_on_every_call(self):
        env = tools({"a.py": b"x"})
        for _ in range(3):
            await call(env, "list_repo_files", RepoFilesArgs(repo_id=REPO, ref=SHA))
        env.github.snapshot.assert_awaited_once_with(REPO, SHA)
        self.assertEqual(env.execution.authorize_current.await_count, 3)

    async def test_a_snapshot_of_a_different_commit_is_refused(self):
        env = tools({"a.py": b"x"}, snapshot_sha="1" * 40)
        with self.assertRaises(ScopeDenied):
            await call(env, "list_repo_files", RepoFilesArgs(repo_id=REPO, ref=SHA))

    async def test_an_issue_is_read_through_github_and_kept_as_evidence(self):
        env = tools({}, issue={"number": 7, "title": "Crash on start", "state": "open"})
        outcome = await call(env, "get_issue", IssueArgs(repo_id=REPO, issue_number=7))
        env.github.get_issue.assert_awaited_once_with(REPO, 7)
        self.assertEqual((outcome.data["title"], outcome.evidence_ids), ("Crash on start", (outcome.data["source_id"],)))
        self.assertEqual(env.sources.saved[0]["url"], f"https://github.com/{REPO}/issues/7")

    def test_search_prefix_is_normalised(self):
        for given, expected in (("src/api/", "src/api"), ("./src", "src"), (".", None), ("  ", None)):
            self.assertEqual(RepoSearchArgs(repo_id=REPO, ref=SHA, query="x", path_prefix=given).path_prefix, expected)


class RepositoryScopeTests(IsolatedAsyncioTestCase):
    """The task fixes one repository and one commit; the model can neither widen nor change them."""

    def ctx(self, allowed=(REPO,)):
        return NS(task_permissions=NS(allowed_repo_ids=allowed))

    async def test_the_repository_and_commit_are_bound_for_every_repository_tool(self):
        env = tools({})
        self.assertEqual(env.resources.bindings(self.ctx(), "read_repo_file"), {"repo_id": REPO, "ref": SHA})

    async def test_a_call_for_the_selected_repository_passes_and_rechecks_the_users_access(self):
        env = tools({})
        await env.resources.verify_tool_inputs(self.ctx(), "read_repo_file", NS(repo_id=REPO, ref=SHA))
        env.github.authorize.assert_awaited_once_with(REPO)

    async def test_another_repository_another_commit_or_an_ungranted_repository_is_denied(self):
        env = tools({})
        for args, ctx in ((NS(repo_id="other/repo", ref=SHA), self.ctx()), (NS(repo_id=REPO, ref="1" * 40), self.ctx()),
                          (NS(repo_id=REPO, ref=SHA), self.ctx(allowed=()))):
            with self.subTest(args=args), self.assertRaises(ScopeDenied):
                await env.resources.verify_tool_inputs(ctx, "search_repo", args)
        env.github.authorize.assert_not_awaited()

    async def test_tools_that_are_not_repository_tools_are_not_checked_here(self):
        env = tools({})
        await env.resources.verify_tool_inputs(self.ctx(allowed=()), "web_search", NS(query="x"))
        env.github.authorize.assert_not_awaited()
