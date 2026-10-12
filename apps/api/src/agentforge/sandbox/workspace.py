"""Byte-only workspaces: never check out, import, or execute repository code locally."""

import difflib
import gzip
import hashlib
import io
import json
import tarfile
import unicodedata
from collections.abc import Mapping
from pathlib import PurePosixPath

# Bounds of `import_archive` and `build_diff` only (an uploaded archive is untrusted input, and neither has a production caller
# any more). Loading a repository into a conversation's sandbox has no file-count or size limit: see `validate_workspace`.
MAX_FILES = 2000
MAX_FILE_BYTES = 1_000_000
MAX_WORKSPACE_BYTES = 10_000_000
MAX_ARCHIVE_BYTES = 12_000_000


def validate_path(path: str) -> str:
    if not isinstance(path, str) or not path or len(path.encode()) > 1024:
        raise ValueError("invalid_workspace_path")
    parts = path.split("/")
    if (path.startswith("/") or "\\" in path or any(p in ("", ".", "..") for p in parts)
            or any(unicodedata.category(c).startswith("C") for c in path)
            or ":" in path or unicodedata.normalize("NFC", path) != path):
        raise ValueError("invalid_workspace_path")
    lowered = [p.casefold() for p in parts]
    if any(p in (".git", ".ssh", ".aws", ".gnupg", ".npmrc", ".pypirc", ".netrc",
                 "credentials", "id_rsa", "id_ed25519") for p in lowered):
        raise ValueError("sensitive_workspace_path")
    if any((p == ".env" or p.startswith(".env.")) and p not in
           (".env.example", ".env.sample", ".env.template") for p in lowered):
        raise ValueError("sensitive_workspace_path")
    if lowered[-1].endswith((".pem", ".p12", ".pfx", ".key")):
        raise ValueError("sensitive_workspace_path")
    return path


def validate_workspace(files: Mapping[str, bytes]) -> dict[str, bytes]:
    """The files with safe, distinct paths. How many there are, and how big, is not limited here."""
    result: dict[str, bytes] = {}
    canonical: set[str] = set()
    for path, data in files.items():
        validate_path(path)
        if not isinstance(data, bytes):
            raise ValueError("invalid_workspace_file")
        key = path.casefold()
        if key in canonical:
            raise ValueError("workspace_path_collision")
        canonical.add(key)
        result[path] = data
    # A file cannot also be a parent directory, including case aliases.
    for path in canonical:
        if any(str(parent) in canonical for parent in PurePosixPath(path).parents if str(parent) != "."):
            raise ValueError("workspace_path_collision")
    return dict(sorted(result.items()))


def workspace_revision(files: Mapping[str, bytes]) -> str:
    digest = hashlib.sha256(b"agentforge-workspace-v1\0")
    for path, data in validate_workspace(files).items():
        name = path.encode("utf-8")
        digest.update(len(name).to_bytes(4, "big"))
        digest.update(name)
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


def read_text(files: Mapping[str, bytes], path: str) -> str:
    validate_path(path)
    data = files[path]
    if b"\0" in data:
        raise ValueError("binary_file_not_editable")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("binary_file_not_editable") from None


def write_text(files: Mapping[str, bytes], path: str, content: str,
               expected_sha256: str | None) -> dict[str, bytes]:
    """None means create-only; existing files always require an exact CAS hash."""
    result = validate_workspace(files)
    validate_path(path)
    if path in result:
        read_text(result, path)
        if expected_sha256 != hashlib.sha256(result[path]).hexdigest():
            raise ValueError("workspace_write_conflict")
    elif expected_sha256 is not None:
        raise ValueError("workspace_write_conflict")
    if "\0" in content:
        raise ValueError("binary_file_not_editable")
    result[path] = content.encode("utf-8")
    return validate_workspace(result)


def import_archive(archive: bytes) -> dict[str, bytes]:
    """Read bounded tar bytes without extracting to a host filesystem."""
    if len(archive) > MAX_ARCHIVE_BYTES:
        raise ValueError("archive_byte_limit")
    # Bound expansion before tarfile parses PAX/GNU extension headers. Otherwise
    # a compressed huge header may allocate memory before yielding a member.
    if archive.startswith(b"\x1f\x8b"):
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(archive)) as compressed:
                archive = compressed.read(MAX_ARCHIVE_BYTES + 1)
        except (OSError, EOFError):
            raise ValueError("invalid_archive") from None
        if len(archive) > MAX_ARCHIVE_BYTES:
            raise ValueError("archive_expansion_limit")
    elif archive.startswith((b"BZh", b"\xfd7zXZ")):
        raise ValueError("unsupported_archive_compression")
    files: dict[str, bytes] = {}
    seen: set[str] = set()
    total = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r|") as tar:
            count = 0
            for member in tar:
                count += 1
                if count > MAX_FILES * 2:
                    raise ValueError("archive_entry_limit")
                name = member.name.rstrip("/") if member.isdir() else member.name
                validate_path(name)
                if name.casefold() in seen:
                    raise ValueError("archive_duplicate_path")
                seen.add(name.casefold())
                if member.isdir():
                    continue
                if not member.isfile() or member.issparse():
                    raise ValueError("archive_special_file")
                if member.size > MAX_FILE_BYTES or len(files) >= MAX_FILES:
                    raise ValueError("workspace_file_limit")
                total += member.size
                if total > MAX_WORKSPACE_BYTES:
                    raise ValueError("workspace_byte_limit")
                stream = tar.extractfile(member)
                if stream is None:
                    raise ValueError("invalid_archive")
                data = stream.read(MAX_FILE_BYTES + 1)
                if len(data) != member.size:
                    raise ValueError("invalid_archive")
                files[name] = data
    except (tarfile.TarError, EOFError, OSError):
        raise ValueError("invalid_archive") from None
    return validate_workspace(files)


def build_diff(baseline: Mapping[str, bytes], files: Mapping[str, bytes]) -> str:
    """Compare externally retained bytes; untrusted .git never defines the baseline."""
    before, after = validate_workspace(baseline), validate_workspace(files)
    chunks: list[str] = []
    size = 0
    for path in sorted(before.keys() | after.keys()):
        if before.get(path) == after.get(path):
            continue
        if (path not in before and after[path] == b"") or (path not in after and before[path] == b""):
            # Unified text hunks cannot represent an empty file's existence.
            # Git mode/index metadata can, including an empty-file deletion.
            chunk = f"diff --git {json.dumps('a/' + path)} {json.dumps('b/' + path)}\n"
            chunk += ("new file mode 100644\nindex 0000000..e69de29\n" if path not in before
                      else "deleted file mode 100644\nindex e69de29..0000000\n")
            chunks.append(chunk)
            continue
        try:
            old = read_text(before, path) if path in before else ""
            new = read_text(after, path) if path in after else ""
            lines = difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True),
                fromfile=f"a/{path}" if path in before else "/dev/null",
                tofile=f"b/{path}" if path in after else "/dev/null")
            chunk = "".join(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n"
                            for line in lines)
        except ValueError:
            chunk = f"Binary files a/{path} and b/{path} differ\n"
        size += len(chunk.encode())
        if size > MAX_WORKSPACE_BYTES * 3:
            raise ValueError("diff_byte_limit")
        chunks.append(chunk)
    return "".join(chunks)
