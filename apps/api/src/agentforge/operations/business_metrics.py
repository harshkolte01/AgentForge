"""Export aggregate ledger costs, never goals, prompts, IDs, tokens or artifact data."""
import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from sqlalchemy import text

from agentforge.database.engine import create_db_engine
from agentforge.settings import get_settings


def aggregate(connection, since):
    connection.execute(text("SET TRANSACTION READ ONLY"))
    connection.execute(text("SET LOCAL statement_timeout = '15s'"))
    tasks = connection.execute(text("""SELECT count(*) AS tasks,
        count(*) FILTER (WHERE status = 'COMPLETED') AS completed,
        count(*) FILTER (WHERE status = 'PARTIAL') AS partial,
        count(*) FILTER (WHERE status = 'FAILED') AS failed,
        count(*) FILTER (WHERE status = 'CANCELLED') AS cancelled
        FROM tasks WHERE created_at >= :since"""), {"since": since}).mappings().one()
    costs = connection.execute(text("""SELECT count(*) AS operations,
        count(*) FILTER (WHERE o.cost_usd IS NULL) AS unmeasured_operations,
        coalesce(sum(o.cost_usd), 0) AS measured_cost_usd,
        coalesce(sum(coalesce(o.cost_usd, o.max_cost_usd)), 0) AS conservative_cost_usd
        FROM budget_operations o JOIN budget_reservations r ON r.id = o.reservation_id
        JOIN tasks t ON t.id = r.task_id WHERE t.created_at >= :since"""), {"since": since}).mappings().one()
    return {"task_counts": dict(tasks), "ledger": {key: str(value) if key.endswith("usd") else value for key, value in costs.items()}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=7, choices=range(1, 366))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    since = datetime.now(timezone.utc) - timedelta(days=args.days)
    engine = None
    try:
        engine = create_db_engine(get_settings().database_url)
        with engine.begin() as connection:
            result = aggregate(connection, since)
        result.update(schema_version=1, since=since.isoformat(), generated_at=datetime.now(timezone.utc).isoformat(),
                      scope="all application users; operator-only aggregate", source="internal budget ledger; not a provider invoice",
                      exclusions=["EC2/EBS/network costs", "provider operations outside this ledger", "revenue", "tax", "labour"],
                      cfo_ai_export=False)
        # Private output and exclusive creation prevent accidental overwrite/symlink follow.
        import os
        descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            json.dump(result, stream, indent=2)
            stream.write("\n")
        print("Aggregate ledger export written; review sample size and costs before sharing.")
        return 0
    except Exception:
        print("Aggregate export failed; private database details suppressed.")
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
