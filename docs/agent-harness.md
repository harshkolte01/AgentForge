# Agent harness and tool calling

How a child agent acts, how each tool call is checked, and what the model is told when something goes wrong. The
design goal is that every call is precise: the right tool, with arguments that match its contract, exactly once, and
that a mistake is explained well enough to be fixed in one more call.

Code map: `subagents/runtime.py` (the loop), `tools/gateway.py` (every call), `tools/catalog.py` and
`tools/contracts.py` (the closed tool set), `tools/errors.py` (typed errors), `llm/openai_adapter.py` (the provider),
`subagents/context.py` (compaction).

## The loop

```
model -> tools -> model -> ... -> submit_result
```

1. Each turn the model sees the permitted tools plus a synthetic **`submit_result`** whose parameters are the result
   schema for its role. It finishes by calling it, so the result is validated like any other call.
2. A rejected result comes back as a tool message naming the exact fields to fix (`RESULT REJECTED: ...`). The model
   keeps its tools, so it can still gather missing evidence. After two failed repairs the run ends `PARTIAL`.
3. If the model answers in plain content instead, a valid JSON result is still accepted (older models and fixtures);
   anything else gets one forced `submit_result` turn.

### Limits end a run gracefully

Every tool result carries `limits` (`steps_left`, `tool_calls_left`) and a notice when either is nearly gone. The next
turn is a **forced `submit_result`** (tools reduced to that one, `tool_choice` pinned) when any of these is true:

| Reason | Trigger |
| --- | --- |
| steps | the last allowed model step |
| tool_calls | no tool calls left |
| time | under 20 s (or a quarter of the budget, if smaller) before the deadline |
| errors | 5 consecutive failed tool calls |

The model then reports what it has with `PARTIAL`/`BLOCKED`, instead of the run being thrown away as `BLOCKED`. A
model that calls another tool anyway ends `PARTIAL`. The hard wall-clock timeout remains as a backstop.

### Concurrency

Independent read-only calls in one turn run together (up to 4 at a time). A turn that contains anything with an effect
(`create_task_artifact`, `write_sandbox_file`, `edit_sandbox_file`, `run_sandbox_command`, `run_tests`) runs strictly in
order, and effectful calls hold an exclusive lock. Every requested call always gets a reply, in request order.

### Context

`maximum_request_cost` bounds every request. When the conversation outgrows half of the usable window
(`context_budget_bytes`), the oldest tool results are replaced by a stub that keeps the tool name, status and every
evidence/artifact id; the four newest results stay whole. Stored state is never changed, so citations stay valid.

## One call, step by step (`ToolGateway.execute`)

1. The ledger commits an **intent** (digest only) before anything else.
2. Re-authorize the task, check the budget, and require the tool to be installed **and** permitted.
3. **Parse**: invalid JSON or a non-object is `SCHEMA_INVALID`; `null` for an optional field means omitted; identifiers
   the task fixes are filled in (below); then strict pydantic validation. Errors list each field and the constraint it
   broke, never the offending value.
4. Scope checks (`authorize_tool`), resource ownership, and for web tools the pinned, resolved URL.
5. **Duplicate guard** for read-only tools: an identical call that already succeeded returns `DUPLICATE_CALL` pointing at
   the earlier call. It resets after any effectful call, and a failed read can be retried.
6. Reserve cost, run the adapter under its timeout, re-validate its output, record usage, verify evidence ownership.
7. The ledger records the outcome: status, error code, safe arguments, duration, attempt number.

### Identifiers the model never types

`repo_id`, `ref`, `base_ref`, `sandbox_id` and `task_id` have exactly one authorized value per task, so the resolver
(`bindings()`) supplies them and the schema hides them. If a model supplies one anyway, an equal value is accepted and a
different one is refused with `SCOPE_DENIED`. A resolver that cannot bind a field leaves it in the schema.

### Errors

Every failure is an outcome with `error_code`, an optional `detail`, and static `guidance`. Adapters raise
`ToolError(code, detail)` for a definite, effect-free rejection; the gateway treats it as a no-op (cost 0, status
`FAILED`) even for effectful tools. Any other exception from an effectful tool is `UNKNOWN_REMOTE_OUTCOME`, which stops the
run and is never retried automatically.

| Code | Meaning / what the model should do |
| --- | --- |
| `SCHEMA_INVALID` | detail lists the wrong fields; fix exactly those |
| `SCOPE_DENIED`, `TOOL_UNAVAILABLE` | not allowed / not installed; do not retry |
| `FILE_NOT_FOUND` | detail suggests the closest real paths or directory names |
| `BINARY_FILE`, `INVALID_RANGE`, `LINE_TOO_LONG` | cannot read that; adjust the range or skip |
| `FILE_EXISTS`, `STALE_WRITE` | read the file again (the current hash is deliberately not revealed) |
| `EDIT_NO_MATCH`, `EDIT_AMBIGUOUS` | copy the exact text / add surrounding lines; detail says whether only whitespace differs, or how many places match |
| `PROFILE_MISMATCH` | use `run_tests` for tests and `run_sandbox_command` for builds |
| `DUPLICATE_CALL` | reuse the earlier result or change the arguments |
| `TOOL_LIMIT_REACHED` | no calls left; submit now |
| `TOOL_TIMEOUT`, `TOOL_FAILED`, `SEARCH_PROVIDER_ERROR`, `FETCH_*` | retry once differently, or record the gap |

Model-provider failures use the taxonomy in the plan: `RATE_LIMIT`, `MODEL_PROVIDER_ERROR`, `AUTH_ERROR`,
`MODEL_CONTEXT_LIMIT` (plus `MODEL_QUOTA_EXCEEDED`).

## Tools

| Tool | Notes |
| --- | --- |
| `read_repo_file`, `read_sandbox_file` | paged by line; missing path gives `FILE_NOT_FOUND` with suggestions |
| `list_repo_files` | `max_depth` collapses deep folders to `folder/ (N files)` |
| `search_repo` | plain substring (no regular expressions, deliberately: untrusted patterns can hang the worker); `path_prefix`, `case_sensitive`, `context_lines` (0-3), `max_results` (1-50) |
| `edit_sandbox_file` | exact `old_string` -> `new_string`; one match unless `replace_all`; no hash needed; handles CRLF files |
| `write_sandbox_file` | new files, or whole-file replacement with `expected_sha256`; warns on protected test files |
| `run_tests`, `run_sandbox_command` | profile only; no arguments exist |
| `web_search`, `fetch_webpage`, `read_task_input`, `read_research_source`, `create_task_artifact`, `read_task_artifact`, `get_issue`, `git_diff`, `get_test_results` | unchanged contracts |
| `get_pr_status`, `browser_public_page` | contracts exist, **no adapter is installed**; calls return `TOOL_UNAVAILABLE` and the schema is never offered |

## Provider adapter

- With tools offered there is no JSON mode (it only pushes the model to write JSON text instead of calling a tool);
  `tool_choice` is `auto`, or pinned to `submit_result` on a forced turn.
- Retries are bounded (`LLM_MAX_RETRIES`, default 2), jittered, honour `Retry-After` up to 8 s, and apply only to rate
  limits, 5xx and refused connections, where no completion was produced and nothing was billed. Timeouts and failures
  after output began are never repeated. Raw SDK messages never leave the adapter.
- `LLM_STRICT_TOOLS=true` sends strict structured-output schemas (every field required, optional ones nullable; length
  bounds move into the description). It is off by default: enable it after confirming the configured model accepts it.
- The reply streams from the `submit_result` arguments (or from content when no tools are offered).

## Audit and observability

`tool_calls.result_json` records, for every call: status, `attempt`, `duration_ms`, structural `arguments` (paths, ids,
numbers; free text and file content become `*_chars` lengths; URLs become their host) and, for rejected calls, the
validation `issues`. Self-corrections (`SCHEMA_INVALID`, `DUPLICATE_CALL`, `TOOL_LIMIT_REACHED`) are hidden from the
person-facing activity timeline.

## Tests

`tests/test_tool_gateway.py`, `test_child_runtime.py`, `test_llm_adapter.py`, `test_tool_handlers.py` and
`test_tool_schemas.py` drive the real gateway, loop and adapter with scripted models and fakes. No live provider or
database is involved, so they run anywhere. A live-model evaluation of tool selection quality is still to be done.
