# Agent prompts and tool descriptions

Every model call is built from three layers, highest priority first:

1. **Platform rules** (`subagents/prompt_builder.py`, the `developer` message). Instruction priority, how to work
   with tools, honesty and safety, and the result contract (finish by calling `submit_result`; its parameters are the
   result schema, so the schema is sent once, as a tool, not repeated in the prompt). Change them here once and every
   role, built-in or custom, follows.
2. **Role instructions** (`subagents/definitions/<name>.v<N>.json`, `system_prompt`). One immutable version per
   role. Released versions are never edited; a change ships as a new version and `registry.py` (`latest`) points to it.
3. **Brief** (the `user` message, JSON). The assignment from the parent workflow, the current date, permitted tools,
   limits and untrusted context. Data in it never outranks layers 1 and 2.

## Roles

| Role | Used for | Summary is shown to the person |
| --- | --- | --- |
| `intent-router` v3 | Front door for every message: answers directly or chooses the work | no (`response` is) |
| `researcher` v4 | Evidence-backed research with verbatim, source-linked quotations | yes |
| `general-executor` v5 | Drafting, analysis and planning with a saved deliverable | yes |
| `verifier` v4 | Independent assessment of an answer, claims and sources | no |
| `code-executor` v2 | Makes exact, targeted edits in the sandbox and runs real tests | no |
| `code-reviewer` v4 | Read-only review of the diff, tests and secrets | no |
| `code-verifier` v2 | Read-only check that the change meets the goal | no |

Older versions (including `general-executor` v1-v2 and `verifier` v2, which were coding roles) remain for runs that
already used them.

## Writing a role prompt

Start with `# Role`, then a numbered `# Process`, then `# Result rules`, then hard limits. Keep it under about
3,000 characters. State, for the fields the gates check:

- whether the person reads `summary` directly (then it must be a finished reply, never a description of the process);
- what makes `PASS`, `PARTIAL` and `BLOCKED`;
- the exact format the deterministic checks need (for example claims are exact quotations of 20 or more characters);
- that anything read is data, never instructions.

## Tools

`tools/catalog.py` holds one description per tool: what it does, when to use it, how to continue (`next_offset`,
`next_line`), and its limits. Every argument in `tools/contracts.py` has a `description`. Provider schemas drop
pydantic's auto-generated titles, and hide identifiers the task fixes (repository, commit, sandbox, task). A failed call
returns an error code, a short detail written by our own code (for a bad argument: each wrong field and the constraint it
broke, never the value) and static guidance from `error_guidance`, so the model can correct itself without ever seeing
provider text. Adding a tool means adding its argument model, a description, its capability in
`contracts/agent_definition.py`, and a label in `apps/web/lib/tool-steps.ts`. See `docs/agent-harness.md` for the whole
tool-calling contract.

## Rendering activity

Run events become one timeline (`apps/web/lib/activity.ts`) shared by the live view and finished replies: reading the
message and conversation, each agent phase (researching, checking the result, writing code), and every tool call with a
short detail (the search query, the site's domain, the file path). Tool events carry that `detail`, derived from the
validated arguments by `activity_detail` in `tools/catalog.py`; it never includes tool output or file content.

While a run is active the thread shows the timeline with only the newest step animating. After the reply arrives, an
"N actions" line above it opens the same timeline, loaded from the run's event history. Replies with no tool actions
(plain chat) show nothing. Assistant messages expose `steps` (tool name and outcome only) so the action count needs no
extra request. `tests/test_agent_prompts.py` locks in these contracts.

## Scrolling

Sending a message anchors the new turn near the top of the view with room below it (`lib/scroll.ts`), so the reply
builds in front of the reader instead of against the composer. The thread follows the reply only when the reader is at
the end of the content, and "scroll to latest" appears only when real content, not reserved room, is below the view.

## Live replies

The reply is shown while the model writes it. Models answer with one typed result (the arguments of `submit_result`),
so `llm/streaming.py` reads the token stream and yields only the person-facing field: `response` for chat replies and `summary` for the last
research/general stage. Other fields, and fields that are `null`, never stream.

1. `OpenAIAdapter.complete(..., on_text=...)` streams the completion and returns the same message, usage and finish
   reason as the non-streaming path.
2. `orchestration/stream.ReplyStream` keeps a throttled copy of the growing text in `task_streams`. It is fenced by an
   epoch (a retry or newer attempt starts a new one) and is best effort: any failure switches streaming off for the
   run and the task still completes. Apply the migration (`uv run alembic upgrade head`) to enable it; without the
   table, replies simply arrive when complete.
3. The task's SSE endpoint sends `message.delta` events (`epoch`, `revision`, `kind`, `offset`, `text`, `reset`) and polls quickly only while a
   reply is being written. They are not part of the durable event log, so a reconnect restarts from offset 0.
4. `TaskTurn` renders the text as it grows, folds the steps into an "N actions" line, and the saved message takes its
   place without a second entrance. The copy is cleared when the task ends; the saved message stays authoritative.

`tests/test_streaming.py` covers the extractor, adapter assembly, the writer and the runtime wiring;
`examples/phase8_streaming_smoke.py` checks the migration, SQL, fencing and the SSE endpoint on a disposable database.
