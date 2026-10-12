"""Runs INSIDE the sandbox VM, never in the API process: lists and prunes a repository that was just unpacked there.

Standard library only (the VM has Python 3). The API reads this file as text and uploads it (see `vm_repository.py`); tests import
it. Paths are handled as bytes, so a file name that is not valid UTF-8 is listed (as hex) and can still be removed.

  python3 vm_scan.py list <folder> <out.json>   every file, link and special file below <folder>: [{"k": kind, "p": path | "h": hex}]
  python3 vm_scan.py drop <folder> <plan.json>  removes the files a plan names ([{"p": path} | {"h": hex}]); folders are never removed

`kind` is "f" a regular file (a hard link is one too: git stores it as a separate file), "l" a symbolic link and "x" anything else.
"""
import json
import os
import stat
import sys


def scan(root: str) -> list[dict]:
    base = os.fsencode(root)
    found = []
    stack = [b""]
    while stack:
        relative = stack.pop()
        with os.scandir(os.path.join(base, relative) if relative else base) as entries:
            for entry in entries:
                path = os.path.join(relative, entry.name) if relative else entry.name
                info = entry.stat(follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    stack.append(path)
                    continue
                kind = "l" if stat.S_ISLNK(info.st_mode) else "f" if stat.S_ISREG(info.st_mode) else "x"
                found.append((path, kind))
    found.sort(key=lambda item: item[0])
    result = []
    for path, kind in found:
        try:
            result.append({"k": kind, "p": path.decode("utf-8")})
        except UnicodeDecodeError:
            result.append({"k": kind, "h": path.hex()})
    return result


def drop(root: str, plan: list[dict]) -> int:
    base = os.fsencode(root)
    removed = 0
    for item in plan:
        raw = bytes.fromhex(item["h"]) if "h" in item else item["p"].encode("utf-8")
        parts = raw.split(b"/")
        # A plan can only name something below the folder.
        if not raw or raw.startswith(b"/") or any(part in (b"", b".", b"..") for part in parts):
            continue
        target = os.path.join(base, raw)
        try:
            if os.path.isdir(target) and not os.path.islink(target):
                continue
            os.unlink(target)
            removed += 1
        except FileNotFoundError:
            pass
    return removed


def main(argv: list[str]) -> int:
    if len(argv) != 4 or argv[1] not in ("list", "drop"):
        print("usage: vm_scan.py list|drop <folder> <file>", file=sys.stderr)
        return 2
    command, root, path = argv[1:]
    if command == "list":
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(scan(root), handle)
    else:
        with open(path, encoding="utf-8") as handle:
            drop(root, json.load(handle))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
