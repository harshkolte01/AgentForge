"""Publishing a conversation's changes to GitHub, and reading how they are doing (docs/delivery.md).

Everything is made of plain git objects through the REST API with the person's user token, so the sandbox never holds a
credential. Rules that keep it safe:

* A branch only ever moves **forward** (`force: false`), never rewritten.
* Before changing a file, the copy on the branch must be the one AgentForge last published (or the base commit's copy on
  the first push): if somebody else changed it on GitHub, the push is refused and the paths are named.
* Every commit carries an `AgentForge-Operation` trailer, so a retry after a lost response finds its own commit instead of
  making a second one. A pull request is found by its head branch before another is opened.
"""
import asyncio
import base64
import re
from datetime import datetime, timezone
from typing import NamedTuple
from urllib.parse import quote

from agentforge.integrations.github_app import SHA, gather_all
from agentforge.integrations.github_client import GitHubError
from agentforge.sandbox.workspace import validate_path

MAX_CHANGED_FILES = 500
MAX_CHANGED_BYTES = 20 * 1024 * 1024
BLOB_CONCURRENCY = 6
AUTHOR = {"name": "AgentForge", "email": "agentforge@users.noreply.github.com"}
OPERATION_TRAILER = "AgentForge-Operation"
MERGE_METHODS = ("squash", "merge", "rebase")
ZERO_SHA = "0" * 40
SUMMARY_CHARS = 600


class Change(NamedTuple):
    """One file of a push. `data` is None for a deletion. `old` is the blob the file had in the state last published
    (None if it did not exist there) and `new` the blob it has now (None if deleted)."""
    mode: str
    data: bytes | None
    old: str | None
    new: str | None


class Published(NamedTuple):
    commit_sha: str
    branch: str
    created_branch: bool
    already: bool      # the commit was made by an earlier attempt of the same operation


def _sha(value) -> str:
    sha = value.get("sha") if isinstance(value, dict) else None
    if not isinstance(sha, str) or not SHA.fullmatch(sha):
        raise GitHubError("github_response_invalid")
    return sha


def branch_ref(branch: str) -> str:
    return quote(branch, safe="/")


def trailers(operation_key: str, coauthor: tuple[str, int] | None) -> str:
    lines = [f"{OPERATION_TRAILER}: {operation_key}"]
    if coauthor:
        login, user_id = coauthor
        lines.append(f"Co-authored-by: {login} <{user_id}+{login}@users.noreply.github.com>")
    return "\n".join(lines)


def check_changes(changes: dict[str, Change]) -> None:
    if not changes:
        raise GitHubError("github_nothing_to_commit")
    if len(changes) > MAX_CHANGED_FILES or sum(len(c.data) for c in changes.values() if c.data is not None) > MAX_CHANGED_BYTES:
        raise GitHubError("github_change_too_large")
    for path, change in changes.items():
        try:
            validate_path(path)
        except ValueError:
            raise GitHubError("github_unsafe_change_path") from None
        if path.split("/")[0].casefold() == ".agentforge" or (change.data is not None and change.mode not in {"100644", "100755"}):
            raise GitHubError("github_unsupported_change")


async def coauthor_of(client) -> tuple[str, int] | None:
    """The signed-in GitHub user, for the commit's Co-authored-by trailer. Best effort: a commit without it is still correct."""
    try:
        user = await client.request("GET", "/user")
        login, user_id = user.get("login"), user.get("id")
        if isinstance(login, str) and re.fullmatch(r"[A-Za-z0-9-]{1,39}", login) and type(user_id) is int:
            return login, user_id
    except GitHubError:
        pass
    return None


async def publish_commit(client, name: str, *, branch: str, base_sha: str, changes: dict[str, Change], message: str,
                         operation_key: str, branch_known: bool, coauthor: tuple[str, int] | None = None) -> Published:
    """Commits `changes` on top of `branch` (created at `base_sha` on the first push). See the module notes for the rules.

    `branch_known` says AgentForge already pushed to the branch, so finding it is expected; finding a branch it never
    pushed to is only accepted if it holds this very operation's commit (a first push whose answer was lost).
    """
    check_changes(changes)
    if not SHA.fullmatch(base_sha):
        raise GitHubError("github_invalid_base")
    head = await client.request("GET", f"/repos/{name}/git/ref/heads/{branch_ref(branch)}", allow_missing=True)
    if head is None:
        if branch_known:
            # Starting the branch over from the base would leave out everything already pushed to it.
            raise GitHubError("github_branch_missing")
        parent, created = base_sha, True
    else:
        parent, created = _sha(head.get("object")), False
    parent_commit = await client.request("GET", f"/repos/{name}/git/commits/{parent}")
    if not created:
        if f"{OPERATION_TRAILER}: {operation_key}" in str(parent_commit.get("message") or ""):
            return Published(parent, branch, False, True)
        if not branch_known:
            raise GitHubError("github_publish_branch_exists")
    base_tree = _sha(parent_commit.get("tree"))
    if not created:
        # Files somebody else changed on the branch since AgentForge's last push are never overwritten.
        tree = await client.request("GET", f"/repos/{name}/git/trees/{base_tree}?recursive=1")
        if tree.get("truncated") or not isinstance(tree.get("tree"), list):
            raise GitHubError("github_change_too_large")
        remote = {entry.get("path"): entry.get("sha") for entry in tree["tree"] if entry.get("type") == "blob"}
        # A file that already holds exactly the new content is not a conflict (a push that landed but was not recorded).
        conflicts = sorted(path for path, change in changes.items() if remote.get(path) not in (change.old, change.new))
        if conflicts:
            raise GitHubError("github_path_conflict:" + ",".join(conflicts[:20]))
    gate = asyncio.Semaphore(BLOB_CONCURRENCY)

    async def blob(path: str, change: Change) -> dict:
        if change.data is None:
            return {"path": path, "mode": "100644", "type": "blob", "sha": None}
        async with gate:
            made = await client.request("POST", f"/repos/{name}/git/blobs",
                payload={"content": base64.b64encode(change.data).decode(), "encoding": "base64"})
        return {"path": path, "mode": change.mode, "type": "blob", "sha": _sha(made)}

    entries = await gather_all(blob(path, change) for path, change in changes.items())
    tree_sha = _sha(await client.request("POST", f"/repos/{name}/git/trees", payload={"base_tree": base_tree, "tree": entries}))
    stamp = {**AUTHOR, "date": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    body = f"{message.strip()}\n\n{trailers(operation_key, coauthor)}"
    made = _sha(await client.request("POST", f"/repos/{name}/git/commits",
        payload={"message": body, "tree": tree_sha, "parents": [parent], "author": stamp, "committer": stamp}))
    if created:
        await client.request("POST", f"/repos/{name}/git/refs", payload={"ref": f"refs/heads/{branch}", "sha": made})
    else:
        await client.request("PATCH", f"/repos/{name}/git/refs/heads/{branch_ref(branch)}", payload={"sha": made, "force": False})
    return Published(made, branch, created, False)


def pull_url(name: str, number: int) -> str:
    return f"https://github.com/{name}/pull/{number}"


def _pull(value, name: str) -> dict:
    number, url = value.get("number"), value.get("html_url")
    if type(number) is not int or url != pull_url(name, number) or not isinstance(value.get("node_id"), str):
        raise GitHubError("github_pull_request_confirmation_failed")
    return {"number": number, "url": url, "node_id": value["node_id"], "draft": bool(value.get("draft")),
            "state": value.get("state"), "merged": bool(value.get("merged"))}


async def find_pull_request(client, name: str, owner: str, branch: str) -> dict | None:
    """The open pull request whose head is `branch`, if any (this is what makes opening one retry-safe)."""
    found = await client.request("GET", f"/repos/{name}/pulls?state=open&head={quote(owner + ':' + branch, safe='')}&per_page=5")
    if not isinstance(found, list):
        raise GitHubError("github_response_invalid")
    for item in found:
        if isinstance(item, dict) and (item.get("head") or {}).get("ref") == branch:
            return _pull(item, name)
    return None


async def open_pull_request(client, name: str, *, branch: str, base_branch: str, title: str, body: str, draft: bool,
                            footer: str = "") -> dict:
    owner = name.split("/", 1)[0]
    existing = await find_pull_request(client, name, owner, branch)
    if existing is not None:
        return existing
    text = (body.strip() + "\n\n" if body.strip() else "") + footer
    made = await client.request("POST", f"/repos/{name}/pulls", payload={
        "title": title, "body": text.strip(), "head": branch, "base": base_branch, "draft": draft, "maintainer_can_modify": False})
    return _pull(made, name)


async def mark_ready(client, node_id: str) -> None:
    result = await client.request("POST", "/graphql", payload={
        "query": "mutation($id: ID!) { markPullRequestReadyForReview(input: {pullRequestId: $id}) { pullRequest { isDraft } } }",
        "variables": {"id": node_id}})
    if not isinstance(result, dict) or result.get("errors"):
        raise GitHubError("github_write_conflict")


async def merge_pull_request(client, name: str, number: int, *, method: str, expected_head: str, title: str | None = None) -> str:
    if method not in MERGE_METHODS or not SHA.fullmatch(expected_head):
        raise GitHubError("github_invalid_merge")
    payload = {"merge_method": method, "sha": expected_head}
    if title and method != "rebase":
        payload["commit_title"] = title[:250]
    done = await client.request("PUT", f"/repos/{name}/pulls/{number}/merge", payload=payload)
    if not isinstance(done, dict) or done.get("merged") is not True:
        raise GitHubError("github_not_mergeable")
    return str(done.get("sha") or "")


async def close_pull_request(client, name: str, number: int) -> None:
    await client.request("PATCH", f"/repos/{name}/pulls/{number}", payload={"state": "closed"})


async def update_pull_branch(client, name: str, number: int, expected_head: str) -> None:
    await client.request("PUT", f"/repos/{name}/pulls/{number}/update-branch", payload={"expected_head_sha": expected_head})


# --- reading status ---------------------------------------------------------------------------------------------------

_FAILED = {"failure", "timed_out", "action_required", "startup_failure", "error"}
_PASSED = {"success"}


def classify_run(run: dict) -> str:
    """pending | passed | failed | neutral, for one check run."""
    if run.get("status") != "completed":
        return "pending"
    conclusion = run.get("conclusion")
    return "passed" if conclusion in _PASSED else "failed" if conclusion in _FAILED else "neutral"


def classify_status(state: str | None) -> str:
    return {"success": "passed", "pending": "pending", "failure": "failed", "error": "failed"}.get(state or "", "neutral")


def collapse_checks(runs: list[dict], statuses: list[dict]) -> list[dict]:
    """The latest run of each check (re-runs replace earlier ones) and each commit status, as rows for the panel."""
    latest: dict[tuple, dict] = {}
    for run in runs:
        if not isinstance(run, dict) or not isinstance(run.get("name"), str):
            continue
        key = (run["name"], (run.get("app") or {}).get("id"))
        if key not in latest or (run.get("id") or 0) > (latest[key].get("id") or 0):
            latest[key] = run
    items = []
    for run in latest.values():
        status = classify_run(run)
        item = {"name": run["name"][:200], "status": status, "url": run.get("html_url") if isinstance(run.get("html_url"), str) else None,
                "workflow": ((run.get("app") or {}).get("name") or None)}
        if status == "failed":
            output = run.get("output") or {}
            text = " — ".join(part for part in (output.get("title"), output.get("summary")) if isinstance(part, str) and part)
            if text:
                item["summary"] = text[:SUMMARY_CHARS]
        items.append(item)
    seen = {item["name"] for item in items}
    for status in statuses:
        context = status.get("context") if isinstance(status, dict) else None
        if isinstance(context, str) and context not in seen:
            items.append({"name": context[:200], "status": classify_status(status.get("state")),
                          "url": status.get("target_url") if isinstance(status.get("target_url"), str) else None, "workflow": None})
    order = {"failed": 0, "pending": 1, "passed": 2, "neutral": 3}
    return sorted(items, key=lambda item: (order[item["status"]], item["name"].casefold()))


def summarize_checks(items: list[dict], *, readable: bool) -> dict:
    counts = {status: sum(1 for item in items if item["status"] == status) for status in ("passed", "failed", "pending")}
    if not readable:
        state = "unavailable"
    elif not items:
        state = "none"
    else:
        state = "failing" if counts["failed"] else "pending" if counts["pending"] else "passing"
    return {"state": state, **counts, "items": items[:100]}


def review_decision(reviews: list[dict]) -> dict:
    """Each reviewer's latest verdict, and what the lot adds up to."""
    latest: dict[str, str] = {}
    for review in sorted((r for r in reviews if isinstance(r, dict)), key=lambda r: r.get("id") or 0):
        login = (review.get("user") or {}).get("login")
        state = str(review.get("state") or "").lower()
        if isinstance(login, str) and state in {"approved", "changes_requested", "commented", "dismissed", "pending"}:
            # A bare comment never replaces an earlier verdict.
            if state != "commented" or login not in latest:
                latest[login] = state
    items = [{"login": login, "state": state} for login, state in sorted(latest.items())]
    states = set(latest.values())
    decision = "changes_requested" if "changes_requested" in states else "approved" if "approved" in states else "none"
    return {"decision": decision, "items": items[:50]}


def mergeable_label(pull: dict) -> str:
    state = pull.get("mergeable_state")
    if pull.get("mergeable") is False or state == "dirty":
        return "conflicting"
    if pull.get("mergeable") is True:
        return "mergeable"
    return "unknown"


def permission_readable(installation: dict, *names: str) -> bool:
    permissions = installation.get("permissions") or {}
    return all(permissions.get(name) in {"read", "write"} for name in names)


async def read_pull_status(client, name: str, number: int, *, installation: dict, repo_settings: dict | None) -> dict:
    """The pull request as GitHub describes it now, normalised for the panel. Raises GitHubError if the pull request itself cannot be read;
    a missing Checks permission only makes `checks.state` 'unavailable'."""
    pull = await client.request("GET", f"/repos/{name}/pulls/{number}")
    if not isinstance(pull, dict) or pull.get("number") != number:
        raise GitHubError("github_response_invalid")
    head = _sha(pull.get("head"))
    base = (pull.get("base") or {}).get("ref")
    readable = permission_readable(installation, "checks")

    async def reviews():
        return await client.pages(f"/repos/{name}/pulls/{number}/reviews", None)

    async def runs():
        if not readable:
            return [], []
        try:
            found = await client.pages(f"/repos/{name}/commits/{head}/check-runs", "check_runs")
        except GitHubError as error:
            if str(error) == "github_access_denied":
                return None, None
            raise
        statuses = []
        if permission_readable(installation, "statuses"):
            # Commit statuses are an older channel some CI systems still use; their absence never hides the check runs.
            try:
                state = await client.request("GET", f"/repos/{name}/commits/{head}/status")
                statuses = state.get("statuses") if isinstance(state, dict) and isinstance(state.get("statuses"), list) else []
            except GitHubError as error:
                if str(error) != "github_access_denied":
                    raise
        return found, statuses

    async def behind():
        if pull.get("state") != "open" or not isinstance(base, str):
            return None
        try:
            compare = await client.request("GET", f"/repos/{name}/compare/{quote(base, safe='')}...{head}")
        except GitHubError:
            return None
        value = compare.get("behind_by") if isinstance(compare, dict) else None
        return value if type(value) is int and value >= 0 else None

    async def settings():
        if repo_settings is not None:
            return repo_settings
        repo = await client.request("GET", f"/repos/{name}")
        return {"allow_squash_merge": repo.get("allow_squash_merge", True), "allow_merge_commit": repo.get("allow_merge_commit", True),
                "allow_rebase_merge": repo.get("allow_rebase_merge", True)}

    reviewed, (found, statuses), behind_by, merge_settings = await gather_all([reviews(), runs(), behind(), settings()])
    if found is None:
        readable, found, statuses = False, [], []
    methods = [m for m, key in (("squash", "allow_squash_merge"), ("merge", "allow_merge_commit"), ("rebase", "allow_rebase_merge"))
               if merge_settings.get(key, True)]
    merged = bool(pull.get("merged"))
    state = "merged" if merged else "closed" if pull.get("state") == "closed" else "draft" if pull.get("draft") else "open"
    return {
        "number": number, "url": pull_url(name, number), "node_id": pull.get("node_id"), "title": str(pull.get("title") or "")[:300],
        "state": state, "head_sha": head, "head_ref": (pull.get("head") or {}).get("ref"), "base_ref": base,
        "mergeable": mergeable_label(pull), "mergeable_state": pull.get("mergeable_state"), "behind_base": behind_by,
        "checks": summarize_checks(collapse_checks(found, statuses), readable=readable),
        "reviews": review_decision(reviewed), "merged_at": pull.get("merged_at") if merged else None,
        "merge_methods": methods, "repo_settings": merge_settings,
    }
