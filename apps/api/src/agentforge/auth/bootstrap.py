"""Explicit operator CLI: provision one local identity and project, print env setup.

Run only after migrations: python -m agentforge.auth.bootstrap
This command writes the local database; API startup never provisions an identity.
"""

from secrets import token_urlsafe
from uuid import uuid4

from sqlalchemy.orm import Session

from agentforge.database.engine import create_db_engine
from agentforge.database.models import Project, User
from agentforge.settings import get_settings


def main() -> None:
    settings = get_settings()
    if settings.app_env == "production":
        raise SystemExit("Local authentication cannot be provisioned in production.")
    user_id, project_id = uuid4(), uuid4()
    engine = create_db_engine(settings.database_url)
    try:
        with Session(engine) as database, database.begin():
            # Negative ids reserve local identities; real GitHub ids are positive.
            database.add(User(id=user_id, github_user_id=-(uuid4().int % (2**62) + 1),
                              display_name="Local workspace"))
            database.flush()
            database.add(Project(id=project_id, owner_id=user_id, name="Research workspace"))
    finally:
        engine.dispose()
    print("Local identity and project created. Add these private values to apps/api/.env:")
    print("AUTH_MODE=local_single_user")
    print(f"LOCAL_USER_ID={user_id}")
    print(f"LOCAL_BOOTSTRAP_TOKEN={token_urlsafe(48)}")
    print("Restart the API, then enter the bootstrap token at http://localhost:3000/chat.")
    print("Keep the API bound to 127.0.0.1. Do not share or commit the bootstrap token.")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        raise SystemExit("Local setup failed. Check database configuration and application migrations; private error details suppressed.") from None
