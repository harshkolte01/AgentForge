import json
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import patch
from agentforge.repositories.research_inputs import full_range
from agentforge.tools.catalog import ToolCatalog
from agentforge.tools.contracts import TaskInputArgs
from agentforge.tools.general import register_general
from agentforge.subagents.registry import load_builtin
from agentforge.subagents.scope import ScopeDenied


class BoundTests(TestCase):
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
        from agentforge.contracts.agent_definition import AgentDefinition, Budget, Permissions
        definition = AgentDefinition(name="private-research", version=1, description="Reads the sources it is given.",
            system_prompt="Read the supplied sources and answer from them only, in plain text.",
            allowed_tools=("web_search", "fetch_webpage", "read_task_input", "read_research_source"),
            permissions=Permissions(web=True, allowed_repo_ids=(), allowed_sandbox_ids=(), allowed_context_refs=(),
                allowed_command_profiles=(), allowed_web_hosts=()),
            output_schema_id="research_result_v1", budget=Budget(max_steps=8, max_tool_calls=12, max_llm_cost_usd=0.5, max_wall_time_sec=120))
        validate_management(AgentDefinition.model_validate_json(definition.model_dump_json()))

    def test_a_stored_definition_of_the_old_pipeline_still_validates_as_data(self):
        # Rows saved before the plain-text agent keep loading even though their typed output ids no longer do anything.
        from agentforge.contracts.agent_definition import AgentDefinition
        stored = load_builtin("agent", 1).model_dump(mode="json") | {"name": "old-research", "output_schema_id": "general_result_v1"}
        self.assertEqual(AgentDefinition.model_validate(stored).output_schema_id, "general_result_v1")


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
