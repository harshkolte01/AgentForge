# Implementation status

## Step 1.2 — Python API package

Completed on 10 October 2026. Phase 1 as a whole is still incomplete.

Implemented:

- `apps/api/pyproject.toml`: installable `src/agentforge` package with only the seven requested direct runtime dependencies: FastAPI, Uvicorn, Pydantic v2, pydantic-settings, SQLAlchemy 2, Alembic and psycopg. The psycopg binary extra supplies the PostgreSQL client library on Windows.
- `apps/api/.python-version`: selects Python 3.12.
- `apps/api/src/agentforge/__init__.py`: package initialization.
- `apps/api/src/agentforge/settings.py`: typed settings for app environment, frontend/API URLs, allowed origins and database URL. Process environment overrides `apps/api/.env`, which overrides root `.env`. Paths do not depend on the working directory. Unrelated shared environment settings are ignored.
- Root `.env.example`: Phase 1 local configuration with development placeholders.
- Root `.gitignore`: ignores real `.env` files, virtual environments, caches and generated output; keeps `.env.example` and `uv.lock` trackable.
- `apps/api/uv.lock`: resolved dependency versions for reproducible installation.

Verification:

- `uv sync` in `C:\Coding\AgentForge\apps\api`: exit code 0; created `.venv`, built the editable API package and installed 24 packages including transitive dependencies.
- `uv sync --locked`: exit code 0.
- Smoke checks: API package and all seven dependency imports; default settings; `.env.example` loading; file and process environment precedence; comma-separated origin parsing; invalid environment rejection; cached settings; SQLAlchemy psycopg dialect initialization. All passed. No database connection was attempted.
- Git ignore checks: root and nested real `.env` files and `.venv` ignored; `.env.example` and `uv.lock` trackable. No real `.env` was created or tracked.

Local setup:

```powershell
# Optional: create local configuration at the repository root.
Copy-Item C:\Coding\AgentForge\.env.example C:\Coding\AgentForge\.env
cd C:\Coding\AgentForge\apps\api
uv sync
```

The missing uv tool was installed for the current user and its Scripts directory added to user PATH. Open a new terminal to pick up that PATH change. Existing terminals can use `py -3.12 -m uv sync`.

No blockers remain for Step 1.2. PostgreSQL infrastructure, migrations, API routes, web scaffolding and CI belong to the remaining Phase 1 steps.

## Step 1.3 — Next.js UI

Completed on 10 October 2026. Phase 1 as a whole is still incomplete.

Implemented:

- `apps/web/`: Next.js 16.4 App Router, React, TypeScript and Tailwind CSS, scaffolded with the official Next.js generator.
- `components.json`, `components/ui/` and `lib/utils.ts`: shadcn/ui configuration and generated Card, Badge and Button components. The home page uses Card and Badge.
- `app/page.tsx`: one simple home page with the AgentForge AI heading and a System health placeholder marked **Not checked**. It does not claim that the backend is healthy or make a health request.
- `app/layout.tsx` and `app/globals.css`: project metadata, shadcn theme and local system fonts; no Google font download is required.
- `next.config.ts`: rewrites `/api/:path*` to `http://localhost:8000/api/:path*` so browser API calls can use the Next.js origin.
- `package.json` and `pnpm-lock.yaml`: reproducible dependencies and explicit port 3000 for `pnpm dev` and `pnpm start`.
- `README.md`: local setup, verification commands and API proxy usage. No login, task chat or agent pages were added.

Verification:

- `pnpm install`: completed successfully after retrying the stalled registry download with IPv4 preferred. A subsequent `pnpm install --frozen-lockfile` also passed.
- `pnpm lint`: exit code 0.
- `pnpm build`: exit code 0, including TypeScript validation and successful static generation of the home page.
- `pnpm dev`: started successfully on port 3000. A live GET to `http://localhost:3000` returned HTTP 200 and contained AgentForge AI, System health and Not checked.
- API rewrite smoke check: GET preserved a nested `/api/...` path and query string; POST preserved the path, method and JSON body. Verification used a temporary local HTTP listener on port 8000, which was stopped afterward. This does not claim that FastAPI routes have been implemented.
- `git diff --check`: passed.

Local setup:

```powershell
cd C:\Coding\AgentForge\apps\web
pnpm install
pnpm dev
```

Open `http://localhost:3000`. The verified dev server was left running in a hidden background process; its ignored logs are in `apps/web/.local/`. Stop the existing dev server before launching another one on port 3000.

No blockers remain for Step 1.3. PostgreSQL infrastructure, migrations, FastAPI bootstrap/health routes and CI remain for later Phase 1 steps.

## Step 1.4 — Local PostgreSQL (no Docker)

Completed on 10 October 2026. Phase 1 as a whole is still incomplete.

Implemented:

- Used the existing, running Windows PostgreSQL 17.4 service at `localhost:5432`.
- Created the `agentforge` database using the existing local `postgres` account. No dedicated role was needed.
- Created the ignored root `.env` with the real local credentials in `DATABASE_URL` (SQLAlchemy psycopg dialect) and `LANGGRAPH_CHECKPOINT_DB_URL` (native PostgreSQL URI). Credentials are not recorded in tracked files.
- Added the checkpoint URL placeholder to `.env.example` and the corresponding typed field to API settings. Both database URLs are excluded from the settings representation.
- Added `infra/README.md` noting that local PostgreSQL is used. Docker Compose is skipped for local development, and the plan's repository tree, local-start instructions and Step 1.4 row reflect that choice.

Verification:

- PostgreSQL `psql -h localhost -p 5432 -U postgres -d agentforge -c "SELECT 1;"`: exit code 0; returned `1`.
- API virtual environment loaded root `.env` through Pydantic Settings. SQLAlchemy with psycopg connected to `agentforge`; `SELECT 1` returned `1`, and the account has CREATE permission on the public schema for the next migration step.
- Direct psycopg connection using the native checkpoint URL: `SELECT 1` returned `1`. No LangGraph checkpoint tables were initialized.
- `.env.example` still loads successfully without containing the real local credentials.
- `git check-ignore -v .env`: matched root `.gitignore`; `git ls-files -- .env`: empty. The real `.env` remains untracked.

Local verification (PowerShell; enter your local password when prompted):

```powershell
& "C:\Program Files\PostgreSQL\17\bin\psql.exe" -h localhost -p 5432 -U postgres -d agentforge -c "SELECT 1;"
```

No blockers remain for Step 1.4. Application migrations belong to Step 1.5; the API health endpoint and its database check belong to Step 1.6.

## Step 1.5 — Alembic migrations

Completed on 10 October 2026. Phase 1 as a whole is still incomplete.

Implemented:

- `apps/api/alembic.ini`, `alembic/env.py`, `alembic/script.py.mako` and migration usage documentation. Alembic reads `DATABASE_URL` from the API's Pydantic Settings, with no credentials in its tracked configuration.
- `src/agentforge/database/base.py`: SQLAlchemy 2 declarative base with stable constraint names.
- `src/agentforge/database/models.py`: models for exactly `users`, `projects`, `agent_definitions`, `agent_definition_versions` and `tasks`, following plan section 14.4.
- One initial revision, `0001_phase1_create_phase_1_tables.py`, including upgrade and reverse-order downgrade.
- UUID defaults, timezone-aware timestamps, JSONB definitions, foreign keys, version and thread uniqueness, scoped definition indexes, project owner/task lookup indexes, and status/budget checks. Added positive current-version and namespace/project association checks to close the nullable-project uniqueness gap; the representative plan DDL reflects those checks.
- No later-phase application tables or LangGraph checkpoint tables were created. PostgreSQL supplies the UUID function without an added extension.

Verification:

- First `uv run alembic upgrade head`: exit code 0; applied `0001_phase1` to the existing local `agentforge` database.
- Second `uv run alembic upgrade head`: exit code 0; no `Running upgrade` operation, already at head.
- `uv run alembic check`: exit code 0; no new upgrade operations detected.
- `psql` `\dt`: lists the five requested application tables plus Alembic's internal `alembic_version` table. Its stored revision is `0001_phase1`.
- Transactional ORM smoke checks: all five models persist, UUID/timestamp defaults load correctly, JSONB round-trips, and foreign-key, namespace, uniqueness, task-status and budget checks reject invalid rows. All smoke-test records were rolled back, and table row counts were unchanged.
- Real credentials remained in the ignored `.env`; no dependency additions were needed.

Local commands:

```powershell
cd C:\Coding\AgentForge\apps\api
uv run alembic upgrade head
uv run alembic current
uv run alembic check
& "C:\Program Files\PostgreSQL\17\bin\psql.exe" -h localhost -U postgres -d agentforge -c "\dt"
```

The equivalent `py -3.12 -m uv run alembic ...` commands were used for the verified upgrades because existing shells may not have refreshed the uv PATH entry. Both invoke the same installed uv tool.

No blockers remain for Step 1.5. FastAPI bootstrap/health routes and CI remain for Steps 1.6 and 1.7.

## Step 1.6 — FastAPI bootstrap + health

Completed on 10 October 2026. Phase 1 as a whole is still incomplete.

Implemented:

- `apps/api/src/agentforge/main.py`: FastAPI factory with lifespan-managed SQLAlchemy engine, CORS from `ALLOWED_ORIGINS` (default `http://localhost:3000`), and OpenAPI at `/docs`.
- `apps/api/src/agentforge/api/routes_health.py`: `GET /api/v1/health/live` returns `{ "status": "ok" }` without touching the database. `GET /api/v1/health/ready` pings PostgreSQL with `SELECT 1` and returns `ready` (HTTP 200) or `degraded` (HTTP 503). Checkpointer and worker are reported as `not_configured`; they are not required for Phase 1 readiness. Database errors are mapped to `database_unreachable` without leaking connection URLs.
- `apps/api/src/agentforge/database/engine.py`: pooled engine with `pool_pre_ping` and a 5-second connect timeout.
- `apps/web/components/system-health.tsx`: client fetch of `/api/v1/health/live` and `/api/v1/health/ready` through the Next.js rewrite. `app/page.tsx` renders that card. No login, task, or agent routes were added.

Verification:

- `GET http://localhost:8000/api/v1/health/live`: HTTP 200 `{"status":"ok"}`.
- `GET http://localhost:8000/api/v1/health/ready`: HTTP 200 `{"status":"ready","checks":{"database":{"status":"ok"},"checkpointer":{"status":"not_configured"},"worker":{"status":"not_configured"}}}`.
- `GET http://localhost:8000/docs`: HTTP 200 Swagger UI for AgentForge AI API, listing the two health operations.
- Next.js rewrite: `GET http://localhost:3000/api/v1/health/live` and `/ready` both returned the same payloads as the API.
- CORS: request with `Origin: http://localhost:3000` received `Access-Control-Allow-Origin: http://localhost:3000`.
- Browser on `http://localhost:3000`: System health showed Live and Ready with database ok.
- `pnpm lint` and `pnpm exec tsc --noEmit`: exit code 0.

Local commands (use `py -3.12 -m uv` if `uv` is not on PATH):

```powershell
cd C:\Coding\AgentForge\apps\api
py -3.12 -m uv run uvicorn agentforge.main:app --app-dir src --reload --port 8000
```

```powershell
cd C:\Coding\AgentForge\apps\web
pnpm dev
```

Open `http://localhost:3000`, `http://localhost:8000/api/v1/health/live`, `http://localhost:8000/api/v1/health/ready`, and `http://localhost:8000/docs`.

No blockers remain for Step 1.6. CI belongs to Step 1.7.

## Phase 2 — Core harness, registry and permissions

Implemented on 10 October 2026. Steps 2.1–2.9 are **implementation complete and source-reviewed only**. Their exit tests have not been run. Tests, builds, live provider calls and applying migrations were deliberately excluded from this run. **Gate P2 is not empirically verified.**

| Step | Work | Status |
|---|---|---|
| 2.1 | Definition, budget and tool contracts | Implemented; unverified |
| 2.2 | Four validated built-in definitions | Implemented; unverified |
| 2.3 | Source and database registry | Implemented; unverified |
| 2.4 | Effective permissions and resource checks | Implemented; unverified |
| 2.5 | Catalog and central tool gateway | Implemented; unverified |
| 2.6 | Standard bounded spawn brief | Implemented; unverified |
| 2.7 | Child runtime and spawn service | Implemented; unverified |
| 2.8 | Agent runs, tool calls and budget persistence | Implemented; unverified |
| 2.9 | Atomic reservations and finite execution bounds | Implemented; unverified |

Implemented files include `apps/api/src/agentforge/contracts/`, `subagents/`, `security/`, `tools/`, `llm/`, `repositories/`, the Phase 2 additions to `database/models.py`, revision `0002_phase2_harness.py`, explicit model settings/dependencies, and the authored manual example `apps/api/examples/phase2_mock_child.py`. [Phase 2 decisions and handoff](docs/phase-2.md) records researched sources, setup commands and required verification.

Source review addressed contract/namespace integrity, grant narrowing, adapter absence, task cancellation, exact brief/snapshot provenance, audit-before-effect ordering, duplicate usage/settlement, task-row reservation locking, currency precision and unknown-cost accounting. This is manual reasoning, not empirical validation. No automated checks, fixture execution, provider calls or database migration application are claimed as passed. Dependency lock resolution is maintenance only and does not qualify runtime behavior.

The API/web health behavior remains Phase 1 scope; no public task or agent endpoint was added. Trusted composition supplies owner-authorized task context and adapters. Sessions/membership, production GitHub/E2B/network adapters, semantic evidence verification and checkpoint/recovery integration remain later-phase work. The standalone child graph is not restart durable. Hostname fetch/browser calls fail closed until a pinned-DNS adapter exists. Budget operations preserve ambiguous costs and effects rather than authorizing automatic retries.

The pre-existing deletion of `.env.example` was preserved. No secrets, commits, later-phase application tables or applied migration were introduced by this implementation.
