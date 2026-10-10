"""Read/write logical sandbox bytes, with atomic exact-hash CAS and trusted diff."""
import hashlib
from decimal import Decimal
from agentforge.sandbox.workspace import read_text
from .contracts import ToolOutcome
from .text_ranges import read_lines


def register_sandbox_files(catalog, repository):
    async def read(args, execution):
        files = repository.files(execution.task)
        text = read_text(files, args.path)
        page = read_lines(text, line_start=args.line_start, max_lines=args.max_lines)
        state = repository.session(execution.task)
        saved = repository.record_evidence(execution.task, operation_key=execution.operation_key,
            kind="workspace_read", revision=state["revision"], payload=dict(path=args.path,
                sha256=hashlib.sha256(files[args.path]).hexdigest(),
                line_start=page["line_start"], next_line=page["next_line"], truncated=page["truncated"]))
        return ToolOutcome(status="ok", data=dict(path=args.path, **page, sha256=hashlib.sha256(files[args.path]).hexdigest(),
            revision=state["revision"]), evidence_ids=(saved["evidence_id"],), cost_usd=Decimal(0))

    async def write(args, execution):
        value = repository.write_file(execution.task, operation_key=execution.operation_key,
            path=args.path, content=args.content, expected_sha256=args.expected_sha256)
        return ToolOutcome(status="ok", data=value, cost_usd=Decimal(0))

    async def diff(args, execution):
        changed, data = repository.diff(execution.task)
        state = repository.session(execution.task)
        saved = repository.record_evidence(execution.task, operation_key=execution.operation_key,
            kind="diff", revision=state["revision"], payload=dict(changed_paths=changed,
                base_sha=state["commit_sha"], sha256=hashlib.sha256(data).hexdigest()),
            artifact_content=data, filename="changes.diff", mime_type="text/x-diff; charset=utf-8")
        return ToolOutcome(status="ok", data=dict(diff=data[:10000].decode("utf-8", errors="replace"),
            changed_paths=changed, revision=state["revision"], truncated=len(data) > 10000),
            evidence_ids=(saved["evidence_id"],), artifact_ids=(saved["artifact_id"],), cost_usd=Decimal(0))

    for name, handler in (("read_sandbox_file", read), ("write_sandbox_file", write), ("git_diff", diff)):
        catalog.register(name, handler, max_cost_usd=Decimal(0))
