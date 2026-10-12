"""Qualify aggregate costs and SELECT-only ops against disposable PostgreSQL."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from _disposable_db import admin_engine
from agentforge.database.models import User, Project, Task, BudgetReservation, BudgetOperation
from agentforge.operations.business_metrics import aggregate
from agentforge.operations.check import inspect
from agentforge.settings import Settings
import agentforge.settings as settings_module

ROOT = Path(__file__).resolve().parents[3]
name = "agentforge_ops_smoke_" + uuid4().hex[:16]
admin = engine = None
created = False
try:
    source = make_url(Settings().database_url)
    admin = admin_engine(source)
    with admin.connect() as connection:
        owner = connection.dialect.identifier_preparer.quote_identifier(source.username)
        connection.execute(text('CREATE DATABASE "' + name + '" OWNER ' + owner))
    created = True
    settings = Settings(_env_file=None, app_env="test", database_url=source.set(database=name).render_as_string(hide_password=False),
                        llm_api_key=None, neatlogs_api_key=None, tavily_api_key=None, e2b_api_key=None)
    config = Config(str(ROOT / "apps/api/alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "apps/api/alembic"))
    with patch.object(settings_module, "get_settings", return_value=settings):
        command.upgrade(config, "head")
    engine = create_engine(settings.database_url)
    sessions = sessionmaker(engine)
    user, project, task, reservation = uuid4(), uuid4(), uuid4(), uuid4()
    with sessions.begin() as db:
        db.add(User(id=user, github_user_id=7, display_name="PRIVATE USER")); db.flush()
        db.add(Project(id=project, owner_id=user, name="PRIVATE PROJECT")); db.flush()
        db.add(Task(id=task, project_id=project, created_by=user, goal="PRIVATE GOAL", status="PARTIAL",
                    thread_id=str(uuid4()), max_cost_usd=1, max_wall_time_sec=60)); db.flush()
        db.add(BudgetReservation(id=reservation, task_id=task, run_id=uuid4(), amount_usd=1,
                                max_steps=2, max_tool_calls=2, depth=1, deadline=datetime.now(timezone.utc)+timedelta(minutes=1))); db.flush()
        db.add_all([BudgetOperation(reservation_id=reservation, operation_key="known", kind="model", max_cost_usd=Decimal("0.4"), cost_usd=Decimal("0.1"),
                        usage_json={"cost_source":"adapter_reported_cost"}, recorded_at=datetime.now(timezone.utc)),
                    BudgetOperation(reservation_id=reservation, operation_key="unknown", kind="tool", max_cost_usd=Decimal("0.2"), cost_usd=None),
                    BudgetOperation(reservation_id=reservation, operation_key="bounded", kind="model", max_cost_usd=Decimal("0.5"), cost_usd=Decimal("0.5"),
                        usage_json={"cost_source":"authorized_upper_bound"}, recorded_at=datetime.now(timezone.utc))])
    with engine.begin() as connection:
        result = aggregate(connection, datetime.now(timezone.utc)-timedelta(days=1))
        assert result["task_counts"]["tasks"] == 1
        assert result["ledger"]["unmeasured_operations"] == 2
        assert Decimal(result["ledger"]["measured_cost_usd"]) == Decimal("0.1")
        assert Decimal(result["ledger"]["conservative_cost_usd"]) == Decimal("0.8")
        assert Decimal(result["ledger"]["upper_bound_cost_usd"]) == Decimal("0.7")
        assert "PRIVATE" not in str(result) and str(user) not in str(result)
    with engine.begin() as connection:
        result = inspect(connection)
        assert result["worker_process"] == "not_observed"
        assert result["application_revision"] is not None
        try:
            connection.execute(text("DELETE FROM task_jobs"))
        except DBAPIError as error:
            assert error.orig.sqlstate == "25006"
        else:
            raise AssertionError("Operational inspection transaction permitted writes")
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from agentforge.database.models import Base
    from agentforge.database.migration_filter import include_autogenerate_object
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE checkpoint_migrations (v integer PRIMARY KEY)"))
        connection.execute(text("CREATE TABLE unknown_extra (id integer PRIMARY KEY)"))
        differences = compare_metadata(MigrationContext.configure(connection, opts={
            "include_object": include_autogenerate_object}), Base.metadata)
        removed = {operation[1].name for operation in differences if operation[0] == "remove_table"}
        assert removed == {"unknown_extra"}, "framework filter hid unknown schema drift"
    print("PASS separately owned checkpoint table excluded; unknown extra table still detected in disposable DB")
    print("PASS aggregate measured/unknown ledger costs; no content/identifiers; idle-worker honesty; read-only transaction enforcement")
except Exception as error:
    print("FAIL operations fixture class=" + type(error).__name__)
    print("SQLSTATE " + str(getattr(getattr(error, "orig", None), "sqlstate", None)))
    print("CONSTRAINT " + str(getattr(getattr(getattr(error, "orig", None), "diag", None), "constraint_name", None)))
    raise SystemExit(1) from None
finally:
    if engine is not None:
        engine.dispose()
    if created:
        with admin.connect() as connection:
            connection.execute(text('DROP DATABASE "' + name + '" WITH (FORCE)'))
        print("PASS disposable database removed")
    if admin is not None:
        admin.dispose()
