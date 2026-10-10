"""Shared, side-effect-free file helpers for the repository and workspace tools.

They turn the ways a file call goes wrong (no such path, binary content, a missing directory) into
typed ToolErrors with a suggestion, and give the search and listing tools one implementation of
scoping, context lines and depth-limited overviews.
"""

import difflib
from collections.abc import Mapping

from .errors import ToolError

MAX_SUGGESTIONS = 5
MATCH_TEXT_LIMIT = 300


def require_file(files: Mapping[str, bytes], path: str) -> bytes:
    if path in files:
        return files[path]
    base = path.rsplit("/", 1)[-1]
    same_name = [name for name in files if name.rsplit("/", 1)[-1] == base][:MAX_SUGGESTIONS]
    close = difflib.get_close_matches(path, list(files), n=MAX_SUGGESTIONS, cutoff=0.5)
    suggestions = list(dict.fromkeys([*same_name, *close]))[:MAX_SUGGESTIONS]
    detail = f"There is no file at '{path}'."
    detail += (" Closest real paths: " + ", ".join(suggestions) + ".") if suggestions else " Use list_repo_files or search_repo to find real paths."
    raise ToolError("FILE_NOT_FOUND", detail, message="file_not_found")


def require_text(files: Mapping[str, bytes], path: str) -> str:
    data = require_file(files, path)
    try:
        if b"\0" in data:
            raise UnicodeDecodeError("utf-8", b"", 0, 1, "binary")
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise ToolError("BINARY_FILE", f"'{path}' is a binary file, not text.",
                        message="binary_file_not_readable_as_text") from None


def in_directory(path: str, prefix: str | None) -> bool:
    return not prefix or path == prefix or path.startswith(prefix + "/")


def require_directory(files: Mapping[str, bytes], prefix: str | None) -> None:
    if prefix and not any(in_directory(path, prefix) for path in files):
        parent = prefix.rsplit("/", 1)[0] if "/" in prefix else ""
        siblings = sorted({path[len(parent) + 1 if parent else 0:].split("/", 1)[0] for path in files
                           if in_directory(path, parent or None)})
        close = difflib.get_close_matches(prefix.rsplit("/", 1)[-1], siblings, n=MAX_SUGGESTIONS, cutoff=0.5)
        detail = f"There is no directory '{prefix}'."
        if close:
            detail += " Similar names there: " + ", ".join(close) + "."
        raise ToolError("FILE_NOT_FOUND", detail, message="directory_not_found")


def line_matches(text: str, needle: str, *, case_sensitive: bool, context: int):
    """Yield (line_number, line, before, after) for each line containing the plain-text needle."""
    lines = text.splitlines()
    wanted = needle if case_sensitive else needle.casefold()
    for index, line in enumerate(lines):
        if wanted in (line if case_sensitive else line.casefold()):
            before = [item[:MATCH_TEXT_LIMIT] for item in lines[max(0, index - context):index]] if context else []
            after = [item[:MATCH_TEXT_LIMIT] for item in lines[index + 1:index + 1 + context]] if context else []
            yield index + 1, line, before, after


def match_record(path: str, number: int, line: str, before: list[str], after: list[str]) -> dict:
    record = {"path": path, "line": number, "text": line[:MATCH_TEXT_LIMIT]}
    if before:
        record["before"] = before
    if after:
        record["after"] = after
    return record


def overview(paths: list[str], directory: str, max_depth: int | None) -> list[str]:
    """Entries under a directory. With max_depth, deeper folders collapse to 'folder/ (N files)'."""
    if max_depth is None:
        return paths
    prefix = "" if directory == "." else directory.rstrip("/") + "/"
    entries: list[str] = []
    folders: dict[str, int] = {}
    for path in paths:
        parts = path[len(prefix):].split("/")
        if len(parts) <= max_depth:
            entries.append(path)
        else:
            folder = prefix + "/".join(parts[:max_depth]) + "/"
            if folder not in folders:
                folders[folder] = 0
                entries.append(folder)
            folders[folder] += 1
    return [f"{entry} ({folders[entry]} files)" if entry in folders else entry for entry in entries]
