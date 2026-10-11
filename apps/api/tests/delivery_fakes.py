"""Test doubles for the delivery tests: a stateful GitHub (git data and pull request APIs) and a real-git local workspace.

`FakeGitHub` keeps real content-addressed objects (blob shas are git's), so the per-path conflict guard compares what it
would compare on GitHub. `LocalWorkspace` runs the delivery module's actual shell scripts with real git in a temp folder, so
those scripts are tested, not mocked.
"""
import asyncio
import base64
import hashlib
import json
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace as NS
from urllib.parse import unquote

from agentforge.integrations.github_client import GitHubError

OWNER_REPO = "owner/repo"


def blob_sha(data: bytes) -> str:
    return hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()


def digest(*parts) -> str:
    return hashlib.sha1(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()


class FakeGitHub:
    """One repository on GitHub, answering the requests the delivery module makes. Also stands in for the authorized client."""

    def __init__(self, files: dict[str, bytes], *, permissions=None, name=OWNER_REPO):
        self.name = name
        self.blobs, self.trees, self.commits, self.refs, self.pulls = {}, {}, {}, {}, {}
        self.calls, self.fail = [], {}
        self.reviews, self.check_runs, self.statuses, self.behind_by = [], [], [], 0
        self.permissions = permissions or {"contents": "write", "pull_requests": "write", "checks": "read", "statuses": "read", "metadata": "read"}
        self.settings = {"allow_squash_merge": True, "allow_merge_commit": True, "allow_rebase_merge": False}
        tree = {}
        for path, data in files.items():
            sha = blob_sha(data)
            self.blobs[sha] = data
            tree[path] = ("100644", sha)
        tree_sha = self._tree(tree)
        self.base = self._commit("base", tree_sha, [])
        self.refs["main"] = self.base

    # --- object store -------------------------------------------------------------------------------------------------

    def _tree(self, entries: dict) -> str:
        sha = digest("tree", sorted(entries.items()))
        self.trees[sha] = dict(entries)
        return sha

    def _commit(self, message, tree, parents) -> str:
        sha = digest("commit", message, tree, parents, len(self.commits))
        self.commits[sha] = {"message": message, "tree": tree, "parents": list(parents)}
        return sha

    def ancestors(self, sha) -> set:
        seen, todo = set(), [sha]
        while todo:
            current = todo.pop()
            if current not in seen:
                seen.add(current)
                todo.extend(self.commits[current]["parents"])
        return seen

    def files_at(self, sha) -> dict[str, bytes]:
        return {path: self.blobs[blob] for path, (_, blob) in self.trees[self.commits[sha]["tree"]].items()}

    def push_other_commit(self, branch, files: dict[str, bytes | None], message="someone else") -> str:
        """A change made on GitHub by somebody else, directly on `branch`."""
        parent = self.refs[branch]
        tree = dict(self.trees[self.commits[parent]["tree"]])
        for path, data in files.items():
            if data is None:
                tree.pop(path, None)
            else:
                sha = blob_sha(data)
                self.blobs[sha] = data
                tree[path] = ("100644", sha)
        self.refs[branch] = self._commit(message, self._tree(tree), [parent])
        return self.refs[branch]

    # --- the client interface -----------------------------------------------------------------------------------------

    async def authorize(self, repo_id, **kwargs):
        self.calls.append(("AUTHORIZE", repo_id, kwargs))
        if kwargs.get("write") and (self.permissions.get("contents") != "write" or self.permissions.get("pull_requests") != "write"):
            raise GitHubError("github_write_permission_required")
        return self, {"full_name": self.name, "default_branch": "main", "id": 1}, {"id": 7, "permissions": self.permissions}

    async def pages(self, path, key, **kwargs):
        value = await self.request("GET", path)
        return value if key is None else value[key]

    async def request(self, method, path, payload=None, allow_missing=False, http=None):
        self.calls.append((method, path, payload))
        for needle, error in self.fail.items():
            if needle in f"{method} {path}":
                raise GitHubError(error)
        result = self._route(method, path, payload)
        if result is None and not allow_missing:
            raise GitHubError("github_access_denied")
        return result

    def _route(self, method, path, payload):
        base = f"/repos/{self.name}"
        if path == "/user":
            return {"login": "alice", "id": 42}
        if path == "/graphql":
            number = next(iter(self.pulls))
            self.pulls[number]["draft"] = False
            return {"data": {"markPullRequestReadyForReview": {"pullRequest": {"isDraft": False}}}}
        if not path.startswith(base):
            raise AssertionError(f"unexpected {method} {path}")
        rest = path[len(base):]
        if method == "GET" and (match := re.fullmatch(r"/git/ref/heads/(.+)", rest)):
            branch = unquote(match.group(1))
            return {"object": {"sha": self.refs[branch]}} if branch in self.refs else None
        if method == "GET" and (match := re.fullmatch(r"/git/commits/([0-9a-f]{40})", rest)):
            commit = self.commits[match.group(1)]
            return {"sha": match.group(1), "message": commit["message"], "tree": {"sha": commit["tree"]}}
        if method == "GET" and (match := re.fullmatch(r"/git/trees/([0-9a-f]{40})\?recursive=1", rest)):
            return {"truncated": False, "tree": [{"path": p, "mode": m, "type": "blob", "sha": s} for p, (m, s) in self.trees[match.group(1)].items()]}
        if method == "POST" and rest == "/git/blobs":
            data = base64.b64decode(payload["content"])
            self.blobs[blob_sha(data)] = data
            return {"sha": blob_sha(data)}
        if method == "POST" and rest == "/git/trees":
            tree = dict(self.trees[payload["base_tree"]])
            for entry in payload["tree"]:
                if entry["sha"] is None:
                    tree.pop(entry["path"], None)
                else:
                    tree[entry["path"]] = (entry["mode"], entry["sha"])
            return {"sha": self._tree(tree)}
        if method == "POST" and rest == "/git/commits":
            return {"sha": self._commit(payload["message"], payload["tree"], payload["parents"])}
        if method == "POST" and rest == "/git/refs":
            branch = payload["ref"].removeprefix("refs/heads/")
            if branch in self.refs:
                raise GitHubError("github_write_conflict")
            self.refs[branch] = payload["sha"]
            return {}
        if method == "PATCH" and (match := re.fullmatch(r"/git/refs/heads/(.+)", rest)):
            branch = unquote(match.group(1))
            if payload.get("force") is not False and payload.get("force") is not None:
                raise AssertionError("a push must never force")
            if self.refs[branch] not in self.ancestors(payload["sha"]):
                raise GitHubError("github_write_conflict")
            self.refs[branch] = payload["sha"]
            return {}
        if method == "GET" and rest.startswith("/pulls?"):
            head = re.search(r"head=([^&]+)", rest)
            wanted = unquote(head.group(1)).split(":", 1)[1] if head else None
            return [self._pull_json(p) for p in self.pulls.values() if p["state"] == "open" and p["head"] == wanted]
        if method == "POST" and rest == "/pulls":
            number = len(self.pulls) + 1
            self.pulls[number] = {"number": number, "title": payload["title"], "body": payload["body"], "head": payload["head"],
                                  "base": payload["base"], "draft": payload["draft"], "state": "open", "merged": False}
            return self._pull_json(self.pulls[number])
        if (match := re.fullmatch(r"/pulls/(\d+)", rest)):
            pull = self.pulls[int(match.group(1))]
            if method == "PATCH":
                pull["state"] = payload["state"]
            return self._pull_json(pull)
        if method == "GET" and (match := re.fullmatch(r"/pulls/(\d+)/reviews", rest)):
            return self.reviews
        if method == "PUT" and (match := re.fullmatch(r"/pulls/(\d+)/merge", rest)):
            pull = self.pulls[int(match.group(1))]
            if payload["sha"] != self.refs[pull["head"]]:
                raise GitHubError("github_write_conflict")
            if pull["draft"]:
                raise GitHubError("github_not_mergeable")
            pull["merged"], pull["state"] = True, "closed"
            return {"merged": True, "sha": "9" * 40}
        if method == "PUT" and re.fullmatch(r"/pulls/\d+/update-branch", rest):
            return {"message": "Updating pull request branch."}
        if method == "GET" and (match := re.fullmatch(r"/commits/([0-9a-f]{40})/check-runs(\?.*)?", rest)):
            return {"total_count": len(self.check_runs), "check_runs": self.check_runs}
        if method == "GET" and re.fullmatch(r"/commits/[0-9a-f]{40}/status", rest):
            return {"state": "pending", "statuses": self.statuses}
        if method == "GET" and rest.startswith("/compare/"):
            return {"behind_by": self.behind_by}
        if method == "GET" and rest == "":
            return self.settings
        raise AssertionError(f"unexpected {method} {path}")

    def _pull_json(self, pull) -> dict:
        head = self.refs.get(pull["head"], "0" * 40)
        return {"number": pull["number"], "html_url": f"https://github.com/{self.name}/pull/{pull['number']}", "node_id": f"PR_{pull['number']}",
                "title": pull["title"], "state": pull["state"], "draft": pull["draft"], "merged": pull["merged"],
                "merged_at": "2026-10-11T10:00:00Z" if pull["merged"] else None, "mergeable": True, "mergeable_state": "clean",
                "head": {"ref": pull["head"], "sha": head}, "base": {"ref": pull["base"]}}


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True,
                          env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
                               "HOME": str(root), "PATH": "/usr/bin:/bin:/opt/homebrew/bin"}).stdout


class LocalWorkspace:
    """The subset of the sandbox Workspace the delivery code uses, backed by a real git checkout in a temp folder."""

    def __init__(self, root: Path, files: dict[str, bytes], sha: str, repo_id=OWNER_REPO):
        self.root = root
        self.recreated = False
        root.mkdir(parents=True, exist_ok=True)
        for path, data in files.items():
            (root / path).parent.mkdir(parents=True, exist_ok=True)
            (root / path).write_bytes(data)
        (root / ".agentforge").mkdir(exist_ok=True)
        (root / ".agentforge" / "repo").write_text(f"{repo_id}@{sha}")
        git(root, "init", "-q", "-b", "main")
        git(root, "config", "user.email", "agentforge@users.noreply.github.com")
        git(root, "config", "user.name", "AgentForge")
        (root / ".git" / "info" / "exclude").write_text(".agentforge/\n")
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "base", "--allow-empty")
        git(root, "tag", "-f", "agentforge-base")
        self.sandbox_id = "sandbox-1"

    def path(self, name: str) -> Path:
        return Path(name) if name.startswith("/") else self.root / name

    async def capture(self, command: str, *, timeout_sec: float = 120):
        process = await asyncio.create_subprocess_exec("bash", "-c", command, cwd=self.root, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, env={"HOME": str(self.root), "PATH": "/usr/bin:/bin:/opt/homebrew/bin"})
        out, _ = await process.communicate()
        return process.returncode, out.decode()

    async def read_bytes(self, path: str) -> bytes:
        return self.path(path).read_bytes()

    async def write(self, path: str, data) -> None:
        target = self.path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data if isinstance(data, bytes) else data.encode())

    def put(self, path: str, data: bytes | str) -> None:
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data if isinstance(data, bytes) else data.encode())

    def remove(self, path: str) -> None:
        (self.root / path).unlink()


class FakeProvider:
    def __init__(self, workspace=None):
        self.workspace, self.killed = workspace, 0

    async def attach(self, existing_id):
        return self.workspace if existing_id else None

    async def kill(self, workspace):
        self.killed += 1


def settings(**overrides):
    values = dict(enable_github_writes=True, auth_mode="github_app", app_base_url="http://localhost:3000", e2b_api_key=None)
    values.update(overrides)
    return NS(**values)
