"""Sign-in, GitHub connection and configuration contracts the web app and operators rely on. Provider- and DB-free."""
import os
import stat
import tempfile
import unittest
from contextlib import nullcontext, redirect_stdout
from datetime import timedelta
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx
from fastapi import HTTPException
from pydantic import SecretStr, ValidationError
from sqlalchemy.dialects import postgresql

from agentforge.auth import github_service
from agentforge.auth.durable import OAUTH_COOKIE_NAME, PUBLIC_COOKIE_NAME, now
from agentforge.auth.github_service import GitHubIdentityService
from agentforge.auth.routes import github_callback, github_start, safe_next
from agentforge.integrations.github_client import GitHubError
from agentforge.settings import Settings, validation_summary

APP = "https://workspace.example"


def github_settings(**overrides):
    values = dict(_env_file=None, app_env="production", auth_mode="github_app", github_app_id=123, github_app_slug="forge",
        github_client_id="fixture", github_client_secret=SecretStr("fixture"), github_token_encryption_key=SecretStr("x" * 32),
        app_base_url=APP, api_public_base_url=APP)
    return Settings(**{**values, **overrides})


def browser(settings, cookies=None, headers=None):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=settings)),
        url=SimpleNamespace(hostname="workspace.example", scheme="https"), headers=headers or {}, cookies=cookies or {})


class SettingsTests(unittest.TestCase):
    def test_github_mode_names_missing_settings_and_never_their_values(self):
        with self.assertRaises(ValidationError) as raised:
            Settings(_env_file=None, auth_mode="github_app", github_client_secret=SecretStr("sekrit-fixture"))
        summary = validation_summary(raised.exception)
        for name in ("GITHUB_CLIENT_ID", "GITHUB_APP_ID", "GITHUB_TOKEN_ENCRYPTION_KEY"):
            self.assertIn(name, summary)
        self.assertNotIn("GITHUB_CLIENT_SECRET", summary)
        self.assertNotIn("sekrit", summary)

    def test_production_requires_github_sign_in_and_https(self):
        with self.assertRaises(ValidationError) as raised:
            Settings(_env_file=None, app_env="production", auth_mode="local_single_user")
        summary = validation_summary(raised.exception)
        self.assertIn("AUTH_MODE=github_app", summary)
        self.assertIn("https APP_BASE_URL", summary)
        with self.assertRaises(ValidationError) as raised:
            github_settings(allowed_origins="https://workspace.example,http://other.example")
        self.assertIn("ALLOWED_ORIGINS", validation_summary(raised.exception))
        self.assertEqual(github_settings().allowed_origins, [APP])

    def test_allowed_origins_default_to_the_web_app_origin(self):
        self.assertEqual(Settings(_env_file=None).allowed_origins, ["http://localhost:3000"])
        self.assertEqual(Settings(_env_file=None, app_base_url="http://127.0.0.1:3001/app").allowed_origins, ["http://127.0.0.1:3001"])
        self.assertEqual(Settings(_env_file=None, allowed_origins="http://a.test, http://b.test").allowed_origins,
            ["http://a.test", "http://b.test"])

    def test_allowlist_and_session_defaults(self):
        settings = Settings(_env_file=None, github_allowed_logins=" @Alice, bob ,")
        self.assertEqual(settings.github_allowed_logins, ["alice", "bob"])
        self.assertEqual((settings.session_idle_ttl_sec, settings.session_absolute_ttl_sec, settings.local_session_ttl_sec),
            (86400, 604800, 86400))

    def test_api_startup_reports_invalid_settings_without_values(self):
        from agentforge import main
        try:
            Settings(_env_file=None, auth_mode="github_app", github_client_secret=SecretStr("sekrit-fixture"),
                github_client_id="leaky-client-fixture")
        except ValidationError as error:
            invalid = error
        with patch.object(main, "get_settings", side_effect=invalid), self.assertRaises(SystemExit) as raised:
            main.create_app()
        message = str(raised.exception.code)
        self.assertIn("GITHUB_APP_ID", message)
        self.assertNotIn("sekrit", message)
        self.assertNotIn("leaky", message)


class CallbackTests(unittest.IsolatedAsyncioTestCase):
    async def callback(self, *, service=None, store=None, cookies=None, **query):
        settings = github_settings()
        cookies = {OAUTH_COOKIE_NAME: "binding", **(cookies or {})}
        service = service or SimpleNamespace(consume=lambda state, binding: ("verifier", None), signup=AsyncMock(return_value=uuid4()))
        store = store or SimpleNamespace(revoke=lambda token: None, issue=lambda user: "new-session", resolve=lambda token: None)
        with patch("agentforge.auth.routes.GitHubIdentityService", return_value=service), \
                patch("agentforge.auth.routes.sessions", return_value=None), \
                patch("agentforge.auth.routes.durable_sessions", return_value=store):
            response = await github_callback(browser(settings, cookies), **query)
        self.assertEqual(response.status_code, 303)
        # The one-use binding cookie is always cleared.
        self.assertTrue(any(key == b"set-cookie" and OAUTH_COOKIE_NAME.encode() in value and b"Max-Age=0" in value
            for key, value in response.raw_headers))
        return response.headers["location"]

    def code(self, location):
        return parse_qs(urlsplit(location).query).get("auth_error", [None])[0]

    async def test_cancel_is_denied_and_spends_the_state(self):
        consumed = []
        service = SimpleNamespace(consume=lambda state, binding: consumed.append(state) or ("verifier", None), signup=AsyncMock())
        location = await self.callback(service=service, state="state", error="access_denied")
        self.assertEqual(location, APP + "/chat?auth_error=denied")
        self.assertEqual(consumed, ["state"])
        service.signup.assert_not_awaited()
        self.assertEqual(self.code(await self.callback(state="state", error="redirect_uri_mismatch")), "app_misconfigured")

    async def test_stale_or_replayed_state_is_expired(self):
        def consume(state, binding):
            raise GitHubError("github_authorization_expired")
        self.assertEqual(self.code(await self.callback(service=SimpleNamespace(consume=consume), code="c", state="s")), "expired")

    async def test_each_signup_failure_maps_to_a_stable_code(self):
        cases = {"github_signup_disabled": "signup_closed", "github_login_not_allowed": "not_allowed",
            "github_expiring_tokens_required": "app_misconfigured", "github_refresh_token_required": "app_misconfigured",
            "github_app_user_token_required": "app_misconfigured", "github_identity_invalid": "app_misconfigured",
            "github_app_misconfigured": "app_misconfigured", "github_rate_limited": "rate_limited",
            "github_unreachable": "unavailable", "github_unavailable": "unavailable", "github_authorization_expired": "expired"}
        for failure, expected in cases.items():
            with self.subTest(failure=failure):
                service = SimpleNamespace(consume=lambda state, binding: ("verifier", None),
                    signup=AsyncMock(side_effect=GitHubError(failure)))
                self.assertEqual(self.code(await self.callback(service=service, code="c", state="s")), expected)

    async def test_an_unexpected_failure_redirects_and_logs_only_its_type(self):
        service = SimpleNamespace(consume=lambda state, binding: ("verifier", None),
            signup=AsyncMock(side_effect=RuntimeError("ghu_private_fixture")))
        with self.assertLogs("agentforge.auth.routes", "ERROR") as logs:
            location = await self.callback(service=service, code="c", state="s")
        self.assertEqual(self.code(location), "unavailable")
        self.assertIn("RuntimeError", logs.output[0])
        self.assertNotIn("ghu_private_fixture", "".join(logs.output))

    async def test_success_lands_on_the_requested_page(self):
        service = SimpleNamespace(consume=lambda state, binding: ("verifier", "/settings?section=github"),
            signup=AsyncMock(return_value=uuid4()))
        self.assertEqual(await self.callback(service=service, code="c", state="s"), APP + "/settings?section=github")
        self.assertEqual(await self.callback(code="c", state="s", installation_id="9", setup_action="install"),
            APP + "/settings?section=github&installed=1")

    async def test_post_install_redirect_without_our_state_never_signs_in(self):
        service = SimpleNamespace(consume=MagicMock(), signup=AsyncMock())
        signed_in = SimpleNamespace(resolve=lambda token: uuid4() if token == "live" else None)
        location = await self.callback(service=service, store=signed_in, cookies={PUBLIC_COOKIE_NAME: "live"},
            code="unrequested", installation_id="9", setup_action="install")
        self.assertEqual(location, APP + "/settings?section=github&installed=1")
        location = await self.callback(service=service, store=signed_in, cookies={OAUTH_COOKIE_NAME: ""},
            code="unrequested", installation_id="9", setup_action="install")
        self.assertEqual(urlsplit(location).path, "/api/v1/auth/github/start")
        self.assertEqual(parse_qs(urlsplit(location).query)["next"], ["/settings?section=github&installed=1"])
        service.consume.assert_not_called()
        service.signup.assert_not_awaited()


class StartTests(unittest.TestCase):
    def test_navigation_failures_redirect_with_a_code_and_fetches_get_json(self):
        unconfigured = Settings(_env_file=None, app_base_url=APP, api_public_base_url=APP)
        navigation = {"sec-fetch-mode": "navigate"}
        response = github_start(browser(unconfigured, headers=navigation))
        self.assertEqual(response.headers["location"], APP + "/chat?auth_error=app_misconfigured")
        with self.assertRaises(HTTPException) as raised:
            github_start(browser(unconfigured))
        self.assertEqual(raised.exception.status_code, 503)
        def limited(*values):
            raise GitHubError("github_login_rate_limited")
        with patch("agentforge.auth.routes.GitHubIdentityService", return_value=SimpleNamespace(start=limited)), \
                patch("agentforge.auth.routes.sessions", return_value=None):
            response = github_start(browser(github_settings(), headers={"accept": "text/html"}))
            self.assertEqual(response.headers["location"], APP + "/chat?auth_error=rate_limited")
            with self.assertRaises(HTTPException) as raised:
                github_start(browser(github_settings()))
            self.assertEqual(raised.exception.status_code, 429)

    def test_only_same_origin_paths_survive_as_next(self):
        for value in ("/settings?section=github", "/chat/abc"):
            self.assertEqual(safe_next(value), value)
        for value in ("", "settings", "//evil.example", "/\\evil.example", "https://evil.example", "/a b", "/x\n", "/" + "a" * 600):
            self.assertIsNone(safe_next(value))
        captured = []
        with patch("agentforge.auth.routes.GitHubIdentityService", return_value=SimpleNamespace(start=lambda *values: captured.append(values))), \
                patch("agentforge.auth.routes.sessions", return_value=None):
            github_start(browser(github_settings()), next="//evil.example")
            github_start(browser(github_settings()), next="/settings?section=github")
        self.assertEqual([values[3] for values in captured], [None, "/settings?section=github"])


class FakeDatabase:
    """Just enough of a SQLAlchemy session for one service transaction."""
    def __init__(self, *rows):
        self.rows, self.added, self.executed = list(rows), [], []

    def __call__(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def begin(self):
        return nullcontext()

    def scalar(self, statement):
        return self.rows.pop(0) if self.rows else None

    def execute(self, statement, *args):
        self.executed.append(statement)

    def add(self, row):
        self.added.append(row)

    def flush(self):
        pass


TOKENS = {"access_token": "ghu_new", "token_type": "bearer", "expires_in": 28800, "refresh_token": "ghr_new",
    "refresh_token_expires_in": 15897600}


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    def service(self, handler=None, database=None, **settings):
        transport = handler if handler is None or isinstance(handler, httpx.MockTransport) else httpx.MockTransport(handler)
        return GitHubIdentityService(database or FakeDatabase(), github_settings(**settings), transport=transport)

    async def test_token_endpoint_failures_are_classified(self):
        def raises(error):
            def handler(request):
                raise error
            return handler
        cases = [
            (raises(httpx.ConnectError("refused")), "github_unreachable"),
            (raises(httpx.ConnectTimeout("slow")), "github_unreachable"),
            (raises(httpx.ReadTimeout("lost")), "github_unavailable"),
            (lambda request: httpx.Response(502), "github_unavailable"),
            (lambda request: httpx.Response(429), "github_rate_limited"),
            (lambda request: httpx.Response(200, json={"error": "bad_verification_code"}), "github_authorization_expired"),
            (lambda request: httpx.Response(200, json={"error": "incorrect_client_credentials"}), "github_app_misconfigured"),
            (lambda request: httpx.Response(200, json={"error": "bad_refresh_token"}), "github_reauthorization_required"),
            (lambda request: httpx.Response(200, json={"access_token": "gho_oauth_app", "token_type": "bearer"}),
                "github_app_user_token_required"),
        ]
        for handler, expected in cases:
            with self.subTest(expected=expected), self.assertRaises(GitHubError) as raised:
                await self.service(handler).token_request({"code": "c"})
            self.assertEqual(str(raised.exception), expected)
        value = await self.service(lambda request: httpx.Response(200, json=TOKENS)).token_request({"code": "c"})
        self.assertEqual(value["access_token"], "ghu_new")

    async def refresh_with(self, handler):
        service = self.service(handler)
        stamp = now()
        service._claim_refresh = MagicMock(return_value=("refresh", (stamp, "ghr_old")))
        service._end_refresh, service._finish_refresh = MagicMock(), MagicMock()
        return service, stamp

    async def test_an_unsent_refresh_releases_the_fence_instead_of_revoking(self):
        def refused(request):
            raise httpx.ConnectError("refused")
        for handler in (refused, lambda request: httpx.Response(429)):
            service, stamp = await self.refresh_with(handler)
            with self.assertRaises(GitHubError) as raised:
                await service.access_token("user")
            self.assertEqual(str(raised.exception), "github_unavailable")
            service._end_refresh.assert_called_once_with("user", stamp, revoke=False)

    async def test_a_rejected_or_lost_refresh_revokes(self):
        def lost(request):
            raise httpx.ReadTimeout("lost")
        for handler in (lambda request: httpx.Response(200, json={"error": "bad_refresh_token"}),
                lambda request: httpx.Response(400), lost):
            service, stamp = await self.refresh_with(handler)
            with self.assertRaises(GitHubError) as raised:
                await service.access_token("user")
            self.assertEqual(str(raised.exception), "github_reauthorization_required")
            service._end_refresh.assert_called_once_with("user", stamp, revoke=True)

    async def test_a_successful_refresh_is_stored_under_its_fence(self):
        service, stamp = await self.refresh_with(lambda request: httpx.Response(200, json=TOKENS))
        self.assertEqual(await service.access_token("user"), "ghu_new")
        service._finish_refresh.assert_called_once_with("user", stamp, TOKENS)
        service._end_refresh.assert_not_called()

    async def test_concurrent_callers_wait_for_the_refresh_in_flight(self):
        service = self.service()
        service._claim_refresh = MagicMock(side_effect=[("wait", None), ("wait", None), ("token", "ghu_rotated")])
        with patch.object(github_service.asyncio, "sleep", AsyncMock()):
            self.assertEqual(await service.access_token("user"), "ghu_rotated")
        service._claim_refresh = MagicMock(return_value=("wait", None))
        with patch.object(github_service, "REFRESH_WAIT_SEC", 0), self.assertRaises(GitHubError) as raised:
            await service.access_token("user")
        self.assertEqual(str(raised.exception), "github_unavailable")

    def test_claim_refresh_waits_only_for_a_fresh_fence(self):
        service = self.service()
        def credential(**values):
            row = SimpleNamespace(revoked_at=None, refresh_started_at=None, expires_at=now() - timedelta(minutes=1),
                refresh_expires_at=now() + timedelta(days=30), access_encrypted=service.cipher.encrypt("ghu_old"),
                refresh_encrypted=service.cipher.encrypt("ghr_old"))
            row.__dict__.update(values)
            return row
        service.session_factory = FakeDatabase(credential(refresh_started_at=now() - timedelta(seconds=5)))
        self.assertEqual(service._claim_refresh("user"), ("wait", None))
        stale = credential(refresh_started_at=now() - timedelta(seconds=120))
        service.session_factory = FakeDatabase(stale)
        action, (stamp, refresh) = service._claim_refresh("user")
        self.assertEqual((action, refresh, stale.refresh_started_at), ("refresh", "ghr_old", stamp))
        service.session_factory = FakeDatabase(credential(expires_at=now() + timedelta(hours=1)))
        self.assertEqual(service._claim_refresh("user"), ("token", "ghu_old"))
        for row in (None, credential(revoked_at=now()), credential(refresh_expires_at=now()),
                credential(refresh_encrypted="v1:not-decryptable")):
            service.session_factory = FakeDatabase(row)
            with self.assertRaises(GitHubError) as raised:
                service._claim_refresh("user")
            self.assertEqual(str(raised.exception), "github_reauthorization_required")

    async def test_allowlist_is_checked_before_any_account_is_touched(self):
        service = self.service(github_allowed_logins="alice")
        service.token_request = AsyncMock(return_value=TOKENS)
        service._save_identity = MagicMock(return_value="user")
        with patch("agentforge.auth.github_service.GitHubAppClient") as client:
            client.return_value.request = AsyncMock(return_value={"id": 7, "login": "Mallory"})
            with self.assertRaises(GitHubError) as raised:
                await service.signup("code", "verifier")
            self.assertEqual(str(raised.exception), "github_login_not_allowed")
            service._save_identity.assert_not_called()
            client.return_value.request = AsyncMock(return_value={"id": 8, "login": "ALICE"})
            self.assertEqual(await service.signup("code", "verifier"), "user")

    def test_closed_signup_admits_allowlisted_newcomers_only(self):
        with self.assertRaises(GitHubError) as raised:
            self.service(public_signup_enabled=False)._save_identity(7, "newcomer", TOKENS)
        self.assertEqual(str(raised.exception), "github_signup_disabled")
        database = FakeDatabase()
        service = self.service(database=database, public_signup_enabled=False, github_allowed_logins="newcomer")
        service._save_identity(7, "newcomer", TOKENS)
        self.assertEqual(sorted(type(row).__name__ for row in database.added), ["GitHubCredential", "Project", "User"])

    def test_record_grants_is_one_ordered_upsert_per_table(self):
        database = FakeDatabase()
        installation = {"id": 5, "account": {"id": 1, "login": "org"}, "permissions": {"contents": "write"}}
        pairs = [(installation, {"id": 30, "full_name": "org/b", "permissions": {}}),
                 (installation, {"id": 10, "full_name": "org/a", "permissions": {}}),
                 (installation, {"id": 10, "full_name": "org/a", "permissions": {"push": True}})]
        user = uuid4()
        self.service(database=database).record_grants(user, pairs)
        installs, grants = (str(statement.compile(dialect=postgresql.dialect())) for statement in database.executed)
        self.assertNotIn("advisory", installs + grants)
        self.assertIn("ON CONFLICT (id) DO UPDATE", installs)
        self.assertIn("ON CONFLICT (user_id, repository_id) DO UPDATE", grants)
        parameters = database.executed[1].compile(dialect=postgresql.dialect()).params
        self.assertEqual([parameters["repository_id_m0"], parameters["repository_id_m1"]], [10, 30])
        self.assertEqual(parameters["permissions_json_m0"], {"push": True})
        self.service(database=database).record_grants(user, [])
        self.assertEqual(len(database.executed), 2)


class StatusTests(unittest.IsolatedAsyncioTestCase):
    async def status(self, credential, access=None, installations=()):
        from agentforge.api import routes_github
        settings = github_settings()
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=settings)))
        service = SimpleNamespace(access_token=access or AsyncMock(return_value="ghu_fixture"))
        with patch.object(routes_github, "stored_credential", return_value=credential), \
                patch.object(routes_github, "identity", return_value=service), \
                patch.object(routes_github, "GitHubAppClient") as client:
            client.return_value.installations = AsyncMock(return_value=list(installations))
            return await routes_github.status(request, user=SimpleNamespace(id=uuid4()))

    async def test_status_reports_installations_and_reauthorization(self):
        live = SimpleNamespace(revoked_at=None, refresh_started_at=now(), refresh_expires_at=now() + timedelta(days=30))
        result = await self.status(live, installations=[{"id": 1}, {"id": 2}])
        self.assertEqual((result["connected"], result["reauthorization_required"], result["installations"]), (True, False, 2))
        self.assertEqual(result["install_url"], "https://github.com/apps/forge/installations/new")
        rejected = AsyncMock(side_effect=GitHubError("github_reauthorization_required"))
        result = await self.status(live, access=rejected)
        self.assertEqual((result["connected"], result["reauthorization_required"], result["installations"]), (False, True, None))
        result = await self.status(live, access=AsyncMock(side_effect=GitHubError("github_unavailable")))
        self.assertEqual((result["connected"], result["reauthorization_required"], result["installations"]), (True, False, None))
        revoked = SimpleNamespace(revoked_at=now(), refresh_started_at=None, refresh_expires_at=now() + timedelta(days=1))
        result = await self.status(revoked)
        self.assertEqual((result["connected"], result["reauthorization_required"], result["installations"]), (False, True, None))
        result = await self.status(None)
        self.assertEqual((result["configured"], result["connected"], result["reauthorization_required"]), (True, False, False))


class SessionTests(unittest.TestCase):
    def test_local_sessions_are_durable_with_the_local_lifetime(self):
        from agentforge.auth.durable import DurableSessionStore
        from agentforge.auth.session import local_sessions
        settings = Settings(_env_file=None, local_session_ttl_sec=7200)
        store = local_sessions(SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=settings, engine=None))))
        self.assertIsInstance(store, DurableSessionStore)
        self.assertEqual((store.settings.session_absolute_ttl_sec, store.settings.session_idle_ttl_sec), (7200, 7200))

    def test_a_local_identity_never_authenticates_github_mode(self):
        from agentforge.auth import session
        settings = github_settings()
        local = SimpleNamespace(github_user_id=-5)
        database = SimpleNamespace(get=lambda model, key: local, expunge=lambda row: None)
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=settings, engine=None)),
            cookies={PUBLIC_COOKIE_NAME: "token"}, method="GET")
        with patch.object(session, "require_public_transport"), \
                patch.object(session, "durable_sessions", return_value=SimpleNamespace(resolve=lambda token: uuid4())), \
                patch.object(session, "Session", return_value=nullcontext(database)):
            with self.assertRaises(HTTPException) as raised:
                session.current_user(request)
            self.assertEqual(raised.exception.status_code, 401)
            local.github_user_id = 99
            self.assertIs(session.current_user(request), local)


class BootstrapTests(unittest.TestCase):
    def test_write_env_fills_only_missing_values_and_keeps_the_file_private(self):
        from agentforge.auth.bootstrap import fill_env
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("# keep me\nAUTH_MODE=\nLOCAL_USER_ID=already-set\nOTHER='x'\n", encoding="utf-8")
            os.chmod(path, 0o644)
            written = fill_env(path, {"AUTH_MODE": "local_single_user", "LOCAL_USER_ID": "new", "LOCAL_BOOTSTRAP_TOKEN": "t" * 48})
            self.assertEqual(written, ["AUTH_MODE", "LOCAL_BOOTSTRAP_TOKEN"])
            self.assertEqual(path.read_text(encoding="utf-8"),
                "# keep me\nAUTH_MODE=local_single_user\nLOCAL_USER_ID=already-set\nOTHER='x'\nLOCAL_BOOTSTRAP_TOKEN=" + "t" * 48 + "\n")
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(fill_env(path, {"AUTH_MODE": "disabled"}), [])
            created = Path(directory) / "fresh.env"
            self.assertEqual(fill_env(created, {"AUTH_MODE": "local_single_user"}), ["AUTH_MODE"])
            self.assertEqual(stat.S_IMODE(created.stat().st_mode), 0o600)


class DoctorTests(unittest.TestCase):
    def test_doctor_prints_names_not_values(self):
        from agentforge import doctor
        settings = Settings(_env_file=None, auth_mode="local_single_user", local_user_id=uuid4(),
            local_bootstrap_token=SecretStr("b" * 40), e2b_api_key=SecretStr("e2b-private-fixture"),
            llm_api_key=SecretStr("sk-private-fixture"), llm_model="fixture-model")
        output = StringIO()
        with patch.object(doctor, "check_settings", return_value=(settings, [("PASS", "settings", "valid")])), \
                patch.object(doctor, "check_database", return_value=([("PASS", "database", "reachable")], True)), \
                redirect_stdout(output):
            code = doctor.main()
        text = output.getvalue()
        self.assertEqual(code, 1)   # the model's limits and prices are missing
        self.assertIn("FAIL  model", text)
        self.assertIn("PASS  sign-in", text)
        self.assertIn("WARN  tavily", text)
        for secret in ("e2b-private-fixture", "sk-private-fixture", "b" * 40, str(settings.local_user_id)):
            self.assertNotIn(secret, text)

    def test_disabled_sign_in_fails(self):
        from agentforge.doctor import check_auth
        self.assertEqual(check_auth(Settings(_env_file=None), None)[0][0], "FAIL")


if __name__ == "__main__":
    unittest.main()
