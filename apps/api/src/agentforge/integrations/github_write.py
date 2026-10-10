"""Exact approved diffs to create-only task branches and verified draft PRs.

Git objects are content addressed; only creating the branch and PR changes
visible repository state. Those effects are reconciled before any uncertain retry.
"""
import asyncio
import base64
import hashlib
from urllib.parse import quote
from agentforge.integrations.github_app import AuthorizedGitHubClient, SHA
from agentforge.integrations.github_client import GitHubError, _deadline
from agentforge.repositories.approvals import ApprovalError

def blob_sha(data):
    return hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()

def checked_sha(value):
    sha = value.get("sha") if isinstance(value, dict) else None
    if not isinstance(sha, str) or not SHA.fullmatch(sha):
        raise GitHubError("github_response_invalid")
    return sha

class GitHubWriteService:
    def __init__(self, repository, session_factory, settings, *, transport=None):
        self.repository, self.session_factory, self.settings, self.transport = repository, session_factory, settings, transport

    async def _effect(self, approval, owner_id, client, kind, path, payload, *, reconcile=None, content_addressed=False):
        operation = self.repository.operation(approval["id"], owner_id, kind, payload)
        if operation["status"] == "CONFIRMED":
            return operation["response"]
        if operation["status"] in {"SENT", "UNKNOWN"}:
            if reconcile:
                remote = await reconcile()
                if remote is not None:
                    self.repository.operation_state(operation["id"], "CONFIRMED", remote)
                    return remote
            if not content_addressed:
                # An absent result after an ambiguous network outcome is not
                # proof of rejection: replication/PR indexes may lag.
                raise GitHubError("github_write_outcome_unknown")
        self.repository.require_unexpired(approval["id"], owner_id)
        self.repository.operation_state(operation["id"], "SENT")
        try:
            response = await client.request("POST", path, payload=payload)
            self.repository.operation_state(operation["id"], "CONFIRMED", response)
            return response
        except BaseException:
            self.repository.operation_state(operation["id"], "UNKNOWN")
            raise

    @_deadline(240)
    async def execute(self, approval_id, owner_id):
        approval = self.repository.begin(approval_id, owner_id)
        if approval["state"] == "CONSUMED":
            return approval
        action = approval["action"]
        try:
            adapter = AuthorizedGitHubClient(self.session_factory, self.settings, owner_id, transport=self.transport)
            client, repo, install = await adapter.authorize(action["repo_id"], write=True, repository_id=action["repository_id"])
            if install["id"] != action["installation_id"] or repo["default_branch"] != action["base_branch"]:
                raise ApprovalError("github_repository_scope_changed")
            name, base_sha = action["repo_id"], action["base_sha"]
            if action["head_branch"] != f"agentforge/task-{approval['task_id'].replace('-', '')}" or action["head_branch"] == repo["default_branch"]:
                raise ApprovalError("github_unsafe_branch")
            base, current = self.repository.workspace(approval_id, owner_id)
            remote_base = await client.request("GET", f"/repos/{name}/git/ref/heads/{quote(action['base_branch'], safe='')}")
            if remote_base.get("object", {}).get("sha") != base_sha:
                raise ApprovalError("github_base_branch_moved")
            commit = await client.request("GET", f"/repos/{name}/git/commits/{base_sha}")
            tree_sha = checked_sha(commit.get("tree"))
            tree = await client.request("GET", f"/repos/{name}/git/trees/{tree_sha}?recursive=1")
            if tree.get("truncated") or not isinstance(tree.get("tree"), list) or len(tree["tree"]) > 100000:
                raise GitHubError("github_base_tree_incomplete")
            entries = {entry["path"]: entry for entry in tree["tree"]}
            changes = []
            for index, path in enumerate(action["changed_paths"]):
                original = entries.get(path)
                if path in base:
                    if not original or original.get("type") != "blob" or original.get("mode") not in {"100644", "100755"} or original.get("sha") != blob_sha(base[path]):
                        raise ApprovalError("github_baseline_tree_mismatch")
                elif original is not None:
                    raise ApprovalError("github_new_path_already_exists")
                mode = original["mode"] if original else "100644"
                if path not in current:
                    changes.append(dict(path=path, mode=mode, type="blob", sha=None))
                    continue
                data = current[path]
                expected = blob_sha(data)
                blob = await self._effect(approval, owner_id, client, f"blob:{index}", f"/repos/{name}/git/blobs",
                    {"content": base64.b64encode(data).decode(), "encoding": "base64"}, content_addressed=True)
                if checked_sha(blob) != expected:
                    raise GitHubError("github_blob_integrity_failure")
                changes.append(dict(path=path, mode=mode, type="blob", sha=expected))
            created_tree = await self._effect(approval, owner_id, client, "tree", f"/repos/{name}/git/trees",
                {"base_tree": tree_sha, "tree": changes}, content_addressed=True)
            # Confirm every resulting path/mode/object, including all untouched
            # entries. Filtered sandbox maps cannot delete unreviewed remote data.
            created_tree_sha = checked_sha(created_tree)
            final_tree = await client.request("GET", f"/repos/{name}/git/trees/{created_tree_sha}?recursive=1")
            if final_tree.get("truncated") or not isinstance(final_tree.get("tree"), list):
                raise GitHubError("github_result_tree_incomplete")
            original_leaves = {p: (e["mode"], e["sha"], e["type"]) for p, e in entries.items() if e["type"] != "tree"}
            expected_leaves = dict(original_leaves)
            for item in changes:
                if item["sha"] is None:
                    expected_leaves.pop(item["path"], None)
                else:
                    expected_leaves[item["path"]] = (item["mode"], item["sha"], "blob")
            actual_leaves = {e["path"]: (e["mode"], e["sha"], e["type"]) for e in final_tree["tree"] if e["type"] != "tree"}
            if expected_leaves != actual_leaves:
                raise GitHubError("github_result_tree_mismatch")
            author = {"name": "AgentForge", "email": "agentforge@users.noreply.github.com", "date": action["commit_date"]}
            result_commit = await self._effect(approval, owner_id, client, "commit", f"/repos/{name}/git/commits",
                {"message": action["commit_message"], "tree": created_tree_sha, "parents": [base_sha],
                    "author": author, "committer": author}, content_addressed=True)
            sha = checked_sha(result_commit)
            branch_path = f"/repos/{name}/git/ref/heads/{quote(action['head_branch'], safe='')}"
            async def reconcile_branch():
                branch = await client.request("GET", branch_path, allow_missing=True)
                if branch is None:
                    return None
                if branch.get("object", {}).get("sha") != sha:
                    raise ApprovalError("github_task_branch_conflict")
                return branch
            branch = await reconcile_branch()
            # Existing branch must belong to this operation ledger; never claim a
            # user-created branch even if it happens to reference the same commit.
            branch_payload = {"ref": "refs/heads/" + action["head_branch"], "sha": sha}
            branch_operation = self.repository.operation(approval_id, owner_id, "branch", branch_payload)
            if branch is not None and branch_operation["status"] == "PREPARED":
                raise ApprovalError("github_task_branch_already_exists")
            # Recheck user+App permissions and pinned base immediately before the
            # first visible effect; git-object creation granted no branch writes.
            client, _, _ = await adapter.authorize(name, write=True, repository_id=action["repository_id"])
            fresh_base = await client.request("GET", f"/repos/{name}/git/ref/heads/{quote(action['base_branch'], safe='')}")
            if fresh_base.get("object", {}).get("sha") != base_sha:
                raise ApprovalError("github_base_branch_moved")
            await self._effect(approval, owner_id, client, "branch", f"/repos/{name}/git/refs", branch_payload, reconcile=reconcile_branch)
            owner = name.split("/", 1)[0]
            async def reconcile_pr():
                prs = await client.request("GET", f"/repos/{name}/pulls?state=all&head={quote(owner + ':' + action['head_branch'], safe='')}&base={quote(action['base_branch'], safe='')}&per_page=100")
                if not isinstance(prs, list) or len(prs) >= 100:
                    raise GitHubError("github_pr_reconciliation_invalid")
                if len(prs) > 1:
                    raise ApprovalError("github_duplicate_pr_conflict")
                return self._checked_pr(prs[0], action, sha) if prs else None
            body = action["body"] + f"\n\nAgentForge task: {approval['task_id']}\nApproved diff SHA-256: {action['diff_sha256']}"
            payload = {"title": action["title"], "body": body, "head": action["head_branch"], "base": action["base_branch"], "draft": True, "maintainer_can_modify": False}
            existing = await reconcile_pr()
            pr_operation = self.repository.operation(approval_id, owner_id, "draft_pr", payload)
            if existing is not None and pr_operation["status"] == "PREPARED":
                raise ApprovalError("github_existing_pr_not_owned")
            client, fresh_repo, fresh_install = await adapter.authorize(name, write=True, repository_id=action["repository_id"])
            if fresh_install["id"] != action["installation_id"] or fresh_repo["default_branch"] != action["base_branch"]:
                raise ApprovalError("github_repository_scope_changed")
            fresh_base = await client.request("GET", f"/repos/{name}/git/ref/heads/{quote(action['base_branch'], safe='')}")
            if fresh_base.get("object", {}).get("sha") != base_sha:
                raise ApprovalError("github_base_branch_moved")
            if await reconcile_branch() is None:
                raise ApprovalError("github_task_branch_missing")
            response = await self._effect(approval, owner_id, client, "draft_pr", f"/repos/{name}/pulls", payload, reconcile=reconcile_pr)
            confirmed = self._checked_pr(response, action, sha)
            return self.repository.complete(approval_id, owner_id, {"repo_id": name, "head_branch": action["head_branch"],
                "base_branch": action["base_branch"], "commit_sha": sha, "pr_number": confirmed["number"], "pr_url": confirmed["html_url"]})
        finally:
            self.repository.release(approval_id, owner_id)

    @staticmethod
    def _checked_pr(value, action, sha):
        if (not isinstance(value, dict) or value.get("draft") is not True or value.get("state") != "open"
                or value.get("head", {}).get("sha") != sha or value.get("head", {}).get("ref") != action["head_branch"]
                or value.get("base", {}).get("ref") != action["base_branch"]
                or value.get("base", {}).get("sha") != action["base_sha"]
                or value.get("head", {}).get("repo", {}).get("id") != action["repository_id"]
                or value.get("base", {}).get("repo", {}).get("id") != action["repository_id"]
                or type(value.get("number")) is not int or value["number"] <= 0
                or value.get("html_url") != f"https://github.com/{action['repo_id']}/pull/{value['number']}"):
            raise GitHubError("github_draft_pr_confirmation_failed")
        return value
