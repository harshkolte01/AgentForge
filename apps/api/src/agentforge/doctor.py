"""Local setup check: one PASS / WARN / FAIL line per requirement. python -m agentforge.doctor

Prints setting names, never their values. Reads the database (read-only) and makes no provider calls, so it
costs nothing to run. Exits 1 when anything required fails.
"""

from types import SimpleNamespace

from pydantic import ValidationError
from sqlalchemy import text

from agentforge.settings import API_DIR, Settings, validation_summary


def check_settings():
    try:
        settings = Settings()
    except ValidationError as error:
        return None, [("FAIL", "settings", "invalid (values hidden):\n" + validation_summary(error))]
    return settings, [("PASS", "settings", f"valid (APP_ENV={settings.app_env}, AUTH_MODE={settings.auth_mode})")]


def check_database(settings):
    """Reachability, migration head and the local identity, in one short read-only connection."""
    from sqlalchemy import create_engine
    results, revision, local_user = [], None, None
    engine = create_engine(settings.database_url, connect_args={"connect_timeout": 5})
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            results.append(("PASS", "database", "reachable (DATABASE_URL)"))
            if connection.execute(text("SELECT to_regclass('public.alembic_version')")).scalar():
                revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
            if settings.local_user_id and revision:
                local_user = connection.execute(text("SELECT 1 FROM users WHERE id = :id"),
                    {"id": settings.local_user_id}).scalar() is not None
    except Exception as error:
        # The type only: driver messages can include the connection URL.
        return [("FAIL", "database", f"unreachable ({type(error).__name__}); check DATABASE_URL and that PostgreSQL is running")], None
    finally:
        engine.dispose()
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    heads = set(ScriptDirectory.from_config(Config(str(API_DIR / "alembic.ini"))).get_heads())
    if revision in heads:
        results.append(("PASS", "migrations", f"at head ({revision})"))
    else:
        found = revision or "none applied"
        results.append(("FAIL", "migrations", f"database is at {found}, code expects {', '.join(sorted(heads))}; run `cd apps/api && uv run alembic upgrade head`"))
    return results, local_user


def check_auth(settings, local_user):
    from agentforge.auth.github_service import github_configured
    from agentforge.auth.session import local_auth_enabled
    if settings.auth_mode == "disabled":
        return [("FAIL", "sign-in", "AUTH_MODE is disabled; run `cd apps/api && uv run python -m agentforge.auth.bootstrap --write-env`")]
    if settings.auth_mode == "local_single_user":
        if not local_auth_enabled(settings):
            return [("FAIL", "sign-in", "local_single_user needs LOCAL_USER_ID and a LOCAL_BOOTSTRAP_TOKEN of 32+ characters; run the bootstrap command")]
        if local_user is False:
            return [("FAIL", "sign-in", "LOCAL_USER_ID is not in the database; run `uv run python -m agentforge.auth.bootstrap`")]
        return [("PASS", "sign-in", "local_single_user (LOCAL_USER_ID, LOCAL_BOOTSTRAP_TOKEN)")]
    if not github_configured(settings):
        return [("FAIL", "sign-in", "github_app needs GITHUB_APP_ID, GITHUB_CLIENT_ID, GITHUB_CLIENT_SECRET, GITHUB_TOKEN_ENCRYPTION_KEY")]
    note = ", open to any GitHub user" if settings.public_signup_enabled and not settings.github_allowed_logins else ""
    return [("PASS", "sign-in", f"github_app{note}")]


def check_model(settings):
    from agentforge.api.routes_configuration import configuration
    state = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=settings)))
    missing = configuration(state, user=None)["general_missing_settings"]
    if missing:
        return [("FAIL", "model", "missing " + ", ".join(missing))]
    return [("PASS", "model", "LLM_MODEL, LLM_API_KEY, limits and prices set")]


def check_optional(settings):
    results = [
        ("PASS", "e2b", "E2B_API_KEY set (coding sandbox)") if settings.e2b_api_key
        else ("WARN", "e2b", "E2B_API_KEY not set: no coding sandbox (bash, files, search)"),
        ("PASS", "tavily", "TAVILY_API_KEY set (web research)") if settings.tavily_api_key
        else ("WARN", "tavily", "TAVILY_API_KEY not set: no web search"),
    ]
    if settings.auth_mode == "github_app" or settings.github_app_id:
        missing = [name for name, value in (("GITHUB_APP_ID", settings.github_app_id), ("GITHUB_APP_SLUG", settings.github_app_slug),
            ("GITHUB_CLIENT_ID", settings.github_client_id), ("GITHUB_CLIENT_SECRET", settings.github_client_secret),
            ("GITHUB_TOKEN_ENCRYPTION_KEY", settings.github_token_encryption_key),
            ("GITHUB_WEBHOOK_SECRET", settings.github_webhook_secret)) if not value]
        results.append(("WARN", "github-app", "missing " + ", ".join(missing) + " (see docs/github-app.md)") if missing
            else ("PASS", "github-app", "configured"))
        if settings.auth_mode != "github_app":
            results.append(("WARN", "github-app", "repository access needs AUTH_MODE=github_app; local sign-in uses public repositories only"))
    else:
        results.append(("WARN", "github-app", "not configured: public repositories only (see docs/github-app.md)"))
    return results


def check_tracing(settings):
    """Neatlogs: whether runs are traced, and what a trace is allowed to carry. Offline; it never contacts Neatlogs."""
    if not settings.neatlogs_api_key:
        return [("WARN", "neatlogs", "NEATLOGS_API_KEY not set: runs are not traced (see docs/neatlogs.md)")]
    from importlib.metadata import PackageNotFoundError, version
    try:
        sdk = version("neatlogs")
    except PackageNotFoundError:
        return [("FAIL", "neatlogs", "NEATLOGS_API_KEY is set but the `neatlogs` package is not installed; run `cd apps/api && uv sync`")]
    where = f", endpoint {settings.neatlogs_endpoint.host}" if settings.neatlogs_endpoint else ""
    content = "prompts and tool data are SENT (secrets masked)" if settings.neatlogs_capture_content else "metadata only, no prompts or answers"
    return [("PASS", "neatlogs", f"tracing on (SDK {sdk}, workflow {settings.neatlogs_workflow_name}{where}); {content}")]


def run() -> list[tuple[str, str, str]]:
    settings, results = check_settings()
    if settings is None:
        return results
    database, local_user = check_database(settings)
    results += database
    for check in (lambda: check_auth(settings, local_user), lambda: check_model(settings), lambda: check_optional(settings),
                  lambda: check_tracing(settings)):
        try:
            results += check()
        except Exception as error:
            results.append(("FAIL", "doctor", f"a check could not run ({type(error).__name__})"))
    return results


def main() -> int:
    results = run()
    for status, name, detail in results:
        print(f"{status:<4}  {name:<11} {detail}")
    failed = sum(status == "FAIL" for status, _, _ in results)
    print(f"\n{failed} required check(s) failed." if failed else "\nReady.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
