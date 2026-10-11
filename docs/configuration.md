# Configuration reference

[`apps/api/src/agentforge/settings.py`](../apps/api/src/agentforge/settings.py) is the contract: every setting below is a field
there, read from the environment. Only a few settings need a value; the rest have defaults that are right for local development and
for the supplied deployment. Keep your `.env` to what you actually change.

| Where | File | Holds |
| --- | --- | --- |
| Local development | root `.env` (from [`.env.example`](../.env.example)) | database, sign-in, model, optional keys |
| Deployment, runtime | `/etc/agentforge/runtime.env` (from [`runtime.env.example`](../infra/deploy/runtime.env.example)) | what API and worker need in production |
| Deployment, operator | `/etc/agentforge/deploy.env` (from [`deploy.env.example`](../infra/deploy/deploy.env.example)) | Compose interpolation and database administration; never given to API or worker |
| Web app, local | `apps/web/.env.local` | `API_ORIGIN` only (see below) |

Precedence: the process environment, then `apps/api/.env`, then the root `.env`. An empty value is ignored (the default applies), so
`AUTH_MODE=` and a missing line mean the same. The API refuses to start on an invalid combination and names the fields, never their
values. Check a setup with `cd apps/api && uv run python -m agentforge.doctor`.

Keep `.env` private (`chmod 600`); it is git-ignored and excluded from Docker images. Changing a value needs a restart of the API and
the worker.

## What each environment needs

**Local development** (`.env`): `DATABASE_URL`; `AUTH_MODE`, `LOCAL_USER_ID` and `LOCAL_BOOTSTRAP_TOKEN` (filled by
`python -m agentforge.auth.bootstrap --write-env`); and the model block (`LLM_MODEL`, `LLM_API_KEY`, `LLM_SUPPORTS_TOOLS`,
`LLM_SUPPORTS_JSON`, `LLM_CONTEXT_WINDOW_TOKENS`, `LLM_INPUT_USD_PER_MILLION`, `LLM_OUTPUT_USD_PER_MILLION`). Add `TAVILY_API_KEY`,
`E2B_API_KEY`, `NEATLOGS_API_KEY` and `PLUGIN_ENCRYPTION_KEY` for the capability each one switches on.

**Production** (`runtime.env`): `APP_ENV=production`, the three https URLs, `DATABASE_URL`, `RESEARCH_STORAGE_PATH`,
`AUTH_MODE=github_app` with the GitHub App block, who may sign in, and the model block. Production refuses `local_single_user`, plain
http URLs and `PLUGIN_ALLOW_PRIVATE_NETWORK`.

## Core

| Setting | Default | Meaning |
| --- | --- | --- |
| `APP_ENV` | `development` | `development`, `test` or `production`. Production requires https URLs and `AUTH_MODE=github_app`. |
| `APP_BASE_URL` | `http://localhost:3000` | The origin people open in the browser. |
| `API_PUBLIC_BASE_URL` | `http://localhost:8000` | The origin the browser (and GitHub) reach the API at. |
| `ALLOWED_ORIGINS` | the origin of `APP_BASE_URL` | Comma-separated browser origins allowed to call the API. |
| `DATABASE_URL` | `postgresql+psycopg://agentforge:change-me@localhost:5432/agentforge` | SQLAlchemy URL of the PostgreSQL database. |
| `RESEARCH_STORAGE_PATH` | `<repo>/.local/artifacts` | Private artifact directory. Set it in a container (the supplied Compose mounts `/data/artifacts`). |

## Sign-in

| Setting | Default | Meaning |
| --- | --- | --- |
| `AUTH_MODE` | `disabled` | `disabled`, `local_single_user` or `github_app`. |
| `LOCAL_USER_ID` | none | The local identity, created by the bootstrap command. |
| `LOCAL_BOOTSTRAP_TOKEN` | none | At least 32 characters; typed once in the browser to sign in locally. |
| `LOCAL_SESSION_TTL_SEC` | `86400` | Local sign-in lifetime (max 7 days). |
| `SESSION_ABSOLUTE_TTL_SEC` | `604800` | GitHub sign-in ends after this long... |
| `SESSION_IDLE_TTL_SEC` | `86400` | ...or after this long without use. |
| `GITHUB_ALLOWED_LOGINS` | empty | Comma-separated GitHub logins who may sign in (case-insensitive); they get an account on first sign-in. Empty means no allowlist: existing accounts, plus anyone while `PUBLIC_SIGNUP_ENABLED=true`. |
| `PUBLIC_SIGNUP_ENABLED` | `true` | Let any GitHub user create an account. Set `false` in production and use the allowlist. |

## GitHub App (GitHub sign-in and private repositories; see [github-app.md](github-app.md))

| Setting | Default | Meaning |
| --- | --- | --- |
| `GITHUB_APP_ID`, `GITHUB_APP_SLUG`, `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET` | none | From the GitHub App's settings page. |
| `GITHUB_TOKEN_ENCRYPTION_KEY` | none | At least 32 random characters; encrypts stored GitHub tokens. Changing it signs every user out of GitHub. (`ENCRYPTION_KEY` is accepted as an alias.) |
| `GITHUB_WEBHOOK_SECRET` | none | At least 32 random characters; must match the App's webhook secret. |
| `GITHUB_OAUTH_CALLBACK_URL` | `API_PUBLIC_BASE_URL` + `/api/v1/auth/github/callback` | Set only if the callback is served elsewhere. |
| `ENABLE_GITHUB_WRITES` | `false` | Lets the agent open draft pull requests. |
| `GITHUB_API_VERSION` | `2026-03-10` | The GitHub REST API version header. |

## Model

An OpenAI-compatible provider. Verify the model's tool support, context window and current prices before opening it to people.

| Setting | Default | Meaning |
| --- | --- | --- |
| `LLM_MODEL`, `LLM_API_KEY` | none | Required to chat. |
| `LLM_SUPPORTS_TOOLS`, `LLM_SUPPORTS_JSON` | `false` | Set `true` when the model supports tool calls and JSON output; the agent needs tools. |
| `LLM_CONTEXT_WINDOW_TOKENS` | none | The model's context window (8192 to 2,000,000). |
| `LLM_INPUT_USD_PER_MILLION`, `LLM_OUTPUT_USD_PER_MILLION` | none | Current prices; cost accounting depends on them. |
| `LLM_LONG_CONTEXT_INPUT_USD_PER_MILLION`, `LLM_LONG_CONTEXT_OUTPUT_USD_PER_MILLION` | none | Only for models priced differently above a long-context threshold. |
| `LLM_REASONING_EFFORT` | provider default | `none`, `minimal`, `low`, `medium`, `high` or `xhigh`. |
| `LLM_PROVIDER` | `openai` | The only provider kind. |
| `LLM_MAX_OUTPUT_TOKENS` | `16384` | Room for a whole file in one tool call; a longer final answer is continued, not cut. |
| `LLM_TIMEOUT_SEC` | `180` | The longest wait for the next piece of a reply. |
| `LLM_MAX_RETRIES` | `4` | Retries for failures that produced no completion (rate limit, 5xx, connection refused). |
| `LLM_PARALLEL_TOOL_CALLS` | `true` | Let the model request several independent read-only tools in one turn. |
| `LLM_STRICT_TOOLS` | `false` | Send tool schemas in the provider's strict form; verify the model accepts it first. |

## Plugins and skills

| Setting | Default | Meaning |
| --- | --- | --- |
| `PLUGINS_ENABLED` | `true` | MCP servers a person connects in Settings > Plugins. |
| `PLUGIN_ENCRYPTION_KEY` | falls back to `GITHUB_TOKEN_ENCRYPTION_KEY` | At least 32 random characters; encrypts the tokens people save for plugins. Without either key only plugins that need no token can be connected. Changing it makes saved plugin tokens unreadable (people enter them again). |
| `PLUGIN_CALL_TIMEOUT_SEC` | `60` | Seconds one plugin call may take. |
| `PLUGIN_MAX_CONNECTIONS` | `20` | How many plugins one person may connect. |
| `PLUGIN_ALLOW_PRIVATE_NETWORK` | `false` | Development only: plugin servers on localhost or a private network, and plain http. Refused in production. |
| `PLUGIN_OAUTH_ENABLED` | `true` | Sign in to plugins with OAuth (the MCP authorization specification). Needs a plugin encryption key and an `API_PUBLIC_BASE_URL` the person's browser can reach; the return address is `API_PUBLIC_BASE_URL` + `/api/v1/plugins/oauth/callback`. |
| `PLUGIN_OAUTH_REGISTRATION` | `auto` | `auto` registers through a Client ID Metadata Document where the server supports one and the URL is https, else Dynamic Client Registration; `dcr` always uses the latter. |
| `SKILLS_ENABLED` | `true` | Playbooks the agent loads when a request fits (Settings > Skills). |
| `SKILL_MAX_PER_USER` | `50` | How many skills of their own one person may have (the built-in ones do not count). |

## Optional tools

| Setting | Default | Meaning |
| --- | --- | --- |
| `TAVILY_API_KEY` | none | Turns on web search. |
| `TAVILY_USD_PER_CREDIT` | none | Price of one Tavily credit, to account for the cost of a search. |
| `TAVILY_SEARCH_MAX_COST_USD` | `0.02` | The most one search may cost. |
| `RESEARCH_FETCH_TIMEOUT_SEC` | `15` | Page fetch timeout (max 30). |
| `RESEARCH_MAX_RESPONSE_BYTES` | `1000000` | Largest page body read. |
| `E2B_API_KEY` | none | Turns on the coding sandbox: bash, files and search. |
| `E2B_TEMPLATE_ID` | none | Optional; E2B's default image has git, Python and Node. See [infra/e2b](../infra/e2b/README.md). |
| `E2B_REQUEST_TIMEOUT_SEC` | `30` | Per request. |
| `E2B_SANDBOX_TTL_SEC` | `3600` | A sandbox is paused after this many idle seconds and resumed on use. |

### Tracing (Neatlogs; see [neatlogs.md](neatlogs.md))

| Setting | Default | Meaning |
| --- | --- | --- |
| `NEATLOGS_API_KEY` | none | A key turns tracing on for the worker. |
| `NEATLOGS_WORKFLOW_NAME` | `agentforge-ai` | The workflow the traces belong to. |
| `NEATLOGS_ENDPOINT` | the SDK default | Base URL only, for an EU or self-hosted project (for example `https://eu.ingest.neatlogs.com`). |
| `NEATLOGS_TAGS` | empty | Comma-separated labels on every trace, to filter and compare runs (`baseline`, `prompt-v3`). `env:` and `release:` are automatic. |
| `NEATLOGS_SAMPLE_RATE` | `1` | Share of traces exported; 1 keeps every run. |
| `NEATLOGS_CAPTURE_CONTENT` | `false` | `false`: structure, timing, tokens, cost and status only. `true`: prompts, answers and tool data are sent too (known secret shapes masked, long values cut), so private conversations reach Neatlogs. Choose deliberately. |

## Worker and conversations

| Setting | Default | Meaning |
| --- | --- | --- |
| `WORKER_LEASE_SEC`, `WORKER_HEARTBEAT_SEC`, `WORKER_POLL_SEC` | `90`, `10`, `2` | Task lease, heartbeat and poll intervals. |
| `WORKER_CONCURRENCY` | `4` | Tasks one worker runs at once. |
| `WORKER_DRAIN_SEC` | `25` | Seconds a stopping worker lets running tasks finish; keep it below the Compose `stop_grace_period` (120 s). |
| `DATABASE_POOL_SIZE`, `DATABASE_MAX_OVERFLOW` | `10`, `10` | Each API and worker process holds up to their sum in database connections. |
| `CONVERSATION_QUEUE_ENABLED` | `true` | Accept messages sent while a reply is still running and run them in order. `false` refuses them until the reply finishes. |
| `CONVERSATION_SUMMARIES_ENABLED` | `true` | Keep a running summary of the conversation for later turns. |
| `CONVERSATION_TITLES_ENABLED` | `true` | Name a new conversation with one small model call before its first reply. |

## Web app and release checks

* `API_ORIGIN` (in `apps/web/.env.local`, default `http://localhost:8000`) is the server-side origin the Next.js `/api` rewrite
  forwards to. Deployments route `/api/` straight to FastAPI on the same origin.
* `SITE_URL` (in `deploy.env`) is the public origin, baked into the web build for social cards, the sitemap and robots.
* `TEST_DATABASE_ADMIN_URL` is used only by `scripts/release_checks.py` and the smoke checks, to create and drop disposable test
  databases.
