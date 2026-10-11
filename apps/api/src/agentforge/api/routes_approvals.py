"""Approval of the agent's tool calls, and the person's choice between asking and approving everything."""
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict
from starlette.concurrency import run_in_threadpool

from agentforge.api.dependencies import sessions
from agentforge.auth.session import current_user
from agentforge.database.models import User
from agentforge.tools.approvals import ApprovalNotFound, decide, get_mode, set_mode

router = APIRouter(prefix="/api/v1", tags=["approvals"])


class ApprovalModeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["ask", "auto"]


class ToolDecisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["approve", "deny"]


@router.get("/settings/approval-mode")
async def read_mode(request: Request, user: User = Depends(current_user)):
    """"ask": the agent asks before anything that changes something. "auto": it never asks."""
    return {"mode": await run_in_threadpool(get_mode, sessions(request), user.id)}


@router.put("/settings/approval-mode")
async def write_mode(body: ApprovalModeBody, request: Request, user: User = Depends(current_user)):
    return {"mode": await run_in_threadpool(set_mode, sessions(request), user.id, body.mode)}


@router.post("/tool-approvals/{approval_id}")
async def answer(approval_id: UUID, body: ToolDecisionBody, request: Request, user: User = Depends(current_user)):
    """Approve or decline one waiting tool call. Answering twice returns the answer already given."""
    try:
        return await run_in_threadpool(decide, sessions(request), user.id, approval_id, body.decision)
    except ApprovalNotFound:
        raise HTTPException(404, "approval_not_found") from None
