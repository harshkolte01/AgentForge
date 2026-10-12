# Agent prompts and tool descriptions

Every model call is one `developer` message followed by the conversation (`subagents/prompt_builder.py`):

1. **Platform rules** (`PLATFORM_INSTRUCTIONS` plus the research, workspace, publishing and downloadable-file sections
   that apply to the tools available). Instruction priority, how to work with tools, honesty, how to answer. Every agent,
   built-in or custom, follows them. They depend only on the tool set, so a run's requests share one cacheable prefix.
2. **Role** (`subagents/definitions/agent.v<N>.json`, `system_prompt`): the built-in agent's persona and habits, or a
   custom agent's own instructions. A role ranks below the platform rules.
3. **Environment**: the date, the selected repository, whether workspace editing and GitHub publishing are available.
   The tool names are not repeated: the tool definitions travel with every request.

The person's messages follow as real chat messages and outrank everything except layers 1 and 2. Web pages, files,
repository content, attachments and tool output are data: the model uses them but never takes instructions from them.

## Versions

Stored built-in snapshots are immutable and checked byte for byte, so a released `agent.v<N>.json` is never edited. A
change ships as a new file and `registry.AGENT_VERSION` points to it; tasks that recorded an older version keep resolving
it. v2 is the current built-in: a short persona and coding/research habits on top of the platform rules (v1 repeated
much of what the platform rules now say).

## Writing a role prompt

Keep it short and specific to the role: what it is for, the habits that make it good at that, and anything it must
never do. Do not restate the platform rules (priority, honesty, how to answer) or the tool descriptions. Name only tools
the role is allowed to use (`tests/test_agent_prompts.py` checks this).

## Tools

`tools/catalog.py` holds one description per tool: what it does, when to use it, how to continue (`next_offset`,
`next_line`), and its limits. Every argument in `tools/contracts.py` has a `description`. Provider schemas drop
pydantic's auto-generated titles and hide identifiers the task fixes (repository, commit, sandbox, task). A failed call
returns an error code, a short detail written by our own code and static guidance from `error_guidance`. Adding a tool
means adding its argument model, a description, its capability in `contracts/agent_definition.py`, and a label in
`apps/web/lib/tool-steps.ts`. See [the harness](agent-harness.md) for the whole tool-calling contract.

## Rendering activity

Run events become one timeline shared by the live view and finished replies: each tool call with a short detail (the
search query, the site's domain, the file path), and the notes the model wrote next to its calls. Tool events carry that
`detail`, derived from the validated arguments by `activity_detail` in `tools/catalog.py`; it never includes tool output
or file content. While a run is active the thread shows the steps; once the answer arrives they fold into a
"Worked · 41s · 22 steps" line above it. Replies with no tool actions show nothing.

## Live replies

The reply is shown while the model writes it.

1. `OpenAIAdapter.complete(..., on_text=..., on_narration=..., on_retry=...)` streams the completion and returns the same
   message, usage and finish reason as the non-streaming path. With tools offered, text is narration until the call ends
   without tool calls; a broken stream is requested again after `on_retry` clears what it showed.
2. `orchestration/stream.ReplyStream` keeps a throttled copy of the growing text in `task_streams`, fenced by an epoch.
   It is best effort: any failure switches streaming off for the run and the task still completes.
3. The task's SSE endpoint sends `message.delta` events (`epoch`, `revision`, `kind`, `offset`, `text`, `reset`). They
   are not part of the durable event log, so a reconnect restarts from offset 0.
4. The saved message takes the live text's place without a second entrance.
