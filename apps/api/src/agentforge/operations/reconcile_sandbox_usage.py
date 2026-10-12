"""Read E2B lifecycle history; --apply imports bounded receipts into the application DB. Never changes sandboxes."""
import argparse
from datetime import datetime, timedelta, timezone
import json
import httpx
from sqlalchemy.dialects.postgresql import insert
from agentforge.api.routes_sandbox_usage import parse_event
from agentforge.database.engine import create_db_engine
from agentforge.database.sandbox_usage import SandboxUsageEvent
from agentforge.integrations.tls import outbound_tls
from agentforge.settings import get_settings


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--days',type=int,choices=range(1,31),default=7)
    parser.add_argument('--max-pages',type=int,choices=range(1,101),default=20)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    engine=None
    try:
        settings=get_settings()
        if not settings.e2b_api_key:raise ValueError('not configured')
        since=datetime.now(timezone.utc)-timedelta(days=args.days)
        found={};exhausted=False;skipped=0
        with httpx.Client(timeout=30,verify=outbound_tls(),trust_env=False,headers={'X-API-Key':settings.e2b_api_key.get_secret_value()}) as client:
            for page in range(args.max_pages):
                response=client.get('https://api.e2b.app/events/sandboxes',params={'limit':100,'offset':page*100,'orderAsc':'false'})
                response.raise_for_status();events=response.json()
                if not isinstance(events,list):raise ValueError('incompatible events response')
                older=False
                for event in events:
                    try:row=parse_event(event,source='e2b_lifecycle_api')
                    except ValueError:skipped+=1;continue  # a new event type or a malformed entry must not abort the whole run
                    if row['occurred_at']<since:older=True;continue
                    found[row['event_id']]=row
                if len(events)<100 or older:exhausted=True;break
        if args.apply:
            engine=create_db_engine(settings.database_url)
            with engine.begin() as connection:
                for row in found.values():
                    connection.execute(insert(SandboxUsageEvent).values(**row).on_conflict_do_nothing())
        print(json.dumps({'events':len(found),'skipped':skipped,'applied':args.apply,'pagination_complete':exhausted,
                          'coverage':'Provider retention, delayed events, and active executions can leave gaps.'}))
        return 0
    except Exception as error:
        print('Lifecycle reconciliation failed:',type(error).__name__)
        return 1
    finally:
        if engine is not None:engine.dispose()


if __name__=='__main__':raise SystemExit(main())
