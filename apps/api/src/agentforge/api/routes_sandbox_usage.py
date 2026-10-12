"""Signed E2B lifecycle evidence. Event IDs, not retry delivery IDs, deduplicate receipts."""
import asyncio
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy.dialects.postgresql import insert
from agentforge.database.sandbox_usage import SandboxUsageEvent
from .dependencies import sessions

router = APIRouter(prefix='/api/v1/sandbox-usage', tags=['plugins'])
EVENTS = {'created', 'resumed', 'paused', 'killed', 'updated', 'checkpointed'}


class IgnoredEvent(ValueError):
    """A well-formed, correctly signed event of a kind this app does not record (the provider may add new ones)."""


def verified_event(raw, signature, secret):
    # E2B v1 signature is base64(SHA256(secret + raw_payload)), without padding (vendor protocol).
    expected = base64.b64encode(hashlib.sha256(secret.encode() + raw).digest()).decode().rstrip('=')
    if not hmac.compare_digest(expected, signature):
        raise ValueError('signature')
    return parse_event(json.loads(raw))


def parse_event(event, *, source='signed_e2b_lifecycle'):
    if not isinstance(event, dict):
        raise ValueError('event shape')
    event = dict(event)
    # The authenticated history API uses camelCase; webhook deliveries use snake_case.
    for wire, canonical in {'sandboxId': 'sandbox_id', 'sandboxExecutionId': 'sandbox_execution_id',
                            'sandboxTemplateId': 'sandbox_template_id', 'eventData': 'event_data'}.items():
        if wire in event:
            if canonical in event and event[wire] != event[canonical]:
                raise ValueError('conflicting event fields')
            event[canonical] = event[wire]
    for key in ('id', 'sandbox_id', 'type', 'timestamp'):
        if not isinstance(event.get(key), str) or not 0 < len(event[key]) <= 256:
            raise ValueError('event shape')
    if event['type'] not in {'sandbox.lifecycle.' + item for item in EVENTS}:
        raise IgnoredEvent('event type')
    occurred = datetime.fromisoformat(event['timestamp'].replace('Z', '+00:00'))
    if occurred.tzinfo is None or occurred > datetime.now(timezone.utc) + timedelta(minutes=5):
        raise ValueError('timestamp')
    data = event.get('event_data', {})
    if not isinstance(data, dict):
        raise ValueError('event data')
    execution = data.get('execution')
    if execution is not None and not isinstance(execution, dict):
        raise ValueError('execution')
    return {'event_id': event['id'], 'sandbox_id': event['sandbox_id'], 'event_type': event['type'], 'occurred_at': occurred,
            'payload_json': {'execution_id': event.get('sandbox_execution_id'), 'template_id': event.get('sandbox_template_id'),
                'task_id': (data.get('sandbox_metadata') or {}).get('agentforge_task') if isinstance(data.get('sandbox_metadata'), dict) else None,
                'execution': execution, 'source': source}}


@router.post('/webhook', status_code=202)
async def receive(request: Request):
    secret = request.app.state.settings.e2b_webhook_secret
    if secret is None:
        raise HTTPException(503, 'sandbox_usage_not_configured')
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 65536:
            raise HTTPException(413, 'event_too_large')
    try:
        if request.headers.get('e2b-signature-version') != 'v1':
            raise ValueError('signature version')
        values = verified_event(bytes(body), request.headers.get('e2b-signature', ''), secret.get_secret_value())
    except IgnoredEvent:
        # Acknowledged, not stored: refusing it would make E2B retry it and could get the endpoint disabled.
        return {'accepted': False, 'ignored': True}
    except (ValueError, TypeError, KeyError):
        raise HTTPException(403, 'invalid_sandbox_event') from None
    def save():
        with sessions(request)() as db, db.begin():
            db.execute(insert(SandboxUsageEvent).values(**values).on_conflict_do_nothing())
            row = db.get(SandboxUsageEvent, values['event_id'])
            # The same event can arrive from the webhook and from the history API, which omits some fields: only what identifies
            # the event must agree.
            if row.sandbox_id != values['sandbox_id'] or row.event_type != values['event_type'] or row.occurred_at != values['occurred_at']:
                raise HTTPException(409, 'event_identity_conflict')
    await asyncio.to_thread(save)
    return {'accepted': True}
