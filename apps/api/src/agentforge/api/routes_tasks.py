"""Authenticated task submission and durable task state."""

from uuid import UUID

from starlette.concurrency import run_in_threadpool

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from agentforge.api.dependencies import task_repository
from agentforge.auth.session import current_user
from agentforge.contracts.tasks import TaskCreate, TaskStepView, TaskView
from agentforge.database.models import User
from agentforge.integrations.github_client import GitHubError
from agentforge.repositories.tasks import TaskConflict, TaskNotFound, TaskRepository

router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])


async def prepare_task(body: TaskCreate, request: Request, user: User, repository: TaskRepository) -> TaskCreate:
    await run_in_threadpool(repository.authorize_project, owner_id=user.id, project_id=body.project_id)
    if body.coding is not None:
        settings = request.app.state.settings
        if not getattr(settings, "e2b_api_key", None):
            raise HTTPException(503, "coding_sandbox_not_configured")
        from agentforge.auth.github_service import github_for_user
        from agentforge.api.dependencies import sessions
        github = await github_for_user(sessions(request), settings, user.id)
        resolved = await github.resolve(body.coding.repository.repo_id,
            body.coding.repository.ref, issue_number=body.coding.issue_number)
        selection = body.coding.repository.model_copy(update={"repo_id": resolved.repo_id, "ref": resolved.commit_sha})
        body = body.model_copy(update={"coding": body.coding.model_copy(update={"repository": selection})})
    return body


@router.post("", response_model=TaskView, status_code=202)
async def create_task(body: TaskCreate, request: Request, user: User = Depends(current_user),
                repository: TaskRepository = Depends(task_repository)) -> TaskView:
    try:
        from agentforge.integrations.attachment_storage import validate_uploads, InvalidAttachment
        from starlette.concurrency import run_in_threadpool
        try:
            await run_in_threadpool(validate_uploads, body.attachments, user.id, request.app.state.settings)
        except InvalidAttachment as failure:
            raise HTTPException(422, str(failure)) from None
        prepared = await prepare_task(body, request, user, repository)
        return await run_in_threadpool(repository.create, owner_id=user.id, request=prepared)
    except GitHubError as exc:
        code = str(exc)
        raise HTTPException(503 if code in {"github_rate_limited", "github_read_unavailable"} else 422, code) from None
    except TaskNotFound:
        raise HTTPException(404, "project_not_found") from None
    except TaskConflict as exc:
        raise HTTPException(409, str(exc)) from None
    except Exception as exc:
        from agentforge.repositories.admission import AdmissionDenied
        if isinstance(exc, AdmissionDenied):
            raise HTTPException(429, str(exc)) from None
        raise


@router.get("", response_model=list[TaskView])
def list_tasks(user: User = Depends(current_user),
               repository: TaskRepository = Depends(task_repository),
               project_id: UUID | None = None, limit: int = Query(50, ge=1, le=100),
               offset: int = Query(0, ge=0, le=10000)) -> list[TaskView]:
    return repository.list(owner_id=user.id, project_id=project_id, limit=limit, offset=offset)


@router.get("/{task_id}", response_model=TaskView)
def get_task(task_id: UUID, user: User = Depends(current_user),
             repository: TaskRepository = Depends(task_repository)) -> TaskView:
    try:
        return repository.get(task_id=task_id, owner_id=user.id)
    except TaskNotFound:
        raise HTTPException(404, "task_not_found") from None


@router.get("/{task_id}/steps", response_model=list[TaskStepView])
def get_steps(task_id: UUID, user: User = Depends(current_user),
              repository: TaskRepository = Depends(task_repository)) -> list[TaskStepView]:
    try:
        return repository.steps(task_id=task_id, owner_id=user.id)
    except TaskNotFound:
        raise HTTPException(404, "task_not_found") from None


@router.get("/{task_id}/runs")
def get_runs(task_id: UUID, user: User = Depends(current_user),
             repository: TaskRepository = Depends(task_repository)) -> list[dict]:
    try:
        return repository.runs(task_id=task_id, owner_id=user.id)
    except TaskNotFound:
        raise HTTPException(404, "task_not_found") from None


@router.post("/{task_id}/cancel", response_model=TaskView, status_code=202)
def cancel_task(task_id: UUID, user: User = Depends(current_user),
                repository: TaskRepository = Depends(task_repository)) -> TaskView:
    try:
        return repository.cancel(task_id=task_id, owner_id=user.id)
    except TaskNotFound:
        raise HTTPException(404, "task_not_found") from None
    except TaskConflict as error:
        raise HTTPException(409, str(error)) from None


@router.post("/{task_id}/resume")
def resume_task(task_id: UUID, user: User = Depends(current_user),
                repository: TaskRepository = Depends(task_repository)) -> None:
    # Crash recovery is lease/checkpoint driven. Replaying an uncertain paid effect
    # from a browser request would bypass the durable reconciliation policy.
    try:
        repository.get(task_id=task_id, owner_id=user.id)
    except TaskNotFound:
        raise HTTPException(404, "task_not_found") from None
    raise HTTPException(409, "manual_resume_not_available; worker_recovers_eligible_checkpoints")
