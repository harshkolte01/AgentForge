"""Approval preview, exact decision and bounded reconciliation entry point."""
from uuid import UUID
from urllib.parse import quote
from fastapi import APIRouter, Depends, HTTPException, Request
from agentforge.api.dependencies import sessions
from agentforge.auth.session import current_user
from agentforge.contracts.approvals import ApprovalPrepare, ApprovalDecision
from agentforge.database.models import User
from agentforge.integrations.github_app import AuthorizedGitHubClient
from agentforge.integrations.github_client import GitHubError
from agentforge.integrations.github_write import GitHubWriteService
from agentforge.repositories.approvals import ApprovalRepository, ApprovalError

router = APIRouter(prefix="/api/v1", tags=["approvals"])

def repo(request):
    return ApprovalRepository(sessions(request), request.app.state.settings)

def fail(error):
    code = str(error)
    status = 404 if code.endswith("not_found") else 503 if code in {"github_unavailable", "github_read_unavailable", "github_rate_limited"} else 409
    raise HTTPException(status, code)

@router.get("/tasks/{task_id}/approvals")
def approvals(task_id: UUID, request: Request, user: User = Depends(current_user)):
    try:
        return {"approvals": repo(request).list(task_id, user.id)}
    except ApprovalError as error:
        fail(error)

@router.post("/tasks/{task_id}/approvals", status_code=201)
async def prepare(task_id: UUID, body: ApprovalPrepare, request: Request, user: User = Depends(current_user)):
    repository = repo(request)
    try:
        # Check ownership and actual verification before making provider calls.
        name, base = repository.task_candidate(task_id, user.id)
        client, remote, installation = await AuthorizedGitHubClient(sessions(request), request.app.state.settings, user.id).authorize(name, write=True)
        branch = await client.request("GET", f"/repos/{name}/git/ref/heads/{quote(remote['default_branch'], safe='')}")
        if branch.get("object", {}).get("sha") != base:
            raise ApprovalError("github_base_branch_moved")
        return repository.prepare(task_id, user.id, body, (remote, installation))
    except (ApprovalError, GitHubError) as error:
        fail(error)

@router.post("/approvals/{approval_id}/decision")
def decide(approval_id: UUID, body: ApprovalDecision, request: Request, user: User = Depends(current_user)):
    try:
        return repo(request).decision(approval_id, user.id, body)
    except ApprovalError as error:
        fail(error)

@router.post("/approvals/{approval_id}/execute")
async def execute(approval_id: UUID, request: Request, user: User = Depends(current_user)):
    repository = repo(request)
    try:
        return await GitHubWriteService(repository, sessions(request), request.app.state.settings).execute(approval_id, user.id)
    except (ApprovalError, GitHubError) as error:
        fail(error)
