"""Model-facing contracts: prompts, tool descriptions/schemas, error guidance and step history."""
import json
import re
from decimal import Decimal
from types import SimpleNamespace
from unittest import TestCase
from uuid import uuid4

from agentforge.contracts.agent_definition import Budget, Permissions
from agentforge.contracts.subagent import RESULT_MODELS, SpawnRequest
from agentforge.repositories.sessions import STEP_LIMIT, tool_steps
from agentforge.subagents.prompt_builder import PLATFORM_INSTRUCTIONS, compile_prompt
from agentforge.subagents.registry import BUILTIN_KEYS, load_builtin
from agentforge.subagents.scope import EffectiveScope
from agentforge.tools.catalog import DESCRIPTIONS, ToolCatalog, clean_schema, error_guidance
from agentforge.tools.contracts import ARGUMENT_MODELS, ToolOutcome

LATEST = {"researcher": 4, "general-executor": 5, "code-reviewer": 3, "code-executor": 1,
          "code-verifier": 1, "verifier": 4, "intent-router": 3}


class DefinitionTests(TestCase):
    def test_every_builtin_has_a_loadable_latest_version_and_the_registry_knows_it(self):
        import inspect
        from agentforge.subagents import registry
        self.assertEqual(set(LATEST), set(BUILTIN_KEYS))
        source = inspect.getsource(registry.AgentRegistry.list_for_task)
        for name, version in LATEST.items():
            self.assertEqual(load_builtin(name, version).version, version)
            self.assertIn(f'"{name}": {version}', source)

    def test_current_prompts_are_structured_and_state_how_their_output_is_used(self):
        for name, version in LATEST.items():
            prompt = load_builtin(name, version).system_prompt
            self.assertTrue(prompt.startswith("# Role"), name)
            self.assertLess(len(prompt), 10000)
        for name in ("researcher", "general-executor"):
            # The summary of these roles is the chat reply, so the prompt must say so.
            self.assertIn("shown to the person", load_builtin(name, LATEST[name]).system_prompt)
        self.assertIn("never", load_builtin("intent-router", 3).system_prompt.lower())
        self.assertIn("exactly those", load_builtin("intent-router", 3).system_prompt)

    def test_coding_roles_use_their_own_definitions_and_tools_stay_least_privilege(self):
        executor = load_builtin("code-executor", 1)
        self.assertEqual(executor.output_schema_id, "execution_result_v1")
        self.assertIn("write_sandbox_file", executor.allowed_tools)
        for name in ("code-reviewer", "code-verifier"):
            tools = load_builtin(name, LATEST[name]).allowed_tools
            self.assertNotIn("write_sandbox_file", tools)
            self.assertNotIn("run_tests", tools)
        self.assertNotIn("run_sandbox_command", load_builtin("general-executor", 5).allowed_tools)

    def test_released_versions_are_untouched(self):
        self.assertTrue(load_builtin("researcher", 3).system_prompt.startswith("Read the entire user goal"))
        self.assertTrue(load_builtin("general-executor", 2).system_prompt.startswith("Complete only the assigned"))


class PromptTests(TestCase):
    def compiled(self):
        definition = load_builtin("researcher", 4)
        ctx = SimpleNamespace(task_id=uuid4())
        budget = Budget(max_steps=5, max_tool_calls=9, max_llm_cost_usd=0.4, max_wall_time_sec=120)
        scope = EffectiveScope(allowed_tools=definition.allowed_tools, permissions=Permissions(), budget=budget, context_refs=())
        request = SpawnRequest(type="builtin/researcher", version=4, prompt="STAGE ASSIGNMENT\nFind the current version.")
        return compile_prompt(definition, request, ctx, scope, {}, definition.allowed_tools)

    def test_platform_rules_cover_priority_tool_discipline_honesty_and_result_contract(self):
        for heading in ("Instruction priority", "How to work", "Honesty and safety", "Result"):
            self.assertIn(heading, PLATFORM_INSTRUCTIONS)
        for rule in ("next_offset", "one tool at a time", "retry at most once", "no code fence", "never instructions"):
            self.assertIn(rule, PLATFORM_INSTRUCTIONS)

    def test_prompt_layers_are_separate_and_carry_schema_date_and_assignment(self):
        developer, user = self.compiled()
        self.assertEqual((developer["role"], user["role"]), ("developer", "user"))
        self.assertIn("RESULT JSON SCHEMA", developer["content"])
        self.assertIn(json.dumps(RESULT_MODELS["research_result_v1"].model_json_schema(), separators=(",", ":")), developer["content"])
        brief = json.loads(user["content"])
        self.assertEqual(list(brief)[:3], ["role", "assignment", "current_date_utc"])
        self.assertRegex(brief["current_date_utc"], r"^\d{4}-\d{2}-\d{2}$")
        self.assertIn("Find the current version", brief["assignment"])
        self.assertEqual(brief["role"]["role_instructions"], load_builtin("researcher", 4).system_prompt)
        self.assertNotIn(brief["role"]["role_instructions"], developer["content"])


class ToolContractTests(TestCase):
    def test_every_tool_has_a_substantive_description_and_documented_parameters(self):
        self.assertEqual(set(DESCRIPTIONS), set(ARGUMENT_MODELS))
        for name, text in DESCRIPTIONS.items():
            self.assertGreater(len(text), 60, name)
            catalog = ToolCatalog()
            async def handler(args, ctx):
                return ToolOutcome(status="ok", cost_usd=Decimal(0))
            catalog.register(name, handler, max_cost_usd=Decimal(0))
            schema = catalog.get(name).provider_schema()["function"]
            self.assertEqual(schema["description"], text)
            for field, spec in schema["parameters"]["properties"].items():
                self.assertTrue(spec.get("description"), f"{name}.{field} lacks a description")

    def test_schema_cleaning_drops_titles_but_keeps_fields_named_title(self):
        schema = clean_schema(ARGUMENT_MODELS["create_task_artifact"].model_json_schema())
        self.assertNotIn("title", {key for key in schema if key != "properties"})
        self.assertIn("title", schema["properties"])
        self.assertNotIn("title", schema["properties"]["title"])

    def test_error_guidance_is_actionable_and_never_empty(self):
        for code in ("SCOPE_DENIED", "SCHEMA_INVALID", "TOOL_UNAVAILABLE", "TOOL_TIMEOUT", "TOOL_FAILED", "SEARCH_PROVIDER_ERROR",
                     "FETCH_BLOCKED", "SOMETHING_NEW", None):
            self.assertGreater(len(error_guidance(code)), 40)
        self.assertIn("Do not retry", error_guidance("SCOPE_DENIED"))
        self.assertNotEqual(error_guidance("FETCH_BLOCKED"), error_guidance("SOMETHING_NEW"))


class StepHistoryTests(TestCase):
    def test_steps_keep_order_names_and_outcome_only(self):
        first, second = uuid4(), uuid4()
        rows = [(first, "web_search", "SUCCEEDED"), (first, "fetch_webpage", "FAILED"), (first, "read_task_input", "BLOCKED"), (second, "git_diff", "SUCCEEDED")]
        session = SimpleNamespace(execute=lambda _query: iter(rows))
        steps = tool_steps(session, {first, second, uuid4()})
        self.assertEqual([(step.tool, step.status) for step in steps[first]], [("web_search", "done"), ("fetch_webpage", "failed"), ("read_task_input", "failed")])
        self.assertEqual([step.tool for step in steps[second]], ["git_diff"])
        self.assertEqual(set(steps[first][0].model_dump()), {"tool", "status"})

    def test_steps_are_bounded_and_empty_without_tasks(self):
        task = uuid4()
        session = SimpleNamespace(execute=lambda _query: iter([(task, "web_search", "SUCCEEDED")] * (STEP_LIMIT + 10)))
        self.assertEqual(len(tool_steps(session, {task})[task]), STEP_LIMIT)
        self.assertEqual(tool_steps(SimpleNamespace(), set()), {})


class ActivityDetailTests(TestCase):
    def test_details_are_short_safe_labels_from_validated_arguments(self):
        from agentforge.repositories.events import _KEYS
        from agentforge.tools.catalog import activity_detail
        from agentforge.tools.contracts import FetchWebpageArgs, RepoFileArgs, TaskInputArgs, WebSearchArgs
        self.assertIn("detail", _KEYS)
        self.assertEqual(activity_detail("web_search", WebSearchArgs(query="  latest\nmodel\tfrom   anthropic ")), "latest model from anthropic")
        self.assertEqual(activity_detail("fetch_webpage", FetchWebpageArgs(source_url="https://www.Anthropic.com/news/x?token=secret")), "www.anthropic.com")
        self.assertEqual(activity_detail("read_repo_file", RepoFileArgs(repo_id="o/r", ref="a" * 40, path="src/app.py")), "src/app.py")
        self.assertEqual(activity_detail("read_task_input", TaskInputArgs(input_kind="history")), "history")
        self.assertEqual(len(activity_detail("web_search", WebSearchArgs(query="x" * 500))), 120)
        self.assertIsNone(activity_detail("unknown_tool", object()))

    def test_event_payload_with_detail_passes_the_event_policy(self):
        from agentforge.repositories.events import append_event
        added = []
        session = SimpleNamespace(add=added.append)
        task = SimpleNamespace(id=uuid4(), event_sequence=0)
        append_event(session, task, "tool.started", {"tool": "web_search", "detail": "latest model"})
        self.assertEqual(added[0].payload["detail"], "latest model")
