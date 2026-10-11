"""Owner-scoped delivery of a conversation's repository work to GitHub (docs/delivery.md)."""
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from agentforge.api.dependencies import sessions
from agentforge.auth.session import current_user
from agentforge.database.models import User
from agentforge.delivery.service import DeliveryError, DeliveryService, from_github
from agentforge.integrations.github_client import GitHubError

router = APIRouter(prefix="/api/v1/sessions/{session_id}/delivery", tags=["delivery"])


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class KeyBody(_Body):
    idempotency_key: UUID


class PushBody(KeyBody):
    message: str = Field(min_length=1, max_length=5000)
    branch: str | None = Field(default=None, max_length=100)


class PullRequestBody(KeyBody):
    title: str = Field(min_length=1, max_length=256)
    body: str = Field(default="", max_length=20000)
    draft: bool = True
    message: str | None = Field(default=None, max_length=5000)
    branch: str | None = Field(default=None, max_length=100)


class MergeBody(KeyBody):
    method: Literal["squash", "merge", "rebase"]
    expected_head_sha: str = Field(pattern=r"^[0-9a-f]{40}$")


class ResetBody(KeyBody):
    discard_unpublished: bool = False


class DiscardBody(_Body):
    path: str = Field(min_length=1, max_length=1024)


def service(request: Request) -> DeliveryService:
    state = request.app.state
    if getattr(state, "delivery_service", None) is None:
        settings = state.settings
        provider = None
        if settings.e2b_api_key:
            from agentforge.integrations.e2b_sandbox import SandboxConfig, SandboxProvider
            provider = SandboxProvider(SandboxConfig(api_key=settings.e2b_api_key.get_secret_value(), template=settings.e2b_template_id,
                ttl_sec=settings.e2b_sandbox_ttl_sec, request_timeout_sec=settings.e2b_request_timeout_sec))
        state.delivery_service = DeliveryService(sessions(request), settings, provider=provider)
    return state.delivery_service


async def respond(call):
    """Runs a service call, turning its failures into the machine-readable errors of docs/delivery.md."""
    try:
        return await call
    except DeliveryError as error:
        raise HTTPException(error.status, error.code) from None
    except GitHubError as error:
        converted = from_github(error)
        raise HTTPException(converted.status, converted.code) from None


@router.get("")
async def get_delivery(session_id: UUID, svc: DeliveryService = Depends(service), user: User = Depends(current_user)):
    return await respond(svc.view(session_id, user.id))


@router.get("/changes")
async def get_changes(session_id: UUID, svc: DeliveryService = Depends(service), user: User = Depends(current_user)):
    return await respond(svc.changes(session_id, user.id))


@router.post("/suggest")
async def suggest(session_id: UUID, svc: DeliveryService = Depends(service), user: User = Depends(current_user)):
    return await respond(svc.suggest(session_id, user.id))


@router.post("/push")
async def push(session_id: UUID, body: PushBody, svc: DeliveryService = Depends(service), user: User = Depends(current_user)):
    return await respond(svc.push(session_id, user.id, key=str(body.idempotency_key), message=body.message, branch=body.branch))


@router.post("/pull-request")
async def pull_request(session_id: UUID, body: PullRequestBody, svc: DeliveryService = Depends(service), user: User = Depends(current_user)):
    return await respond(svc.open_pull_request(session_id, user.id, key=str(body.idempotency_key), title=body.title, body=body.body,
                                               draft=body.draft, message=body.message, branch=body.branch))


@router.post("/ready")
async def ready(session_id: UUID, body: KeyBody, svc: DeliveryService = Depends(service), user: User = Depends(current_user)):
    return await respond(svc.mark_ready(session_id, user.id, key=str(body.idempotency_key)))


@router.post("/merge")
async def merge(session_id: UUID, body: MergeBody, svc: DeliveryService = Depends(service), user: User = Depends(current_user)):
    return await respond(svc.merge(session_id, user.id, key=str(body.idempotency_key), method=body.method,
                                   expected_head_sha=body.expected_head_sha))


@router.post("/close")
async def close(session_id: UUID, body: KeyBody, svc: DeliveryService = Depends(service), user: User = Depends(current_user)):
    return await respond(svc.close(session_id, user.id, key=str(body.idempotency_key)))


@router.post("/update-branch")
async def update_branch(session_id: UUID, body: KeyBody, svc: DeliveryService = Depends(service), user: User = Depends(current_user)):
    return await respond(svc.update_branch(session_id, user.id, key=str(body.idempotency_key)))


@router.post("/discard-file")
async def discard_file(session_id: UUID, body: DiscardBody, svc: DeliveryService = Depends(service), user: User = Depends(current_user)):
    return await respond(svc.discard_file(session_id, user.id, body.path))


@router.post("/refresh")
async def refresh(session_id: UUID, svc: DeliveryService = Depends(service), user: User = Depends(current_user)):
    return await respond(svc.view(session_id, user.id, refresh=True))


@router.post("/reset")
async def reset(session_id: UUID, body: ResetBody, svc: DeliveryService = Depends(service), user: User = Depends(current_user)):
    return await respond(svc.reset(session_id, user.id, key=str(body.idempotency_key), discard_unpublished=body.discard_unpublished))
