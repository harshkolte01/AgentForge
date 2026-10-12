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

from agentforge.integrations.tls import outbound_tls

# A repository has no limit on its number of files, the size of one file or its size in total. This is the one safety ceiling:
# the whole snapshot is held in this server's memory (and then tarred), so a decompression bomb (a repository of a few MB that
# unpacks to many GB) must not be able to take every other conversation down with it. It is far beyond any source tree.
MAX_SNAPSHOT_BYTES = 1024 ** 3
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
    # (path, reason) of every file left out of `files`; see `skip_reason`. The rest of the repository is still complete.
    skipped: tuple[tuple[str, str], ...] = ()


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


def skip_reason(path: str) -> str | None:
    """Why one repository file is left out of a snapshot, or None when it is kept.

    A file that cannot be loaded (secrets, an unsafe name, ...) is skipped; it never fails the rest of the repository.
    """
    try:
        parts = _safe_path(path)
    except GitHubError:
        return "unsafe_name"
    if _secret_path(parts):
        return "sensitive"
    from agentforge.sandbox.workspace import validate_path
    try:
        validate_path(path)
    except ValueError as error:
        return "sensitive" if "sensitive" in str(error) else "unsafe_name"
    return None


def checked_workspace(files: dict[str, bytes]) -> dict[str, bytes]:
    """The files as a validated workspace, or a GitHubError when their paths cannot form one."""
    from agentforge.sandbox.workspace import validate_workspace
    try:
        return validate_workspace(files)
    except ValueError:
        raise GitHubError("github_archive_workspace_invalid") from None


def is_transient(error: GitHubError) -> bool:
    """True when asking again later may work (GitHub busy or unreachable); False when the same request will always fail."""
    return str(error) in {"github_rate_limited", "github_read_unavailable", "github_unavailable"}


class _Counted:
    """The unpacked tar stream, failing once it is longer than MAX_SNAPSHOT_BYTES (see there)."""

    def __init__(self, source) -> None:
        self.source, self.total = source, 0

    def read(self, size: int = -1) -> bytes:
        data = self.source.read(size)
        self.total += len(data)
        if self.total > MAX_SNAPSHOT_BYTES:
            raise GitHubError("github_archive_decompressed_limit")
        return data


def read_archive(compressed: bytes) -> tuple[dict[str, bytes], list[tuple[str, str]]]:
    """The regular files of a GitHub source archive and the (path, reason) of each one left out.

    Left out, not fatal: secrets, links, special files, unsafe names and names that differ only by letter case. Fatal: an
    archive that is malformed or empty, or one that unpacks past MAX_SNAPSHOT_BYTES. The archive is read as a stream, so
    only the files that are kept are held in memory next to the compressed bytes.
    """
    if len(compressed) > MAX_SNAPSHOT_BYTES:
        raise GitHubError("github_archive_compressed_limit")
    try:
        files: dict[str, bytes] = {}
        skipped: list[tuple[str, str]] = []
        root: str | None = None
        seen: set[str] = set()
        folded: set[str] = set()
        with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as stream, \
                tarfile.open(fileobj=_Counted(stream), mode="r|") as tar:
            for member in tar:
                name = member.name.rstrip("/") if member.isdir() else member.name
                parts = name.split("/")
                if root is None:
                    root = parts[0]
                    if _safe_path(root) != (root,):
                        raise GitHubError("github_archive_root_invalid")
                if parts[0] != root or (len(parts) == 1 and not member.isdir()):
                    raise GitHubError("github_archive_root_invalid")
                if member.isdir():
                    continue  # a folder carries nothing; one whose files are all skipped is simply never created
                if name in seen:
                    raise GitHubError("github_archive_duplicate_path")
                seen.add(name)
                path = "/".join(parts[1:])
                if member.issym() or member.islnk():
                    reason = "symlink"
                elif not member.isfile():
                    reason = "special"
                else:
                    reason = skip_reason(path)
                    if reason is None and path.casefold() in folded:
                        reason = "case_clash"
                if reason:
                    skipped.append((path, reason))
                    continue
                if member.size < 0:
                    raise GitHubError("github_archive_invalid")
                extracted = tar.extractfile(member)
                if extracted is None:
                    raise GitHubError("github_archive_invalid")
                data = extracted.read(member.size)
                if len(data) != member.size:
                    raise GitHubError("github_archive_invalid")
                files[path] = data
                folded.add(path.casefold())
        if not files:
            raise GitHubError("github_repository_empty")
        # Reject directory/file collisions even if the tar's entry order differs.
        for path in files:
            parts = path.split("/")
            if any("/".join(parts[:index]) in files for index in range(1, len(parts))):
                raise GitHubError("github_archive_path_collision")
        return checked_workspace(files), skipped
    except GitHubError:
        raise
    except (tarfile.TarError, OSError, EOFError, ValueError):
        raise GitHubError("github_archive_invalid") from None


def unpack_archive(compressed: bytes) -> dict[str, bytes]:
    return read_archive(compressed)[0]


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
            trust_env=False, verify=outbound_tls(), transport=self.transport, headers={"Accept": "application/vnd.github+json",
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

    # Unpacking a large repository takes minutes of CPU, so the time allowed is generous and the work runs off the event loop.
    @_deadline(600)
    async def snapshot(self, repo_id: str, ref: str) -> RepositorySnapshot:
        if not _SHA.fullmatch(ref):
            raise GitHubError("github_immutable_commit_required")
        resolved = await self.resolve(repo_id, ref)
        if resolved.commit_sha != ref:
            raise GitHubError("github_commit_mismatch")
        async with self._client() as client:
            archive = await self._read(client,
                f"https://codeload.github.com/{resolved.repo_id}/tar.gz/{resolved.commit_sha}", MAX_SNAPSHOT_BYTES)
        files, skipped = await asyncio.to_thread(read_archive, archive)
        return RepositorySnapshot(resolved.repo_id, resolved.commit_sha, files, skipped=tuple(skipped))
