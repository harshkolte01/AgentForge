# Delegated assignments

The default assistant (immutable `builtin/agent` version 3) can call
`delegate_task` with a short `title`, a self-contained `prompt`, and an optional
`mode` (`research` by default, or `work`). Existing tasks pinned to versions 1
and 2 retain their original definitions.

Delegation is synchronous, and independent assignments run in parallel. When the
main agent calls `delegate_task` several times in one reply, the children start
together and the agent receives every report once all have finished, in the order
it asked for them; it then verifies and integrates them. A reply that also calls
`bash`, `write` or `edit` stays strictly in order, as before. The tool description
is what teaches the model to launch independent assignments together (the agent
prompt is an immutable version). Work assignments share one workspace, so the
model is told to give parallel ones different files; `write` replaces a whole file,
so two children writing the same path would overwrite each other. A child that
fails or times out hands back its last few progress notes (`progress_notes`) so the
parent can build on its work. The child uses the configured model in a
separate conversation containing its assignment, platform instructions, and
authorized environment information. Parent messages are not automatically
copied. The conversation workspace and task-owned resources remain shared.

Research assignments cannot write files or execute commands. Work assignments
can use those tools only within the parent's grants, with the same approval
policy. Neither mode can delegate again or invoke `create_pull_request`.
The parent tool allowlist is a ceiling even when the packaged child definition
contains more tools. Settings' existing custom research agents retain their
current tool restrictions.

Each parent can create at most eight children, which is also the most that can
work at once. Each child has at most 40 model
operations, 80 tool operations, 600 seconds, and $5 in configured usage bounds,
further narrowed by inherited limits. These limits do not change the main
conversation's existing behavior. Model retries retain usage accounting through
the telemetry wrapper. Actual provider invoices can differ from configured cost
estimates.

## Persistence and recovery

Migration `0015_delegated_runs` adds the parent link, assignment title and prompt
to `agent_runs`, and permits child reservations at depth 2. Apply migrations
before restarting the API and worker. Downgrade refuses to remove depth-2 support
while child reservations remain; it does not discard historical records.

The child ID is derived from the parent tool operation identity. A task-locked
reservation transaction admits siblings of the running parent (counting ones whose
run row is not written yet, so a burst cannot pass the limit of eight) and rejects
unauthorized parents and any other active run.
The child owns its model/tool charges; the delegation adapter adds no second
charge. Both runs settle on cancellation. Existing worker lease supervision and
reconciliation preserve ambiguous outcomes without replaying external effects.
An uncertain child action stops the parent instead of suggesting a blind retry.

## Inspection

Each assignment is a card in the conversation, where it was started: title, status,
a running clock, step count and what the subagent is doing now. Subagents that stand
together get a heading ("3 subagents · 2 working"). The card needs no request of its
own: the delegation's `tool.started` event carries `child_run_id`, and the child's
events (which carry `parent_run_id`) update the card without appearing as steps of
the main run or in its counts. A subagent's Approve / Decline card is labelled with
its assignment (`approval.required` carries `agent_run_id`), and its card says it is
waiting. Runs recorded before this have neither field and show a plain card.
Opening a card reads
`GET /api/v1/tasks/{task_id}/tool-calls/{tool_call_id}/subagent`, with task and
project ownership enforced. It shows the assignment, child notes and tool
activity, terminal status, duration, and saved report. While open and running,
it refreshes every 1.5 seconds; closing aborts the request. Failed reads retain
the last result and offer Retry.

Child events keep their original task sequence and carry a parent marker.
They appear inside the assignment instead of duplicating the main timeline or
its counts. The detail response is bounded to the latest 200 events and marks
truncation. Reports remain on the run; large tool results are stored as context
resources before the parent receives a bounded preview.

## Verification

`scripts/release_checks.py` includes `apps/api/examples/subagent_smoke.py`.
The disposable PostgreSQL fixture exercises real run persistence, the model
loop, tool gateway, child results, owner isolation, declined writes, cancellation,
usage limits, and crash reconciliation without live provider effects.

Unit coverage lives in `test_delegation.py`, `test_telemetry_wrappers.py`, and
`activity-subagents.test.mjs`. Browser verification should cover desktop/mobile,
light/dark themes, keyboard disclosure, transient errors, and live updates.
Hosted rollout and real external workspace operations need their own environment
qualification; a local arithmetic delegation does not validate those services.
