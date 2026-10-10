# Application migrations

Run from `apps/api`:

```powershell
uv run alembic upgrade head
uv run alembic current
uv run alembic check
```

The connection URL comes from `agentforge.settings` and the ignored root or API `.env`. Credentials do not belong in `alembic.ini`.

The initial revision creates only `users`, `projects`, `agent_definitions`, `agent_definition_versions` and `tasks`. Alembic also maintains its own `alembic_version` table. PostgreSQL 16+ provides the `gen_random_uuid()` function used by primary key defaults.

For future changes, update the models, generate a new revision with `uv run alembic revision --autogenerate -m "Describe the change"`, and review the generated migration before applying it. Applied revisions must remain unchanged. Add later-phase tables in separate revisions.

Revision `0002_phase2` adds only the Phase 2 child harness tables: `agent_runs`, `tool_calls`, `budget_reservations`, `budget_operations` and `budget_transactions`. It preserves the Phase 1 schema. Reservations/operations support atomic accounting and crash-conservative usage; they are prerequisites for safe Phase 2 invocation, not Phase 3 orchestration tables. `task_steps`, artifacts, task events and LangGraph checkpoint tables remain deferred.

The Phase 2 revision was authored and source-reviewed without being applied. No upgrade/downgrade, database history or metadata-equivalence checks were run during that implementation. Perform them against an isolated database as part of the [Phase 2 handoff](../../../docs/phase-2.md).
