# Live activity: notes, steps and the reply

What a person sees while an agent works, and how it gets from the model to the page. The aim is a transcript like a
coding assistant's: the agent says what it is about to do, each step appears the moment it starts, and the answer is
written out as the model produces it.

```
model call ──text next to tool calls──▶ note   ──▶ assistant.message event (kept, in order)
           ──tool calls───────────────▶ steps  ──▶ drawn live while written ─▶ tool.started / tool.finished events
                                                                       └─▶ a command's output live while it runs
           ──text without tool calls───▶ reply ──▶ live text row ──▶ saved message when the task ends
```

## The kinds of output

| Output | Where it comes from | How it reaches the browser | Kept afterwards |
| --- | --- | --- | --- |
| **Note** | plain text a model writes in a turn that also calls tools (`on_narration` in `llm/openai_adapter.py`) | live as it is written (`message.delta`), then saved as an `assistant.message` event | yes, as an event, in order with the steps |
| **Call being written** | the JSON arguments of a long tool call (`on_tool`), for the tools in `DRAFTS` (`orchestration/stream.py`) | live as it is written (`message.delta`, kind `tool`) | no: its row takes over when the call starts |
| **Step** | each tool call (`tools/gateway.py`, reported through `tools/lifecycle.py`) | `tool.started` just before the adapter runs, `tool.finished` once the outcome is saved | yes, as events and in `tool_calls` |
| **Output of a running command** | `bash` (`on_output` in `integrations/e2b_sandbox.py`) | live as it prints (`message.delta`, kind `output`) | no: the finished row opens the saved, redacted output |
| **Reply** | the model's text in a turn without tool calls | live as it is written (`message.delta`) | as the assistant message when the task ends |

The agent is told to write one short sentence before the first tool call of a turn (`PLATFORM_INSTRUCTIONS`), which is what
makes a note appear before its steps. A model can still skip it; nothing invents one, so the step simply shows by itself.

## Tool calls shown live

Without this a step appeared only after the model had finished its whole reply (a `write` of a long file is tens of seconds
of arguments), and a command showed nothing until it ended.

- **While the model writes a call.** The adapter reports each fragment of a tool call (`on_tool(index, call id, name,
  arguments piece)`). `ReplyStream.feed_tool` shows the calls in `DRAFTS` (`bash`, `write`, `edit`, `create_pull_request`,
  `create_task_artifact`, `delegate_task`) as soon as they are named: `text` is the argument that is the call's text (the
  command, the file content; the start of it, up to `DRAFT_CHARS`) and `detail` the one that says what it is about (the path
  or title). The arguments are parsed with `jiter` in partial mode, so escapes and half-written strings never show as
  garbage. The note written before the call is saved first (`commit_narration`), so the call shows after it, in order. Calls
  with short arguments are not drawn: they run the moment the reply ends.
- **When the call starts.** The gateway has computed the same id the loop used (`legacy_call_id(f"{run}:tool:{provider id}")`,
  the `call_id` of `tool.started`). It then replaces the drawing: a command opens its `output` row (a new epoch), every other
  tool clears the drawing. Either way the real row, not the drawing, is what stays.
- **While a command runs.** `Workspace.run(on_output=...)` hands each piece of stdout and stderr to `ReplyStream.output`.
  What is shown is the end of the output (`OUTPUT_CHARS`, whole lines), redacted with the same `redact_output` as the saved
  result. It is a moving window, so once it is full every flush is a replacement (a new revision), not an append.
- **When it settles.** The gateway clears the row after the outcome is saved and `tool.finished` is sent (also when the call
  was refused or failed), so the output never goes while the row still says "running".

`task_streams` carries it with `kind` `tool` (being written) or `output` (running), plus `tool`, `call_id` and `detail`
(migration `0016_tool_stream`). Only the run the person is watching does this: a delegated subagent's stream has no live
view, and its notes and steps stay inside its panel.

On the page (`features/sessions/run-steps.tsx`, `timeline-model.ts`): a call being written is a row of its own, "Writing a
file `src/a.py`", with its text in a bounded monospace block that follows the newest text until the reader scrolls up. That block shows no
scrollbar (it would flash with every line) and fades its top edge while earlier lines are scrolled out of view; a running
command's output block keeps its scrollbar. It never
says "running" (`describeDraft`). It is hidden once the call's real row exists (matched by `call_id`) and while an Approve /
Decline card waits for it (the card carries the exact text). A running command's output sits under its own row and goes when
the row settles.

While the run is going, a list of steps shows only its newest four rows. The finished steps before them fold into one line,
"17 earlier steps", with what they were ("Read 11 files · Ran 6 searches"); it opens in place and remembers the choice
(`collapseRows` in `timeline-model.ts`, `EarlierSteps` in `run-steps.tsx`, `Timeline`'s `collapse`). Only stretches of three or
more steps that all finished well fold: a step that is running, failed, refused, interrupted or a warning, a failing command and
a team of subagents always stay on screen. Finished runs, a subagent's panel and the opened "Worked" disclosure show every step. Neither is held through the end of a run, and neither is prose: they never reach the answer area.

## Backend

- `orchestration/stream.py` (`ReplyStream`) holds one live row per task (`task_streams`: `epoch`, `revision`, `kind`, `text`).
  It keeps a reply buffer and a note buffer and publishes whichever is relevant, throttled to about eight writes a second.
  `kind` is `progress` for a note and `answer` for the reply. `revision` changes whenever the text is replaced rather than
  extended: an attempt restarting (`begin()`), a discard, a note being saved, a note giving way to the reply. Every such
  change is written at once, and a clear is written as empty text with the newer revision, so a reader is never left with
  words that were saved elsewhere or thrown away. Writes are fenced by epoch and revision, so a stale worker can neither
  overwrite a newer attempt nor bring back cleared text (a fenced-out stream stops publishing).
  `commit_narration()` saves the finished note with `store.progress("assistant.message", {text, epoch})` first and only then
  clears the live copy, so the words are never in neither place. If a reply follows in the same model call the stream moves
  to a new `epoch` (opened already holding the reply, so there is no blank frame), and a reader that already took the note
  never ignores the reply.
- The live copy is best effort: any failure switches it off for the run and nothing else changes. Notes are still saved as
  events, because the event log, not the live row, is the record.
- `subagents/runtime.py` passes `on_narration` only on turns that are not forced to submit, and after each model call either
  commits the note (the turn requested tools) or discards it (the turn only submitted the result, or answered in content).
- Which runs narrate: the stages of research and general runs and the coding executor. The intent router and the verifier do
  not. Only the last research stage streams a reply. Earlier stages' live text is discarded when they finish.
- Runs that narrate get a short "progress notes" section appended to the platform instructions
  (`subagents/prompt_builder.py`, `PROGRESS_NOTES`). It is not part of `PLATFORM_INSTRUCTIONS`, so context-size estimates and
  runs without a live sink are unchanged.
- The tool gateway owns tool events (`telemetry/runtime.py` only traces). `tool.started` carries `tool_call_id` (the saved
  `tool_calls` row), `agent_run_id`, the legacy `call_id` and a safe `detail`; `tool.finished` is sent after the outcome is
  validated and saved, with `outcome` (`completed`, `failed`, `blocked`, `cancelled` or `unknown`) and `status` (`ok` or `error`,
  for older clients). Several calls of one tool side by side stay distinct by `tool_call_id`.
- For `run_tests`, `run_sandbox_command` and `get_test_results` the finish also carries `exit_code`, `timed_out` and
  `qualified` (numbers and booleans only, never output). A test run that executed and failed is a completed call with a
  nonzero `exit_code`: "tool returned ok" never means "tests passed".
- Not shown: the model correcting its own call (`SCHEMA_INVALID`, `DUPLICATE_CALL`, `TOOL_LIMIT_REACHED`), `read_task_input`
  and unregistered names (`tools/visibility.py`, shared with the read APIs). Other refusals appear as a start followed by a
  `blocked` finish.
- When a task ends (`WorkflowStore.finish`), every start without a finish is settled from the saved `tool_calls` rows before
  `task.finished`: a call still at `INTENT` becomes `cancelled` if the task was cancelled and `unknown` otherwise (a crash
  or uncertain effect), never a success. The row itself is left as it is.
- `GET /tasks/{id}/events` forwards both: durable events with their sequence as the SSE `id`, and `message.delta` for the
  live row. It polls quickly only while the live row holds text. A `message.delta` carries
  `{task_id, epoch, revision, kind, offset, text, reset}`: `reset: true` (always at `offset` 0) means "replace your live text
  with `text`", and is sent for the first frame of every connection and whenever `(epoch, revision)` changed, including an
  empty reset for text that was cleared; otherwise it is a pure append at `offset`. A reconnect therefore starts again from a
  reset and never repeats or keeps stale text. `task_streams.text` of a finished task is cleared by `store.finish`
  (`clear_stream`: the row stays, empty, one revision newer); a row a dead worker left behind is reported as cleared.
- `GET /tasks/{id}/stream-snapshot` returns the whole live text (`epoch`, `revision`, `kind`, `text`) with the task's
  `event_sequence` and `terminal` flag, owner-authorized and `Cache-Control: private, no-store`, for a client whose offsets
  no longer match or that polls instead of streaming. A task with no live row answers epoch 0, revision 0, empty text.
- `subagents/runtime.py` clears the live text of everything it stops using: a reply cut off by the output limit, a filtered or
  rejected result, and any run that ends in an error, a stop request or its deadline. The accepted reply stays until the task
  ends.

## Frontend

One model serves the live run and a finished reply: `lib/activity/` (reducer, live text, recovery controller, disclosure store).
The components only render its snapshots.

- **Timeline** (`features/sessions/run-steps.tsx`). Entries are notes, phase headings, milestones, tool calls and counted
  groups. Every row is an icon plus words, never colour alone: running (the newest call has the orb and shimmer, other running
  calls a dim dot), done (check), failed (triangle), blocked (ban: "not allowed"), cancelled (square: "Stopped …"), unknown
  (question mark: "… may not have finished"). Execution rows take their wording from evidence, so failing tests read "Tests
  failed" with the failure icon although the tool returned. A counted group ("Read 4 sources") is one collapsed row whose
  disclosure lists the individual calls. Rows are memoized by entry identity, so streaming and running updates re-render only
  what changed.
- **Task turn** (`features/sessions/task-turn.tsx`, `use-task-sync.ts`). `TaskTurn` runs one recovery controller per task
  (SSE plus polling and bounded history catch-up) and republishes its snapshot at most once per animation frame
  (`frame-batcher.ts`), with a short timer so a hidden tab still hears about the end. Trouble reaching the server shows inside
  the timeline: "Reconnecting…", "Catching up…", then after repeated failure "Couldn't load the latest activity" with a Retry
  button. A task known to be over never shows a spinning step: unfinished calls show as unknown. `onTerminal` fires once, only
  after every event up to the task's end has been read.
- **When "Thinking" shows.** Right after a message is sent, until the run reports anything. After that only when nothing else
  shows life: no step is running, no text is being written, no connection notice, and the run has been quiet for 2 seconds
  (`QUIET_MS`), so it never sits next to text the agent has just written. Phase markers do not count as "running".
  A run that ends before any of its answer was shown keeps its "Thinking" line until the saved answer replaces it, so the turn
  is never blank in between.
- **The end of a task arrives before its live text is cleared.** The SSE loop (`api/routes_events.py`) and the snapshot read the
  live text before the events: the worker records the task's end before it clears the live copy, so a reader always learns the
  task ended before it sees the text go, and keeps the last words on screen until the saved answer takes over.
- **Live text to saved text.** The live tail (a progress note being written, then the answer) is shown unchanged until its
  durable copy exists: a saved note of its epoch or later (it is then a row in the timeline), or the saved answer
  (`savedReady`, from the thread). The tail then renders nothing in the same commit that the thread renders the saved message,
  so there is no blank frame, no second copy and no replayed entrance. Rows entering from history or from a finished turn do not
  play `message-in` again.
- **Details on demand** (`tool-detail.tsx`). Search, fetch, browse, read and write rows are compact status lines with no
  expansion. Test, command and test-result rows open an execution summary (outcome, exit code, timeout, qualification,
  duration), bounded stdout and stderr (monospace, internal scroll, copy button, "Truncated" notice), a safe error line and
  the log download. Diff rows open a changed-file list and a bounded diff preview. Saved-artifact rows open a download link
  and an optional bounded preview. Details load only when a row is opened (`GET /tasks/{id}/tool-calls/{call}`), abort when
  it closes, are remembered for the page session, and show loading, unavailable, truncated and retry states inside the
  disclosure. Legacy calls without a persisted id are not expandable.
- **Disclosure state.** Manual open/closed choices (the "N actions" summary, groups, call details) are remembered per task in
  `lib/activity/disclosure.ts`, across live updates, completion and in-page navigation. Toggle buttons are real buttons with
  `aria-expanded`, `aria-controls`, a visible focus ring, touch-sized targets on coarse pointers, and `data-scroll-anchor-toggle`
  for the scroll controller.
- **Finished replies.** `ActivityDisclosure` is collapsed in history, labelled from the exact `activity.action_count` ("212
  actions", or "Progress notes" for note-only activity), and loads the whole timeline page by page on first open (no cutoff),
  with a loading state and a retry. When a run ends in the open view its entries stay in place, expanded.
- **Under the answer** (`answer-extras.tsx`). A state line for answers that did not simply complete (partial, stopped, blocked,
  failed, waiting for approval; approval actions stay on the run-details page), compact sources and downloadable files. Sources
  and files are fetched from the owner-scoped routes only when the message's `source_count` / `artifact_count` is above zero and
  the block is near the screen. Fetched pages, search-only leads and unreadable sources are told apart in words and icons; links
  exist only for public https addresses. A plain completed answer with nothing attached renders nothing extra.
- **Answers** (`assistant-message.tsx`). react-markdown with GFM, inert HTML and the restricted link policy. While streaming,
  the text goes through `stabilizeMarkdown` so partial fences, tables, lists and links look like the finished text. Legacy leading
  `<think>`/`<thinking>` metadata is hidden; the same tags later in an answer are kept. Code and tables scroll inside their own
  containers. Completed code blocks are highlighted lazily (`highlight.js` core plus one dynamically imported grammar per
  language, up to 20,000 characters); streaming, oversized and unsupported blocks stay plain text, as does any load failure. A
  refused clipboard shows "Couldn't copy" (announced, clears itself).
- **Screen readers.** The timeline log is `aria-live="off"`: streamed words are never read out. A separate polite status says
  only what changed ("Working, 3 actions", "Stopping", "Reconnecting", "Finished", "Stopped"), spaced out while the run is busy.
- **Reduced motion.** The shimmer stops (plain text), the orb fades instead of scaling, and entrances are off.
- The thread follows a growing run to the bottom unless the reader has scrolled by hand.

## Why updates used to arrive all at once in a browser

The event stream sets `Cache-Control: no-cache, no-store, no-transform`, but a middleware in `main.py` replaced that header on
every API response with plain `no-store`. Without `no-transform`, the Next.js dev proxy (`/api/*` rewrite, used on
`localhost:3000`) gzip-compressed the stream for browsers, which send `Accept-Encoding: gzip`, and held the output back until
the stream ended. `curl` sends no `Accept-Encoding`, which is why the stream looked fine from the command line. The middleware
now keeps a route's own non-caching policy (`tests/test_live_activity.py::ResponseHeaderTests`). Behind nginx
(`infra/deploy/nginx.conf`) `/api/` is not compressed, so production was not affected.

To check a deployment: `curl -N -H 'Accept-Encoding: gzip' -D - <origin>/api/v1/tasks/<id>/events` must answer with
`cache-control: no-cache, no-store, no-transform` and no `content-encoding`.

## Run limits that end a run early

A reply cut off by the output limit (`finish_reason: length`) no longer ends the run: the model is asked once or twice for a
shorter one (`MAX_CUTOFF_RETRIES`). Two `submit_result` calls in one turn use the first. Messages the runtime writes when a
run cannot finish are plain sentences for the person, not internal text.

## Tests

- `apps/api/tests/test_live_activity.py`: the live stream, the adapter's narration callback, the loop's handling of notes,
  cut-off replies, call ids, cancellation and the stop-request poll.
- `apps/api/examples/phase8_streaming_smoke.py` checks notes and events against a
  real PostgreSQL database and the real graph (notes saved in order, each followed by its step, tool events carry call ids).
- `apps/web/tests/*.test.mjs` (`pnpm test:web`): the reducer, live text, recovery controller and the pure helpers behind the
  timeline, tool details, answer extras, highlighting, frame batching and copy feedback. `pnpm test:activity` runs the core subset.
