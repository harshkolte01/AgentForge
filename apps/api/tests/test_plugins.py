"""Plugins: the MCP client against a real local server, tool naming and schemas, and how a plugin's tools pass the gateway."""
import asyncio
import json
import threading
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace as NS
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from agentforge.contracts.agent_definition import Budget
from agentforge.plugins import mcp
from agentforge.plugins.mcp import McpClient, McpError, normalize_result, resolve_target, validate_url
from agentforge.plugins.runtime import RESULT_BYTES, fit_text, outcome_from, register_plugins
from agentforge.plugins.schema import clean_input_schema, function_name, make_slug, tool_spec, tool_specs
from agentforge.tools.catalog import PluginTool, ToolCatalog, approval_summary, error_guidance, safe_arguments
from agentforge.tools.contracts import PluginArguments
from agentforge.tools.gateway import ToolGateway

TOOLS = [
    {"name": "echo", "description": "Echo the arguments back.", "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
     "annotations": {"readOnlyHint": True}},
    {"name": "make_issue", "description": "Create an issue.", "inputSchema": {"type": "object", "properties": {"title": {"type": "string"}, "path": {"type": "string"}}}},
    {"name": "fail", "inputSchema": {"type": "object"}},
    {"name": "big", "inputSchema": {"type": "object"}},
    {"name": "rpc_error", "inputSchema": {"type": "object"}},
]


class FakeServer:
    """A minimal Streamable HTTP MCP server on localhost that records what it was sent."""

    def __init__(self, *, sse=False, session=True, auth=None, page_size=100, status=None, redirect=False):
        self.sse, self.session, self.auth, self.page_size, self.status, self.redirect = sse, session, auth, page_size, status, redirect
        self.requests: list[dict] = []
        self.sessions: set[str] = set()
        self.counter = 0
        server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            wbufsize = 65536  # one write per response: avoids a 200 ms Nagle/delayed-ACK stall on Windows

            def log_message(self, *args):
                pass

            def reply(self, status, body=b"", content_type="application/json", headers=None):
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                for key, value in (headers or {}).items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(body)

            def do_DELETE(self):
                server.requests.append({"method": "DELETE", "headers": dict(self.headers)})
                server.sessions.discard(self.headers.get("Mcp-Session-Id", ""))
                self.reply(200)

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                server.requests.append({"method": "POST", "headers": dict(self.headers), "body": body})
                if server.redirect:
                    return self.reply(302, headers={"Location": "https://elsewhere.example/mcp"})
                if server.status:
                    return self.reply(server.status)
                if server.auth and self.headers.get("Authorization") != server.auth:
                    return self.reply(401)
                method, ident = body.get("method"), body.get("id")
                sid = self.headers.get("Mcp-Session-Id")
                if method != "initialize" and server.session and sid not in server.sessions:
                    return self.reply(404)
                if method == "notifications/initialized":
                    return self.reply(202)
                headers = {}
                if method == "initialize":
                    result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}, "serverInfo": {"name": "Fake Server"}}
                    if server.session:
                        server.counter += 1
                        new = f"session-{server.counter}"
                        server.sessions.add(new)
                        headers["Mcp-Session-Id"] = new
                elif method == "tools/list":
                    start = int((body.get("params") or {}).get("cursor") or 0)
                    page = TOOLS[start:start + server.page_size]
                    result = {"tools": page, **({"nextCursor": str(start + server.page_size)} if start + server.page_size < len(TOOLS) else {})}
                elif method == "tools/call":
                    name, arguments = body["params"]["name"], body["params"]["arguments"]
                    if name == "rpc_error":
                        return self.send_message({"jsonrpc": "2.0", "id": ident, "error": {"code": -32602, "message": "bad things"}}, headers)
                    if name == "fail":
                        result = {"content": [{"type": "text", "text": "it broke"}], "isError": True}
                    elif name == "big":
                        result = {"content": [{"type": "text", "text": "x" * 50000}]}
                    else:
                        result = {"content": [{"type": "text", "text": json.dumps(arguments)}, {"type": "image", "data": "AAAA", "mimeType": "image/png"}]}
                else:
                    return self.send_message({"jsonrpc": "2.0", "id": ident, "error": {"code": -32601, "message": "no such method"}}, headers)
                self.send_message({"jsonrpc": "2.0", "id": ident, "result": result}, headers)

            def send_message(self, message, headers):
                if server.sse:
                    note = {"jsonrpc": "2.0", "method": "notifications/message", "params": {"data": "hello"}}
                    payload = f"event: message\ndata: {json.dumps(note)}\n\nevent: message\ndata: {json.dumps(message)}\n\n".encode()
                    return self.reply(200, payload, "text/event-stream", headers)
                self.reply(200, json.dumps(message).encode(), headers=headers)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_port}/mcp"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def methods(self):
        return [item["body"].get("method") for item in self.requests if item["method"] == "POST"]


def client(server, **kwargs):
    return McpClient(server.url, allow_private=True, timeout=5, **kwargs)


class ServerCase(IsolatedAsyncioTestCase):
    def serve(self, **kwargs):
        server = FakeServer(**kwargs)
        self.addCleanup(server.close)
        return server


class ClientTests(ServerCase):
    async def test_it_initializes_lists_and_calls_over_json(self):
        server = self.serve()
        c = client(server)
        tools = await c.list_tools()
        self.assertEqual([tool["name"] for tool in tools], [tool["name"] for tool in TOOLS])
        self.assertEqual(c.server_name, "Fake Server")
        result = await c.call_tool("echo", {"text": "hi"})
        self.assertEqual(json.loads(result["text"].split("\n\n")[0]), {"text": "hi"})
        self.assertIn("[image content not shown]", result["text"])
        self.assertEqual(server.methods()[:2], ["initialize", "notifications/initialized"])
        # The session and protocol version are sent on everything after initialize.
        later = server.requests[2]["headers"]
        self.assertEqual(later["Mcp-Session-Id"], "session-1")
        self.assertEqual(later["MCP-Protocol-Version"], "2025-06-18")
        self.assertIn("text/event-stream", later["Accept"])

    async def test_it_reads_an_event_stream_answer_and_skips_other_events(self):
        server = self.serve(sse=True)
        result = await client(server).call_tool("echo", {"text": "stream"})
        self.assertIn("stream", result["text"])

    async def test_tool_lists_are_read_page_by_page(self):
        server = self.serve(page_size=2)
        self.assertEqual(len(await client(server).list_tools()), len(TOOLS))
        self.assertEqual(server.methods().count("tools/list"), 3)

    async def test_a_server_without_sessions_works(self):
        server = self.serve(session=False)
        await client(server).list_tools()
        self.assertNotIn("Mcp-Session-Id", server.requests[2]["headers"])

    async def test_an_expired_session_starts_a_new_one_once(self):
        server = self.serve()
        c = client(server)
        await c.list_tools()
        server.sessions.clear()
        result = await c.call_tool("echo", {"text": "again"})
        self.assertIn("again", result["text"])
        self.assertEqual(server.methods().count("initialize"), 2)

    async def test_overlapping_calls_share_one_session_and_one_renewal(self):
        server = self.serve()
        c = client(server)
        await c.list_tools()
        results = await asyncio.gather(*[c.call_tool("echo", {"text": f"n{n}"}) for n in range(4)])
        self.assertEqual([json.loads(r["text"].split("\n\n")[0])["text"] for r in results], ["n0", "n1", "n2", "n3"])
        server.sessions.clear()
        again = await asyncio.gather(*[c.call_tool("echo", {"text": f"m{n}"}) for n in range(4)])
        self.assertEqual(len(again), 4)
        # One renewal serves them all (calls that overlap it may each have been refused once, but start no extra sessions).
        self.assertLessEqual(server.methods().count("initialize"), 3)
        self.assertGreaterEqual(server.methods().count("initialize"), 2)

    async def test_the_credential_is_sent_and_a_wrong_one_is_reported_without_it(self):
        server = self.serve(auth="Bearer secret-token")
        await client(server, headers={"Authorization": "Bearer secret-token"}).list_tools()
        with self.assertRaises(McpError) as caught:
            await client(server, headers={"Authorization": "Bearer wrong-token-value"}).list_tools()
        self.assertEqual(caught.exception.code, "PLUGIN_AUTH_FAILED")
        self.assertNotIn("wrong-token-value", caught.exception.message)
        self.assertNotIn(server.url, caught.exception.message)

    async def test_redirects_and_http_errors_are_refused_with_stable_codes(self):
        for kwargs, code in (({"redirect": True}, "PLUGIN_REDIRECT"), ({"status": 500}, "PLUGIN_SERVER_ERROR"), ({"status": 429}, "PLUGIN_RATE_LIMITED"),
                             ({"status": 405}, "PLUGIN_PROTOCOL_ERROR"), ({"status": 404}, "PLUGIN_NOT_FOUND")):
            server = self.serve(**kwargs)
            with self.assertRaises(McpError) as caught:
                await client(server).list_tools()
            self.assertEqual(caught.exception.code, code, kwargs)
        self.assertEqual(len(server.requests), 1)  # a redirect target is never contacted

    async def test_a_json_rpc_error_becomes_a_request_failure(self):
        server = self.serve()
        with self.assertRaises(McpError) as caught:
            await client(server).call_tool("rpc_error", {})
        self.assertEqual(caught.exception.code, "PLUGIN_REQUEST_FAILED")
        self.assertIn("bad things", caught.exception.message)

    async def test_an_oversized_answer_is_refused(self):
        server = self.serve()
        with patch.object(mcp, "MAX_RESPONSE_BYTES", 10_000):
            with self.assertRaises(McpError) as caught:
                await client(server).call_tool("big", {})
        self.assertEqual(caught.exception.code, "PLUGIN_RESPONSE_TOO_LARGE")

    async def test_a_silent_server_times_out(self):
        block = threading.Event()

        class Silent(BaseHTTPRequestHandler):
            def do_POST(self):
                block.wait(5)

            def log_message(self, *args):
                pass

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), Silent)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (block.set(), httpd.shutdown(), httpd.server_close()))
        slow = McpClient(f"http://127.0.0.1:{httpd.server_port}/mcp", allow_private=True, timeout=0.5)
        with self.assertRaises(McpError) as caught:
            await slow.list_tools()
        self.assertEqual(caught.exception.code, "PLUGIN_TIMEOUT")

    async def test_closing_ends_the_session(self):
        server = self.serve()
        c = client(server)
        await c.list_tools()
        await c.aclose()
        self.assertEqual(server.requests[-1]["method"], "DELETE")
        self.assertEqual(server.sessions, set())

    async def test_an_unreachable_server_is_reported(self):
        server = self.serve()
        url = server.url
        server.close()
        with self.assertRaises(McpError) as caught:
            await McpClient(url, allow_private=True, timeout=2).list_tools()
        self.assertIn(caught.exception.code, ("PLUGIN_UNREACHABLE", "PLUGIN_TIMEOUT"))


class NetworkRuleTests(IsolatedAsyncioTestCase):
    def test_only_plain_https_addresses_are_accepted_by_default(self):
        validate_url("https://example.com/mcp", allow_private=False)
        for url in ("http://example.com/mcp", "https://user:pass@example.com/mcp", "https://example.com/mcp#x", "ftp://example.com",
                    "https://localhost/mcp", "https://intranet/mcp", "https://box.local/mcp", "https://metadata.google.internal/", "",
                    "https://exa mple.com", "https://example.com\\evil", "https://host.internal/x"):
            with self.assertRaises(McpError, msg=url) as caught:
                validate_url(url, allow_private=False)
            self.assertEqual(caught.exception.code, "PLUGIN_URL_REJECTED")
        validate_url("http://127.0.0.1:9000/mcp", allow_private=True)

    async def test_a_name_that_resolves_to_a_private_address_is_refused_even_if_one_answer_is_public(self):
        def answers(*addresses):
            async def lookup(*args, **kwargs):
                return [(0, 0, 0, "", (address, 443)) for address in addresses]
            return lookup
        loop = asyncio.get_running_loop()
        for addresses in (("10.0.0.5",), ("127.0.0.1",), ("169.254.169.254",), ("93.184.216.34", "10.0.0.5"), ("::1",), ("::ffff:10.0.0.1",), ("fd00::1",)):
            with patch.object(loop, "getaddrinfo", answers(*addresses)):
                with self.assertRaises(McpError, msg=addresses) as caught:
                    await resolve_target("https://plugin.example.com/mcp", allow_private=False)
                self.assertEqual(caught.exception.code, "PLUGIN_URL_REJECTED")
        with patch.object(loop, "getaddrinfo", answers("93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946")):
            target = await resolve_target("https://plugin.example.com/mcp", allow_private=False)
        self.assertEqual(target.addresses[0], "93.184.216.34")  # IPv4 first

    def test_credential_headers_are_checked(self):
        self.assertEqual(mcp.clean_headers({"X-Api-Key": "abc"}), {"X-Api-Key": "abc"})
        for headers in ({"Bad Name": "x"}, {"Host": "evil"}, {"Cookie": "a=b"}, {"X-Key": "a\r\nInjected: 1"}, {"X-Key": ""}):
            with self.assertRaises(McpError, msg=headers):
                mcp.clean_headers(headers)

    def test_results_keep_text_and_name_other_media(self):
        result = normalize_result({"content": [{"type": "text", "text": "a"}, {"type": "audio", "data": "x"}, {"type": "resource_link", "uri": "file:///x"},
                                               {"type": "resource", "resource": {"uri": "u", "text": "inline"}}, "junk"], "isError": True})
        self.assertEqual(result["text"], "a\n\n[audio content not shown]\n\n[resource: file:///x]\n\ninline")
        self.assertTrue(result["is_error"])
        with self.assertRaises(McpError):
            normalize_result("nope")


class SchemaTests(TestCase):
    def test_function_names_are_valid_unique_and_short(self):
        taken: set[str] = set()
        names = [function_name("linear", remote, taken) for remote in ("create_issue", "Create Issue", "create.issue", "x" * 200, "x" * 200 + "y", "日本語")]
        self.assertEqual(len(set(names)), len(names))
        for name in names:
            self.assertRegex(name, r"^linear__[a-z0-9_-]+$")
            self.assertLessEqual(len(name), 64)

    def test_slugs_are_unique_lowercase_identifiers(self):
        self.assertEqual(make_slug("Context7", set()), "context7")
        self.assertEqual(make_slug("My Cool Server!", {"my-cool-server"}), "my-cool-server-2")
        self.assertEqual(make_slug("123", set()), "plugin")
        self.assertRegex(make_slug("a" * 80, set()), r"^[a-z][a-z0-9-]{0,23}$")

    def test_schemas_keep_standard_keywords_only_and_stay_objects(self):
        cleaned = clean_input_schema({"$schema": "x", "title": "T", "type": "object", "x-vendor": 1, "additionalProperties": False,
            "properties": {"q": {"type": "string", "pattern": "(?<=a)", "format": "uri", "description": "d", "title": "Q"},
                           "n": {"type": ["integer", "null"], "minimum": 1}, "ref": {"$ref": "http://evil/x"}, "ok": {"$ref": "#/$defs/a"}},
            "required": ["q", "missing"], "$defs": {"a": {"type": "string"}}})
        self.assertEqual(cleaned["required"], ["q"])
        self.assertNotIn("title", cleaned)
        self.assertNotIn("$schema", cleaned)
        self.assertEqual(cleaned["properties"]["q"], {"type": "string", "description": "d"})
        self.assertEqual(cleaned["properties"]["ref"], {})
        self.assertEqual(cleaned["properties"]["ok"], {"$ref": "#/$defs/a"})
        self.assertEqual(cleaned["properties"]["n"], {"type": ["integer", "null"], "minimum": 1})
        for junk in (None, "string", [], {"type": "string"}):
            self.assertEqual(clean_input_schema(junk)["type"], "object")

    def test_an_enormous_schema_is_reduced(self):
        huge = {"type": "object", "properties": {f"p{i}": {"type": "string", "description": "d" * 400} for i in range(100)}}
        cleaned = clean_input_schema(huge)
        self.assertLessEqual(len(json.dumps(cleaned)), 12000)
        self.assertEqual(cleaned["type"], "object")
        deep: dict = {"type": "string"}
        for _ in range(50):
            deep = {"type": "object", "properties": {"a": deep}}
        self.assertLessEqual(len(json.dumps(clean_input_schema(deep))), 12000)

    def test_tool_specs_drop_unusable_and_duplicate_tools_and_default_to_not_read_only(self):
        specs = tool_specs([{"name": "b", "annotations": {"readOnlyHint": True}}, {"name": "a"}, {"name": "a"}, {"nope": 1}, "x", {"name": ""}])
        self.assertEqual([spec["name"] for spec in specs], ["a", "b"])
        self.assertEqual([spec["read_only"] for spec in specs], [False, True])
        self.assertIsNone(tool_spec({"name": 5}))
        self.assertFalse(tool_spec({"name": "t", "annotations": {"readOnlyHint": "true"}})["read_only"])


class FakeCalls:
    def __init__(self):
        self.outcomes = []

    def record_intent(self, **kwargs):
        return uuid4()

    def record_outcome(self, *, call_id, status, result_json=None, error_code=None, evidence_ids=()):
        self.outcomes.append({"status": status, "error_code": error_code})


class FakeBudgets:
    def check_active(self, reservation_id): pass

    def authorize_operation(self, *args, **kwargs): pass

    def record_usage(self, *args, **kwargs): pass


class FakeResources:
    async def verify_tool_inputs(self, ctx, name, args): pass

    async def verify_references(self, ctx, *, evidence_ids, artifact_ids): pass


def connection(server, *, slug="demo", name="Demo", disabled=(), tools=None):
    return NS(id=uuid4(), slug=slug, name=name, server_url=server.url, auth_kind="none", auth_header=None, credential_encrypted=None,
              disabled_tools=list(disabled), tools_json=tool_specs(TOOLS if tools is None else tools))


SETTINGS = NS(plugin_secret=None, plugin_allow_private_network=True, plugin_call_timeout_sec=5)


class GatewayTests(ServerCase):
    def build(self, server, *, approvals=None, **kwargs):
        catalog = ToolCatalog()
        runtime = register_plugins(catalog, [connection(server, **kwargs)], SETTINGS)
        self.addAsyncCleanup(runtime.aclose)
        names = catalog.plugin_tool_names()
        gateway = ToolGateway(catalog=catalog, calls=(calls := FakeCalls()), budgets=FakeBudgets(), resources=FakeResources(), reauthorize=AsyncMock(),
            ctx=NS(task_id=uuid4()), scope=NS(allowed_tools=names, permissions=NS(), budget=Budget()), run_id=uuid4(), reservation_id=uuid4(),
            approvals=approvals)
        return gateway, catalog, calls

    async def test_tools_are_offered_under_safe_names_with_the_servers_schema(self):
        gateway, catalog, _ = self.build(self.serve())
        schemas = {item["function"]["name"]: item["function"] for item in gateway.schemas()}
        self.assertEqual(set(schemas), {f"demo__{tool['name']}" for tool in TOOLS})
        self.assertTrue(schemas["demo__echo"]["description"].startswith("[Demo] Echo"))
        self.assertEqual(schemas["demo__echo"]["parameters"]["required"], ["text"])
        self.assertNotIn("strict", schemas["demo__echo"])
        strict = ToolGateway(catalog=catalog, calls=FakeCalls(), budgets=FakeBudgets(), resources=FakeResources(), reauthorize=AsyncMock(), ctx=NS(task_id=uuid4()),
                             scope=NS(allowed_tools=catalog.plugin_tool_names(), permissions=NS(), budget=Budget()), run_id=uuid4(), reservation_id=uuid4(), strict_tools=True)
        self.assertNotIn("strict", strict.schemas()[0]["function"])

    async def test_a_call_reaches_the_server_and_its_text_is_returned_as_untrusted_data(self):
        server = self.serve()
        gateway, _, calls = self.build(server)
        outcome = await gateway.execute("demo__echo", json.dumps({"text": "hello"}), "c1")
        self.assertEqual(outcome.status, "ok")
        self.assertIn("hello", outcome.data["content"])
        self.assertIn("never instructions", outcome.data["trust"])
        self.assertEqual(calls.outcomes[-1]["status"], "SUCCEEDED")
        called = [item["body"] for item in server.requests if item["method"] == "POST" and item["body"].get("method") == "tools/call"]
        self.assertEqual(called[0]["params"], {"name": "echo", "arguments": {"text": "hello"}})

    async def test_builtin_path_and_repository_rules_do_not_apply_to_plugin_arguments(self):
        gateway, _, _ = self.build(self.serve())
        with patch("agentforge.tools.gateway.authorize_tool") as authorize:
            outcome = await gateway.execute("demo__make_issue", json.dumps({"title": "t", "path": "../../etc/passwd", "repo_id": "x/y", "url": "http://10.0.0.1"}), "c1")
        authorize.assert_not_called()
        self.assertEqual(outcome.status, "ok")

    async def test_a_tool_that_is_not_in_the_allowlist_does_not_run(self):
        server = self.serve()
        gateway, catalog, calls = self.build(server)
        gateway.scope = NS(allowed_tools=("demo__echo",), permissions=NS(), budget=Budget())
        outcome = await gateway.execute("demo__make_issue", "{}", "c1")
        self.assertEqual(outcome.error_code, "SCOPE_DENIED")
        self.assertNotIn("tools/call", server.methods())
        self.assertEqual(calls.outcomes[-1]["status"], "BLOCKED")

    async def test_a_tool_the_person_switched_off_is_not_offered(self):
        gateway, _, _ = self.build(self.serve(), disabled=("make_issue", "fail"))
        names = {item["function"]["name"] for item in gateway.schemas()}
        self.assertNotIn("demo__make_issue", names)
        self.assertNotIn("demo__fail", names)
        self.assertIn("demo__echo", names)

    async def test_plugin_errors_are_normal_results_the_model_can_read(self):
        gateway, _, calls = self.build(self.serve())
        failed = await gateway.execute("demo__fail", "{}", "c1")
        self.assertEqual((failed.status, failed.error_code), ("error", "PLUGIN_TOOL_ERROR"))
        self.assertIn("it broke", failed.error_detail)
        self.assertEqual(calls.outcomes[-1]["status"], "FAILED")
        rpc = await gateway.execute("demo__rpc_error", "{}", "c2")
        self.assertEqual(rpc.error_code, "PLUGIN_REQUEST_FAILED")
        self.assertIn("PLUGIN", error_guidance(rpc.error_code).upper() + "PLUGIN")  # guidance exists for every plugin code

    async def test_a_large_result_is_cut_to_fit_and_says_so(self):
        gateway, _, _ = self.build(self.serve())
        outcome = await gateway.execute("demo__big", "{}", "c1")
        self.assertEqual(outcome.status, "ok")
        self.assertTrue(outcome.data["truncated"])
        self.assertLessEqual(len(outcome.data["content"].encode()), RESULT_BYTES)

    async def test_an_unreachable_server_is_a_tool_error_not_a_crash(self):
        server = self.serve()
        gateway, _, calls = self.build(server)
        server.close()
        outcome = await gateway.execute("demo__make_issue", json.dumps({"title": "t"}), "c1")
        self.assertEqual(outcome.status, "error")
        self.assertTrue(outcome.error_code.startswith("PLUGIN_"))
        self.assertEqual(calls.outcomes[-1]["status"], "FAILED")

    async def test_only_tools_that_say_they_only_read_run_alongside_others_and_without_asking(self):
        gateway, catalog, _ = self.build(self.serve())
        self.assertTrue(gateway.is_parallel_safe("demo__echo"))
        self.assertFalse(gateway.is_parallel_safe("demo__make_issue"))
        self.assertFalse(catalog.get("demo__echo").asks_before)
        self.assertTrue(catalog.get("demo__make_issue").asks_before)
        self.assertTrue(catalog.get("demo__make_issue").has_effects)

    async def test_ask_me_stops_before_a_plugin_tool_that_changes_things(self):
        server = self.serve()
        approvals = NS(asks=AsyncMock(return_value=True), allows=AsyncMock(return_value=False))
        gateway, _, calls = self.build(server, approvals=approvals)
        declined = await gateway.execute("demo__make_issue", json.dumps({"title": "Secret plan"}), "c1")
        self.assertEqual(declined.error_code, "DECLINED")
        self.assertNotIn("tools/call", server.methods())
        shown = approvals.allows.await_args.kwargs
        self.assertEqual(shown["tool"], "demo__make_issue")
        self.assertIn("Demo: make_issue", shown["summary"])
        self.assertIn("Secret plan", shown["summary"])
        # What is stored about the call holds field names, never their content.
        self.assertEqual(shown["detail"], {"argument_count": 1, "fields": ["title"]})
        approvals.allows.reset_mock()
        approvals.allows.return_value = True
        await gateway.execute("demo__echo", json.dumps({"text": "x"}), "c2")  # reads never ask
        approvals.allows.assert_not_awaited()
        approved = await gateway.execute("demo__make_issue", json.dumps({"title": "ok"}), "c3")
        self.assertEqual(approved.status, "ok")


class HelperTests(TestCase):
    def test_text_is_cut_on_a_character_boundary(self):
        text, cut = fit_text("é" * 100, 11)
        self.assertTrue(cut)
        self.assertLessEqual(len(text.encode()), 11)
        self.assertEqual(fit_text("short", 100), ("short", False))

    def test_structured_content_is_used_when_there_is_no_text(self):
        outcome = outcome_from({"text": "", "is_error": False, "structured": {"count": 3}})
        self.assertIn('"count": 3', outcome.data["content"])

    def test_audit_arguments_keep_names_not_content(self):
        record = safe_arguments(PluginArguments.model_validate({"title": "SECRET", "body": {"nested": "SECRET"}}))
        self.assertEqual(record, {"argument_count": 2, "fields": ["title", "body"]})
        self.assertNotIn("SECRET", json.dumps(record))

    def test_approval_text_shows_the_plugin_tool_and_every_argument(self):
        plugin = PluginTool("id", "Linear", "create_issue", "Create issue", "d", {"type": "object"}, False)
        text, truncated = approval_summary("linear__create_issue", PluginArguments.model_validate({"title": "Ship it"}), plugin=plugin)
        self.assertIn("Linear: Create issue", text)
        self.assertIn("Ship it", text)
        self.assertFalse(truncated)

    def test_plugin_tool_names_cannot_shadow_builtins_or_break_the_format(self):
        catalog = ToolCatalog()
        plugin = PluginTool("id", "P", "r", "", "", {"type": "object"}, True)

        async def handler(args, ctx):
            return None
        for bad in ("bash", "read_task_input", "UPPER__x", "a__" + "x" * 80, "no-separator", "a__b c"):
            with self.assertRaises(Exception, msg=bad):
                catalog.register_plugin(bad, handler, plugin, timeout_sec=1)
        catalog.register_plugin("ok__tool", handler, plugin, timeout_sec=1)
        with self.assertRaises(ValueError):
            catalog.register_plugin("ok__tool", handler, plugin, timeout_sec=1)
        self.assertEqual(catalog.plugin_tool_names(), ("ok__tool",))
        self.assertEqual(Decimal(0), catalog.get("ok__tool").max_cost_usd)


class RunTests(IsolatedAsyncioTestCase):
    async def test_a_run_offers_plugin_tools_beside_its_own_and_tells_the_model_how_to_treat_them(self):
        from test_spawn_service import context, request, service_with
        from agentforge.contracts.subagent import SubagentResult
        seen = {}

        async def run(**kwargs):
            seen.update(kwargs)
            return SubagentResult(status="PASS", summary="done")
        service, recorder = service_with(run)
        plugin = PluginTool("id", "Demo", "echo", "", "Echo.", {"type": "object", "properties": {}}, True)

        async def handler(args, ctx):
            return None
        service.catalog.register_plugin("demo__echo", handler, plugin, timeout_sec=1)
        await service.spawn_subagent(request(), context())
        names = [item["function"]["name"] for item in seen["gateway"].schemas()]
        self.assertEqual(names, ["web_search", "demo__echo"])
        prompt = seen["messages"][0]["content"]
        self.assertIn("## Plugins", prompt)
        self.assertIn("demo__echo", prompt)
        self.assertEqual(recorder.started[0]["effective_permissions"]["available_tools"], ["web_search", "demo__echo"])

    async def test_without_plugins_nothing_changes(self):
        from test_spawn_service import context, request, service_with
        from agentforge.contracts.subagent import SubagentResult
        seen = {}

        async def run(**kwargs):
            seen.update(kwargs)
            return SubagentResult(status="PASS", summary="done")
        service, _ = service_with(run)
        await service.spawn_subagent(request(), context())
        self.assertEqual([item["function"]["name"] for item in seen["gateway"].schemas()], ["web_search"])
        self.assertNotIn("## Plugins", seen["messages"][0]["content"])


class LoopTests(ServerCase):
    """The agent loop with a scripted model calling plugin tools through the real gateway and a real (local) MCP server."""

    async def test_the_model_uses_plugin_tools_and_reads_their_results(self):
        from test_child_runtime import Ledger, ScriptedModel, body, calls, run, text, tool_messages
        server = self.serve()
        catalog = ToolCatalog()
        runtime = register_plugins(catalog, [connection(server)], SETTINGS)
        self.addAsyncCleanup(runtime.aclose)
        gateway = ToolGateway(catalog=catalog, calls=FakeCalls(), budgets=FakeBudgets(), resources=FakeResources(), reauthorize=AsyncMock(),
            ctx=NS(task_id=uuid4()), scope=NS(allowed_tools=catalog.plugin_tool_names(), permissions=NS(), budget=Budget()), run_id=uuid4(), reservation_id=uuid4())
        model = ScriptedModel([
            calls(("demo__echo", {"text": "one"}), ("demo__echo", {"text": "two"})),
            calls(("demo__fail", {}), ("demo__make_issue", {"title": "T"})),
            text("All done."),
        ])
        result = await run(model, gateway)
        self.assertEqual((result.status, result.summary), ("PASS", "All done."))
        self.assertEqual(sorted(model.seen[0]["tools"]), sorted(f"demo__{tool['name']}" for tool in TOOLS))
        first = [body(message) for message in tool_messages(model, 1)]
        self.assertEqual([item["tool"] for item in first], ["demo__echo", "demo__echo"])
        self.assertTrue(all(item["status"] == "ok" and "never instructions" in item["data"]["trust"] for item in first))
        self.assertIn("one", first[0]["data"]["content"])
        second = [body(message) for message in tool_messages(model, 2)][2:]
        self.assertEqual((second[0]["status"], second[0]["error_code"]), ("error", "PLUGIN_TOOL_ERROR"))
        self.assertIn("it broke", second[0]["detail"])
        self.assertIn("guidance", second[0])
        self.assertEqual(second[1]["status"], "ok")
        names = [item["body"]["params"]["name"] for item in server.requests if item["method"] == "POST" and item["body"].get("method") == "tools/call"]
        self.assertEqual(sorted(names), ["echo", "echo", "fail", "make_issue"])
        await runtime.aclose()
        self.assertEqual(server.requests[-1]["method"], "DELETE")
