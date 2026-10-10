# Phase 4: managed sandbox coding

Phase 4 adds a coding task kind beside the existing default research workflow.
The application is intended for later AWS hosting; execution uses managed E2B.
There is no self-hosted execution service or host-shell fallback. GitHub writes,
branch creation and PR publication remain outside this phase.

Public GitHub reads resolve the submitted ref to a full immutable commit SHA
before queueing. The worker imports a bounded archive as bytes, captures actual
issue content when selected, and persists a task-owned logical workspace.
Source text is untrusted task data. Repository reads use the pinned baseline;
sandbox reads use the current revision. Binary assets remain byte-exact, while
text read/write tools reject binary files. Text reads provide bounded line ranges;
whole-file text writes are limited to 100,000 characters and the gateway byte cap. Exact-hash compare-and-swap protects
existing files; a null expected hash means create-only. Source files are never
checked out or executed on the API host.

The shared `SpawnService`, registry and child runtime execute version 2 of the
existing general executor, then independent read-only code reviewer and verifier.
Version 1 built-ins remain unchanged. Required tests run again under the parent
at the final workspace revision. Actual argv, profile, provider, revision, exit
code, timeout, output truncation and cleanup state are persisted with private log
artifacts. A failed required check can trigger at most two repair attempts.
Unknown paid outcomes or unconfirmed cleanup stop further execution.

Completion requires successful fresh revision-matching required tests, preserved
baseline tests and test configuration, successful independent reviews with
explicit acceptance checks, actual review reads of diff/source/test evidence,
and the obvious-secret check. Zero observed tests, missing tests, nonzero exits,
timeouts, output truncation, modified baseline tests/configuration, unsupported
review assertions or missing evidence produce an incomplete receipt. Test counts
are parsed from untrusted output and are advisory; they are not independent proof
of semantic correctness. Build evidence is distinct from test evidence.

Workspace revisions and diffs come from database snapshots outside generated
`.git` state. Import bounds are 2,000 files, 1 MB per file and 10 MB total; a task
can retain at most 50 snapshots and 100 MB of encoded snapshot data. Diff artifacts
are capped at 2 MB. This controlled slice does not preserve executable mode bits,
or support symlinks, submodules, LFS or arbitrary dependency installation. Package
scripts run without network access and with a fixed prepared toolchain; unsupported
repositories receive actual failures or partial verification. Use small known
repositories whose dependencies are preinstalled in the qualified template.

Every remote command receives a fresh E2B sandbox containing the immutable input
snapshot. Generated programs run as a restricted non-root user against read-only
source; bounded scratch/build paths are disposable. Source changes made by a
program are never imported as agent edits. The sandbox is terminated after each
command, with a finite kill-on-timeout TTL as a backstop. See
[infra/e2b/README.md](../infra/e2b/README.md) for the template
and its explicit operator qualification requirements.

Sandbox operation caps are reserved before allocation and include allocation,
command lifetime and cleanup. Missing measured cost retains the authorized upper
bound. Both tool-driven commands and parent-required checks use this accounting.
Durable operation intents prevent automatic retry after an unknown paid outcome.
Database writes check the current task ownership, cancellation and worker lease;
async boundaries also recheck unchanged task grants. Coding has a fixed ceiling of
32 reservations (at most 20 planned parent/child slices); research retains its
original eight-child ceiling. The command lifecycle timeout is computed from
command timeout plus four request timeouts and ten seconds, capped at 330 seconds,
within the existing child/task wall-time ceilings. Parent checks also enforce their
reservation deadline with an async timeout.

Configure `E2B_API_KEY`, `E2B_TEMPLATE_ID`, `E2B_TEMPLATE_QUALIFIED=true` and an
operator-verified `E2B_EXECUTION_MAX_COST_USD` before creating coding tasks. Key,
template and qualification absence fail before paid calls. No key is needed for
offline tests. The SDK is pinned to `e2b==2.55.1`. The configured application database remains untouched by validation; live E2B setup updates only its selected template and qualification settings in the ignored root `.env`.

## Operator setup after adding the key

These are operator actions, not commands run against the configured application
database during this implementation. From the repository root:

```sh
uv sync --locked --project apps/api
cd apps/api
uv run alembic upgrade head
uv run python -m agentforge.workers.checkpoints setup
```

The migration command applies application schema through `0005_phase4_coding` to
the database selected by Settings. Review that target before running it. The
checkpoint setup is needed for a new installation; existing Phase 3 deployments
already have those framework tables.

After adding the key to the root `.env`, explicitly build the remote template:

```sh
uv run --env-file ../../.env python ../../infra/e2b/template.py
```

This command can incur E2B charges. The builder uses the same Settings precedence
as the API; the explicit environment file is optional. Live setup on 10 October
2026 built and qualified the selected development template. Its actual template
and build identifiers, observed resources and mutable-selector limitation are
recorded in the [template guide](../infra/e2b/README.md).
Complete its live isolation/profile checks before setting
`E2B_TEMPLATE_QUALIFIED=true`. Also verify the execution cost cap against the
qualified template's resources and maximum lifetime. Restart the API and worker
to reload settings, then create a small public coding task with a supported test
profile. No application deployment or AWS provisioning is part of these steps.

## Validation on 10 October 2026

All 38 offline focused tests passed. They cover GitHub/archive boundaries, provider request contracts,
workspace paths/binary/CAS/diff, finite cleanup, cancellation, evidence qualifiers,
zero tests, changed tests/configuration and unsupported result assertions.

A unique disposable PostgreSQL database applied migration `0005_phase4_coding`
with zero ORM metadata drift. The real parent graph, shared child runtime,
`SpawnService`, budget ledger, lease checks and private artifact storage ran with
injected offline model/GitHub/provider adapters. Only exact generated fixture
programs ran locally through the fixture adapter: tests produced actual exit
codes 1 then 0, the executor repaired once, both read-only reviews consumed actual
evidence, and the receipt became `COMPLETED`. The database/artifacts were removed.
Additional fixture flows rejected assertion-only reviewer PASS, capped ineffective
repairs at exactly two, and stopped after one execution when cleanup was unknown.
CAS/idempotency, ownership, cancellation, immutable snapshots and missing-key
no-call checks passed. The existing Phase 3 research API/graph regression also
passed with the new schema, including its truthful `PARTIAL` receipt. Frontend
TypeScript, focused ESLint and the Next production build passed after the coding
form, optional build profile and evidence/artifact views were added.
This proves offline contracts and integration. The separate live E2B checks below qualify its execution boundary.

Run focused tests with `uv run --project apps/api python -m unittest discover -s
apps/api/tests -v`. Run the opt-in generated-fixture/PostgreSQL smoke with
`uv run --project apps/api python apps/api/examples/phase4_offline_smoke.py`.
It requires a local PostgreSQL administrator able to create a unique disposable
database; the configured application database is never migrated or populated.

Live E2B checks on 10 October 2026 passed actual Python failure-to-success,
pytest, Node test/npm test/build, guest network/metadata socket denial, non-root
identity, read-only source, bounded resource exhaustion, output overflow,
detached-child timeout, cancellation and provider TTL termination. The first
provider calls exposed unsupported IPv6 deny CIDRs and a reachable link-local
metadata token endpoint; the final template disables IPv6 and applies persistent
seccomp socket denial after a trusted root launcher drops to UID/GID 10001.
No metadata body or credentials were read. The ignored `.env` now selects that
qualified template; final provider inventory had zero active sandboxes. See the
[template guide](../infra/e2b/README.md) for exact evidence and bounds.

The E2B execution slice is qualified for those synthetic fixtures. Full Gate P4
remains open for live model/GitHub end-to-end orchestration, real GitHub issue
delivery, authenticated browser interactions, hosted tracing and concurrent
worker crash recovery. AWS hosting, AArch64 and production image digest pinning
remain deferred. No configured application DB migration, LLM call, AWS action,
commit or push was performed during E2B setup.
