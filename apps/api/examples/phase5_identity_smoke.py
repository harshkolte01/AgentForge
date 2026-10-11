"""Disposable PostgreSQL and mocked GitHub qualification of identity: sessions, OAuth and webhooks; no live providers."""
import asyncio
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4
import httpx
from pydantic import SecretStr
from sqlalchemy import create_engine, select, text, func
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from agentforge.settings import Settings
from _disposable_db import admin_engine
import agentforge.settings as settings_module

ROOT = Path(__file__).resolve().parents[3]
name = "agentforge_identity_smoke_" + uuid4().hex[:12]
admin = engine = None
created, stage = False, "setup"

def check(value, message):
    if not value:
        raise AssertionError(message)

async def run(settings):
    global engine, stage
    from alembic import command
    from alembic.config import Config
    cfg = Config(str(ROOT / "apps/api/alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "apps/api/alembic"))
    stage = "migrations"
    command.upgrade(cfg, "head")
    from agentforge.database.models import User, Project
    from agentforge.database.identity import AuthSession, GitHubCredential
    from agentforge.auth.github_service import GitHubIdentityService
    from agentforge.auth.durable import DurableSessionStore, now
    from agentforge.integrations.github_client import GitHubError
    engine = create_engine(settings.database_url)
    sessions = sessionmaker(engine, expire_on_commit=False)
    owner, outsider, project = uuid4(), uuid4(), uuid4()
    with sessions.begin() as db:
        db.add_all([User(id=owner, github_user_id=8123, display_name="Fixture"), User(id=outsider, github_user_id=8124, display_name="Other")])
        db.flush()
        db.add(Project(id=project, owner_id=owner, name="Fixture"))
    stage = "durable session serialization"
    store = DurableSessionStore(sessions, settings)
    with ThreadPoolExecutor(max_workers=4) as pool:
        tokens = list(pool.map(lambda _: store.issue(owner), range(14)))
    with sessions() as db:
        check(db.scalar(select(func.count()).select_from(AuthSession).where(AuthSession.revoked_at.is_(None))) == 10, "session cap race")
    durable = DurableSessionStore(sessions, settings)
    check(sum(durable.resolve(token) == owner for token in tokens) == 10, "restart session continuity")
    token = tokens[-1]
    durable.revoke(token)
    check(durable.resolve(token) is None, "revoked session accepted")
    stage = "oauth state and rotation"
    identity = GitHubIdentityService(sessions, settings)
    identity.start("state-fixture", "binding-fixture", "verifier-fixture")
    try:
        identity.consume("state-fixture", "wrong-binding")
    except GitHubError:
        pass
    else:
        raise AssertionError("OAuth browser binding bypass")
    check(identity.consume("state-fixture", "binding-fixture") == ("verifier-fixture", None), "PKCE verifier mismatch")
    try:
        identity.consume("state-fixture", "binding-fixture")
    except GitHubError:
        pass
    else:
        raise AssertionError("OAuth state replay")
    with sessions.begin() as db:
        row = GitHubCredential(user_id=owner)
        identity._store_token(row, {"access_token": "ghu_fixture", "refresh_token": "ghr_fixture", "expires_in": 28800, "refresh_token_expires_in": 15897600})
        db.add(row)
    stage = "public signup and rotating refresh"
    def auth_handler(request):
        if request.url.path == "/login/oauth/access_token":
            return httpx.Response(200, json={"access_token": "ghu_signup_fixture", "refresh_token": "ghr_signup_fixture", "token_type": "bearer",
                "expires_in": 28800, "refresh_token_expires_in": 15897600})
        if request.url.path == "/user":
            return httpx.Response(200, json={"id": 900123, "login": "public-signup-fixture"})
        raise AssertionError("unexpected auth provider request")
    signup = GitHubIdentityService(sessions, settings, transport=httpx.MockTransport(auth_handler))
    signup_id = await signup.signup("fixture-code", "fixture-verifier")
    check(await signup.signup("fixture-code", "fixture-verifier") == signup_id, "duplicate GitHub identity created")
    with sessions.begin() as db:
        check(db.scalar(select(func.count()).select_from(Project).where(Project.owner_id == signup_id)) == 1, "signup default workspace duplication")
        credential = db.scalar(select(GitHubCredential).where(GitHubCredential.user_id == signup_id))
        check("ghu_" not in credential.access_encrypted and "ghr_" not in credential.refresh_encrypted, "cleartext credential persisted")
        credential.expires_at = now() - timedelta(seconds=1)
    check(await signup.access_token(signup_id) == "ghu_signup_fixture", "rotating refresh failed")
    with sessions.begin() as db:
        credential = db.scalar(select(GitHubCredential).where(GitHubCredential.user_id == signup_id))
        credential.expires_at = now() - timedelta(seconds=1)
    def revoked_during_refresh(request):
        with sessions.begin() as db:
            db.scalar(select(GitHubCredential).where(GitHubCredential.user_id == signup_id)).revoked_at = now()
        return auth_handler(request)
    racing = GitHubIdentityService(sessions, settings, transport=httpx.MockTransport(revoked_during_refresh))
    try:
        await racing.access_token(signup_id)
    except GitHubError:
        pass
    else:
        raise AssertionError("refresh restored concurrently revoked credential")
    with sessions() as db:
        check(db.scalar(select(GitHubCredential).where(GitHubCredential.user_id == signup_id)).revoked_at is not None,
            "refresh erased revocation")
    await signup.signup("fixture-code", "fixture-verifier")
    with sessions.begin() as db:
        db.scalar(select(GitHubCredential).where(GitHubCredential.user_id == signup_id)).expires_at = now() - timedelta(seconds=1)
    async def lost_refresh(request):
        raise httpx.ReadTimeout("simulated lost refresh response")
    uncertain = GitHubIdentityService(sessions, settings, transport=httpx.MockTransport(lost_refresh))
    for _ in range(2):
        try:
            await uncertain.access_token(signup_id)
        except GitHubError as error:
            check(str(error) == "github_reauthorization_required", "refresh uncertainty not fenced")
        else:
            raise AssertionError("unknown refresh outcome accepted")
    stage = "cached grant upsert"
    from agentforge.database.identity import GitHubInstallation, GitHubRepositoryGrant
    installation = {"id": 4401, "account": {"id": 77, "login": "fixture-org"}, "permissions": {"contents": "write"}}
    def repo(identifier, full_name, push):
        return {"id": identifier, "full_name": full_name, "permissions": {"push": push}}
    signup.record_grants(signup_id, [(installation, repo(501, "fixture-org/b", False)), (installation, repo(500, "fixture-org/a", False))])
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: signup.record_grants(signup_id, [(dict(installation, permissions={"contents": "read"}),
            repo(500, "fixture-org/renamed", True))]), range(4)))
    with sessions() as db:
        grants = {row.repository_id: row for row in db.scalars(select(GitHubRepositoryGrant).where(GitHubRepositoryGrant.user_id == signup_id))}
        check(set(grants) == {500, 501} and grants[500].full_name == "fixture-org/renamed"
            and grants[500].permissions_json == {"push": True}, "grant upsert")
        check(db.get(GitHubInstallation, 4401).permissions_json == {"contents": "read"}, "installation upsert")
    stage = "webhook durable replay and revocation"
    from fastapi import FastAPI
    from agentforge.api.routes_github import router as github_router
    import hmac
    app = FastAPI()
    app.include_router(github_router)
    app.state.settings, app.state.engine = settings, engine
    raw = json.dumps({"installation": {"id": 7}, "sender": {"id": 80}, "action": "suspend"}).encode()
    signature = "sha256=" + hmac.new(settings.github_webhook_secret.get_secret_value().encode(), raw, hashlib.sha256).hexdigest()
    headers = {"X-GitHub-Event": "installation", "X-GitHub-Delivery": str(uuid4()), "X-Hub-Signature-256": signature}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture") as http:
        check((await http.post("/api/v1/github/webhook", content=raw, headers=headers)).status_code == 202, "valid webhook rejected")
        check((await http.post("/api/v1/github/webhook", content=raw, headers=headers)).status_code == 409, "webhook replay accepted")
        check((await http.post("/api/v1/github/webhook", content=raw+b" ", headers=headers)).status_code == 403, "forged raw body accepted")
    print("PASS durable session cap/restart/revocation; PKCE state binding/replay; rotating refresh with concurrent revocation and lost responses; signed webhook replay and forgery", flush=True)

try:
    source = make_url(Settings().database_url)
    check(source.host in ("localhost", "127.0.0.1", "::1"), "nonlocal configured DB")
    admin = admin_engine(source)
    with admin.connect() as connection:
        owner = connection.dialect.identifier_preparer.quote_identifier(source.username)
        connection.execute(text('CREATE DATABASE "' + name + '" OWNER ' + owner))
    created = True
    settings = Settings(_env_file=None, database_url=source.set(database=name).render_as_string(hide_password=False),
        app_env="test", auth_mode="github_app", github_app_id=123, github_client_id="fixture", github_client_secret=SecretStr("fixture"),
        github_token_encryption_key=SecretStr("fixture-encryption-"+"x"*32), enable_github_writes=True,
        github_webhook_secret=SecretStr("fixture-webhook-"+"x"*32),
        llm_api_key=None, tavily_api_key=None, neatlogs_api_key=None)
    with patch.object(settings_module, "get_settings", return_value=settings):
        asyncio.run(run(settings))
except BaseException as exc:
    import traceback
    print("FAIL stage=" + stage + " class=" + type(exc).__name__, flush=True)
    if isinstance(exc, AssertionError):
        print("ASSERTION " + str(exc), flush=True)
    for frame in traceback.extract_tb(exc.__traceback__):
        print(f"LOCATION {Path(frame.filename).name}:{frame.lineno} {frame.name}", flush=True)
    raise SystemExit(1) from None
finally:
    if engine is not None:
        engine.dispose()
    if created:
        with admin.connect() as connection:
            connection.execute(text('DROP DATABASE "' + name + '" WITH (FORCE)'))
        print("PASS disposable database removed", flush=True)
    if admin is not None:
        admin.dispose()
