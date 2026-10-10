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
