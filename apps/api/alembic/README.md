# Database migrations

Alembic owns the schema. Run these from `apps/api`:

```sh
uv run alembic upgrade head   # apply every pending revision
uv run alembic current        # show the applied revision
uv run alembic check          # fail if the models and the schema have drifted
```

The connection URL comes from `agentforge.settings` and the ignored root or API `.env`; credentials never belong in
`alembic.ini`. PostgreSQL 16 or newer is required (primary keys default to `gen_random_uuid()`).

## Changing the schema

1. Change the models under `src/agentforge/database/`.
2. Generate a revision: `uv run alembic revision --autogenerate -m "Describe the change"`.
3. Read the generated migration, edit it where autogenerate is wrong, and apply it to a disposable database first.

An applied revision is never edited; later changes ship as new revisions. Revisions `0015` and `0016` each exist twice (two
branches developed side by side); `0018_merge_plugins_deliveries` joins them, so there is a single head.
