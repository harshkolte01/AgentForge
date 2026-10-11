"""Bounded, provenance-preserving context from completed predecessor turns only."""
import hashlib
import json
from uuid import UUID
from sqlalchemy import Text, cast, func, or_, select
from agentforge.database.models import Project, Task, TaskEvent, ToolCall
from agentforge.database.sessions import (Conversation, ConversationMessage, ConversationSummary,
    ProviderInput, SessionTurn, ToolResultSnapshot, TurnExecution, TurnInput)
from agentforge.database.research_models import ResearchArtifact
from agentforge.subagents.scope import ScopeDenied


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def completed_turns(db, session_id, before=None):
    query = select(TurnExecution, SessionTurn).join(SessionTurn, SessionTurn.id == TurnExecution.turn_id).where(
        TurnExecution.session_id == session_id, TurnExecution.assistant_message_id.is_not(None))
    if before is not None:
        query = query.where(TurnExecution.ordinal < before)
    return query


def latest_summary(db, session_id, before=None):
    query = select(ConversationSummary).where(ConversationSummary.session_id == session_id)
    if before is not None:
        query = query.where(ConversationSummary.through_ordinal < before)
    return db.scalar(query.order_by(ConversationSummary.through_ordinal.desc()).limit(1))


def turn_messages(db, pairs):
    ids = [ident for execution, admission in pairs for ident in (admission.user_message_id, execution.assistant_message_id) if ident]
    rows = {row.id: row for row in db.scalars(select(ConversationMessage).where(ConversationMessage.id.in_(ids)))}
    return [(execution, [rows[i] for i in (admission.user_message_id, execution.assistant_message_id) if i in rows])
        for execution, admission in pairs]


TOOLS_PER_TURN = 12


def tool_activity(db, task_ids) -> dict:
    """{task_id: ["bash: npm test (done)", ...]}: the last tool calls behind each earlier reply, as the short labels the
    activity view shows (a query, a site, a path, a command; never output). Without them the next turn only sees the
    reply's text and may redo work, or not know what it ran."""
    if not task_ids:
        return {}
    ranked = select(TaskEvent.task_id, TaskEvent.event_type, TaskEvent.payload, TaskEvent.sequence,
        func.row_number().over(partition_by=TaskEvent.task_id, order_by=TaskEvent.sequence.desc()).label("rank")).where(
        TaskEvent.task_id.in_(task_ids), TaskEvent.event_type.in_(("tool.started", "tool.finished"))).subquery()
    rows = db.execute(select(ranked.c.task_id, ranked.c.event_type, ranked.c.payload).where(ranked.c.rank <= 2 * TOOLS_PER_TURN)
        .order_by(ranked.c.task_id, ranked.c.sequence))
    calls: dict = {}
    for task_id, kind, payload in rows:
        key = payload.get("call_id") or payload.get("tool_call_id")
        entry = calls.setdefault(task_id, {}).setdefault(key, {"tool": payload.get("tool"), "detail": None, "outcome": None})
        if kind == "tool.started":
            entry["detail"] = payload.get("detail")
        else:
            entry["outcome"] = ("declined by the person" if payload.get("error_code") == "DECLINED"
                                else payload.get("outcome") or payload.get("status"))
    labels = {}
    for task_id, entries in calls.items():
        labels[task_id] = [f"{entry['tool']}" + (f": {entry['detail']}" if entry["detail"] else "")
            + f" ({'done' if entry['outcome'] in ('completed', 'ok') else entry['outcome'] or 'unfinished'})"
            for entry in list(entries.values())[-TOOLS_PER_TURN:] if entry["tool"] and entry["tool"] != "unknown"]
    return labels


def history_snapshot(db, conversation, *, before=None, budget=48000):
    """Pure shared planner: same function powers next-turn status and execution."""
    from .sessions import clip
    if not 2000 <= budget <= 48000:
        raise ValueError("invalid_history_budget")
    pairs = list(db.execute(completed_turns(db, conversation.id, before)
        .order_by(TurnExecution.ordinal.desc()).limit(6)))
    summary = latest_summary(db, conversation.id, before)
    digest = {"untrusted_history": True, "summary": {"method": "incremental_source_linked_v1",
        "excerpts": [], "entries": json.loads(encoded(summary.content_json["entries"])) if summary else [],
        "id": str(summary.id) if summary else None, "through_ordinal": summary.through_ordinal if summary else 0},
        "recent_messages": [], "resources": [], "coverage": {"revision": conversation.revision,
        "before_ordinal": before, "summary_through_ordinal": summary.through_ordinal if summary else 0}}
    recent = []
    activity = tool_activity(db, [execution.task_id for execution, _ in pairs if execution.task_id])
    for execution, rows in reversed(turn_messages(db, pairs)):
        for row in rows:
            recent.append({"message_id": str(row.id), "resource_id": f"message:{row.id}", "sequence": row.sequence,
                "turn_id": str(execution.turn_id), "turn_ordinal": execution.ordinal, "role": row.role,
                "state": execution.state, "text": row.content, "truncated": False,
                **({"tools": activity[execution.task_id]} if row.role == "assistant" and activity.get(execution.task_id) else {})})
    digest["recent_messages"] = recent
    # Small deterministic manifests make previous attachments and deliverables discoverable.
    turn_ids = [execution.turn_id for execution, _ in pairs]
    for source in db.scalars(select(TurnInput).where(TurnInput.turn_id.in_(turn_ids))):
        for index, item in enumerate(source.request_json.get("attachments", [])):
            digest["resources"].append({"resource_id": f"attachment:{source.turn_id}:{index}", "name": item["name"], "kind": "attachment"})
    task_ids = [execution.task_id for execution, _ in pairs if execution.task_id]
    for artifact in db.scalars(select(ResearchArtifact).where(ResearchArtifact.task_id.in_(task_ids)).order_by(ResearchArtifact.created_at.desc()).limit(12)):
        digest["resources"].append({"resource_id": f"artifact:{artifact.id}", "name": artifact.filename, "kind": "artifact"})
    digest["resources"] = digest["resources"][:12]
    # Reserve at least half the allowance for the recent turns, then remove the
    # oldest pairs as units. Sources remain retrievable even when not injected.
    while digest["summary"]["entries"] and len(encoded(digest["summary"]).encode()) > budget // 3:
        digest["summary"]["entries"] = digest["summary"]["entries"][:-1]
    while len(encoded(digest).encode()) > budget and len(digest["recent_messages"]) > 2:
        first = digest["recent_messages"][0]["turn_id"]
        digest["recent_messages"] = [m for m in digest["recent_messages"] if m["turn_id"] != first]
    while len(encoded(digest).encode()) > budget and digest["resources"]:
        digest["resources"].pop()
    while len(encoded(digest).encode()) > budget and digest["summary"]["entries"]:
        digest["summary"]["entries"].pop()
    if len(encoded(digest).encode()) > budget:
        originals = [m["text"] for m in digest["recent_messages"]]
        low, high = 0, max(map(len, originals), default=0)
        while low < high:
            cap = (low + high + 1) // 2
            for message, text in zip(digest["recent_messages"], originals):
                message["text"], message["truncated"] = clip(text, cap), len(text) > cap
            if len(encoded(digest).encode()) <= budget - 160:
                low = cap
            else:
                high = cap - 1
        for message, text in zip(digest["recent_messages"], originals):
            message["text"], message["truncated"] = clip(text, low), len(text) > low
    oldest = min((m["turn_ordinal"] for m in digest["recent_messages"]), default=before or conversation.last_sequence + 1)
    uncovered = db.scalar(select(func.count()).select_from(TurnExecution).where(TurnExecution.session_id == conversation.id,
        TurnExecution.assistant_message_id.is_not(None), TurnExecution.ordinal > (summary.through_ordinal if summary else 0),
        TurnExecution.ordinal < oldest)) or 0
    digest["coverage"]["uncovered_turns"] = uncovered
    digest["coverage"]["retrieval_available"] = True
    if len(encoded(digest).encode()) > budget:
        raise ValueError("context_envelope_exceeds_budget")
    return digest


class ConversationContextRepository:
    def __init__(self, sessions):
        self.sessions = sessions

    @staticmethod
    def scope(db, ctx):
        owned = db.scalar(select(Task).join(Project).where(Task.id == ctx.task_id, Task.created_by == ctx.user_id,
            Task.project_id == ctx.project_id, Project.owner_id == ctx.user_id))
        execution = db.scalar(select(TurnExecution).where(TurnExecution.task_id == ctx.task_id))
        if owned is None or execution is None:
            raise ScopeDenied("conversation context unavailable for this task")
        conversation = db.get(Conversation, execution.session_id)
        if conversation.owner_id != ctx.user_id or conversation.project_id != ctx.project_id:
            raise ScopeDenied("conversation ownership changed")
        return owned, execution, conversation

    def search(self, ctx, query, cursor=0, limit=10):
        with self.sessions() as db:
            _, current, conversation = self.scope(db, ctx)
            pairs = completed_turns(db, conversation.id, current.ordinal).subquery()
            messages = select(ConversationMessage).join(pairs, or_(ConversationMessage.id == pairs.c.user_message_id,
                ConversationMessage.id == pairs.c.assistant_message_id)).where(ConversationMessage.sequence > cursor)
            if query.strip():
                messages = messages.where(func.to_tsvector('simple', ConversationMessage.content).op('@@')(
                    func.plainto_tsquery('simple', query)))
            rows = list(db.scalars(messages.order_by(ConversationMessage.sequence).limit(limit + 1)))
            results = [{"resource_id": f"message:{m.id}", "role": m.role, "sequence": m.sequence,
                "preview": m.content[:300], "truncated": len(m.content) > 300} for m in rows[:limit]]
            # Attachment metadata is searched independently and never includes content.
            source_query = select(TurnInput, TurnExecution).join(TurnExecution, TurnExecution.turn_id == TurnInput.turn_id)
            source_query = source_query.where(
                cast(TurnInput.request_json, Text).icontains(query, autoescape=True))
            sources = db.execute(source_query
                .where(TurnExecution.session_id == conversation.id, TurnExecution.ordinal < current.ordinal,
                    TurnExecution.assistant_message_id.is_not(None)).order_by(TurnExecution.ordinal.desc()).limit(20))
            resources = [{"resource_id": f"attachment:{source.turn_id}:{index}", "kind": "attachment", "name": item["name"]}
                for source, _ in sources for index, item in enumerate(source.request_json.get("attachments", []))
                if query.casefold() in (item["name"] + " " + item["content"]).casefold()][:limit]
            # Legacy attachments remain in their original task grant snapshots.
            legacy = db.execute(select(Task, TurnExecution).join(TurnExecution, TurnExecution.task_id == Task.id)
                .outerjoin(TurnInput, TurnInput.turn_id == TurnExecution.turn_id).where(TurnInput.turn_id.is_(None),
                    TurnExecution.session_id == conversation.id, TurnExecution.ordinal < current.ordinal,
                    TurnExecution.assistant_message_id.is_not(None),
                    cast(Task.permissions_json["attachments"], Text).icontains(query, autoescape=True))
                .order_by(TurnExecution.ordinal.desc()).limit(20))
            resources.extend({"resource_id": f"attachment:{e.turn_id}:{index}", "kind": "attachment", "name": a["name"]}
                for task, e in legacy for index, a in enumerate(task.permissions_json.get("attachments", []))
                if query.casefold() in (a["name"] + " " + a["content"]).casefold())
            artifact_query = select(ResearchArtifact).join(TurnExecution, TurnExecution.task_id == ResearchArtifact.task_id).where(
                TurnExecution.session_id == conversation.id, TurnExecution.ordinal < current.ordinal,
                TurnExecution.assistant_message_id.is_not(None), ResearchArtifact.filename.icontains(query, autoescape=True))
            resources.extend({"resource_id": f"artifact:{a.id}", "kind": "artifact", "name": a.filename}
                for a in db.scalars(artifact_query.order_by(ResearchArtifact.created_at.desc()).limit(limit)))
            snapshots = db.execute(select(ToolResultSnapshot, ToolCall.tool_name).join(ToolCall,
                ToolCall.operation_key == ToolResultSnapshot.operation_key).join(TurnExecution,
                TurnExecution.task_id == ToolResultSnapshot.task_id).where(TurnExecution.session_id == conversation.id,
                TurnExecution.ordinal < current.ordinal, TurnExecution.assistant_message_id.is_not(None),
                or_(ToolCall.tool_name.icontains(query, autoescape=True),
                    ToolResultSnapshot.content.icontains(query, autoescape=True))).order_by(TurnExecution.ordinal.desc()).limit(limit))
            resources.extend({"resource_id": f"tool:{snapshot.id}", "kind": "tool", "name": name}
                for snapshot, name in snapshots)
            return {"messages": results, "resources": resources[:limit],
                "next_cursor": rows[limit - 1].sequence if len(rows) > limit else None,
                "resource_results_limited": len(resources) > limit}

    def resource(self, ctx, reference):
        """Resolve originals without crossing the frozen predecessor boundary."""
        with self.sessions() as db:
            _, current, conversation = self.scope(db, ctx)
            try:
                kind, raw = reference.split(":", 1)
                index = 0
                if kind == "attachment":
                    raw, index = raw.rsplit(":", 1)
                    index = int(index)
                ident = UUID(raw)
            except (ValueError, TypeError):
                raise ScopeDenied("invalid context resource") from None
            source_task = None
            if kind == "tool":
                row = db.get(ToolResultSnapshot, ident)
                if row is None:
                    raise ScopeDenied("tool snapshot outside task")
                if row.task_id != ctx.task_id:
                    earlier = db.scalar(select(TurnExecution.turn_id).where(TurnExecution.task_id == row.task_id,
                        TurnExecution.session_id == conversation.id, TurnExecution.ordinal < current.ordinal,
                        TurnExecution.assistant_message_id.is_not(None)))
                    if earlier is None:
                        raise ScopeDenied("tool snapshot outside conversation boundary")
                source = db.get(Task, row.task_id)
                if source.created_by != ctx.user_id or source.project_id != ctx.project_id:
                    raise ScopeDenied("tool snapshot ownership changed")
                grants = source.permissions_json
                return {"content": row.content, "name": "Stored tool result",
                    "historical": row.task_id != ctx.task_id,
                    "repository": grants.get("repository") or (grants.get("coding") or {}).get("repository")}
            if kind == "message":
                execution = db.scalar(select(TurnExecution).join(SessionTurn, SessionTurn.id == TurnExecution.turn_id).where(
                    or_(SessionTurn.user_message_id == ident, TurnExecution.assistant_message_id == ident),
                    TurnExecution.session_id == conversation.id, TurnExecution.ordinal < current.ordinal,
                    TurnExecution.assistant_message_id.is_not(None)))
                row = db.get(ConversationMessage, ident) if execution else None
                if row is None or row.session_id != conversation.id:
                    raise ScopeDenied("message outside conversation boundary")
                source_task = execution.task_id
                value = {"content": row.content, "name": f"{row.role} message {row.sequence}"}
            elif kind in {"attachment", "artifact"}:
                artifact = db.get(ResearchArtifact, ident) if kind == "artifact" else None
                execution = db.scalar(select(TurnExecution).where(
                    TurnExecution.task_id == artifact.task_id if artifact else TurnExecution.turn_id == ident,
                    TurnExecution.session_id == conversation.id, TurnExecution.ordinal < current.ordinal,
                    TurnExecution.assistant_message_id.is_not(None)))
                if execution is None:
                    raise ScopeDenied("resource outside conversation boundary")
                source_task = execution.task_id
                if kind == "attachment":
                    source = db.get(TurnInput, ident)
                    old = db.get(Task, source_task) if source_task else None
                    inputs = source.request_json if source else (old.permissions_json if old else {})
                    items = inputs.get("attachments", [])
                    if not 0 <= index < len(items):
                        raise ScopeDenied("attachment unavailable")
                    value = {"name": items[index]["name"], "content": items[index]["content"]}
                    if items[index].get("upload_id"):
                        value["upload"] = items[index]
                elif artifact:
                    value = {"name": artifact.filename, "storage_key": artifact.storage_key, "sha256": artifact.sha256}
                else:
                    raise ScopeDenied("artifact unavailable")
            else:
                raise ScopeDenied("unsupported context resource")
            task = db.get(Task, source_task) if source_task else None
            if task and (task.created_by != ctx.user_id or task.project_id != ctx.project_id):
                raise ScopeDenied("historical task ownership changed")
            grants = task.permissions_json if task else {}
            value["repository"] = grants.get("repository") or (grants.get("coding") or {}).get("repository")
            return value

    def record_provider_input(self, task_id, operation_key, messages, tools):
        """Fixes the identity of each model request before it is paid for. Written once per model step, so only the
        digest of the exact request and its shape are kept: the request is the whole conversation so far, and nothing
        reads it back (the history it was built from stays in the conversation's own tables)."""
        payload = {"messages": messages, "tools": tools, "prompt_version": 2}
        body = encoded(payload).encode()
        digest = hashlib.sha256(body).hexdigest()
        with self.sessions() as db, db.begin():
            prior = db.get(ProviderInput, operation_key)
            if prior is not None:
                if prior.sha256 != digest:
                    raise ScopeDenied("provider input identity conflict")
                return
            shape = {"prompt_version": 2, "bytes": len(body), "messages": len(messages), "tools": len(tools or [])}
            db.add(ProviderInput(task_id=task_id, operation_key=operation_key, sha256=digest, payload_json=shape))
