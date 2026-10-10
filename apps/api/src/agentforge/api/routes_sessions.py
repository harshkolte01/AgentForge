"""Durable owner-scoped sessions wrapping the existing task worker and SSE."""
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool
from agentforge.api.dependencies import sessions
from agentforge.api.routes_tasks import prepare_task
from agentforge.auth.session import current_user
from agentforge.contracts.sessions import MessageView, SessionCreate, SessionPatch, SessionTurnCreate, SessionView, TurnView
from agentforge.contracts.tasks import TaskCreate
from agentforge.database.models import User
from agentforge.repositories.sessions import SessionRepository
from agentforge.repositories.tasks import TaskNotFound, TaskConflict

router = APIRouter(prefix="/api/v1/sessions", tags=["sessions"])

def repository(request: Request):
    return SessionRepository(sessions(request))

def error(exc):
    if isinstance(exc, TaskNotFound):
        return HTTPException(404, str(exc))
    return HTTPException(409, str(exc))

@router.post("", response_model=SessionView, status_code=201)
def create(body: SessionCreate, user: User = Depends(current_user), repo=Depends(repository)):
    try:
        return repo.create(user.id, body)
    except (TaskNotFound, TaskConflict) as exc:
        raise error(exc) from None

@router.get("", response_model=list[SessionView])
def list_sessions(user: User = Depends(current_user), repo=Depends(repository), project_id: UUID | None = None,
                  archived: bool = False, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0, le=10000),
                  q: str | None = Query(None, max_length=120)):
    return repo.list(user.id, project_id=project_id, archived=archived, limit=limit, offset=offset, q=q)

@router.get("/{session_id}", response_model=SessionView)
def get(session_id: UUID, user: User = Depends(current_user), repo=Depends(repository)):
    try:
        return repo.get(session_id, user.id)
    except TaskNotFound as exc:
        raise error(exc) from None

@router.patch("/{session_id}", response_model=SessionView)
def patch(session_id: UUID, body: SessionPatch, user: User = Depends(current_user), repo=Depends(repository)):
    try:
        return repo.patch(session_id, user.id, body)
    except (TaskNotFound, TaskConflict) as exc:
        raise error(exc) from None

@router.get("/{session_id}/messages", response_model=list[MessageView])
def messages(session_id: UUID, user: User = Depends(current_user), repo=Depends(repository),
             after_sequence: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=100)):
    try:
        return repo.messages(session_id, user.id, after_sequence=after_sequence, limit=limit)
    except TaskNotFound as exc:
        raise error(exc) from None

@router.post("/{session_id}/turns", response_model=TurnView, status_code=202)
async def turn(session_id: UUID, body: SessionTurnCreate, request: Request,
               user: User = Depends(current_user), repo=Depends(repository)):
    try:
        # Reconnect/retry uses the original request hash before any ref/network resolution.
        replay = await run_in_threadpool(repo.replay, session_id, user.id, body)
        if replay is not None:
            return replay
        owned = await run_in_threadpool(repo.get, session_id, user.id)
        task_body = TaskCreate.model_validate({**body.model_dump(exclude={"project_id", "idempotency_key"}), "project_id": owned.project_id})
        prepared = await prepare_task(task_body, request, user, repo.tasks)
        resolved = body.model_copy(update={"coding": prepared.coding})
        return await run_in_threadpool(repo.turn, session_id, user.id, resolved, original_digest=repo.request_digest(body))
    except (TaskNotFound, TaskConflict) as exc:
        raise error(exc) from None
    except Exception as exc:
        from agentforge.integrations.github_client import GitHubError
        if isinstance(exc, GitHubError):
            code = str(exc)
            raise HTTPException(503 if code in {"github_rate_limited", "github_read_unavailable"} else 422, code) from None
        from agentforge.repositories.admission import AdmissionDenied
        if isinstance(exc, AdmissionDenied):
            raise HTTPException(429, str(exc)) from None
        raise
