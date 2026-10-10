"""Read/write/edit logical sandbox bytes, with atomic exact-hash CAS and trusted diff."""
import hashlib
from decimal import Decimal
from .contracts import ToolOutcome
from .errors import ToolError
from .files import require_text
from .test_tools import protected_test_path
from .text_ranges import read_lines

PROTECTED_WARNING = ("This path is a protected test, configuration or manifest file. Changing it disqualifies the "
                     "result; revert it unless the goal truly requires the change.")


def _normalize_whitespace(value: str) -> str:
    return " ".join(value.split())


def register_sandbox_files(catalog, repository):
    async def read(args, execution):
        files = repository.files(execution.task)
        text = require_text(files, args.path)
        page = read_lines(text, line_start=args.line_start, max_lines=args.max_lines)
        state = repository.session(execution.task)
        saved = repository.record_evidence(execution.task, operation_key=execution.operation_key,
            kind="workspace_read", revision=state["revision"], payload=dict(path=args.path,
                sha256=hashlib.sha256(files[args.path]).hexdigest(),
                line_start=page["line_start"], next_line=page["next_line"], truncated=page["truncated"]))
        return ToolOutcome(status="ok", data=dict(path=args.path, **page, sha256=hashlib.sha256(files[args.path]).hexdigest(),
            revision=state["revision"]), evidence_ids=(saved["evidence_id"],), cost_usd=Decimal(0))

    async def write(args, execution):
        if "\x00" in args.content:
            raise ToolError("INVALID_ARGUMENT", "content contains a NUL character; only text files can be written.")
        files = repository.files(execution.task)
        existing = files.get(args.path)
        if existing is None and args.expected_sha256 is not None:
            raise ToolError("FILE_NOT_FOUND", f"'{args.path}' does not exist yet. Omit expected_sha256 to create it.")
        if existing is not None:
            if args.expected_sha256 is None:
                raise ToolError("FILE_EXISTS", f"'{args.path}' already exists. Read it with read_sandbox_file, then pass its "
                                "sha256 as expected_sha256, or use edit_sandbox_file for a small change.")
            if args.expected_sha256 != hashlib.sha256(existing).hexdigest():
                raise ToolError("STALE_WRITE", f"expected_sha256 does not match the current content of '{args.path}'. "
                                "Read the file again and use the sha256 that read returns.")
        value = repository.write_file(execution.task, operation_key=execution.operation_key,
            path=args.path, content=args.content, expected_sha256=args.expected_sha256)
        if protected_test_path(args.path):
            value = {**value, "warning": PROTECTED_WARNING}
        return ToolOutcome(status="ok", data=value, cost_usd=Decimal(0))

    async def edit(args, execution):
        files = repository.files(execution.task)
        text = require_text(files, args.path)
        old, new = args.old_string, args.new_string
        if old == new:
            raise ToolError("INVALID_ARGUMENT", "old_string and new_string are identical; there is nothing to change.")
        count = text.count(old)
        if count == 0 and "\r\n" in text:
            # The file uses Windows line endings; model output normally carries bare newlines.
            crlf_old = old.replace("\r\n", "\n").replace("\n", "\r\n")
            if text.count(crlf_old):
                old, new, count = crlf_old, new.replace("\r\n", "\n").replace("\n", "\r\n"), text.count(crlf_old)
        if count == 0:
            hint = (" The text exists apart from whitespace or indentation; copy it exactly from a fresh read."
                    if _normalize_whitespace(old) in _normalize_whitespace(text) else
                    " Read the file again and copy the exact lines you want to change.")
            raise ToolError("EDIT_NO_MATCH", f"old_string was not found in '{args.path}'." + hint)
        if count > 1 and not args.replace_all:
            raise ToolError("EDIT_AMBIGUOUS", f"old_string matches {count} places in '{args.path}'. Include more surrounding "
                            "lines so it matches exactly one, or set replace_all to true.")
        updated = text.replace(old, new) if args.replace_all else text.replace(old, new, 1)
        value = repository.write_file(execution.task, operation_key=execution.operation_key, path=args.path,
            content=updated, expected_sha256=hashlib.sha256(files[args.path]).hexdigest())
        value = {**value, "replacements": count if args.replace_all else 1}
        if protected_test_path(args.path):
            value["warning"] = PROTECTED_WARNING
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

    for name, handler in (("read_sandbox_file", read), ("write_sandbox_file", write),
                          ("edit_sandbox_file", edit), ("git_diff", diff)):
        catalog.register(name, handler, max_cost_usd=Decimal(0))
