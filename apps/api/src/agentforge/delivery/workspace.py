"""What changed in a conversation's sandbox, measured with git inside it. The sandbox never sees a credential.

Two tags mark the sandbox repository: `agentforge-base` (the commit the repository was loaded at) and `agentforge-pushed`
(what was last published, moved by `advance_baseline`). Changes are the working tree compared with the newer of the two.
"""
import asyncio
import hashlib
import shlex
from datetime import datetime, timezone
from typing import NamedTuple

from agentforge.integrations.e2b_sandbox import WORKSPACE, SandboxUnavailable, Workspace
from agentforge.integrations.github_app import SHA
from agentforge.integrations.github_delivery import MAX_CHANGED_FILES
from agentforge.sandbox.workspace import validate_path
from agentforge.tools.sandbox_shell import BASE_TAG, MARKER

PUSHED_TAG = "agentforge-pushed"
FILE_PATCH_CHARS = 120_000
TOTAL_PATCH_BYTES = 600_000
READ_CONCURRENCY = 6
SECTION = "@@AF:{}@@"
ZERO = "0" * 40
INDEX_INFO = "/tmp/agentforge-index-info"
MEASURE_INDEX = "/tmp/agentforge-measure.index"


class WorkspaceChanged(Exception):
    """The files changed while they were being read: nothing was published."""


class FileChange(NamedTuple):
    path: str
    status: str            # added | modified | deleted
    mode: str              # git mode of the new file ('000000' when deleted)
    old: str | None        # blob in the baseline
    new: str | None        # blob in the working tree
    additions: int
    deletions: int
    binary: bool
    blocked: str | None    # why it cannot be published, if it cannot
    patch: str = ""
    truncated: bool = False


class Measured(NamedTuple):
    baseline: str          # 'base' | 'published'
    files: list[FileChange]
    truncated: bool        # more files than MAX_CHANGED_FILES, or a patch that was cut
    diff_version: str


def blocked_reason(path: str, mode: str) -> str | None:
    try:
        validate_path(path)
    except ValueError as error:
        return "secret" if "sensitive" in str(error) else "path"
    if path.split("/")[0].casefold() == ".agentforge":
        return "path"
    if mode == "160000":
        return "submodule"
    if mode == "120000":
        return "symlink"
    return None


def parse_raw(text: str) -> list[tuple[str, str, str, str | None, str | None]]:
    """`git diff --raw -z --no-abbrev` as (path, status, new mode, old blob, new blob)."""
    parts = text.split("\0")
    result = []
    for index in range(0, len(parts) - 1, 2):
        fields = parts[index].lstrip(":").split(" ")
        path = parts[index + 1]
        if len(fields) < 5 or not path:
            continue
        src_mode, dst_mode, old, new, kind = fields[:5]
        status = {"A": "added", "D": "deleted"}.get(kind[:1], "modified")
        result.append((path, status, dst_mode, None if old == ZERO else old, None if new == ZERO else new))
    return result


def parse_numstat(text: str) -> dict[str, tuple[int, int, bool]]:
    stats = {}
    for record in text.split("\0"):
        pieces = record.split("\t", 2)
        if len(pieces) == 3:
            added, removed, path = pieces
            binary = added == "-" or removed == "-"
            stats[path] = (0 if binary else int(added), 0 if binary else int(removed), binary)
    return stats


def split_patches(text: str, limit_hit: bool) -> list[str]:
    """One unified diff per file, in git's order. A patch cut by the size limit drops its torn last file."""
    marker = "diff --git "
    if limit_hit and "\n" + marker in text:
        text = text[: text.rindex("\n" + marker) + 1]
    blocks = []
    for piece in text.split("\n" + marker):
        piece = piece if piece.startswith(marker) else marker + piece
        if piece.strip() and piece.startswith(marker):
            blocks.append(piece if piece.endswith("\n") else piece + "\n")
    return blocks


async def read_marker(workspace: Workspace) -> tuple[str, str] | None:
    """(repository, commit) the sandbox repository was loaded at, or None if it holds no repository."""
    code, text = await workspace.capture(f"cat {MARKER} 2>/dev/null; true", timeout_sec=30)
    text = text.strip()
    if "@" not in text:
        return None
    repo, sha = text.rsplit("@", 1)
    return (repo, sha) if SHA.fullmatch(sha) else None


async def measure(workspace: Workspace, *, patches: bool = True) -> Measured:
    """Compares the working tree with the baseline. Read only for the person's files: the index is restored afterwards."""
    patch_command = (f"git -c core.quotepath=false diff --cached --no-renames --no-color --no-ext-diff -U3 \"$B\" | head -c {TOTAL_PATCH_BYTES + 1}"
                     if patches else "true")
    # A private index: the agent's own git commands (which use .git/index and its lock) are never disturbed.
    script = (
        # Not a checkout of the repository (no git, or no base tag): say so rather than report 'no changes'.
        f"git rev-parse -q --verify refs/tags/{BASE_TAG} >/dev/null 2>&1 || {{ echo '@@AF:norepo@@'; exit 0; }}; "
        f"git rev-parse -q --verify refs/tags/{PUSHED_TAG} >/dev/null && B={PUSHED_TAG} || B={BASE_TAG}; "
        f"export GIT_INDEX_FILE={MEASURE_INDEX}; rm -f \"$GIT_INDEX_FILE\"; git read-tree \"$B\" >/dev/null 2>&1; "
        "git add -A >/dev/null 2>&1; "
        "printf '%s\\n' \"@@AF:baseline:$B@@\"; "
        "git diff --cached --raw -z --no-renames --no-abbrev \"$B\"; "
        f"printf '\\n{SECTION.format('numstat')}\\n'; git diff --cached --numstat -z --no-renames \"$B\"; "
        f"printf '\\n{SECTION.format('patch')}\\n'; {patch_command}; "
        "rm -f \"$GIT_INDEX_FILE\"; true")
    code, out = await workspace.capture(script, timeout_sec=120)
    head, _, rest = out.partition("\n" + SECTION.format("numstat") + "\n")
    stat_text, _, patch_text = rest.partition("\n" + SECTION.format("patch") + "\n")
    first, _, raw_text = head.partition("\n")
    if not first.startswith("@@AF:baseline:"):
        raise SandboxUnavailable("WORKSPACE_NOT_A_REPOSITORY")
    baseline = "published" if first.startswith("@@AF:baseline:" + PUSHED_TAG) else "base"
    entries = parse_raw(raw_text)
    stats = parse_numstat(stat_text)
    cut = len(patch_text) > TOTAL_PATCH_BYTES
    blocks = split_patches(patch_text[:TOTAL_PATCH_BYTES], cut) if patches else []
    files, truncated = [], len(entries) > MAX_CHANGED_FILES or cut
    for index, (path, status, mode, old, new) in enumerate(entries):
        added, removed, binary = stats.get(path, (0, 0, False))
        block = blocks[index] if index < len(blocks) and path in blocks[index].split("\n", 1)[0] else ""
        long = len(block) > FILE_PATCH_CHARS
        files.append(FileChange(path, status, mode, old, new, added, removed, binary, blocked_reason(path, mode),
            "" if binary or long else block, long or (patches and not binary and not block and (added or removed) > 0)))
    version = hashlib.sha256(raw_text.encode()).hexdigest()
    return Measured(baseline, files, truncated, version)


def summary(measured: Measured) -> dict | None:
    """The short form kept on the delivery row; None when there is nothing to publish."""
    if not measured.files:
        return None
    return {"files": len(measured.files), "additions": sum(f.additions for f in measured.files),
            "deletions": sum(f.deletions for f in measured.files), "diff_version": measured.diff_version,
            "updated_at": datetime.now(timezone.utc).isoformat()}


def blob_sha(data: bytes) -> str:
    return hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()


async def read_contents(workspace: Workspace, files: list[FileChange]) -> dict[str, bytes]:
    """The exact bytes of every added or modified file, each checked against the blob git measured, so what is published is
    what was reviewed even if the agent kept writing."""
    gate = asyncio.Semaphore(READ_CONCURRENCY)
    wanted = [f for f in files if f.status != "deleted"]

    async def read(file: FileChange) -> tuple[str, bytes]:
        async with gate:
            data = await workspace.read_bytes(file.path)
        if blob_sha(data) != file.new:
            raise WorkspaceChanged(file.path)
        return file.path, data

    return dict(await asyncio.gather(*(read(f) for f in wanted)))


async def advance_baseline(workspace: Workspace, files: list[FileChange], commit_sha: str) -> None:
    """Records that `files` are now published: `agentforge-pushed` becomes the previous baseline plus exactly these changes.
    Built from the blobs `git add` already stored, so edits made since are not swallowed."""
    records = []
    for file in files:
        if file.status == "deleted":
            records.append(f"0 {ZERO}\t{file.path}\0")
        else:
            records.append(f"{file.mode} {file.new}\t{file.path}\0")
    await workspace.write(INDEX_INFO, "".join(records).encode())
    script = (
        "set -e; "
        f"git rev-parse -q --verify refs/tags/{PUSHED_TAG} >/dev/null && B={PUSHED_TAG} || B={BASE_TAG}; "
        "export GIT_INDEX_FILE=/tmp/agentforge-pushed.index; rm -f \"$GIT_INDEX_FILE\"; "
        "git read-tree \"$B\"; "
        f"git update-index -z --index-info < {INDEX_INFO}; "
        "tree=$(git write-tree); "
        f"commit=$(git commit-tree \"$tree\" -p \"$B\" -m {shlex.quote('published ' + commit_sha)}); "
        f"git tag -f {PUSHED_TAG} \"$commit\" >/dev/null; "
        f"rm -f \"$GIT_INDEX_FILE\" {INDEX_INFO}")
    code, out = await workspace.capture(script, timeout_sec=120)
    if code not in (0, None):
        raise SandboxUnavailable("BASELINE_NOT_ADVANCED")


def local_path(path: str) -> str:
    """A path inside the workspace that may be reverted. Unlike a path to publish, a secret file's name is fine here (discarding
    `.env` is how it stops blocking a push); escaping the workspace, or touching git's own files, is not."""
    parts = path.split("/") if isinstance(path, str) else []
    if (not path or len(path.encode()) > 1024 or path.startswith("/") or "\\" in path or any(p in ("", ".", "..") for p in parts)
            or any(ord(c) < 32 or ord(c) == 127 for c in path) or parts[0].casefold() in (".git", ".agentforge")):
        raise ValueError("invalid_workspace_path")
    return path


async def discard(workspace: Workspace, path: str, *, baseline_has: bool) -> None:
    """Puts one file back as it was in the baseline (removes it if it was added since)."""
    local_path(path)
    quoted = shlex.quote(path)
    command = (f"B=$(git rev-parse -q --verify refs/tags/{PUSHED_TAG} >/dev/null && echo {PUSHED_TAG} || echo {BASE_TAG}); "
               + (f"git checkout \"$B\" -- {quoted}" if baseline_has else f"rm -f -- {quoted}") + "; git reset -q >/dev/null 2>&1; true")
    await workspace.capture(command, timeout_sec=60)


async def baseline_has(workspace: Workspace, path: str) -> bool:
    local_path(path)
    code, _ = await workspace.capture(
        f"B=$(git rev-parse -q --verify refs/tags/{PUSHED_TAG} >/dev/null && echo {PUSHED_TAG} || echo {BASE_TAG}); "
        f"git cat-file -e \"$B:\"{shlex.quote(path)}", timeout_sec=30)
    return code == 0
