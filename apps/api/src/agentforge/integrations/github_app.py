"""Bounded GitHub App user-token API. Never falls back to installation authority."""
from __future__ import annotations
import asyncio
import base64
import contextlib
import hashlib
import json
import re
import time
from urllib.parse import quote
import httpx
from agentforge.integrations.github_client import (GitHubClient, GitHubError, RepositorySnapshot,
    ResolvedRepository, validate_repo_id, _deadline)

SHA = re.compile(r"[0-9a-f]{40}\Z")
SNAPSHOT_CONCURRENCY = 8   # blobs fetched at once while loading a repository
LISTING_CONCURRENCY = 6    # requests in flight at once while listing a user's repositories
MAX_PAGES = 10             # pages of 100 read from any one listing

def valid_github_id(value):
    return type(value) is int and 0 < value <= 9223372036854775807

async def gather_all(coroutines):
    """`asyncio.gather` whose first failure (or cancellation) cancels the rest instead of leaving them running."""
    tasks = [asyncio.ensure_future(coroutine) for coroutine in coroutines]
    try:
        return await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

class GitHubAppClient:
    def __init__(self, settings, *, token=None, transport=None):
        self.settings, self.token, self.transport = settings, token, transport

    def http(self, **limits):
        """An API client with this token. `request` opens one per call unless it is handed a shared one (`http=`)."""
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": self.settings.github_api_version,
            "User-Agent": "AgentForge"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return httpx.AsyncClient(timeout=httpx.Timeout(20, connect=5), follow_redirects=False,
            trust_env=False, transport=self.transport, headers=headers, **limits)

    async def request(self, method, path, *, payload=None, allow_missing=False, http=None):
        if not path.startswith("/") or path.startswith("//"):
            raise GitHubError("github_invalid_endpoint")
        try:
            if http is not None:
                return await self._send(http, method, path, payload, allow_missing)
            async with self.http() as client:
                return await self._send(client, method, path, payload, allow_missing)
        except (httpx.HTTPError, asyncio.TimeoutError):
            # A caller's durable ledger treats write failures as ambiguous.
            raise GitHubError("github_unavailable") from None
        except (json.JSONDecodeError, UnicodeError):
            raise GitHubError("github_response_invalid") from None

    @staticmethod
    async def _send(client, method, path, payload, allow_missing):
        async with client.stream(method, "https://api.github.com" + path, json=payload) as response:
            if allow_missing and response.status_code == 404:
                return None
            if response.status_code in {401, 403, 404}:
                raise GitHubError("github_access_denied")
            if response.status_code in {409, 422}:
                raise GitHubError("github_write_conflict")
            if response.status_code == 405:
                # A merge GitHub will not make (a draft, a required check, a protected branch).
                raise GitHubError("github_not_mergeable")
            if response.status_code == 429:
                raise GitHubError("github_rate_limited")
            if response.status_code not in {200, 201, 202, 204}:
                raise GitHubError("github_unavailable")
            data = bytearray()
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if len(data) > 6_000_000:
                    raise GitHubError("github_response_size_limit")
            return json.loads(data) if data else {}

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
                if (not isinstance(value.get("token_type"), str) or value["token_type"].lower() != "bearer"
                        or not value["access_token"].startswith("ghu_") or len(value["access_token"]) > 1024):
                    raise GitHubError("github_app_user_token_required")
                return value
        except (httpx.HTTPError, ValueError):
            raise GitHubError("github_authorization_failed") from None

    async def pages(self, path, key, *, http=None, gate=None):
        """Every item of a paginated listing (at most MAX_PAGES pages). A list that reports its `total_count` has its
        remaining pages read together (`gate`, a semaphore, bounds the requests in flight); otherwise, or if the
        count turns out short, pages are read one after another."""
        if http is None:
            async with self.http() as shared:
                return await self.pages(path, key, http=shared, gate=gate)
        separator = "&" if "?" in path else "?"

        async def read(page):
            async with gate or contextlib.nullcontext():
                value = await self.request("GET", f"{path}{separator}per_page=100&page={page}", http=http)
            values = value if key is None else value.get(key) if isinstance(value, dict) else None
            if not isinstance(values, list):
                raise GitHubError("github_response_invalid")
            return value, values

        first, values = await read(1)
        result, page = list(values), 2
        total = first.get("total_count") if isinstance(first, dict) else None
        if len(values) == 100 and type(total) is int and total > 100:
            if total >= 100 * MAX_PAGES:
                raise GitHubError("github_repository_discovery_limit")
            ahead = range(2, -(-total // 100) + 1)
            batch = await gather_all(read(number) for number in ahead)
            for _, values in batch:
                result.extend(values)
            page = ahead.stop
        while len(values) == 100:
            if page > MAX_PAGES:
                raise GitHubError("github_repository_discovery_limit")
            _, values = await read(page)
            result.extend(values)
            page += 1
        return result

    @_deadline(45)
    async def installations(self, *, http=None):
        values = await self.pages("/user/installations", "installations", http=http)
        if any(not isinstance(value, dict) or not valid_github_id(value.get("id"))
                or not valid_github_id(value.get("app_id")) for value in values):
            raise GitHubError("github_response_invalid")
        result = [value for value in values if value["app_id"] == self.settings.github_app_id
            and not value.get("suspended_at")]
        if len(result) > 10:
            raise GitHubError("github_installation_discovery_limit")
        return result

    @_deadline(60)
    async def repositories(self):
        """(installation, repository) pairs, installation by installation in the order GitHub lists them. One
        connection pool serves the whole walk, and the installations' repositories are read together."""
        gate = asyncio.Semaphore(LISTING_CONCURRENCY)
        async with self.http() as http:
            installations = await self.installations(http=http)
            listings = await gather_all(self.pages(f"/user/installations/{int(installation['id'])}/repositories",
                "repositories", http=http, gate=gate) for installation in installations)
        result = []
        for installation, repositories in zip(installations, listings):
            for repository in repositories:
                self.validate_repository(repository)
                result.append((installation, repository))
                if len(result) > 1000:
                    raise GitHubError("github_repository_discovery_limit")
        return result

    @staticmethod
    def validate_repository(repository):
        if (not isinstance(repository, dict) or not valid_github_id(repository.get("id"))
                or not isinstance(repository.get("permissions"), dict)
                or not isinstance(repository.get("default_branch"), str)):
            raise GitHubError("github_response_invalid")
        validate_repo_id(repository.get("full_name", ""))

class AuthorizedGitHubClient:
    """Task adapter obtains fresh user credentials and current repo membership.

    A run rechecks its repository before every model step, tool call and approval poll. A successful read check is
    kept for AUTHORIZATION_TTL_SEC by this client (one per task runtime), so the rechecks cost nothing in between;
    a failed check is never kept, and a write check is always made afresh.
    """
    AUTHORIZATION_TTL_SEC = 60

    def __init__(self, session_factory, settings, user_id, *, transport=None, clock=time.monotonic):
        self.session_factory, self.settings, self.user_id, self.transport = session_factory, settings, user_id, transport
        self._clock = clock
        self._authorized = {}   # repo_id (lower case) -> (valid until, client, repo, installation)
        self._lock = asyncio.Lock()

    async def authorize(self, repo_id, *, write=False, repository_id=None):
        repo_id = validate_repo_id(repo_id)
        key = repo_id.lower()
        # One check at a time: concurrent tool calls wait for the one in flight and then share its result.
        async with self._lock:
            cached = None if write else self._authorized.get(key)
            if cached is not None and cached[0] > self._clock():
                _, client, repo, installation = cached
                if repository_id is not None and repo.get("id") != repository_id:
                    raise GitHubError("github_repository_identity_changed")
                return client, repo, installation
            self._authorized.pop(key, None)
            client, repo, installation = await self._authorize(repo_id, write=write, repository_id=repository_id)
            # The token is at least 60s from expiry when issued (GitHubIdentityService.access_token).
            self._authorized[key] = (self._clock() + self.AUTHORIZATION_TTL_SEC, client, repo, installation)
            return client, repo, installation

    @_deadline(60)
    async def _authorize(self, repo_id, *, write=False, repository_id=None):
        from agentforge.auth.github_service import GitHubIdentityService
        identity = GitHubIdentityService(self.session_factory, self.settings, transport=self.transport)
        client = GitHubAppClient(self.settings, token=await identity.access_token(self.user_id), transport=self.transport)
        from sqlalchemy import select, func
        from agentforge.database.identity import GitHubRepositoryGrant

        def known_installation():
            with self.session_factory() as database:
                known = database.scalar(select(GitHubRepositoryGrant).where(GitHubRepositoryGrant.user_id == self.user_id,
                    func.lower(GitHubRepositoryGrant.full_name) == repo_id.lower()))
                return known.installation_id if known else None
        known_installation = await asyncio.to_thread(known_installation)
        if known_installation is not None:
            # Cached installation ID narrows discovery only; fresh membership,
            # suspension and repository rights still come from the user token.
            installs = [item for item in await client.installations() if item["id"] == known_installation]
            matches = []
            for installation in installs:
                repos = await client.pages(f"/user/installations/{known_installation}/repositories", "repositories")
                for item in repos:
                    client.validate_repository(item)
                matches.extend((installation, item) for item in repos if item.get("full_name", "").lower() == repo_id.lower())
        else:
            matches = [(installation, repo) for installation, repo in await client.repositories()
                if repo.get("full_name", "").lower() == repo_id.lower()]
        if len(matches) != 1:
            raise GitHubError("github_repository_not_authorized")
        installation, repo = matches[0]
        if repository_id is not None and repo.get("id") != repository_id:
            raise GitHubError("github_repository_identity_changed")
        if repo.get("permissions", {}).get("pull") is not True and repo.get("permissions", {}).get("admin") is not True:
            raise GitHubError("github_repository_not_authorized")
        permissions = installation.get("permissions", {})
        if permissions.get("contents") not in {"read", "write"}:
            raise GitHubError("github_contents_permission_required")
        if write and (not self.settings.enable_github_writes or repo.get("permissions", {}).get("push") is not True
                or permissions.get("contents") != "write" or permissions.get("pull_requests") != "write"
                or repo.get("archived") or repo.get("disabled")):
            raise GitHubError("github_write_permission_required")
        await asyncio.to_thread(identity.record_grants, self.user_id, [(installation, repo)])
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
        from agentforge.integrations.github_client import _safe_path, _secret_path
        # Every entry is checked before anything is fetched.
        blobs = []
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
            blobs.append(entry)
        size = 0
        gate = asyncio.Semaphore(SNAPSHOT_CONCURRENCY)

        async def fetch(entry, http):
            nonlocal size
            async with gate:
                blob = await client.request("GET", f"/repos/{canonical}/git/blobs/{entry['sha']}", http=http)
            try:
                if blob.get("encoding") != "base64":
                    raise ValueError()
                data = base64.b64decode(blob["content"].replace("\n", ""), validate=True)
            except (KeyError, ValueError, AttributeError):
                raise GitHubError("github_response_invalid") from None
            size += len(data)
            if len(data) > 1_000_000 or size > 30_000_000:
                raise GitHubError("github_archive_decompressed_limit")
            if hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest() != entry["sha"]:
                raise GitHubError("github_blob_integrity_failure")
            return data

        # One connection pool for the whole snapshot, a few blobs in flight at once; the first failure stops the rest.
        async with client.http(limits=httpx.Limits(max_connections=SNAPSHOT_CONCURRENCY)) as http:
            fetches = [asyncio.create_task(fetch(entry, http)) for entry in blobs]
            try:
                contents = await asyncio.gather(*fetches)
            finally:
                for pending in fetches:
                    pending.cancel()
                await asyncio.gather(*fetches, return_exceptions=True)
        files = {entry["path"]: data for entry, data in zip(blobs, contents)}
        modes = {entry["path"]: 0o755 if entry["mode"] == "100755" else 0o644 for entry in blobs}
        from agentforge.sandbox.workspace import validate_workspace
        return RepositorySnapshot(canonical, ref, validate_workspace(files), modes)

async def github_for_user(session_factory, settings, user_id):
    if settings.auth_mode == "github_app":
        return AuthorizedGitHubClient(session_factory, settings, user_id)
    return GitHubClient()
