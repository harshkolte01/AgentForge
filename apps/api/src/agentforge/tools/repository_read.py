"""Pinned imported repository reads; no Git process or host checkout."""
import hashlib
from decimal import Decimal
from agentforge.sandbox.workspace import read_text
from .contracts import ToolOutcome
from .text_ranges import read_lines


def register_repository_read(catalog, repository, github):
    async def read(args, execution):
        files = repository.files(execution.task, baseline=True)
        data = files[args.path]
        try:
            text = read_text(files, args.path)
        except ValueError:
            raise ValueError("binary_file_not_readable_as_text") from None
        page = read_lines(text, line_start=args.line_start, max_lines=args.max_lines)
        state = repository.session(execution.task)
        payload = dict(path=args.path, sha256=hashlib.sha256(data).hexdigest(), byte_size=len(data),
            line_count=page["total_lines"], binary=False,
            line_start=page["line_start"], next_line=page["next_line"], truncated=page["truncated"])
        saved = repository.record_evidence(execution.task, operation_key=execution.operation_key,
            kind="repository_read", revision=state["revision"], payload=payload)
        return ToolOutcome(status="ok", data={**payload, **page},
            evidence_ids=(saved["evidence_id"],), cost_usd=Decimal(0))

    async def listing(args, execution):
        files = repository.files(execution.task, baseline=True)
        prefix = "" if args.path == "." else args.path.rstrip("/") + "/"
        paths = [path for path in files if path.startswith(prefix)]
        preview = []
        size = 0
        for path in paths:
            size += len(path.encode()) + 4
            if size > 10000:
                break
            preview.append(path)
        return ToolOutcome(status="ok", data={"paths": preview, "truncated": len(paths) > len(preview)}, cost_usd=Decimal(0))

    async def search(args, execution):
        files = repository.files(execution.task, baseline=True)
        matches = []
        for path in files:
            try:
                text = read_text(files, path)
            except ValueError:
                continue
            for number, line in enumerate(text.splitlines(), 1):
                if args.query.casefold() in line.casefold():
                    matches.append(dict(path=path, line=number, text=line[:300]))
                    if len(matches) >= 30:
                        break
            if len(matches) >= 30:
                break
        state = repository.session(execution.task)
        saved = repository.record_evidence(execution.task, operation_key=execution.operation_key,
            kind="repository_search", revision=state["revision"], payload={"matches": matches})
        return ToolOutcome(status="ok", data={"matches": matches}, evidence_ids=(saved["evidence_id"],), cost_usd=Decimal(0))

    async def issue(args, execution):
        value = await github.get_issue(args.repo_id, args.issue_number)
        state = repository.session(execution.task)
        saved = repository.record_evidence(execution.task, operation_key=execution.operation_key,
            kind="repository_issue", revision=state["revision"], payload=value)
        return ToolOutcome(status="ok", data=value, evidence_ids=(saved["evidence_id"],), cost_usd=Decimal(0))

    for name, handler in (("read_repo_file", read), ("list_repo_files", listing), ("search_repo", search), ("get_issue", issue)):
        catalog.register(name, handler, max_cost_usd=Decimal(0))
