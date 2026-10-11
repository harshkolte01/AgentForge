#!/usr/bin/env bash
# One-time local setup: check tools, create .env from the template (only if absent), install dependencies.
# Usage: scripts/dev-setup.sh [--migrate]
#   --migrate  also apply database migrations to DATABASE_URL (never done otherwise).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MIGRATE=0
for argument in "$@"; do
  case "$argument" in
    --migrate) MIGRATE=1 ;;
    -h|--help) sed -n '2,4p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $argument" >&2; exit 2 ;;
  esac
done

failed=0
ok() { printf '  ok    %s\n' "$1"; }
missing() { printf '  MISS  %s\n' "$1"; failed=1; }

echo "Checking tools"
if command -v uv >/dev/null 2>&1; then ok "uv $(uv --version | awk '{print $2}')"; else missing "uv (https://docs.astral.sh/uv/)"; fi
if command -v node >/dev/null 2>&1; then
  node_major="$(node -p 'process.versions.node.split(".")[0]')"
  if [ "$node_major" -ge 24 ]; then ok "node $(node --version)"; else missing "node >= 24 (found $(node --version))"; fi
else
  missing "node >= 24"
fi
# The lockfile needs pnpm 10.34.6; corepack provides the exact version from apps/web/package.json.
if command -v corepack >/dev/null 2>&1; then
  PNPM=(corepack pnpm); ok "corepack (pnpm pinned by apps/web/package.json)"
elif command -v pnpm >/dev/null 2>&1; then
  PNPM=(pnpm); ok "pnpm $(pnpm --version) (corepack not found; the lockfile expects pnpm 10.34.6)"
else
  PNPM=(); missing "pnpm via corepack (ships with Node)"
fi
if command -v psql >/dev/null 2>&1; then ok "psql $(psql --version | awk '{print $3}')"; else missing "psql / PostgreSQL client (a local PostgreSQL server is required)"; fi
if [ "$failed" -ne 0 ]; then
  echo "Install the missing tools above, then rerun this script." >&2
  exit 1
fi

echo "Configuration"
if [ -e "$ROOT/.env" ]; then
  ok ".env exists (left unchanged)"
else
  ( umask 077 && cp "$ROOT/.env.example" "$ROOT/.env" )
  ok "created .env from .env.example (mode 0600): set DATABASE_URL and the model settings"
fi

echo "Installing dependencies"
(cd "$ROOT/apps/api" && uv sync --locked)
(cd "$ROOT/apps/web" && "${PNPM[@]}" install --frozen-lockfile)

if [ "$MIGRATE" -eq 1 ]; then
  echo "Applying migrations to DATABASE_URL"
  (cd "$ROOT/apps/api" && uv run alembic upgrade head)
fi

cat <<EOF

Next steps
  1. Edit .env: DATABASE_URL and the LLM_* block (every other setting: docs/configuration.md).
  2. Create the database tables:    (cd apps/api && uv run alembic upgrade head)   or rerun with --migrate
  3. Create your local sign-in:     (cd apps/api && uv run python -m agentforge.auth.bootstrap --write-env)
  4. Check everything:              (cd apps/api && uv run python -m agentforge.doctor)
  5. Start API, worker and web:     scripts/dev.sh   then open http://localhost:3000
EOF
