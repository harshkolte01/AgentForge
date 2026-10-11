"""Reproducible offline release checks; never calls live providers or migrates app DB.

Run using `uv run --project apps/api python scripts/release_checks.py`.
Receipts and potentially private command logs stay in ignored .local/releases/.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


def source_state():
    """Fingerprint tracked/unignored inputs without exporting filenames or contents."""
    paths = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
                           cwd=ROOT, capture_output=True, check=True).stdout.split(b"\0")
    digest = hashlib.sha256()
    for raw in sorted(set(path for path in paths if path)):
        path = ROOT / os.fsdecode(raw)
        digest.update(raw + b"\0")
        if path.is_symlink():
            digest.update(b"symlink\0" + os.fsencode(os.readlink(path)))
        elif path.is_file():
            with path.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk)
        else:
            digest.update(b"missing")
        digest.update(b"\0")
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-web", action="store_true")
    args = parser.parse_args()
    directory = ROOT / ".local" / "releases" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8])
    directory.mkdir(parents=True, mode=0o700)
    commands = [("api-unit", [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"], ROOT / "apps/api")]
    for filename in ("phase5_identity_smoke.py", "phase6_sessions_smoke.py", "conversation_delivery_smoke.py", "phase7_operations_smoke.py", "settings_agents_smoke.py", "phase8_streaming_smoke.py", "activity_api_smoke.py", "conversation_title_smoke.py", "conversation_status_smoke.py", "plugins_smoke.py", "skills_smoke.py"):
        example = ROOT / "apps/api/examples" / filename
        if example.is_file():
            commands.append((example.stem, [sys.executable, str(example)], ROOT / "apps/api"))
        else:
            raise SystemExit("Required disposable fixture missing: " + filename)
    if not args.skip_web:
        corepack = shutil.which("corepack")
        pnpm = [corepack, "pnpm"] if corepack else [shutil.which("pnpm") or "pnpm"]
        version = subprocess.run([*pnpm, "--version"], cwd=ROOT / "apps/web", capture_output=True, text=True)
        if version.returncode or version.stdout.strip() != "10.34.6":
            raise SystemExit("Use pnpm 10.34.6 (corepack or npm install --global pnpm@10.34.6).")
        commands.extend((name, [*pnpm, *arguments], ROOT / "apps/web") for name, arguments in (
            ("web-conversation", ["exec", "node", "--experimental-strip-types", "--import", "./tests/alias.mjs", "--test", "tests/conversation.test.mjs", "tests/conversation-outbox.test.mjs", "tests/conversation-status.test.mjs"]),
            ("web-context", ["test:context"]), ("web-activity", ["test:activity"]), ("web-lint", ["lint"]), ("web-route-types", ["exec", "next", "typegen"]),
            ("web-types", ["exec", "tsc", "--noEmit"]), ("web-build", ["build"])))
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    dirty = bool(subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True, check=True).stdout)
    source_before = source_state()
    receipt = {"schema_version": 1, "revision": revision, "dirty_worktree": dirty,
               "source_sha256_before": source_before,
               "generated_at": datetime.now(timezone.utc).isoformat(), "mode": "offline-fixtures",
               "checks": [], "unverified": ["live-model/search/GitHub", "hosted-Neatlogs", "authenticated-browser/SSE", "container-build/start", "AWS-deployment", "Entire/cfo.ai", "video/public-submission"],
               "hackathon_gate_passed": False}
    if args.skip_web:
        receipt["unverified"].append("frontend (skipped in this receipt)")
    environment = dict(os.environ, NEXT_TELEMETRY_DISABLED="1")
    for name, command, cwd in commands:
        started = time.monotonic()
        path = directory / (name + ".log")
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as log:
            try:
                result = subprocess.run(command, cwd=cwd, env=environment, stdout=log, stderr=subprocess.STDOUT, timeout=600)
                code = result.returncode
            except subprocess.TimeoutExpired:
                code = 124
            except OSError:
                code = 127
        receipt["checks"].append({"name": name, "status": "passed" if code == 0 else "failed", "exit_code": code,
                                  "seconds": round(time.monotonic() - started, 2), "log_sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        print(name + ": " + receipt["checks"][-1]["status"], flush=True)
        if code:
            break
    receipt["requested_checks_passed"] = len(receipt["checks"]) == len(commands) and all(row["exit_code"] == 0 for row in receipt["checks"])
    receipt["source_sha256_after"] = source_state()
    receipt["source_changed_during_checks"] = receipt["source_sha256_after"] != source_before
    output = directory / "receipt.json"
    output.write_text(json.dumps(receipt, indent=2) + "\n")
    output.chmod(0o600)
    print("Receipt: " + str(output.relative_to(ROOT)))
    return 0 if receipt["requested_checks_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
