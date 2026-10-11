# The agent

One loop serves every message: the model gets the conversation, a system prompt and its tools; it calls tools until it
has what it needs, then answers in plain text. That text is the answer, exactly as written
(`apps/api/src/agentforge/subagents/runtime.py`).

## What was removed, and why

Earlier versions ran a router, a planner, stage agents and an independent verifier, and forced every run to end by
submitting a typed result with evidence. A reply that did not satisfy those rules was rewritten as `PARTIAL`, so a
plain "What would you like help with?" showed a "Partial result" banner. All of that is gone: no router, planner,
verifier, `submit_result`, `PARTIAL` status or run-details page. A run ends as:

| Outcome | When | Shown |
| --- | --- | --- |
| `COMPLETED` | the model answered | its text, as written |
| `FAILED` | the provider or a tool broke | the reason: a provider's own message, credentials removed |
| `CANCELLED` | the person pressed Stop | "Stopped." |

Older messages may still carry `PARTIAL`; they render as plain text.

## Limits

Nothing caps steps, tool calls, cost or time. Usage is still recorded for accounting, and Stop always works. The
numbers in the budget rows (`Budget`, `UNLIMITED_*`) exist because the ledger needs positive values; they are set
high enough never to refuse anything.

## Tools

| Group | Tools |
| --- | --- |
| Web | `web_search`, `fetch_webpage` |
| Repository (read only, pinned commit) | `list_repo_files`, `search_repo`, `read_repo_file`, `get_issue`, `get_pr_status` |
| Sandbox (needs `E2B_API_KEY`) | `bash`, `read`, `write`, `edit`, `grep`, `find`, `ls` |
| Pull request (needs GitHub App + `ENABLE_GITHUB_WRITES`) | `create_pull_request` |
| Files and history | `create_task_artifact`, `read_task_artifact`, `search_conversation_history`, `read_context_resource` |
| Skills (when the person has any switched on) | `use_skill`: loads a playbook by name; see [skills](skills.md) |
| Plugins (per conversation) | `slug__tool`, from the MCP servers the person connected; see [plugins](plugins.md) |

The sandbox (`integrations/e2b_sandbox.py`, `tools/sandbox_shell.py`) is created on first use and kept for the
conversation: files and installed packages stay between messages. It has internet access and holds no secrets. When a
repository is selected the backend uploads its pinned commit and commits it as `agentforge-base`; pull requests are opened
by the backend from `git diff agentforge-base`, so GitHub credentials never enter the sandbox.

## Approvals

A global setting (`GET/PUT /api/v1/settings/approval-mode`, default `ask`) applies to every conversation.

* **Ask me**: before `bash`, `write`, `edit` or `create_pull_request` the run stops and the chat shows an Approve /
  Decline card (`approval.required`) with exactly what will run: the whole command, the file content or the edit, or
  the pull request with every file it changes. It waits as long as it takes. Declining returns a normal tool result
  telling the agent the person said no. A command too long to show in full is refused before anyone is asked.
* **Approve for me**: nothing asks.

A plugin tool also asks in **Ask me** mode unless its server declares it read-only.

The mode is read before each call, so switching it releases a waiting card. Answers: `POST /api/v1/tool-approvals/{id}`.
Implementation: `tools/approvals.py`, hooked into `tools/gateway.py`.
