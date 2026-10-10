"""Provider-free contracts for GitHub authentication and approval boundaries."""
import asyncio
import hashlib
import hmac
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4
import httpx
from pydantic import SecretStr, ValidationError
from agentforge.auth.crypto import CredentialCipher
from agentforge.contracts.approvals import ApprovalDecision, canonical_hash
from agentforge.integrations.github_app import GitHubAppClient
from agentforge.integrations.github_client import GitHubError
from agentforge.integrations.github_write import GitHubWriteService
from agentforge.security.webhook_verification import verify_github_signature
from agentforge.settings import Settings
from fastapi import FastAPI, Request
from agentforge.security.request_limits import RequestBodyLimitMiddleware
from agentforge.api.routes_github import router as github_router
from agentforge.auth.routes import github_callback, github_start
from agentforge.auth.session import require_public_transport
from agentforge.auth.durable import PUBLIC_COOKIE_NAME, OAUTH_COOKIE_NAME
from fastapi import HTTPException
from urllib.parse import urlsplit, parse_qs

class SecurityTests(unittest.TestCase):
    def test_canonical_hash_and_exact_decision(self):
        self.assertEqual(canonical_hash({"b": 2, "a": ["é", 1]}), canonical_hash({"a": ["é", 1], "b": 2}))
        self.assertNotEqual(canonical_hash({"sha": "a"}), canonical_hash({"sha": "b"}))
        with self.assertRaises(ValidationError):
            ApprovalDecision(action_hash="a"*64, decision="approve", action={"repo": "evil"})

    def test_tokens_encrypted_and_tampering_rejected(self):
        cipher = CredentialCipher(SecretStr("fixture-key-" + "x" * 32))
        encrypted = cipher.encrypt("ghu_fixture_never_log")
        self.assertNotIn("ghu_fixture", encrypted)
        self.assertEqual(cipher.decrypt(encrypted), "ghu_fixture_never_log")
        with self.assertRaises(ValueError):
            cipher.decrypt(encrypted[:-5] + "abcde")
        with self.assertRaises(ValueError):
            CredentialCipher(SecretStr("other-key-" + "z"*32)).decrypt(encrypted)

    def test_webhook_raw_bytes_and_constant_signature(self):
        raw, secret = b'{"installation":{"id":1}}', "test-secret"
        signature = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
        verify_github_signature(raw, signature, secret)
        for body, supplied in ((raw+b" ", signature), (raw, None), (raw, signature.upper())):
            with self.assertRaises(ValueError):
                verify_github_signature(body, supplied, secret)

    def test_pr_confirmation_is_exact(self):
        action = {"repo_id": "owner/repo", "repository_id": 42, "head_branch": "agentforge/task-a", "base_branch": "main", "base_sha": "b"*40}
        value = {"number": 7, "html_url": "https://github.com/owner/repo/pull/7", "draft": True, "state": "open",
            "head": {"ref": action["head_branch"], "sha": "a"*40, "repo": {"id": 42}},
            "base": {"ref": "main", "sha": "b"*40, "repo": {"id": 42}}}
        GitHubWriteService._checked_pr(value, action, "a"*40)
        for change in ({"draft": False}, {"html_url": "https://evil.example/pr"}, {"base": {**value["base"], "sha": "c"*40}}):
            with self.assertRaises(GitHubError):
                GitHubWriteService._checked_pr({**value, **change}, action, "a"*40)

class ProviderTests(unittest.IsolatedAsyncioTestCase):
    def settings(self):
        return Settings(_env_file=None, github_app_id=123, github_client_id="client", github_client_secret=SecretStr("fixture"))

    async def test_oauth_posts_pkce_and_requires_app_user_token(self):
        def handler(request):
            self.assertEqual(request.url.host, "github.com")
            self.assertIn(b"code_verifier=verifier", request.content)
            self.assertNotIn("Authorization", request.headers)
            return httpx.Response(200, json={"access_token": "ghu_fixture", "token_type": "bearer"})
        client = GitHubAppClient(self.settings(), transport=httpx.MockTransport(handler))
        self.assertEqual((await client.oauth({"code": "code", "code_verifier": "verifier"}))["access_token"], "ghu_fixture")
        client.transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"access_token": "ghp_forbidden", "token_type": "bearer"}))
        with self.assertRaises(GitHubError):
            await client.oauth({})

    async def test_installation_filtered_and_suspended_rejected(self):
        client = GitHubAppClient(self.settings(), transport=httpx.MockTransport(lambda request: httpx.Response(200,
            json={"installations": [{"id": 1, "app_id": 123}, {"id": 2, "app_id": 999}, {"id": 3, "app_id": 123, "suspended_at": "date"}]})))
        self.assertEqual([item["id"] for item in await client.installations()], [1])

    async def test_malformed_oauth_and_identity_payloads_fail_closed(self):
        client = GitHubAppClient(self.settings())
        for identity in (True, -1, 0, 2**63):
            client.transport = httpx.MockTransport(lambda request: httpx.Response(200,
                json={"installations": [{"id": identity, "app_id": 123}]}))
            with self.assertRaises(GitHubError):
                await client.installations()
        for payload in ([None], {"installations": [None]}, {"installations": {}}):
            client.transport = httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
            with self.assertRaises(GitHubError):
                await client.installations()
        client.transport = httpx.MockTransport(lambda request: httpx.Response(200,
            json={"access_token": "ghu_fixture", "token_type": True}))
        with self.assertRaises(GitHubError):
            await client.oauth({})

    async def test_unknown_pr_outcome_reconciles_without_reissue(self):
        store = SimpleNamespace(operation=lambda *args: {"id": uuid4(), "status": "UNKNOWN", "response": None},
            operation_state=lambda *args: None, require_unexpired=lambda *args: None)
        service = GitHubWriteService(store, None, self.settings())
        client = SimpleNamespace(request=AsyncMock())
        approval = {"id": str(uuid4())}
        remote = {"number": 1}
        result = await service._effect(approval, uuid4(), client, "draft_pr", "/path", {}, reconcile=AsyncMock(return_value=remote))
        self.assertEqual(result, remote)
        client.request.assert_not_called()
        with self.assertRaisesRegex(GitHubError, "outcome_unknown"):
            await service._effect(approval, uuid4(), client, "draft_pr", "/path", {}, reconcile=AsyncMock(return_value=None))
        client.request.assert_not_called()

    async def test_branch_only_cancellation_reconciles_without_creating_pr(self):
        approval_id, owner = uuid4(), uuid4()
        action = {"repo_id": "owner/repo", "repository_id": 42, "installation_id": 7,
            "head_branch": "agentforge/task-a", "base_branch": "main"}
        branch = {"object": {"sha": "a"*40}}
        client = SimpleNamespace(request=AsyncMock(return_value=branch))
        adapter = SimpleNamespace(authorize=AsyncMock(return_value=(client, {}, {"id": 7})))
        states = []
        store = SimpleNamespace(begin=lambda *args: {"state": "EXECUTING", "action": action},
            sent_delivery_receipt=lambda *args: {"id": uuid4(), "kind": "branch", "commit": {"sha": "a"*40}},
            operation_state=lambda *args: states.append(args[1]),
            complete=lambda approval, user, result: {"state": "CONSUMED", "result": result}, release=lambda *args: None)
        with patch("agentforge.integrations.github_write.AuthorizedGitHubClient", return_value=adapter):
            result = await GitHubWriteService(store, None, self.settings()).execute(approval_id, owner)
        self.assertEqual(states, ["CONFIRMED"])
        self.assertNotIn("pr_url", result["result"])
        self.assertEqual(result["result"]["commit_sha"], "a"*40)
        self.assertEqual(client.request.await_count, 1)
        self.assertEqual(client.request.await_args.args[0], "GET")
        self.assertNotIn("write", adapter.authorize.await_args.kwargs)

    async def test_sent_is_durable_before_network_and_error_becomes_unknown(self):
        states=[]
        store = SimpleNamespace(operation=lambda *args: {"id": uuid4(), "status": "PREPARED", "response": None},
            operation_state=lambda op, state, *args: states.append(state), require_unexpired=lambda *args: None)
        async def failed(*args, **kwargs):
            self.assertEqual(states, ["SENT"])
            raise GitHubError("github_unavailable")
        with self.assertRaises(GitHubError):
            await GitHubWriteService(store, None, self.settings())._effect({"id": str(uuid4())}, uuid4(),
                SimpleNamespace(request=failed), "draft_pr", "/path", {})
        self.assertEqual(states, ["SENT", "UNKNOWN"])

    async def test_body_limit_counts_chunked_bytes_and_length(self):
        app = FastAPI()
        app.add_middleware(RequestBodyLimitMiddleware, maximum=10)
        @app.post("/api/fixture")
        async def fixture(request: Request):
            return {"length": len(await request.body())}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            self.assertEqual((await client.post("/api/fixture", content=b"a"*11)).status_code, 413)
            async def chunks():
                yield b"a"*6
                yield b"b"*6
            response = await client.post("/api/fixture", content=chunks())
            self.assertEqual(response.status_code, 413)
            self.assertEqual((await client.post("/api/fixture", content=b"small")).json(), {"length": 5})

    async def test_signed_malformed_webhook_shapes_fail_before_database(self):
        app = FastAPI()
        app.include_router(github_router)
        app.state.settings = Settings(_env_file=None, github_webhook_secret=SecretStr("x"*32))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            for payload in ({"installation": None}, {"installation": []},
                    {"installation": {"id": 1}, "sender": {"id": 2}, "repositories_removed": [None]},
                    {"installation": {"id": -1}, "sender": {"id": 2}},
                    {"installation": {"id": 1}, "sender": {"id": True}},
                    {"installation": {"id": 2**63}, "sender": {"id": 2}}):
                body = json.dumps(payload).encode()
                response = await client.post("/api/v1/github/webhook", content=body, headers={
                    "X-GitHub-Event": "installation_repositories", "X-GitHub-Delivery": str(uuid4()),
                    "X-Hub-Signature-256": "sha256=" + hmac.new(b"x"*32, body, hashlib.sha256).hexdigest()})
                self.assertEqual(response.status_code, 422)

    async def test_oauth_callback_rotates_session_and_cookie_is_host_secure(self):
        settings = Settings(_env_file=None, app_env="production", auth_mode="github_app", github_app_id=123,
            github_client_id="fixture", github_client_secret=SecretStr("fixture"), github_token_encryption_key=SecretStr("x"*32),
            app_base_url="https://workspace.example", api_public_base_url="https://workspace.example")
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=settings)),
            url=SimpleNamespace(hostname="workspace.example", scheme="https"), headers={},
            cookies={OAUTH_COOKIE_NAME: "browser-binding", PUBLIC_COOKIE_NAME: "old-session"})
        identity = SimpleNamespace(consume=lambda state, binding: "verifier" if (state, binding) == ("state", "browser-binding") else None,
            signup=AsyncMock(return_value=uuid4()))
        store = SimpleNamespace(revoke=lambda token: None, issue=lambda user: "new-session")
        with patch("agentforge.auth.routes.GitHubIdentityService", return_value=identity), patch("agentforge.auth.routes.sessions", return_value=None), patch("agentforge.auth.routes.durable_sessions", return_value=store):
            response = await github_callback(request, code="code", state="state")
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "https://workspace.example/workspace")
        cookie = next(value for key, value in response.raw_headers if key == b"set-cookie" and PUBLIC_COOKIE_NAME.encode() in value)
        self.assertIn(b"HttpOnly", cookie)
        self.assertIn(b"Secure", cookie)
        self.assertIn(b"Path=/;", cookie)
        self.assertNotIn(b"Domain=", cookie)
        identity.signup.assert_awaited_once_with("code", "verifier")
        request.url.scheme = "http"
        with self.assertRaises(HTTPException) as rejected:
            require_public_transport(request)
        self.assertEqual(rejected.exception.status_code, 503)

    async def test_oauth_start_binds_state_and_s256_without_exposing_verifier(self):
        settings = Settings(_env_file=None, auth_mode="github_app", github_app_id=123, github_client_id="fixture",
            github_client_secret=SecretStr("fixture"), github_token_encryption_key=SecretStr("x"*32))
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=settings)),
            url=SimpleNamespace(hostname="localhost", scheme="http"), headers={})
        captured = []
        identity = SimpleNamespace(start=lambda *values: captured.append(values))
        with patch("agentforge.auth.routes.GitHubIdentityService", return_value=identity), patch("agentforge.auth.routes.sessions", return_value=None):
            response = github_start(request)
        state, binding, verifier = captured[0]
        query = parse_qs(urlsplit(response.headers["location"]).query)
        import base64
        expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        self.assertEqual(query["state"], [state])
        self.assertEqual(query["code_challenge"], [expected])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertNotIn(verifier, response.headers["location"])
        self.assertIn(binding, response.headers["set-cookie"])

if __name__ == "__main__":
    unittest.main()
