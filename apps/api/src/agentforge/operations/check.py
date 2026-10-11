"""Read-only operational snapshot; does not claim an idle worker is alive."""
import json
import os

from sqlalchemy import text

from agentforge.database.engine import create_db_engine
from agentforge.settings import get_settings


def inspect(connection):
    connection.execute(text("SET TRANSACTION READ ONLY"))
    connection.execute(text("SET LOCAL statement_timeout = '5s'"))
    tables = connection.execute(text("SELECT to_regclass('public.alembic_version'), to_regclass('public.task_jobs')")).one()
    result = {"database": "reachable", "application_revision": None,
              "worker_process": "not_observed", "jobs": None}
    if tables[0]:
        result["application_revision"] = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    if tables[1]:
        row = connection.execute(text("""SELECT
            count(*) FILTER (WHERE status = 'QUEUED') AS queued,
            count(*) FILTER (WHERE status = 'RUNNING') AS running,
            count(*) FILTER (WHERE status = 'RUNNING' AND lease_expires_at < now()) AS expired,
            extract(epoch FROM now() - max(heartbeat_at)) AS heartbeat_age_seconds
            FROM task_jobs""")).mappings().one()
        result["jobs"] = {key: float(value) if key == "heartbeat_age_seconds" and value is not None else value for key, value in row.items()}
    return result


def main():
    engine = None
    try:
        settings = get_settings()
        engine = create_db_engine(settings.database_url)
        with engine.begin() as connection:
            result = inspect(connection)
        path = settings.research_storage_path
        # No file is created. ACL/mount/sandbox behavior still needs a write fixture.
        result["artifact_directory"] = "accessible" if path.is_dir() and os.access(path, os.R_OK | os.W_OK | os.X_OK) else "unavailable"
        result["artifact_write_test"] = "not_performed"
        print(json.dumps(result, sort_keys=True))
        return 0 if result["application_revision"] and result["artifact_directory"] == "accessible" else 1
    except Exception:
        print(json.dumps({"status": "inspection_failed", "detail": "Check configuration and migrations; private exception details suppressed."}))
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
