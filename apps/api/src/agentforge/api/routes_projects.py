"""Owner-filtered projects used by the task composer."""

from uuid import uuid4

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select

from agentforge.api.dependencies import sessions
from agentforge.auth.session import current_user
from agentforge.database.models import Project, User

router = APIRouter(prefix="/api/v1/projects", tags=["projects"])


class ProjectCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("project_name_required")
        return value.strip()


def project_view(project: Project) -> dict:
    return {"id": str(project.id), "name": project.name, "created_at": project.created_at}


@router.get("")
def list_projects(request: Request, user: User = Depends(current_user),
                  limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0, le=10000)) -> list[dict]:
    with sessions(request)() as database:
        projects = database.scalars(select(Project).where(Project.owner_id == user.id)
                                    .order_by(Project.created_at.desc(), Project.id)
                                    .limit(limit).offset(offset)).all()
        return [project_view(project) for project in projects]


@router.post("", status_code=201)
def create_project(body: ProjectCreate, request: Request, user: User = Depends(current_user)) -> dict:
    with sessions(request)() as database, database.begin():
        project = Project(id=uuid4(), owner_id=user.id, name=body.name)
        database.add(project)
        database.flush()
        return project_view(project)
