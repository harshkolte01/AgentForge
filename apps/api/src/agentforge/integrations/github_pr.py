"""Opens a draft pull request from file changes, with the user token that stays on the backend.

The agent's edits live in the sandbox; the sandbox has no credentials. This turns the changed files into git objects
(blobs, a tree, a commit), points a new `agentforge/...` branch at the commit and opens a draft pull request against the
repository's default branch. Git objects are content addressed and unreachable until the branch exists, so a failure
before the branch leaves nothing visible on GitHub.
"""
import base64
from datetime import datetime, timezone
from urllib.parse import quote

from agentforge.integrations.github_app import SHA
from agentforge.integrations.github_client import GitHubError
from agentforge.sandbox.workspace import validate_path

MAX_CHANGED_FILES = 500
MAX_CHANGED_BYTES = 20 * 1024 * 1024
AUTHOR = {"name": "AgentForge", "email": "agentforge@users.noreply.github.com"}


def _sha(value) -> str:
    sha = value.get("sha") if isinstance(value, dict) else None
    if not isinstance(sha, str) or not SHA.fullmatch(sha):
        raise GitHubError("github_response_invalid")
    return sha


async def open_draft_pull_request(adapter, repo_id: str, *, base_sha: str, changes: dict[str, tuple[str, bytes] | None],
                                  title: str, body: str, branch: str, base_ref: str | None = None) -> dict:
    """`changes` maps a path to (git mode, content), or to None for a deleted file. Returns the pull request's details."""
    if not changes:
        raise GitHubError("github_nothing_to_commit")
    if len(changes) > MAX_CHANGED_FILES or sum(len(item[1]) for item in changes.values() if item) > MAX_CHANGED_BYTES:
        raise GitHubError("github_change_too_large")
    for path, change in changes.items():
        try:
            validate_path(path)
        except ValueError:
            raise GitHubError("github_unsafe_change_path") from None
        if path.split('/')[0].casefold() == '.agentforge' or (change is not None and change[0] not in {"100644", "100755"}):
            raise GitHubError("github_unsupported_change")
    if not SHA.fullmatch(base_sha):
        raise GitHubError("github_invalid_base")
    client, repo, _ = await adapter.authorize(repo_id, write=True)
    name = repo["full_name"]
    base_branch = repo["default_branch"] if not base_ref or base_ref == "HEAD" or SHA.fullmatch(base_ref) else base_ref.removeprefix("refs/heads/")
    base = await client.request("GET", f"/repos/{name}/branches/{quote(base_branch, safe='')}")
    if _sha(base.get("commit")) != base_sha:
        raise GitHubError("github_base_branch_changed")
    if await client.request("GET", f"/repos/{name}/git/ref/heads/{quote(branch, safe='/')}", allow_missing=True) is not None:
        raise GitHubError("github_publish_branch_exists")
    commit = await client.request("GET", f"/repos/{name}/git/commits/{base_sha}")
    base_tree = _sha(commit.get("tree"))
    entries = []
    for path, change in changes.items():
        if change is None:
            entries.append({"path": path, "mode": "100644", "type": "blob", "sha": None})
            continue
        mode, data = change
        blob = await client.request("POST", f"/repos/{name}/git/blobs",
            payload={"content": base64.b64encode(data).decode(), "encoding": "base64"})
        entries.append({"path": path, "mode": mode, "type": "blob", "sha": _sha(blob)})
    tree = _sha(await client.request("POST", f"/repos/{name}/git/trees", payload={"base_tree": base_tree, "tree": entries}))
    stamp = {**AUTHOR, "date": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    made = _sha(await client.request("POST", f"/repos/{name}/git/commits",
        payload={"message": title, "tree": tree, "parents": [base_sha], "author": stamp, "committer": stamp}))
    await client.request("POST", f"/repos/{name}/git/refs", payload={"ref": f"refs/heads/{branch}", "sha": made})
    pull = await client.request("POST", f"/repos/{name}/pulls", payload={
        "title": title, "body": (body + "\n\n" if body else "") + "Opened by AgentForge as a draft.",
        "head": branch, "base": base_branch, "draft": True, "maintainer_can_modify": False})
    number, url = pull.get("number"), pull.get("html_url")
    if type(number) is not int or url != f"https://github.com/{name}/pull/{number}":
        raise GitHubError("github_draft_pr_confirmation_failed")
    return {"repo_id": name, "branch": branch, "base_branch": base_branch, "commit_sha": made, "pr_number": number, "pr_url": url,
            "branch_url": f"https://github.com/{name}/tree/{quote(branch, safe='/')}"}
