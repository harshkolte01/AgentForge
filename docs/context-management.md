# Conversation context and composer status

The current automatic-conversation path adds source-linked incremental summaries,
retrieval of originals, and execution-time snapshots. See
[conversation reliability](conversation-reliability.md) for the current delivery,
history and deployment contracts. The extractive policy below remains the legacy
standalone compatibility path.

Reviewed 2026-10-11. This covers the request path from conversation admission to
provider accounting and the composer. It does not qualify a hosted deployment
or million-token live workload.

## Model capacity and cost

OpenAI's [Luna model documentation](https://developers.openai.com/api/docs/models/gpt-6-luna)
specifies a 1,050,000-token total window, a 922,000-token input limit and a
128,000-token maximum output. AgentForge reserves its configured output allowance
and uses the smaller of the remaining window and the provider input limit. Smaller
operator windows continue to work. Luna's Chat Completions endpoint takes function
calls only without reasoning, so requests at effort `none` use it, as before, and every
other effort (the person's choice in the composer, High by default) is sent through the
Responses endpoint, which keeps tools with reasoning. The runtime still speaks the chat
message format: the adapter translates the history and the reply (`llm/openai_adapter.py`).

Luna installations with an input allowance above 272,000 require these explicit
settings in addition to the standard input/output rates:

```dotenv
LLM_CONTEXT_WINDOW_TOKENS=1050000
LLM_LONG_CONTEXT_INPUT_USD_PER_MILLION=0.20
LLM_LONG_CONTEXT_OUTPUT_USD_PER_MILLION=0.75
```

These are the Standard rates reviewed on the date above; verify them against the
[official pricing page](https://developers.openai.com/api/docs/pricing) before
deployment. Long-context pricing applies to the entire request when measured
input exceeds 272,000. Cached input is priced at the input rate unless
`LLM_CACHED_INPUT_USD_PER_MILLION` and `LLM_CACHE_WRITE_USD_PER_MILLION` are set
(the provider reports the cached, written and plain parts of every request, and the
ledger records them either way; see [the harness](agent-harness.md#cost-per-request)).
Missing long rates fail closed. Increasing capacity never increases the task's spending,
time, permission, or tool-call grants.

Request fitting uses an upper-biased local text estimate, not an exact tokenizer:
three ASCII characters per token, two tokens per Unicode code point, plus protocol
and message overhead. Authorization uses a separate UTF-8 byte bound, capped by
the effective provider input limit, and the appropriate price tier. This avoids
treating a prose heuristic as a monetary upper bound for dense code. Final charges
use provider-reported input/output tokens. Unknown usage retains its reservation.
Exact model-side input counting and opaque provider compaction would require a
Responses adapter and a separate protocol/accounting qualification; they are not
represented as already implemented here.

## What carries into the next message

Each turn starts new specialist working contexts. It receives the current goal,
attachments, and an immutable, task-owned history extract through paged tools.
The next turn does not append the previous run's tool transcript.

The HTTP conversation path shares the same history policy with the status API:
approximately a tenth of usable input, capped at 48,000 serialized UTF-8 bytes.
The cap bounds read pages, storage, latency, and cost even for a million-token
model. Standalone repository callers retain the 7,500-byte compatibility default.

The extract considers the latest 40 stored messages. Up to 12 recent messages are
included in full when they fit, and up to 12 earlier messages have short attributed
excerpts. Space is reserved for those earlier excerpts. A shortened message keeps
both its opening and ending and is explicitly marked `truncated`; whitespace in
complete originals is preserved. Message IDs, sequence, and role retain provenance.
Full originals stay stored and available through owner-scoped message pagination.
This is deterministic excerpting, not a model-generated semantic memory. Details
outside the extract are not implicitly recalled by the harness.

Coding executors and both independent reviewers also receive the complete owned
goal, attachments and saved history extract in a separate bounded user-role data
message. Short planning previews cannot silently remove the end of a long goal or
the supplied attachments. This data is never a developer instruction and grants
no additional tools or repository access. The provider fitting and cost checks
include it, and its contents are included in the recorded brief fingerprint.

The v4 router has enough bounded steps/tool calls for paged history plus all
attachment inputs. Released v3 definitions remain intact. Its logical routing
operation ID stays stable so recovery reuses a completed earlier result rather
than repeating paid work. History, attachments, excerpts and tool results remain
lower-trust data; they cannot grant repository access or override platform rules.

## Within a run

At half of usable input the runtime shortens already-read old tool results. A
hard fitting pass can shorten more when needed. The platform prompt, assignment,
goal/attachment/history reads, and the latest unread tool batch remain intact.
Tool-call/result pairing is preserved. Compacted results retain tool/status,
evidence/artifact IDs, and bounded file identity and paging metadata. Their original
content remains persisted; rereads still pass the same ownership/permission checks.

The full serialized conversation is recounted after shortening, including JSON
escaping. Space for a tool batch includes worst-case dense Unicode and deferred
reply overhead. Unaffordable-to-fit calls are deferred before execution. A forced
typed partial result remains the fallback when context, budget, time or step limits
prevent further work. Large previews are bounded by their final encoded envelope.

## What the composer reports

The small indicator opens on hover, click, or keyboard activation. It does not
disable sending or interfere with repository selection, model selection,
attachments, or stop controls. Touch retains an enlarged target.

The ring has two readings, and the card says which one it is showing:

- **While a run is going on**, it is the **measured** size of the model's latest request: the provider-reported input
  tokens of the main agent's newest model call, tool results included. This is what the model actually holds right now; in a
  tool-heavy run it climbs from the starting size (often tens of thousands of tokens) to well over a hundred thousand. The
  unsent draft is not part of it. It follows the run because the status is re-read every few seconds while it is live and each
  call's usage is recorded as soon as the call ends. A delegated subagent has a context of its own and is not counted here.
- **Otherwise**, it is an **estimated next-message input**: instruction/tool overhead, the actual next history extract, and the
  unsent draft/attachments. The denominator in both cases is the effective input allowance.

Below the meter, a conversation that has had a run also shows that run's measured numbers ("This run" or "Last run": number of
requests, peak input and its share of the allowance, and whether older tool results were shortened). The server reports
`last_run.latest_input_tokens`, `first_input_tokens` and `active` (`SessionRepository.last_run_context`); `active` is true only for
the conversation's current task, so a finished run's latest request is never mistaken for the live context. The estimate is
unchanged and is still labelled as one: the next turn starts fresh from instructions and the history extract, so a previous run's
tool results do not count toward it. Empty, unknown, loading, stale, error, and near-limit states remain distinct. Near the limit
during a run the card says older results are shortened automatically; near it for the next message it suggests shortening the
message or starting a new conversation.

The popup is centered directly above the context indicator with a fixed 6px gap
from its button. Its distance does not grow with the text area's height. It flips
vertically when needed and shifts away from screen edges without falling back to
a distant side of the composer. Pointer opening does not move focus into the
popup; keyboard opening supports focus and Escape dismissal.

The latest run's provider-measured peak can include several independent agents, is never added
to the next draft and is never used to recommend a new conversation. API coverage describes included text, not proof of model consumption.
Actual input read receipts remain independently verified by the harness.

Polling is sequential, times out, pauses when hidden, resumes on visibility, aborts
on conversation changes, and preserves only the last successful value for the
same conversation. A failed refresh is explicitly shown as stale. No provider
requests occur when viewing context status or typing a draft.

## Validation

`scripts/release_checks.py` includes backend unit checks, disposable PostgreSQL
fixtures, frontend context projection regressions, lint, types and build. Focused
regressions cover Luna limits and price thresholds, serialized multilingual
history bounds, truncation provenance, pinned/unread results, draft projections,
reply reservations, and preserving the 1.05M capacity in number formatting.
