"""Owner-scoped GitHub App discovery and signed, durable revocation webhooks."""
import hashlib
import json
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Request, Query
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from agentforge.api.dependencies import sessions
from agentforge.auth.session import current_user
from agentforge.auth.github_service import github_configured, GitHubIdentityService
from agentforge.auth.durable import now
from agentforge.database.models import User
from agentforge.database.identity import AuthSession, GitHubCredential, GitHubInstallation, GitHubRepositoryGrant, GitHubWebhookDelivery
from agentforge.integrations.github_app import GitHubAppClient, AuthorizedGitHubClient, valid_github_id
from agentforge.integrations.github_client import GitHubError, validate_repo_id
from agentforge.security.webhook_verification import verify_github_signature

router = APIRouter(prefix="/api/v1/github", tags=["github"])

def fail(error):
    code = str(error)
    raise HTTPException(503 if code in {"github_unavailable", "github_read_unavailable", "github_rate_limited"} else 403, code)

def identity(request):
    if not github_configured(request.app.state.settings):
        raise HTTPException(503, "github_auth_not_configured")
    return GitHubIdentityService(sessions(request), request.app.state.settings)

@router.get("/status")
def status(request: Request, user: User = Depends(current_user)):
    settings = request.app.state.settings
    with sessions(request)() as database:
        credential = database.scalar(select(GitHubCredential).where(GitHubCredential.user_id == user.id)) if github_configured(settings) else None
        connected = bool(credential and not credential.revoked_at and not credential.refresh_started_at
            and credential.refresh_expires_at and credential.refresh_expires_at > now())
    return {"configured": github_configured(settings), "connected": connected,
        "enabled_writes": settings.enable_github_writes,
        "install_url": f"https://github.com/apps/{settings.github_app_slug}/installations/new" if settings.github_app_slug else None}

@router.get("/installations")
async def installations(request: Request, user: User = Depends(current_user)):
    try:
        service = identity(request)
        values = await GitHubAppClient(request.app.state.settings, token=await service.access_token(user.id)).installations()
        return {"installations": [{"id": item["id"], "account": item["account"]["login"],
            "repository_selection": item.get("repository_selection"), "permissions": item.get("permissions", {})} for item in values]}
    except GitHubError as error:
        fail(error)

@router.get("/repositories")
async def repositories(request: Request, user: User = Depends(current_user)):
    try:
        service = identity(request)
        pairs = await GitHubAppClient(request.app.state.settings, token=await service.access_token(user.id)).repositories()
        service.record_grants(user.id, pairs)
        return {"repositories": [{"id": repo["id"], "full_name": repo["full_name"], "private": bool(repo.get("private")),
            "default_branch": repo.get("default_branch"), "installation_id": install["id"],
            "can_write": bool(request.app.state.settings.enable_github_writes and repo.get("permissions", {}).get("push")
                and install.get("permissions", {}).get("contents") == "write"
                and install.get("permissions", {}).get("pull_requests") == "write" and not repo.get("archived"))}
            for install, repo in pairs]}
    except GitHubError as error:
        fail(error)

@router.get("/issues")
async def issues(request: Request, repo_id: str = Query(max_length=200), user: User = Depends(current_user)):
    try:
        client, repo, install = await AuthorizedGitHubClient(sessions(request), request.app.state.settings, user.id).authorize(repo_id)
        if install.get("permissions", {}).get("issues") not in {"read", "write"}:
            raise GitHubError("github_issues_permission_required")
        values = await client.request("GET", f"/repos/{validate_repo_id(repo['full_name'])}/issues?state=open&per_page=100")
        if not isinstance(values, list):
            raise GitHubError("github_response_invalid")
        return {"issues": [{"number": value["number"], "title": str(value["title"])[:1000], "state": value["state"],
            "url": f"https://github.com/{repo['full_name']}/issues/{value['number']}"} for value in values if "pull_request" not in value]}
    except GitHubError as error:
        fail(error)

@router.get("/issues/{number}")
async def issue(number: int, request: Request, repo_id: str = Query(max_length=200), user: User = Depends(current_user)):
    try:
        return await AuthorizedGitHubClient(sessions(request), request.app.state.settings, user.id).get_issue(repo_id, number)
    except GitHubError as error:
        fail(error)

@router.post("/webhook", status_code=202)
async def webhook(request: Request):
    settings = request.app.state.settings
    if not settings.github_webhook_secret:
        raise HTTPException(503, "github_webhook_not_configured")
    # Consume streamed raw bytes with a hard cap before parsing.
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 1_000_000:
            raise HTTPException(413, "github_webhook_size_limit")
    try:
        verify_github_signature(bytes(body), request.headers.get("x-hub-signature-256"), settings.github_webhook_secret.get_secret_value())
        delivery = str(UUID(request.headers.get("x-github-delivery", "")))
        payload = json.loads(body)
        if not isinstance(payload, dict):
            raise ValueError()
    except (ValueError, UnicodeError):
        raise HTTPException(403, "github_webhook_invalid") from None
    event = request.headers.get("x-github-event", "")
    if event not in {"installation", "installation_repositories", "github_app_authorization", "ping"}:
        raise HTTPException(422, "github_webhook_event_not_supported")
    installation = payload.get("installation")
    install_id = installation.get("id") if isinstance(installation, dict) else None
    if event.startswith("installation") and not valid_github_id(install_id):
        raise HTTPException(422, "github_webhook_identity_invalid")
    sender = payload.get("sender")
    if event != "ping" and (not isinstance(sender, dict) or not valid_github_id(sender.get("id"))):
        raise HTTPException(422, "github_webhook_identity_invalid")
    removed_repositories = payload.get("repositories_removed", [])
    if event == "installation_repositories" and (not isinstance(removed_repositories, list)
            or any(not isinstance(item, dict) or not valid_github_id(item.get("id")) for item in removed_repositories)):
        raise HTTPException(422, "github_webhook_identity_invalid")
    # Persist receipt and process only authorization-reducing actions in this
    # transaction. Granting authority always requires fresh authenticated API.
    with sessions(request)() as database, database.begin():
        accepted = database.execute(insert(GitHubWebhookDelivery).values(id=delivery, event=event,
            body_sha256=hashlib.sha256(body).hexdigest(), payload_json=payload).on_conflict_do_nothing().returning(GitHubWebhookDelivery.id)).scalar()
        if accepted is None:
            raise HTTPException(409, "github_webhook_delivery_replayed")
        if event == "installation" and payload.get("action") in {"deleted", "suspend", "new_permissions_accepted"}:
            database.execute(update(GitHubInstallation).where(GitHubInstallation.id == install_id).values(revoked_at=now()))
            database.execute(update(GitHubRepositoryGrant).where(GitHubRepositoryGrant.installation_id == install_id).values(revoked_at=now()))
        if event == "installation_repositories" and payload.get("action") == "removed":
            removed = [repo["id"] for repo in removed_repositories]
            database.execute(update(GitHubRepositoryGrant).where(GitHubRepositoryGrant.installation_id == install_id,
                GitHubRepositoryGrant.repository_id.in_(removed)).values(revoked_at=now()))
        if event == "github_app_authorization" and payload.get("action") == "revoked":
            identifier = sender["id"]
            if type(identifier) is not int:
                raise HTTPException(422, "github_webhook_identity_invalid")
            user_id = database.scalar(select(User.id).where(User.github_user_id == identifier))
            if user_id:
                database.execute(update(GitHubCredential).where(GitHubCredential.user_id == user_id).values(revoked_at=now()))
                database.execute(update(AuthSession).where(AuthSession.user_id == user_id).values(revoked_at=now()))
        database.execute(update(GitHubWebhookDelivery).where(GitHubWebhookDelivery.id == delivery).values(processed_at=now(), payload_json={}))
    return {"accepted": True}
