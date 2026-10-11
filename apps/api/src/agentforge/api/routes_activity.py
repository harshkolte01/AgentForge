"""Owner-scoped activity summary and tool call details."""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from agentforge.api.dependencies import sessions, task_repository
from agentforge.auth.session import current_user
from agentforge.contracts.activity import ActivitySummary, ToolCallDetail
from agentforge.database.models import User
from agentforge.integrations.artifact_storage import ArtifactStorage
from agentforge.repositories.activity import ActivityRepository, ToolCallNotFound, ToolDetailUnavailable
from agentforge.repositories.tasks import TaskNotFound, TaskRepository

router = APIRouter(prefix="/api/v1/tasks", tags=["activity"])


def owned_task(task_id: UUID, user: User, repository: TaskRepository):
    try:
        return repository.get(task_id=task_id, owner_id=user.id)
    except TaskNotFound:
        raise HTTPException(404, "task_not_found") from None


@router.get("/{task_id}/activity-summary", response_model=ActivitySummary)
def activity_summary(task_id: UUID, request: Request, response: Response, user: User = Depends(current_user),
                     repository: TaskRepository = Depends(task_repository)) -> ActivitySummary:
    owned_task(task_id, user, repository)
    response.headers["Cache-Control"] = "private, no-store"
    try:
        return ActivityRepository(sessions(request)).summary(task_id, user.id)
    except TaskNotFound:
        raise HTTPException(404, "task_not_found") from None


@router.get("/{task_id}/tool-calls/{tool_call_id}", response_model=ToolCallDetail)
def tool_call_detail(task_id: UUID, tool_call_id: UUID, request: Request, response: Response,
                     user: User = Depends(current_user),
                     repository: TaskRepository = Depends(task_repository)) -> ToolCallDetail:
    task = owned_task(task_id, user, repository)
    response.headers["Cache-Control"] = "private, no-store"
    storage = ArtifactStorage(request.app.state.settings.research_storage_path)
    try:
        return ActivityRepository(sessions(request), storage).tool_call_detail(
            task_id=task_id, tool_call_id=tool_call_id, owner_id=user.id, project_id=task.project_id)
    except ToolCallNotFound:
        raise HTTPException(404, "tool_call_not_found") from None
    except ToolDetailUnavailable:
        raise HTTPException(404, "tool_detail_unavailable") from None
    except PermissionError:  # ScopeDenied: ownership no longer holds
        raise HTTPException(404, "task_not_found") from None
