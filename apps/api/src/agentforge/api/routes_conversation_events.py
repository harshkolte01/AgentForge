"""Owner-reauthorized durable conversation changes, independent of a task stream."""
import asyncio
import json
from uuid import UUID
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool
from sqlalchemy import select
from agentforge.api.dependencies import sessions
from agentforge.api.routes_events import cursor_value
from agentforge.auth.session import current_user
from agentforge.database.sessions import ConversationChange
from agentforge.repositories.sessions import SessionRepository
from agentforge.repositories.tasks import TaskNotFound

router = APIRouter(prefix="/api/v1/sessions", tags=["sessions"])


def changes(factory, session_id, owner_id, after):
    with factory() as db:
        row = SessionRepository.owned(db, session_id, owner_id, lock=False)
        if after > row.revision:
            raise HTTPException(409, "conversation_cursor_ahead")
        return [{"session_id": str(session_id), "sequence": e.sequence, "event_type": e.event_type, "payload": e.payload_json}
            for e in db.scalars(select(ConversationChange).where(ConversationChange.session_id == session_id,
                ConversationChange.sequence > after).order_by(ConversationChange.sequence).limit(100))]


@router.get("/{session_id}/events")
async def events(session_id: UUID, request: Request, after: int = Query(0, ge=0),
                 last_event_id: str | None = Header(None, alias="Last-Event-ID"), user=Depends(current_user)):
    cursor = cursor_value(last_event_id, after)
    factory = sessions(request)
    try:
        await run_in_threadpool(changes, factory, session_id, user.id, cursor)
    except TaskNotFound:
        raise HTTPException(404, "session_not_found") from None

    async def stream():
        nonlocal cursor
        yield "retry: 3000\n\n"
        quiet = 0
        try:
            while not await request.is_disconnected():
                identity = await run_in_threadpool(current_user, request)
                if identity.id != user.id:
                    return
                page = await run_in_threadpool(changes, factory, session_id, user.id, cursor)
                for item in page:
                    cursor = item["sequence"]
                    yield f"id: {cursor}\nevent: conversation.changed\ndata: {json.dumps(item)}\n\n"
                quiet = 0 if page else quiet + 1
                if quiet >= 20:
                    yield ": keepalive\n\n"
                    quiet = 0
                await asyncio.sleep(0 if page else 0.5)
        except (HTTPException, TaskNotFound):
            yield "event: session.closed\ndata: {}\n\n"
        except asyncio.CancelledError:
            return
    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "private, no-store, no-transform",
        "X-Accel-Buffering": "no", "X-Content-Type-Options": "nosniff"})
