"""Bounded GitHub App user-token API. Never falls back to installation authority."""
from __future__ import annotations
import asyncio
import base64
import json
import re
from urllib.parse import quote
import httpx
from agentforge.integrations.github_client import (GitHubClient, GitHubError, RepositorySnapshot,
    ResolvedRepository, validate_repo_id, _deadline)

SHA = re.compile(r"[0-9a-f]{40}\Z")

class GitHubAppClient:
    def __init__(self, settings, *, token=None, transport=None):
        self.settings, self.token, self.transport = settings, token, transport

    async def request(self, method, path, *, payload=None, allow_missing=False):
        if not path.startswith("/") or path.startswith("//"):
            raise GitHubError("github_invalid_endpoint")
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": self.settings.github_api_version,
            "User-Agent": "AgentForge"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(20, connect=5), follow_redirects=False,
                    trust_env=False, transport=self.transport, headers=headers) as client:
                async with client.stream(method, "https://api.github.com" + path, json=payload) as response:
                    if allow_missing and response.status_code == 404:
                        return None
                    if response.status_code in {401, 403, 404}:
                        raise GitHubError("github_access_denied")
                    if response.status_code in {409, 422}:
                        raise GitHubError("github_write_conflict")
                    if response.status_code == 429:
                        raise GitHubError("github_rate_limited")
                    if response.status_code not in {200, 201, 204}:
                        raise GitHubError("github_unavailable")
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        data.extend(chunk)
                        if len(data) > 6_000_000:
                            raise GitHubError("github_response_size_limit")
                    return json.loads(data) if data else {}
        except (httpx.HTTPError, asyncio.TimeoutError):
            # A caller's durable ledger treats write failures as ambiguous.
            raise GitHubError("github_unavailable") from None
        except (json.JSONDecodeError, UnicodeError):
            raise GitHubError("github_response_invalid") from None

    async def oauth(self, payload):
        payload = {**payload, "client_id": self.settings.github_client_id,
            "client_secret": self.settings.github_client_secret.get_secret_value()}
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=False, trust_env=False,
                    transport=self.transport) as client:
                async with client.stream("POST", "https://github.com/login/oauth/access_token", data=payload,
                        headers={"Accept": "application/json", "User-Agent": "AgentForge"}) as response:
                    if response.status_code != 200:
                        raise GitHubError("github_authorization_failed")
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        data.extend(chunk)
                        if len(data) > 20_000:
                            raise GitHubError("github_authorization_failed")
                    value = json.loads(data)
                if not isinstance(value, dict) or not isinstance(value.get("access_token"), str) or value.get("error"):
                    raise GitHubError("github_authorization_failed")
                if value.get("token_type", "").lower() != "bearer" or not value["access_token"].startswith("ghu_"):
                    raise GitHubError("github_app_user_token_required")
                return value
        except (httpx.HTTPError, ValueError):
            raise GitHubError("github_authorization_failed") from None

    async def pages(self, path, key):
        result = []
        for page in range(1, 11):
            separator = "&" if "?" in path else "?"
            value = await self.request("GET", f"{path}{separator}per_page=100&page={page}")
            values = value if key is None else value.get(key)
            if not isinstance(values, list):
                raise GitHubError("github_response_invalid")
            result.extend(values)
            if len(values) < 100:
                return result
        raise GitHubError("github_repository_discovery_limit")

    @_deadline(45)
    async def installations(self):
        values = await self.pages("/user/installations", "installations")
        result = [value for value in values if value.get("app_id") == self.settings.github_app_id
            and not value.get("suspended_at")]
        if len(result) > 10:
            raise GitHubError("github_installation_discovery_limit")
        return result

    @_deadline(60)
    async def repositories(self):
        result = []
        for installation in await self.installations():
            for repository in await self.pages(f"/user/installations/{int(installation['id'])}/repositories", "repositories"):
                result.append((installation, repository))
                if len(result) > 1000:
                    raise GitHubError("github_repository_discovery_limit")
        return result

class AuthorizedGitHubClient:
    """Task adapter obtains fresh user credentials and current repo membership per call."""
    def __init__(self, session_factory, settings, user_id, *, transport=None):
        self.session_factory, self.settings, self.user_id, self.transport = session_factory, settings, user_id, transport

    @_deadline(60)
    async def authorize(self, repo_id, *, write=False, repository_id=None):
        from agentforge.auth.github_service import GitHubIdentityService
        repo_id = validate_repo_id(repo_id)
        identity = GitHubIdentityService(self.session_factory, self.settings, transport=self.transport)
        client = GitHubAppClient(self.settings, token=await identity.access_token(self.user_id), transport=self.transport)
        from sqlalchemy import select, func
        from agentforge.database.identity import GitHubRepositoryGrant
        with self.session_factory() as database:
            known = database.scalar(select(GitHubRepositoryGrant).where(GitHubRepositoryGrant.user_id == self.user_id,
                func.lower(GitHubRepositoryGrant.full_name) == repo_id.lower()))
            known_installation = known.installation_id if known else None
        if known_installation is not None:
            # Cached installation ID narrows discovery only; fresh membership,
            # suspension and repository rights still come from the user token.
            installs = [item for item in await client.installations() if item["id"] == known_installation]
            matches = []
            for installation in installs:
                repos = await client.pages(f"/user/installations/{known_installation}/repositories", "repositories")
                matches.extend((installation, item) for item in repos if item.get("full_name", "").lower() == repo_id.lower())
        else:
            matches = [(installation, repo) for installation, repo in await client.repositories()
                if repo.get("full_name", "").lower() == repo_id.lower()]
        if len(matches) != 1:
            raise GitHubError("github_repository_not_authorized")
        installation, repo = matches[0]
        if repository_id is not None and repo.get("id") != repository_id:
            raise GitHubError("github_repository_identity_changed")
        if not repo.get("permissions", {}).get("pull") and not repo.get("permissions", {}).get("admin"):
            raise GitHubError("github_repository_not_authorized")
        permissions = installation.get("permissions", {})
        if permissions.get("contents") not in {"read", "write"}:
            raise GitHubError("github_contents_permission_required")
        if write and (not self.settings.enable_github_writes or not repo.get("permissions", {}).get("push")
                or permissions.get("contents") != "write" or permissions.get("pull_requests") != "write"
                or repo.get("archived") or repo.get("disabled")):
            raise GitHubError("github_write_permission_required")
        identity.record_grants(self.user_id, [(installation, repo)])
        return client, repo, installation

    @_deadline(90)
    async def resolve(self, repo_id, ref="HEAD", *, issue_number=None):
        client, repo, _ = await self.authorize(repo_id)
        canonical = validate_repo_id(repo["full_name"])
        if not ref or len(ref) > 255 or any(ord(c) < 32 for c in ref):
            raise GitHubError("github_invalid_ref")
        value = await client.request("GET", f"/repos/{canonical}/commits/{quote(ref, safe='')}")
        sha = value.get("sha", "")
        if not SHA.fullmatch(sha):
            raise GitHubError("github_response_invalid")
        if issue_number is not None:
            await self.get_issue(canonical, issue_number)
        return ResolvedRepository(canonical, sha, repo["default_branch"])

    @_deadline(75)
    async def get_issue(self, repo_id, issue_number):
        client, repo, installation = await self.authorize(repo_id)
        if installation.get("permissions", {}).get("issues") not in {"read", "write"}:
            raise GitHubError("github_issues_permission_required")
        if isinstance(issue_number, bool) or not isinstance(issue_number, int) or not 1 <= issue_number <= 2147483647:
            raise GitHubError("github_issue_not_found")
        value = await client.request("GET", f"/repos/{repo['full_name']}/issues/{issue_number}")
        if value.get("number") != issue_number or "pull_request" in value:
            raise GitHubError("github_issue_not_found")
        title, body, state = value.get("title"), value.get("body") or "", value.get("state")
        if not all(isinstance(x, str) for x in (title, body, state)) or len(body.encode()) > 100_000 or len(title) > 1000:
            raise GitHubError("github_issue_size_limit")
        return {"number": issue_number, "title": title, "body": body, "state": state,
            "url": f"https://github.com/{repo['full_name']}/issues/{issue_number}"}

    @_deadline(180)
    async def snapshot(self, repo_id, ref):
        if not SHA.fullmatch(ref):
            raise GitHubError("github_immutable_commit_required")
        client, repo, _ = await self.authorize(repo_id)
        canonical = validate_repo_id(repo["full_name"])
        # Git blobs avoid redirecting a private token to codeload, and preserve
        # private content entirely in bounded memory/database, never host checkout.
        tree = await client.request("GET", f"/repos/{canonical}/git/trees/{ref}?recursive=1")
        if tree.get("truncated") or not isinstance(tree.get("tree"), list) or len(tree["tree"]) > 2000:
            raise GitHubError("github_archive_file_count_limit")
        files, size = {}, 0
        from agentforge.integrations.github_client import _safe_path, _secret_path
        for entry in tree["tree"]:
            parts = _safe_path(entry.get("path", ""))
            if _secret_path(parts):
                raise GitHubError("github_archive_sensitive_path")
            if entry.get("type") == "tree":
                continue
            if entry.get("type") != "blob" or entry.get("mode") not in {"100644", "100755"}:
                raise GitHubError("github_archive_special_file")
            if entry.get("size", 0) > 1_000_000:
                raise GitHubError("github_archive_file_size_limit")
            if not isinstance(entry.get("sha"), str) or not SHA.fullmatch(entry["sha"]):
                raise GitHubError("github_response_invalid")
            blob = await client.request("GET", f"/repos/{canonical}/git/blobs/{entry['sha']}")
            try:
                if blob.get("encoding") != "base64":
                    raise ValueError()
                data = base64.b64decode(blob["content"].replace("\n", ""), validate=True)
            except (KeyError, ValueError):
                raise GitHubError("github_response_invalid") from None
            size += len(data)
            if len(data) > 1_000_000 or size > 30_000_000:
                raise GitHubError("github_archive_decompressed_limit")
            import hashlib
            if hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest() != entry["sha"]:
                raise GitHubError("github_blob_integrity_failure")
            files[entry["path"]] = data
        from agentforge.sandbox.workspace import validate_workspace
        return RepositorySnapshot(canonical, ref, validate_workspace(files))

async def github_for_user(session_factory, settings, user_id):
    if settings.auth_mode == "github_app":
        return AuthorizedGitHubClient(session_factory, settings, user_id)
    return GitHubClient()
