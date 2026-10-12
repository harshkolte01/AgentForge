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
`LLM_SUPPORTS_JSON`, `LLM_CONTEXT_WINDOW_TOKENS`, `LLM_INPUT_USD_PER_MILLION`, `LLM_OUTPUT_USD_PER_MILLION`). Add `EXA_API_KEY` (web search; `TAVILY_API_KEY` as its fallback),
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
| `LLM_CACHED_INPUT_USD_PER_MILLION`, `LLM_CACHE_WRITE_USD_PER_MILLION` | none | The provider's prices for input read from its prompt cache and written to it. Unset, every input token is priced at the input rate (an overestimate for cached reads). The cached, written and plain parts are recorded per request either way. Above the long-context threshold, cached input is priced at the long-context input rate. |
| `LLM_PROVIDER` | `openai` | The only provider kind. |
| `LLM_MAX_OUTPUT_TOKENS` | `16384` | Room for a whole file in one tool call; a longer final answer is continued, not cut. |
| `LLM_TIMEOUT_SEC` | `180` | The longest wait for the next piece of a reply. |
| `LLM_MAX_RETRIES` | `4` | Retries for failures that produced no completion (rate limit, 5xx, connection refused). |
| `LLM_PARALLEL_TOOL_CALLS` | `true` | Let the model request several independent read-only tools in one turn. |
| `LLM_STRICT_TOOLS` | `false` | Send tool schemas in the provider's strict form; verify the model accepts it first. |

**Reasoning effort is not configured here.** How much the model thinks before it answers is each person's choice in the model
menu of the composer (None, Low, Medium, High, Extra high, Max for `gpt-6-luna`). It defaults to **High**, is kept on the
server per person (`users.reasoning_effort`, endpoint `/api/v1/settings/reasoning-effort`) and is read when a run starts, so a
change applies from the next message. A model that is not listed in `llm/effort.py` offers no choice and is called as before. A
leftover `LLM_REASONING_EFFORT` in an environment file is ignored. Higher efforts spend more of `LLM_MAX_OUTPUT_TOKENS` on
thinking (a reply that thinks through the whole allowance fails with a message that suggests a lower effort) and are given
longer to start answering than `LLM_TIMEOUT_SEC` (2x for High, 3x Extra high, 4x Max, capped at 15 minutes).

## Plugins and skills

| Setting | Default | Meaning |
| --- | --- | --- |
| `PLUGINS_ENABLED` | `true` | MCP servers a person connects on the Plugins page (sidebar → Plugins). |
| `PLUGIN_ENCRYPTION_KEY` | falls back to `GITHUB_TOKEN_ENCRYPTION_KEY` | At least 32 random characters; encrypts the tokens people save for plugins. Without either key only plugins that need no token can be connected. Changing it makes saved plugin tokens unreadable (people enter them again). |
| `PLUGIN_CALL_TIMEOUT_SEC` | `60` | Seconds one plugin call may take. |
| `PLUGIN_MAX_CONNECTIONS` | `20` | How many plugins one person may connect. |
| `PLUGIN_ALLOW_PRIVATE_NETWORK` | `false` | Development only: plugin servers on localhost or a private network, and plain http. Refused in production. |
| `PLUGIN_OAUTH_ENABLED` | `true` | Sign in to plugins with OAuth (the MCP authorization specification). Needs a plugin encryption key and an `API_PUBLIC_BASE_URL` the person's browser can reach; the return address is `API_PUBLIC_BASE_URL` + `/api/v1/plugins/oauth/callback`. |
| `PLUGIN_OAUTH_REGISTRATION` | `auto` | `auto` registers through a Client ID Metadata Document where the server supports one and the URL is https, else Dynamic Client Registration; `dcr` always uses the latter. |
| `SKILLS_ENABLED` | `true` | Playbooks the agent loads when a request fits (Plugins page > Skills). |
| `SKILL_MAX_PER_USER` | `50` | How many skills of their own one person may have (the built-in ones do not count). |

## Optional tools

| Setting | Default | Meaning |
| --- | --- | --- |
| `EXA_API_KEY` | none | Turns on web search with [Exa](https://exa.ai), the first choice. One key, or several separated by commas (`key1,key2`). |
| `EXA_SEARCH_TYPE` | `auto` | Exa's search type: `instant`, `fast`, `auto`, `deep-lite`, `deep` or `deep-reasoning` (the deep ones are slower and cost more). |
| `EXA_SEARCH_MAX_COST_USD` | `0.03` | The most one Exa search may cost. Exa reports each request's real cost, and that is what is recorded. |
| `TAVILY_API_KEY` | none | Turns on web search with Tavily: the fallback when `EXA_API_KEY` is also set, the only provider when it is not. One key or several, separated by commas. |
| `TAVILY_USD_PER_CREDIT` | none | Price of one Tavily credit, to account for the cost of a search. |
| `TAVILY_SEARCH_MAX_COST_USD` | `0.02` | The most one Tavily search may cost. |
| `RESEARCH_FETCH_TIMEOUT_SEC` | `15` | Page fetch timeout (max 30). |
| `RESEARCH_MAX_RESPONSE_BYTES` | `1000000` | Largest page body read. |
| `E2B_API_KEY` | none | Turns on the coding sandbox: bash, files and search. |
| `E2B_TEMPLATE_ID` | none | Optional; E2B's default image has git, Python and Node. See [infra/e2b](../infra/e2b/README.md). |
| `E2B_WEBHOOK_SECRET` | none | Signature secret (32+ characters) for E2B lifecycle events sent to `/api/v1/sandbox-usage/webhook`; see [sandbox-usage.md](sandbox-usage.md). Without it the webhook is unavailable. |
| `E2B_REQUEST_TIMEOUT_SEC` | `30` | Per request. |
| `E2B_SANDBOX_TTL_SEC` | `3600` | A sandbox is paused after this many idle seconds and resumed on use. |
| `SANDBOX_ARCHIVE_LOAD` | `true` | The sandbox downloads the selected repository itself from a short-lived GitHub archive link: fast, any size, no request per file. `false`, or any failure, makes the API upload a snapshot instead. |

**How a search picks its provider.** The agent has one `web_search` tool. With `EXA_API_KEY` set it goes to Exa; if Exa cannot
answer (rate limit, no credit, outage, timeout) and `TAVILY_API_KEY` is also set, the same search goes to Tavily, and the
failure is logged (`web search provider failed: provider=exa reason=...`) so a bad key does not hide behind the fallback. With
only one key set, that provider serves every search. When a search falls back after an Exa attempt that may have been charged,
the search is booked at the combined maximum of both providers rather than guessing the charge.

**Several keys for one provider.** Put them in the same setting, separated by commas: `EXA_API_KEY=key1,key2,key3` (spaces and
new lines also work; repeats are dropped; up to 20; a malformed key stops the app at start without printing it). A search uses
the first key that works. When a provider refuses a key for a reason that is about the key, the next key serves that same
search and the refused key is left alone for a while, so a dead key does not cost a request on every search:

| The provider says | Meaning | The key rests for |
| --- | --- | --- |
| Exa `401`/`403`, Tavily `401` | wrong, expired or blocked key | 30 minutes |
| Exa `402`, Tavily `432`/`433` | out of credit, or over its plan or pay-as-you-go limit | 10 minutes (top-ups are picked up) |
| `429` | rate limited | the provider's `Retry-After` (5 s to 5 min), else 30 s |

The provider refuses all of these before doing any work, so nothing is charged and no cost is booked for the refused attempts.
Anything that is not about the key (a timeout, a server error, a rejected request) does not try the other keys; the search goes
to the other provider instead. What the app has learned about a key is shared by every search in a worker process, and each
refusal is logged by position only (`web search key refused: provider=exa key=1/2 reason=exhausted; not used for 600s`), never
with the key itself. `python -m agentforge.doctor` shows how many keys each provider has. To add or replace a key, edit the
setting and restart the worker.

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
