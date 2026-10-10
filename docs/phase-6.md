# Phase 6 — Conversations, general tasks and custom agents

Implemented on 10 October 2026; offline compatibility, security, integration
and frontend build checks passed. **Gate P6 remains open** for live model/custom-agent
traces and authenticated browser qualification. The existing API8000, worker,
web3000 and configured database were preserved; migration `0007_sessions_agents`
was exercised only on disposable PostgreSQL databases.

## Persistent conversations and continuation

Owner-scoped sessions support research/general/coding modes, search, rename,
archive and sequenced message history. A turn derives its project from the owned
session, uses a caller-generated UUID idempotency key and atomically persists the
user message, task and queue intent. Replays return the same turn; session locking
permits at most one active task. Final assistant receipts are immutable and
terminal task updates clear the active pointer transactionally.

History is lower-trust context, never permission or approval. Context snapshots
contain bounded recent messages and a bounded historical digest with provenance;
the total serialized context is capped at 4,500 characters. Task inputs and
same-task sources are separately bounded and exposed through authorized read
tools. An archived session rejects new work. One identity can retain at most 500
sessions; list/history queries paginate rather than loading the whole account.

Coding follow-ups retain an immutable prior patch and the original repository
baseline. Each follow-up gets a fresh task workspace, explicit current grants,
test evidence and approval state. Prior messages, artifact references and previous
approvals cannot authorize new writes. A changed repository/ref or another owner's
task is rejected rather than silently carrying a patch into a different scope.

## Custom-agent management

Project-private agents use validated definitions and immutable sequential
versions. Built-in names are reserved. Save conflicts cannot overwrite an
existing version; history retains exact definition hashes. Disable is soft and
does not rewrite historical runs. A project can hold at most 200 agents and an
agent at most 100 versions. The test endpoint queues a real bounded task using
the selected exact version through the same registry/spawn/runtime path.

The initial management surface permits source-research tools and
`research_result_v1`, with explicit web hosts/resource restrictions, at most
12 steps/20 tool calls/USD1/300 seconds. It rejects shell, GitHub writes, sandbox
and browser capabilities. This is a deliberate supported capability boundary;
generic definition JSON does not grant arbitrary tool execution. Runtime grants
are always intersected with the task, user, platform and parent ceilings.

## General and research completion

General tasks use the same bounded parent/child runtime and private report
artifacts. Completion requires persisted same-task inputs and successful actual
gateway reads by the independent verifier, including complete coverage of bounded
inputs/output. Research adds deterministic source/hash/quote checks and an
independent evidence-grounded coverage/attribution/conflict assessment. A model
PASS alone cannot complete a task. Missing/foreign evidence, incomplete reads,
unsupported claims or unverified output retain a truthful incomplete receipt.
These checks establish bounded evidence support, not external factual certainty.

The report renderer escapes external/model text and constructs source links from
checked HTTPS records. Artifacts stay in private owner-authorized storage; source
and artifact IDs are scoped to the task. Unknown paid-operation outcomes remain
conservative and are not repeated automatically.

## UI and verification

The workspace provides searchable session history, a centered composer,
conversation receipts and task run details. Repository connections, approval
controls and custom-agent management have separate routes; settings report
configuration state. The task view preserves events, child-run evidence, diff and
artifact access. Frontend lint/types/build checks do not substitute for browser
interaction, hydration, keyboard/accessibility, streaming or narrow-layout tests.

`phase6_sessions_smoke.py` passed on a newly created/migrated/dropped database:
concurrent turn replay and single-active-task enforcement, ownership/cursor
checks, immutable receipts, bounded untrusted context, version history/conflicts,
exact older-version custom-agent test, coding patch continuation and global
cross-user quota serialization. `phase6_research_general_smoke.py` exercises the
real parent graph with offline adapters for successful and rejected research and
general verification. No paid provider call or actual external publication is
claimed. Run these through [release checks](phase-7.md) for a hashed receipt.

The independently staged Phase 6 snapshot passed 63 offline unit tests, Python
compilation/imports, frontend lint/route types/TypeScript and the production build.
Disposable PostgreSQL checks passed the Phase 4 coding regression, Phase 6 session
and continuation workflow, and all nine research/general graph modes. Positive
research and general cases completed; unsupported review, unread attachments,
contradictions, missing criteria, unsupported quotes and unresolved prior issues
remained PARTIAL. A resolved interim issue completed only after fresh supporting
evidence. Each disposable database was removed.

Remaining gates include actual public auth/browser flows, live provider traces,
controlled worker process interruption/restart with no duplicate paid action,
E2B coding continuation and public deployment qualification. Optional parallel
read-only children and browser sandbox work remain stretch scope.
