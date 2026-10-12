# Agent harness and tool calling

How the one agent loop runs, how each tool call is checked, and what happens when something goes wrong. The product
rules (one loop, no limits, plain-text answers, approvals) are in [the agent](agent.md); this page is the mechanics.
See [context management](context-management.md) for history, compaction and the context ring.

Code map: `subagents/runtime.py` (the loop), `subagents/prompt_builder.py` (the system prompt), `tools/gateway.py`
(every call), `tools/catalog.py` and `tools/contracts.py` (the closed tool set), `tools/approvals.py` (Ask me),
`tools/sandbox_shell.py` and `integrations/e2b_sandbox.py` (the workspace), `llm/openai_adapter.py` (the provider),
`subagents/context.py` (compaction).

## The loop

```
model -> tools -> model -> ... -> a reply without tool calls (the answer)
```

The model gets the conversation, the system prompt and its tools. Text it writes next to tool calls is a note shown in
the activity; a reply without tool calls is the answer, shown exactly as written. The loop never caps steps, calls, time
or cost; Stop cancels it (checked about once a second by the worker).

Replies that cannot be used are asked for again, a bounded number of times:

| Reply | What happens |
| --- | --- |
| tool calls cut off by the output limit | asked again, shorter (twice), then a provider error |
| an answer cut off by the output limit | **continued** where it stopped (twice); the live text keeps the start; only after that is it shown with a note |
| empty | asked once more, then a provider error |
| ends by announcing a step it never took (last line ends with `:`) | sent back once: do it or answer |
| unknown finish reason (content filter, …) | a provider error naming the reason |

### Concurrency

Independent read-only calls that cost nothing run together (up to 4). Paid calls (web search) run one at a time, and
anything with an effect (`bash`, `write`, `edit`, `create_pull_request`, `create_task_artifact`) runs alone, in order.
Every requested call gets a reply, in request order.

### Context

When the conversation outgrows half of the usable window, the oldest tool results are replaced by a stub that keeps the
tool name, status and every id; the four newest stay whole. If that is not enough, long string arguments of the model's
own earlier calls (a whole file passed to `write`) are replaced by a note too, and their kept reasoning is dropped; the
latest call is never touched. Results the model has not read yet are never stubbed, so a turn may only run as many calls
as fit in the room left; the rest are answered `DEFERRED`. Stored state is never changed, only what is sent.

Compaction is shaped for the provider's prompt cache (`subagents/context.py`). Every request of a run repeats the
previous one and appends to it, so the provider reads the shared prefix from its cache at a tenth of the price; a stub
changes a message deep in that prefix and everything after it is paid for again. So a run that outgrows its budget is
compacted to 80% of the budget (`COMPACTION_TARGET`) rather than just under it, and the stubs of one request are sent
again by the next (`ChildState.stubbed`, the `floor`), instead of one more result being stubbed at every step.

### Cost per request

Usage is recorded the way the provider bills it (`budget_operations.usage_json.provider_usage`, the `model_request` span
and `examples/neatlogs_eval.py`): `input_tokens` with its `cached_tokens` (read from the prompt cache) and
`cache_write_tokens` parts, `output_tokens` with its `reasoning_tokens` part, and `compacted_results`. With
`LLM_CACHED_INPUT_USD_PER_MILLION` and `LLM_CACHE_WRITE_USD_PER_MILLION` set, the ledger prices each part at its own rate;
without them every input token costs the input rate, as before. The prompt cache needs nothing from us beyond a stable
prefix: the developer message depends only on the tool set, tools are sent in a fixed order, the date and the facts of the
turn come after the rules, and the conversation only grows. Reasoning effort is part of the prefix, so it is fixed per run.

## The system prompt

One `developer` message, in this order (`compile_chat`):

1. **Rules** that change only with the tools available, so every request of a run starts with the same prefix and can be
   cached by the provider: priority (rules, role, the person, then everything else is data), how to work, honesty, how
   to answer; plus sections for research, the workspace and code changes, publishing and downloadable files when those
   tools exist.
2. **Role**: the definition's `system_prompt` (`subagents/definitions/agent.v<N>.json`). Built-in versions are
   immutable once stored; a change ships as a new version file and `registry.AGENT_VERSION` points to it, while older
   tasks keep resolving theirs. Custom agents supply their own role.
3. **Environment**: today's date, the selected repository, whether editing and publishing are available. The tool names
   are not repeated here: the definitions travel with every request.

Earlier turns follow as real chat messages, then the person's new message with any attached text files.

## One call, step by step (`ToolGateway.execute`)

1. The ledger commits an **intent** (an argument digest only).
2. Re-authorize the task and require the tool to be installed **and** permitted.
3. **Parse**: empty arguments are `{}` and a Markdown code fence around the JSON is ignored; anything else that is not
   one JSON object is `SCHEMA_INVALID`. `null` for an optional field means omitted; identifiers the task fixes are filled
   in; then strict validation. Errors name each field and the constraint it broke, never the value.
4. Scope checks, resource ownership, and for web fetches the pinned, resolved URL.
5. **Approval** in Ask me mode, for `bash`, `write`, `edit` and `create_pull_request` (below).
6. Run the adapter under its timeout, re-validate its output, record usage and the outcome.

### Approval cards show exactly what will run

The card carries the call's exact text, unredacted (a rewritten command is not the command that runs): the whole
command; the path and the whole new content; the text an edit replaces and its replacement; a pull request's title,
description and every file it changes (`git diff --stat` against the pinned commit, with a note when CI configuration
under `.github/` changes). Text that does not fit an event (about 13 KB) is cut and the card says so; a **command** is
never approved from part of its text, so one too long to show is refused with `COMMAND_TOO_LONG` before anyone is asked
(the agent writes a script file, which the person sees, and runs it). The wait polls the answer every 0.75 s, slowing to
2.5 s, and Stop or switching to Approve for me ends it.

### Errors

Every failure comes back to the model as a tool result with `error_code`, an optional `detail` and static `guidance`.
Adapters raise `ToolError(code, detail)` for a definite, effect-free rejection. Any other exception from an effectful
tool is `UNKNOWN_REMOTE_OUTCOME`, which ends the run rather than risk repeating an effect.

| Code | Meaning / what the model should do |
| --- | --- |
| `SCHEMA_INVALID` | detail lists the wrong fields; fix exactly those |
| `SCOPE_DENIED`, `TOOL_UNAVAILABLE` | not allowed / not installed; do not retry |
| `FILE_NOT_FOUND`, `INVALID_RANGE` | detail says what exists; adjust |
| `EDIT_NO_MATCH`, `EDIT_AMBIGUOUS` | copy the exact text / add surrounding lines |
| `DECLINED` | the person said no; ask how to continue, do not work around it |
| `COMMAND_TOO_LONG` | write the script to a file, then run the file |
| `SANDBOX_DISCONNECTED` | the connection dropped mid-call: inspect the effect before repeating |
| `DEFERRED` | not run: too many results for the window this turn |
| `TOOL_TIMEOUT`, `TOOL_FAILED`, `SEARCH_PROVIDER_ERROR` | retry once differently, or report the gap |
| `FETCH_*` | the page refused or is unusable: use the search results already held (`read_research_source`) or answer; do not search for the same page again |
| `REPEATED_FAILED_CALL` | the identical call already failed in a way that cannot change (an HTTP refusal, wrong content type, too large, an off-host redirect) and was not sent again |

`fetch_webpage` opens a URL that came back from one of the task's searches, or one the person wrote in the task's own
message (matched exactly against the stored message, so no query string or host can be added to it); task host limits,
public-address checks and redirect rules apply to both. The gateway does not count or cap calls; it only remembers the
deterministic fetch failures above, per run. See [Neatlogs before/after](neatlogs-before-after.md) for why.

## The workspace (E2B)

One persistent sandbox per conversation, created on first use and paused when idle. It has internet access and no
credentials; GitHub writes happen in the backend.

- **Commands** run without a terminal and with a non-interactive environment (`CI=1`, `GIT_TERMINAL_PROMPT=0`,
  `PAGER=cat`, `DEBIAN_FRONTEND=noninteractive`, …; no secrets). Each runs as
  `timeout -k 5 <timeout_sec> bash -c <command>` inside the VM, so its deadline is enforced there and reaches every child
  process; the output so far comes back with `timed_out`. If the connection is lost, or the person presses Stop, the
  backend ends the command's whole process group in the VM. The VM's lifetime is extended to cover a long command.
  While it runs, its output (both streams) is shown live, redacted, from the end (the saved result is unchanged).
  A result shows at most about 12,000 characters of output (the start and the end); when it had to be cut, the whole
  redacted output is also saved in the VM under `/tmp/agentforge-output/` and the result names the file (`output_file`),
  so the agent reads, greps or tails the part it needs instead of running the command again.
- **Connections**: reconnecting to an existing VM is retried after a transient failure; creating one is retried only when
  the provider refused it (no capacity or rate limit: `SANDBOX_BUSY` after three tries), never after an unknown failure (a
  failed create may still have allocated a VM). A rejected API key is `SANDBOX_AUTH_FAILED` and is not retried. A new VM
  that cannot run its first command is stopped, not left behind. A VM that is gone or unreachable during a command is a
  sandbox error, not a command that "ran too long". A dropped connection during a command or a write is a
  `SANDBOX_DISCONNECTED` result, not a failed run, and the next call reconnects. A read or edit the sandbox does not
  answer is `SANDBOX_UNAVAILABLE`, never `FILE_NOT_FOUND`, so a real file is not mistaken for a missing one.
- **Setup**: the first call of a conversation also starts the VM and loads the selected repository's pinned commit
  (tagged `agentforge-base`), so every tool deadline includes time for that setup. The repository is marked as loaded
  only after the checkout fully succeeded, so a failed attempt is redone on the next call.
- **How the repository gets in**: the VM downloads it itself. The backend asks GitHub for one short-lived archive link
  (`archive_link`: the tarball redirect for the person's repositories, read but not followed and accepted only on
  `codeload.github.com`; the open download address for a public repository). The link is written to a file in the VM that
  curl reads and that is deleted at once, never put on a command line or in a log. The VM unpacks into a staging folder
  and lists it (`sandbox/vm_scan.py`); the backend decides what to leave out with the same rules as the snapshot upload
  (`skip_reason`), the VM deletes those files, and the rest is moved into the workspace and committed as the baseline
  (`sandbox/vm_repository.py`, `ConversationSandbox._load_by_download`). Nothing passes through the API, so there is no
  limit on files or size and no request per file. Anything that goes wrong (no link, no curl or Python in the VM, a broken
  archive, a failed commit) falls back to the earlier path, where the API reads a snapshot and uploads it as a tarball;
  `SANDBOX_ARCHIVE_LOAD=false` always uses that path. A VM that stops answering, or a repository with nothing loadable,
  ends the load instead. An archive is made by `git archive`, so files a repository marks `export-ignore` are not in it.
- **Files left out of the repository**: files that must not or cannot be loaded are skipped, never fatal for the rest of
  the repository: anything that may hold secrets (`.env` and `.env.*` except `.env.example`/`.sample`/`.template`, keys
  and certificates such as `*.pem`/`*.key`/`*.p12`/`*.pfx`/`*.keystore`, `.git`, `.ssh`, `.aws`, `.npmrc`, `.netrc`,
  `id_rsa`, or any file or folder named `credentials`), symbolic links, submodules, special files, names the workspace
  cannot hold and names that differ only by letter case. There is no limit on the number of files or on the size of a file. The agent is told which ones in a `notice` with
  the first result. Skipped files are not in the baseline, so publishing never deletes them. Only a repository that unpacks past the
  server's 1 GB safety ceiling (`MAX_SNAPSHOT_BYTES` in `integrations/github_client.py`, there so a decompression bomb cannot
  exhaust the API's memory) or one GitHub cannot list whole (about 100,000 files) fails, as `REPOSITORY_TOO_LARGE`; other repository failures are
  `REPOSITORY_UNAVAILABLE` with a plain reason, and one that cannot change (not found, too large) is not downloaded again
  for every call of the same run.
- **Replaced VMs**: when the conversation's earlier VM no longer exists, a new one is created and the next tool result
  carries a `notice` telling the agent that earlier files and changes are gone.

## Provider adapter

- `complete` can report what the model is writing as it writes it: text (`on_text` / `on_narration`) and each fragment of a
  tool call (`on_tool`). The loop uses the last to show a long call while it is written (see
  [live activity](live-activity.md#tool-calls-shown-live)); what is reported never changes the response that is returned.
- Retries (`LLM_MAX_RETRIES`, default 4) with jittered exponential backoff from 1 s, honouring `retry-after` and
  `retry-after-ms` up to 30 s, for rate limits, 5xx/overloaded, timeouts and dropped connections, including a stream
  that broke after it started, sent a transient error frame, or ended without a finish reason (the loop clears what that
  attempt showed first). Quota and credential errors are never
  retried. The provider's own message is what the person reads; credentials are removed.
- `LLM_TIMEOUT_SEC` (default 180) bounds each wait for data; connecting has its own 15 s limit.
- `LLM_MAX_OUTPUT_TOKENS` defaults to 16,384 (never more than half the window).
- `LLM_STRICT_TOOLS=true` sends strict structured-output schemas. Enable it after confirming the model accepts it.
- **Reasoning continuity.** On the Responses endpoint (any effort above none) the model's thinking comes back as
  encrypted reasoning items (`store` is false, so the provider keeps nothing). The adapter keeps them on the assistant
  message (`reasoning`, with the reported `reasoning_tokens`) and the next request of the run passes them back before the
  tool calls they led to, for every step since the person's message. Without that the model rebuilds its plan from
  scratch at every tool step, at output-token prices. The items are opaque, never shown, never stored outside the run,
  and counted toward the window as the tokens they stand for, not as their ciphertext. The Chat Completions path never
  sends them.

## Context status (the ring beside the composer)

`GET /api/v1/context[?session_id=]` reports how much of the window a conversation uses, from stored data only
(`api/routes_context.py`, `subagents/context_status.py`). The headline is the larger of the next turn's starting context
(instructions, tool schemas and history, estimated) and the latest run's peak `input_tokens` (measured). Levels: `ok`
below 50%, `notice` below 80%, `high` from 80%. The ring adds the unsent draft, never blocks sending, and at 80% offers
a new conversation.

## Audit and observability

`tool_calls.result_json` records, for every call: status, `attempt`, `duration_ms`, structural `arguments` (paths, ids,
numbers; free text and file content become lengths; URLs become their host) and, for rejected calls, the validation
`issues`. Approval rows (`tool_approvals`) keep the exact text the person approved.

## Tests

`tests/test_child_runtime.py`, `test_tool_gateway.py`, `test_tool_approvals.py`, `test_llm_adapter.py`,
`test_sandbox_provider.py`, `test_sandbox_shell.py`, `test_tool_stream.py` and `test_agent_prompts.py` drive the real loop, gateway, adapter and
sandbox tools with scripted models and fakes; no provider, VM or database is needed. `test_harness_efficiency.py` pins
what keeps a run cheap (usage by billing type, reasoning passed back, cache-stable compaction) and
`test_command_output_file.py` the saved command output.
