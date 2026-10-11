"""Bounded durable event replay and owner-reauthorized SSE connections."""

import asyncio
import json
import time
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from agentforge.api.dependencies import sessions, task_repository
from agentforge.auth.session import current_user
from agentforge.contracts.activity import LiveStreamSnapshot
from agentforge.database.models import User
from agentforge.repositories.events import EventRepository
from agentforge.repositories.tasks import TaskNotFound, TaskRepository

router = APIRouter(prefix="/api/v1/tasks", tags=["events"])


def cursor_value(header: str | None, after: int) -> int:
    if header is None or header == "":
        return after
    if not header.isascii() or not header.isdecimal() or len(header) > 18:
        raise HTTPException(422, "invalid_last_event_id")
    cursor = int(header)
    if cursor > 9_007_199_254_740_991:
        raise HTTPException(422, "invalid_last_event_id")
    return cursor


@router.get("/{task_id}/events/history")
def event_history(task_id: UUID, request: Request, user: User = Depends(current_user),
                  after: int = Query(0, ge=0, le=9_007_199_254_740_991),
                  limit: int = Query(100, ge=1, le=100)):
    try:
        return EventRepository(sessions(request)).list_after(
            task_id=task_id, owner_id=user.id, after_sequence=after, limit=limit)
    except TaskNotFound:
        raise HTTPException(404, "task_not_found") from None


@router.get("/{task_id}/stream-snapshot", response_model=LiveStreamSnapshot)
async def stream_snapshot(task_id: UUID, request: Request, response: Response,
                          user: User = Depends(current_user)) -> LiveStreamSnapshot:
    """The whole live text right now: for a client whose offsets no longer match, or that polls instead of streaming."""
    try:
        snapshot = await run_in_threadpool(EventRepository(sessions(request)).live_snapshot, task_id, user.id)
    except TaskNotFound:
        raise HTTPException(404, "task_not_found") from None
    response.headers["Cache-Control"] = "private, no-store"
    return snapshot


def delta_frame(task_id: UUID, epoch: int, revision: int, kind: str, offset: int, text: str, reset: bool) -> str:
    """One `message.delta`: a reset replaces the client's live text (offset 0), anything else appends at `offset`."""
    payload = json.dumps({"task_id": str(task_id), "epoch": epoch, "revision": revision, "kind": kind,
                          "offset": offset, "text": text, "reset": reset})
    return f"event: message.delta\ndata: {payload}\n\n"


@router.get("/{task_id}/events")
async def events(task_id: UUID, request: Request, user: User = Depends(current_user),
                 repository: TaskRepository = Depends(task_repository),
                 after: int = Query(0, ge=0, le=9_007_199_254_740_991),
                 last_event_id: str | None = Header(None, alias="Last-Event-ID")) -> StreamingResponse:
    cursor = cursor_value(last_event_id, after)
    try:
        task = await run_in_threadpool(repository.get, task_id=task_id, owner_id=user.id)
    except TaskNotFound:
        raise HTTPException(404, "task_not_found") from None
    if cursor > task.event_sequence:
        raise HTTPException(409, "event_cursor_ahead_of_task")
    event_repository = EventRepository(sessions(request))

    async def stream():
        nonlocal cursor
        yield "retry: 3000\n\n"
        # The live text this connection has delivered: (epoch, revision) and how much of it. A new connection has
        # delivered nothing, so its first frame is always a full-state reset.
        sent_state, sent_length = None, 0
        last_output = time.monotonic()
        try:
            while not await request.is_disconnected():
                # Check cookie expiry/revocation, current DB identity and task ownership
                # for every page, not just when the streaming response was opened.
                authenticated = await run_in_threadpool(current_user, request)
                if authenticated.id != user.id:
                    return
                page = await run_in_threadpool(event_repository.list_after,
                    task_id=task_id, owner_id=user.id, after_sequence=cursor, limit=100)
                for event in page:
                    if await request.is_disconnected():
                        return
                    cursor = event.sequence
                    # model_dump_json escapes line breaks; event types are persisted
                    # server constants, never model-authored content.
                    event_type = event.event_type.replace("\n", "").replace("\r", "")
                    yield f"id: {cursor}\nevent: {event_type}\ndata: {event.model_dump_json()}\n\n"
                    last_output = time.monotonic()
                # The live text, as increments. Not part of the durable event log (no id), so a reconnect starts again
                # from offset 0 with a reset; saved notes and the saved message stay authoritative. Whenever the text
                # was replaced rather than extended (a newer epoch or revision, including a clear) the whole state is sent
                # as a reset, so a reader is never left showing text that is gone.
                snapshot = await run_in_threadpool(event_repository.stream_snapshot, task_id, user.id)
                if snapshot is not None:
                    epoch, revision, kind, text = snapshot
                    if sent_state != (epoch, revision) or len(text) < sent_length:
                        sent_state, sent_length = (epoch, revision), len(text)
                        yield delta_frame(task_id, epoch, revision, kind, 0, text, True)
                        last_output = time.monotonic()
                    elif len(text) > sent_length:
                        yield delta_frame(task_id, epoch, revision, kind, sent_length, text[sent_length:], False)
                        sent_length = len(text)
                        last_output = time.monotonic()
                if page:
                    # Yield to cancellation/backpressure between each bounded page.
                    await asyncio.sleep(0)
                    continue
                if time.monotonic() - last_output >= 10:
                    yield ": keepalive\n\n"
                    last_output = time.monotonic()
                # Poll quickly only while text is being written (the row stays, emptied, once a note has been saved).
                await asyncio.sleep(0.15 if snapshot is not None and snapshot[3] else 0.5)
        except (HTTPException, TaskNotFound):
            # The response has started: close without disclosing revoked task data.
            yield "event: session.closed\ndata: {}\n\n"
        except asyncio.CancelledError:
            return

    return StreamingResponse(stream(), media_type="text/event-stream", headers={
        # no-transform keeps intermediaries (including the web app's proxy) from compressing and buffering the stream.
        "Cache-Control": "no-cache, no-store, no-transform", "X-Accel-Buffering": "no",
        "X-Content-Type-Options": "nosniff",
    })
