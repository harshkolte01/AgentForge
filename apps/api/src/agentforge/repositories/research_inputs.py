"""Owned immutable input snapshots and proof of successful paged consumption.

A model returning an evidence ID is never a read receipt. A receipt must occur in
an actual successful gateway tool call for this exact child and content hash.
"""
import hashlib
import json
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5
from sqlalchemy import select
from agentforge.database.models import AgentRun, ToolCall
from agentforge.database.research_models import ResearchEvidence
from agentforge.repositories.research import ResearchRepository, _uuid
from agentforge.subagents.scope import ScopeDenied


def full_range(ranges, total):
    """Require gap-free coverage; duplicate/overlapping pages do not add coverage."""
    end = 0
    for start, stop in sorted(ranges):
        if start > end or start < 0 or stop < start or stop > total:
            return False
        end = max(end, stop)
    return end == total


class ResearchInputRepository(ResearchRepository):
    def input_manifest(self, ctx):
        with self.session_factory() as db:
            task = self._owned(db, **self._scope(ctx))
            grants = task.permissions_json or {}
            items = [("goal", 0, "User goal", task.goal)]
            items.extend(("attachment", i, item["name"], item["content"])
                         for i, item in enumerate(grants.get("attachments", [])))
            history = grants.get("session_context", {})
            if history:
                items.append(("history", 0, "Prior conversation", json.dumps(history, ensure_ascii=False)))
            return [{"input_kind": kind, "attachment_index": index, "name": name,
                "id": str(uuid5(NAMESPACE_URL, f"agentforge:{ctx.task_id}:input:{kind}:{index}:" + hashlib.sha256(content.encode()).hexdigest())),
                "content": content, "content_sha256": hashlib.sha256(content.encode()).hexdigest()}
                for kind, index, name, content in items]

    def input_snapshot(self, ctx, kind, index=0):
        item = next((item for item in self.input_manifest(ctx)
            if item["input_kind"] == kind and item["attachment_index"] == index), None)
        if item is None:
            raise ScopeDenied("task input is unavailable")
        with self.session_factory() as db, db.begin():
            self._writable(self._owned(db, **self._scope(ctx)))
            row = db.get(ResearchEvidence, UUID(item["id"]))
            payload = {**item, "fetch_status": "OWNED_INPUT"}
            if row is None:
                db.add(ResearchEvidence(id=UUID(item["id"]), task_id=ctx.task_id,
                    kind="task_input_snapshot", validation_state="OWNED_INPUT", evidence_json=payload))
            elif row.task_id != ctx.task_id or row.evidence_json != payload:
                raise ScopeDenied("input snapshot integrity failure")
        return payload

    def read_document(self, ctx, source_id):
        try:
            document = self.read_source(source_id, **self._scope(ctx))
            if hashlib.sha256(document["content"].encode()).hexdigest() != document["content_sha256"]:
                raise ScopeDenied("source snapshot content mismatch")
            return document
        except ScopeDenied:
            with self.session_factory() as db:
                self._owned(db, **self._scope(ctx))
                row = db.scalar(select(ResearchEvidence).where(ResearchEvidence.id == _uuid(source_id),
                    ResearchEvidence.task_id == ctx.task_id, ResearchEvidence.kind == "task_input_snapshot"))
                if row is None:
                    raise ScopeDenied("document is outside the task") from None
                p = row.evidence_json
                if hashlib.sha256(p["content"].encode()).hexdigest() != p["content_sha256"]:
                    raise ScopeDenied("input snapshot content mismatch")
                return {**p, "title": p["name"], "url": "", "retrieved_at": row.created_at.isoformat()}

    def record_read(self, ctx, execution, *, resource, sha256, start, end, total):
        read_id = uuid4()
        with self.session_factory() as db, db.begin():
            self._writable(self._owned(db, **self._scope(ctx)))
            run = db.get(AgentRun, execution.agent_run_id)
            if run is None or run.task_id != ctx.task_id or run.status != "RUNNING":
                raise ScopeDenied("read requires an active owned child")
            db.add(ResearchEvidence(id=read_id, task_id=ctx.task_id, kind="document_read",
                validation_state="READ", evidence_json={"run_id": str(execution.agent_run_id),
                    "operation_key": execution.operation_key, "resource": resource,
                    "sha256": sha256, "start": start, "end": end, "total": total}))
        return str(read_id)

    def consumed(self, ctx, run_id, requirements):
        """requirements maps opaque resource to exact (sha256, character length)."""
        with self.session_factory() as db:
            self._owned(db, **self._scope(ctx))
            calls = list(db.scalars(select(ToolCall).where(ToolCall.task_id == ctx.task_id,
                ToolCall.agent_run_id == run_id, ToolCall.status == "SUCCEEDED",
                ToolCall.tool_name.in_(("read_task_input", "read_research_source", "read_task_artifact")))))
            receipts = {value: call.operation_key for call in calls for value in call.evidence_ids}
            ids = [_uuid(value) for value in receipts]
            rows = db.scalars(select(ResearchEvidence).where(ResearchEvidence.task_id == ctx.task_id,
                ResearchEvidence.id.in_(ids), ResearchEvidence.kind == "document_read"))
            spans = {key: [] for key in requirements}
            for row in rows:
                p = row.evidence_json
                key = p.get("resource")
                if key not in requirements or p.get("run_id") != str(run_id) or p.get("operation_key") != receipts[str(row.id)]:
                    continue
                digest, total = requirements[key]
                if p.get("sha256") == digest and p.get("total") == total:
                    spans[key].append((p["start"], p["end"]))
            return {key for key, ranges in spans.items() if ranges and full_range(ranges, requirements[key][1])}

    def run_payload(self, ctx, run_id):
        with self.session_factory() as db:
            self._owned(db, **self._scope(ctx))
            row = db.scalar(select(AgentRun).where(AgentRun.id == run_id, AgentRun.task_id == ctx.task_id))
            return row.result_json if row and row.finished_at else None

    def research_runs(self, ctx):
        with self.session_factory() as db:
            self._owned(db, **self._scope(ctx))
            return [(row.id, row.result_json) for row in db.scalars(select(AgentRun).where(
                AgentRun.task_id == ctx.task_id, AgentRun.result_json.is_not(None)).order_by(AgentRun.started_at, AgentRun.id))
                if row.definition_snapshot.get("output_schema_id") in {"research_result_v1", "general_result_v1"}]

    def task_kind(self, ctx):
        with self.session_factory() as db:
            return (self._owned(db, **self._scope(ctx)).permissions_json or {}).get("kind", "research")
