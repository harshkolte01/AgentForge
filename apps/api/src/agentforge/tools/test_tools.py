"""Actual exit evidence is authoritative; test-count parsing remains advisory."""
import re
from pathlib import PurePosixPath
from decimal import Decimal
from .contracts import ToolOutcome


def observed_test_count(profile_id, stdout, stderr):
    text = stdout + "\n" + stderr
    patterns = (r"Ran\s+(\d+)\s+tests?\b",) if profile_id == "python_unittest" else (
        r"(?:^|\s)(\d+) passed\b", r"Tests:\s+(?:\d+ failed,\s*)?(\d+) passed", r"(?m)^# pass (\d+)\s*$")
    counts = [int(match) for pattern in patterns for match in re.findall(pattern, text)]
    return max(counts, default=0)


def protected_test_path(path):
    parts = PurePosixPath(path).parts
    name = parts[-1].lower()
    return (any(part.lower() in {"test", "tests", "__tests__", "scripts", ".github"} for part in parts)
        or name.startswith(("test_", "conftest")) or ".test." in name or ".spec." in name
        or name in {"pyproject.toml", "pytest.ini", "tox.ini", "setup.cfg", "setup.py", "package.json",
                    "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "requirements.txt", "uv.lock", "makefile"})


def baseline_test_paths(files, profile_id):
    if profile_id.startswith("python_"):
        return [path for path in files if PurePosixPath(path).name.startswith("test") and path.endswith(".py")]
    return [path for path in files if path.endswith((".js", ".mjs", ".cjs", ".ts", ".tsx")) and (any(token in path for token in (".test.", ".spec.", "__tests__/")) or any(part in {"test", "tests"} for part in PurePosixPath(path).parts))]


def qualification(files, baseline, payload):
    profile = payload["profile_id"]
    unchanged = all(files.get(path) == data for path, data in baseline.items() if protected_test_path(path))
    # New test/config shadowing is equally disallowed for automatic PASS.
    unchanged = unchanged and not any(protected_test_path(path) and path not in baseline for path in files)
    actual_success = payload.get("exit_code") == 0 and not payload.get("timed_out") and not payload.get("output_truncated") and payload.get("cleanup_confirmed") is True
    build = profile == "node_build"
    tests_observed = bool(baseline_test_paths(baseline, profile)) and payload.get("observed_test_count", 0) > 0
    return dict(exit_success=actual_success, baseline_test_integrity=unchanged,
        tests_observed=tests_observed if not build else None,
        qualified=bool(actual_success and unchanged and (build or tests_observed)),
        qualification_limit="Counts are parsed from untrusted output; independent review and preserved baseline tests are also required.")


def register_test_results(catalog, repository):
    async def results(args, execution):
        value = repository.evidence(execution.task, args.run_id)
        if value["kind"] not in {"test", "build"}:
            raise ValueError("reference is not test evidence")
        preview = {key: item for key, item in value.items() if key not in {"stdout", "stderr"}}
        preview.update(stdout=value.get("stdout", "")[:4000], stderr=value.get("stderr", "")[:4000])
        return ToolOutcome(status="ok", data=preview, evidence_ids=(value["id"],), cost_usd=Decimal(0))
    catalog.register("get_test_results", results, max_cost_usd=Decimal(0))
