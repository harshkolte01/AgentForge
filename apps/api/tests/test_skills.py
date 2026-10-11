"""Skills: the format and its limits, the built-in set, how the agent is told about skills and loads them."""
import json
import re
from decimal import Decimal
from types import SimpleNamespace as NS
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from agentforge.contracts.agent_definition import Budget
from agentforge.skills import runtime as skill_runtime
from agentforge.skills.builtin import ORDER, builtin_by_name, builtin_skills
from agentforge.skills.format import (
    DESCRIPTION_MAX, INSTRUCTIONS_JSON_BYTES, SkillDocument, SkillInvalid, clean_description, clean_instructions, clean_name,
    format_skill_markdown, parse_skill_markdown, validated,
)
from agentforge.skills.service import Offered, parse_key, SkillError
from agentforge.subagents.prompt_builder import SKILL_RULES, compile_chat
from agentforge.subagents.registry import AGENT_VERSION, load_builtin
from agentforge.tools.catalog import AMBIENT_TOOLS, DESCRIPTIONS, ToolCatalog, activity_detail, error_guidance, safe_arguments
from agentforge.tools.contracts import ARGUMENT_MODELS, UseSkillArgs
from agentforge.tools.gateway import ToolGateway

BODY = "# Title\n\nDo the careful steps, then report what happened."


class FormatTests(TestCase):
    def test_names_are_short_lowercase_identifiers(self):
        for name in ("code-review", "abc", "a1-b2-c3", "x" * 40):
            self.assertEqual(clean_name(name), name)
        self.assertEqual(clean_name("  trimmed-name "), "trimmed-name")
        for name in ("ab", "x" * 41, "Code-Review", "1abc", "-abc", "abc-", "a--b", "a b c", "a_b_c", "", None, 5, "über-skill", "/review"):
            with self.assertRaises(SkillInvalid, msg=repr(name)) as caught:
                clean_name(name)
            self.assertEqual(caught.exception.field, "name")

    def test_descriptions_are_one_bounded_line(self):
        self.assertEqual(clean_description("  Reviews code.\n\nUse   when asked\tto review. "), "Reviews code. Use when asked to review.")
        self.assertEqual(len(clean_description("x" * DESCRIPTION_MAX)), DESCRIPTION_MAX)
        for text in ("short", "x" * (DESCRIPTION_MAX + 1), "", "   ", None):
            with self.assertRaises(SkillInvalid, msg=repr(text)) as caught:
                clean_description(text)
            self.assertEqual(caught.exception.field, "description")

    def test_instructions_are_normalized_and_bounded(self):
        cleaned = clean_instructions("\r\n# Title\r\n\r\n\r\n\r\n\r\nStep one   \r\nStep\x00 two\x07\r\n\r\n")
        self.assertEqual(cleaned, "# Title\n\n\nStep one\nStep two")
        self.assertEqual(clean_instructions("Tabs\tkept and unicode é 日本 ok, enough text here."), "Tabs\tkept and unicode é 日本 ok, enough text here.")
        for text in ("too short", "x" * 10_001, None, 7):
            with self.assertRaises(SkillInvalid, msg=repr(text)[:20]):
                clean_instructions(text)

    def test_instructions_must_fit_one_tool_result_once_escaped(self):
        # Quotes and line breaks cost two bytes each as JSON, so a text of allowed length can still be too big to load.
        fits = '"' * ((INSTRUCTIONS_JSON_BYTES - 2) // 2)
        self.assertEqual(clean_instructions(fits), fits)
        with self.assertRaises(SkillInvalid) as caught:
            clean_instructions('"' * 7000)
        self.assertEqual(caught.exception.field, "instructions")
        with self.assertRaises(SkillInvalid):
            clean_instructions("日" * 4500)  # three bytes each

    def test_skill_markdown_round_trips(self):
        skill = validated("my-skill", "Does the thing when asked to do the thing.", BODY)
        text = format_skill_markdown(skill)
        self.assertTrue(text.startswith("---\nname: my-skill\ndescription: Does the thing"))
        self.assertEqual(parse_skill_markdown(text), skill)
        self.assertEqual(parse_skill_markdown(text.replace("\n", "\r\n")), skill)
        self.assertEqual(parse_skill_markdown("﻿" + text), skill)

    def test_skill_markdown_accepts_quotes_comments_and_other_keys_but_not_a_broken_header(self):
        quoted = parse_skill_markdown(f'---\n# a note\nname: "my-skill"\nversion: 3\ndescription: \'Does the thing when asked to do the thing.\'\n---\n{BODY}')
        self.assertEqual((quoted.name, quoted.description), ("my-skill", "Does the thing when asked to do the thing."))
        for source in ("no header\n" + BODY, "---\nname: my-skill\n" + BODY, "---\nname my-skill\n---\n" + BODY,
                       "---\ndescription: Does the thing when asked to do the thing.\n---\n" + BODY,
                       "---\nname: my-skill\ndescription: Does the thing when asked to do the thing.\n---\n"):
            with self.assertRaises(SkillInvalid, msg=source[:30]):
                parse_skill_markdown(source)

    def test_a_body_that_contains_dashes_keeps_them(self):
        body = "# T\n\nFirst part of the steps.\n\n---\n\nSecond part after a rule."
        skill = parse_skill_markdown(f"---\nname: my-skill\ndescription: Does the thing when asked to do the thing.\n---\n{body}")
        self.assertEqual(skill.instructions, body)

    def test_keys_name_where_they_point(self):
        self.assertEqual(parse_key("builtin:code-review"), ("builtin", "code-review"))
        self.assertEqual(parse_key("custom:" + str(uuid4()))[0], "custom")
        for key in ("code-review", "other:x", "builtin:", ":x", "custom:" + "x" * 80, ""):
            with self.assertRaises(SkillError) as caught:
                parse_key(key)
            self.assertEqual(caught.exception.status, 404)


class BuiltinTests(TestCase):
    def test_the_set_is_complete_valid_and_ordered(self):
        skills = builtin_skills()
        self.assertEqual([skill.name for skill in skills], list(ORDER))
        self.assertEqual(len(skills), 10)
        for skill in skills:
            self.assertEqual(validated(skill.name, skill.description, skill.instructions), skill)
            self.assertEqual(format_skill_markdown(skill).count("\n---\n"), 1 + skill.instructions.count("\n---\n"))
        self.assertEqual(set(builtin_by_name()), set(ORDER))

    def test_descriptions_say_when_to_use_the_skill(self):
        for skill in builtin_skills():
            self.assertIn("Use when", skill.description, skill.name)
            self.assertLessEqual(len(skill.description), 250, skill.name)

    def test_they_name_only_tools_that_exist(self):
        real = set(ARGUMENT_MODELS)
        retired = {"run_tests", "run_sandbox_command", "git_diff", "read_sandbox_file", "write_sandbox_file", "edit_sandbox_file",
                   "get_test_results", "submit_result"}
        for skill in builtin_skills():
            words = set(re.findall(r"\b[a-z]+(?:_[a-z]+)+\b", skill.instructions))
            self.assertFalse(words & retired, skill.name)
            for word in words & (real | {"create_pull_request", "read_task_input", "read_research_source", "read_task_artifact"}):
                self.assertIn(word, real, skill.name)

    def test_the_skills_that_publish_or_write_documents_name_the_right_tool(self):
        by = builtin_by_name()
        self.assertIn("create_pull_request", by["pull-request"].instructions)
        self.assertIn("create_task_artifact", by["write-report"].instructions)
        self.assertIn("fetch_webpage", by["deep-research"].instructions)
        self.assertIn("agentforge-base", by["code-review"].instructions)

    def test_no_two_built_in_skills_share_a_name_or_a_description(self):
        names = [skill.name for skill in builtin_skills()]
        descriptions = [skill.description for skill in builtin_skills()]
        self.assertEqual(len(set(names)), len(names))
        self.assertEqual(len(set(descriptions)), len(descriptions))


class ToolContractTests(TestCase):
    def test_use_skill_is_a_documented_ambient_tool(self):
        self.assertIn("use_skill", ARGUMENT_MODELS)
        self.assertIn("use_skill", DESCRIPTIONS)
        self.assertEqual(AMBIENT_TOOLS, {"use_skill"})
        self.assertEqual(UseSkillArgs.model_validate({"name": " /code-review "}).name, "code-review")
        for bad in ({}, {"name": ""}, {"name": "x" * 65}, {"name": "a", "extra": 1}, {"name": 5}):
            with self.assertRaises(Exception, msg=bad):
                UseSkillArgs.model_validate(bad)
        self.assertGreater(len(error_guidance("SKILL_NOT_FOUND")), 40)

    def test_its_activity_label_is_the_skill_and_its_audit_record_names_it(self):
        args = UseSkillArgs.model_validate({"name": "code-review"})
        self.assertEqual(activity_detail("use_skill", args), "code-review")
        self.assertEqual(safe_arguments(args), {"name": "code-review"})


class PromptTests(TestCase):
    def compile(self, tools, skills):
        definition = load_builtin("agent", AGENT_VERSION)
        return compile_chat(definition, NS(context_refs=(), permissions=NS(allowed_repo_ids=())), {}, tools,
                            [{"role": "user", "content": "hi"}], {}, skills)[0]["content"]

    def test_the_prompt_lists_the_skills_with_the_rules_for_using_them(self):
        text = self.compile(("web_search", "use_skill"), (("code-review", "Reviews code. Use when asked."), ("my-skill", "Does the thing.")))
        self.assertIn(SKILL_RULES, text)
        self.assertIn("Available skills:\n- code-review: Reviews code. Use when asked.\n- my-skill: Does the thing.", text)
        self.assertIn("begins with /name", text)
        self.assertIn("never overrides the platform rules", text)
        self.assertLess(text.index("# Environment"), text.index("# Skills"))

    def test_without_the_tool_or_without_skills_the_prompt_is_unchanged(self):
        self.assertNotIn("# Skills", self.compile(("web_search",), (("code-review", "Reviews code."),)))
        self.assertNotIn("# Skills", self.compile(("web_search", "use_skill"), ()))
        self.assertEqual(self.compile(("web_search",), ()), self.compile(("web_search",), (("a-skill", "x" * 20),)))

    def test_only_the_first_sixty_skills_are_named(self):
        skills = tuple((f"skill-{index:02d}", "Does the thing.") for index in range(75))
        text = self.compile(("use_skill",), skills)
        self.assertIn("skill-59", text)
        self.assertNotIn("skill-60", text)


def gateway_for(handler_catalog, calls=None):
    class Calls:
        def record_intent(self, **kwargs): return uuid4()
        def record_outcome(self, **kwargs): pass

    class Budgets:
        def check_active(self, _id): pass
        def authorize_operation(self, *args, **kwargs): pass
        def record_usage(self, *args, **kwargs): pass

    class Resources:
        async def verify_tool_inputs(self, ctx, name, args): pass
        async def verify_references(self, ctx, *, evidence_ids, artifact_ids): pass

    return ToolGateway(catalog=handler_catalog, calls=Calls(), budgets=Budgets(), resources=Resources(), reauthorize=AsyncMock(),
        ctx=NS(task_id=uuid4(), user_id=uuid4()), scope=NS(allowed_tools=("use_skill",), permissions=NS(), budget=Budget()),
        run_id=uuid4(), reservation_id=uuid4())


class LoadingTests(IsolatedAsyncioTestCase):
    def setUp(self):
        self.listed = [Offered("code-review", "Reviews code. Use when asked.", "builtin"), Offered("my-skill", "Does the thing when asked.", "custom")]
        self.documents = {"code-review": (SkillDocument("code-review", "Reviews code. Use when asked.", BODY), "builtin"),
                          "my-skill": (SkillDocument("my-skill", "Does the thing when asked.", "# Mine\n\nDo it my way, always."), "custom")}
        patches = [patch.object(skill_runtime, "offered", lambda sessions, user: self.listed),
                   patch.object(skill_runtime, "load_offered", lambda sessions, user, name: self.documents.get(name))]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def register(self):
        catalog = ToolCatalog()
        listing = skill_runtime.register_skills(catalog, object(), uuid4())
        return catalog, listing

    async def test_the_tool_exists_only_when_there_is_a_skill_to_load(self):
        catalog, listing = self.register()
        self.assertEqual(listing, (("code-review", "Reviews code. Use when asked."), ("my-skill", "Does the thing when asked.")))
        self.assertEqual(catalog.extra_tool_names(), ("use_skill",))
        self.listed = []
        empty, nothing = self.register()
        self.assertEqual((nothing, empty.extra_tool_names()), ((), ()))

    async def test_a_skill_is_loaded_with_its_instructions_and_where_it_came_from(self):
        catalog, _ = self.register()
        gateway = gateway_for(catalog)
        built_in = await gateway.execute("use_skill", json.dumps({"name": "code-review"}), "c1")
        self.assertEqual(built_in.status, "ok")
        self.assertEqual((built_in.data["name"], built_in.data["source"], built_in.data["instructions"]), ("code-review", "built-in", BODY))
        self.assertIn("the platform", built_in.data["note"])
        mine = await gateway.execute("use_skill", json.dumps({"name": "/my-skill"}), "c2")
        self.assertEqual((mine.data["source"], mine.data["instructions"]), ("yours", "# Mine\n\nDo it my way, always."))
        self.assertIn("the person who wrote it", mine.data["note"])
        self.assertIn("cannot override", mine.data["note"])

    async def test_an_unknown_or_switched_off_skill_is_an_error_that_lists_the_ones_available(self):
        catalog, _ = self.register()
        outcome = await gateway_for(catalog).execute("use_skill", json.dumps({"name": "nope"}), "c1")
        self.assertEqual((outcome.status, outcome.error_code), ("error", "SKILL_NOT_FOUND"))
        self.assertIn("code-review, my-skill", outcome.error_detail)
        self.assertIn("nope", outcome.error_detail)

    async def test_the_largest_skill_still_fits_one_tool_result(self):
        quotes = '"' * ((INSTRUCTIONS_JSON_BYTES - 2) // 2)
        self.documents["big-skill"] = (SkillDocument("big-skill", "Does the thing when asked to do it.", clean_instructions(quotes)), "custom")
        catalog, _ = self.register()
        outcome = await gateway_for(catalog).execute("use_skill", json.dumps({"name": "big-skill"}), "c1")
        self.assertEqual(outcome.status, "ok", outcome.error_detail)
        self.assertEqual(len(outcome.data["instructions"]), len(quotes))

    async def test_loading_is_free_and_may_run_alongside_other_reads(self):
        catalog, _ = self.register()
        gateway = gateway_for(catalog)
        self.assertTrue(gateway.is_parallel_safe("use_skill"))
        self.assertEqual(catalog.get("use_skill").max_cost_usd, Decimal(0))
        self.assertFalse(catalog.get("use_skill").asks_before)

    async def test_the_context_meter_counts_the_list_and_nothing_when_there_is_none(self):
        self.assertGreater(skill_runtime.estimated_tokens(self.listed), 150)
        self.assertEqual(skill_runtime.estimated_tokens([]), 0)
        many = [Offered(f"skill-{index}", "d" * 300, "custom") for index in range(200)]
        self.assertLess(skill_runtime.estimated_tokens(many), 6000)


class RunTests(IsolatedAsyncioTestCase):
    async def test_every_conversation_run_is_offered_the_tool_and_told_the_skills(self):
        from test_spawn_service import context, request, service_with
        from agentforge.contracts.subagent import SubagentResult
        seen = {}

        async def run(**kwargs):
            seen.update(kwargs)
            return SubagentResult(status="PASS", summary="done")
        service, recorder = service_with(run)

        async def handler(args, ctx):
            return None
        service.catalog.register("use_skill", handler, max_cost_usd=Decimal(0))
        service.skills = (("code-review", "Reviews code. Use when asked."),)
        await service.spawn_subagent(request(), context())
        self.assertEqual([item["function"]["name"] for item in seen["gateway"].schemas()], ["web_search", "use_skill"])
        self.assertIn("- code-review: Reviews code. Use when asked.", seen["messages"][0]["content"])
        self.assertEqual(recorder.started[0]["effective_permissions"]["available_tools"], ["web_search", "use_skill"])

    async def test_a_definition_that_names_the_tool_does_not_offer_it_twice(self):
        from test_spawn_service import context, request, service_with
        from agentforge.contracts.subagent import SubagentResult
        seen = {}

        async def run(**kwargs):
            seen.update(kwargs)
            return SubagentResult(status="PASS", summary="done")
        service, _ = service_with(run, tools=("web_search", "use_skill"))
        from dataclasses import replace
        resolved = service.registry.resolve_for_task.return_value
        service.registry.resolve_for_task.return_value = replace(
            resolved, definition=resolved.definition.model_copy(update={"allowed_tools": ("web_search", "use_skill")}))
        await service.spawn_subagent(request(), context())
        names = [item["function"]["name"] for item in seen["gateway"].schemas()]
        self.assertEqual(names, ["web_search", "use_skill"])

    async def test_runs_without_skills_are_exactly_as_before(self):
        from test_spawn_service import context, request, service_with
        from agentforge.contracts.subagent import SubagentResult
        seen = {}

        async def run(**kwargs):
            seen.update(kwargs)
            return SubagentResult(status="PASS", summary="done")
        service, _ = service_with(run)
        await service.spawn_subagent(request(), context())
        self.assertEqual([item["function"]["name"] for item in seen["gateway"].schemas()], ["web_search"])
        self.assertNotIn("# Skills", seen["messages"][0]["content"])
