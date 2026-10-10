"""Model-facing schemas, audit records, error guidance and conversation compaction."""
import json
from decimal import Decimal
from unittest import TestCase

from pydantic import TypeAdapter

from agentforge.contracts.agent_definition import TOOL_CAPABILITIES
from agentforge.subagents.context import RESULT_PREFIX, compact_messages, conversation_bytes
from agentforge.tools.catalog import (
    BINDABLE_FIELDS, DESCRIPTIONS, EFFECT_TOOLS, GENERIC_ERROR_GUIDANCE, ToolCatalog, error_guidance, safe_arguments,
)
from agentforge.tools.contracts import ARGUMENT_MODELS, SandboxWriteArgs, ToolOutcome, ToolRequest
from agentforge.tools.schema import drop_fields, to_strict_schema

# Keywords strict structured outputs may reject; the strict form must not contain them.
BANNED = {"minLength", "maxLength", "minItems", "maxItems", "uniqueItems", "default", "title"}


def walk(node):
    """Every schema node. A "properties" map holds field names (which may be called "title"), not keywords."""
    if isinstance(node, dict):
        yield node
        for key, value in node.items():
            if key == "properties":
                for spec in value.values():
                    yield from walk(spec)
            else:
                yield from walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from walk(value)


def catalog_with(name):
    catalog = ToolCatalog()
    async def handler(args, ctx):
        return ToolOutcome(status="ok", cost_usd=Decimal(0))
    catalog.register(name, handler, max_cost_usd=Decimal(0))
    return catalog.get(name)


class StrictSchemaTests(TestCase):
    def test_every_tool_converts_to_a_strict_schema_with_all_fields_required_and_optionals_nullable(self):
        for name, model in ARGUMENT_MODELS.items():
            strict = catalog_with(name).provider_schema(strict=True)["function"]
            self.assertTrue(strict["strict"], name)
            schema = strict["parameters"]
            self.assertEqual(list(schema["properties"]), schema["required"], name)
            self.assertIs(schema["additionalProperties"], False, name)
            for node in walk(schema):
                self.assertFalse(BANNED & set(node), f"{name}: {BANNED & set(node)}")
            for field, spec in model.model_fields.items():
                if not spec.is_required():
                    options = schema["properties"][field].get("anyOf", [])
                    self.assertIn({"type": "null"}, options, f"{name}.{field} must be nullable in strict form")

    def test_dropped_bounds_move_into_the_description_instead_of_vanishing(self):
        strict = catalog_with("web_search").provider_schema(strict=True)["function"]["parameters"]
        self.assertIn("at most 2000 characters", strict["properties"]["query"]["description"])

    def test_non_strict_schemas_are_unchanged_by_the_option(self):
        plain = catalog_with("read_repo_file").provider_schema()["function"]
        self.assertNotIn("strict", plain)
        self.assertEqual(plain["parameters"]["properties"]["max_lines"]["maximum"], 200)

    def test_hiding_fields_removes_them_from_properties_and_required_only(self):
        full = catalog_with("read_repo_file").provider_schema()["function"]["parameters"]
        hidden = catalog_with("read_repo_file").provider_schema(hide={"repo_id", "ref"})["function"]["parameters"]
        self.assertEqual(set(full["properties"]) - set(hidden["properties"]), {"repo_id", "ref"})
        self.assertEqual(hidden["required"], ["path"])
        self.assertIn("repo_id", full["required"])  # the original is not mutated
        self.assertEqual(drop_fields({"properties": {"a": {}}, "required": ["a"]}, {"a"}), {"properties": {}})
        schema = {"properties": {"a": {}}}
        self.assertIs(drop_fields(schema, set()), schema)

    def test_strict_conversion_does_not_mutate_its_input(self):
        original = {"type": "object", "properties": {"a": {"type": "string", "maxLength": 3}}}
        before = json.dumps(original)
        to_strict_schema(original)
        self.assertEqual(json.dumps(original), before)


class ContractCoverageTests(TestCase):
    def test_every_tool_is_covered_by_the_request_union_descriptions_and_capabilities(self):
        union = TypeAdapter(ToolRequest)
        self.assertEqual(set(DESCRIPTIONS), set(ARGUMENT_MODELS))
        self.assertEqual(set(TOOL_CAPABILITIES), set(ARGUMENT_MODELS))
        for name in ARGUMENT_MODELS:
            self.assertTrue(any(name in str(member) for member in union.core_schema["choices"]), name)
        self.assertTrue(EFFECT_TOOLS <= set(ARGUMENT_MODELS))
        self.assertIn("edit_sandbox_file", EFFECT_TOOLS)

    def test_bindable_identifiers_are_only_ones_the_task_fixes(self):
        self.assertEqual(BINDABLE_FIELDS, {"repo_id", "ref", "base_ref", "sandbox_id", "task_id"})

    def test_every_error_code_the_system_emits_has_specific_guidance(self):
        codes = ["SCOPE_DENIED", "SCHEMA_INVALID", "TOOL_UNAVAILABLE", "TOOL_TIMEOUT", "TOOL_FAILED", "FILE_NOT_FOUND",
                 "BINARY_FILE", "INVALID_RANGE", "LINE_TOO_LONG", "STALE_WRITE", "FILE_EXISTS", "EDIT_NO_MATCH",
                 "EDIT_AMBIGUOUS", "PROFILE_MISMATCH", "DUPLICATE_CALL", "INVALID_ARGUMENT", "SEARCH_PROVIDER_ERROR",
                 "SANDBOX_CONFIGURATION_REQUIRED"]
        for code in codes:
            self.assertNotEqual(error_guidance(code), GENERIC_ERROR_GUIDANCE, code)
        self.assertEqual(error_guidance("SOMETHING_NEW"), GENERIC_ERROR_GUIDANCE)

    def test_the_new_edit_tool_parses_through_the_discriminated_union(self):
        request = TypeAdapter(ToolRequest).validate_python({"name": "edit_sandbox_file", "args": {
            "sandbox_id": "s", "path": "a.py", "old_string": "x", "new_string": ""}})
        self.assertFalse(request.args.replace_all)


class AuditRecordTests(TestCase):
    def test_free_text_and_file_content_never_enter_the_record(self):
        args = SandboxWriteArgs(sandbox_id="s", path="src/a.py", content="API_KEY=hunter2", expected_sha256="a" * 64)
        record = safe_arguments(args)
        self.assertEqual(record["path"], "src/a.py")
        self.assertEqual(record["content_chars"], len("API_KEY=hunter2"))
        self.assertNotIn("hunter2", json.dumps(record))

    def test_urls_are_reduced_to_their_host_and_queries_to_a_length(self):
        from agentforge.tools.contracts import FetchWebpageArgs, WebSearchArgs
        self.assertEqual(safe_arguments(FetchWebpageArgs(source_url="https://example.com/private/path?token=abc")), {"host": "example.com"})
        record = safe_arguments(WebSearchArgs(query="confidential acquisition target", max_results=3))
        self.assertEqual(record, {"query_chars": 31, "max_results": 3})


def result_message(tool, size, ids=("e1",), status="ok"):
    body = {"tool": tool, "status": status, "data": {"content": "x" * size}, "evidence_ids": list(ids), "artifact_ids": []}
    return {"role": "tool", "tool_call_id": f"id-{tool}-{size}", "content": RESULT_PREFIX + json.dumps(body)}


class CompactionTests(TestCase):
    def conversation(self, count, size=2000):
        messages = [{"role": "developer", "content": "rules"}, {"role": "user", "content": "brief"}]
        for index in range(count):
            messages.append({"role": "assistant", "content": None, "tool_calls": [{"id": f"id-t{index}-{size}"}]})
            messages.append(result_message(f"t{index}", size, ids=(f"e{index}",)))
        return messages

    def test_a_conversation_that_fits_is_returned_untouched(self):
        messages = self.conversation(3)
        self.assertIs(compact_messages(messages, 1_000_000), messages)
        self.assertIs(compact_messages(messages, None), messages)

    def test_oldest_results_are_stubbed_first_with_ids_kept_and_the_recent_ones_whole(self):
        messages = self.conversation(8)
        limit = 12000
        compacted = compact_messages(messages, limit)
        self.assertLessEqual(conversation_bytes(compacted), limit)
        results = [m for m in compacted if m["role"] == "tool"]
        flags = ["compacted" in m["content"] for m in results]
        self.assertEqual(flags, sorted(flags, reverse=True))  # oldest compacted, newest whole
        self.assertFalse(any(flags[-4:]))
        first = json.loads(results[0]["content"][len(RESULT_PREFIX):])
        self.assertEqual((first["tool"], first["evidence_ids"], first["compacted"]), ("t0", ["e0"], True))
        self.assertNotIn("content", first.get("data", {}))

    def test_structure_is_preserved_so_every_tool_call_keeps_its_reply(self):
        messages = self.conversation(8)
        compacted = compact_messages(messages, 5000)
        self.assertEqual([m["role"] for m in compacted], [m["role"] for m in messages])
        self.assertEqual([m.get("tool_call_id") for m in compacted], [m.get("tool_call_id") for m in messages])

    def test_the_stored_conversation_is_never_modified(self):
        messages = self.conversation(8)
        before = json.dumps(messages)
        compact_messages(messages, 5000)
        self.assertEqual(json.dumps(messages), before)

    def test_only_what_is_needed_is_compacted(self):
        messages = self.conversation(8)
        total = conversation_bytes(messages)
        compacted = compact_messages(messages, total - 1500)
        self.assertEqual(sum("compacted" in m.get("content", "") for m in compacted if m["role"] == "tool"), 1)

    def test_small_or_foreign_tool_messages_are_left_alone(self):
        messages = self.conversation(6, size=10)
        messages[3] = {"role": "tool", "tool_call_id": "x", "content": "RESULT REJECTED: fix summary"}
        self.assertEqual(compact_messages(messages, 100), messages)
