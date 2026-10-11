# AgentForge AI

AgentForge is a chat with one honest agent: a system prompt and a set of tools.
It answers directly, researches the web, reads and edits code in a sandbox, and
replies in plain text. The reply is shown exactly as the model wrote it. Nothing
grades it, wraps it in a status or limits how long it works; a provider's own
error is shown as it is, and Stop ends a run. See [the agent](docs/agent.md).

Coding runs in a persistent E2B sandbox per conversation (internet on, no
secrets inside). In **Ask me** mode the agent stops before anything that changes
something (a command, a file write or edit, a pull request) and the chat shows an
Approve / Decline card; in **Approve for me** mode it never asks. Each task keeps
event history and a usage ledger.

The workspace supports persistent conversations and immutable custom-agent
versions. GitHub App sign-in uses durable, revocable sessions and retains the
user's own repository permissions. See [implementation status](phase-status.md)
and [qualification tracker](docs/remaining-phases.md) for tested boundaries.

Start a conversation and describe the work. The composer has an optional
repository selection that defaults to No repo, and the Ask me / Approve for me
switch. The sidebar focuses on conversations. Account, model/limits, GitHub
connections and custom-agent management live in Settings. See
[conversation behavior and migration](docs/conversation-ux.md) for the details.
Durable queued follow-ups, source-linked conversation summaries, original-input
retrieval and deployment requirements are documented in
[conversation reliability](docs/conversation-reliability.md).

```mermaid
flowchart LR
    Browser --> Proxy[Same-origin TLS proxy]
    Proxy --> Web[Next.js workspace]
    Proxy --> API[FastAPI identity and task API]
    API --> DB[(PostgreSQL tasks, events, leases)]
    Worker[Python worker] --> DB
    Worker --> Agent[Agent loop: model, tools, plain-text answer]
    Agent --> Providers[Configured model and research adapters]
    Agent --> E2B[Persistent E2B sandbox per conversation]
    Agent --> Approvals[Inline approval cards in the chat]
    Worker --> Artifacts[Private POSIX artifact storage]
    API --> Artifacts
    Worker --> GitHub[Draft pull requests, opened by the backend]
```

## Development

Use Python 3.12–3.13, uv, Node 24 and pnpm **10.34.6**. Dependencies are locked in
`apps/api/uv.lock` and `apps/web/pnpm-lock.yaml`. Local development uses the existing
PostgreSQL service rather than deployment Compose. Keep credentials in an ignored
root `.env`; [Settings](apps/api/src/agentforge/settings.py) is the configuration
contract. Never overwrite an existing `.env` with a template.

```sh
cd apps/api
uv sync --locked
# Explicitly target a development database before migrating.
uv run alembic upgrade head
uv run uvicorn agentforge.main:app --host 127.0.0.1 --port 8000 --no-access-log
```

In separate terminals:

```sh
cd apps/api
uv run python -m agentforge.workers.runner
```

```sh
cd apps/web
corepack pnpm install --frozen-lockfile
corepack pnpm dev
```

Open `http://localhost:3000`. `API_ORIGIN` is a server-only HTTP(S) origin used by
Next.js's API rewrite; it defaults to `http://localhost:8000`. Deployments route
`/api/` directly to FastAPI and keep browser requests on the same origin. Configure
local single-user access or [GitHub identity](docs/phase-5.md) explicitly before
using protected routes. Provider names, tool/JSON support, context/output limits
and current per-token prices must be configured; missing keys fail closed.
Set `E2B_API_KEY` to give the agent its coding sandbox (`E2B_TEMPLATE_ID` is
optional; E2B's default image has git, Python and Node).

## Checks and release

From the repository root:

```sh
uv run --project apps/api python scripts/release_checks.py
```

This runs unit checks, uniquely named disposable PostgreSQL fixtures and frontend
lint/types/build. It writes private logs and a machine-readable receipt under
ignored `.local/releases/`. The local PostgreSQL OS-user administrator needs
CREATE DATABASE, or use `TEST_DATABASE_ADMIN_URL` naming the loopback administrative
`postgres` database. Fixtures migrate and remove only their newly created database;
they do not upgrade the configured application database or call paid providers.
Use pnpm 10.34.6; a global pnpm 12 installation is incompatible with this lockfile.

[Phase 7](docs/phase-7.md) maps golden fixtures to actual evidence and open gates.
[Single-EC2 deployment](infra/deploy/README.md) includes TLS, nonroot app services,
least-privilege runtime database access, persistent encrypted disk requirements,
backups, restore and restart procedures. Container execution and AWS deployment
remain unqualified until run on a deployment host. This is a single-instance
release configuration, with downtime during coordinated backups and no HA claim.

[Hackathon evidence](docs/hackathon.md) provides the partner-evidence checklist,
demo script and business-plan assumptions. A local fixture receipt is not live
provider evidence or a completed submission.
