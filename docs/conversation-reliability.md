# Conversation delivery and continuity

AgentForge keeps its Python/PostgreSQL/LangGraph runtime. Conversation lifecycle
decisions are serialized through PostgreSQL; clients and streams are read models.

## Delivery

Automatic turns are admitted as immutable messages, request snapshots and retry
receipts. Separate execution records associate each turn with its task and reply.
Assistant replies are never paired by sequence adjacency. Existing explicit-mode
and standalone task endpoints keep their execution contracts.

Each conversation runs one turn at a time. Up to 20 additional messages wait in
FIFO order, each becoming a separate task. Repository selection and continuation
preference are captured at admission. Inherited continuation resolves against the
latest compatible predecessor at dispatch; source rights are checked again.

The existing admission limits apply when a waiting turn is dispatched. Temporary
capacity limits retry after 10 seconds. Other admission failures block the head
and pause the queue; resolve the cause and Resume. Accepted messages are retained.
Repository changes and archiving require no active or waiting turns.

Stop is fenced to the task the browser observed and pauses the queue. Resume, or
a new message, permits FIFO dispatch again. Cancelling a waiting turn retains
its message. An unknown external effect pauses further work and is never replayed
automatically. Task receipts, queue eligibility and conversation changes commit
with terminal task state. A bounded indexed dispatcher recovers eligibility
after worker restart.

New automatic conversation tasks start their execution deadline at first worker
execution. Reclaiming a lease does not reset that deadline. Existing tasks and
standalone callers retain their original creation-time deadline policy.

## Context

At execution, a turn freezes context from completed predecessors only. Later
queued messages cannot leak into its routing or specialist context. The current
goal and attachments stay complete and separate from history.

The shared history planner uses at most 48 KB, reduced for small windows. It
retains recent turn pairs, a source-linked summary, discoverable resource
references and explicit coverage metadata. Shortened messages retain their
resource identity and a truncation marker. Originals remain immutable.

Older decisions are summarized incrementally, in chronological chunks bounded
at 96 KB and 100 predecessor turns per attempt. At most one summary call is
attempted per task, within the smaller of 10% of its budget or USD0.05. Its
measured or unknown usage is accounted through the ordinary task ledger.
Summary output is bounded at 8 KB; referenced message identities are validated.
Conditional commits prevent stale summary updates. Failure retains the last
valid summary. Large legacy histories can have an uncovered range; coverage
reports it rather than implying all decisions were recalled.

`search_conversation_history` finds original messages, attachments, deliverables
and stored tool results. `read_context_resource` pages those originals and mints
evidence in the current task. Resource scope comes from the executing task, not
a model-supplied conversation ID. Historical repository content rechecks source
access. Historical execution evidence is context, never fresh test qualification
or publication approval.

Successful bounded tool outputs are persisted before previews. Compaction keeps
resource IDs so recovering a paid result does not require repeating the external
operation. Required inputs and unread result batches remain protected. Request
fitting includes schemas, serialized envelopes, output reservation, protocol
overhead and a 5% safety margin (at least 256 tokens). Provider-rejected input
can receive one stronger fit when ledger and step limits allow it; uncertain
generation is not retried.

Current user requests are instructions within the platform's permissions.
Historical user decisions remain applicable until corrected. Assistant
suggestions, summaries and source contents cannot become platform authority.
New built-in definition versions add retrieval without rewriting old versions.
Each provider input is recorded as a SHA-256 digest plus its shape (bytes,
message and tool counts), not the exact request; the record is private, not
public activity or telemetry. The composer reports estimates separately from measured usage.

## Browser recovery and interfaces

The browser durably stores unresolved sends in account-scoped IndexedDB before
posting. Creation and turn retries reuse their original identifiers. The 3 MiB
outbox limit rejects new entries instead of evicting unresolved ones. Replayed
work cannot cross an account change. Matching acknowledged drafts are cleared;
newer edited drafts are preserved.

Session creation accepts an optional `idempotency_key`. Turn receipts add
`turn_id`, `state`, `queue_position` and `error_code`. Messages add turn identity
and state; sessions add `revision`, `queued_count` and `queue_paused`.

- `GET /api/v1/sessions/{id}/turns` supports bounded ordinal pagination and
  `waiting=true` for the queue.
- `POST /api/v1/sessions/{id}/queue/stop` requires the observed `task_id`.
- `POST /api/v1/sessions/{id}/queue/resume` retries a blocked head and resumes.
- `POST /api/v1/sessions/{id}/turns/{turn_id}/cancel` cancels a waiting turn.
- `GET /api/v1/sessions/{id}/events` replays monotonic durable changes and
  reauthorizes the connection. Task SSE continues to carry execution activity.

The thread groups messages by turn while preserving intake sequence for
pagination. Waiting messages have a separate queue. Task activity uses the
existing recovery controller, which catches up from a contiguous cursor and
handles revisioned Unicode live text, gaps and terminal reconciliation.

## Deployment and qualification

Apply migration `0011_conversation_delivery` after taking the normal database
and artifact backup. Coordinate the API and worker rollout; older workers do not
understand queued execution records. The migration preserves messages and
backfills historical task and task-free reply relationships. It performs no
paid work. Downgrade refuses to discard accepted new turns; use a reviewed
backup/restore or roll forward.

Apply migration `0013_conversation_read_state` (the sidebar's unread state) the same way, **before** starting the
new API, because the API reads the new column. It adds one column, marks every existing conversation as seen without
changing its order, and performs no paid work. Downgrade drops the column and only forgets which replies were seen.

`CONVERSATION_QUEUE_ENABLED=false` rejects additional busy-conversation intake
while allowing already accepted work to drain. `CONVERSATION_SUMMARIES_ENABLED=false`
disables new paid summary updates and retains existing summaries and retrieval.
`CONVERSATION_TITLES_ENABLED=false` stops generating conversation titles; conversations
keep showing their first message and nothing else changes. Titles need no migration.

The offline release runner includes durable-delivery, streaming, activity API,
and IndexedDB recovery regressions alongside existing coding, identity, research,
operations, Settings and frontend checks. `conversation_delivery_smoke.py` uses
only disposable PostgreSQL and fixture models. An explicitly paid qualification
is available as `python examples/conversation_live_canary.py --run`, capped at
USD0.50. It checks recall after 52 historical messages, a later correction,
the earlier Hindi constraint, and retrieval of the original old attachment.
Offline fixtures do not qualify live providers, browser layout or a deployment.
