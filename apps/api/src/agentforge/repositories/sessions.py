"""Atomic turn admission and recoverable receipts; no model or network effects."""
import hashlib
import json
from bisect import bisect_right
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4
from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import distinct_on
from agentforge.contracts.activity import AttachmentView
from agentforge.contracts.sessions import AutoTurnCreate, MessageView, SessionCreate, StepView, SessionPatch, SessionView, SessionTurnCreate, TurnView
from agentforge.contracts.tasks import MAX_ANSWER_CHARS, TaskCreate, TaskView, TERMINAL_STATUSES
from agentforge.database.models import BudgetOperation, BudgetReservation, Project, Task, ToolCall, User
from agentforge.database.sessions import Conversation, ConversationMessage, SessionTurn, TurnExecution, TurnInput, SessionCreationReceipt, ToolApproval
from agentforge.database.coding import CodingSession
from agentforge.tools.visibility import SELF_CORRECTION_CODES
from .activity import activity_for_tasks, attachments_for_tasks
from .tasks import TaskConflict, TaskNotFound, TaskRepository


PLACEHOLDER_TITLES = frozenset({"New session", "New conversation"})
# Budget operations made to name a conversation start with this, so they can be told from the reply's own.
TITLE_OPERATION_PREFIX = "title:"


def provisional_title(goal):
    """The title a conversation shows from its first message until a better one is generated (see
    workers/conversation_title.py): the message on one line, cut to the title limit."""
    return " ".join(goal.split())[:120]


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
        message = ConversationMessage(id=uuid4(), session_id=conversation.id, sequence=conversation.last_sequence,
            role="assistant", content=(task.final_summary or f"Task ended with status {task.status}.")[:MAX_ANSWER_CHARS], task_id=task.id)
        session.add(message)
        existing = message.id
    execution = session.scalar(select(TurnExecution).where(TurnExecution.task_id == task.id))
    if execution is not None:
        execution.assistant_message_id, execution.state = existing, task.status.lower()
        execution.error_code = task.error_code
    if task.error_code == "UNKNOWN_REMOTE_OUTCOME":
        conversation.queue_paused = True
    conversation.active_task_id = None
    bump_conversation(session, conversation, "turn.finished")
    conversation.dispatch_after = datetime.now(timezone.utc)
    conversation.updated_at = datetime.now(timezone.utc)
    session.flush()


def complete_task_conversation(session, task_id):
    conversation = session.scalar(select(Conversation).where(Conversation.active_task_id == task_id).with_for_update())
    if conversation is not None:
        settle_conversation(session, conversation)


STEP_LIMIT = 40




def tool_steps(session, task_ids):
    """Finished tool calls per task in the order they ran, for rendering a reply's work step by step."""
    steps = {task_id: [] for task_id in task_ids}
    if not steps:
        return steps
    # Numbered per task in the database, so only the first STEP_LIMIT of each task's calls are read.
    ranked = select(ToolCall.task_id, ToolCall.tool_name, ToolCall.status, func.row_number().over(
        partition_by=ToolCall.task_id, order_by=(ToolCall.started_at, ToolCall.id)).label("position")).where(
        ToolCall.task_id.in_(list(steps)), ToolCall.status.in_(("SUCCEEDED", "FAILED", "BLOCKED", "UNKNOWN")),
        ToolCall.tool_name != "unknown",
        # The model correcting its own malformed or repeated call is not a step the person needs to see.
        or_(ToolCall.error_code.is_(None), ToolCall.error_code.notin_(SELF_CORRECTION_CODES))).subquery()
    rows = session.execute(select(ranked.c.task_id, ranked.c.tool_name, ranked.c.status)
        .where(ranked.c.position <= STEP_LIMIT).order_by(ranked.c.task_id, ranked.c.position))
    for task_id, tool, status in rows:
        if len(steps[task_id]) < STEP_LIMIT:
            steps[task_id].append(StepView(tool=tool, status="done" if status == "SUCCEEDED" else "failed"))
    return steps


CONTEXT_BUDGET = 7500        # serialized UTF-8 bytes; compatibility default for standalone callers
MAX_CONTEXT_BUDGET = 48000  # finite paging/cost ceiling, including multilingual text and JSON escaping
RECENT_MESSAGE_CAP = 8000   # a complete stored message in the normal case
EXCERPT_CAP = 160            # older turns are reduced to a short, attributed excerpt
EXCERPT_LIMIT = 12
RECENT_COUNT = 12


def clip(text, cap):
    if len(text) <= cap:
        return text
    # Keep both the original request and final constraints/conclusion instead of only the opening.
    marker = "\n[… shortened …]\n"
    room = max(0, cap - len(marker))
    head = (room + 1) // 2
    return text[:head] + marker + (text[-(room - head):] if room > head else "")


def context_budget_chars(settings):
    """Share at most a tenth of usable input with history, with a finite paging/cost ceiling."""
    from agentforge.llm.limits import input_limit
    available = input_limit(settings.llm_model, settings.llm_context_window_tokens, settings.llm_max_output_tokens)
    return min(MAX_CONTEXT_BUDGET, max(2000, (available or 25_000) * 3 // 10))


def digest_history(session, conversation, budget=CONTEXT_BUDGET):
    """The newest-first extractive history (recent turns in full, then short excerpts) within CONTEXT_BUDGET.

    Pure: nothing is written, so the same digest can be measured for the context status.
    """
    if not 2000 <= budget <= MAX_CONTEXT_BUDGET:
        raise ValueError("invalid_history_budget")
    rows = list(session.scalars(select(ConversationMessage).where(
        ConversationMessage.session_id == conversation.id).order_by(ConversationMessage.sequence.desc()).limit(40)))

    def item(row, cap):
        text = clip(row.content, cap)
        return {"message_id": str(row.id), "sequence": row.sequence, "role": row.role,
                "text": text, "truncated": text != row.content}

    def size(value):
        return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))

    recent, excerpts = [], []
    used = 200  # envelope and key overhead
    # Reserve space for earlier decisions instead of letting full recent replies consume it all.
    recent_limit = budget - min(budget // 5, EXCERPT_LIMIT * 320)
    for row in rows:  # newest to oldest
        full = len(recent) < RECENT_COUNT and not excerpts
        entry = item(row, RECENT_MESSAGE_CAP if full else EXCERPT_CAP)
        cost = size(entry) + 1
        if used + cost > (recent_limit if full else budget):
            if full and not recent:
                # The newest message always survives, trimmed to what the budget allows.
                # JSON escaping can multiply the serialized size. Find a fitting preview,
                # rather than assuming a character in the original is one in the envelope.
                low, high = 32, min(RECENT_MESSAGE_CAP, recent_limit - used - 300)
                while low < high:
                    middle = (low + high + 1) // 2
                    if used + size(item(row, middle)) + 1 <= recent_limit:
                        low = middle
                    else:
                        high = middle - 1
                entry = item(row, low)
                cost = size(entry) + 1
            elif full:
                # Full-text budget is spent: older turns continue as short excerpts.
                full = False
                entry = item(row, EXCERPT_CAP)
                cost = size(entry) + 1
                if used + cost > budget:
                    break
            else:
                break
        if full:
            recent.append(entry)
        elif len(excerpts) < EXCERPT_LIMIT:
            excerpts.append(entry)
        else:
            break
        used += cost
    recent.reverse()
    excerpts.reverse()
    summary = {"method": "bounded_extractive_v3", "untrusted": True,
        "through_sequence": max((row["sequence"] for row in excerpts), default=0), "excerpts": excerpts}
    return {"untrusted_history": True, "summary": summary, "recent_messages": recent}


def bounded_context(session, conversation, budget=CONTEXT_BUDGET):
    """Newest-first extractive history: full recent turns, then short excerpts, <= CONTEXT_BUDGET chars.

    The conversation is replayed to the model as faithfully as the budget allows so follow-ups
    resolve against what was actually said. No text is promoted to platform authority, every item
    carries its message id and sequence, and full originals remain paginated.
    """
    digest = digest_history(session, conversation, budget)
    conversation.summary_json = digest["summary"]
    return digest


class SessionRepository:
    def __init__(self, session_factory, *, context_budget=CONTEXT_BUDGET):
        self.session_factory = session_factory
        self.context_budget = context_budget
        self.tasks = TaskRepository(session_factory)

    @staticmethod
    def continuation(session, conversation, selection=None):
        selection = conversation.repository_json if selection is None else selection
        if not selection:
            return None
        candidates = session.execute(select(Task, CodingSession).join(TurnExecution, TurnExecution.task_id == Task.id)
            .join(SessionTurn, SessionTurn.id == TurnExecution.turn_id)
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

    @staticmethod
    def continuations(session, rows):
        """`continuation` for a page of conversations in one query (only those with a repository selected can have one).

        Legacy: only conversations from before the agent rewrite have coding workspaces, so this is nearly always empty.
        """
        wanted = {row.id: row.repository_json for row in rows if row.repository_json}
        if not wanted:
            return {}
        candidates = session.execute(select(SessionTurn.session_id, Task.id, Task.permissions_json)
            .join(TurnExecution, TurnExecution.task_id == Task.id)
            .join(SessionTurn, SessionTurn.id == TurnExecution.turn_id)
            .join(ConversationMessage, ConversationMessage.id == SessionTurn.user_message_id)
            .join(CodingSession, CodingSession.task_id == Task.id)
            .join(Conversation, Conversation.id == SessionTurn.session_id)
            .where(SessionTurn.session_id.in_(list(wanted)), Task.created_by == Conversation.owner_id,
                Task.project_id == Conversation.project_id, Task.status.in_(TERMINAL_STATUSES),
                ConversationMessage.sequence > Conversation.repository_since_sequence,
                CodingSession.repo_id == Conversation.repository_json["repo_id"].astext)
            .order_by(SessionTurn.session_id, ConversationMessage.sequence.desc()))
        found, seen = {}, {}
        for ident, task_id, grants in candidates:
            # The same newest-first window of 100 candidates `continuation` looks at.
            seen[ident] = seen.get(ident, 0) + 1
            if ident in found or seen[ident] > 100:
                continue
            selection = wanted[ident]
            if selection in ((grants.get("automatic") or {}).get("repository"), (grants.get("coding") or {}).get("repository")):
                found[ident] = task_id
        return found

    @staticmethod
    def queue_counts(session, ids):
        """Per conversation, in one query: (messages waiting to start, messages that could not start)."""
        ids = list(ids)
        if not ids:
            return {}
        rows = session.execute(select(TurnExecution.session_id, func.count().filter(TurnExecution.state == "queued"),
            func.count().filter(TurnExecution.state == "blocked")).where(TurnExecution.session_id.in_(ids),
            TurnExecution.state.in_(("queued", "blocked"))).group_by(TurnExecution.session_id))
        return {ident: (waiting, blocked) for ident, waiting, blocked in rows}

    @staticmethod
    def activities(session, rows, counts):
        """What each conversation's sidebar entry shows (SessionView.status), for any number of conversations at once.

        Live states come first: a task running or waiting on a tool approval, or a next message about to start. Otherwise
        the latest finished turn decides: a failure, or a reply that has not been seen (beyond `read_sequence`), shows
        until the owner opens the conversation; a Stop is the owner's own act and shows nothing.
        """
        rows = list(rows)
        waiting_on_approval = set()
        active = [row.active_task_id for row in rows if row.active_task_id]
        if active:
            waiting_on_approval = set(session.scalars(select(ToolApproval.task_id).where(
                ToolApproval.task_id.in_(active), ToolApproval.state == "PENDING")))
        result, finished = {}, []
        for row in rows:
            queued, blocked = counts.get(row.id, (0, 0))
            if row.active_task_id:
                result[row.id] = "waiting_approval" if row.active_task_id in waiting_on_approval else "running"
            elif queued and not row.queue_paused:
                result[row.id] = "running"
            elif blocked:
                result[row.id] = "failed"
            else:
                result[row.id] = "idle"
                finished.append(row)
        if finished:
            # The newest finished turn of each conversation, with the sequence of its reply (a turn that never ran has none).
            latest = session.execute(select(TurnExecution.session_id, TurnExecution.state, TurnExecution.ordinal, ConversationMessage.sequence)
                .outerjoin(ConversationMessage, ConversationMessage.id == TurnExecution.assistant_message_id)
                .where(TurnExecution.session_id.in_([row.id for row in finished]),
                    TurnExecution.state.in_(("completed", "partial", "failed", "cancelled")))
                .ext(distinct_on(TurnExecution.session_id)).order_by(TurnExecution.session_id, TurnExecution.ordinal.desc()))
            read = {row.id: row.read_sequence for row in finished}
            for ident, state, ordinal, reply in latest:
                if (reply or ordinal) > read[ident]:
                    result[ident] = "failed" if state == "failed" else "idle" if state == "cancelled" else "unread"
        return result

    @classmethod
    def view(cls, session, row, *, counts=None, activity=None, continuations=None):
        """`counts`, `activity` and `continuations` let a caller that already measured many conversations at once avoid
        asking again per row."""
        counts = cls.queue_counts(session, [row.id]) if counts is None else counts
        waiting, blocked = counts.get(row.id, (0, 0))
        activity = cls.activities(session, [row], counts)[row.id] if activity is None else activity
        continuation = cls.continuation(session, row) if continuations is None else continuations.get(row.id)
        return SessionView.model_validate(row).model_copy(update={"continuation_task_id": continuation,
            "queued_count": waiting + blocked, "status": activity})

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
            digest = self.request_digest(request)
            if request.idempotency_key:
                prior = session.get(SessionCreationReceipt, (owner_id, request.idempotency_key))
                if prior:
                    if prior.request_sha256 != digest:
                        raise TaskConflict("session_idempotency_conflict")
                    return self.view(session, self.owned(session, prior.session_id, owner_id, lock=False))
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
            if request.idempotency_key:
                session.add(SessionCreationReceipt(owner_id=owner_id, idempotency_key=request.idempotency_key,
                    session_id=row.id, request_sha256=digest))
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
            rows = list(session.scalars(query.order_by(Conversation.updated_at.desc(), Conversation.id).limit(limit).offset(offset)))
            counts = self.queue_counts(session, [row.id for row in rows])
            states = self.activities(session, rows, counts)
            continuations = self.continuations(session, rows)
            return [self.view(session, row, counts=counts, activity=states[row.id], continuations=continuations) for row in rows]

    def mark_read(self, session_id, owner_id, sequence):
        """Records that the owner has now seen the conversation up to `sequence` (at most its last message, never backwards)."""
        with self.session_factory() as session, session.begin():
            row = self.owned(session, session_id, owner_id, lock=False)
            # Setting updated_at to itself keeps this from reordering the sidebar, which sorts by it.
            session.execute(update(Conversation).where(Conversation.id == row.id).values(
                read_sequence=func.greatest(Conversation.read_sequence, func.least(sequence, Conversation.last_sequence)),
                updated_at=Conversation.updated_at))
            session.refresh(row)
            return self.view(session, row)

    @staticmethod
    def unsettled(session, row):
        """The conversation still names an active task that has already ended (its worker died before settling it)."""
        return row.active_task_id is not None and session.scalar(
            select(Task.status).where(Task.id == row.active_task_id)) in TERMINAL_STATUSES

    def settled_read(self, session_id, owner_id, read):
        """`read(session, row)` for the frontend's polls: a plain read, without a row lock or a write transaction.
        Only a conversation left unsettled by a dead worker is locked and settled first (see settle_conversation)."""
        with self.session_factory() as session:
            row = self.owned(session, session_id, owner_id, lock=False)
            if not self.unsettled(session, row):
                return read(session, row)
        with self.session_factory() as session, session.begin():
            row = self.owned(session, session_id, owner_id)
            settle_conversation(session, row)
            return read(session, row)

    def get(self, session_id, owner_id):
        return self.settled_read(session_id, owner_id, self.view)

    def patch(self, session_id, owner_id, request: SessionPatch):
        with self.session_factory() as session, session.begin():
            row = self.owned(session, session_id, owner_id)
            settle_conversation(session, row)
            queued = self.view(session, row).queued_count
            if request.archived and (row.active_task_id or queued):
                raise TaskConflict("active_session_cannot_archive")
            if "repository" in request.model_fields_set:
                if row.active_task_id or queued:
                    raise TaskConflict("active_session_repository_cannot_change")
                selection = request.repository.model_dump(mode="json") if request.repository else None
                if selection != row.repository_json:
                    row.repository_since_sequence = row.last_sequence
                row.repository_json = selection
            for key, value in request.model_dump(exclude_none=True, exclude={"repository"}).items():
                setattr(row, key, value)
            row.updated_at = datetime.now(timezone.utc)
            bump_conversation(session, row)
            session.flush()
            return self.view(session, row)

    def context_usage(self, session_id, owner_id):
        """Measured inputs for the context status: the digest the next turn would carry, and the
        provider-reported size of the latest run's largest request."""
        with self.session_factory() as session:
            row = self.owned(session, session_id, owner_id, lock=False)
            from .conversation_context import history_snapshot
            digest = history_snapshot(session, row, budget=self.context_budget)
            from agentforge.llm.models import estimate_tokens
            serialized = json.dumps(digest, ensure_ascii=False, separators=(",", ":"))
            total = session.scalar(select(func.count()).select_from(ConversationMessage).where(
                ConversationMessage.session_id == row.id)) or 0
            entries = [*digest["recent_messages"], *digest["summary"].get("excerpts", [])]
            return {"history_chars": len(serialized), "history_tokens": estimate_tokens(serialized),
                "messages_total": total, "messages_in_full": sum(not item["truncated"] for item in entries),
                "messages_condensed": sum(item["truncated"] for item in entries),
                "last_run": self.last_run_context(session, row.id), "coverage": digest.get("coverage", {})}

    @staticmethod
    def last_run_context(session, session_id):
        """Peak input tokens over the model calls of the newest turn that made any."""
        recent = list(session.scalars(select(TurnExecution.task_id).join(SessionTurn, SessionTurn.id == TurnExecution.turn_id)
            .join(ConversationMessage, ConversationMessage.id == SessionTurn.user_message_id)
            .where(SessionTurn.session_id == session_id, TurnExecution.task_id.is_not(None))
            .order_by(ConversationMessage.sequence.desc()).limit(3)))
        tokens = BudgetOperation.usage_json["provider_usage"]["input_tokens"].as_integer()
        compacted = BudgetOperation.usage_json["provider_usage"]["compacted_results"].as_integer()
        for task_id in recent:
            peak, calls, stubbed = session.execute(select(func.max(tokens), func.count(), func.max(compacted))
                .join(BudgetReservation, BudgetReservation.id == BudgetOperation.reservation_id)
                .where(BudgetReservation.task_id == task_id, BudgetOperation.kind == "model", tokens > 0,
                    # Naming the conversation is not part of the reply whose context is being measured.
                    BudgetOperation.operation_key.not_like(f"{TITLE_OPERATION_PREFIX}%"))).one()
            if calls:
                return {"peak_input_tokens": int(peak), "model_calls": calls, "compacted_results": int(stubbed or 0)}
        return None

    def messages(self, session_id, owner_id, *, after_sequence=0, limit=100):
        if not 1 <= limit <= 100 or after_sequence < 0:
            raise ValueError("invalid_message_limit")
        return self.settled_read(session_id, owner_id, lambda session, row: self._messages(session, row, after_sequence, limit))

    @staticmethod
    def _messages(session, row, after_sequence, limit):
        items = list(session.scalars(select(ConversationMessage).where(
            ConversationMessage.session_id == row.id, ConversationMessage.sequence > after_sequence)
            .order_by(ConversationMessage.sequence).limit(limit)))
        states = list(session.execute(select(TurnExecution, SessionTurn).join(SessionTurn, SessionTurn.id == TurnExecution.turn_id)
            .where(TurnExecution.session_id == row.id, or_(SessionTurn.user_message_id.in_([i.id for i in items]),
                TurnExecution.assistant_message_id.in_([i.id for i in items])))))
        by_message = {ident: execution for execution, admission in states
            for ident in (admission.user_message_id, execution.assistant_message_id) if ident}
        # Task association is a read projection; immutable admitted messages need no rewrite.
        for item in items:
            if item.id in by_message:
                session.expunge(item)
                item.task_id = by_message[item.id].task_id
        assistant_tasks = {item.task_id for item in items if item.role == "assistant" and item.task_id}
        user_tasks = {item.task_id for item in items if item.role == "user" and item.task_id}
        steps = tool_steps(session, assistant_tasks)
        # Grouped over the whole page, so the cost does not grow with the number of messages.
        activity = activity_for_tasks(session, assistant_tasks)
        attachments = attachments_for_tasks(session, user_tasks)
        inputs = ({r.turn_id: r for r in session.scalars(select(TurnInput).where(TurnInput.turn_id.in_([e.turn_id for e in by_message.values()])))}
            if by_message else {})
        result = [MessageView.model_validate(item).model_copy(update=(
            {"steps": steps.get(item.task_id, []), "activity": activity.get(item.task_id)} if item.role == "assistant"
            else {"attachments": attachments.get(item.task_id, [])})) for item in items]
        for item in result:
            state = by_message.get(item.id)
            if state:
                item.turn_id, item.turn_state = state.turn_id, state.state
                source = inputs.get(state.turn_id)
                if source and item.role == "user":
                    item.attachments = [AttachmentView(name=a["name"], characters=len(a["content"])) for a in source.request_json.get("attachments", [])]
        return result

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

    @classmethod
    def receipt(cls, session, session_id, prior, *, replayed=False):
        return cls.receipts(session, session_id, [prior], replayed=replayed)[0]

    @staticmethod
    def receipts(session, session_id, priors, *, replayed=False):
        """The TurnView of each admitted turn, for a whole page in a fixed number of queries (not several per turn)."""
        priors = list(priors)
        if not priors:
            return []
        turn_ids = [prior.id for prior in priors]
        executions = {row.turn_id: row for row in session.scalars(select(TurnExecution).where(TurnExecution.turn_id.in_(turn_ids)))}
        message_ids = [prior.user_message_id for prior in priors] + [
            row.assistant_message_id for row in executions.values() if row.assistant_message_id]
        messages = {row.id: row for row in session.scalars(select(ConversationMessage).where(ConversationMessage.id.in_(message_ids)))}
        task_ids = {executions[prior.id].task_id if prior.id in executions else prior.task_id for prior in priors} - {None}
        tasks = {row.id: row for row in session.scalars(select(Task).where(Task.id.in_(task_ids)))} if task_ids else {}
        inputs = {row.turn_id: row for row in session.scalars(select(TurnInput).where(TurnInput.turn_id.in_(turn_ids)))}
        # Attachment names come from the turn's input; a turn admitted before inputs were kept has them on its task.
        legacy = {messages[prior.user_message_id].task_id for prior in priors if prior.id not in inputs} - {None}
        shown = attachments_for_tasks(session, legacy) if legacy else {}
        # Each waiting turn's place in the queue: waiting turns at or before its ordinal.
        waiting = (sorted(session.scalars(select(TurnExecution.ordinal).where(TurnExecution.session_id == session_id,
            TurnExecution.state.in_(("queued", "blocked")))))
            if any(row.state in {"queued", "blocked"} for row in executions.values()) else [])
        result = []
        for prior in priors:
            message, execution, source = messages[prior.user_message_id], executions.get(prior.id), inputs.get(prior.id)
            assistant = messages.get(execution.assistant_message_id) if execution and execution.assistant_message_id else None
            task_id = execution.task_id if execution else prior.task_id
            task = tasks.get(task_id) if task_id else None
            attachments = ([AttachmentView(name=a["name"], characters=len(a["content"])) for a in source.request_json.get("attachments", [])]
                if source else shown.get(message.task_id, []))
            position = bisect_right(waiting, execution.ordinal) if execution and execution.state in {"queued", "blocked"} else None
            result.append(TurnView(session_id=session_id, turn_id=prior.id, state=execution.state if execution else "completed",
                queue_position=position, error_code=execution.error_code if execution else None,
                user_message=MessageView.model_validate(message).model_copy(update={"attachments": attachments,
                    "task_id": task_id, "turn_id": prior.id, "turn_state": execution.state if execution else None}),
                task=TaskView.model_validate(task) if task else None,
                assistant_message=MessageView.model_validate(assistant) if assistant else None, replayed=replayed))
        return result

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
        if row.title in PLACEHOLDER_TITLES:
            row.title = provisional_title(goal)

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
            if session.scalar(select(TurnExecution.turn_id).where(TurnExecution.session_id == row.id,
                TurnExecution.state.in_(("queued", "blocked"))).limit(1)):
                raise TaskConflict("session_turn_in_progress")
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
            task = self.tasks.create_in_session(session, owner_id, task_request,
                context=bounded_context(session, row, self.context_budget), automatic=automatic)
            row.last_sequence += 1
            message = ConversationMessage(session_id=row.id, sequence=row.last_sequence, role="user", content=request.goal, task_id=task.id)
            session.add(message)
            session.flush()
            admission = SessionTurn(id=uuid4(), session_id=row.id, idempotency_key=request.idempotency_key,
                request_sha256=digest, task_id=task.id, user_message_id=message.id)
            session.add(admission)
            session.flush()
            session.add(TurnExecution(turn_id=admission.id, session_id=row.id, ordinal=message.sequence,
                task_id=task.id, state="running"))
            row.active_task_id = task.id
            self.update_title(row, request.goal)
            row.updated_at = datetime.now(timezone.utc)
            session.flush()
            shown = [AttachmentView(name=item.name, characters=len(item.content)) for item in request.attachments]
            return TurnView(session_id=row.id, turn_id=admission.id, user_message=MessageView.model_validate(message).model_copy(
                update={"attachments": shown}), task=task)

    def enqueue(self, session_id, owner_id, request: AutoTurnCreate):
        """Admit immutable intent, then attempt the FIFO head. No provider effects."""
        from agentforge.settings import get_settings
        with self.session_factory() as session, session.begin():
            row = self.owned(session, session_id, owner_id)
            prior = session.scalar(select(SessionTurn).where(SessionTurn.session_id == row.id,
                SessionTurn.idempotency_key == request.idempotency_key))
            digest = self.request_digest(request)
            if prior:
                if prior.request_sha256 != digest:
                    raise TaskConflict("turn_idempotency_conflict")
                return self.receipt(session, row.id, prior, replayed=True)
            settle_conversation(session, row)
            if row.archived:
                raise TaskConflict("session_archived")
            waiting = self.view(session, row).queued_count
            if waiting >= 20:
                raise TaskConflict("session_queue_full")
            if row.last_sequence + waiting + bool(row.active_task_id) >= 1999:
                raise TaskConflict("session_message_limit_reached")
            if not get_settings().conversation_queue_enabled and (row.active_task_id or waiting):
                raise TaskConflict("session_turn_in_progress")
            automatic = {"repository": request.repository.model_dump(mode="json") if request.repository else None,
                "inherit_continuation": "continue_from_task_id" not in request.model_fields_set,
                "continue_from_task_id": str(request.continue_from_task_id) if request.continue_from_task_id else None}
            if "repository" not in request.model_fields_set:
                automatic["repository"] = row.repository_json
            payload = request.model_dump(mode="json", exclude={"routing", "repository", "continue_from_task_id", "idempotency_key"})
            payload["kind"] = "general"
            row.last_sequence += 1
            message = ConversationMessage(id=uuid4(), session_id=row.id, sequence=row.last_sequence,
                role="user", content=request.goal, task_id=None)
            session.add(message)
            session.flush()
            prior = SessionTurn(id=uuid4(), session_id=row.id, idempotency_key=request.idempotency_key,
                request_sha256=digest, task_id=None, user_message_id=message.id)
            session.add(prior)
            session.flush()
            session.add_all([TurnInput(turn_id=prior.id, request_json=payload, automatic_json=automatic),
                TurnExecution(turn_id=prior.id, session_id=row.id, ordinal=message.sequence, state="queued")])
            self.update_title(row, request.goal)
            row.queue_paused = False
            bump_conversation(session, row)
            row.updated_at = datetime.now(timezone.utc)
            row.dispatch_after = row.updated_at
            session.flush()
            self._dispatch(session, row)
            return self.receipt(session, row.id, prior)

    def _dispatch(self, session, row):
        if row.active_task_id or row.queue_paused or row.archived:
            return
        head = session.scalar(select(TurnExecution).where(TurnExecution.session_id == row.id,
            TurnExecution.state.in_(("queued", "blocked"))).order_by(TurnExecution.ordinal).limit(1))
        if head is None:
            row.dispatch_after = None
            return
        if head.state == "blocked":
            row.queue_paused = True
            row.dispatch_after = None
            return
        from .admission import AdmissionDenied
        source = session.get(TurnInput, head.turn_id)
        try:
            # Savepoint: a failed admission must not roll back accepted user text.
            with session.begin_nested():
                automatic = dict(source.automatic_json or {})
                selection = automatic.get("repository")
                candidate = self.continuation(session, row, selection) if selection else None
                supplied = automatic.get("continue_from_task_id")
                if automatic.pop("inherit_continuation", False):
                    automatic["continue_from_task_id"] = str(candidate) if candidate else None
                elif supplied and str(candidate) != supplied:
                    raise TaskConflict("continuation_does_not_match_conversation_selection")
                request = TaskCreate.model_validate({**source.request_json, "project_id": row.project_id})
                task = self.tasks.create_in_session(session, row.owner_id, request, automatic=automatic)
                head.task_id, head.state, head.error_code = task.id, "running", None
                row.active_task_id = task.id
                row.dispatch_after = None
                bump_conversation(session, row)
                row.updated_at = datetime.now(timezone.utc)
                session.flush()
        except (AdmissionDenied, TaskConflict, TaskNotFound) as exc:
            code = str(exc)
            head.error_code = code
            if code in {"global_concurrent_task_limit", "concurrent_task_limit"}:
                row.dispatch_after = datetime.now(timezone.utc) + timedelta(seconds=10)
            else:
                head.state = "blocked"
                row.queue_paused = True
                row.dispatch_after = None
            bump_conversation(session, row)

    def dispatch_ready(self, limit=20):
        """Bounded indexed recovery sweep; safe with several worker processes."""
        with self.session_factory() as session, session.begin():
            rows = list(session.scalars(select(Conversation).where(Conversation.dispatch_after <= func.now(),
                Conversation.active_task_id.is_(None), Conversation.queue_paused.is_(False), Conversation.archived.is_(False))
                .order_by(Conversation.dispatch_after).limit(limit).with_for_update(skip_locked=True)))
            for row in rows:
                self._dispatch(session, row)

    def turns(self, session_id, owner_id, *, after_ordinal=0, limit=100, waiting=False):
        with self.session_factory() as session:
            self.owned(session, session_id, owner_id, lock=False)
            query = select(SessionTurn).join(TurnExecution, TurnExecution.turn_id == SessionTurn.id)
            if waiting:
                query = query.where(TurnExecution.state.in_(("queued", "blocked")))
            records = session.scalars(query
                .where(SessionTurn.session_id == session_id, TurnExecution.ordinal > after_ordinal)
                .order_by(TurnExecution.ordinal).limit(limit))
            return self.receipts(session, session_id, records)

    def queue_command(self, session_id, owner_id, action, *, task_id=None, turn_id=None):
        with self.session_factory() as session, session.begin():
            conversation_lock(session, session_id)
            row = self.owned(session, session_id, owner_id)
            if action == "stop":
                # An old retry can never cancel a successor.
                if task_id and task_id == row.active_task_id:
                    task = session.get(Task, task_id, with_for_update=True)
                    if task and task.status not in TERMINAL_STATUSES and task.cancel_requested_at is None:
                        from .events import append_event
                        task.cancel_requested_at = datetime.now(timezone.utc)
                        append_event(session, task, "task.cancel_requested", {"status": task.status})
                    row.queue_paused = True
            elif action == "resume":
                if row.archived:
                    raise TaskConflict("session_archived")
                settle_conversation(session, row)
                row.queue_paused = False
                head = session.scalar(select(TurnExecution).where(TurnExecution.session_id == row.id,
                    TurnExecution.state == "blocked").order_by(TurnExecution.ordinal).limit(1))
                if head:
                    head.state, head.error_code = "queued", None
                row.dispatch_after = datetime.now(timezone.utc)
            elif action == "cancel":
                execution = session.scalar(select(TurnExecution).where(TurnExecution.turn_id == turn_id,
                    TurnExecution.session_id == row.id))
                if execution is None:
                    raise TaskNotFound("turn_not_found")
                if execution.state in {"queued", "blocked"}:
                    execution.state, execution.error_code = "cancelled", "USER_CANCELLED"
                    row.dispatch_after = datetime.now(timezone.utc)
            else:
                raise ValueError("invalid_queue_action")
            bump_conversation(session, row)
            row.updated_at = datetime.now(timezone.utc)
            session.flush()
            return self.view(session, row)


def conversation_lock(session, session_id):
    session.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(str(session_id), 0))))


def lock_task_conversation(session, task_id):
    ident = session.scalar(select(TurnExecution.session_id).where(TurnExecution.task_id == task_id))
    if ident:
        conversation_lock(session, ident)
    return ident


def bump_conversation(session, row, event_type="conversation.changed"):
    from agentforge.database.sessions import ConversationChange
    row.revision += 1
    session.add(ConversationChange(session_id=row.id, sequence=row.revision, event_type=event_type,
        payload_json={"active_task_id": str(row.active_task_id) if row.active_task_id else None,
            "queue_paused": row.queue_paused, "last_sequence": row.last_sequence}))
