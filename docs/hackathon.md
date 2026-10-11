# Hackathon evidence pack

Checked against [official neatHack requirements](https://neatlogs.com/hackathon)
on 10 October 2026. Code-only preparation is complete here; partner-account work,
deployment, recording and posting remain outstanding. **This is not a submission.**

The published window starts 10 October at 12:30 IST, with submissions opening
12 October at 19:00 IST and a planned close around 23:55 IST; the final cutoff is
announced on Discord. Do not alter commit dates or imply pre-window code is
eligible. The official requirements call for Neatlogs, Entire and cfo.ai evidence,
a public repository and a video no longer than three minutes. A quote-post draft
does not submit the entry.

## Evidence manifest

| Claim | Artifact to attach | Current state |
|---|---|---|
| Substantial task completes with evidence | Actual task receipt, source/test evidence and artifact download | Offline fixtures available; live end-to-end pending |
| Failure recovery works | Disclosed failing attempt and subsequent legitimate action | Actual generated fixture exits available; live trace pending |
| Neatlogs improved the agent | Before/after hosted trace links, comparable latency/tokens/cost and change | Integration implemented and wire-verified offline (`docs/neatlogs.md`); hosted before/after traces pending |
| Entire used during implementation | Allowed checkpoint + commit or actual versioned entire-graph output | Pending; no conversation/transcript export authorized |
| Business plan uses cfo.ai | Export/screenshots covering pricing, costs, revenue and runway | Assumption template below; cfo.ai export pending |
| Built in public | Actual dated #neatHack posts | Pending |
| Submitted | Public quote of official post with team/name/project/repo/demo | Pending; follow confirmed cutoff |

Keep raw traces, user repositories and credentials private. Review screenshots
and exported graph/source snippets before public inclusion. By default the
application sends trace metadata only (structure, timing, tokens, cost, status);
`NEATLOGS_CAPTURE_CONTENT=true` adds prompts and tool data with best-effort secret
masking, which makes the inputs/responses visible in a demo trace. Still inspect
actual exports before sharing. Do not substitute a
mocked-provider receipt for hosted evidence.

## Neatlogs evidence

How the trace maps to each thing the official page asks to trace, how to run a before/after comparison with
`NEATLOGS_TAGS`, how to produce a failure-recovery trace, and what has and has not been verified are in
[neatlogs.md](neatlogs.md). Offline checks (`python -m neatlogs doctor --local`, `tests/test_neatlogs_wire.py`,
`examples/neatlogs_trace_smoke.py`) prove what would be exported; only a run with a real project key produces the hosted
trace links this pack needs. Record the worker's `neatlogs_trace_id=` log line for each run you want to cite.

## Entire graph without transcript export

The [official Entire Graph project](https://github.com/entireio/entire-graph)
documents local static analysis and `entire graph health --repo . --json`. After
an operator installs a verified CLI/plugin, record `entire graph version`, the
commit being analyzed, and actual health/query output for a code question such as
approval reconciliation or worker lease fencing. Keep original output; do not
claim a graph/checkpoint has been produced before running the command. Do not run
`init-agents` merely for evidence: it writes repository guidance and should be a
separate intentional integration. No chat transcripts are needed for graph-only
code analysis.

## Business-plan assumptions for cfo.ai

Export internal cost aggregates with:

```sh
cd apps/api
uv run python -m agentforge.operations.business_metrics --days 7 --output /secure-path/agentforge-costs.json
```

This performs SELECT-only aggregation across application users and creates a new
mode-0600 file. It exports task counts and measured/conservative ledger costs;
unknown operation costs retain their recorded upper bound. No goals, prompts,
user/task IDs, repository names, artifact content or secrets are included. It is
operator-only business evidence, **not** a provider invoice or cfo.ai export.

Enter these explicit assumptions into cfo.ai, using measured results where
available and labeling every unmeasured value:

| Input | Evidence/value source |
|---|---|
| Monthly price and included task allowance | Product hypothesis; choose after measured task distribution |
| Active paid customers | Scenario assumption; no existing revenue claimed |
| Average tasks/customer/month | Scenario assumption plus usage aggregates |
| Model/research/E2B cost per task | Provider invoices and ledger totals; retain unknown-cost bounds |
| EC2/EBS/backup/egress cost | Current regional estimate and actual AWS billing; exclude promotional credits from steady-state margin |
| Support/operations and payment fees | Explicit estimates, not zero by omission |
| Cash balance and monthly overhead | Owner-supplied private business assumptions |

Revenue = customers × monthly price. Variable cost = customers × tasks/customer ×
measured cost/task. Gross margin = (revenue − variable costs − hosting costs −
payment fees) / revenue, when revenue is nonzero. Runway = cash / net monthly burn,
when burn is positive. Compare base/downside scenarios and show failure/unknown
cost sensitivity. Never present fixture costs as live model unit economics.

## Demo script (target 2:50)

1. **0:00–0:20:** State the user problem and show a real named conversation.
2. **0:20–1:10:** Start one bounded task; show plan, child runs and evidence.
   For coding, show the actual failing test, repair and independent review.
3. **1:10–1:35:** Show the final artifact/receipt and the separate exact-change
   approval. Only show live PR delivery after controlled qualification succeeds.
4. **1:35–2:05:** Show actual Neatlogs failure/success traces and measured change.
5. **2:05–2:25:** Show actual Entire checkpoint or graph output and what it helped
   inspect; briefly explain its implementation role.
6. **2:25–2:45:** Show the cfo.ai export: assumptions, pricing, cost and runway.
7. **2:45–2:50:** Show repository/demo links and honest remaining limitations.

Record actual results; label fixture setup and time compression. Check the final
video duration, audio, public link permissions and visible secrets. During the
official submission window, quote the official post with the required team/name,
project, public repository and demo link, publish, and save the actual post URL.
No public post, account purchase or submission is performed by this code change.
