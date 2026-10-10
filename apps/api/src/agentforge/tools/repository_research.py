"""Selected pinned repository reads as immutable research source evidence."""
from decimal import Decimal
import json
from urllib.parse import quote
from agentforge.repositories.research_inputs import ResearchInputRepository
from agentforge.subagents.scope import ScopeDenied
from agentforge.tools.contracts import ToolOutcome
from agentforge.tools.text_ranges import read_lines


def _preview_fits(data, evidence_ids=()):
    # Match the gateway's JSON encoding, including escaping and reference IDs.
    # Leave room below its 16000-byte ceiling for the remaining outcome envelope.
    return len(json.dumps({"data": data, "evidence_ids": evidence_ids},
        ensure_ascii=False, allow_nan=False).encode("utf-8")) <= 10000


def register_repository_research(catalog, sessions, selection, github, resources):
    repository = ResearchInputRepository(sessions)
    cached = None
    original_verify = resources.verify_tool_inputs

    async def verify(ctx, name, args):
        await original_verify(ctx, name, args)
        if name not in {"read_repo_file", "list_repo_files", "search_repo", "get_issue"}:
            return
        if args.repo_id != selection["repo_id"] or selection["repo_id"] not in ctx.task_permissions.allowed_repo_ids:
            raise ScopeDenied("repository is outside selected scope")
        if hasattr(args, "ref") and args.ref != selection["ref"]:
            raise ScopeDenied("repository ref differs from selected commit")
        authorize = getattr(github, "authorize", None)
        if authorize:
            await authorize(selection["repo_id"])
    resources.verify_tool_inputs = verify

    async def files(execution):
        nonlocal cached
        if cached is None:
            snapshot = await github.snapshot(selection["repo_id"], selection["ref"])
            if snapshot.commit_sha != selection["ref"]:
                raise ScopeDenied("repository snapshot differs from selected commit")
            cached = snapshot.files
        if execution.authorize_current:
            await execution.authorize_current()
        return cached

    def source(execution, path, content, ordinal=0, *, issue_number=None):
        return repository.add_source(execution.task, run_id=execution.agent_run_id,
            operation_key=execution.operation_key, ordinal=ordinal,
            url=(f"https://github.com/{selection['repo_id']}/issues/{issue_number}" if issue_number else
                f"https://github.com/{selection['repo_id']}/blob/{selection['ref']}/{quote(path, safe='/')}"),
            title=path, content=content, source_type="repository", fetch_status="FETCHED")

    async def read(args, execution):
        data = (await files(execution))[args.path]
        try:
            text = data.decode("utf-8")
        except UnicodeError:
            raise ValueError("binary_file_not_readable_as_text") from None
        saved = source(execution, args.path, text)
        return ToolOutcome(status="ok", data={"path": args.path, "source_id": saved["id"],
            **read_lines(text, line_start=args.line_start, max_lines=args.max_lines)},
            evidence_ids=(saved["id"],), cost_usd=Decimal(0))

    async def listing(args, execution):
        selected = await files(execution)
        prefix = "" if args.path == "." else args.path.rstrip("/") + "/"
        paths = [path for path in selected if path.startswith(prefix)]
        preview = []
        for path in paths:
            if not _preview_fits({"paths": [*preview, path], "truncated": False}):
                break
            preview.append(path)
        return ToolOutcome(status="ok", data={"paths": preview, "truncated": len(paths) > len(preview)}, cost_usd=Decimal(0))

    async def search(args, execution):
        selected = await files(execution)
        matches, refs = [], []
        for path, data in selected.items():
            try:
                text = data.decode("utf-8")
            except UnicodeError:
                continue
            source_id = None
            for line, value in enumerate(text.splitlines(), 1):
                if args.query.casefold() not in value.casefold():
                    continue
                # Source IDs are canonical UUID strings. Size the first match
                # before writing a source that the bounded result cannot return.
                candidate = {"path": path, "line": line, "text": value[:300],
                    "source_id": source_id or "00000000-0000-0000-0000-000000000000"}
                candidate_refs = refs if source_id else [*refs, candidate["source_id"]]
                if len(matches) >= 30 or not _preview_fits({"matches": [*matches, candidate], "truncated": False}, candidate_refs):
                    return ToolOutcome(status="ok", data={"matches": matches, "truncated": True},
                        evidence_ids=tuple(refs), cost_usd=Decimal(0))
                if source_id is None:
                    source_id = source(execution, path, text, len(refs))["id"]
                    refs.append(source_id)
                    candidate["source_id"] = source_id
                matches.append(candidate)
        return ToolOutcome(status="ok", data={"matches": matches, "truncated": False}, evidence_ids=tuple(refs), cost_usd=Decimal(0))

    async def issue(args, execution):
        value = await github.get_issue(selection["repo_id"], args.issue_number)
        if execution.authorize_current:
            await execution.authorize_current()
        saved = source(execution, f"Issue {args.issue_number}", json.dumps(value, ensure_ascii=False), issue_number=args.issue_number)
        return ToolOutcome(status="ok", data={**value, "source_id": saved["id"]}, evidence_ids=(saved["id"],), cost_usd=Decimal(0))

    for name, handler in (("read_repo_file", read), ("list_repo_files", listing), ("search_repo", search), ("get_issue", issue)):
        catalog.register(name, handler, max_cost_usd=Decimal(0), timeout_sec=90)
