"""Export aggregate usage and cost evidence, never prompts, identities, credentials or artifact data."""
import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from decimal import Decimal

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
        count(*) FILTER (WHERE o.recorded_at IS NULL OR o.usage_json->>'cost_source' = 'authorized_upper_bound'
            OR o.usage_json->>'cost_source' IS NULL) AS unmeasured_operations,
        coalesce(sum(o.cost_usd) FILTER (WHERE o.usage_json->>'cost_source' = 'adapter_reported_cost'), 0) AS measured_cost_usd,
        coalesce(sum(o.cost_usd) FILTER (WHERE o.usage_json->>'cost_source' = 'configured_rates_from_reported_usage'), 0) AS rate_estimated_cost_usd,
        coalesce(sum(coalesce(o.cost_usd, o.max_cost_usd)) FILTER
            (WHERE o.usage_json->>'cost_source' = 'authorized_upper_bound' OR o.recorded_at IS NULL), 0) AS upper_bound_cost_usd,
        coalesce(sum(coalesce(o.cost_usd, o.max_cost_usd)), 0) AS conservative_cost_usd
        FROM budget_operations o JOIN budget_reservations r ON r.id = o.reservation_id
        JOIN tasks t ON t.id = r.task_id WHERE t.created_at >= :since"""), {"since": since}).mappings().one()
    daily = connection.execute(text("""SELECT date_trunc('day', t.created_at AT TIME ZONE 'UTC')::date AS day,
        count(DISTINCT t.id) AS tasks, coalesce(sum(coalesce(o.cost_usd,o.max_cost_usd)),0) AS conservative_cost_usd
        FROM tasks t LEFT JOIN budget_reservations r ON r.task_id=t.id
        LEFT JOIN budget_operations o ON o.reservation_id=r.id
        WHERE t.created_at >= :since GROUP BY 1 ORDER BY 1"""), {'since': since}).mappings().all()
    distribution = connection.execute(text("""WITH per_task AS (
        SELECT t.id, coalesce(sum(coalesce(o.cost_usd,o.max_cost_usd)),0) AS cost
        FROM tasks t LEFT JOIN budget_reservations r ON r.task_id=t.id
        LEFT JOIN budget_operations o ON o.reservation_id=r.id WHERE t.created_at>=:since GROUP BY t.id)
        SELECT count(*) AS sample_size, coalesce(avg(cost),0) AS mean_usd,
        coalesce(percentile_cont(0.5) WITHIN GROUP (ORDER BY cost),0) AS p50_usd,
        coalesce(percentile_cont(0.95) WITHIN GROUP (ORDER BY cost),0) AS p95_usd FROM per_task"""), {'since': since}).mappings().one()
    completed = tasks['completed']
    models = connection.execute(text("""SELECT
        coalesce(o.usage_json->'provider_usage'->>'reported_model',
                 o.usage_json->'provider_usage'->>'configured_model', 'unknown') AS model,
        count(*) AS operations,
        count(*) FILTER (WHERE o.operation_key LIKE '%:broken:%') AS interrupted_attempts,
        coalesce(sum(CASE WHEN o.usage_json->'provider_usage'->>'input_tokens' ~ '^[0-9]+$'
            THEN (o.usage_json->'provider_usage'->>'input_tokens')::numeric ELSE 0 END),0) AS input_tokens,
        coalesce(sum(CASE WHEN o.usage_json->'provider_usage'->>'output_tokens' ~ '^[0-9]+$'
            THEN (o.usage_json->'provider_usage'->>'output_tokens')::numeric ELSE 0 END),0) AS output_tokens
        FROM budget_operations o JOIN budget_reservations r ON r.id=o.reservation_id
        JOIN tasks t ON t.id=r.task_id WHERE t.created_at>=:since AND o.kind='model' GROUP BY 1"""), {'since':since}).mappings().all()
    return {"task_counts": dict(tasks), "ledger": {key: str(value) if key.endswith("usd") else value for key, value in costs.items()},
            'daily': [{key: str(value) if key in ('day','conservative_cost_usd') else value for key,value in row.items()} for row in daily],
            'distribution': {key: str(value) if key.endswith('usd') else value for key,value in distribution.items()},
            'conservative_cost_per_success_usd': str(costs['conservative_cost_usd']/completed) if completed else None,
            'cost_per_success_definition': 'All cohort operation costs, including failed attempts, divided by completed tasks; bounds are not actual charges.',
            'models': [{key: int(value) if key.endswith('_tokens') else value for key,value in row.items()} for row in models],
            'coverage': {'complete': False, 'external_tool_costs': 'A zero tool-ledger value does not establish that the external service is free.'}}


def validate_external(value):
    """Private operator assumptions, never fabricated invoice facts."""
    if not isinstance(value, dict) or set(value) - {'currency', 'monthly_costs', 'cash_balance', 'sandbox_rates', 'note'}:
        raise ValueError('invalid external cost file')
    if value.get('currency', 'USD') != 'USD':
        raise ValueError('external costs must be normalized to USD')
    costs = value.get('monthly_costs', {})
    if not isinstance(costs, dict) or len(costs)>30:
        raise ValueError('invalid monthly costs')
    for amount in [*costs.values(), value.get('cash_balance', '0')]:
        number = Decimal(str(amount))
        if not number.is_finite() or number < 0:
            raise ValueError('invalid amount')
    return value


def sandbox_costs(rows, since, rates=None):
    """Count each provider execution once, including idle time. Incomplete evidence stays explicit."""
    from datetime import timedelta
    intervals, open_executions = {}, set()
    for row in rows:
        payload = row['payload_json'] or {}
        key = payload.get('execution_id')
        if not isinstance(key, str) or not key:
            continue
        open_executions.add(key)
        details = payload.get('execution')
        if not row['event_type'].endswith(('.killed','.paused')) or not isinstance(details, dict):
            continue
        try:
            start = datetime.fromisoformat(details['started_at'].replace('Z','+00:00'))
            duration = Decimal(str(details['execution_time']))/1000
            cpu, memory = Decimal(str(details['vcpu_count'])), Decimal(str(details['memory_mb']))/1024
            if start.tzinfo is None or not all(x.is_finite() and x >= 0 for x in (duration,cpu,memory)):
                continue
            end = start + timedelta(seconds=float(duration))
            seconds = Decimal(str(max(0,(end-max(start,since)).total_seconds())))
            interval = (seconds,cpu,memory,start)
            if key in intervals and intervals[key] != interval:
                intervals[key] = None
            else:
                intervals[key] = interval
        except (ValueError,KeyError,TypeError,ArithmeticError):
            continue
    total, running_seconds, estimated, unknown = Decimal(0), Decimal(0), 0, 0
    for interval in intervals.values():
        if interval is None:
            unknown += 1; continue
        seconds,cpu,memory,start = interval
        running_seconds += seconds
        try:
            effective = datetime.fromisoformat(rates['effective_from'].replace('Z','+00:00'))
            cpu_rate = Decimal(str(rates['vcpu_usd_per_second']))
            memory_rate = Decimal(str(rates['gib_usd_per_second']))
            if effective.tzinfo is None or start < effective or not all(x.is_finite() and x >= 0 for x in (cpu_rate,memory_rate)):
                raise ValueError('uncovered rate period')
            total += seconds*(cpu*cpu_rate + memory*memory_rate)
            estimated += 1
        except (TypeError,KeyError,ValueError,ArithmeticError):
            unknown += 1
    return {'running_seconds': str(running_seconds), 'rate_estimated_cost_usd': str(total), 'estimated_executions': estimated,
            'unpriced_executions': unknown, 'incomplete_executions': len(open_executions-set(intervals)),
            'source': 'signed lifecycle runtime with operator rates; not an invoice', 'coverage_complete': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=7, choices=range(1, 366))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--external-costs", type=Path)
    args = parser.parse_args()
    since = datetime.now(timezone.utc) - timedelta(days=args.days)
    engine = None
    try:
        engine = create_db_engine(get_settings().database_url)
        external = validate_external(json.loads(args.external_costs.read_text())) if args.external_costs else {}
        with engine.begin() as connection:
            result = aggregate(connection, since)
            events = connection.execute(text("""SELECT event_type,payload_json FROM sandbox_usage_events
                WHERE occurred_at >= :since AND sandbox_id IN (SELECT sandbox_id FROM sandbox_usage_events
                WHERE payload_json->>'task_id' IN (SELECT id::text FROM tasks))"""), {'since':since}).mappings().all()
            result['sandbox'] = sandbox_costs(events, since, external.get('sandbox_rates'))
        result.update(schema_version=2, since=since.isoformat(), generated_at=datetime.now(timezone.utc).isoformat(),
                      scope="all application users; operator-only aggregate", source="internal budget ledger; not a provider invoice",
                      exclusions=["EC2/EBS/network costs", "provider operations outside this ledger", "revenue", "tax", "labour"])
        result['external_assumptions'] = external
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
