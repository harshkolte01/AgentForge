"""Project-private immutable agents; test uses the real bounded task queue."""
from datetime import datetime
from decimal import Decimal
import json
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from agentforge.api.dependencies import sessions
from agentforge.auth.session import current_user
from agentforge.contracts.agent_definition import AgentDefinition as DefinitionContract, Permissions
from agentforge.contracts.tasks import TaskCreate, TaskView
from agentforge.database.models import AgentDefinition, AgentDefinitionVersion, Project, User
from agentforge.repositories.definitions import DefinitionRepository
from agentforge.repositories.tasks import TaskRepository
from agentforge.subagents.registry import BUILTIN_KEYS, canonical_snapshot

router = APIRouter(prefix="/api/v1/agents", tags=["agents"])

class AgentSave(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: UUID
    definition: DefinitionContract

class AgentTest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)
    goal: str = Field(min_length=1, max_length=8000)
    max_cost_usd: Decimal = Field(default=Decimal("0.25"), gt=0, le=1, allow_inf_nan=False)
    max_wall_time_sec: int = Field(default=180, ge=30, le=300)
    allowed_web_hosts: tuple[str, ...] = ()


def owned(session, agent_id, owner_id, *, lock=False):
    query = select(AgentDefinition).join(Project).where(AgentDefinition.id == agent_id,
        AgentDefinition.namespace == "project", Project.owner_id == owner_id)
    row = session.scalar(query.with_for_update(of=AgentDefinition) if lock else query)
    if row is None:
        raise HTTPException(404, "agent_not_found")
    return row


def view(session, row):
    snapshot = session.scalar(select(AgentDefinitionVersion).where(
        AgentDefinitionVersion.definition_id == row.id, AgentDefinitionVersion.version == row.current_version))
    return {"id": row.id, "project_id": row.project_id, "key": f"project/{row.project_id}/{row.agent_key}",
        "name": row.agent_key, "current_version": row.current_version, "disabled_at": row.disabled_at,
        "definition": snapshot.definition_json, "current_sha256": snapshot.sha256, "created_at": row.created_at}


def validate_management(definition):
    if definition.name in BUILTIN_KEYS or not definition.enabled:
        raise HTTPException(422, "built_in_name_or_disabled_definition")
    # Phase 6 exposes reviewed source research tools. Future sandbox roles require
    # separately reviewed management grants and execution verification.
    if definition.output_schema_id != "research_result_v1" or not set(definition.allowed_tools).issubset({"web_search", "fetch_webpage", "read_task_input", "read_research_source"}):
        raise HTTPException(422, "custom_agents_support_source_research_tools")
    if not definition.permissions.web or any(getattr(definition.permissions, field) for field in
        ("sandbox_read", "sandbox_exec", "sandbox_write", "github_read", "github_write", "browser")):
        raise HTTPException(422, "custom_agent_capability_not_available")
    for field in Permissions.model_fields:
        if field.startswith("allowed_"):
            value = getattr(definition.permissions, field)
            if value is None or (field != "allowed_web_hosts" and value):
                raise HTTPException(422, "custom_agent_requires_explicit_restricted_resources")
    TaskCreate.validate_hosts(definition.permissions.allowed_web_hosts)
    budget = definition.budget
    if budget.max_steps > 12 or budget.max_tool_calls > 20 or budget.max_llm_cost_usd > 1 or budget.max_wall_time_sec > 300:
        raise HTTPException(422, "custom_agent_budget_exceeds_management_ceiling")


@router.get("")
def list_agents(request: Request, project_id: UUID, user: User = Depends(current_user)):
    with sessions(request)() as session:
        if session.scalar(select(Project.id).where(Project.id == project_id, Project.owner_id == user.id)) is None:
            raise HTTPException(404, "project_not_found")
        return [view(session, row) for row in session.scalars(select(AgentDefinition).where(
            AgentDefinition.project_id == project_id, AgentDefinition.namespace == "project")
            .order_by(AgentDefinition.agent_key).limit(200))]


@router.post("", status_code=201)
def save_agent(body: AgentSave, request: Request, user: User = Depends(current_user)):
    try:
        validate_management(body.definition)
        snapshot, digest = canonical_snapshot(body.definition)
        with sessions(request)() as session, session.begin():
            project = session.scalar(select(Project).where(Project.id == body.project_id, Project.owner_id == user.id).with_for_update())
            if project is None:
                raise HTTPException(404, "project_not_found")
            rows = list(session.scalars(select(AgentDefinition).where(AgentDefinition.project_id == project.id).limit(201)))
            if len(rows) >= 200 and not any(row.agent_key == body.definition.name for row in rows):
                raise HTTPException(409, "project_agent_limit_reached")
            version = DefinitionRepository(session).save_project(project_id=project.id, agent_key=body.definition.name,
                definition_json=snapshot, sha256=digest, created_by=user.id, version=body.definition.version)
            return view(session, session.get(AgentDefinition, version.definition_id))
    except ValueError as exc:
        raise HTTPException(409, str(exc) if str(exc) in {"first_version_must_be_one", "definition_version_conflict", "builtin_key_reserved", "agent_version_limit_reached"} else "agent_definition_rejected") from None


@router.get("/{agent_id}/versions")
def versions(agent_id: UUID, request: Request, user: User = Depends(current_user)):
    with sessions(request)() as session:
        row = owned(session, agent_id, user.id)
        return [{"id": item.id, "definition_id": row.id, "version": item.version,
            "definition": item.definition_json, "sha256": item.sha256, "created_at": item.created_at}
            for item in session.scalars(select(AgentDefinitionVersion).where(AgentDefinitionVersion.definition_id == row.id)
                .order_by(AgentDefinitionVersion.version.desc()).limit(100))]


@router.delete("/{agent_id}", status_code=204)
def disable(agent_id: UUID, request: Request, user: User = Depends(current_user)):
    with sessions(request)() as session, session.begin():
        row = owned(session, agent_id, user.id)
        DefinitionRepository(session).disable_project(project_id=row.project_id, definition_id=row.id, owner_id=user.id)
    return Response(status_code=204)


@router.post("/{agent_id}/test", response_model=TaskView, status_code=202)
def test_agent(agent_id: UUID, body: AgentTest, request: Request, user: User = Depends(current_user)):
    with sessions(request)() as session:
        row = owned(session, agent_id, user.id)
        project_id, key = row.project_id, f"project/{row.project_id}/{row.agent_key}"
    try:
        return TaskRepository(sessions(request)).create(user.id, TaskCreate(project_id=project_id,
            kind="research", goal=body.goal, max_cost_usd=body.max_cost_usd, max_wall_time_sec=body.max_wall_time_sec,
            allowed_web_hosts=body.allowed_web_hosts, agent_key=key, agent_version=body.version))
    except ValueError as exc:
        from agentforge.repositories.admission import AdmissionDenied
        raise HTTPException(429 if isinstance(exc, AdmissionDenied) else 409, str(exc)) from None
