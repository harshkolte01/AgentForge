"""Private immutable local storage addressed only by server-generated UUID keys."""

import hashlib
import os
from pathlib import Path
import re
import stat
from uuid import UUID

from agentforge.subagents.scope import ScopeDenied

_KEY = re.compile(r"^[0-9a-f-]{36}/[0-9a-f-]{36}\.md$")


class ArtifactStorage:
    def __init__(self, root: Path | str) -> None:
        # Construction is inert. Only a configured artifact write creates dirs.
        self.root = Path(root).absolute()
        self._posix = os.name == "posix" and hasattr(os, "O_NOFOLLOW") and os.open in os.supports_dir_fd
        if not self._posix and os.name != "nt":
            raise ValueError("Phase 3 research artifact storage requires a POSIX host with dir_fd and O_NOFOLLOW support")
        # Windows has no dir_fd or O_NOFOLLOW, so a local-development fallback is used instead: path based, it
        # refuses links and junctions below the root and never overwrites, but it cannot close the race that
        # the descriptor walk closes and cannot enforce owner-only modes. Production runs on POSIX.

    def _root_fd(self, *, create: bool = False) -> int:
        # Walk directory FDs from the root so an ancestor symlink replacement
        # cannot redirect creation/opening between a lexical check and use.
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        fd = os.open(self.root.anchor, flags)
        try:
            for name in self.root.parts[1:]:
                if name in ("", ".", ".."):
                    raise ScopeDenied("artifact storage configuration contains traversal")
                try:
                    child_fd = os.open(name, flags, dir_fd=fd)
                except FileNotFoundError:
                    if not create:
                        raise
                    try:
                        os.mkdir(name, mode=0o700, dir_fd=fd)
                    except FileExistsError:
                        pass
                    child_fd = os.open(name, flags, dir_fd=fd)
                os.close(fd)
                fd = child_fd
        except BaseException:
            os.close(fd)
            raise
        mode = os.fstat(fd).st_mode
        if mode & 0o077:
            os.close(fd)
            raise ScopeDenied("artifact storage requires owner-only directory permissions; set the configured directory mode to 0700")
        return fd

    @staticmethod
    def _parts(key: str) -> tuple[str, str]:
        if not _KEY.fullmatch(key):
            raise ScopeDenied("invalid artifact storage key")
        task, filename = key.split("/")
        if str(UUID(task)) != task or str(UUID(filename[:-3])) != filename[:-3]:
            raise ScopeDenied("invalid artifact storage key")
        return task, filename

    @staticmethod
    def _plain(path: Path, *, directory: bool) -> None:
        if path.is_symlink() or path.is_junction():
            raise ScopeDenied("artifact storage object is a link")
        if path.exists() and not (path.is_dir() if directory else path.is_file()):
            raise ScopeDenied("artifact storage object is invalid")

    def _write_local(self, key: str, content: bytes) -> None:
        directory, filename = self._parts(key)
        self.root.mkdir(parents=True, exist_ok=True)
        folder = self.root / directory
        folder.mkdir(exist_ok=True)
        self._plain(folder, directory=True)
        with open(folder / filename, "xb") as handle:  # exclusive: FileExistsError, like O_EXCL
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        self._plain(folder / filename, directory=False)

    def _read_local(self, key: str, sha256: str, max_bytes: int) -> bytes:
        directory, filename = self._parts(key)
        folder = self.root / directory
        self._plain(folder, directory=True)
        target = folder / filename
        self._plain(target, directory=False)
        with open(target, "rb") as handle:
            if os.fstat(handle.fileno()).st_size > max_bytes:
                raise ScopeDenied("artifact storage object is invalid")
            content = handle.read(max_bytes + 1)
        if len(content) > max_bytes or hashlib.sha256(content).hexdigest() != sha256:
            raise ScopeDenied("artifact content integrity check failed")
        return content

    def write(self, *, task_id: UUID, artifact_id: UUID, content: bytes, max_bytes: int = 2_000_000) -> tuple[str, str]:
        if len(content) > max_bytes:
            raise ValueError("artifact_size_limit")
        key = f"{task_id}/{artifact_id}.md"
        directory, filename = self._parts(key)
        if not self._posix:
            self._write_local(key, content)
            return key, hashlib.sha256(content).hexdigest()
        root_fd = self._root_fd(create=True)
        directory_fd = None
        try:
            try:
                os.mkdir(directory, mode=0o700, dir_fd=root_fd)
            except FileExistsError:
                pass
            directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
            if os.fstat(directory_fd).st_mode & 0o077:
                raise ScopeDenied("artifact task directory is not private")
            fd = os.open(filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=directory_fd)
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.fsync(directory_fd)
        finally:
            if directory_fd is not None:
                os.close(directory_fd)
            os.close(root_fd)
        return key, hashlib.sha256(content).hexdigest()

    def read(self, storage_key: str, sha256: str, *, max_bytes: int = 2_000_000) -> bytes:
        directory, filename = self._parts(storage_key)
        if not self._posix:
            return self._read_local(storage_key, sha256, max_bytes)
        root_fd = self._root_fd()
        directory_fd = None
        try:
            directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
            fd = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
            with os.fdopen(fd, "rb") as handle:
                info = os.fstat(handle.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_size > max_bytes:
                    raise ScopeDenied("artifact storage object is invalid")
                content = handle.read(max_bytes + 1)
            if len(content) > max_bytes or hashlib.sha256(content).hexdigest() != sha256:
                raise ScopeDenied("artifact content integrity check failed")
            return content
        finally:
            if directory_fd is not None:
                os.close(directory_fd)
            os.close(root_fd)
