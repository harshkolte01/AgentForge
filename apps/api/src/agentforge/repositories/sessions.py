"""Atomic turn admission and recoverable receipts; no model or network effects."""
import hashlib
import json
from datetime import datetime, timezone
from uuid import UUID, uuid4
from sqlalchemy import select
from agentforge.contracts.sessions import AutoTurnCreate, MessageView, SessionCreate, SessionPatch, SessionView, SessionTurnCreate, TurnView
from agentforge.contracts.tasks import TaskCreate, TaskView, TERMINAL_STATUSES
from agentforge.database.models import Project, Task, User
from agentforge.database.sessions import Conversation, ConversationMessage, SessionTurn
from agentforge.database.coding import CodingSession
from .tasks import TaskConflict, TaskNotFound, TaskRepository


def canonical_workspace(session, owner_id):
    """Internal namespace only; old projects and their history keep their identity."""
    owner = session.scalar(select(User).where(User.id == owner_id).with_for_update())
    if owner is None:
        raise TaskNotFound("user_not_found")
    project = session.scalar(select(Project).where(Project.owner_id == owner_id).order_by(Project.created_at, Project.id).limit(1))
    if project is None:
        project = Project(id=uuid4(), owner_id=owner_id, name="My workspace")
        session.add(project)
        session.flush()
    return project


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
    def continuation(session, conversation, selection=None):
        selection = conversation.repository_json if selection is None else selection
        if not selection:
            return None
        candidates = session.execute(select(Task, CodingSession).join(SessionTurn, SessionTurn.task_id == Task.id)
            .join(ConversationMessage, ConversationMessage.id == SessionTurn.user_message_id)
            .join(CodingSession, CodingSession.task_id == Task.id).where(SessionTurn.session_id == conversation.id,
                Task.created_by == conversation.owner_id, Task.project_id == conversation.project_id,
                Task.status.in_(TERMINAL_STATUSES), ConversationMessage.sequence > conversation.repository_since_sequence,
                CodingSession.repo_id == selection["repo_id"]).order_by(ConversationMessage.sequence.desc()).limit(100))
        for task, workspace in candidates:
            raw = (task.permissions_json.get("automatic") or {}).get("repository")
            pinned = (task.permissions_json.get("coding") or {}).get("repository")
            if selection == raw or selection == pinned:
                return task.id
        return None

    @classmethod
    def view(cls, session, row):
        return SessionView.model_validate(row).model_copy(update={"continuation_task_id": cls.continuation(session, row)})

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
            project = (session.scalar(select(Project).where(Project.id == request.project_id, Project.owner_id == owner_id).with_for_update())
                if request.project_id is not None else canonical_workspace(session, owner_id))
            if project is None:
                raise TaskNotFound("project_not_found")
            if len(list(session.scalars(select(Conversation.id).where(Conversation.owner_id == owner_id).limit(501)))) >= 500:
                raise TaskConflict("session_limit_reached")
            row = Conversation(owner_id=owner_id, project_id=project.id, title=request.title,
                mode=request.mode, archived=False, last_sequence=0,
                repository_json=request.repository.model_dump(mode="json") if request.repository else None)
            session.add(row)
            session.flush()
            return self.view(session, row)

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
            return [self.view(session, row) for row in session.scalars(query.order_by(
                Conversation.updated_at.desc(), Conversation.id).limit(limit).offset(offset))]

    def get(self, session_id, owner_id):
        with self.session_factory() as session, session.begin():
            row = self.owned(session, session_id, owner_id)
            settle_conversation(session, row)
            return self.view(session, row)

    def patch(self, session_id, owner_id, request: SessionPatch):
        with self.session_factory() as session, session.begin():
            row = self.owned(session, session_id, owner_id)
            settle_conversation(session, row)
            if request.archived and row.active_task_id:
                raise TaskConflict("active_session_cannot_archive")
            if "repository" in request.model_fields_set:
                if row.active_task_id:
                    raise TaskConflict("active_session_repository_cannot_change")
                selection = request.repository.model_dump(mode="json") if request.repository else None
                if selection != row.repository_json:
                    row.repository_since_sequence = row.last_sequence
                row.repository_json = selection
            for key, value in request.model_dump(exclude_none=True, exclude={"repository"}).items():
                setattr(row, key, value)
            row.updated_at = datetime.now(timezone.utc)
            session.flush()
            return self.view(session, row)

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
        payload = request.model_dump(mode="json")
        if isinstance(request, AutoTurnCreate) and "repository" not in request.model_fields_set:
            payload["repository"] = {"inherit_session_selection": True}
        if isinstance(request, AutoTurnCreate) and "continue_from_task_id" not in request.model_fields_set:
            payload["continue_from_task_id"] = "inherit_compatible_workspace"
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def replay(self, session_id, owner_id, request):
        with self.session_factory() as session:
            self.owned(session, session_id, owner_id, lock=False)
            prior = session.scalar(select(SessionTurn).where(SessionTurn.session_id == session_id,
                SessionTurn.idempotency_key == request.idempotency_key))
            if prior is None:
                return None
            if prior.request_sha256 != self.request_digest(request):
                raise TaskConflict("turn_idempotency_conflict")
            return self.receipt(session, session_id, prior, replayed=True)

    @staticmethod
    def receipt(session, session_id, prior, *, replayed=False):
        message = session.get(ConversationMessage, prior.user_message_id)
        assistant = session.scalar(select(ConversationMessage).where(
            ConversationMessage.session_id == session_id, ConversationMessage.sequence == message.sequence + 1,
            ConversationMessage.role == "assistant"))
        task = session.get(Task, prior.task_id) if prior.task_id else None
        return TurnView(session_id=session_id, user_message=MessageView.model_validate(message),
            task=TaskView.model_validate(task) if task else None,
            assistant_message=MessageView.model_validate(assistant) if assistant else None, replayed=replayed)

    def conversational_turn(self, session_id, owner_id, request: AutoTurnCreate, response):
        """Atomic free greeting/clarification, with the same fencing and retry rules."""
        digest = self.request_digest(request)
        with self.session_factory() as session, session.begin():
            row = self.owned(session, session_id, owner_id)
            prior = session.scalar(select(SessionTurn).where(SessionTurn.session_id == row.id,
                SessionTurn.idempotency_key == request.idempotency_key))
            if prior:
                if prior.request_sha256 != digest:
                    raise TaskConflict("turn_idempotency_conflict")
                return self.receipt(session, row.id, prior, replayed=True)
            settle_conversation(session, row)
            self.check_available(row)
            message = ConversationMessage(session_id=row.id, sequence=row.last_sequence + 1,
                role="user", content=request.goal, task_id=None)
            assistant = ConversationMessage(session_id=row.id, sequence=row.last_sequence + 2,
                role="assistant", content=response[:8000], task_id=None)
            session.add_all([message, assistant])
            session.flush()
            session.add(SessionTurn(session_id=row.id, idempotency_key=request.idempotency_key,
                request_sha256=digest, task_id=None, user_message_id=message.id))
            self.update_title(row, request.goal)
            row.last_sequence += 2
            row.updated_at = datetime.now(timezone.utc)
            session.flush()
            return TurnView(session_id=row.id, user_message=MessageView.model_validate(message),
                assistant_message=MessageView.model_validate(assistant), task=None)

    @staticmethod
    def check_available(row):
        if row.archived:
            raise TaskConflict("session_archived")
        if row.active_task_id:
            raise TaskConflict("session_turn_in_progress")
        if row.last_sequence >= 1999:
            raise TaskConflict("session_message_limit_reached")

    @staticmethod
    def update_title(row, goal):
        if row.title in {"New session", "New conversation"}:
            row.title = " ".join(goal.split())[:120]

    def turn(self, session_id, owner_id, request: SessionTurnCreate, *, original_digest=None, automatic=None):
        digest = original_digest or self.request_digest(request)
        with self.session_factory() as session, session.begin():
            row = self.owned(session, session_id, owner_id)
            prior = session.scalar(select(SessionTurn).where(SessionTurn.session_id == row.id,
                SessionTurn.idempotency_key == request.idempotency_key))
            if prior:
                if prior.request_sha256 != digest:
                    raise TaskConflict("turn_idempotency_conflict")
                return self.receipt(session, row.id, prior, replayed=True)
            settle_conversation(session, row)
            self.check_available(row)
            if automatic is not None:
                automatic = dict(automatic)
                if automatic.pop("inherit_repository", False):
                    automatic["repository"] = row.repository_json
                selection = automatic.get("repository")
                candidate = self.continuation(session, row, selection) if selection else None
                supplied = automatic.get("continue_from_task_id")
                if supplied and str(candidate) != supplied:
                    raise TaskConflict("continuation_does_not_match_conversation_selection")
                if automatic.pop("inherit_continuation", False):
                    automatic["continue_from_task_id"] = str(candidate) if candidate else None
            payload = request.model_dump(exclude={"idempotency_key", "project_id"})
            task_request = TaskCreate.model_validate({**payload, "project_id": row.project_id})
            task = self.tasks.create_in_session(session, owner_id, task_request, context=bounded_context(session, row), automatic=automatic)
            row.last_sequence += 1
            message = ConversationMessage(session_id=row.id, sequence=row.last_sequence, role="user", content=request.goal, task_id=task.id)
            session.add(message)
            session.flush()
            session.add(SessionTurn(session_id=row.id, idempotency_key=request.idempotency_key,
                request_sha256=digest, task_id=task.id, user_message_id=message.id))
            row.active_task_id = task.id
            self.update_title(row, request.goal)
            row.updated_at = datetime.now(timezone.utc)
            session.flush()
            return TurnView(session_id=row.id, user_message=MessageView.model_validate(message), task=task)
