"""Owner-scoped research evidence and immutable report downloads."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response

from agentforge.api.dependencies import sessions, task_repository
from agentforge.auth.session import current_user
from agentforge.database.models import User
from agentforge.repositories.tasks import TaskNotFound, TaskRepository
from agentforge.repositories.research import ResearchRepository
from agentforge.repositories.coding import CodingRepository
from agentforge.subagents.scope import ScopeDenied
from agentforge.integrations.artifact_storage import ArtifactStorage
from agentforge.contracts.artifact_names import FORMATS, artifact_filename

router = APIRouter(prefix="/api/v1/tasks", tags=["artifacts"])


def owned_task(task_id: UUID, user: User, repository: TaskRepository):
    try:
        return repository.get(task_id=task_id, owner_id=user.id)
    except TaskNotFound:
        raise HTTPException(404, "task_not_found") from None


@router.get("/{task_id}/sources")
def sources(task_id: UUID, request: Request, user: User = Depends(current_user),
            repository: TaskRepository = Depends(task_repository)) -> list[dict]:
    task = owned_task(task_id, user, repository)
    try:
        return ResearchRepository(sessions(request)).list_sources(
            task_id=task_id, owner_id=user.id, project_id=task.project_id)
    except ScopeDenied:
        raise HTTPException(404, "task_not_found") from None


@router.get("/{task_id}/artifacts")
def artifacts(task_id: UUID, request: Request, user: User = Depends(current_user),
              repository: TaskRepository = Depends(task_repository)) -> list[dict]:
    task = owned_task(task_id, user, repository)
    try:
        selected = CodingRepository(sessions(request)) if task.kind == "coding" else ResearchRepository(sessions(request))
        return selected.list_artifacts(
            task_id=task_id, owner_id=user.id, project_id=task.project_id)
    except ScopeDenied:
        raise HTTPException(404, "task_not_found") from None


@router.get("/{task_id}/artifacts/{artifact_id}")
def download(task_id: UUID, artifact_id: UUID, request: Request,
             user: User = Depends(current_user),
             repository: TaskRepository = Depends(task_repository)) -> Response:
    task = owned_task(task_id, user, repository)
    try:
        selected = CodingRepository(sessions(request)) if task.kind == "coding" else ResearchRepository(sessions(request))
        artifact = selected.get_artifact(
            artifact_id=str(artifact_id), task_id=task_id, owner_id=user.id, project_id=task.project_id)
        data = ArtifactStorage(request.app.state.settings.research_storage_path).read(
            artifact["storage_key"], artifact["sha256"], max_bytes=2_000_000)
    except ScopeDenied:
        raise HTTPException(404, "artifact_not_found") from None
    except (OSError, ValueError):
        raise HTTPException(503, "artifact_unavailable") from None
    # Only validated inert basenames may enter the response header. Legacy or
    # malformed metadata falls back to the original server-generated name.
    coding_filenames = {"diff": "coding.patch", "test": "test-log.txt", "build": "build-log.txt",
                        "review": "code-review.md", "verification": "verification-report.md"}
    filename = coding_filenames.get(artifact["kind"], "coding-report.txt") if task.kind == "coding" else {"general_text": "general-output.txt", "general_markdown": "general-report.md", "general_csv": "general-table.csv"}.get(artifact["kind"], "research-report.md")
    format = artifact["kind"].removeprefix("general_")
    if task.kind != "coding" and artifact["kind"].startswith("general_") and format in FORMATS:
        filename = artifact_filename(format, artifact.get("filename"))
    return Response(data, media_type="text/plain; charset=utf-8", headers={
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
        "ETag": f'"{artifact["sha256"]}"',
    })


@router.get("/{task_id}/coding")
def coding_details(task_id: UUID, request: Request, user: User = Depends(current_user),
                   repository: TaskRepository = Depends(task_repository)) -> dict:
    task = owned_task(task_id, user, repository)
    if task.kind != "coding":
        raise HTTPException(404, "coding_task_not_found")
    try:
        coding = CodingRepository(sessions(request))
        scope = dict(task_id=task_id, owner_id=user.id, project_id=task.project_id)
        return {"session": coding.get_session(**scope), "evidence": coding.list_evidence(**scope)}
    except ScopeDenied:
        raise HTTPException(404, "task_not_found") from None
