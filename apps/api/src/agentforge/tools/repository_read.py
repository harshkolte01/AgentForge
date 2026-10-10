"""Pinned imported repository reads; no Git process or host checkout."""
import hashlib
import json
from decimal import Decimal
from .contracts import ToolOutcome
from .files import in_directory, line_matches, match_record, overview, require_directory, require_text
from .text_ranges import read_lines

PREVIEW_BYTES = 10_000


def register_repository_read(catalog, repository, github):
    async def read(args, execution):
        files = repository.files(execution.task, baseline=True)
        text = require_text(files, args.path)
        data = files[args.path]
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
        directory = "." if args.path == "." else args.path.rstrip("/")
        require_directory(files, None if directory == "." else directory)
        paths = [path for path in files if directory == "." or path.startswith(directory + "/")]
        entries = overview(paths, directory, args.max_depth)
        preview = []
        size = 0
        for entry in entries:
            size += len(entry.encode()) + 4
            if size > PREVIEW_BYTES:
                break
            preview.append(entry)
        return ToolOutcome(status="ok", data={"paths": preview, "truncated": len(entries) > len(preview),
                                              "total_entries": len(entries)}, cost_usd=Decimal(0))

    async def search(args, execution):
        files = repository.files(execution.task, baseline=True)
        require_directory(files, args.path_prefix)
        matches, size, truncated = [], 0, False
        for path, data in files.items():
            if not in_directory(path, args.path_prefix):
                continue
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                continue
            for number, line, before, after in line_matches(text, args.query,
                                                           case_sensitive=args.case_sensitive, context=args.context_lines):
                record = match_record(path, number, line, before, after)
                size += len(json.dumps(record, ensure_ascii=False).encode())
                if len(matches) >= args.max_results or size > PREVIEW_BYTES:
                    truncated = True
                    break
                matches.append(record)
            if truncated:
                break
        state = repository.session(execution.task)
        saved = repository.record_evidence(execution.task, operation_key=execution.operation_key,
            kind="repository_search", revision=state["revision"], payload={"matches": matches})
        return ToolOutcome(status="ok", data={"matches": matches, "truncated": truncated},
                           evidence_ids=(saved["evidence_id"],), cost_usd=Decimal(0))

    async def issue(args, execution):
        value = await github.get_issue(args.repo_id, args.issue_number)
        state = repository.session(execution.task)
        saved = repository.record_evidence(execution.task, operation_key=execution.operation_key,
            kind="repository_issue", revision=state["revision"], payload=value)
        return ToolOutcome(status="ok", data=value, evidence_ids=(saved["evidence_id"],), cost_usd=Decimal(0))

    for name, handler in (("read_repo_file", read), ("list_repo_files", listing), ("search_repo", search), ("get_issue", issue)):
        catalog.register(name, handler, max_cost_usd=Decimal(0))
