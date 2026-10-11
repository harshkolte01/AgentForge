# Phase 3 implementation and operator handoff

Implemented on 10 October 2026 and initially source-reviewed only. A subsequent
user-authorized light validation passed the local checks below. Gate P3 remains
unverified: no live model/search/tracing request or browser interaction was run.
No commit or push was made.

## Light validation (10 October 2026)

- `UV_HTTP_TIMEOUT=120 uv sync --locked` passed after the first download attempt
  timed out. All `agentforge` package modules then imported without invoking CLI
  entry points. This found and fixed a `TaskRepository` import error: its `list`
  method shadowed the built-in used in later annotations; annotations are now
  postponed.
- `uv run --locked python ../../.local/phase3_backend_smoke.py` passed from
  `apps/api`. The ignored, temporary harness created a uniquely named disposable
  PostgreSQL database using the local OS-user administrator because the configured
  application role lacks `CREATEDB`. All application migrations through
  `0004_phase3_research` applied, Alembic metadata comparison returned no drift,
  and `AsyncPostgresSaver.setup()` succeeded there. The configured application
  database was not migrated or given checkpoint tables.
- With explicit test settings and OpenAI, Tavily and Neatlogs secrets set to
  `None`, the real API session/task/job/event path and real PostgreSQL parent and
  child LangGraph execution passed. An injected model made exactly three fixture
  calls; fixture search plus patched DNS/HTTP fetch produced two durable source
  records. Verification found `QUOTE_SUPPORTED`, persisted a private downloadable
  report, and correctly finalized `PARTIAL` because goal coverage remains
  unresolved. No paid provider or telemetry export was invoked.
- API checks passed for anonymous/invalid-session rejection, session cookie flags,
  task validation, owner/project/artifact boundaries, atomic queued job and first
  event, plan/run/source/report reads, 21 sequential timeline events, bounded
  history cursor replay, invalid/ahead SSE cursor rejection, manual resume and
  terminal cancellation conflicts, logout revocation, and cancellation before
  model execution. An expired fixture lease was reclaimed with a new generation;
  a stale generation write was rejected. This was a sequential smoke, not a
  concurrent-worker or crash-recovery test. Valid SSE streaming was not exercised.
- `pnpm lint`, `pnpm exec next typegen`, `pnpm exec tsc --noEmit`, and `pnpm build`
  passed in `apps/web`. Lint found two task-detail issues, which were fixed before
  the successful rerun. Read-only local HTTP checks returned 200 for `/`,
  `/workspace` and a UUID task route's session-loading shell, and 404 for an
  invalid task ID. No browser was available for hydration/authentication/task/SSE
  interaction checks. `git diff --check` passed after the code and documentation
  updates.

The disposable database and its fixture artifact directory were removed after
the successful smoke. The ignored harness remains in `.local` for inspection;
no fixture rows or report files remain in an existing application database.

## Implemented workflow

The LangGraph parent plans a single read-only research step, chooses an eligible
immutable definition from the current registry using its research output schema,
effective permissions and actually registered search capability, delegates through
the existing shared `SpawnService`, verifies durable sources, then finalizes.
The built-in researcher is preferred among eligible definitions. There is no
second role engine, coding execution or arbitrary model-generated workflow DAG.
Plan criteria and dependency references are persisted in `task_steps`; checkpoints
hold small task/step references rather than source bodies or entire reports.

Task creation, its queued job and first per-task event commit together. Task-row
locks serialize monotonically increasing event sequences with state changes.
The task API validates current owner/project access, exposes bounded pagination,
supports cooperative cancellation, and streams cursor-based SSE with periodic
reauthorization, bounded batches and keepalive. Actual model, tool and child
progress generates metadata-only timeline events. The `/workspace` UI displays
the durable plan, runs, final status, source checks and private report downloads.

Workers claim ready/expired jobs using PostgreSQL `FOR UPDATE SKIP LOCKED`, use
generation-fenced leases, heartbeat during graph reads/execution, and verify the
current lease, ownership, cancellation and unchanged task grants at model/tool
boundaries. The parent graph has the task's remaining wall-time deadline. Child
cancellation is awaited before terminal job completion or lease release. SIGTERM
stops further claims and drains the current bounded task; forced process death
is recovered after lease expiry.

The parent uses `AsyncPostgresSaver` and `thread_id = task UUID`. A pending saved
graph resumes with `ainvoke(None, config)`. Each step stores its fixed child run
ID before reserving budget or calling a provider. If a child result committed
before a parent checkpoint, recovery reuses the persisted result without another
paid call. A child interrupted with an unknown outcome is not replayed: tool
intents become UNKNOWN, unknown operations retain authorized upper-bound charges,
reservations are settled and the task closes PARTIAL. Settled reservations with
unfinished run receipts are reconciled too. Child internals retain Phase 2's
per-invocation isolation; this slice does not promise resumable child model loops.

Tavily search installs only when its API key is configured; missing credentials
produce an explicit configuration gap. Search sources are unverified snippets.
Fetch validates HTTPS destinations, task-owned discovery, public DNS pinning,
redirects, response sizes/content, and stores immutable source hashes and evidence.
Discovery is enabled only in trusted research composition. Failed sources cannot
become supporting evidence. Tavily basic search is bounded by an explicit per-call
USD cap; when an observed-credit conversion is unavailable, accounting retains
the authorized upper bound rather than inventing actual provider billing.

Verification checks actual specialized child claims against task-owned fetched
snapshots and records missing, unreachable, unsupported and unresolved claims in
a downloadable report. Exact quotation coverage is not semantic truth, source
reliability, relevance or goal completeness. This deterministic slice leaves
broad acceptance criteria unresolved and therefore reports PARTIAL; it must not
claim COMPLETED merely because an unrelated quote exists. Reports are private
immutable files under the ignored `.local/artifacts` directory with hashes and
owner-only access. Current filesystem storage requires POSIX `dir_fd` and
`O_NOFOLLOW`; Windows artifact execution is not implemented or qualified.

Neatlogs initializes once per worker when configured. Its LangChain handler is
attached at graph invocation; actual child, model and tool calls create nested
spans. Custom decorator input/output capture and SDK log capture are disabled.
A global export mask permits structural trace fields, workflow identifiers,
opaque IDs and numeric metrics while removing prompts, responses, source text,
URLs, exceptions, events and arbitrary attributes. Pinned SDK source review
confirmed mask shape, resource preservation and explicit error-description
replacement. Flush and shutdown happen once at worker teardown. No actual trace
tree, export, SDK Doctor or dashboard evidence has been produced.

## Configuration and explicit operator setup

Python 3.12 or 3.13 is required because current Neatlogs 1.4.26 declares Python
`>=3.10,<3.14`. The package's base dependencies plus existing `langchain-core`
provide its callback; its broad `[langchain]` extra is intentionally unnecessary.

The existing ignored `.env` was preserved. Only missing, non-secret settings for
the existing `gpt-6-luna` model were added: tool/JSON flags, standard pricing
`0.10` input / `0.50` output USD per million, `LLM_REASONING_EFFORT=none`, and a
conservative `LLM_CONTEXT_WINDOW_TOKENS=262144`. That is an operational cap below
the documented long-context price threshold, not the model's full 1,050,000-token
window. The adapter explicitly requests standard/default service tier and rejects
Luna tool calls without reasoning `none` or this short-context pricing cap.
Existing settings were not overwritten; credentials were not printed or tracked.
No API request verified the configured account's model access or token usage.

The final presence-only configuration review found `TAVILY_API_KEY`,
`NEATLOGS_API_KEY`, and required model settings configured after the user added
the Tavily key. This verifies presence only; no credential or provider was tested.
Optional `TAVILY_USD_PER_CREDIT` must reflect the account's purchased pricing;
no guessed conversion was added. `NEATLOGS_API_KEY` enables telemetry. Model identity,
credentials, verified features, context/output ceilings and current rates remain
operator configuration. `/api/v1/configuration` reports presence only and never
returns secrets. Health readiness checks the database; worker/checkpointer are
honestly reported as not observed by the API.

After reviewing all unapplied revisions and choosing the intended local database:

```sh
cd apps/api
uv sync --locked
uv run alembic upgrade head
uv run python -m agentforge.auth.bootstrap
```

(The LangGraph Postgres checkpointer and its `agentforge.workers.checkpoints setup`
command have since been removed; the runtime keeps its state in application tables.)

The last command explicitly creates a local user/project and prints private
configuration for the root `.env`: `AUTH_MODE=local_single_user`, `LOCAL_USER_ID`
and a random `LOCAL_BOOTSTRAP_TOKEN`. Keep that token private and out of frontend
environment variables. Local auth defaults disabled and is forbidden in production.
It requires loopback peer/Host plus permitted Origin for mutations and uses an
opaque HttpOnly SameSite Strict session cookie. Sessions are bounded in-memory,
expire by default after one hour and reset on API restart. Use one API process;
durable sessions/GitHub auth belong to Phase 5.

```sh
# separate terminals, after configuration
cd apps/api
uv run uvicorn agentforge.main:app --host 127.0.0.1 --port 8000 --no-proxy-headers
uv run python -m agentforge.workers.runner
```

```sh
cd apps/web
pnpm install --frozen-lockfile
pnpm dev
```

Open `http://localhost:3000/chat`, unlock with the local bootstrap token,
then submit a bounded research task. Starting the worker with queued tasks can
incur real configured provider costs. The worker never applies migrations or
sets up framework tables automatically. Manual task resume returns 409 because
recovery is lease/checkpoint-driven and ambiguous paid calls must not be repeated.

## Remaining qualification

The light checks above cover imports, isolated migrations/schema comparison,
builds, sequential cancellation/fencing, bounded history replay and report delivery.
Remaining Phase 3 qualification includes cancellation/fencing under concurrent
workers, process restart at each boundary, valid SSE reconnect/replay and session
expiry, source/network SSRF and filesystem rejection cases, missing-provider
behavior, budget exhaustion, browser interactions, real source-backed model
delivery, and an actual sanitized Neatlogs trace tree.
No production, Windows, hosted CI, packaged or live-provider readiness is claimed.

## Official sources reviewed

- [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence)
- [Postgres async checkpointer setup](https://docs.langchain.com/oss/python/langgraph/add-memory)
- [Subgraph persistence](https://docs.langchain.com/oss/python/langgraph/use-subgraphs)
- [Neatlogs Python SDK](https://docs.neatlogs.com/sdk/python) and published 1.4.26 wheel source
- [Neatlogs PyPI metadata](https://pypi.org/project/neatlogs/)
- [OpenAI GPT-6 Luna model](https://developers.openai.com/api/docs/models/gpt-6-luna)
- [Tavily search API](https://docs.tavily.com/documentation/api-reference/endpoint/search)
- [HTTPX custom transports](https://www.python-httpx.org/advanced/transports/)

Additional source/security implementation details are recorded in
[Phase 3 research handoff](phase-3-research.md).
