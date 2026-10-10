"""Disposable PostgreSQL and mocked GitHub qualification; no live providers."""
import asyncio
import getpass
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
from sqlalchemy.engine import make_url, URL
from sqlalchemy.orm import sessionmaker
from agentforge.settings import Settings
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
    from agentforge.database.models import User, Project, Task
    from agentforge.database.identity import AuthSession, Approval, ExternalOperation, GitHubCredential
    from agentforge.database.coding import CodingSession, CodingSnapshot, CodingEvidence, CodingArtifact
    from agentforge.auth.github_service import GitHubIdentityService
    from agentforge.auth.durable import DurableSessionStore, now
    from agentforge.contracts.approvals import ApprovalPrepare, ApprovalDecision
    from agentforge.repositories.approvals import ApprovalRepository, ApprovalError
    from agentforge.repositories.coding import _encode
    from agentforge.sandbox.workspace import workspace_revision, build_diff
    from agentforge.integrations.github_write import GitHubWriteService, blob_sha
    from agentforge.integrations.github_client import GitHubError
    engine = create_engine(settings.database_url)
    sessions = sessionmaker(engine, expire_on_commit=False)
    owner, outsider, project, task_id, coding_id = uuid4(), uuid4(), uuid4(), uuid4(), uuid4()
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
    check(identity.consume("state-fixture", "binding-fixture") == "verifier-fixture", "PKCE verifier mismatch")
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
    from agentforge.integrations.github_app import GitHubAppClient
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
    stage = "verified coding fixture"
    base = {"app.py": b"x = 1\n", "test_app.py": b"assert x == 2\n"}
    current = {**base, "app.py": b"x = 2\n"}
    base_revision, revision = workspace_revision(base), workspace_revision(current)
    base_sha, tree_sha, new_tree_sha, commit_sha = "b"*40, "c"*40, "d"*40, "e"*40
    diff = build_diff(base, current).encode()
    evidence_ids = [uuid4(), uuid4(), uuid4()]
    artifact_id = uuid4()
    with sessions.begin() as db:
        db.add(Task(id=task_id, created_by=owner, project_id=project, goal="Fix fixture", status="COMPLETED", thread_id=str(task_id),
            max_cost_usd=1, max_wall_time_sec=60, permissions_json={"kind": "coding"}, event_sequence=0,
            final_result={"coding": {"revision": revision, "base_sha": base_sha, "test_evidence_ids": [str(evidence_ids[0])],
                "review_evidence_ids": [str(item) for item in evidence_ids[1:]], "diff_artifact_id": str(artifact_id)},
                "verification": {"status": "PASS", "fresh_revision": True, "secret_check": True}}))
        db.flush()
        db.add(CodingSession(id=coding_id, task_id=task_id, repo_id="fixture/repo", commit_sha=base_sha, baseline_revision=base_revision, revision=revision))
        db.flush()
        db.add_all([CodingSnapshot(id=uuid4(), task_id=task_id, session_id=coding_id, revision=base_revision, files_json=_encode(base)),
            CodingSnapshot(id=uuid4(), task_id=task_id, session_id=coding_id, revision=revision, files_json=_encode(current))])
        db.add_all([CodingEvidence(id=identifier, task_id=task_id, session_id=coding_id, revision=revision, operation_key=str(identifier),
            kind=kind, evidence_json={"qualified": True}) for identifier, kind in zip(evidence_ids, ("test", "review", "verification"))])
        db.add(CodingArtifact(id=artifact_id, task_id=task_id, operation_key="diff", kind="diff", filename="changes.diff", mime_type="text/plain",
            storage_key="fixture/changes.diff", sha256=hashlib.sha256(diff).hexdigest(), byte_size=len(diff), metadata_json={"revision": revision}))
    remote_repo = {"id": 42, "full_name": "fixture/repo", "default_branch": "main", "private": True, "permissions": {"pull": True, "push": True}}
    installation = {"id": 7, "app_id": 123, "account": {"id": 80, "login": "fixture"}, "permissions": {"contents": "write", "issues": "read", "pull_requests": "write"}, "repository_selection": "selected"}
    repository = ApprovalRepository(sessions, settings)
    preview = repository.prepare(task_id, owner, ApprovalPrepare(title="Fix fixture", body="A verified change."), (remote_repo, installation))
    stage = "exact approval policy"
    for who, action_hash in ((outsider, preview["action_hash"]), (owner, "f"*64)):
        try:
            repository.decision(preview["id"], who, ApprovalDecision(action_hash=action_hash, decision="approve"))
        except ApprovalError:
            pass
        else:
            raise AssertionError("approval owner/hash bypass")
    repository.decision(preview["id"], owner, ApprovalDecision(action_hash=preview["action_hash"], decision="approve"))
    try:
        with sessions.begin() as db:
            db.execute(text("UPDATE approvals SET action_json='{}'::jsonb"))
    except Exception:
        pass
    else:
        raise AssertionError("immutable action changed")
    state = {"branch": None, "pr": None, "branch_posts": 0, "pr_posts": 0, "base_sha": base_sha}
    action = preview["action"]
    leaves = [{"path": path, "mode": "100755" if path == "app.py" else "100644", "type": "blob", "sha": blob_sha(data)} for path, data in base.items()]
    final_leaves = [{**item, "sha": blob_sha(current[item["path"]])} for item in leaves]
    def handler(request):
        path, method = request.url.path, request.method
        payload = json.loads(request.content) if request.content else {}
        if path == "/user/installations":
            return httpx.Response(200, json={"installations": [installation]})
        if path == "/user/installations/7/repositories":
            return httpx.Response(200, json={"repositories": [remote_repo]})
        if method == "GET" and path.endswith("/git/ref/heads/main"):
            return httpx.Response(200, json={"object": {"sha": state["base_sha"]}})
        if method == "GET" and path.endswith("/git/commits/"+base_sha):
            return httpx.Response(200, json={"tree": {"sha": tree_sha}})
        if method == "GET" and "/git/trees/" in path:
            return httpx.Response(200, json={"tree": final_leaves if path.endswith(new_tree_sha) else leaves, "truncated": False})
        if method == "POST" and path.endswith("/git/blobs"):
            import base64
            return httpx.Response(201, json={"sha": blob_sha(base64.b64decode(payload["content"]))})
        if method == "POST" and path.endswith("/git/trees"):
            check(payload["base_tree"] == tree_sha and payload["tree"][0]["mode"] == "100755", "base tree or original mode lost")
            check([item["path"] for item in payload["tree"]] == ["app.py"], "unchanged path written")
            return httpx.Response(201, json={"sha": new_tree_sha})
        if method == "POST" and path.endswith("/git/commits"):
            check(payload["parents"] == [base_sha], "unapproved commit parent")
            return httpx.Response(201, json={"sha": commit_sha})
        if method == "GET" and "/git/ref/heads/agentforge/" in path:
            return httpx.Response(200, json=state["branch"]) if state["branch"] else httpx.Response(404)
        if method == "POST" and path.endswith("/git/refs"):
            state["branch_posts"] += 1
            check(payload["ref"] == "refs/heads/"+action["head_branch"], "unapproved branch")
            state["branch"] = {"ref": payload["ref"], "object": {"sha": commit_sha}}
            return httpx.Response(201, json=state["branch"])
        if method == "GET" and path.endswith("/pulls"):
            return httpx.Response(200, json=[state["pr"]] if state["pr"] else [])
        if method == "POST" and path.endswith("/pulls"):
            state["pr_posts"] += 1
            check(payload["draft"] is True, "non-draft PR attempted")
            state["pr"] = {"number": 8, "html_url": "https://github.com/fixture/repo/pull/8", "draft": True, "state": "open",
                "head": {"ref": action["head_branch"], "sha": commit_sha, "repo": {"id": 42}},
                "base": {"ref": "main", "sha": base_sha, "repo": {"id": 42}}}
            raise httpx.ReadTimeout("simulated provider accepted then response lost")
        raise AssertionError("unexpected mocked provider request")
    stage = "ambiguous PR then reconciliation"
    transport = httpx.MockTransport(handler)
    state["base_sha"] = "a"*40
    try:
        await GitHubWriteService(repository, sessions, settings, transport=transport).execute(preview["id"], owner)
    except ApprovalError as error:
        check(str(error) == "github_base_branch_moved", "moved base not rejected")
    else:
        raise AssertionError("changed approved base accepted")
    check(state["branch_posts"] == 0 and state["pr_posts"] == 0, "moved base caused write")
    state["base_sha"] = base_sha
    try:
        await GitHubWriteService(repository, sessions, settings, transport=transport).execute(preview["id"], owner)
    except GitHubError as error:
        check(str(error) == "github_unavailable", "unexpected ambiguity code")
    else:
        raise AssertionError("lost response reported confirmed")
    with sessions() as db:
        check(db.scalar(select(ExternalOperation).where(ExternalOperation.kind == "draft_pr")).status == "UNKNOWN", "ambiguous outcome not persisted")
    restarted = ApprovalRepository(sessions, settings)
    result = await GitHubWriteService(restarted, sessions, settings, transport=transport).execute(preview["id"], owner)
    check(result["state"] == "CONSUMED" and result["result"]["pr_url"].endswith("/pull/8"), "delivery not confirmed")
    check(state["pr_posts"] == 1 and state["branch_posts"] == 1, "duplicate external write")
    result = await GitHubWriteService(ApprovalRepository(sessions, settings), sessions, settings, transport=transport).execute(preview["id"], owner)
    check(result["state"] == "CONSUMED" and state["pr_posts"] == 1, "consumed approval replayed")
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
    print("PASS durable session cap/restart/revocation; PKCE state binding/replay; approval owner/hash/immutability; changed-only Git trees preserve modes; PR response-loss reconciles to one branch and one draft PR", flush=True)

try:
    source = make_url(Settings().database_url)
    check(source.host in ("localhost", "127.0.0.1", "::1"), "nonlocal configured DB")
    admin = create_engine(URL.create("postgresql+psycopg", username=getpass.getuser(), port=source.port, database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        owner = connection.dialect.identifier_preparer.quote_identifier(source.username)
        connection.execute(text('CREATE DATABASE "' + name + '" OWNER ' + owner))
    created = True
    settings = Settings(_env_file=None, database_url=source.set(database=name).render_as_string(hide_password=False),
        langgraph_checkpoint_db_url=source.set(drivername="postgresql", database=name).render_as_string(hide_password=False),
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
