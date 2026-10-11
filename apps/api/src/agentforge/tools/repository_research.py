"""Selected pinned repository reads as immutable research source evidence."""
from decimal import Decimal
import json
from urllib.parse import quote
from agentforge.repositories.research_inputs import ResearchInputRepository
from agentforge.subagents.scope import ScopeDenied
from agentforge.tools.contracts import ToolOutcome
from agentforge.tools.files import in_directory, line_matches, match_record, overview, require_directory, require_text
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
        if name not in {"read_repo_file", "list_repo_files", "search_repo", "get_issue", "get_pr_status"}:
            return
        if args.repo_id != selection["repo_id"] or selection["repo_id"] not in ctx.task_permissions.allowed_repo_ids:
            raise ScopeDenied("repository is outside selected scope")
        if hasattr(args, "ref") and args.ref != selection["ref"]:
            raise ScopeDenied("repository ref differs from selected commit")
        authorize = getattr(github, "authorize", None)
        if authorize:
            await authorize(selection["repo_id"])
    resources.verify_tool_inputs = verify

    def bindings(ctx, name):
        # One repository and one pinned commit are selected for the whole task.
        return {"repo_id": selection["repo_id"], "ref": selection["ref"]}
    resources.bindings = bindings

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
        text = require_text(await files(execution), args.path)
        saved = source(execution, args.path, text)
        return ToolOutcome(status="ok", data={"path": args.path, "source_id": saved["id"],
            **read_lines(text, line_start=args.line_start, max_lines=args.max_lines)},
            evidence_ids=(saved["id"],), cost_usd=Decimal(0))

    async def listing(args, execution):
        selected = await files(execution)
        directory = "." if args.path == "." else args.path.rstrip("/")
        require_directory(selected, None if directory == "." else directory)
        paths = [path for path in selected if directory == "." or path.startswith(directory + "/")]
        entries = overview(paths, directory, args.max_depth)
        preview = []
        for entry in entries:
            if not _preview_fits({"paths": [*preview, entry], "truncated": False}):
                break
            preview.append(entry)
        return ToolOutcome(status="ok", data={"paths": preview, "truncated": len(entries) > len(preview),
                                              "total_entries": len(entries)}, cost_usd=Decimal(0))

    async def search(args, execution):
        selected = await files(execution)
        require_directory(selected, args.path_prefix)
        matches, refs = [], []
        for path, data in selected.items():
            if not in_directory(path, args.path_prefix):
                continue
            try:
                text = data.decode("utf-8")
            except UnicodeError:
                continue
            source_id = None
            for line, value, before, after in line_matches(text, args.query,
                                                           case_sensitive=args.case_sensitive, context=args.context_lines):
                # Source IDs are canonical UUID strings. Size the first match
                # before writing a source that the bounded result cannot return.
                candidate = {**match_record(path, line, value, before, after),
                    "source_id": source_id or "00000000-0000-0000-0000-000000000000"}
                candidate_refs = refs if source_id else [*refs, candidate["source_id"]]
                if len(matches) >= args.max_results or not _preview_fits({"matches": [*matches, candidate], "truncated": False}, candidate_refs):
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

    async def pr_status(args, execution):
        """Where a pull request stands: state, mergeability, reviews and every check with the reason a failed one gave."""
        from agentforge.integrations.github_client import GitHubError
        from agentforge.integrations.github_delivery import read_pull_status
        from agentforge.tools.errors import ToolError
        authorize = getattr(github, "authorize", None)
        if authorize is None:
            raise ToolError("GITHUB_UNAVAILABLE", "Pull request status needs the person's GitHub connection, which is not available here.")
        try:
            client, repo, installation = await authorize(selection["repo_id"])
            status = await read_pull_status(client, repo["full_name"], args.pr_number, installation=installation, repo_settings=None)
        except GitHubError as error:
            if str(error) == "github_access_denied":
                raise ToolError("PR_NOT_FOUND", f"Pull request #{args.pr_number} could not be read in {selection['repo_id']}.") from None
            raise ToolError("GITHUB_UNAVAILABLE", f"GitHub did not answer ({error}). Try again.") from None
        if execution.authorize_current:
            await execution.authorize_current()
        checks = status["checks"]
        failing = [item for item in checks["items"] if item["status"] == "failed"]
        return ToolOutcome(status="ok", cost_usd=Decimal(0), data={
            "number": status["number"], "url": status["url"], "title": status["title"], "state": status["state"],
            "head_sha": status["head_sha"], "mergeable": status["mergeable"], "behind_base": status["behind_base"],
            "checks": {"state": checks["state"], "passed": checks["passed"], "failed": checks["failed"], "pending": checks["pending"],
                       "failing": failing[:20], "note": ("Checks could not be read: the GitHub App needs the Checks: Read permission."
                                                         if checks["state"] == "unavailable" else None)},
            "reviews": status["reviews"]})

    for name, handler in (("read_repo_file", read), ("list_repo_files", listing), ("search_repo", search), ("get_issue", issue),
                          ("get_pr_status", pr_status)):
        catalog.register(name, handler, max_cost_usd=Decimal(0), timeout_sec=90)
