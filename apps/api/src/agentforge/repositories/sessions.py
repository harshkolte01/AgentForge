"""Atomic turn admission and recoverable receipts; no model or network effects."""
import hashlib
import json
from datetime import datetime, timezone
from uuid import UUID
from sqlalchemy import select
from agentforge.contracts.sessions import MessageView, SessionCreate, SessionPatch, SessionView, SessionTurnCreate, TurnView
from agentforge.contracts.tasks import TaskCreate, TaskView, TERMINAL_STATUSES
from agentforge.database.models import Project, Task, User
from agentforge.database.sessions import Conversation, ConversationMessage, SessionTurn
from .tasks import TaskConflict, TaskNotFound, TaskRepository


def settle_conversation(session, conversation):
    """Idempotently reconstruct a terminal assistant receipt after worker crash."""
    if conversation.active_task_id is None:
        return
    task = session.get(Task, conversation.active_task_id)
    if task is None or task.status not in TERMINAL_STATUSES:
        return
    existing = session.scalar(select(ConversationMessage.id).where(
        ConversationMessage.task_id == task.id, ConversationMessage.role == "assistant"))
    if existing is None:
        conversation.last_sequence += 1
        session.add(ConversationMessage(session_id=conversation.id, sequence=conversation.last_sequence,
            role="assistant", content=(task.final_summary or f"Task ended with status {task.status}.")[:8000], task_id=task.id))
    conversation.active_task_id = None
    conversation.updated_at = datetime.now(timezone.utc)
    session.flush()


def complete_task_conversation(session, task_id):
    conversation = session.scalar(select(Conversation).where(Conversation.active_task_id == task_id).with_for_update())
    if conversation is not None:
        settle_conversation(session, conversation)


def bounded_context(session, conversation):
    """Extractive older excerpts with provenance plus recent messages, <= 4500 chars.

    No text is promoted to platform authority. Full originals remain paginated.
    """
    recent = list(session.scalars(select(ConversationMessage).where(
        ConversationMessage.session_id == conversation.id).order_by(ConversationMessage.sequence.desc()).limit(6)))
    recent.reverse()
    cutoff = recent[0].sequence if recent else 0
    prior = list(session.scalars(select(ConversationMessage).where(ConversationMessage.session_id == conversation.id,
        ConversationMessage.sequence < cutoff).order_by(ConversationMessage.sequence.desc()).limit(8)))
    prior.reverse()
    summary = {"method": "bounded_extractive_v1", "untrusted": True,
        "through_sequence": max((row.sequence for row in prior), default=0),
        "excerpts": [{"message_id": str(row.id), "sequence": row.sequence, "role": row.role,
            "text": row.content[:120]} for row in prior]}
    conversation.summary_json = summary
    context = {"untrusted_history": True, "summary": summary,
        "recent_messages": [{"message_id": str(row.id), "sequence": row.sequence,
            "role": row.role, "text": row.content[:300]} for row in recent]}
    # JSON overhead and multibyte text are included, fail closed if bounds drift.
    while len(json.dumps(context, ensure_ascii=False)) > 4500 and context["recent_messages"]:
        context["recent_messages"].pop(0)
    return context


class SessionRepository:
    def __init__(self, session_factory):
        self.session_factory = session_factory
        self.tasks = TaskRepository(session_factory)

    @staticmethod
    def owned(session, session_id, owner_id, *, lock=True):
        query = select(Conversation).join(Project).where(Conversation.id == session_id,
            Conversation.owner_id == owner_id, Project.owner_id == owner_id)
        row = session.scalar(query.with_for_update(of=Conversation) if lock else query)
        if row is None:
            raise TaskNotFound("session_not_found")
        return row

    def create(self, owner_id: UUID, request: SessionCreate):
        with self.session_factory() as session, session.begin():
            session.scalar(select(User).where(User.id == owner_id).with_for_update())
            project = session.scalar(select(Project).where(Project.id == request.project_id, Project.owner_id == owner_id).with_for_update())
            if project is None:
                raise TaskNotFound("project_not_found")
            if len(list(session.scalars(select(Conversation.id).where(Conversation.owner_id == owner_id).limit(501)))) >= 500:
                raise TaskConflict("session_limit_reached")
            row = Conversation(owner_id=owner_id, project_id=project.id, title=request.title,
                mode=request.mode, archived=False, last_sequence=0)
            session.add(row)
            session.flush()
            return SessionView.model_validate(row)

    def list(self, owner_id, *, project_id=None, archived=False, limit=50, offset=0, q=None):
        if not 1 <= limit <= 100 or not 0 <= offset <= 10000:
            raise ValueError("invalid_session_limit")
        with self.session_factory() as session:
            query = select(Conversation).join(Project).where(Conversation.owner_id == owner_id,
                Project.owner_id == owner_id, Conversation.archived == archived)
            if project_id:
                query = query.where(Conversation.project_id == project_id)
            if q:
                if len(q) > 120:
                    raise ValueError("invalid_session_search")
                query = query.where(Conversation.title.icontains(q, autoescape=True))
            return [SessionView.model_validate(row) for row in session.scalars(query.order_by(
                Conversation.updated_at.desc(), Conversation.id).limit(limit).offset(offset))]

    def get(self, session_id, owner_id):
        with self.session_factory() as session, session.begin():
            row = self.owned(session, session_id, owner_id)
            settle_conversation(session, row)
            return SessionView.model_validate(row)

    def patch(self, session_id, owner_id, request: SessionPatch):
        with self.session_factory() as session, session.begin():
            row = self.owned(session, session_id, owner_id)
            settle_conversation(session, row)
            if request.archived and row.active_task_id:
                raise TaskConflict("active_session_cannot_archive")
            for key, value in request.model_dump(exclude_none=True).items():
                setattr(row, key, value)
            row.updated_at = datetime.now(timezone.utc)
            session.flush()
            return SessionView.model_validate(row)

    def messages(self, session_id, owner_id, *, after_sequence=0, limit=100):
        if not 1 <= limit <= 100 or after_sequence < 0:
            raise ValueError("invalid_message_limit")
        with self.session_factory() as session, session.begin():
            row = self.owned(session, session_id, owner_id)
            settle_conversation(session, row)
            return [MessageView.model_validate(item) for item in session.scalars(select(ConversationMessage).where(
                ConversationMessage.session_id == row.id, ConversationMessage.sequence > after_sequence)
                .order_by(ConversationMessage.sequence).limit(limit))]

    @staticmethod
    def request_digest(request):
        return hashlib.sha256(json.dumps(request.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def replay(self, session_id, owner_id, request):
        with self.session_factory() as session:
            self.owned(session, session_id, owner_id, lock=False)
            prior = session.scalar(select(SessionTurn).where(SessionTurn.session_id == session_id,
                SessionTurn.idempotency_key == request.idempotency_key))
            if prior is None:
                return None
            if prior.request_sha256 != self.request_digest(request):
                raise TaskConflict("turn_idempotency_conflict")
            return TurnView(session_id=session_id,
                user_message=MessageView.model_validate(session.get(ConversationMessage, prior.user_message_id)),
                task=TaskView.model_validate(session.get(Task, prior.task_id)), replayed=True)

    def turn(self, session_id, owner_id, request: SessionTurnCreate, *, original_digest=None):
        digest = original_digest or self.request_digest(request)
        with self.session_factory() as session, session.begin():
            row = self.owned(session, session_id, owner_id)
            prior = session.scalar(select(SessionTurn).where(SessionTurn.session_id == row.id,
                SessionTurn.idempotency_key == request.idempotency_key))
            if prior:
                if prior.request_sha256 != digest:
                    raise TaskConflict("turn_idempotency_conflict")
                return TurnView(session_id=row.id, user_message=MessageView.model_validate(session.get(ConversationMessage, prior.user_message_id)),
                    task=TaskView.model_validate(session.get(Task, prior.task_id)), replayed=True)
            settle_conversation(session, row)
            if row.archived:
                raise TaskConflict("session_archived")
            if row.active_task_id:
                raise TaskConflict("session_turn_in_progress")
            if row.last_sequence >= 2000:
                raise TaskConflict("session_message_limit_reached")
            payload = request.model_dump(exclude={"idempotency_key", "project_id"})
            task_request = TaskCreate.model_validate({**payload, "project_id": row.project_id})
            task = self.tasks.create_in_session(session, owner_id, task_request, context=bounded_context(session, row))
            row.last_sequence += 1
            message = ConversationMessage(session_id=row.id, sequence=row.last_sequence, role="user", content=request.goal, task_id=task.id)
            session.add(message)
            session.flush()
            session.add(SessionTurn(session_id=row.id, idempotency_key=request.idempotency_key,
                request_sha256=digest, task_id=task.id, user_message_id=message.id))
            row.active_task_id = task.id
            row.updated_at = datetime.now(timezone.utc)
            session.flush()
            return TurnView(session_id=row.id, user_message=MessageView.model_validate(message), task=task)
