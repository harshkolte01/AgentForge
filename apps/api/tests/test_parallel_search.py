"""Keyless search through selection, MCP transport, gateway and task-owned source consumption."""
import asyncio
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from contextlib import contextmanager
from decimal import Decimal
from types import SimpleNamespace as NS
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx

from agentforge.contracts.agent_definition import Budget, Permissions
from agentforge.integrations.parallel_search import ENDPOINT, ParallelSearchClient
from agentforge.integrations.web_search import SearchFailure, provider_names
from agentforge.plugins import mcp
from agentforge.settings import Settings
from agentforge.subagents.scope import EffectiveScope, ScopeDenied
from agentforge.tools import research
from agentforge.tools.catalog import ToolCatalog
from agentforge.tools.gateway import ToolGateway


class SelectionTests(TestCase):
    def test_saved_disable_and_environment_override_keep_settings_precedence(self):
        with TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            saved = Path(directory) / ".env"
            saved.write_text("WEB_SEARCH_PROVIDER=none\nEXA_API_KEY=exa-key-0001\nTAVILY_API_KEY=tavily-key-0001\n")
            self.assertEqual(provider_names(Settings(_env_file=saved)), ())
            with patch.dict(os.environ, {"WEB_SEARCH_PROVIDER": "tavily"}):
                self.assertEqual(provider_names(Settings(_env_file=saved)), ("tavily",))

    def test_fresh_no_key_settings_and_explicit_choices(self):
        with patch.dict(os.environ, {}, clear=True):
            fresh = Settings(_env_file=None)
        self.assertEqual(provider_names(fresh), ("parallel",))
        self.assertIsInstance(research.search_providers(fresh)[0][1], ParallelSearchClient)
        for choice, expected in (("auto", ("exa", "tavily")), ("parallel", ("parallel",)),
                                 ("exa", ("exa",)), ("tavily", ("tavily",)), ("none", ())):
            settings = Settings(_env_file=None, web_search_provider=choice, exa_api_key="exa-key-0001", tavily_api_key="tavily-key-0001")
            self.assertEqual(tuple(name for name, _ in research.search_providers(settings)), expected)
        for choice in ("exa", "tavily"):
            self.assertEqual(provider_names(Settings(_env_file=None, web_search_provider=choice)), ())


class ConversationIdentityTests(TestCase):
    def test_owned_turns_share_their_conversation_and_standalone_tasks_use_the_task_id(self):
        from agentforge.repositories.research import ResearchRepository
        db = MagicMock()
        sessions = MagicMock()
        sessions.return_value.__enter__.return_value = db
        repository = ResearchRepository(sessions)
        conversation = uuid4()
        db.scalar.side_effect = [conversation, conversation, None]
        tasks = [NS(task_id=uuid4(), user_id=uuid4(), project_id=uuid4()) for _ in range(3)]
        with patch.object(repository, "_owned") as owned:
            self.assertEqual([repository.search_session_id(t) for t in tasks],
                             [str(conversation), str(conversation), str(tasks[2].task_id)])
        for task, own, query in zip(tasks, owned.call_args_list, db.scalar.call_args_list):
            self.assertEqual(own.kwargs, {"task_id": task.task_id, "owner_id": task.user_id, "project_id": task.project_id})
            self.assertIn(task.task_id, query.args[0].compile().params.values())
        db.scalar.reset_mock()
        with patch.object(repository, "_owned", side_effect=ScopeDenied("not owned")), self.assertRaises(ScopeDenied):
            repository.search_session_id(tasks[0])
        db.scalar.assert_not_called()


class Sources:
    def __init__(self):
        self.saved = []
        self.session_id = str(uuid4())

    def search_session_id(self, task):
        return self.session_id

    def add_source(self, task, **fields):
        self.saved.append(fields)
        return {"id": str(uuid4()), "url": fields["url"], "title": fields["title"], "excerpt": fields["content"]}


class Calls:
    def __init__(self):
        self.outcomes = []

    def record_intent(self, **kwargs):
        return uuid4()

    def record_outcome(self, **kwargs):
        self.outcomes.append(kwargs)


def gateway(*, settings=None, hosts=(), allowed=True, discovery=True):
    settings = settings or Settings(_env_file=None, exa_api_key=None, tavily_api_key=None, llm_model="fixture/model")
    sources, catalog, calls = Sources(), ToolCatalog(), Calls()
    with patch.object(research, "ResearchRepository", return_value=sources):
        research.register_research(catalog, None, settings)
    resources = NS(verify_tool_inputs=AsyncMock(), verify_references=AsyncMock())
    budgets = NS(check_active=lambda *a: None, authorize_operation=lambda *a, **kw: None, record_usage=lambda *a, **kw: None)
    task = NS(task_id=uuid4(), parent_run_id=None, source_discovery_enabled=discovery)
    scope = EffectiveScope(Permissions(web=allowed, allowed_web_hosts=hosts), ("web_search",) if allowed else (), Budget(), ())
    return ToolGateway(catalog=catalog, calls=calls, budgets=budgets, resources=resources, reauthorize=AsyncMock(),
                       ctx=task, scope=scope, run_id=uuid4(), reservation_id=uuid4()), sources, calls


class Server:
    def __init__(self, *, sse=False, status=200, error=False, text_only=False, malformed=False, results=None):
        self.sse, self.status, self.error, self.text_only, self.malformed = sse, status, error, text_only, malformed
        self.requests = []
        self.results = results if results is not None else [
            {"url": "https://docs.example.com/guide", "title": "Guide", "excerpts": ["Useful excerpt", "Second excerpt"]}]

    async def handle(self, request):
        self.requests.append(request)
        if self.status != 200:
            return httpx.Response(self.status, text="private provider error", request=request)
        if request.method == "DELETE":
            return httpx.Response(200, request=request)
        body = json.loads(request.content)
        method = body["method"]
        if method == "notifications/initialized":
            return httpx.Response(202, request=request)
        if method == "initialize":
            result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}, "serverInfo": {"name": "fixture"}}
        elif method == "tools/list":
            result = {"tools": [{"name": "web_search", "inputSchema": {"type": "object"}}]}
        else:
            data = {"results": self.results}
            result = {"content": [{"type": "text", "text": "private error" if self.error else "not JSON" if self.malformed else json.dumps(data)}],
                      "isError": self.error}
            if not (self.text_only or self.malformed):
                result["structuredContent"] = data
        payload = {"jsonrpc": "2.0", "id": body["id"], "result": result}
        headers = {"mcp-session-id": "fixture-session"}
        if self.sse:
            return httpx.Response(200, headers={**headers, "content-type": "text/event-stream"},
                                  content=f"data: {json.dumps(payload)}\n\n".encode(), request=request)
        return httpx.Response(200, headers=headers, json=payload, request=request)


@contextmanager
def transport(server):
    real = httpx.AsyncClient
    target = mcp.Target(ENDPOINT, "search.parallel.ai", 443, True, ("93.184.216.34",))
    with patch.object(mcp, "resolve_target", AsyncMock(return_value=target)) as resolver, \
            patch.object(mcp.httpx, "AsyncClient", lambda **kw: real(**{**kw, "transport": httpx.MockTransport(server.handle)})):
        yield resolver


async def search(gw, call="c1"):
    return await gw.execute("web_search", json.dumps({"query": "current Python release notes", "max_results": 3}), call)


class CallerTests(IsolatedAsyncioTestCase):
    async def test_configured_provider_failure_does_not_switch_to_parallel(self):
        for choice in ("auto", "exa"):
            client = NS(search=AsyncMock(side_effect=SearchFailure("SEARCH_PROVIDER_ERROR")))
            settings = Settings(_env_file=None, exa_api_key="exa-key-0001", tavily_api_key=None, web_search_provider=choice)
            with patch.object(research, "ExaClient", return_value=client), patch.object(research, "ParallelSearchClient") as parallel:
                gw, sources, _ = gateway(settings=settings)
                with self.assertLogs(research.logger, "WARNING"):
                    result = await search(gw)
            self.assertEqual(result.error_code, "SEARCH_PROVIDER_ERROR")
            self.assertEqual(sources.saved, [])
            parallel.assert_not_called()

    async def test_normal_dispatch_consumes_sources_over_json_and_sse_without_credentials(self):
        for sse in (False, True):
            server = Server(sse=sse)
            gw, sources, calls = gateway()
            with patch.dict(os.environ, {"PARALLEL_API_KEY": "must-not-be-used"}), transport(server) as resolver:
                result = await search(gw)
            self.assertEqual((result.status, result.data["provider"], result.cost_usd), ("ok", "parallel", Decimal(0)))
            self.assertEqual(result.data["billing"], "free_anonymous")
            self.assertEqual(result.data["sources"][0]["excerpt"], "Useful excerpt … Second excerpt")
            self.assertEqual(result.evidence_ids, (result.data["sources"][0]["id"],))
            self.assertEqual(sources.saved[0]["fetch_status"], "SEARCH_ONLY")
            self.assertEqual(calls.outcomes[-1]["status"], "SUCCEEDED")
            for call in resolver.call_args_list:
                self.assertEqual(call.args, (ENDPOINT,))
                self.assertEqual(call.kwargs, {"allow_private": False})
            for request in server.requests:
                self.assertEqual(str(request.url), "https://93.184.216.34/mcp")
                self.assertEqual(request.headers["host"], "search.parallel.ai")
                self.assertEqual(request.extensions["sni_hostname"], "search.parallel.ai")
                self.assertEqual(request.headers["user-agent"], "AgentForge/0.1.0")
                self.assertNotIn("authorization", request.headers)
                self.assertNotIn("x-api-key", request.headers)
                self.assertNotIn("cookie", request.headers)
            body = next(json.loads(r.content) for r in server.requests if r.method == "POST" and json.loads(r.content)["method"] == "tools/call")
            self.assertEqual(body["params"], {"name": "web_search", "arguments": {
                "objective": "current Python release notes", "search_queries": ["current Python release notes"],
                "session_id": sources.session_id, "model_name": "fixture/model"}})

    async def test_discovery_uses_the_project_user_agent_and_same_endpoint(self):
        server = Server()
        with transport(server):
            c = mcp.McpClient(ENDPOINT)
            self.assertEqual((await c.list_tools())[0]["name"], "web_search")
            await c.aclose()
        self.assertTrue(any(r.method == "POST" and json.loads(r.content)["method"] == "tools/list" for r in server.requests))
        self.assertTrue(all(r.headers["user-agent"] == "AgentForge/0.1.0" for r in server.requests))

    async def test_text_only_result_and_repeated_calls_keep_the_same_conversation_identity(self):
        server, (gw, sources, _) = Server(text_only=True), gateway()
        with transport(server):
            self.assertEqual((await search(gw)).status, "ok")
            self.assertEqual((await search(gw, "c2")).status, "ok")
        ids = [json.loads(r.content)["params"]["arguments"]["session_id"] for r in server.requests
               if r.method == "POST" and json.loads(r.content)["method"] == "tools/call"]
        self.assertEqual(ids, [sources.session_id, sources.session_id])

    async def test_host_url_and_discovery_restrictions_apply_before_saving(self):
        results = [{"url": url, "title": "T", "excerpts": ["snippet"]} for url in (
            "https://elsewhere.test/page", "https://docs.example.com/guide", "https://user:secret@docs.example.com/page")]
        for hosts, discovery, expected in ((("docs.example.com",), False, 1), ((), False, 0)):
            server = Server(results=results)
            gw, sources, _ = gateway(hosts=hosts, discovery=discovery)
            with transport(server):
                result = await search(gw)
            self.assertEqual(result.status, "ok")
            self.assertEqual(len(sources.saved), expected)
            if hosts:
                body = next(json.loads(r.content) for r in server.requests
                            if r.method == "POST" and json.loads(r.content)["method"] == "tools/call")
                self.assertIn("site:docs.example.com", body["params"]["arguments"]["search_queries"][0])
        server = Server(results=[{"url": "https://[bad", "excerpts": []}])
        gw, sources, _ = gateway()
        with transport(server):
            self.assertEqual((await search(gw)).status, "ok")
        self.assertEqual(sources.saved, [])

    async def test_disabled_or_ungranted_search_never_reaches_the_transport(self):
        for settings, allowed in ((None, False), (Settings(_env_file=None, web_search_provider="none"), True)):
            gw, sources, _ = gateway(settings=settings, allowed=allowed)
            server = Server()
            with transport(server):
                self.assertEqual((await search(gw)).status, "error")
            self.assertEqual((server.requests, sources.saved), ([], []))

    async def test_remote_failures_and_malformed_answers_stay_safe(self):
        for server in (Server(status=401), Server(status=429), Server(status=500), Server(error=True), Server(malformed=True)):
            gw, sources, _ = gateway()
            with transport(server), self.assertLogs(research.logger, "WARNING") as logs:
                result = await search(gw)
            self.assertEqual((result.status, result.error_code), ("error", "SEARCH_PROVIDER_ERROR"))
            self.assertEqual(result.cost_usd, Decimal(0))
            self.assertEqual(sources.saved, [])
            self.assertNotIn("private", str(result) + str(logs.output))

    async def test_timeout_and_cancellation_leave_no_sources_and_close_the_session(self):
        for cancel in (False, True):
            started, closed = asyncio.Event(), asyncio.Event()
            class Slow(Server):
                async def handle(self, request):
                    if request.method == "DELETE":
                        closed.set()
                    elif json.loads(request.content)["method"] == "tools/call":
                        started.set()
                        await asyncio.Event().wait()
                    return await super().handle(request)
            gw, sources, calls = gateway()
            # Keep selection and construction real; shorten only this client's deadline.
            with transport(Slow()), patch.object(research, "SOLE_PROVIDER_SEC", 0.05):
                # Providers are built at registration, so apply the deadline to the existing instance via a new gateway.
                gw, sources, calls = gateway()
                task = asyncio.create_task(search(gw))
                await started.wait()
                if cancel:
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                    self.assertEqual(calls.outcomes[-1]["error_code"], "USER_CANCELLED")
                else:
                    with self.assertLogs(research.logger, "WARNING"):
                        result = await task
                    self.assertEqual(result.error_code, "SEARCH_PROVIDER_ERROR")
            self.assertEqual(sources.saved, [])
            self.assertTrue(closed.is_set())
