#!/usr/bin/env bash
# Runs only for a NEW Postgres volume. Existing databases require reviewed grants.
set -euo pipefail
: "${RUNTIME_DATABASE_PASSWORD:?missing runtime database password}"
psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --set=ON_ERROR_STOP=1 \
  --set=runtime_password="$RUNTIME_DATABASE_PASSWORD" <<'SQL'
SELECT format('CREATE ROLE agentforge LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION', :'runtime_password') \gexec
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO agentforge;
GRANT CONNECT ON DATABASE agentforge TO agentforge;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO agentforge;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO agentforge;
SQL
