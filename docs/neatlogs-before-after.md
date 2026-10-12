# Neatlogs before/after: the agent could not open the link it was given

Measured on 12 October 2026 with the real model (`gpt-6-luna`), Tavily search and the Neatlogs project, local worker,
content capture on. Same five prompts, one new conversation each, run once before and once after the change. The
driver is [`apps/api/examples/neatlogs_eval.py`](../apps/api/examples/neatlogs_eval.py); runs are tagged `baseline` and
`improved` in Neatlogs (`NEATLOGS_TAGS`), so the dashboard can be filtered to either side.

## What Neatlogs showed

1. **The first step of every task was refused.** In all five `baseline` traces the first `fetch_webpage` call is a
   `tool_policy` guardrail span with `SCOPE_DENIED`: the page the person had pasted ("Open https://www.npmjs.com/…") had
   not come from a search, and `fetch_webpage` only opened search results. The agent had to search for the person's own
   link before it could read it. The web app sends no allowed hosts, so every user hit this.
2. **A blocked page sent the agent into search loops.** Content mode showed what the model received for a failed fetch:
   `{"fetch_status": "FAILED"}` and the advice "Try another source". In the OpenAI-pricing trace that became 37 web
   searches, 49 tool calls and 430k tokens for one question.
3. **Identical failing calls were sent again.** In the earlier history 4 of the 22 failed fetches were exact repeats of a
   call that had already failed, and each failed again; in the baseline two refused fetches were repeated unchanged.

Found by reading the traces through the Neatlogs MCP (`.mcp.json`) from Claude Code: the `tools` analytics view
(fetch_webpage had the most errors per call) and the content of trace `9f2b3d1df6b30eddfb5175e10165cff2`.

## The change (commit `31eb2f4`, Entire checkpoint `01M4QGRYYNZKXFMPX3E9PEWBW3`)

- A URL written in the task's own message is now as trusted as a search result. It is matched **exactly** against the
  stored message (`subagents/resources.py`, `repositories/research.py`), so neither the model nor a page it read can add
  a query string or invent a host. Task host limits, public-address checks and redirect rules are unchanged; a URL the
  model makes up still needs a search.
- A failed fetch tells the model to use the search results it already holds (`read_research_source`) or answer, and not
  to search for the same page again (`tools/catalog.py`).
- The gateway remembers fetches that failed in a way that cannot change within a run (HTTP refusal, wrong content type,
  too large, off-host redirect) and answers the identical call with `REPEATED_FAILED_CALL` (`tools/gateway.py`).

A Neatlogs detection now watches for the pattern: **Agent tool call refused by policy** (conditional, Negative group,
created through the MCP after a preview flagged 19 refused calls in the previous 14 days) flags every `tool_policy`
guardrail span that did not pass, which covers both a refused URL and `REPEATED_FAILED_CALL`.

## Result

| | baseline | improved | change |
|---|---:|---:|---:|
| Tasks whose first fetch was refused | 5 / 5 | **0 / 5** | −100% |
| Tool calls | 95 | 65 | −32% |
| Failed or blocked tool calls | 16 | 11 | −31% |
| Model calls | 61 | 45 | −26% |
| Tokens (input + output) | 1,096,985 | 769,829 | **−30%** |
| Ledger cost | $0.1200 | $0.0861 | −28% |
| Wall time, five tasks | 471.9 s | 423.6 s | −10% |

All ten tasks completed. Per task:

| # | Prompt | Time | Tool calls | Tokens | Trace (baseline → improved) |
|---|---|---|---|---|---|
| 1 | npm react version | 109 s → **21 s** | 17 → 3 | 368k → **28k** | `d12ea0a8f5d6707f7f1485c3cb208a7d` → `5b1744236d459522ad982bf89e3b58c0` |
| 2 | Stack Overflow answer | 70 s → 103 s | 11 → 13 | 157k → 247k | `4de311d372e4bf52545089bd4994b9c4` → `23e846c131ca3603e9b86fc3deac8221` (not in Neatlogs, see below) |
| 3 | OpenAI pricing | 209 s → 242 s | 49 → 37 | 430k → 419k | `9f2b3d1df6b30eddfb5175e10165cff2` → `3bc2e0fd7b986cb39b31a5518c1de55a` |
| 4 | G2 Linear reviews | 61 s → 45 s | 11 → 10 | 97k → 57k | `5856fc6833e92435de681ccdcf0e3664` → `5b3428ebad5490f55103e64f965b5a0b` |
| 5 | uv docs (control) | 24 s → **12 s** | 7 → 2 | 45k → **19k** | `8ce92fe5393b0545e79198f5462d4d82` → `c995e6da6e93af22547b532e3f39383e` |

Task 5 is the clean case: before, refuse → search → fetch four pages; after, one fetch and the answer. Task 1: npm still
answers 403, but the agent now answers from the search result it already has instead of searching ten more times.

## Limits of this evidence

- **One run per side, five prompts.** The model is not deterministic, so per-task numbers move between runs. The refusal
  of the person's link is structural (5/5 → 0/5 does not depend on sampling); the token and time totals are one sample.
- **Not everything improved.** Task 2 used more searches and tokens this time. Task 3 still loops on searches when the
  pricing page is blocked and the search snippets do not carry the prices: the guidance reduced tool calls (49 → 37) but
  not tokens. That is the next thing to fix (each `model_request` resends the whole context; 98% of tokens are input).
- `REPEATED_FAILED_CALL` did not fire in the improved run (the model did not repeat a failed fetch); it is covered by
  unit tests.
- The improved trace for task 2 (`23e846c1…`) was logged by the worker but is not in the Neatlogs project (trace lookup
  returns 404, search finds only the baseline trace); the worker logged no export error. Its numbers above come from
  the application database, like every other row.
- Neatlogs prices `gpt-6-luna` as unknown (cost 0 in the dashboard); costs here are the application's ledger.
