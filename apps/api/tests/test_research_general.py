import json
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import patch
from agentforge.orchestration.graph import bounded_stages
from agentforge.repositories.research_inputs import full_range
from agentforge.tools.catalog import ToolCatalog
from agentforge.tools.contracts import TaskInputArgs
from agentforge.tools.general import register_general
from agentforge.subagents.registry import load_builtin
from agentforge.subagents.scope import ScopeDenied


class BoundTests(TestCase):
    def test_complexity_is_bounded_and_goal_is_never_sliced_into_lost_clauses(self):
        self.assertEqual(len(bounded_stages("What does this policy require?", "research")), 1)
        self.assertEqual(len(bounded_stages("Compare A; evaluate B\nThen C", "research")), 3)
        self.assertEqual(len(bounded_stages("a" * 8000, "research")), 3)
        self.assertEqual(len(bounded_stages("Compare A; evaluate B", "general")), 1)

    def test_model_only_general_configuration_is_truthful(self):
        from agentforge.api.routes_configuration import configuration
        from agentforge.settings import Settings
        settings = Settings(_env_file=None, app_env="test", auth_mode="disabled", llm_model="fixture/model", llm_api_key="fixture-only",
            llm_supports_tools=True, llm_supports_json=True, llm_context_window_tokens=32000,
            llm_input_usd_per_million=1, llm_output_usd_per_million=1, tavily_api_key=None)
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=settings)))
        result = configuration(request, SimpleNamespace(github_user_id=-1))
        self.assertTrue(result["general_configured"])
        self.assertFalse(result["research_configured"])
        self.assertEqual(result["general_sandbox_execution"], "unsupported")
        self.assertNotIn("source_backed_research", result["general_capabilities"])

    def test_coverage_requires_no_gaps_and_matching_real_ranges(self):
        self.assertTrue(full_range([(5, 10), (0, 6), (0, 6)], 10))
        self.assertFalse(full_range([(0, 4), (5, 10)], 10))
        self.assertFalse(full_range([(0, 20)], 10))
        self.assertFalse(full_range([(-1, 10)], 10))

    def test_custom_source_versions_can_read_owned_inputs_without_execution_grants(self):
        from agentforge.api.routes_agents import validate_management
        from agentforge.contracts.agent_definition import AgentDefinition, Permissions
        definition = load_builtin("researcher", 2).model_copy(update={"name": "private-research", "version": 1,
            "permissions": Permissions(web=True, allowed_repo_ids=(), allowed_sandbox_ids=(), allowed_context_refs=(),
                allowed_command_profiles=(), allowed_web_hosts=()),
            "budget": load_builtin("researcher", 1).budget})
        validated = AgentDefinition.model_validate_json(definition.model_dump_json())
        validate_management(validated)

    def test_new_versions_leave_existing_coding_and_research_snapshots_valid(self):
        for name, version in (("researcher", 1), ("researcher", 2), ("general-executor", 2), ("general-executor", 3), ("verifier", 2), ("verifier", 3)):
            definition = load_builtin(name, version)
            self.assertEqual(definition.version, version)
        self.assertEqual(load_builtin("general-executor", 3).output_schema_id, "general_result_v1")
        self.assertNotIn("run_sandbox_command", load_builtin("general-executor", 3).allowed_tools)


class PageTests(IsolatedAsyncioTestCase):
    async def test_json_escaping_utf8_and_cursors_preserve_exact_model_visible_consumption(self):
        content = ('"\\\n\t文🙂' * 2000)
        receipts = []
        repository = SimpleNamespace(input_snapshot=lambda *a: {"id": "owned-id", "content": content, "content_sha256": "a" * 64},
            record_read=lambda *a, **kw: receipts.append(kw) or "read-id")
        catalog = ToolCatalog()
        with patch("agentforge.tools.general.ResearchInputRepository", return_value=repository):
            register_general(catalog, None, SimpleNamespace(research_storage_path="/tmp/unused"))
        handler = catalog.get("read_task_input").handler
        offset, pages = 0, []
        while True:
            result = await handler(TaskInputArgs(input_kind="attachment", offset=offset, max_chars=8000), SimpleNamespace(task=None, authorize_current=None))
            rendered = json.dumps(result.model_dump(mode="json", exclude={"cost_usd"}), ensure_ascii=False).encode()
            self.assertLess(len(rendered), 16000)
            pages.append(result.data["content"])
            self.assertEqual(receipts[-1]["start"], offset)
            self.assertEqual(receipts[-1]["end"], offset + len(result.data["content"]))
            if result.data["next_offset"] is None: break
            offset = result.data["next_offset"]
        self.assertEqual("".join(pages), content)
        self.assertTrue(full_range([(item["start"], item["end"]) for item in receipts], len(content)))
        with self.assertRaises(ScopeDenied):
            await handler(TaskInputArgs(input_kind="attachment", offset=len(content) + 1), SimpleNamespace(task=None, authorize_current=None))
