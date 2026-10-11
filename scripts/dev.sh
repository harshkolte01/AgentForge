#!/usr/bin/env bash
# Run the API, the worker and the web app together with prefixed output. Ctrl-C stops all three.
# Usage: scripts/dev.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if command -v corepack >/dev/null 2>&1; then PNPM="corepack pnpm"; else PNPM="pnpm"; fi
pids=()

# Each process gets its own process group, so stopping it also stops whatever it spawned (uvicorn reloader, next).
start() {
  local name="$1" directory="$2"; shift 2
  set -m
  ( cd "$ROOT/$directory" && "$@" 2>&1 | while IFS= read -r line; do printf '[%s] %s\n' "$name" "$line"; done ) &
  pids+=("$!")
  set +m
}

stop() {
  trap - INT TERM EXIT
  echo "Stopping..."
  for pid in "${pids[@]}"; do kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null; done
  wait 2>/dev/null
  exit "${1:-0}"
}
trap stop INT TERM EXIT

start api    apps/api uv run uvicorn agentforge.main:app --host 127.0.0.1 --port 8000 --reload --no-access-log
start worker apps/api uv run python -m agentforge.workers.runner
start web    apps/web $PNPM dev

# Stop everything as soon as any one of them exits, so a crash is not hidden behind the others' output.
while :; do
  for pid in "${pids[@]}"; do
    if ! kill -0 "$pid" 2>/dev/null; then
      echo "A process exited; stopping the others."
      stop 1
    fi
  done
  sleep 1
done
