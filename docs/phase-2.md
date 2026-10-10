# Phase 2 implementation and handoff

Implemented on 10 October 2026; source review only. This document records implementation decisions, not execution evidence. Tests, builds, live provider calls and migration application were deliberately not performed. Dependency lock resolution is separate maintenance and does not qualify the implementation. Gate P2 is **not empirically verified**.

## Scope and entry points

- `contracts/agent_definition.py` and `contracts/subagent.py`: closed Pydantic definitions, immutable grants, finite budgets and child request/result envelopes.
- `subagents/definitions/*.json`: researcher, general executor, code reviewer and verifier. Source files remain authoritative for built-ins.
- `subagents/registry.py`: explicit namespace/version lookup, canonical snapshot SHA256, private project versions, source-to-database integrity checks and soft disable.
- `subagents/scope.py` and `security/policies.py`: intersection of platform, user, task, parent and definition grants; exact resource checks and owner-only database authorization.
- `tools/catalog.py`, `tools/contracts.py` and `tools/gateway.py`: typed tool catalog and deterministic gateway before adapter execution.
- `subagents/prompt_builder.py`, `runtime.py` and `spawn_service.py`: bounded brief and reusable child execution.
- `llm/`: injectable provider boundary, usage bounds and explicit structured child output.
- `repositories/definitions.py`, `repositories/runs.py`, `subagents/budget.py`: durable definitions, immutable run identities, tool audit intent/outcomes and atomic budget accounting.
- `alembic/versions/0002_phase2_harness.py`: runs, calls, reservations, operations and ledger. The Phase 1 models and imports remain compatible. No `task_steps`, artifacts, task events or checkpoint tables are introduced.

## Researched decisions

1. Use a fresh short SQLAlchemy session and explicit transaction per reservation, accounting mutation and audit write. A committed tool intent must exist before the external adapter runs; commit failure denies execution. A session is not shared across concurrent child operations. This follows [SQLAlchemy session transaction guidance](https://docs.sqlalchemy.org/en/20/orm/session_basics.html).
2. Serialize every budget mutation on the parent `tasks` row using `SELECT ... FOR UPDATE`. PostgreSQL holds the row lock through transaction end and competing lockers observe the updated row. This closes the parallel read-then-reserve race. See [PostgreSQL explicit locking](https://www.postgresql.org/docs/current/explicit-locking.html).
3. Keep child executions per invocation; Phase 3 owns LangGraph checkpoint initialization and durable orchestration recovery. See [LangGraph subgraph persistence](https://docs.langchain.com/oss/python/langgraph/use-subgraphs).
4. Validate nested contracts and reconstruct boundaries rather than relying on shallow model immutability. See [Pydantic models](https://docs.pydantic.dev/latest/concepts/models/).
5. Treat fetched/context/model text as untrusted task data, bound its size, and enforce tool/resource policy independently of prompt instructions. See [OWASP prompt injection prevention](https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html).

## Persistence and accounting contract

A run stores the exact definition version UUID, normalized snapshot/hash, brief hash, effective grants/budget, reservation, terminal outcome and safe error code. Private brief text is not copied into the audit tables. Tool audit stores argument digests and sanitized metadata before effect; the gateway supplies sanitized outcomes, never raw secrets or exception text. Definition/version snapshots are append-only through the repository service; soft disable preserves historical references.

The parent ledger computes committed cost as `RESERVE + CHARGE - RELEASE`: while a slice is active its entire reserve is outstanding. Settlement atomically adds its final charge and releases the original reserve exactly once, under the task lock. A unique reservation/kind key and the settled marker prevent duplicate release. Operation caps are durable before invocation. Recorded measured costs replace operation caps; missing, interrupted or unknown usage retains the authorized upper bound and records `cost_source=authorized_upper_bound`. Unfinished operations are charged at their caps on settlement. Actual overrun remains charged and prevents subsequent spending; it is never erased to manufacture a nonnegative balance.

The legacy-style `agent_runs.measured_cost_usd` field stores the accounted settlement total and can include conservative caps. It is not proof of an actual provider invoice. Token usage priced through configured rates is an estimate based on reported usage; per-operation usage metadata records `configured_rates_from_reported_usage`, `adapter_reported_cost` or `authorized_upper_bound`. Reconcile actual provider billing separately.

A child slice is the minimum of the caller's already-intersected policy/definition/request ceiling and the task's positive remaining funds. Child operations additionally enforce that slice, model step count, tool count, depth exactly one, deadline and current task cancellation. The MVP permits one outstanding child per task and defaults to eight total child reservations, counting failures and abandoned starts. A crashed run keeps its reserve and blocks another child until explicit reconciliation. Task wall time starts at `tasks.created_at`; fixtures must create fresh tasks. The child deadline is the earlier of the task deadline and its own wall-time limit.

`BudgetService` and the repositories are synchronous short database operations called by the asynchronous harness. Integration should move blocking database work to an appropriate worker/thread boundary if parallel HTTP-driven execution is added. No network effect is kept inside a database transaction.

## Authorization and integration boundary

Phase 2 is an internal harness, not a public task/agent API. Trusted host code constructs the task context from authenticated server policy and injects the provider/tool adapters. The current database authorizer verifies the project owner and task creator. Sessions, project members, GitHub installation rights, sandbox ownership tables, artifacts/evidence storage, durable orchestration and a production composition root belong to later phases. Empty resource scopes grant nothing; absent adapters fail closed. Nonempty evidence/context references require a trusted ownership resolver; this phase does not fabricate artifact tables or IDs.

Current reference checks establish ownership, not semantic truth; Phase 3's verifier must establish whether evidence actually supports the claim. Repository policy narrows authorized repository IDs and validates lexical paths/refs. Pinned approved commits, branch/path allowlists, symlink containment and sandbox isolation remain the obligations of future GitHub/E2B adapters. Hostname fetch/browser tools deliberately fail closed until a DNS-pinned, redirect-validating adapter is installed. Standalone child state is not restart durable; Phase 3 must integrate checkpointing and operation reconciliation.

Do not expose context/grant constructors, arbitrary adapters, custom definition save ceilings or reservation services directly to model input or HTTP request JSON. Runtime agent definitions never provide executable Python, arbitrary shell commands or GitHub write rights.

## Required next verification

The following are handoff work, deliberately not executed in this implementation run:

1. Install locked dependencies and run import/type/static checks in the supported Python environment.
2. Review then apply revision `0002_phase2` to an isolated PostgreSQL database; compare metadata, exercise upgrade/downgrade and retain history across definition updates/disable.
3. Exercise strict contract fixtures; all four built-ins; namespace collisions; cross-project definitions; grants narrowing; context/evidence ownership; forbidden tools, paths, domains and command profiles.
4. Exercise child runtime with deterministic injected adapters: result validation and repair, model/tool timeouts, cancellation, output bounds, unavailable adapters, audit write failures and ambiguous effect outcomes.
5. Verify concurrent reservations under real PostgreSQL locking, duplicate settlement/usage, repeated operation keys, failed starts, unknown and over-cap costs, spawn/step/tool/depth/time limits and crash reconciliation.
6. After configuring a compatible model, run a real LLM child with authorized tools and demonstrate a forbidden call failing before effect. This is Gate P2; source review and mock adapters cannot qualify it.

`apps/api/examples/phase2_mock_child.py` is an authored, unexecuted manual composition example. Its default injected model and search adapter make no provider call; it creates fresh fixture records in the configured isolated database and returns an explicitly synthetic result. The optional `--real-model` path uses `ModelRouter.from_settings` and closes its provider client; search sources remain fixtures. Configure `LLM_MODEL`, `LLM_API_KEY`, `LLM_SUPPORTS_TOOLS`, `LLM_SUPPORTS_JSON`, `LLM_CONTEXT_WINDOW_TOKENS`, `LLM_INPUT_USD_PER_MILLION` and `LLM_OUTPUT_USD_PER_MILLION` before using that path. Output/token limits and provider timeout are separately configurable in settings. Neither path was run during implementation.

The following are future operator commands, **not commands executed during this implementation**. Use an isolated database in `DATABASE_URL` and review the unapplied migration first:

```sh
cd apps/api
uv sync --locked
uv run alembic upgrade head
uv run python examples/phase2_mock_child.py
# Optional paid real-model request, still with a synthetic search source:
uv run python examples/phase2_mock_child.py --real-model
```

For the optional real model path, set these through process environment or the ignored local `.env`; never commit the API key:

| Setting | Required configuration |
|---|---|
| `LLM_PROVIDER` | `openai` (the installed adapter) |
| `LLM_MODEL` | Operator-selected compatible model ID |
| `LLM_API_KEY` | Private provider credential |
| `LLM_SUPPORTS_TOOLS` | `true` after confirming model tool support |
| `LLM_SUPPORTS_JSON` | `true` after confirming JSON response support |
| `LLM_CONTEXT_WINDOW_TOKENS` | Verified model context limit |
| `LLM_INPUT_USD_PER_MILLION` | Current configured input token rate |
| `LLM_OUTPUT_USD_PER_MILLION` | Current configured output token rate |
| `LLM_MAX_OUTPUT_TOKENS` | Optional bounded output cap; default 8192 (a tool call can carry a whole file) |
| `LLM_TIMEOUT_SEC` | Optional provider request timeout; default 60 |
| `LLM_PARALLEL_TOOL_CALLS` | Optional; default `true`. Lets the model request several independent read-only tools in one turn |
| `LLM_STRICT_TOOLS` | Optional; default `false`. Sends tool schemas in strict structured-output form; confirm the model accepts it first |
| `LLM_MAX_RETRIES` | Optional; default 2. Retries for rate limits, 5xx and refused connections only (no completion was produced) |

Reservation ceilings use four decimal places in USD and round down to avoid exceeding the requested slice; requests below USD 0.0001 are rejected. Operation caps and charges round up to that precision to avoid unaccounted usage.

No tests or builds are claimed as passed. No migration is claimed as applied, and no live provider behavior is claimed as verified.
