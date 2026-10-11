"""Authenticated, bounded attachment ingestion shared by picker, drop and paste."""
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool
from agentforge.auth.session import current_user
from agentforge.database.models import User
from agentforge.integrations.attachment_storage import AttachmentStorage, InvalidAttachment, MAX_UPLOAD_BYTES

router = APIRouter(prefix='/api/v1/attachments', tags=['attachments'])

@router.post('', status_code=201)
async def upload(request: Request, name: str = Query(min_length=1, max_length=120), user: User = Depends(current_user)):
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > MAX_UPLOAD_BYTES: raise HTTPException(413, 'Choose a file up to 8 MB.')
    try:
        return await run_in_threadpool(AttachmentStorage(request.app.state.settings.research_storage_path).save, user.id, name, bytes(data))
    except InvalidAttachment as error:
        raise HTTPException(422, str(error)) from None


@router.get('/{upload_id}')
def preview(upload_id: str, request: Request, sha256: str = Query(pattern=r'^[0-9a-f]{64}$'), user: User = Depends(current_user)):
    import base64
    from urllib.parse import quote
    from fastapi.responses import Response
    try:
        record = AttachmentStorage(request.app.state.settings.research_storage_path).read(user.id, upload_id, sha256)
    except InvalidAttachment:
        raise HTTPException(404, 'attachment_not_found') from None
    disposition = 'inline' if record['media_type'].startswith('image/') else 'attachment'
    return Response(base64.b64decode(record['data']), media_type=record['media_type'], headers={
        'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff',
        'Content-Disposition': disposition + "; filename*=UTF-8''" + quote(record['name'], safe=''),
        'Content-Security-Policy': "default-src 'none'; sandbox",
    })
