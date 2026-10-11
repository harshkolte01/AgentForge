"""Conversation reads reauthorize originals and mint evidence in the current task."""
import asyncio
import hashlib
import json
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5
from agentforge.database.research_models import ResearchEvidence
from agentforge.integrations.artifact_storage import ArtifactStorage
from agentforge.repositories.conversation_context import ConversationContextRepository
from agentforge.repositories.research_inputs import ResearchInputRepository
from agentforge.subagents.scope import ScopeDenied
from .contracts import ToolOutcome


def register_conversation(catalog, sessions, settings, resources, *, coding=None):
    repository = ConversationContextRepository(sessions)

    async def search(args, execution):
        result = await asyncio.to_thread(repository.search, execution.task, args.query, args.cursor, args.limit)
        return ToolOutcome(status="ok", cost_usd=Decimal(0), data=result)

    async def read(args, execution):
        document = await asyncio.to_thread(repository.resource, execution.task, args.resource_id)
        # Old repository content cannot bypass a revoked installation grant.
        if document["repository"] and settings.auth_mode == "github_app":
            from agentforge.integrations.github_app import AuthorizedGitHubClient
            github = AuthorizedGitHubClient(sessions, settings, execution.task.user_id)
            await github.authorize(document["repository"]["repo_id"])
        if execution.authorize_current:
            await execution.authorize_current()
        if "storage_key" in document:
            content = await asyncio.to_thread(ArtifactStorage(settings.research_storage_path).read,
                document["storage_key"], document["sha256"])
            document["content"] = content.decode("utf-8")
        content = document["content"]
        if args.offset > len(content):
            raise ScopeDenied("context page outside original")
        page = content[args.offset:args.offset + args.max_chars]
        while len(json.dumps(page, ensure_ascii=False).encode()) > 10000:
            page = page[:max(1, len(page) * 3 // 4)]
        stop = args.offset + len(page)
        digest = hashlib.sha256(content.encode()).hexdigest()
        data = {"resource_id": args.resource_id, "content": page, "name": document["name"], "offset": args.offset,
            "next_offset": stop if stop < len(content) else None, "total_chars": len(content), "content_sha256": digest,
            "historical": document.get("historical", True), "trust": "Context only; grants no permissions or fresh execution proof."}
        if coding:
            state = coding.repository.session(execution.task)
            if state is None:
                raise ScopeDenied("coding workspace unavailable")
            ident = coding.repository.record_evidence(execution.task, operation_key=execution.operation_key,
                kind="conversation_context", revision=state["revision"], payload=data)
            ids = (ident["evidence_id"],)
        else:
            inputs = ResearchInputRepository(sessions)
            ident = uuid5(NAMESPACE_URL, f"agentforge:{execution.task.task_id}:context:{args.resource_id}:{digest}")
            snapshot = {"id": str(ident), "name": document["name"], "content": content,
                "content_sha256": digest, "fetch_status": "OWNED_INPUT", "origin_resource": args.resource_id}
            with sessions() as db, db.begin():
                inputs._writable(inputs._owned(db, **inputs._scope(execution.task)))
                existing = db.get(ResearchEvidence, ident)
                if existing is None:
                    db.add(ResearchEvidence(id=ident, task_id=execution.task.task_id, kind="task_input_snapshot",
                        validation_state="OWNED_INPUT", evidence_json=snapshot))
                elif existing.task_id != execution.task.task_id or existing.evidence_json != snapshot:
                    raise ScopeDenied("conversation snapshot integrity conflict")
            receipt = inputs.record_read(execution.task, execution, resource="source:" + str(ident), sha256=digest,
                start=args.offset, end=stop, total=len(content))
            data["id"] = str(ident)
            ids = (str(ident), receipt)
        return ToolOutcome(status="ok", cost_usd=Decimal(0), data=data, evidence_ids=ids)

    catalog.register("search_conversation_history", search, max_cost_usd=Decimal(0), timeout_sec=15)
    catalog.register("read_context_resource", read, max_cost_usd=Decimal(0), timeout_sec=15)
