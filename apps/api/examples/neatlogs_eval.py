"""Fixed-prompt evaluation for Neatlogs before/after comparisons.

Runs the same five research prompts against a *running* local AgentForge (API on 127.0.0.1:8000, worker started with
`NEATLOGS_TAGS=<label>`), one new conversation each, waits for each to finish, then reads the per-task numbers back from
the application database (read-only): tool calls and their failures, model calls, tokens and the ledger cost. The worker
logs each task's `neatlogs_trace_id`; open those traces, or filter the dashboard by the tag, to see the same runs.

    cd apps/api
    uv run python examples/neatlogs_eval.py run baseline --out ../../.local/eval/baseline.json
    # change one thing, restart the worker with NEATLOGS_TAGS=improved, then
    uv run python examples/neatlogs_eval.py run improved --out ../../.local/eval/improved.json
    uv run python examples/neatlogs_eval.py compare ../../.local/eval/baseline.json ../../.local/eval/improved.json

It calls the configured model and search provider for real (a few cents per run). The local bootstrap token is read from
the root .env and never printed; it only works against a loopback API in local single-user mode.
"""
import argparse
import json
import time
import uuid
from pathlib import Path

import httpx
from sqlalchemy import text

from agentforge.database.engine import create_db_engine
from agentforge.settings import get_settings

ROOT = Path(__file__).resolve().parents[3]
PROMPTS = (
    "Open https://www.npmjs.com/package/react and tell me the latest version and weekly downloads.",
    "Summarise the top answer at https://stackoverflow.com/questions/38987 in 3 bullets.",
    "Read https://openai.com/api/pricing/ and list the per-million-token prices.",
    "Fetch https://www.g2.com/products/linear/reviews and summarise what reviewers complain about.",
    "Summarise https://docs.astral.sh/uv/ in 5 bullets.",
)
TERMINAL = {"COMPLETED", "FAILED", "CANCELLED", "PARTIAL"}


def bootstrap_token(env_file: Path) -> str:
    for line in env_file.read_text(encoding="utf-8").splitlines():
        if line.startswith("LOCAL_BOOTSTRAP_TOKEN="):
            return line.split("=", 1)[1].strip().strip("'\"")
    raise SystemExit(f"LOCAL_BOOTSTRAP_TOKEN is not set in {env_file}")


def run_prompts(label: str, api: str, env_file: Path, timeout: int) -> list[dict]:
    # The API accepts writes from the web app's origin only; this is the origin the browser would send.
    client = httpx.Client(base_url=api, timeout=60, headers={"Origin": "http://localhost:3000"})
    client.post("/api/v1/auth/session", headers={"Authorization": f"Bearer {bootstrap_token(env_file)}"}).raise_for_status()
    results = []
    for number, prompt in enumerate(PROMPTS, 1):
        session = client.post("/api/v1/sessions", json={"title": f"{label} eval {number}", "idempotency_key": str(uuid.uuid4())})
        session.raise_for_status()
        session_id = session.json()["id"]
        client.post(f"/api/v1/sessions/{session_id}/turns", json={
            "routing": "auto", "idempotency_key": str(uuid.uuid4()), "goal": prompt}).raise_for_status()
        started, task = time.time(), {}
        while time.time() - started < timeout:
            turns = client.get(f"/api/v1/sessions/{session_id}/turns").json()
            task = (turns[-1].get("task") if turns else None) or {}
            if task.get("status") in TERMINAL:
                break
            time.sleep(3)
        results.append({"n": number, "prompt": prompt, "task_id": task.get("id"), "status": task.get("status"),
                        "seconds": round(time.time() - started, 1)})
        print(f"[{label} {number}/{len(PROMPTS)}] {task.get('status')} in {results[-1]['seconds']}s", flush=True)
    return results


def measure(results: list[dict]) -> dict:
    engine = create_db_engine(get_settings().database_url)
    rows = []
    with engine.connect() as connection:
        connection.execute(text("SET TRANSACTION READ ONLY"))
        for result in results:
            tools = connection.execute(text("""SELECT tool_name, status, coalesce(error_code, '') AS code FROM tool_calls
                WHERE task_id = :task ORDER BY started_at"""), {"task": result["task_id"]}).mappings().all()
            # Tokens by how they are billed: cached reads and cache writes are parts of the input, thinking is part of the output
            # (older rows recorded none of the parts and count as 0 there).
            model = connection.execute(text("""SELECT count(*) AS calls,
                coalesce(sum((o.usage_json->'provider_usage'->>'input_tokens')::bigint), 0) AS input_tokens,
                coalesce(sum((o.usage_json->'provider_usage'->>'cached_tokens')::bigint), 0) AS cached_tokens,
                coalesce(sum((o.usage_json->'provider_usage'->>'cache_write_tokens')::bigint), 0) AS cache_write_tokens,
                coalesce(sum((o.usage_json->'provider_usage'->>'output_tokens')::bigint), 0) AS output_tokens,
                coalesce(sum((o.usage_json->'provider_usage'->>'reasoning_tokens')::bigint), 0) AS reasoning_tokens,
                coalesce(sum(o.cost_usd), 0) AS cost_usd
                FROM budget_operations o JOIN budget_reservations r ON r.id = o.reservation_id
                WHERE r.task_id = :task AND o.kind = 'model'"""), {"task": result["task_id"]}).mappings().one()
            failed = [t for t in tools if t["status"] in ("FAILED", "BLOCKED")]
            first_fetch = next((t for t in tools if t["tool_name"] == "fetch_webpage"), None)
            rows.append({**result, "tool_calls": len(tools), "failed_or_blocked": len(failed),
                         "first_fetch_refused": first_fetch is not None and first_fetch["code"] == "SCOPE_DENIED",
                         "failure_codes": sorted({t["code"] for t in failed}),
                         "model_calls": model["calls"], "input_tokens": int(model["input_tokens"]),
                         "cached_tokens": int(model["cached_tokens"]), "cache_write_tokens": int(model["cache_write_tokens"]),
                         "output_tokens": int(model["output_tokens"]), "reasoning_tokens": int(model["reasoning_tokens"]),
                         "cost_usd": float(model["cost_usd"]),
                         "tools": [f"{t['tool_name']}:{t['status']}{'(' + t['code'] + ')' if t['code'] else ''}" for t in tools]})
    engine.dispose()
    keys = ("seconds", "tool_calls", "failed_or_blocked", "model_calls", "input_tokens", "cached_tokens", "cache_write_tokens",
            "output_tokens", "reasoning_tokens", "cost_usd")
    total = {key: round(sum(row[key] for row in rows), 4) for key in keys}
    total["first_fetch_refused"] = sum(row["first_fetch_refused"] for row in rows)
    total["cache_hit_rate"] = round(total["cached_tokens"] / total["input_tokens"], 3) if total["input_tokens"] else 0.0
    completed = sum(row["status"] == "COMPLETED" for row in rows)
    total["cost_per_completed_task_usd"] = round(total["cost_usd"] / completed, 4) if completed else None
    return {"tasks": rows, "total": total}


def compare(before: dict, after: dict) -> None:
    for key in ("first_fetch_refused", "tool_calls", "failed_or_blocked", "model_calls", "input_tokens", "cached_tokens",
                "cache_write_tokens", "output_tokens", "reasoning_tokens", "cache_hit_rate", "cost_usd",
                "cost_per_completed_task_usd", "seconds"):
        if key not in before["total"] or key not in after["total"]:
            continue  # a run recorded before a column existed
        old, new = before["total"][key], after["total"][key]
        if old is None or new is None:
            continue
        change = f"{(new - old) / old * 100:+.0f}%" if old else "n/a"
        print(f"{key:28} {old:>12} -> {new:>12}  {change}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    run.add_argument("label")
    run.add_argument("--out", type=Path, required=True)
    run.add_argument("--api", default="http://127.0.0.1:8000")
    run.add_argument("--env-file", type=Path, default=ROOT / ".env")
    run.add_argument("--timeout", type=int, default=600)
    diff = commands.add_parser("compare")
    diff.add_argument("before", type=Path)
    diff.add_argument("after", type=Path)
    args = parser.parse_args()
    if args.command == "run":
        report = {"label": args.label, **measure(run_prompts(args.label, args.api, args.env_file, args.timeout))}
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report["total"]))
    else:
        compare(*(json.loads(path.read_text(encoding="utf-8")) for path in (args.before, args.after)))


if __name__ == "__main__":
    main()
