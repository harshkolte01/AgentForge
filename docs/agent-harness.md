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
own earlier calls (a whole file passed to `write`) are replaced by a note too; the latest call is never touched. Results
the model has not read yet are never stubbed, so a turn may only run as many calls as fit in the room left; the rest are
answered `DEFERRED`. Stored state is never changed, only what is sent.

## The system prompt

One `developer` message, in this order (`compile_chat`):

1. **Rules** that change only with the tools available, so every request of a run starts with the same prefix and can be
   cached by the provider: priority (rules, role, the person, then everything else is data), how to work, honesty, how
   to answer; plus sections for research, the workspace, publishing and downloadable files when those tools exist.
2. **Role**: the definition's `system_prompt` (`subagents/definitions/agent.v<N>.json`). Built-in versions are
   immutable once stored; a change ships as a new version file and `registry.AGENT_VERSION` points to it, while older
   tasks keep resolving theirs. Custom agents supply their own role.
3. **Environment**: today's date, the selected repository, whether editing and publishing are available, the tools.

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
| `TOOL_TIMEOUT`, `TOOL_FAILED`, `SEARCH_PROVIDER_ERROR`, `FETCH_*` | retry once differently, or report the gap |

## The workspace (E2B)

One persistent sandbox per conversation, created on first use and paused when idle. It has internet access and no
credentials; GitHub writes happen in the backend.

- **Commands** run without a terminal and with a non-interactive environment (`CI=1`, `GIT_TERMINAL_PROMPT=0`,
  `PAGER=cat`, `DEBIAN_FRONTEND=noninteractive`, …; no secrets). Each runs as
  `timeout -k 5 <timeout_sec> bash -c <command>` inside the VM, so its deadline is enforced there and reaches every child
  process; the output so far comes back with `timed_out`. If the connection is lost, or the person presses Stop, the
  backend ends the command's whole process group in the VM. The VM's lifetime is extended to cover a long command.
- **Connections**: reconnecting to an existing VM is retried after a transient failure; creating one is never retried
  (a failed create may still have allocated a VM). A dropped connection during a command or a write is a
  `SANDBOX_DISCONNECTED` result, not a failed run, and the next call reconnects.
- **Setup**: the first call of a conversation also starts the VM and uploads the selected repository's pinned commit
  (tagged `agentforge-base`), so every tool deadline includes ten minutes for that setup.
- **Replaced VMs**: when the conversation's earlier VM no longer exists, a new one is created and the next tool result
  carries a `notice` telling the agent that earlier files and changes are gone.

## Provider adapter

- Retries (`LLM_MAX_RETRIES`, default 4) with jittered exponential backoff from 1 s, honouring `retry-after` and
  `retry-after-ms` up to 30 s, for rate limits, 5xx/overloaded, timeouts and dropped connections, including a stream
  that broke after it started (the loop clears what that attempt showed first). Quota and credential errors are never
  retried. The provider's own message is what the person reads; credentials are removed.
- `LLM_TIMEOUT_SEC` (default 180) bounds each wait for data; connecting has its own 15 s limit.
- `LLM_MAX_OUTPUT_TOKENS` defaults to 16,384 (never more than half the window).
- `LLM_STRICT_TOOLS=true` sends strict structured-output schemas. Enable it after confirming the model accepts it.

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
`test_sandbox_provider.py`, `test_sandbox_shell.py` and `test_agent_prompts.py` drive the real loop, gateway, adapter and
sandbox tools with scripted models and fakes; no provider, VM or database is needed.
