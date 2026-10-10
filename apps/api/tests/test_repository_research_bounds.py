import json
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from agentforge.tools.catalog import ToolCatalog
from agentforge.tools.contracts import RepoFilesArgs, RepoSearchArgs
from agentforge.tools.repository_research import register_repository_research


class RepositoryPreviewTests(IsolatedAsyncioTestCase):
    async def fixture(self, files):
        sources = []
        def add_source(*args, **kwargs):
            sources.append(kwargs)
            return {"id": str(uuid4())}
        repository = SimpleNamespace(add_source=add_source)
        github = SimpleNamespace(snapshot=AsyncMock(return_value=SimpleNamespace(commit_sha="a" * 40, files=files)))
        catalog = ToolCatalog()
        with patch("agentforge.tools.repository_research.ResearchInputRepository", return_value=repository):
            register_repository_research(catalog, None, {"repo_id": "owner/repo", "ref": "a" * 40}, github,
                SimpleNamespace(verify_tool_inputs=AsyncMock()))
        execution = SimpleNamespace(task=None, authorize_current=None, agent_run_id=uuid4(), operation_key="fixture-search")
        return catalog, execution, sources

    def assert_bounded(self, result):
        encoded = json.dumps(result.model_dump(mode="json", exclude={"cost_usd"}), ensure_ascii=False).encode("utf-8")
        self.assertLess(len(encoded), 10500)
        self.assertLess(len(encoded), 16000)

    async def test_search_counts_unicode_and_escaped_long_paths_and_records_only_returned_sources(self):
        files = {("quoted\"" * 90) + str(index) + ".md": (("🙂漢字\\\"" * 100) + " needle\n").encode() for index in range(30)}
        catalog, execution, sources = await self.fixture(files)
        result = await catalog.get("search_repo").handler(RepoSearchArgs(repo_id="owner/repo", ref="a" * 40, query="needle"), execution)
        self.assert_bounded(result)
        self.assertTrue(result.data["truncated"])
        self.assertGreater(len(result.data["matches"]), 0)
        self.assertLess(len(result.data["matches"]), 30)
        self.assertEqual(len(sources), len(result.data["matches"]))
        self.assertEqual({match["source_id"] for match in result.data["matches"]}, set(result.evidence_ids))
        self.assertEqual({source["title"] for source in sources}, {match["path"] for match in result.data["matches"]})

    async def test_listing_counts_json_escaping_with_truthful_truncation(self):
        files = {("quoted\"" * 90) + str(index) + ".md": b"" for index in range(30)}
        catalog, execution, sources = await self.fixture(files)
        result = await catalog.get("list_repo_files").handler(RepoFilesArgs(repo_id="owner/repo", ref="a" * 40), execution)
        self.assert_bounded(result)
        self.assertTrue(result.data["truncated"])
        self.assertLess(len(result.data["paths"]), len(files))
        self.assertFalse(sources)

    async def test_exact_match_limit_does_not_claim_truncation_without_omitted_matches(self):
        catalog, execution, sources = await self.fixture({"small.md": b"needle\n" * 30, "irrelevant.md": b"nothing"})
        result = await catalog.get("search_repo").handler(RepoSearchArgs(repo_id="owner/repo", ref="a" * 40, query="needle"), execution)
        self.assert_bounded(result)
        self.assertEqual(len(result.data["matches"]), 30)
        self.assertFalse(result.data["truncated"])
        self.assertEqual(len(sources), 1)
