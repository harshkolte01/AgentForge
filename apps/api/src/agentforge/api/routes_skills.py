"""Skills: the built-in ones (on or off) and a person's own (create, edit, delete)."""
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from agentforge.api.dependencies import sessions
from agentforge.auth.session import current_user
from agentforge.database.models import User
from agentforge.skills.service import SkillError, SkillService

router = APIRouter(prefix="/api/v1/skills", tags=["skills"])


class SkillCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Bounded here only to refuse absurd bodies; the real limits and their messages are in skills/format.py.
    name: str = Field(max_length=200)
    description: str = Field(max_length=5000)
    instructions: str = Field(max_length=100_000)
    enabled: bool = True


class SkillPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    instructions: str | None = Field(default=None, max_length=100_000)
    enabled: bool | None = None


def service(request: Request) -> SkillService:
    return SkillService(sessions(request), request.app.state.settings)


def failure(error: SkillError) -> HTTPException:
    return HTTPException(error.status, {"code": error.code, "message": error.message, **({"field": error.field} if error.field else {})})


@router.get("")
async def overview(svc: SkillService = Depends(service), user: User = Depends(current_user)):
    """Every skill with whether it is switched on: the built-in ones, and this person's own (without their instructions)."""
    return await run_in_threadpool(svc.overview, user.id)


@router.post("", status_code=201)
async def create(body: SkillCreate, svc: SkillService = Depends(service), user: User = Depends(current_user)):
    try:
        return await run_in_threadpool(svc.create, user.id, body)
    except SkillError as error:
        raise failure(error) from None


@router.get("/{key}")
async def detail(key: str, svc: SkillService = Depends(service), user: User = Depends(current_user)):
    """One skill with its full instructions. `key` is `builtin:<name>` or `custom:<id>`."""
    try:
        return await run_in_threadpool(svc.detail, user.id, key)
    except SkillError as error:
        raise failure(error) from None


@router.patch("/{key}")
async def change(key: str, body: SkillPatch, svc: SkillService = Depends(service), user: User = Depends(current_user)):
    try:
        return await run_in_threadpool(svc.update, user.id, key, body)
    except SkillError as error:
        raise failure(error) from None


@router.delete("/{key}", status_code=204)
async def remove(key: str, svc: SkillService = Depends(service), user: User = Depends(current_user)):
    try:
        await run_in_threadpool(svc.delete, user.id, key)
    except SkillError as error:
        raise failure(error) from None
    return Response(status_code=204)
