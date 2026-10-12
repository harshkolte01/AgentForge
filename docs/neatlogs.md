# Neatlogs tracing

Every task the worker runs is traced to [Neatlogs](https://docs.neatlogs.com) when `NEATLOGS_API_KEY` is set. This page says
what is recorded, what is never sent, how to configure it, how to check it, and how to use it to improve the agent.

Sources: the [Python SDK](https://docs.neatlogs.com/sdk/python), [sessions](https://docs.neatlogs.com/sdk/sessions),
[span kinds](https://docs.neatlogs.com/sdk/span-kinds), [lifecycle](https://docs.neatlogs.com/sdk/lifecycle),
[SDK Doctor](https://docs.neatlogs.com/sdk/doctor) and the [MCP server](https://docs.neatlogs.com/integrations/mcp), checked
against the published `neatlogs` 1.4.26 wheel on 12 October 2026. The neatHack requirement is to trace every agent run: model
and tool calls, inputs and responses, state changes, retries, failures, agent messages, latency, token usage and cost. The
table below maps each of those to where it appears.

## What a trace contains

One trace per task (a task is one chat turn). Turns of a conversation are one **session**.

```
agentforge_task                      WORKFLOW   session = conversation, end user = person, tags, final status
├─ prepare_context                   CHAIN      history compaction / summary for this turn
└─ agent:builtin/agent               AGENT      the run; ERROR when the run failed, even though it returned a result
   ├─ <langgraph node>               CHAIN      LangGraph callback handler: graph steps
   ├─ model_request                  LLM        model, provider, tokens, cost, finish reason, retries
   ├─ tool_approval                  GUARDRAIL  "Ask me" decision: passed / blocked (the wait is the span's duration)
   ├─ tool_policy                    GUARDRAIL  a call refused before it ran (scope, schema, budget …) with its code
   ├─ web_search / bash / write …    TOOL       one per call; ERROR with the tool's code when it failed
   └─ agent:builtin/agent            AGENT      a delegated subagent, nested under the `delegate_task` TOOL span
```

| Hackathon requirement | Where it is in the trace |
|---|---|
| Model calls | `model_request` (LLM): `neatlogs.llm.model_name`, `.provider`, `.token_count.prompt/completion/total`, `.cost_usd`, `.choices.0.finish_reason`; `agentforge.llm.messages`, `.tools_offered`, `.tool_calls`, `.cost_known`, `.reported_model` |
| Tool calls | one `TOOL` span per call: `tool.name`, `status`, `agentforge.tool.cost_usd`, `.evidence_count`, `.artifact_count` |
| Inputs and responses | content mode only (below); otherwise sizes and counts only |
| State changes | `agentforge.task.status` on the root; LangGraph node spans; `prepare_context`; `agentforge.agent.depth` / `.parent_run_id` |
| Retries | `agentforge.llm.retries` and `.retry_reasons` (rate limit, 5xx, timeout, broken stream); a tool that is retried by the model is a second `TOOL` span |
| Failures | span status `ERROR` + `agentforge.error.code` / `.type` on the failing span; an exception event whose type is the failure's code (`FETCH_COMPRESSED_CONTENT_REJECTED`, `RATE_LIMIT` …), never its text; shown by Neatlogs as the span's error type; the root is `ERROR` when the task failed |
| Failure recovery | root `agentforge.task.tool_failures` > 0 with `agentforge.task.status` = `COMPLETED`: the run hit failures and finished. The failing span is followed by the spans the agent chose next |
| Agent messages | the `AGENT` span per run and its parent link (`agentforge.agent.parent_run_id`); text only in content mode |
| Latency | span duration (`neatlogs.metrics.duration_ms`); the approval wait is its own span, separate from tool time |
| Tokens and cost | tokens per `LLM` span, summed per trace by Neatlogs. Neatlogs prices cost itself from its own model table (see Limits); AgentForge's ledger is the authoritative cost. An unknown cost is `agentforge.llm.cost_known = false`, never `0` |
| Guardrails | `tool_approval` / `tool_policy`: `neatlogs.guardrail.passed`, `.name`, `.reason` |

Run ids tie a trace to the database: `spawn_id` on an `AGENT`/`TOOL` span is the `agent_runs.id`, `task_id` is `tasks.id`, and
the session id is the conversation id. The worker also logs `neatlogs_trace_id=<id>` for each task at INFO.

## What is sent

| Mode | Setting | Sent | Never sent |
|---|---|---|---|
| **metadata** (default) | `NEATLOGS_CAPTURE_CONTENT=false` | structure, timing, model, tokens, cost, status, error codes, tags, session and end-user ids, tool names | prompts, answers, tool arguments and results, repository text, URLs, exception messages and stacks, logs |
| **content** | `NEATLOGS_CAPTURE_CONTENT=true` | the above plus prompts, replies, tool arguments/results, LangGraph state; each value cut at 6000 characters; known secret shapes and every configured secret value masked | exception stacks, status text, logs |

The rule is enforced by one export mask (`telemetry/neatlogs_setup.py`) that runs on every span after the SDK normalises its
attributes, so a callback or a new attribute cannot widen it: anything not on the allowlist (or under `agentforge.`) is dropped
in metadata mode. Content mode redacts on a best-effort basis (API-key shapes, bearer tokens, private keys, URL credentials,
`password=`/`token:` pairs, JWTs, and the exact secrets in the settings). **It is not a guarantee**; turn it on only for demos
and debugging, on data you are willing to share, and review a trace before publishing a screenshot. The end-user id is the
person's internal id; no name, email or GitHub login is sent.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `NEATLOGS_API_KEY` | unset | Project key. Unset: nothing is traced and nothing is imported. |
| `NEATLOGS_WORKFLOW_NAME` | `agentforge-ai` | Names the application in the dashboard. Keep it stable across deployments. |
| `NEATLOGS_ENDPOINT` | SDK default | Base URL only (no path), e.g. `https://eu.ingest.neatlogs.com` for an EU project. |
| `NEATLOGS_TAGS` | empty | Comma-separated labels (max 10, 64 chars each) added to every trace, e.g. `baseline`, `prompt-v3`. |
| `NEATLOGS_SAMPLE_RATE` | `1` | Share of traces exported. Keep `1` when collecting evidence. |
| `NEATLOGS_CAPTURE_CONTENT` | `false` | See above. |

`env:<APP_ENV>`, `release:<version>` and `model:<LLM_MODEL>` tags are always added; `user.id` is `agentforge-worker` (the
service, not a person). Tracing is **never allowed to stop a run**: if the SDK cannot start the worker logs an error and
runs untraced; a span that cannot be opened or closed is logged and skipped; attributes that cannot be set are ignored. On
shutdown the worker stops claiming tasks, lets running ones finish (or records them interrupted), then flushes and closes the
SDK once, each step bounded to 10 seconds (never a flush per request, as the SDK lifecycle guide asks).

## Checking it

Offline, no key, no network:

```sh
cd apps/api
uv run python -m neatlogs doctor --local --json        # the installed SDK produces a valid local trace (also run by release_checks.py)
uv run python -m agentforge.doctor                      # "neatlogs" line: on/off, SDK version, metadata or content
uv run python -m unittest discover -s tests -p "test_neatlogs*.py" -v
uv run python examples/neatlogs_trace_smoke.py          # real worker + real SDK + throwaway Postgres + a local OTLP receiver
```

`tests/test_neatlogs_wire.py` and the smoke fixture run the *real* SDK against a receiver on 127.0.0.1 and decode exactly what
would be sent: tokens, cost, model, session grouping, error status, parent/child structure, and the absence of prompts and
secrets in metadata mode.

Against the hosted project (needs the key; sends a synthetic trace; do not paste the key into logs):

```sh
export NEATLOGS_API_KEY=...        # from your shell or secret store, never a committed file
cd apps/api && uv run python -m neatlogs doctor --probe --json
```

A Doctor pass shows the SDK and key work; it does not show that this application's trace is right. Run one real task with
the worker and open its trace: one root, `agentforge_task`, in the conversation's session, with the tree above.

## Using Neatlogs to improve the agent

The loop the hackathon asks for is *measure, change one thing, measure again, keep the evidence*.

1. **Baseline.** Start the worker with `NEATLOGS_TAGS=baseline`, run a fixed set of tasks (same prompts), note the trace ids
   from the worker log.
2. **Find the problem.** In the dashboard filter by `baseline` and sort by cost or duration; open the worst trace. Look for
   repeated `ERROR` spans, `tool_policy` refusals (the model is calling tools wrongly), `agentforge.llm.retries`, and many
   `model_request` spans (`agentforge.llm.messages` growing means context pressure).
3. **Or ask your coding agent.** The Neatlogs MCP server lets it read traces, detections and fix suggestions directly:

   ```json
   { "mcpServers": { "neatlogs": { "type": "http", "url": "https://ingest.neatlogs.com/mcp",
       "headers": { "Authorization": "Bearer ${NEATLOGS_API_KEY}" } } } }
   ```

   (`.mcp.json` for Claude Code, `.cursor/mcp.json` for Cursor, or `npx @neatlogs/wizard mcp --api-key <key>`. Keep the key in
   the environment. Use the EU host for an EU project.) The repository's `.mcp.json` configures it for Claude Code and reads
   `NEATLOGS_API_KEY` from the environment. Checked on 12 October 2026 with the project key: `initialize` and `tools/list`
   answered, offering `search_traces`, `get_trace_context`, `get_project_analytics`, the `triage_*` tools and
   `create_detection` among others.
4. **Change one thing** (a prompt, a tool description, a budget) and restart the worker with `NEATLOGS_TAGS=improved`.
5. **Compare** the same tasks: tokens, cost, duration, error spans, and `agentforge.task.tool_failures` per task. Save both
   trace links (or screenshots) with the numbers; that is the before/after evidence.

A **failure-recovery** example: run a task that makes the agent hit a real failure (for instance ask it to read a file that
does not exist, or decline an "Ask me" approval). In the trace the failing `TOOL`/`tool_policy`/`tool_approval` span is `ERROR`
or blocked, the next span is what the agent did instead, and the root ends `COMPLETED` with `tool_failures` > 0.

## Verified against a hosted project

Run on 12 October 2026 with a real project key (worker, API and web started locally, real model and search calls): the SDK
doctor probe passed; two conversations' tasks arrived as one trace each, grouped under the conversation's session, with the
tree above, per-call model, provider and token counts, tool spans, and `ERROR` spans for real tool failures carrying their
code as the error type. In one run the agent hit two `FETCH_COMPRESSED_CONTENT_REJECTED` failures and still finished with an
answer, which is the failure-recovery trace to cite. Read a trace back with
`GET <endpoint>/api/traces/v3/<trace id>` and the `x-api-key` header (what the SDK doctor does).

## Limits

- **Cost shows as "unpriced" for a model Neatlogs does not know.** Neatlogs computes cost server side from its model table and
  does not take the cost we send (`neatlogs.llm.cost_usd`; the hosted read-back reported `pricing_status: unpriced`,
  `unknown_model` for the configured model). Tokens, model and provider are shown correctly. Use the ledger for money:
  `python -m agentforge.operations.business_metrics`.
- The `agentforge.*` labels are sent but the hosted read-back API does not list them; check the dashboard's span detail to see
  whether it shows them. Status, error type, session, end user, tool name, model and tokens are shown by the read-back.
- The dashboard's trace-link format is not documented by Neatlogs, so the application records the **trace id** (worker log
  and the root span) rather than building URLs.
- Provider retries are counted for the OpenAI-compatible adapter (`on_attempt_failed`); a model adapter that does not offer
  the hook is traced without a retry count.
- Content mode redaction is pattern based. Metadata mode is the only mode with a hard guarantee (an allowlist).
- No hosted trace has been produced from this repository by the code change itself; that evidence comes from running it with
  a project key (see `docs/hackathon.md`).
