"""Plugin sign-in with OAuth: discovery, registration, PKCE and tokens against a fake authorization server, the MCP client's token
source, the catalog, and the network rule that lets a NAT64 address through only when the address inside it is public."""
import asyncio
import re
import time
from urllib.parse import parse_qsl, urlsplit
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock

from agentforge.plugins.catalog import BY_KEY, CATALOG, catalog_view
from agentforge.plugins.mcp import McpClient, McpError, _public, challenge_params
from agentforge.plugins.oauth import (
    Client, Discovery, OAuthError, TokenClient, authorization_server_metadata_urls, authorization_url, callback_url, challenge_params as oauth_challenge,
    check_issuer, choose_scope, code_challenge, discover, exchange_code, metadata_document, metadata_document_url, new_verifier, preferred_method,
    refresh_tokens, register, resource_metadata_urls, same_issuer, same_resource,
)
from agentforge.tools.catalog import ERROR_GUIDANCE, error_guidance

from oauth_fakes import OAuthWorld, challenge_of


class WorldCase(IsolatedAsyncioTestCase):
    def world(self, **kwargs) -> OAuthWorld:
        world = OAuthWorld(**kwargs)
        self.addCleanup(world.close)
        return world


class HelperTests(TestCase):
    def test_a_challenge_is_read_whatever_its_shape(self):
        found = challenge_params(['Bearer error="invalid_token", resource_metadata="https://a.example/.well-known/x", scope="read write"'])
        self.assertEqual(found, {"error": "invalid_token", "resource_metadata": "https://a.example/.well-known/x", "scope": "read write"})
        self.assertEqual(challenge_params(["bearer resource_metadata=https://a.example/m"]), {"resource_metadata": "https://a.example/m"})
        self.assertEqual(challenge_params(['Basic realm="x"']), {})
        self.assertEqual(challenge_params(["Bearer"]), {})
        self.assertIs(oauth_challenge, challenge_params)

    def test_a_declared_resource_must_cover_the_address_it_was_reached_at(self):
        self.assertTrue(same_resource("https://h.example/mcp", "https://h.example/mcp"))
        self.assertTrue(same_resource("https://h.example/mcp/", "https://h.example/mcp"))
        self.assertTrue(same_resource("https://h.example", "https://h.example/mcp"), "a whole origin covers its endpoint")
        self.assertTrue(same_resource("https://H.example:443/", "https://h.example/a/b"))
        self.assertFalse(same_resource("https://h.example/mcp", "https://h.example/mcpx"), "only at a path boundary")
        self.assertFalse(same_resource("https://h.example/a/b", "https://h.example/a"), "a narrower resource does not cover a wider address")
        self.assertFalse(same_resource("https://other.example/mcp", "https://h.example/mcp"), "never another host")
        self.assertFalse(same_resource("http://h.example/mcp", "https://h.example/mcp"), "never another scheme")
        self.assertFalse(same_resource("https://h.example:8443/mcp", "https://h.example/mcp"), "never another port")
        self.assertFalse(same_resource("https://h.example/mcp?x=1", "https://h.example/mcp"))
        self.assertFalse(same_resource("https://h.example:bad/mcp", "https://h.example/mcp"))

    def test_an_issuer_must_be_the_one_the_document_was_found_by(self):
        self.assertTrue(same_issuer("https://auth.example", "https://auth.example"))
        self.assertTrue(same_issuer("https://auth.example/", "https://auth.example"), "an origin with and without its slash is one identifier")
        self.assertTrue(same_issuer("https://auth.example", "https://auth.example/"))
        self.assertFalse(same_issuer("https://auth.example/t1/", "https://auth.example/t1"), "a path is compared exactly")
        self.assertFalse(same_issuer("https://auth.example/t2", "https://auth.example/t1"))
        self.assertFalse(same_issuer("https://evil.example", "https://auth.example"))
        self.assertFalse(same_issuer("http://auth.example", "https://auth.example"))

    def test_metadata_is_looked_for_where_the_specification_says_and_in_its_order(self):
        self.assertEqual(resource_metadata_urls("https://h.example/public/mcp", "https://h.example/meta"),
                         ["https://h.example/meta", "https://h.example/.well-known/oauth-protected-resource/public/mcp", "https://h.example/.well-known/oauth-protected-resource"])
        self.assertEqual(resource_metadata_urls("https://h.example", None), ["https://h.example/.well-known/oauth-protected-resource"])
        self.assertEqual(authorization_server_metadata_urls("https://a.example/tenant1"), [
            "https://a.example/.well-known/oauth-authorization-server/tenant1", "https://a.example/.well-known/openid-configuration/tenant1",
            "https://a.example/tenant1/.well-known/openid-configuration"])
        self.assertEqual(authorization_server_metadata_urls("https://a.example"), [
            "https://a.example/.well-known/oauth-authorization-server", "https://a.example/.well-known/openid-configuration"])

    def test_scopes_follow_the_specifications_order(self):
        self.assertEqual(choose_scope("files:read", ["a", "b"], ["x"]), "files:read", "the challenge first")
        self.assertEqual(choose_scope(None, ["a", "b"], ["x"]), "a b", "else everything the resource lists")
        self.assertIsNone(choose_scope(None, None, ["x"]), "nothing known: none asked")
        self.assertEqual(choose_scope(None, ["a"], ["a", "offline_access"]), "a offline_access", "refresh tokens when the server offers them")
        self.assertEqual(choose_scope("a offline_access", None, ["offline_access"]), "a offline_access")
        self.assertEqual(choose_scope(None, None, ["openid", "offline_access"]), "openid offline_access")
        long = choose_scope(None, [f"scope{i:04d}" for i in range(400)], None)
        self.assertLessEqual(len(long), 1000)
        self.assertFalse(long.endswith(" "))

    def test_the_code_challenge_matches_the_published_test_vector(self):
        # RFC 7636 appendix B.
        self.assertEqual(code_challenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"), "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM")
        verifier = new_verifier()
        self.assertTrue(43 <= len(verifier) <= 128 and re.fullmatch(r"[A-Za-z0-9_-]+", verifier))
        self.assertNotEqual(verifier, new_verifier())

    def test_the_authorization_request_carries_what_the_specification_requires(self):
        found = Discovery("https://h.example/mcp", "https://a.example", "https://a.example/authorize?tenant=7", "https://a.example/token", None, None, False, False, (), None)
        url = authorization_url(found, "client-1", "https://app.example/cb", "state-1", "verifier-1" * 5, "read write")
        query = dict(parse_qsl(urlsplit(url).query))
        self.assertEqual(urlsplit(url).path, "/authorize")
        self.assertEqual(query["tenant"], "7", "what the endpoint already carried stays")
        self.assertEqual({key: query[key] for key in ("response_type", "client_id", "redirect_uri", "state", "code_challenge_method", "resource", "scope")},
                         {"response_type": "code", "client_id": "client-1", "redirect_uri": "https://app.example/cb", "state": "state-1",
                          "code_challenge_method": "S256", "resource": "https://h.example/mcp", "scope": "read write"})
        self.assertEqual(query["code_challenge"], code_challenge("verifier-1" * 5))
        self.assertNotIn("scope", dict(parse_qsl(urlsplit(authorization_url(found, "c", "https://app.example/cb", "s", "v" * 43, None)).query)))

    def test_the_issuer_of_a_response_is_checked_before_its_code_is_used(self):
        for returned, advertised, fine in ((None, False, True), (None, True, False), ("https://a.example", True, True), ("https://a.example", False, True),
                                           ("https://evil.example", True, False), ("https://evil.example", False, False), ("https://a.example/", False, True)):
            with self.subTest(returned=returned, advertised=advertised):
                if fine:
                    check_issuer(returned, "https://a.example", advertised=advertised)
                else:
                    with self.assertRaises(OAuthError) as raised:
                        check_issuer(returned, "https://a.example", advertised=advertised)
                    self.assertEqual(raised.exception.code, "plugin_oauth_issuer_mismatch")

    def test_the_app_authenticates_as_a_public_client_whenever_it_may(self):
        self.assertEqual(preferred_method(()), "none")
        self.assertEqual(preferred_method(("client_secret_basic", "none")), "none")
        self.assertEqual(preferred_method(("client_secret_basic", "client_secret_post")), "client_secret_post")
        self.assertEqual(preferred_method(("client_secret_basic",)), "client_secret_basic")

    def test_the_client_metadata_document_needs_an_https_address(self):
        from types import SimpleNamespace as NS
        secure = NS(api_public_base_url="https://api.example", app_base_url="https://app.example")
        document = metadata_document(secure)
        self.assertEqual(document["client_id"], "https://api.example/api/v1/plugins/oauth/client.json")
        self.assertEqual(document["redirect_uris"], ["https://api.example/api/v1/plugins/oauth/callback"])
        self.assertEqual(document["token_endpoint_auth_method"], "none")
        self.assertIn("authorization_code", document["grant_types"])
        self.assertIsNone(metadata_document_url(NS(api_public_base_url="http://localhost:8000", app_base_url="http://localhost:3000")))
        self.assertEqual(callback_url(NS(api_public_base_url="http://localhost:8000/")), "http://localhost:8000/api/v1/plugins/oauth/callback")


class NetworkRuleTests(TestCase):
    def test_a_nat64_address_is_judged_by_the_address_inside_it(self):
        # DNS64 gives IPv4-only hosts addresses in 64:ff9b::/96 (RFC 6052); refusing all of them refused every such host.
        for address, allowed in (("64:ff9b::c689:9629", True), ("64:ff9b::8.8.8.8", True), ("64:ff9b::7f00:1", False), ("64:ff9b::a00:1", False),
                                 ("64:ff9b::c0a8:1", False), ("64:ff9b::a9fe:a9fe", False), ("64:ff9b:1::1", False)):
            with self.subTest(address=address):
                self.assertEqual(_public(address), allowed)

    def test_the_ordinary_rules_are_unchanged(self):
        for address, allowed in (("198.137.150.41", True), ("2606:4700::1", True), ("127.0.0.1", False), ("10.1.2.3", False), ("169.254.169.254", False),
                                 ("192.168.0.1", False), ("::1", False), ("fe80::1", False), ("::ffff:8.8.8.8", False), ("0.0.0.0", False)):
            with self.subTest(address=address):
                self.assertEqual(_public(address), allowed)


class DiscoveryTests(WorldCase):
    async def test_a_server_is_found_through_its_challenge(self):
        world = self.world(cimd=True, iss_param=True)
        found = await discover(world.mcp_url, allow_private=True)
        self.assertEqual((found.resource, found.issuer), (world.mcp_url, world.issuer))
        self.assertEqual(found.authorization_endpoint, world.base + "/as/authorize")
        self.assertTrue(found.metadata_documents and found.iss_supported and found.registration_endpoint)
        self.assertEqual(found.scope, "read offline_access", "the challenge's scope, plus offline_access, which the server offers")
        self.assertEqual(found.revocation_endpoint, world.base + "/as/revoke")

    async def test_a_server_without_a_challenge_is_found_at_the_well_known_address(self):
        world = self.world(challenge=False)
        found = await discover(world.mcp_url, allow_private=True)
        self.assertEqual(found.scope, "read write offline_access", "no challenge: everything the resource lists")

    async def test_a_server_that_names_its_whole_origin_is_accepted(self):
        world = self.world(resource_path="root", challenge=False)
        world.resource_declared = world.base
        found = await discover(world.mcp_url, allow_private=True)
        self.assertEqual(found.resource, world.base, "the resource asked for is the one the server declared")

    async def test_metadata_is_read_from_openid_connect_discovery_too(self):
        found = await discover(self.world(oidc_only=True).mcp_url, allow_private=True)
        self.assertTrue(found.token_endpoint.endswith("/as/token"))

    async def test_a_server_that_does_not_ask_for_a_sign_in_is_reported_so(self):
        world = self.world()
        world.access["anything"] = {"scope": "", "expires": None}
        world.mcp_status = 200
        with self.assertRaises(OAuthError) as raised:
            await discover(world.mcp_url, allow_private=True)
        self.assertEqual(raised.exception.code, "plugin_oauth_not_required")

    async def test_a_401_with_no_metadata_is_not_a_sign_in_this_app_can_use(self):
        world = self.world(resource_path="/nowhere", challenge=False)
        with self.assertRaises(OAuthError) as raised:
            await discover(world.mcp_url, allow_private=True)
        self.assertEqual(raised.exception.code, "plugin_oauth_unsupported")

    async def test_metadata_that_names_another_resource_is_refused(self):
        world = self.world(resource_declared="https://elsewhere.example/mcp")
        with self.assertRaises(OAuthError) as raised:
            await discover(world.mcp_url, allow_private=True)
        self.assertEqual(raised.exception.code, "plugin_oauth_metadata_invalid")

    async def test_server_metadata_that_names_another_issuer_is_refused(self):
        world = self.world(issuer_declared="https://evil.example")
        with self.assertRaises(OAuthError) as raised:
            await discover(world.mcp_url, allow_private=True)
        self.assertEqual(raised.exception.code, "plugin_oauth_unsupported")

    async def test_a_server_without_pkce_is_refused_and_one_with_only_plain_too(self):
        for pkce in (None, ("plain",)):
            with self.subTest(pkce=pkce):
                with self.assertRaises(OAuthError) as raised:
                    await discover(self.world(pkce=pkce).mcp_url, allow_private=True)
                self.assertIn("PKCE", raised.exception.message)

    async def test_plain_http_and_private_addresses_need_the_development_switch(self):
        world = self.world()
        with self.assertRaises(OAuthError) as raised:
            await discover(world.mcp_url, allow_private=False)
        self.assertEqual(raised.exception.code, "plugin_oauth_url_rejected")


class RegistrationTests(WorldCase):
    async def found(self, world):
        return await discover(world.mcp_url, allow_private=True)

    async def test_a_metadata_document_is_used_when_the_server_supports_it(self):
        world = self.world(cimd=True)
        client = await register(await self.found(world), world.settings(), allow_private=True)
        self.assertEqual((client.kind, client.method, client.secret), ("metadata_document", "none", None))
        self.assertEqual(client.client_id, "https://app.example.test/api/v1/plugins/oauth/client.json")
        self.assertEqual(world.registrations, 0, "nothing was registered")

    async def test_dynamic_registration_is_used_otherwise_and_when_the_address_is_not_https(self):
        for cimd, base in ((False, "https://app.example.test"), (True, "http://localhost:8000")):
            with self.subTest(cimd=cimd, base=base):
                world = self.world(cimd=cimd)
                client = await register(await self.found(world), world.settings(api_public_base_url=base, app_base_url=base), allow_private=True)
                self.assertEqual((client.kind, client.method), ("dynamic", "none"))
                self.assertIn(client.client_id, world.clients)
                self.assertEqual(world.clients[client.client_id]["redirect_uris"], [base + "/api/v1/plugins/oauth/callback"])

    async def test_the_operator_can_turn_metadata_documents_off(self):
        world = self.world(cimd=True)
        client = await register(await self.found(world), world.settings(plugin_oauth_registration="dcr"), allow_private=True)
        self.assertEqual(client.kind, "dynamic")

    async def test_a_client_the_person_registered_is_used_first(self):
        world = self.world(cimd=True)
        client = await register(await self.found(world), world.settings(), allow_private=True, given=("my-client", "my-secret"))
        self.assertEqual((client.kind, client.client_id, client.secret, client.method), ("preregistered", "my-client", "my-secret", "client_secret_post"))
        self.assertEqual((await register(await self.found(world), world.settings(), allow_private=True, given=("public", None))).method, "none")

    async def test_an_earlier_registration_is_reused_instead_of_registering_again(self):
        world = self.world()
        cached = Client("remembered", None, "none", "dynamic")
        client = await register(await self.found(world), world.settings(), allow_private=True, cached=cached)
        self.assertIs(client, cached)
        self.assertEqual(world.registrations, 0)

    async def test_a_server_that_registers_no_one_says_so(self):
        world = self.world(dcr=False)
        with self.assertRaises(OAuthError) as raised:
            await register(await self.found(world), world.settings(), allow_private=True)
        self.assertEqual(raised.exception.code, "plugin_oauth_client_required")

    async def test_a_server_that_only_knows_secrets_gets_a_confidential_client(self):
        world = self.world(auth_methods=("client_secret_basic",))
        client = await register(await self.found(world), world.settings(), allow_private=True)
        self.assertEqual((client.method, bool(client.secret)), ("client_secret_basic", True))

    async def test_a_refused_registration_is_reported_without_the_servers_words(self):
        world = self.world(allowlist=["https://someone-else.example/cb"])
        with self.assertRaises(OAuthError) as raised:
            await register(await self.found(world), world.settings(), allow_private=True)
        self.assertEqual(raised.exception.code, "plugin_oauth_registration_failed")
        self.assertNotIn("invalid_redirect_uri", raised.exception.message)


class TokenTests(WorldCase):
    async def signed_in(self, world, **register_kwargs):
        found = await discover(world.mcp_url, allow_private=True)
        settings = world.settings()
        client = await register(found, settings, allow_private=True, **register_kwargs)
        verifier = new_verifier()
        url = authorization_url(found, client.client_id, callback_url(settings), "state-1", verifier, found.scope)
        callback = world.callback_params(world.authorize(url))
        token_client = TokenClient(found.token_endpoint, client.client_id, client.secret, client.method, found.resource)
        return found, token_client, verifier, callback, settings

    async def test_a_code_is_exchanged_with_its_verifier_and_the_resource(self):
        world = self.world()
        found, token_client, verifier, callback, settings = await self.signed_in(world)
        tokens = await exchange_code(token_client, callback["code"], verifier, callback_url(settings), allow_private=True)
        self.assertTrue(tokens.access.startswith("access-") and tokens.refresh.startswith("refresh-"))
        self.assertIsNotNone(tokens.expires_at)
        form = world.token_requests[-1]["form"]
        self.assertEqual((form["grant_type"], form["code_verifier"], form["resource"]), ("authorization_code", verifier, found.resource))

    async def test_a_wrong_verifier_gets_nothing(self):
        world = self.world()
        found, token_client, verifier, callback, settings = await self.signed_in(world)
        with self.assertRaises(OAuthError) as raised:
            await exchange_code(token_client, callback["code"], new_verifier(), callback_url(settings), allow_private=True)
        self.assertEqual(raised.exception.code, "plugin_oauth_grant_rejected")

    async def test_a_secret_is_sent_the_way_the_server_registered_it(self):
        for method, header in (("client_secret_basic", True), ("client_secret_post", False)):
            with self.subTest(method=method):
                world = self.world(auth_methods=(method,))
                found, token_client, verifier, callback, settings = await self.signed_in(world)
                await exchange_code(token_client, callback["code"], verifier, callback_url(settings), allow_private=True)
                sent = world.token_requests[-1]
                self.assertEqual(sent["authorization"] is not None and sent["authorization"].startswith("Basic "), header)
                self.assertEqual("client_secret" in sent["form"], not header)

    async def test_a_refresh_rotates_the_token_and_keeps_the_scope(self):
        world = self.world()
        found, token_client, verifier, callback, settings = await self.signed_in(world)
        first = await exchange_code(token_client, callback["code"], verifier, callback_url(settings), allow_private=True)
        second = await refresh_tokens(token_client, first.refresh, allow_private=True)
        self.assertNotEqual((second.access, second.refresh), (first.access, first.refresh))
        with self.assertRaises(OAuthError) as raised:
            await refresh_tokens(token_client, first.refresh, allow_private=True)
        self.assertEqual(raised.exception.code, "plugin_oauth_grant_rejected", "the spent refresh token no longer works")

    async def test_a_server_that_does_not_rotate_keeps_the_same_refresh_token(self):
        world = self.world(rotate=False)
        found, token_client, verifier, callback, settings = await self.signed_in(world)
        first = await exchange_code(token_client, callback["code"], verifier, callback_url(settings), allow_private=True)
        second = await refresh_tokens(token_client, first.refresh, allow_private=True)
        self.assertEqual(second.refresh, first.refresh)

    async def test_a_withdrawn_sign_in_is_told_apart_from_a_rejected_client(self):
        world = self.world()
        found, token_client, verifier, callback, settings = await self.signed_in(world)
        first = await exchange_code(token_client, callback["code"], verifier, callback_url(settings), allow_private=True)
        world.fail_refresh = "invalid_grant"
        with self.assertRaises(OAuthError) as grant:
            await refresh_tokens(token_client, first.refresh, allow_private=True)
        self.assertEqual(grant.exception.code, "plugin_oauth_grant_rejected")
        stranger = TokenClient(token_client.token_endpoint, "unknown-client", None, "none", found.resource)
        with self.assertRaises(OAuthError) as client:
            await refresh_tokens(stranger, first.refresh, allow_private=True)
        self.assertEqual(client.exception.code, "plugin_oauth_client_rejected")

    async def test_a_token_without_a_lifetime_or_refresh_token_is_still_usable(self):
        world = self.world(lifetime=0, refresh_tokens=False)
        found, token_client, verifier, callback, settings = await self.signed_in(world)
        tokens = await exchange_code(token_client, callback["code"], verifier, callback_url(settings), allow_private=True)
        self.assertIsNone(tokens.expires_at)
        self.assertIsNone(tokens.refresh)

    async def test_a_kind_of_token_that_is_not_bearer_is_refused(self):
        world = self.world(token_type="mac")
        found, token_client, verifier, callback, settings = await self.signed_in(world)
        with self.assertRaises(OAuthError) as raised:
            await exchange_code(token_client, callback["code"], verifier, callback_url(settings), allow_private=True)
        self.assertEqual(raised.exception.code, "plugin_oauth_exchange_failed")


class FakeSource:
    """A token source whose token the test controls."""

    def __init__(self, tokens, renewed=None):
        self.tokens, self.renewed, self.calls = list(tokens), renewed, []

    async def headers(self):
        self.calls.append("headers")
        return {"Authorization": f"Bearer {self.tokens[0]}"}

    async def rejected(self):
        self.calls.append("rejected")
        if self.renewed is None or len(self.tokens) < 2:
            return False
        self.tokens.pop(0)
        return True

    async def insufficient(self, scope):
        self.calls.append(f"insufficient:{scope}")


class ClientSourceTests(WorldCase):
    def grant(self, world, token="good", scope="read"):
        world.access[token] = {"scope": scope, "expires": None}
        return token

    async def test_the_credential_is_asked_for_on_every_request(self):
        world = self.world()
        good = self.grant(world)
        source = FakeSource([good])
        c = McpClient(world.mcp_url, allow_private=True, timeout=5, auth=source)
        tools = await c.list_tools()
        self.assertEqual([tool["name"] for tool in tools], ["echo", "admin"])
        self.assertGreaterEqual(source.calls.count("headers"), 3, "initialize, its notification and the listing each asked")
        self.assertTrue(all(item["authorization"] == f"Bearer {good}" for item in world.mcp_requests))

    async def test_a_refused_token_is_renewed_once_and_the_request_goes_again(self):
        world = self.world()
        good = self.grant(world)
        source = FakeSource(["stale", good], renewed=True)
        c = McpClient(world.mcp_url, allow_private=True, timeout=5, auth=source)
        self.assertEqual(len(await c.list_tools()), 2)
        self.assertEqual(source.calls.count("rejected"), 1)
        self.assertEqual(world.mcp_requests[0]["authorization"], "Bearer stale")

    async def test_a_sign_in_that_cannot_be_renewed_says_to_sign_in_again(self):
        world = self.world()
        source = FakeSource(["stale"])
        c = McpClient(world.mcp_url, allow_private=True, timeout=5, auth=source)
        with self.assertRaises(McpError) as raised:
            await c.list_tools()
        self.assertEqual(raised.exception.code, "PLUGIN_REAUTHORIZE")
        self.assertEqual(source.calls.count("rejected"), 1, "asked once, not in a loop")

    async def test_a_token_that_dies_mid_session_is_renewed_on_the_next_call(self):
        world = self.world()
        good, better = self.grant(world, "good"), self.grant(world, "better")
        source = FakeSource([good, better], renewed=True)
        c = McpClient(world.mcp_url, allow_private=True, timeout=5, auth=source)
        await c.list_tools()
        del world.access[good]
        result = await c.call_tool("echo", {})
        self.assertEqual(result["text"], "ok echo")
        self.assertEqual(world.mcp_requests[-1]["authorization"], f"Bearer {better}")

    async def test_a_scope_challenge_is_reported_and_the_source_is_told_what_is_needed(self):
        world = self.world()
        good = self.grant(world, scope="read")
        source = FakeSource([good])
        c = McpClient(world.mcp_url, allow_private=True, timeout=5, auth=source)
        await c.list_tools()
        with self.assertRaises(McpError) as raised:
            await c.call_tool("admin", {})
        self.assertEqual(raised.exception.code, "PLUGIN_SCOPE_INSUFFICIENT")
        self.assertIn("insufficient:admin", source.calls)

    async def test_a_source_that_cannot_give_a_credential_stops_the_request(self):
        world = self.world()
        source = FakeSource([])
        source.headers = AsyncMock(side_effect=McpError("PLUGIN_REAUTHORIZE", "sign in again"))
        c = McpClient(world.mcp_url, allow_private=True, timeout=5, auth=source)
        with self.assertRaises(McpError) as raised:
            await c.list_tools()
        self.assertEqual(raised.exception.code, "PLUGIN_REAUTHORIZE")
        self.assertEqual(world.mcp_requests, [], "nothing was sent without a credential")

    async def test_a_long_signed_token_can_be_sent(self):
        world = self.world()
        long = self.grant(world, "t" * 6000)
        c = McpClient(world.mcp_url, allow_private=True, timeout=5, auth=FakeSource([long]))
        self.assertEqual(len(await c.list_tools()), 2)


class CatalogTests(TestCase):
    def test_every_entry_is_well_formed(self):
        keys = [entry.key for entry in CATALOG]
        self.assertEqual(len(keys), len(set(keys)), "keys are unique")
        self.assertGreaterEqual(len(keys), 40)
        for entry in CATALOG:
            with self.subTest(entry=entry.key):
                self.assertRegex(entry.key, r"^[a-z0-9-]{1,40}$")
                self.assertTrue(entry.server_url.startswith("https://"))
                self.assertTrue(entry.name and 10 <= len(entry.description) <= 130, "a short description")
                self.assertTrue(entry.category)
                self.assertIn(entry.auth, ("none", "optional", "required"))
                self.assertTrue(entry.docs_url is None or entry.docs_url.startswith("https://"))
                if entry.oauth:
                    self.assertIn(entry.auth, ("none", "optional"), "a plugin that signs in does not also require a token")
                if entry.auth == "required":
                    self.assertFalse(entry.oauth)

    def test_the_catalog_is_served_with_what_the_app_needs_to_draw_it(self):
        listed = catalog_view()
        self.assertEqual({item["key"] for item in listed}, set(BY_KEY))
        sample = next(item for item in listed if item["key"] == "linear")
        self.assertTrue(sample["oauth"] and sample["auth"] == "optional" and sample["category"] == "Code")
        self.assertTrue(all("oauth" in item for item in listed))

    def test_the_servers_that_need_nothing_or_only_a_token_are_still_there(self):
        self.assertEqual((BY_KEY["github"].auth, BY_KEY["github"].oauth), ("required", False))
        self.assertEqual((BY_KEY["deepwiki"].auth, BY_KEY["deepwiki"].oauth), ("none", False))
        self.assertEqual((BY_KEY["context7"].auth, BY_KEY["context7"].oauth), ("optional", False))

    def test_the_model_is_told_what_to_do_about_a_sign_in_that_ended(self):
        for code in ("PLUGIN_REAUTHORIZE", "PLUGIN_SCOPE_INSUFFICIENT", "PLUGIN_AUTH_UNAVAILABLE"):
            self.assertIn(code, ERROR_GUIDANCE)
            self.assertNotEqual(error_guidance(code), error_guidance("PLUGIN_UNKNOWN_CODE"))
        self.assertIn("Reconnect", error_guidance("PLUGIN_REAUTHORIZE"))
