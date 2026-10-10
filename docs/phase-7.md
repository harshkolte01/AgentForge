# Phase 7 — Release implementation and qualification

Updated 10 October 2026. Release tooling is implemented; **Gate P7 remains open**.
A local offline check cannot establish deployed OAuth, provider behavior or an
actual hackathon submission.

## Implemented release surface

- SHA-pinned GitHub Actions checkouts/tool setup, locked dependencies, Python
  unit checks and uniquely named disposable PostgreSQL workflows, frontend
  lint/route-types/TypeScript/build. CI needs an actual hosted run after push.
- `scripts/release_checks.py`: repeatable checks with private logs, log hashes,
  commit/dirty-tree identity and explicit unverified gates in a JSON receipt.
- Nonroot API/worker/web images, same-origin TLS proxy, bounded request sizes,
  unbuffered event streams and restricted trusted proxy address. App services and
  PostgreSQL have no host ports. Managed E2B remains separate.
- Explicit privileged migration service, non-superuser runtime database grants,
  separate admin/runtime environment files and encrypted persistent-disk setup.
- Read-only operational snapshot and aggregate ledger export; consistent DB plus
  artifact backup script and a new-volume restore/restart/retention runbook.
- README, [deployment runbook](../infra/deploy/README.md) and
  [hackathon evidence pack](hackathon.md), including a ≤3-minute demo outline.

No schema revision was added for Phase7. Final frozen offline checks passed65
unit tests and all five disposable workflows (Phase4 coding, Phase5 identity/
delivery, Phase6 sessions/custom agents, Phase6 nine-mode research/general and
Phase7 operational aggregates). The receipt records `93dd311` plus working-input
hashes, with no source changes during checks. Two later negative tests cover
configuration/engine exceptions without leaking their secret-bearing messages;
the final full unit suite passed67 tests. Phase6's isolated published snapshot
passed63 core tests plus fixtures and frontend lint/types/build.

Compose `config --quiet`, shell syntax, Python compilation and whitespace checks
passed. Backup tests preserve originally stopped services on success and dump
failure. Hosted CI, container builds and public deployment were not run.

## Authorized local runtime handoff

After code freeze, the queue was empty. A mode-0600 custom PostgreSQL dump and
manifest were written under ignored `.local/backups/` and verified readable by
`pg_restore --list`. Additive revisions0006/0007 were then applied to the existing
development database; the resulting head is `0007_sessions_agents`. Existing
checkpoint tables were retained without additional setup DDL. The private artifact
directory was created with mode0700. Same-command loopback API/worker/Next.js
services were restarted with private logs. An initial incorrect Node binary path
was corrected; the web service then started successfully.

API live/ready and programmatic local session login/logout passed; owner-only
projects, sessions, agents and configuration GETs returned200. Workspace, all-runs,
connections, agents, new-agent and settings routes plus the web API proxy returned
200. No task or paid provider call was created. These checks verify local HTTP
contracts and served routes, not browser hydration/interactions, public OAuth,
live model results or AWS deployment. Worker process existence was checked;
idle-worker liveness remains honestly `not_observed` in the API/ops snapshot.

## Golden fixtures and honest evidence

| Fixture | Reproducible local evidence | Remaining qualification |
|---|---|---|
| A: research with official sources, contradictions and artifact | Research fixture uses injected sources/models; deterministic citations and semantic verification must be distinguished | Live official-source research, nested hosted trace, browser download |
| B: permitted repository failing test → patch → independent review | `phase4_offline_smoke.py` runs exact generated programs with actual failing/passing exits and current-revision reviewer evidence | Real parent/model/GitHub/E2B task and browser receipt |
| C: disclosed first incorrect patch → repair | Same Phase4 fixture records real first failure, bounded repair and truthful incomplete outcomes | Live provider failure/recovery trace and demonstration |
| D: unauthorized custom agent attempts an ungranted tool | Unit gateway/definition checks and Phase6 immutable custom-agent tests cover grant/version constraints | Live adversarial custom-agent run with visible denial event |
| E: resume completed child + reconcile ambiguous write | Phase6 persisted-state/recovery fixture and Phase5 mocked PR response-loss reconciliation | Forced process interruption/restart mid-graph, no duplicate paid call or GitHub write on real services |

These are partial fixture mappings, not a declaration that all A–E pass. The
Phase4 local execution adapter accepts only fixed generated fixture programs; it
does not execute downloaded repositories on the development host. E2B synthetic
template evidence from Phase4 is separate from a full live coding task.

## Release checks

Run `uv run --project apps/api python scripts/release_checks.py` from the root.
The helper requires a loopback DB and creates random prefixed databases before
any migrations. An optional `TEST_DATABASE_ADMIN_URL` must name `postgres` or
`template1` on loopback with no query options; CI uses a disposable service. No
credentials are printed. The command records skipped or externally unverified
work and never marks `hackathon_gate_passed` true.

Container runtime versions are pinned to current official-image tags; they still
need `build --pull`, security scanning, resolved digest recording and deployment
smokes. Local Docker daemon availability, TLS, authenticated streaming, actual
backup/restore and public hosting are explicitly open gates. The operational
snapshot is SELECT-only and refuses to infer idle worker liveness.

## Exit gates

Before public intake, collect a clean checkout receipt and hosted CI result, build
and scan deployment images, qualify the protected new-volume restore drill, verify
TLS/cookies/OAuth/install/expiry/revocation and repository selection, prove SSE
stream/reconnect/logout behavior, and run controlled canary tasks with actual
provider costs. Enable signup and GitHub delivery only after those checks. The
public/global quotas and provider-side budgets both need review.

Before marking P7 complete, add actual Neatlogs before/after nested traces,
approved Entire checkpoints or actual entire-graph output, a cfo.ai business-plan
export, a public reachable demo, a video of three minutes or less and the actual
published submission URL. Preserve real commit dates and the confirmed cutoff.
