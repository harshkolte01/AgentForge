"""Coding ownership resolver binds all model references to the task snapshot."""
import json
from agentforge.subagents.scope import ScopeDenied

class CodingResourceResolver:
    def __init__(self, repository, authorizer):
        self.repository, self.authorizer = repository, authorizer

    async def load_context(self, ctx, references):
        await self.authorizer.check_context(ctx)
        if not set(references).issubset(ctx.authorized_context_refs):
            raise ScopeDenied("coding context reference outside grant")
        return {reference: "UNTRUSTED EXECUTION EVIDENCE\n" + json.dumps(self.repository.evidence(ctx, reference)).encode()[:3000].decode("utf-8", errors="ignore")
            for reference in references}

    async def verify_references(self, ctx, *, evidence_ids, artifact_ids):
        await self.authorizer.check_context(ctx)
        self.repository.verify_references(ctx, evidence_ids=evidence_ids, artifact_ids=artifact_ids)

    def bindings(self, ctx, name):
        """The task has exactly one repository, pinned commit and sandbox, so the model never has to name them."""
        state = self.repository.session(ctx)
        if state is None:
            return {}
        return {"repo_id": state["repo_id"], "ref": state["commit_sha"],
                "base_ref": state["commit_sha"], "sandbox_id": state["id"]}

    async def verify_tool_inputs(self, ctx, name, args):
        await self.authorizer.check_context(ctx)
        state = self.repository.session(ctx)
        if state is None:
            raise ScopeDenied("coding workspace missing")
        if hasattr(args, "sandbox_id") and args.sandbox_id != state["id"]:
            raise ScopeDenied("sandbox does not belong to this task")
        if hasattr(args, "repo_id") and args.repo_id != state["repo_id"]:
            raise ScopeDenied("repository does not match pinned task")
        if hasattr(args, "ref") and args.ref != state["commit_sha"]:
            raise ScopeDenied("repository ref is not the pinned SHA")
        if hasattr(args, "base_ref") and args.base_ref != state["commit_sha"]:
            raise ScopeDenied("diff base differs from pinned SHA")
        if name == "get_test_results":
            value = self.repository.evidence(ctx, args.run_id)
            if value["kind"] not in {"test", "build"}:
                raise ScopeDenied("test reference does not belong to this task")
