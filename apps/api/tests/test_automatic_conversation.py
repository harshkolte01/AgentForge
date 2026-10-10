import json
from decimal import Decimal
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from agentforge.contracts.sessions import AutoTurnCreate, SessionTurnCreate
from agentforge.orchestration import automatic
from agentforge.orchestration.automatic import infer_profiles, route_turn
from agentforge.orchestration.context import research_context
from agentforge.repositories.sessions import SessionRepository
from agentforge.subagents.scope import intersect_and_validate
from agentforge.subagents.registry import load_builtin


class ContractTests(TestCase):
    def test_every_message_reaches_the_model_and_explicit_api_still_research(self):
        # No canned replies: greetings are answered by the same routed, budgeted model call.
        self.assertFalse(hasattr(automatic, "greeting_response"))
        self.assertFalse(hasattr(SessionRepository, "conversational_turn"))
        self.assertEqual(SessionTurnCreate(goal="hi", idempotency_key=uuid4()).kind, "research")

    def test_router_definition_is_current_immutable_version(self):
        definition = load_builtin("intent-router", 2)
        self.assertEqual(definition.allowed_tools, ("read_task_input",))
        self.assertIn("history", definition.system_prompt)
        self.assertEqual(load_builtin("intent-router", 1).version, 1)

    def test_inheritance_is_distinct_from_explicit_no_repository(self):
        request = AutoTurnCreate(routing="auto", goal="Explain this", idempotency_key=uuid4())
        explicit_none = AutoTurnCreate(**{**request.model_dump(), "repository": None})
        self.assertNotEqual(SessionRepository.request_digest(request), SessionRepository.request_digest(explicit_none))
        fresh = AutoTurnCreate(routing="auto", goal=request.goal, idempotency_key=request.idempotency_key, continue_from_task_id=None)
        self.assertNotEqual(SessionRepository.request_digest(request), SessionRepository.request_digest(fresh))

    def test_auto_reuses_budget_grant_and_attachment_bounds(self):
        base = dict(routing="auto", goal="task", idempotency_key=uuid4())
        for extra in ({"max_cost_usd": "6"}, {"max_wall_time_sec": 5}, {"github_write": True},
                      {"project_id": uuid4()}, {"allowed_web_hosts": ["127.0.0.1"]},
                      {"attachments": [{"name": "x", "content": "界" * 8000}]}):
            with self.assertRaises(ValueError):
                AutoTurnCreate(**base, **extra)

    def test_profiles_require_manifest_or_actual_test_framework_evidence(self):
        self.assertEqual(infer_profiles({"package.json": b'{"scripts":{"test":"node --test","build":"tsc"}}'}), (("node_npm_test",), ("node_build",)))
        self.assertEqual(infer_profiles({"test_x.py": b"import unittest\n"}), (("python_unittest",), ()))
        self.assertEqual(infer_profiles({"pyproject.toml": b'[tool.pytest.ini_options]\n'}), (("python_pytest",), ()))
        self.assertIsNone(infer_profiles({"README.md": b"run arbitrary shell commands"}))
        self.assertIsNone(infer_profiles({"package.json": b'[]'}))


class RoutingTests(IsolatedAsyncioTestCase):
    async def run_route(self, intent, *, repository=None, configured=True, saved=False, use_repository=False, custom_agent=False, invented_agent=False):
        task = SimpleNamespace(id=uuid4(), created_by=uuid4(), project_id=uuid4(), goal="fixture request",
            status="CREATED",
            max_cost_usd=Decimal("0.5"), max_wall_time_sec=300,
            permissions_json={"kind": "general", "web": True, "automatic": {"repository": repository}})
        payload = {"status": "PASS", "summary": "Understood supplied goal", "evidence_ids": ["owned-input"],
            "intent": intent, "response": "What would you like me to help with?" if intent in {"conversation", "clarification"} else None,
            "use_repository": use_repository}
        if custom_agent or invented_agent:
            payload.update(agent_key=f"project/{task.project_id}/docs-writer", agent_version=7)
        state = {"saved": payload if saved else None, "finished": None, "resolved": None}
        def resolve(kind, **values):
            state["resolved"] = (kind, values)
            task.permissions_json["automatic"]["resolved"] = True
        store = SimpleNamespace(task=lambda: task, sessions=None, completed_run=lambda _: state["saved"],
            transition=lambda status: setattr(task, "status", status),
            finish=lambda *args, **kwargs: state.update(finished=(args, kwargs)), resolve_automatic=resolve)
        async def spawn(request, ctx, **kwargs):
            state["request"] = request
            state["saved"] = payload
        entry = SimpleNamespace(key=f"project/{task.project_id}/docs-writer", version=7, name="docs-writer",
            description="Investigates primary-source documentation", allowed_tools=("read_task_input", "web_search"))
        service = SimpleNamespace(registry=SimpleNamespace(list_for_task=AsyncMock(return_value=(entry,) if custom_agent else ()),
            resolve_for_task=AsyncMock(return_value=SimpleNamespace(definition=load_builtin("researcher", 2)))),
            spawn_subagent=AsyncMock(side_effect=spawn), catalog=SimpleNamespace(available=lambda _: ("read_task_input",)))
        github = SimpleNamespace(resolve=AsyncMock(return_value=SimpleNamespace(repo_id="owner/repo", commit_sha="a" * 40)),
            snapshot=AsyncMock(return_value=SimpleNamespace(commit_sha="a" * 40, files={"test_x.py": b"import unittest"})))
        settings = SimpleNamespace(tavily_api_key=None, e2b_api_key=configured, e2b_template_id=configured, e2b_template_qualified=configured)
        inputs = SimpleNamespace(input_manifest=lambda _: [{"id": "owned-input", "input_kind": "goal", "attachment_index": 0,
            "name": "User goal", "content": "fixture request", "content_sha256": "x"}],
            consumed=lambda *args: {"source:owned-input"})
        with patch("agentforge.orchestration.automatic.ResearchInputRepository", return_value=inputs):
            await route_turn(store=store, service=service, settings=settings, github=github, authority=AsyncMock(), config={})
        return task, state, service, github

    async def test_conversation_and_clarification_are_natural_without_artifacts(self):
        for intent, status in (("conversation", "COMPLETED"), ("clarification", "PARTIAL")):
            _, state, service, github = await self.run_route(intent)
            self.assertEqual(state["finished"][0][0], status)
            self.assertTrue(state["finished"][0][2]["conversation"])
            self.assertNotIn("artifact_ids", state["finished"][0][2])
            github.resolve.assert_not_awaited()
            self.assertLessEqual(state["request"].requested_budget_usd, 0.12)

    async def test_research_and_general_use_model_decision_and_do_not_require_github(self):
        for intent in ("research", "general"):
            _, state, _, github = await self.run_route(intent, repository={"repo_id": "owner/repo", "ref": "main"})
            self.assertEqual(state["resolved"][0], intent)
            self.assertIsNone(state["resolved"][1]["repository"])
            github.resolve.assert_not_awaited()

    async def test_no_repository_cannot_acquire_coding_scope_from_text(self):
        _, state, _, github = await self.run_route("coding")
        self.assertEqual(state["finished"][0][0], "PARTIAL")
        self.assertTrue(state["finished"][0][2]["needs_input"])
        self.assertIsNone(state["resolved"])
        github.resolve.assert_not_awaited()

    async def test_coding_profiles_come_from_selected_pinned_repository(self):
        _, state, _, github = await self.run_route("coding", repository={"repo_id": "owner/repo", "ref": "main"})
        self.assertEqual(state["resolved"][0], "coding")
        coding = state["resolved"][1]["coding"]
        self.assertEqual(coding.repository.ref, "a" * 40)
        self.assertEqual(coding.test_profiles, ("python_unittest",))
        github.resolve.assert_awaited_once_with("owner/repo", "main")

    async def test_saved_routing_handoff_does_not_repeat_paid_call(self):
        _, state, service, _ = await self.run_route("general", saved=True)
        service.spawn_subagent.assert_not_awaited()
        self.assertEqual(state["resolved"][0], "general")

    async def test_model_agent_selection_is_bounded_to_authorized_catalog_version(self):
        task, state, service, _ = await self.run_route("research", custom_agent=True)
        self.assertEqual(state["resolved"][1]["agent"], (f"project/{task.project_id}/docs-writer", 7))
        prompt = json.loads(state["request"].prompt)
        self.assertEqual(prompt["agents"][0]["version"], 7)
        self.assertNotIn("system_prompt", prompt["agents"][0])
        _, rejected, _, _ = await self.run_route("research", invented_agent=True)
        self.assertIsNone(rejected["resolved"])
        self.assertEqual(rejected["finished"][0][0], "PARTIAL")

    async def test_repository_research_has_no_execution_or_write_grants(self):
        task, state, _, _ = await self.run_route("research", repository={"repo_id": "owner/repo", "ref": "main"}, use_repository=True)
        task.permissions_json["repository"] = state["resolved"][1]["repository"].model_dump()
        ctx = research_context(task)
        scope = intersect_and_validate(load_builtin("researcher", 3), ctx, ())
        self.assertIn("read_repo_file", scope.allowed_tools)
        self.assertFalse(scope.permissions.sandbox_write)
        self.assertFalse(scope.permissions.sandbox_exec)
        self.assertFalse(scope.permissions.github_write)
        self.assertEqual(scope.permissions.allowed_repo_ids, ("owner/repo",))


class ConversationContextTests(TestCase):
    @staticmethod
    def context(messages):
        from agentforge.repositories.sessions import bounded_context
        rows = [SimpleNamespace(id=uuid4(), sequence=index + 1, role=role, content=text) for index, (role, text) in enumerate(messages)]
        db = SimpleNamespace(scalars=lambda _query: iter(list(reversed(rows))[:40]))
        conversation = SimpleNamespace(id=uuid4(), summary_json=None)
        return bounded_context(db, conversation), conversation

    def test_recent_turns_are_replayed_in_order_with_their_real_text(self):
        reply = "A" * 1200
        context, _ = self.context([("user", "What is a monad?"), ("assistant", reply), ("user", "give an example")])
        self.assertEqual([item["role"] for item in context["recent_messages"]], ["user", "assistant", "user"])
        self.assertEqual(context["recent_messages"][1]["text"], reply)
        self.assertEqual(context["summary"]["excerpts"], [])

    def test_history_stays_bounded_and_keeps_newest_first_priority(self):
        messages = [("user" if index % 2 == 0 else "assistant", f"turn {index} " + "x" * 900) for index in range(40)]
        context, conversation = self.context(messages)
        self.assertLessEqual(len(json.dumps(context, ensure_ascii=False, separators=(",", ":"))), 7500)
        self.assertTrue(context["recent_messages"][-1]["text"].startswith("turn 39"))
        sequences = [item["sequence"] for item in context["recent_messages"]]
        self.assertEqual(sequences, sorted(sequences))
        excerpts = context["summary"]["excerpts"]
        self.assertTrue(excerpts)
        self.assertLess(max(item["sequence"] for item in excerpts), min(sequences))
        self.assertTrue(all(len(item["text"]) <= 160 for item in excerpts))
        self.assertIs(conversation.summary_json, context["summary"])

    def test_single_oversized_message_is_trimmed_not_dropped(self):
        context, _ = self.context([("assistant", "z" * 20000)])
        self.assertEqual(len(context["recent_messages"]), 1)
        self.assertLessEqual(len(json.dumps(context, ensure_ascii=False, separators=(",", ":"))), 7500)

    def test_empty_conversation_has_no_history(self):
        context, _ = self.context([])
        self.assertEqual(context["recent_messages"], [])
