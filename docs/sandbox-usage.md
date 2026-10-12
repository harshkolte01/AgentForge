# Sandbox usage and cost evidence

`business_metrics` exports schema v2. It separates adapter-reported costs, configured-rate estimates, authorized upper bounds,
and unmeasured operations. A non-null ledger amount is not automatically a measured charge. Reports include cohort counts, daily
totals, distributions, and conservative cost per successful task, including failed-attempt costs. They never expose prompts,
repository names, user IDs, or task IDs. This remains an operator-only command, not an ordinary agent capability.

E2B sends signed lifecycle events to `/api/v1/sandbox-usage/webhook`. Set `E2B_WEBHOOK_SECRET` (a dedicated signature secret of at least 32 characters; without it the webhook is unavailable) and subscribe to created,
resumed, paused, killed, updated, and checkpointed events. Receipt IDs deduplicate delivery retries. Only whitelisted metadata is
stored. API reconciliation accepts the provider's camelCase envelope as well as the documented webhook snake_case envelope.

```sh
cd apps/api
# Read-only preview of retained provider events:
.venv/bin/python -m agentforge.operations.reconcile_sandbox_usage --days 7
# Import after reviewing the preview; this changes only local evidence rows:
.venv/bin/python -m agentforge.operations.reconcile_sandbox_usage --days 7 --apply
.venv/bin/python -m agentforge.operations.business_metrics --days 7 \
  --external-costs /private/assumptions.json --output /private/agentforge-costs.json
```

The optional assumptions file is a JSON object with `currency: "USD"`, `monthly_costs` (category → decimal string),
`cash_balance`, and optional `sandbox_rates` (`effective_from`, `vcpu_usd_per_second`, `gib_usd_per_second`). Supply real rates and
label assumptions in `note`; no default prices or cash balance are invented. Lifecycle billing counts each completed provider
execution once, including idle-running time. Missing terminal events, missing rates, provider retention, and active intervals
remain explicit gaps. A zero plugin ledger amount does not prove the service is free. Neither ledger estimates nor lifecycle
estimates are provider invoices.

Apply the additive Alembic migration `0020_sandbox_usage` (run `alembic upgrade head` from `apps/api`) before importing usage events.
