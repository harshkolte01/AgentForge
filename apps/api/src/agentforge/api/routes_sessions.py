"""Durable owner-scoped sessions wrapping the existing task worker and SSE."""
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool
from agentforge.api.dependencies import sessions
from agentforge.api.routes_tasks import prepare_task
from agentforge.auth.session import current_user
from agentforge.contracts.sessions import AutoTurnCreate, MessageView, QueueCommand, ReadReceipt, SessionCreate, SessionPatch, SessionTurnCreate, SessionView, TurnView
from agentforge.contracts.tasks import TaskCreate
from agentforge.database.models import User
from agentforge.repositories.sessions import SessionRepository, context_budget_chars
from agentforge.repositories.tasks import TaskNotFound, TaskConflict

router = APIRouter(prefix="/api/v1/sessions", tags=["sessions"])

def repository(request: Request):
    return SessionRepository(sessions(request), context_budget=context_budget_chars(request.app.state.settings))

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

@router.post("/{session_id}/read", response_model=SessionView)
def mark_read(session_id: UUID, body: ReadReceipt, user: User = Depends(current_user), repo=Depends(repository)):
    """The owner has seen the conversation up to `sequence`. Clears its "unread"/"failed" sidebar status; changes nothing else."""
    try:
        return repo.mark_read(session_id, user.id, body.sequence)
    except TaskNotFound as exc:
        raise error(exc) from None

@router.get("/{session_id}/messages", response_model=list[MessageView])
def messages(session_id: UUID, user: User = Depends(current_user), repo=Depends(repository),
             after_sequence: int = Query(0, ge=0, le=9_007_199_254_740_991), limit: int = Query(100, ge=1, le=100)):
    try:
        return repo.messages(session_id, user.id, after_sequence=after_sequence, limit=limit)
    except TaskNotFound as exc:
        raise error(exc) from None

@router.post("/{session_id}/turns", response_model=TurnView, status_code=202)
async def turn(session_id: UUID, body: AutoTurnCreate | SessionTurnCreate, request: Request,
               user: User = Depends(current_user), repo=Depends(repository)):
    try:
        # Reconnect/retry uses the original request hash before any ref/network resolution.
        replay = await run_in_threadpool(repo.replay, session_id, user.id, body)
        if replay is not None:
            return replay
        owned = await run_in_threadpool(repo.get, session_id, user.id)
        from agentforge.integrations.attachment_storage import validate_uploads, InvalidAttachment
        try:
            await run_in_threadpool(validate_uploads, body.attachments, user.id, request.app.state.settings)
        except InvalidAttachment as failure:
            raise HTTPException(422, str(failure)) from None
        if isinstance(body, AutoTurnCreate):
            return await run_in_threadpool(repo.enqueue, session_id, user.id, body)
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


@router.get("/{session_id}/turns", response_model=list[TurnView])
def turns(session_id: UUID, user: User = Depends(current_user), repo=Depends(repository),
          after_ordinal: int = Query(0, ge=0, le=9_007_199_254_740_991), limit: int = Query(100, ge=1, le=100), waiting: bool = False):
    try:
        return repo.turns(session_id, user.id, after_ordinal=after_ordinal, limit=limit, waiting=waiting)
    except TaskNotFound as exc:
        raise error(exc) from None


@router.post("/{session_id}/queue/{action}", response_model=SessionView)
def queue_action(session_id: UUID, action: str, body: QueueCommand, user: User = Depends(current_user), repo=Depends(repository)):
    if action not in {"stop", "resume"}:
        raise HTTPException(404, "queue_action_not_found")
    if action == "stop" and body.task_id is None:
        raise HTTPException(422, "stop_target_required")
    try:
        return repo.queue_command(session_id, user.id, action, task_id=body.task_id)
    except (TaskNotFound, TaskConflict) as exc:
        raise error(exc) from None


@router.post("/{session_id}/turns/{turn_id}/cancel", response_model=SessionView)
def cancel_waiting(session_id: UUID, turn_id: UUID, user: User = Depends(current_user), repo=Depends(repository)):
    try:
        return repo.queue_command(session_id, user.id, "cancel", turn_id=turn_id)
    except (TaskNotFound, TaskConflict) as exc:
        raise error(exc) from None
