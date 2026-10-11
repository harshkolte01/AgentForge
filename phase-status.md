# Implementation status

## GitHub delivery (branch, commits, pull request, checks, merge)

Updated 11 October 2026. A conversation's repository work now has a durable GitHub side: the branch AgentForge publishes
to, the commits it pushed and the pull request with its checks and reviews, driven from a Changes panel in the workspace and
by the agent's `create_pull_request` (which now adds commits to the same branch and pull request instead of opening a second
one). Pushes are fast-forward only and never overwrite files changed on GitHub; a lost answer is recovered, not repeated; merge,
close and mark-ready are human-only. Design, API contract, safety rules and code map: [delivery](docs/delivery.md). Migration
`0017_deliveries` (authored and checked on a disposable PostgreSQL: applies, reverses, no metadata drift).

Verified: the full API suite (840 tests) including new tests against a stateful fake GitHub, real git inside a temporary
checkout, the routes, and the whole workflow on a disposable PostgreSQL. **Not verified:** no live GitHub account, installation,
App permission change or webhook delivery, and no live sandbox (E2B) was used; the running local database was not migrated
and the API/worker were not restarted. The GitHub App needs *Checks: Read* and *Commit statuses: Read* (and optionally the
pull-request/check webhook events) for CI status; see [GitHub App setup](docs/github-app.md).

## Conversation-first workspace update

The compact conversation sidebar, automatic message routing, conversation-scoped
optional repository and consolidated Settings are implemented. Every message,
greetings included, is answered by a real model call through the bounded shared
router, which receives the recent conversation as context. Existing agents/history retain their internal ownership
scopes. Settings manages actual account/logout, model/limits, GitHub and all owned
custom agents without project controls. Old management routes redirect there.

Conversations and run details now have real URLs: `/chat`, `/chat/<id>` and
`/chat/<id>/runs/<task-id>` (see `docs/conversation-ux.md#urls`). Back/Forward,
deep links, reloads and open-in-new-tab work; `/workspace[?session=]` redirects.
Verified with lint, `next build`, the 14 backend OAuth/security tests and a headless
Chrome pass over navigation, history, deep links, not-found states and titles.

Settings API checks passed on a disposable PostgreSQL database through revision
`0008_automatic_conversation`, including cross-scope listing, pagination,
automatic creation, ownership denial, versions, exact-version test queuing and
soft disable. Focused frontend lint/types and isolated mocked-API desktop/mobile
Settings interaction/layout checks passed. The migration is authored and tested
in disposable databases. Final checks passed 84 unit tests, six disposable
workflows, and frontend lint/types/isolated production build. After a private
mode-0600 DB/artifact backup and idle-queue check, the serving local database was
upgraded to `0008_automatic_conversation` and the API/worker restarted; Next.js was
preserved. Old message/task/job counts stayed unchanged. A real authenticated
`hey` turn returned a short reply with no task; replay was idempotent and
task/job/run/budget-operation counts stayed unchanged. Its canary conversation was
archived. Metadata checks passed after excluding only the four separately owned
LangGraph framework tables; a disposable unknown-table check still detected
application drift. No live semantic model task, paid provider request or GitHub
account action was performed. See [conversation UX](docs/conversation-ux.md).

## Current release work — Phases 5–7

Updated 10 October 2026. Phase5 identity/GitHub delivery was published as
`8c5dfed1b389eaebe1cf5e93d1f82f4b4e45b389`, with remote SHA verified. Phase6
conversation/custom-agent/general/research and continuation code plus its UI were
published as `cb7bbfa7fb8c2f6a3aab4a20610d7841a9b0c633`, with remote SHA verified.
The Phase5 security follow-up is `93dd311`. Phase7 release
configuration, CI, evaluation runner and runbooks are implemented. **External
gates P5, P6 and P7 remain open.** Phase7 release commit `fa559e1` was
published and remote-verified. Its [hosted Release checks run](https://github.com/harshkolte01/AgentForge/actions/runs/38056877597)
completed successfully with both API and web jobs passing. This closes the hosted
CI check only; the other qualification gates below remain open.

The final unit suite passed 67 checks; the frozen release runner passed 65 unit
checks plus five disposable PostgreSQL
workflows: actual generated coding failure/repair, mocked identity/GitHub delivery
including lost-response reconciliation, sessions/custom versions/continuations,
research/general verification and read-only operational/aggregate reporting.
The frozen release receipt records `93dd311` plus the working inputs, with no source
changes during checks. Two later negative operational-error tests also passed.
Phase6 isolated publication checks passed 63 core tests, the coding/session/
nine-mode research-general fixtures, and frontend lint/types/build. Compose quiet validation, shell syntax and Python compilation passed.
Backup mocks preserve the originally running service set on success and failure.

After final code freeze and an idle-queue check, the authorized local handoff
created a readable mode-0600 PostgreSQL backup under ignored `.local/backups/`,
applied additive migrations `0006_identity_github` and `0007_sessions_agents` to
the configured development DB, and restarted the same loopback API8000, worker and
web3000 commands. Existing checkpoint tables were retained; a private mode-0700
artifact directory was created. API live/ready, programmatic local login/logout,
owner projects/sessions/agents/configuration, web routes and the web API proxy
passed HTTP checks. No task was submitted and no paid provider was called.
This is local HTTP evidence, not authenticated browser interaction or AWS hosting.
No AWS account or server, new public deployment, live GitHub authorization/publication, hosted trace,
Entire evidence, cfo.ai export, video or public submission is claimed. Container
build/start, authenticated browser/SSE and backup/restore qualification remain
open. See [Phase6](docs/phase-6.md), [Phase7](docs/phase-7.md),
[deployment](infra/deploy/README.md) and [remaining gates](docs/remaining-phases.md).

The older entries below describe their original implementation-time checks;
later receipts above supersede their statements about unrun local tests or
unapplied early migrations.

## Phase 4 — Sandbox coding executor

Implemented on 10 October 2026 with managed E2B as the execution boundary; AWS app
hosting is deferred. The live E2B execution slice is qualified on synthetic
fixtures. **Gate P4 remains open** for live model/GitHub end-to-end orchestration,
browser, tracing and worker recovery qualification.

| Step | Work | Status |
|---|---|---|
| 4.1 | Provider-neutral E2B execution, bounded snapshots and cleanup | Offline contracts and live restricted-template fixtures passed |
| 4.2 | Public GitHub issue and pinned commit import | Offline HTTP/archive/submission tests passed |
| 4.3 | Pinned source reads/search with hashes and line ranges | Focused tests passed |
| 4.4 | Task workspace text read/CAS writes, binary preservation and trusted diff | Disposable DB/fixture checks passed |
| 4.5 | Known command profiles, actual exits, bounded logs and durable budget intents | Actual E2B exits/build, overflow, timeout, cancellation and TTL passed |
| 4.6 | Immutable test/build evidence, baseline-test guards and missing/zero-test denial | Focused qualifier tests passed |
| 4.7 | General executor v2 through the shared SpawnService/runtime | Disposable DB/generated fixture passed |
| 4.8 | Independent read-only reviewer and verifier v2 | Both consumed actual diff/source/tests; assertion-only PASS rejected |
| 4.9 | At most two repairs with failure evidence | Real generated fixture failed then repaired/passed; failure bound and unknown-cleanup stop passed |

All **38 offline focused tests passed**. Migration `0005_phase4_coding` applied to a unique disposable PostgreSQL database
with zero ORM metadata drift. The full real parent graph/shared child runtime,
lease checks, budget ledger and private artifact storage ran with injected offline
model/GitHub/provider adapters. Only exact generated fixture programs executed
locally inside the fixture adapter: actual exits were 1 then 0; one repair and
both independent reviews produced a current-revision `COMPLETED` receipt.
Unsubstantiated reviewer PASS, a task that never repairs, and unconfirmed cleanup
all produced truthful `PARTIAL` receipts. Cleanup uncertainty stopped further
execution. CAS replay/stale hashes, traversal, binary edits, cross-task evidence,
unknown paid-operation replay, cancellation, immutable snapshots and missing-key
no-call behavior passed. The disposable database and artifacts were removed.

Existing v1 built-ins and research defaults remain unchanged. No GitHub writes,
AWS provisioning, self-hosted runtime, host execution of real repositories, live
LLM/tracing calls, authenticated browser qualification, commits or pushes. Live E2B setup used only synthetic fixtures; its selected template is configured in the ignored root `.env`, and final provider inventory had zero active sandboxes.
See [docs/phase-4.md](docs/phase-4.md) for bounds, template setup and remaining gates.

## Phase 3 — Planning, durable tasks and research

Implemented on 10 October 2026, initially source-reviewed only, then checked with
user-authorized light local validation. **Gate P3 has not passed:** real-provider,
trace, browser and recovery qualification remains outstanding.

| Step | Work | Status |
|---|---|---|
| 3.1 | Bounded registry-selected research parent graph and persisted plan | Offline model/real DB smoke passed |
| 3.2 | Atomic sequenced metadata events and authorized cursor SSE | History/cursor denials passed; streaming unverified |
| 3.3 | SKIP LOCKED queue, leases, heartbeat, fencing and conservative reconciliation | Sequential cancellation/reclaim/fencing passed; concurrency/recovery unverified |
| 3.4 | Async PostgreSQL parent checkpoints and explicit setup CLI | Disposable DB setup/checkpoint persistence passed; restart unverified |
| 3.5 | Invocation-bound Neatlogs callbacks, actual nested spans and global export mask | Implemented; no trace produced |
| 3.6 | Tavily search, pinned public fetch and durable sources | Fixture search/fetch persistence passed; live/network safety unverified |
| 3.7 | Deterministic citation/quote checks and private report artifact | Fixture quote/report download passed; goal coverage unresolved/PARTIAL |
| 3.8 | Local authenticated task API, events, status and workspace UI | API smoke/lint/types/build passed; browser interactions unverified |

Light validation passed locked dependency installation, all package imports,
application migrations through `0004_phase3_research` with zero metadata drift,
explicit PostgreSQL checkpoint setup and a real DB/real parent+child graph fixture
run. The API created an authenticated task and atomic queued job/first event;
three mock model calls and fixture search/fetch produced durable sources, a
`QUOTE_SUPPORTED` check, a truthful `PARTIAL` receipt, 21 sequential events and an
owner-only report download. Focused checks also passed validation/ownership
denials, bounded history replay/SSE cursor rejection, pre-execution cancellation,
expired lease reclaim/stale generation rejection and logout revocation. A real
import-time annotation-shadowing error in `TaskRepository` was fixed and rechecked.

The smoke used a unique disposable database, explicit settings without provider
or telemetry secrets, and injected offline adapters. Its database and artifacts
were removed; the configured application database was not migrated. The ignored
smoke harness remains in `.local`. No live model/search/tracing calls, valid SSE
stream, concurrent-worker/crash-recovery run, commits or pushes were performed.
Existing ignored environment data and credentials were preserved.

Frontend `pnpm lint`, Next route type generation, `tsc --noEmit` and `pnpm build`
passed after two task-detail lint fixes. Local read-only HTTP route checks passed;
these showed session-loading shells, not authenticated browser behavior. Browser
automation was unavailable, so hydration/auth/task/SSE interactions remain
unverified. `git diff --check` passed after the code and documentation updates.

[Phase 3 decisions, setup and remaining qualification](docs/phase-3.md) records
the exact scope and boundaries. Parent checkpoints resume eligible work and reuse
persisted completed child results. Unknown in-flight paid child operations retain
upper-bound charges and close PARTIAL, without automatic repeat calls. Broad
semantic acceptance criteria remain unresolved by quote matching; no false
COMPLETED research status is claimed. The current private artifact adapter is
POSIX-only, and local sessions are single-process/in-memory until Phase 5.

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
