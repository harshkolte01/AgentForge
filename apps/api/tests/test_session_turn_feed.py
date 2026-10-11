"""An explicit turn tells the conversation's change feed, as every other conversation state change does.

Real PostgreSQL, disposable: set TEST_DATABASE_ADMIN_URL (a loopback postgres/template1 URL, as CI does) and the module creates
a random database, migrates it to head and drops it afterwards. Without it the tests are skipped.
"""
import os
import subprocess
import sys
from pathlib import Path
from random import randint
from unittest import TestCase
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

API = Path(__file__).resolve().parents[1]
URL = None
_created = None   # (admin engine, database name)


def setUpModule():
    global URL, _created
    admin_url = os.environ.get("TEST_DATABASE_ADMIN_URL")
    if not admin_url:
        return
    admin = make_url(admin_url)
    if admin.host not in ("localhost", "127.0.0.1", "::1") or admin.database not in ("postgres", "template1") or admin.query:
        raise ValueError("TEST_DATABASE_ADMIN_URL must name a loopback postgres/template1 database without options")
    engine = create_engine(admin.set(drivername="postgresql+psycopg"), isolation_level="AUTOCOMMIT")
    name = "agentforge_turn_feed_" + uuid4().hex[:12]
    with engine.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    _created = (engine, name)
    URL = admin.set(drivername="postgresql+psycopg", database=name).render_as_string(hide_password=False)
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=API, check=True, capture_output=True,
                   env={**os.environ, "DATABASE_URL": URL})


def tearDownModule():
    if _created:
        engine, name = _created
        with engine.connect() as connection:
            connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        engine.dispose()


class ExplicitTurnFeedTests(TestCase):
    def setUp(self):
        if not URL:
            self.skipTest("TEST_DATABASE_ADMIN_URL (a disposable database) is not set")
        from agentforge.settings import Settings
        import agentforge.settings as settings_module
        settings = Settings(_env_file=None, database_url=URL, app_env="test", auth_mode="disabled", llm_api_key=None,
                            tavily_api_key=None, neatlogs_api_key=None)
        patcher = patch.object(settings_module, "get_settings", return_value=settings)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.engine = create_engine(URL)
        self.addCleanup(self.engine.dispose)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)

    def test_admitting_an_explicit_turn_bumps_the_revision_and_records_a_change(self):
        from agentforge.contracts.sessions import SessionCreate, SessionTurnCreate
        from agentforge.database.models import Project, User
        from agentforge.database.sessions import Conversation, ConversationChange
        from agentforge.repositories.sessions import SessionRepository
        user, project = uuid4(), uuid4()
        with self.sessions.begin() as db:
            db.add(User(id=user, github_user_id=-randint(10**6, 10**9), display_name="Fixture"))
            db.flush()
            db.add(Project(id=project, owner_id=user, name="Fixture"))
        repo = SessionRepository(self.sessions)
        conversation = repo.create(user, SessionCreate(project_id=project, mode="general"))
        before = repo.get(conversation.id, user).revision
        turn = repo.turn(conversation.id, user, SessionTurnCreate(idempotency_key=uuid4(), kind="general",
                                                                  goal="Research a concrete question with evidence."))
        with self.sessions() as db:
            row = db.get(Conversation, conversation.id)
            change = db.scalar(select(ConversationChange).where(ConversationChange.session_id == row.id)
                               .order_by(ConversationChange.sequence.desc()).limit(1))
        self.assertEqual(row.revision, before + 1)
        self.assertEqual((change.sequence, change.event_type), (row.revision, "conversation.changed"))
        self.assertEqual(change.payload_json["active_task_id"], str(turn.task.id))
