"""Explicit operator CLI: provision one local identity and project, then configure the root .env.

Run only after migrations: python -m agentforge.auth.bootstrap [--write-env]
This command writes the local database; API startup never provisions an identity. It reuses a configured
LOCAL_USER_ID (creating that identity if the database lacks it) and never replaces a value already set.
"""

import argparse
import os
import re
from pathlib import Path
from secrets import token_urlsafe
from uuid import uuid4

from sqlalchemy.orm import Session

from agentforge.database.engine import create_db_engine
from agentforge.database.models import Project, User
from agentforge.settings import REPO_ROOT, get_settings

ENV_FILE = REPO_ROOT / ".env"


def env_values(path: Path) -> dict[str, str]:
    """Non-empty KEY=value pairs, read the way dotenv does for simple lines."""
    values = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            match = re.match(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)", line)
            if match:
                value = re.sub(r"\s+#.*$", "", match[2]).strip().strip("'\"")
                values[match[1]] = value
    return {key: value for key, value in values.items() if value}


def fill_env(path: Path, updates: dict[str, str]) -> list[str]:
    """Set each key that is missing or empty in `path`; every other line is kept as it is. Returns the keys written."""
    present = env_values(path)
    pending = {key: value for key, value in updates.items() if key not in present}
    if not pending:
        return []
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    written = []
    for index, line in enumerate(lines):
        match = re.match(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        if match and match[1] in pending and match[1] not in written:
            lines[index] = f"{match[1]}={pending[match[1]]}"
            written.append(match[1])
    lines += [f"{key}={value}" for key, value in pending.items() if key not in written]
    temporary = path.with_name(path.name + ".tmp")
    # Owner-only from the first byte, then an atomic swap: the file holds secrets.
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
    return list(pending)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Provision the local single-user identity and configure the root .env.")
    parser.add_argument("--write-env", action="store_true",
                        help=f"fill missing AUTH_MODE / LOCAL_USER_ID / LOCAL_BOOTSTRAP_TOKEN in {ENV_FILE} (never overwrites)")
    arguments = parser.parse_args(argv)
    settings = get_settings()
    if settings.app_env == "production":
        raise SystemExit("Local authentication cannot be provisioned in production.")
    user_id = settings.local_user_id or uuid4()
    engine = create_db_engine(settings.database_url)
    try:
        with Session(engine) as database, database.begin():
            created = database.get(User, user_id) is None
            if created:
                # Negative ids reserve local identities; real GitHub ids are positive.
                database.add(User(id=user_id, github_user_id=-(uuid4().int % (2**62) + 1),
                                  display_name="Local workspace"))
                database.flush()
                database.add(Project(id=uuid4(), owner_id=user_id, name="Research workspace"))
    finally:
        engine.dispose()
    print("Local identity and project created." if created else "Local identity already provisioned.")
    # Only what no configuration source sets yet; apps/api/.env and the process environment also count.
    wanted = {}
    if "auth_mode" not in settings.model_fields_set:
        wanted["AUTH_MODE"] = "local_single_user"
    elif settings.auth_mode != "local_single_user":
        print(f"AUTH_MODE is set to {settings.auth_mode}; change it to local_single_user to use this identity.")
    if settings.local_user_id is None:
        wanted["LOCAL_USER_ID"] = str(user_id)
    if settings.local_bootstrap_token is None:
        wanted["LOCAL_BOOTSTRAP_TOKEN"] = token_urlsafe(48)
    if not wanted:
        print("AUTH_MODE, LOCAL_USER_ID and LOCAL_BOOTSTRAP_TOKEN are already configured; nothing to change.")
    elif arguments.write_env:
        written = fill_env(ENV_FILE, wanted)
        print(f"Wrote {', '.join(written) or 'nothing'} to {ENV_FILE} (mode 0600).")
        if "LOCAL_BOOTSTRAP_TOKEN" in written:
            print(f"The bootstrap token is in {ENV_FILE} as LOCAL_BOOTSTRAP_TOKEN.")
    else:
        print(f"Add these private values to {ENV_FILE} (or rerun with --write-env):")
        for key, value in wanted.items():
            print(f"{key}={value}")
    base = str(settings.app_base_url).rstrip("/")
    print(f"Restart the API, then enter the bootstrap token at {base}/chat.")
    print("Keep the API bound to 127.0.0.1. Do not share or commit the bootstrap token.")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        raise SystemExit("Local setup failed. Check database configuration and application migrations; private error details suppressed.") from None
