"""Credential-free public GitHub reads and bounded immutable source archives.

Repository content is untrusted data. This module never clones, extracts onto the
host filesystem, executes files, follows redirects, or uses ambient credentials.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import wraps
import asyncio
import gzip
import io
import re
import tarfile
from urllib.parse import quote

import httpx

MAX_COMPRESSED_BYTES = 10_000_000
MAX_ARCHIVE_BYTES = 30_000_000
MAX_FILE_BYTES = 1_000_000
MAX_FILES = 2_000
_REPO = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9_.-]{1,100}\Z")
_SHA = re.compile(r"[0-9a-f]{40}\Z")


class GitHubError(ValueError):
    """A stable public error code with no upstream response content."""


@dataclass(frozen=True)
class ResolvedRepository:
    repo_id: str
    commit_sha: str
    default_branch: str


@dataclass(frozen=True)
class RepositorySnapshot:
    repo_id: str
    commit_sha: str
    files: dict[str, bytes]
    # Unix mode per path (0o755 executable, 0o644 otherwise) when the source knows it; empty means 0o644 for all.
    modes: dict[str, int] = field(default_factory=dict)


def validate_repo_id(repo_id: str) -> str:
    if not isinstance(repo_id, str) or not _REPO.fullmatch(repo_id) or repo_id.split("/")[1] in {".", ".."}:
        raise GitHubError("github_invalid_repository")
    return repo_id


def _safe_path(name: str) -> tuple[str, ...]:
    parts = name.split("/")
    if (not name or len(name) > 1000 or name.startswith("/") or "\\" in name or
            any(part in {"", ".", ".."} for part in parts) or
            any(ord(char) < 32 or ord(char) == 127 for char in name) or ":" in name):
        raise GitHubError("github_unsafe_archive_path")
    return tuple(parts)


def _secret_path(parts: tuple[str, ...]) -> bool:
    for part in parts:
        value = part.lower()
        if value == ".git" or value in {".netrc", ".npmrc", ".pypirc", "credentials", "id_rsa", "id_ed25519"}:
            return True
        if value == ".env" or (value.startswith(".env.") and value not in {".env.example", ".env.sample", ".env.template"}):
            return True
        if value.endswith((".pem", ".key", ".p12", ".pfx", ".keystore")):
            return True
    return False


def unpack_archive(compressed: bytes) -> dict[str, bytes]:
    if len(compressed) > MAX_COMPRESSED_BYTES:
        raise GitHubError("github_archive_compressed_limit")
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as stream:
            archive = stream.read(MAX_ARCHIVE_BYTES + 1)
        if len(archive) > MAX_ARCHIVE_BYTES:
            raise GitHubError("github_archive_decompressed_limit")
        files: dict[str, bytes] = {}
        root: str | None = None
        seen: set[str] = set()
        total_bytes = 0
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as tar:
            for count, member in enumerate(tar, start=1):
                if count > MAX_FILES:
                    raise GitHubError("github_archive_file_count_limit")
                name = member.name.rstrip("/") if member.isdir() else member.name
                parts = _safe_path(name)
                if root is None:
                    root = parts[0]
                if parts[0] != root or (len(parts) == 1 and not member.isdir()):
                    raise GitHubError("github_archive_root_invalid")
                if member.issym() or member.islnk() or not (member.isdir() or member.isfile()):
                    raise GitHubError("github_archive_special_file")
                if name in seen:
                    raise GitHubError("github_archive_duplicate_path")
                seen.add(name)
                if _secret_path(parts[1:]):
                    raise GitHubError("github_archive_sensitive_path")
                if member.isdir():
                    continue
                path = "/".join(parts[1:])
                if member.size < 0 or member.size > MAX_FILE_BYTES:
                    raise GitHubError("github_archive_file_size_limit")
                total_bytes += member.size
                if total_bytes > MAX_ARCHIVE_BYTES:
                    raise GitHubError("github_archive_decompressed_limit")
                extracted = tar.extractfile(member)
                if extracted is None:
                    raise GitHubError("github_archive_invalid")
                data = extracted.read(MAX_FILE_BYTES + 1)
                if len(data) != member.size:
                    raise GitHubError("github_archive_invalid")
                files[path] = data
        if not files:
            raise GitHubError("github_repository_empty")
        # Reject directory/file collisions even if the tar's entry order differs.
        for path in files:
            parts = path.split("/")
            if any("/".join(parts[:index]) in files for index in range(1, len(parts))):
                raise GitHubError("github_archive_path_collision")
        from agentforge.sandbox.workspace import validate_workspace
        from agentforge.subagents.scope import ScopeDenied
        try:
            return validate_workspace(files)
        except (ScopeDenied, ValueError):
            raise GitHubError("github_archive_workspace_invalid") from None
    except GitHubError:
        raise
    except (tarfile.TarError, OSError, EOFError, ValueError):
        raise GitHubError("github_archive_invalid") from None


def _deadline(seconds: float):
    def decorate(function):
        @wraps(function)
        async def bounded(*args, **kwargs):
            try:
                async with asyncio.timeout(seconds):
                    return await function(*args, **kwargs)
            except TimeoutError:
                raise GitHubError("github_read_unavailable") from None
        return bounded
    return decorate


class GitHubClient:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None):
        self.transport = transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=httpx.Timeout(20, connect=5), follow_redirects=False,
            trust_env=False, transport=self.transport, headers={"Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "AgentForge-public-source"})

    @staticmethod
    async def _read(client: httpx.AsyncClient, url: str, maximum: int) -> bytes:
        try:
            async with client.stream("GET", url) as response:
                if response.status_code == 404:
                    raise GitHubError("github_public_repository_or_ref_not_found")
                if response.status_code in {403, 429}:
                    raise GitHubError("github_rate_limited")
                if response.status_code != 200:
                    raise GitHubError("github_read_unavailable")
                length = response.headers.get("content-length")
                if length and (not length.isdigit() or int(length) > maximum):
                    raise GitHubError("github_response_size_limit")
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > maximum:
                        raise GitHubError("github_response_size_limit")
                return bytes(data)
        except httpx.HTTPError:
            raise GitHubError("github_read_unavailable") from None

    async def _json(self, client: httpx.AsyncClient, path: str) -> dict:
        import json
        try:
            value = json.loads(await self._read(client, f"https://api.github.com{path}", 500_000))
            if not isinstance(value, dict):
                raise ValueError()
            return value
        except (ValueError, UnicodeDecodeError) as error:
            if isinstance(error, GitHubError):
                raise
            raise GitHubError("github_response_invalid") from None

    @_deadline(45)
    async def resolve(self, repo_id: str, ref: str = "HEAD", *, issue_number: int | None = None) -> ResolvedRepository:
        repo_id = validate_repo_id(repo_id)
        if not ref or len(ref) > 255 or any(ord(char) < 32 for char in ref):
            raise GitHubError("github_invalid_ref")
        async with self._client() as client:
            repository = await self._json(client, f"/repos/{repo_id}")
            if repository.get("private") is not False or repository.get("visibility") not in {None, "public"}:
                raise GitHubError("github_public_repository_required")
            canonical = validate_repo_id(repository.get("full_name", ""))
            # Renames must be explicitly selected, rather than following redirects.
            if canonical.lower() != repo_id.lower():
                raise GitHubError("github_repository_identity_changed")
            commit = await self._json(client, f"/repos/{canonical}/commits/{quote(ref, safe='')}")
            sha = commit.get("sha", "")
            if not isinstance(sha, str) or not _SHA.fullmatch(sha):
                raise GitHubError("github_response_invalid")
            if issue_number is not None:
                issue = await self._json(client, f"/repos/{canonical}/issues/{issue_number}")
                if issue.get("number") != issue_number or "pull_request" in issue:
                    raise GitHubError("github_issue_not_found")
            branch = repository.get("default_branch", "")
            return ResolvedRepository(canonical, sha, branch if isinstance(branch, str) else "")

    @_deadline(45)
    async def get_issue(self, repo_id: str, issue_number: int) -> dict:
        repo_id = validate_repo_id(repo_id)
        if isinstance(issue_number, bool) or not isinstance(issue_number, int) or not 1 <= issue_number <= 2_147_483_647:
            raise GitHubError("github_issue_not_found")
        async with self._client() as client:
            repository = await self._json(client, f"/repos/{repo_id}")
            if repository.get("private") is not False or repository.get("visibility") not in {None, "public"}:
                raise GitHubError("github_public_repository_required")
            issue = await self._json(client, f"/repos/{repo_id}/issues/{issue_number}")
            if issue.get("number") != issue_number or "pull_request" in issue:
                raise GitHubError("github_issue_not_found")
            title, body, state = issue.get("title", ""), issue.get("body") or "", issue.get("state", "")
            if not all(isinstance(value, str) for value in (title, body, state)) or len(body.encode()) > 100_000 or len(title) > 1000:
                raise GitHubError("github_issue_size_limit")
            return {"number": issue_number, "title": title, "body": body, "state": state,
                    "url": f"https://github.com/{repo_id}/issues/{issue_number}"}

    @_deadline(90)
    async def snapshot(self, repo_id: str, ref: str) -> RepositorySnapshot:
        if not _SHA.fullmatch(ref):
            raise GitHubError("github_immutable_commit_required")
        resolved = await self.resolve(repo_id, ref)
        if resolved.commit_sha != ref:
            raise GitHubError("github_commit_mismatch")
        async with self._client() as client:
            archive = await self._read(client,
                f"https://codeload.github.com/{resolved.repo_id}/tar.gz/{resolved.commit_sha}", MAX_COMPRESSED_BYTES)
        return RepositorySnapshot(resolved.repo_id, resolved.commit_sha, unpack_archive(archive))


async def fetch_repository_archive(repo_id: str, commit_sha: str) -> dict[str, bytes]:
    return (await GitHubClient().snapshot(repo_id, commit_sha)).files
