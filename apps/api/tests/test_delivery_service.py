"""The delivery workflow end to end: real PostgreSQL, real git in the workspace, a stateful fake GitHub.

The database is disposable. Either set TEST_DATABASE_ADMIN_URL (a loopback postgres/template1 URL, as CI does): the tests then
create a random database, migrate it to head and drop it afterwards; or set AGENTFORGE_TEST_DATABASE_URL to a database that is
already migrated and may be filled with test rows (conversations are append-only, so rows are never removed). Without either,
the tests are skipped.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from random import randint
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import create_engine, delete, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from agentforge.database.delivery import ConversationDelivery, DeliveryCommit
from agentforge.database.models import Project, User
from agentforge.database.sessions import Conversation, ConversationMessage, SessionTurn, TurnExecution
from agentforge.delivery import workspace as ws
from agentforge.delivery.service import DeliveryError, DeliveryService
from agentforge.integrations.github_client import GitHubError

from delivery_fakes import OWNER_REPO, FakeGitHub, FakeProvider, LocalWorkspace, settings

URL = os.environ.get("AGENTFORGE_TEST_DATABASE_URL")
FILES = {"a.py": b"one\ntwo\n", "b.py": b"keep\n"}
API = Path(__file__).resolve().parents[1]
_created = None   # (admin engine, database name) when this module made the database


def setUpModule():
    global URL, _created
    admin_url = os.environ.get("TEST_DATABASE_ADMIN_URL")
    if URL or not admin_url:
        return
    admin = make_url(admin_url)
    if admin.host not in ("localhost", "127.0.0.1", "::1") or admin.database not in ("postgres", "template1") or admin.query:
        raise ValueError("TEST_DATABASE_ADMIN_URL must name a loopback postgres/template1 database without options")
    engine = create_engine(admin.set(drivername="postgresql+psycopg"), isolation_level="AUTOCOMMIT")
    name = "agentforge_delivery_" + uuid4().hex[:12]
    with engine.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    _created = (engine, name)
    URL = admin.set(drivername="postgresql+psycopg", database=name).render_as_string(hide_password=False)
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=API, check=True, capture_output=True,
                   env={**os.environ, "DATABASE_URL": URL})


def tearDownModule():
    global URL
    if _created:
        engine, name = _created
        with engine.connect() as connection:
            connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        engine.dispose()
        URL = None


class DeliveryServiceTests(IsolatedAsyncioTestCase):
    def setUp(self):
        if not URL:
            self.skipTest("TEST_DATABASE_ADMIN_URL or AGENTFORGE_TEST_DATABASE_URL (a disposable database) is not set")
        self.engine = create_engine(URL)
        self.sessions = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        for name, value in (("MEASURE_INDEX", str(root / "measure.index")), ("INDEX_INFO", str(root / "index-info"))):
            patcher = patch.object(ws, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.github = FakeGitHub(FILES)
        self.work = LocalWorkspace(root / "work", FILES, self.github.base)
        self.provider = FakeProvider(self.work)
        with self.sessions() as db, db.begin():
            db.execute(delete(ConversationDelivery))   # cascades to delivery_commits
            user = User(github_user_id=randint(10**9, 10**12), display_name="t")
            db.add(user)
            db.flush()
            project = Project(owner_id=user.id, name="p")
            db.add(project)
            db.flush()
            conversation = Conversation(owner_id=user.id, project_id=project.id, title="Fix the parser", mode="coding",
                repository_json={"repo_id": OWNER_REPO, "ref": "main"}, sandbox_id="sandbox-1")
            db.add(conversation)
            db.flush()
            self.user_id, self.project_id, self.conversation_id = user.id, project.id, conversation.id
        self.service = self.make_service()

    def make_service(self, **overrides):
        async def github(user_id):
            return self.github
        return DeliveryService(self.sessions, settings(**overrides), provider=self.provider, github=github)

    def tearDown(self):
        # Conversations are append-only by design (database triggers refuse deletes), so each test gets new ones and the
        # database is disposable; only the delivery tables, which the tests count across conversations, are emptied in setUp.
        if URL:
            self.engine.dispose()
            self.temp.cleanup()

    # --- helpers ------------------------------------------------------------------------------------------------------

    def view(self, **kwargs):
        return self.service.view(self.conversation_id, self.user_id, **kwargs)

    async def push(self, key=None, message="Fix a", **kwargs):
        return await self.service.push(self.conversation_id, self.user_id, key=key or str(uuid4()), message=message, **kwargs)

    async def pull_request(self, key=None, title="Fix the parser", **kwargs):
        return await self.service.open_pull_request(self.conversation_id, self.user_id, key=key or str(uuid4()), title=title, body="Why", **kwargs)

    def revision(self):
        with self.sessions() as db:
            return db.scalar(select(Conversation.revision).where(Conversation.id == self.conversation_id))

    def delivery_row(self):
        with self.sessions() as db:
            return db.scalar(select(ConversationDelivery).where(ConversationDelivery.conversation_id == self.conversation_id,
                                                                ConversationDelivery.status == "active"))

    def commit_count(self):
        with self.sessions() as db:
            return len(list(db.scalars(select(DeliveryCommit))))

    async def code(self, coroutine):
        with self.assertRaises(DeliveryError) as caught:
            await coroutine
        return caught.exception.code

    def edit(self):
        self.work.put("a.py", b"one\nTWO\n")

    # --- availability -------------------------------------------------------------------------------------------------

    async def test_a_conversation_without_a_repository_has_nothing_to_deliver(self):
        with self.sessions() as db, db.begin():
            db.execute(update(Conversation).where(Conversation.id == self.conversation_id).values(repository_json=None))
        view = await self.view()
        self.assertEqual((view["available"], view["unavailable_reason"], view["state"]), (False, "no_repository", "none"))
        self.assertEqual(await self.code(self.push()), "delivery_no_repository")

    async def test_someone_elses_conversation_does_not_exist(self):
        with self.assertRaises(DeliveryError) as caught:
            await self.service.view(self.conversation_id, uuid4())
        self.assertEqual((caught.exception.code, caught.exception.status), ("session_not_found", 404))

    async def test_without_a_github_connection_or_a_sandbox_the_panel_says_why(self):
        view = await self.make_service(auth_mode="local_single_user").view(self.conversation_id, self.user_id)
        self.assertEqual(view["unavailable_reason"], "github_not_connected")
        service = DeliveryService(self.sessions, settings(), provider=None)
        self.assertEqual((await service.view(self.conversation_id, self.user_id))["unavailable_reason"], "sandbox_not_configured")

    # --- changes ------------------------------------------------------------------------------------------------------

    async def test_measuring_the_changes_remembers_a_summary_for_the_card_and_chip(self):
        self.assertIsNone((await self.view())["unpublished"])
        self.edit()
        before = self.revision()
        changes = await self.service.changes(self.conversation_id, self.user_id)
        self.assertEqual([(f["path"], f["status"], f["additions"], f["deletions"]) for f in changes["files"]], [("a.py", "modified", 1, 1)])
        view = await self.view()
        self.assertEqual({k: view["unpublished"][k] for k in ("files", "additions", "deletions")}, {"files": 1, "additions": 1, "deletions": 1})
        self.assertGreater(self.revision(), before)   # readers of the conversation are told
        steady = self.revision()
        await self.service.changes(self.conversation_id, self.user_id)
        self.assertEqual(self.revision(), steady)   # measuring the same thing again tells nobody anything

    async def test_a_vanished_sandbox_is_reported_as_expired_not_as_no_changes(self):
        self.provider.workspace = None
        self.assertEqual((await self.service.changes(self.conversation_id, self.user_id))["workspace"], "expired")

    # --- pushing ------------------------------------------------------------------------------------------------------

    async def test_the_first_push_creates_the_conversations_branch_and_records_it(self):
        self.edit()
        view = await self.push(message="Fix the parser")
        self.assertEqual((view["state"], view["pull_request"], view["unpublished"]), ("branch", None, None))
        self.assertRegex(view["branch"], r"^agentforge/fix-the-parser-fix-[0-9a-f]{6}$|^agentforge/fix-a-[0-9a-f]{6}$|^agentforge/[a-z0-9-]+-[0-9a-f]{6}$")
        self.assertEqual(self.github.refs[view["branch"]], view["head_sha"])
        self.assertEqual(self.github.files_at(view["head_sha"])["a.py"], b"one\nTWO\n")
        self.assertEqual([(c["message"], c["files"]) for c in view["commits"]], [("Fix the parser", 1)])
        self.assertEqual((await self.service.changes(self.conversation_id, self.user_id))["files"], [])   # published: nothing left to push

    async def test_the_same_request_twice_pushes_once(self):
        self.edit()
        key = str(uuid4())
        first = await self.push(key)
        second = await self.push(key)
        self.assertEqual((first["head_sha"], second["head_sha"], self.commit_count()), (second["head_sha"],) * 2 + (1,))

    async def test_later_pushes_add_commits_to_the_same_branch(self):
        self.edit()
        first = await self.push()
        self.work.put("c.py", b"new\n")
        second = await self.push(message="Add c")
        self.assertEqual(second["branch"], first["branch"])
        self.assertEqual(self.github.commits[second["head_sha"]]["parents"], [first["head_sha"]])
        self.assertEqual([c["message"] for c in second["commits"]], ["Add c", "Fix a"])

    async def test_nothing_to_push_and_a_busy_agent_are_refused_before_touching_github(self):
        self.assertEqual(await self.code(self.push()), "nothing_to_publish")
        self.edit()
        with self.sessions() as db, db.begin():
            db.execute(update(Conversation).where(Conversation.id == self.conversation_id).values(active_task_id=None))
        busy = self.make_service()
        busy._context = lambda *a: {**DeliveryService._context(busy, *a), "busy": True}
        self.assertEqual(await self.code(busy.push(self.conversation_id, self.user_id, key=str(uuid4()), message="x")), "agent_running")
        writes_disabled = self.make_service(enable_github_writes=False)
        self.assertEqual(await self.code(writes_disabled.push(self.conversation_id, self.user_id, key=str(uuid4()), message="x")), "github_writes_disabled")
        self.assertEqual([c for c in self.github.calls if c[0] in {"POST", "PATCH"}], [])

    async def test_secret_files_block_the_push_and_name_themselves(self):
        self.edit()
        self.work.put(".env", b"TOKEN=1\n")
        self.assertEqual(await self.code(self.push()), "delivery_blocked_paths:.env")
        self.assertEqual([c for c in self.github.calls if c[0] in {"POST", "PATCH"}], [])
        self.assertIsNone(self.delivery_row().pending_op_json)   # a refusal leaves nothing pending
        await self.service.discard_file(self.conversation_id, self.user_id, ".env")
        self.assertEqual((await self.push())["state"], "branch")

    async def test_a_file_changed_on_github_is_reported_and_nothing_is_overwritten(self):
        self.edit()
        first = await self.push()
        self.github.push_other_commit(first["branch"], {"a.py": b"theirs\n"})
        self.work.put("a.py", b"one\nTWO\nthree\n")
        self.assertEqual(await self.code(self.push()), "github_path_conflict:a.py")
        self.assertEqual(self.github.files_at(self.github.refs[first["branch"]])["a.py"], b"theirs\n")
        self.assertIsNone(self.delivery_row().pending_op_json)

    async def test_a_permission_failure_is_reported_as_one(self):
        self.edit()
        self.github.permissions = {"contents": "read", "pull_requests": "read"}
        self.assertEqual(await self.code(self.push()), "github_write_permission_required")

    # --- lost answers -------------------------------------------------------------------------------------------------

    async def test_a_push_whose_answer_was_lost_is_recognised_on_retry_and_never_duplicated(self):
        self.edit()
        key = str(uuid4())
        original = self.github._route

        def lost(method, path, payload):
            result = original(method, path, payload)
            if method == "POST" and path.endswith("/git/refs"):
                raise GitHubError("github_unavailable")      # GitHub did it; the answer never arrived
            return result
        self.github._route = lost
        self.assertEqual(await self.code(self.push(key)), "delivery_outcome_unknown")
        self.assertEqual((await self.view())["pending_operation"]["kind"], "push")
        self.assertEqual(await self.code(self.push(str(uuid4()), message="other")), "delivery_operation_in_progress")
        self.github._route = original
        view = await self.push(key)                           # the same request again
        self.assertEqual((view["state"], view["pending_operation"]), ("branch", None))
        pushed = [sha for sha, commit in self.github.commits.items() if "AgentForge-Operation:" in commit["message"]]
        self.assertEqual(len(pushed), 1)                      # one commit on GitHub, however many attempts
        self.assertEqual(self.github.refs[view["branch"]], pushed[0])
        self.assertEqual(self.commit_count(), 1)

    async def test_a_pull_request_whose_answer_was_lost_is_adopted_on_retry(self):
        self.edit()
        key = str(uuid4())
        original = self.github._route

        def lost(method, path, payload):
            result = original(method, path, payload)
            if method == "POST" and path.endswith("/pulls"):
                raise GitHubError("github_unavailable")
            return result
        self.github._route = lost
        self.assertEqual(await self.code(self.pull_request(key)), "delivery_outcome_unknown")
        self.github._route = original
        view = await self.pull_request(key)
        self.assertEqual((view["state"], view["pull_request"]["number"], len(self.github.pulls)), ("pull_request", 1, 1))

    async def test_an_explicit_refresh_finds_a_pull_request_opened_but_not_recorded(self):
        self.edit()
        first = await self.push()
        pull = self.github._route("POST", f"/repos/{OWNER_REPO}/pulls", {"title": "t", "body": "", "head": first["branch"], "base": "main", "draft": True})
        with self.sessions() as db, db.begin():
            db.execute(update(ConversationDelivery).where(ConversationDelivery.conversation_id == self.conversation_id)
                       .values(pending_op_json={"key": "k", "kind": "pull-request", "started_at": "2026-01-01T00:00:00+00:00"}))
        refreshed = await self.view(refresh=True)
        self.assertEqual((refreshed["state"], refreshed["pull_request"]["number"], refreshed["pending_operation"]), ("pull_request", pull["number"], None))

    # --- pull requests ------------------------------------------------------------------------------------------------

    async def test_opening_a_pull_request_pushes_the_pending_changes_first(self):
        self.edit()
        view = await self.pull_request(message="Fix the parser")
        pr = view["pull_request"]
        self.assertEqual((view["state"], pr["state"], pr["number"], pr["checks"]["state"]), ("pull_request", "draft", 1, "none"))
        self.assertEqual((pr["mergeable"], pr["merge_methods"]), ("mergeable", ["squash", "merge"]))
        self.assertEqual(view["unpublished"], None)
        self.assertIn(f"/chat/{self.conversation_id}", self.github.pulls[1]["body"])
        self.assertEqual(self.github.pulls[1]["base"], "main")
        self.assertEqual(view["base_branch"], "main")

    async def test_a_pull_request_can_be_opened_for_a_branch_that_was_already_pushed(self):
        self.edit()
        await self.push()
        self.assertEqual((await self.pull_request())["pull_request"]["number"], 1)

    async def test_opening_twice_never_makes_two_pull_requests(self):
        self.edit()
        await self.pull_request()
        self.work.put("c.py", b"more\n")
        again = await self.pull_request(message="More")
        self.assertEqual((len(self.github.pulls), again["pull_request"]["number"], len(again["commits"])), (1, 1, 2))

    async def test_pushing_to_an_open_pull_request_shows_the_new_head_at_once(self):
        self.edit()
        opened = await self.pull_request()
        self.work.put("c.py", b"more\n")
        pushed = await self.push(message="More")
        self.assertNotEqual(opened["pull_request"]["head_sha"], pushed["pull_request"]["head_sha"])
        self.assertEqual(pushed["pull_request"]["head_sha"], self.github.refs[pushed["branch"]])
        self.assertEqual((pushed["state"], len(pushed["commits"])), ("pull_request", 2))

    async def test_status_follows_the_pull_request_and_a_failed_read_keeps_what_was_known(self):
        self.edit()
        await self.pull_request()
        self.github.check_runs = [{"id": 1, "name": "ci", "status": "completed", "conclusion": "failure", "html_url": "https://ci/1", "app": {"id": 1, "name": "Actions"}}]
        failing = (await self.view(refresh=True))["pull_request"]
        self.assertEqual((failing["checks"]["state"], failing["checks"]["failed"]), ("failing", 1))
        self.github.fail["/pulls/1"] = "github_unavailable"
        degraded = await self.view(refresh=True)
        self.assertEqual((degraded["sync_error"], degraded["pull_request"]["checks"]["state"]), ("github_unavailable", "failing"))
        self.github.fail.clear()
        recovered = await self.view(refresh=True)
        self.assertEqual((recovered["sync_error"], recovered["pull_request"]["checks"]["state"]), (None, "failing"))

    async def test_a_webhook_marks_the_pull_request_stale_and_the_next_read_refreshes_it(self):
        self.edit()
        await self.pull_request()
        before = self.revision()
        self.assertEqual(self.service.invalidate(OWNER_REPO, pr_number=1), 1)
        self.assertGreater(self.revision(), before)
        self.assertTrue(self.delivery_row().pr_json["stale"])
        steady = self.revision()
        self.service.invalidate(OWNER_REPO, pr_number=1)       # a burst of CI events: nothing more to say
        self.assertEqual(self.revision(), steady)
        self.assertEqual(self.service.invalidate("other/repo", pr_number=1), 0)
        await self.service.sync(self.conversation_id, self.user_id, self.service._snapshot(self.delivery_row()))
        self.assertFalse(self.delivery_row().pr_json.get("stale"))

    async def test_ready_then_merge_then_continue_from_the_updated_branch(self):
        self.edit()
        draft = await self.pull_request()
        self.assertEqual(await self.code(self.service.merge(self.conversation_id, self.user_id, key=str(uuid4()), method="squash",
                         expected_head_sha=draft["pull_request"]["head_sha"])), "github_draft_pull_request")
        ready = await self.service.mark_ready(self.conversation_id, self.user_id, key=str(uuid4()))
        self.assertEqual(ready["pull_request"]["state"], "open")
        merged = await self.service.merge(self.conversation_id, self.user_id, key=str(uuid4()), method="squash",
                                          expected_head_sha=ready["pull_request"]["head_sha"])
        self.assertEqual((merged["pull_request"]["state"], merged["pull_request"]["merged_at"] is not None), ("merged", True))
        reset = await self.service.reset(self.conversation_id, self.user_id, key=str(uuid4()))
        self.assertEqual((reset["state"], reset["pull_request"], reset["branch"], self.provider.killed), ("none", None, None, 1))
        with self.sessions() as db:
            self.assertIsNone(db.scalar(select(Conversation.sandbox_id).where(Conversation.id == self.conversation_id)))
            self.assertEqual(db.scalar(select(ConversationDelivery.status).where(ConversationDelivery.conversation_id == self.conversation_id)), "archived")

    async def test_a_merge_against_a_head_that_moved_is_refused(self):
        self.edit()
        ready = await self.service.mark_ready(self.conversation_id, self.user_id, key=str(uuid4())) if False else None
        draft = await self.pull_request(draft=False)
        self.github.push_other_commit(draft["branch"], {"x.md": b"x"})
        self.assertEqual(await self.code(self.service.merge(self.conversation_id, self.user_id, key=str(uuid4()), method="merge",
                         expected_head_sha=draft["pull_request"]["head_sha"])), "github_branch_moved")
        self.assertIsNone(self.delivery_row().pending_op_json)

    async def test_closing_and_the_guards_around_reset(self):
        self.edit()
        await self.pull_request()
        self.assertEqual(await self.code(self.service.reset(self.conversation_id, self.user_id, key=str(uuid4()))), "delivery_reset_not_allowed")
        closed = await self.service.close(self.conversation_id, self.user_id, key=str(uuid4()))
        self.assertEqual(closed["pull_request"]["state"], "closed")
        self.work.put("c.py", b"unpushed\n")
        await self.service.changes(self.conversation_id, self.user_id)
        self.assertEqual(await self.code(self.service.reset(self.conversation_id, self.user_id, key=str(uuid4()))), "delivery_unpublished_changes")
        self.assertEqual((await self.service.reset(self.conversation_id, self.user_id, key=str(uuid4()), discard_unpublished=True))["state"], "none")

    async def test_actions_on_a_pull_request_need_one(self):
        self.assertEqual(await self.code(self.service.close(self.conversation_id, self.user_id, key=str(uuid4()))), "delivery_not_published")

    async def test_update_branch_asks_github_to_merge_the_base_in(self):
        self.edit()
        await self.pull_request(draft=False)
        await self.service.update_branch(self.conversation_id, self.user_id, key=str(uuid4()))
        self.assertTrue(any(c[0] == "PUT" and c[1].endswith("/update-branch") for c in self.github.calls))

    # --- the agent's tool ---------------------------------------------------------------------------------------------

    async def test_the_agent_publishes_through_the_same_flow_and_a_second_call_adds_a_commit(self):
        self.edit()
        task_id = uuid4()
        first = await self.service.publish_for_agent(self.conversation_id, self.user_id, self.work, key="op1", title="Fix the parser", body="Why", task_id=task_id)
        self.assertEqual((first["pr_number"], first["pr_url"]), (1, f"https://github.com/{OWNER_REPO}/pull/1"))
        self.work.put("c.py", b"more\n")
        second = await self.service.publish_for_agent(self.conversation_id, self.user_id, self.work, key="op2", title="More", body="", task_id=task_id)
        self.assertEqual((second["pr_number"], second["branch"], len(self.github.pulls)), (1, first["branch"], 1))
        self.assertEqual(self.github.commits[second["head_sha"]]["parents"], [first["head_sha"]])
        view = await self.view()
        self.assertEqual(len(view["commits"]), 2)
        with self.sessions() as db:
            self.assertEqual(db.scalar(select(DeliveryCommit.task_id).limit(1)), task_id)

    async def test_the_agent_may_publish_while_its_own_turn_is_running(self):
        self.edit()
        service = self.make_service()
        real = DeliveryService._context
        service._context = lambda *a: {**real(service, *a), "busy": True}
        result = await service.publish_for_agent(self.conversation_id, self.user_id, self.work, key="op", title="t", body="", task_id=uuid4())
        self.assertEqual(result["pr_number"], 1)

    # --- the sidebar's summary ----------------------------------------------------------------------------------------

    def listed(self):
        from agentforge.repositories.sessions import SessionRepository
        return next(item for item in SessionRepository(self.sessions).list(self.user_id) if item.id == self.conversation_id)

    async def test_the_conversation_list_carries_where_each_conversation_stands_on_github(self):
        self.assertIsNone(self.listed().delivery)               # nothing for GitHub yet: no mark
        self.edit()
        await self.service.changes(self.conversation_id, self.user_id)
        summary = self.listed().delivery
        self.assertEqual((summary.state, summary.unpublished, summary.pr_state), ("none", True, None))
        await self.pull_request()
        self.github.check_runs = [{"id": 1, "name": "ci", "status": "completed", "conclusion": "failure", "html_url": "https://ci/1", "app": {"id": 1, "name": "Actions"}}]
        await self.view(refresh=True)
        summary = self.listed().delivery
        self.assertEqual((summary.state, summary.pr_state, summary.checks, summary.mergeable, summary.unpublished), ("pull_request", "draft", "failing", "mergeable", False))

    def test_a_fault_reading_the_summaries_only_costs_the_marks_never_the_list(self):
        from agentforge.repositories.sessions import SessionRepository

        class Broken:
            def begin_nested(self):
                raise RuntimeError('relation "conversation_deliveries" does not exist')
        self.assertEqual(SessionRepository.deliveries(Broken(), [uuid4()]), {})
        self.assertEqual(SessionRepository.deliveries(Broken(), []), {})

    # --- faults are typed, never bare ---------------------------------------------------------------------------------

    async def test_a_fault_before_anything_was_written_is_reported_as_unavailable_not_as_an_unknown_outcome(self):
        # e.g. a database that has not been migrated yet: this used to escape as a bare exception and end the agent's run.
        self.edit()

        def broken(*args):
            raise RuntimeError("relation conversation_deliveries does not exist")
        self.service._context = broken
        for call in (self.service.view(self.conversation_id, self.user_id), self.push(),
                     self.service.changes(self.conversation_id, self.user_id),
                     self.service.publish_for_agent(self.conversation_id, self.user_id, self.work, key="k", title="t", body="", task_id=uuid4())):
            self.assertEqual(await self.code(call), "delivery_unavailable")
        self.assertEqual([c for c in self.github.calls if c[0] in {"POST", "PATCH"}], [])

    async def test_an_unforeseen_fault_after_github_may_have_been_written_to_is_an_uncertain_outcome_that_the_same_request_resolves(self):
        self.edit()
        key = str(uuid4())
        from agentforge.integrations import github_delivery
        original = github_delivery.publish_commit

        async def broken(*args, **kwargs):
            await original(*args, **kwargs)           # GitHub did it...
            raise KeyError("unexpected payload shape")   # ...and then something nobody planned for happened
        with patch.object(github_delivery, "publish_commit", broken):
            self.assertEqual(await self.code(self.push(key)), "delivery_outcome_unknown")
        self.assertEqual((await self.view())["pending_operation"]["kind"], "push")
        view = await self.push(key)
        self.assertEqual((view["state"], view["pending_operation"], self.commit_count()), ("branch", None, 1))
        self.assertEqual(len([c for c in self.github.commits.values() if "AgentForge-Operation:" in c["message"]]), 1)

    # --- suggestions and housekeeping ---------------------------------------------------------------------------------

    def add_turn(self, sequence, answer, state):
        with self.sessions() as db, db.begin():
            user = ConversationMessage(session_id=self.conversation_id, sequence=sequence, role="user", content="question")
            assistant = ConversationMessage(session_id=self.conversation_id, sequence=sequence + 1, role="assistant", content=answer)
            db.add_all([user, assistant])
            db.flush()
            turn = SessionTurn(session_id=self.conversation_id, idempotency_key=uuid4(), request_sha256="x", user_message_id=user.id)
            db.add(turn)
            db.flush()
            db.add(TurnExecution(turn_id=turn.id, session_id=self.conversation_id, ordinal=sequence, assistant_message_id=assistant.id, state=state))

    async def test_suggestions_come_from_the_conversation_and_the_changes(self):
        self.add_turn(1, "I fixed the parser's off-by-one.", "completed")
        # A later run that failed leaves a receipt as its "answer": that is not a summary of the work.
        self.add_turn(3, "This run stopped because an action's outcome could not be confirmed.", "failed")
        self.edit()
        suggestion = await self.service.suggest(self.conversation_id, self.user_id)
        self.assertEqual((suggestion["commit_message"], suggestion["pr_title"]), ("Fix the parser",) * 2)
        self.assertIn("I fixed the parser's off-by-one.", suggestion["pr_body"])
        self.assertNotIn("could not be confirmed", suggestion["pr_body"])
        self.assertIn("`a.py` (modified, +1 −1)", suggestion["pr_body"])
        self.assertRegex(suggestion["branch"], r"^agentforge/fix-the-parser-[0-9a-f]{6}$")

    async def test_a_delivery_for_another_repository_is_set_aside_when_the_conversation_moves(self):
        self.edit()
        await self.push()
        with self.sessions() as db, db.begin():
            db.execute(update(Conversation).where(Conversation.id == self.conversation_id).values(
                repository_json={"repo_id": "owner/other", "ref": "main"}))
        view = await self.view()
        self.assertEqual((view["state"], view["branch"], view["repo_id"]), ("none", None, "owner/other"))
