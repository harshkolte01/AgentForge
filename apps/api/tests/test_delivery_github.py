"""Publishing to GitHub and reading pull request status (integrations/github_delivery.py) against a stateful fake GitHub."""
from unittest import IsolatedAsyncioTestCase, TestCase

from agentforge.integrations import github_delivery as gh
from agentforge.integrations.github_client import GitHubError

from delivery_fakes import OWNER_REPO, FakeGitHub, blob_sha

FILES = {"a.py": b"one\n", "b.py": b"two\n"}
BRANCH = "agentforge/fix-abc123"


def change(path, data, old=FILES):
    return gh.Change("100644", data, blob_sha(old[path]) if path in old else None, blob_sha(data) if data is not None else None)


class PublishTests(IsolatedAsyncioTestCase):
    def setUp(self):
        self.github = FakeGitHub(FILES)

    async def publish(self, changes, *, key="k1", known=False, message="Fix a", base=None, **kwargs):
        return await gh.publish_commit(self.github, OWNER_REPO, branch=BRANCH, base_sha=base or self.github.base, changes=changes,
                                       message=message, operation_key=key, branch_known=known, **kwargs)

    def writes(self):
        return [(method, path) for method, path, _ in self.github.calls if method in {"POST", "PATCH", "PUT"}]

    async def test_the_first_push_creates_the_branch_at_the_base_with_one_commit_on_top(self):
        result = await self.publish({"a.py": change("a.py", b"uno\n")}, coauthor=("alice", 42))
        self.assertTrue(result.created_branch)
        self.assertEqual(self.github.refs[BRANCH], result.commit_sha)
        commit = self.github.commits[result.commit_sha]
        self.assertEqual(commit["parents"], [self.github.base])
        self.assertIn("AgentForge-Operation: k1", commit["message"])
        self.assertIn("Co-authored-by: alice <42+alice@users.noreply.github.com>", commit["message"])
        self.assertEqual(self.github.files_at(result.commit_sha), {"a.py": b"uno\n", "b.py": b"two\n"})
        self.assertEqual(self.github.refs["main"], self.github.base)  # the base branch is never touched

    async def test_a_later_push_moves_the_branch_forward_without_forcing_and_keeps_earlier_work(self):
        first = await self.publish({"a.py": change("a.py", b"uno\n")})
        second = await self.publish({"b.py": change("b.py", b"dos\n"), "c.py": change("c.py", b"new\n")}, key="k2", known=True)
        self.assertFalse(second.created_branch)
        self.assertEqual(self.github.commits[second.commit_sha]["parents"], [first.commit_sha])
        self.assertEqual(self.github.files_at(second.commit_sha), {"a.py": b"uno\n", "b.py": b"dos\n", "c.py": b"new\n"})
        patch = next(p for m, path, p in self.github.calls if m == "PATCH")
        self.assertIs(patch["force"], False)

    async def test_a_file_changed_on_github_since_the_last_push_is_never_overwritten(self):
        await self.publish({"a.py": change("a.py", b"uno\n")})
        self.github.push_other_commit(BRANCH, {"a.py": b"theirs\n"})
        before = list(self.github.calls)
        with self.assertRaisesRegex(GitHubError, r"github_path_conflict:a\.py"):
            await self.publish({"a.py": change("a.py", b"mine\n", {**FILES, "a.py": b"uno\n"})}, key="k2", known=True)
        self.assertFalse(any(m in {"POST", "PATCH"} for m, _, _ in self.github.calls[len(before):]))  # refused before writing anything

    async def test_other_peoples_changes_to_other_files_are_kept(self):
        first = await self.publish({"a.py": change("a.py", b"uno\n")})
        theirs = self.github.push_other_commit(BRANCH, {"docs.md": b"docs\n"})
        second = await self.publish({"b.py": change("b.py", b"dos\n")}, key="k2", known=True)
        self.assertEqual(self.github.commits[second.commit_sha]["parents"], [theirs])
        self.assertEqual(self.github.files_at(second.commit_sha), {"a.py": b"uno\n", "b.py": b"dos\n", "docs.md": b"docs\n"})
        self.assertNotEqual(first.commit_sha, second.commit_sha)

    async def test_a_file_that_already_holds_the_new_content_is_not_a_conflict(self):
        await self.publish({"a.py": change("a.py", b"uno\n")})
        self.github.push_other_commit(BRANCH, {"b.py": b"dos\n"})   # the same content AgentForge is about to send
        result = await self.publish({"b.py": change("b.py", b"dos\n")}, key="k2", known=True)
        self.assertEqual(self.github.files_at(result.commit_sha)["b.py"], b"dos\n")

    async def test_a_retry_after_a_lost_answer_finds_its_own_commit_and_writes_nothing(self):
        first = await self.publish({"a.py": change("a.py", b"uno\n")})
        before = len(self.writes())
        again = await self.publish({"a.py": change("a.py", b"uno\n")}, known=False)   # same key; the record of the push was lost
        self.assertTrue(again.already)
        self.assertEqual(again.commit_sha, first.commit_sha)
        self.assertEqual(len(self.writes()), before)

    async def test_a_branch_agentforge_never_pushed_to_is_not_taken_over(self):
        self.github.refs[BRANCH] = self.github.base
        with self.assertRaisesRegex(GitHubError, "github_publish_branch_exists"):
            await self.publish({"a.py": change("a.py", b"uno\n")})
        self.assertEqual(self.writes(), [])

    async def test_a_deleted_branch_is_not_silently_restarted_without_the_work_already_on_it(self):
        with self.assertRaisesRegex(GitHubError, "github_branch_missing"):
            await self.publish({"a.py": change("a.py", b"uno\n")}, known=True)
        self.assertEqual(self.writes(), [])

    async def test_a_deletion_removes_the_file(self):
        result = await self.publish({"a.py": change("a.py", None)})
        self.assertEqual(self.github.files_at(result.commit_sha), {"b.py": b"two\n"})

    async def test_a_branch_that_moved_between_reading_and_writing_is_a_conflict_not_a_rewrite(self):
        await self.publish({"a.py": change("a.py", b"uno\n")})
        original = self.github._route

        def racing(method, path, payload):
            if method == "POST" and path.endswith("/git/commits"):
                self.github.push_other_commit(BRANCH, {"x.md": b"x"})   # somebody pushes while we build our commit
            return original(method, path, payload)
        self.github._route = racing
        with self.assertRaisesRegex(GitHubError, "github_write_conflict"):
            await self.publish({"b.py": change("b.py", b"dos\n")}, key="k2", known=True)
        self.assertIn("x.md", self.github.files_at(self.github.refs[BRANCH]))   # their commit is intact

    async def test_unsafe_oversized_and_empty_changes_are_refused_before_any_request(self):
        cases = {
            "github_nothing_to_commit": {},
            "github_unsafe_change_path": {".env": gh.Change("100644", b"s", None, blob_sha(b"s"))},
            "github_unsupported_change": {".agentforge/x": gh.Change("100644", b"s", None, blob_sha(b"s"))},
            "github_unsupported_change ": {"link": gh.Change("120000", b"s", None, blob_sha(b"s"))},
            "github_change_too_large": {f"f{n}": gh.Change("100644", b"x", None, "0" * 40) for n in range(gh.MAX_CHANGED_FILES + 1)},
        }
        for code, changes in cases.items():
            with self.subTest(code=code), self.assertRaisesRegex(GitHubError, code.strip()):
                await self.publish(changes)
        self.assertEqual(self.github.calls, [])


class PullRequestTests(IsolatedAsyncioTestCase):
    def setUp(self):
        self.github = FakeGitHub(FILES)
        self.github.refs[BRANCH] = self.github.base

    async def open(self, **kwargs):
        return await gh.open_pull_request(self.github, OWNER_REPO, branch=BRANCH, base_branch="main", title="Fix", body="Because",
                                          draft=kwargs.pop("draft", True), footer="Opened by AgentForge.", **kwargs)

    async def test_a_draft_pull_request_is_opened_for_the_branch(self):
        pull = await self.open()
        self.assertEqual((pull["number"], pull["draft"], pull["url"]), (1, True, f"https://github.com/{OWNER_REPO}/pull/1"))
        sent = next(p for m, path, p in self.github.calls if m == "POST" and path.endswith("/pulls"))
        self.assertEqual((sent["head"], sent["base"], sent["draft"], sent["maintainer_can_modify"]), (BRANCH, "main", True, False))
        self.assertIn("Opened by AgentForge.", sent["body"])

    async def test_an_open_pull_request_for_the_branch_is_adopted_not_duplicated(self):
        first = await self.open()
        again = await self.open()
        self.assertEqual(again["number"], first["number"])
        self.assertEqual(len(self.github.pulls), 1)

    async def test_an_answer_that_is_not_the_pull_request_asked_for_is_not_trusted(self):
        original = self.github._pull_json
        self.github._pull_json = lambda pull: {**original(pull), "html_url": "https://evil.example/owner/repo/pull/1"}
        with self.assertRaisesRegex(GitHubError, "confirmation_failed"):
            await self.open()

    async def test_ready_merge_close_and_update_branch_send_exactly_what_was_asked(self):
        pull = await self.open()
        await gh.mark_ready(self.github, pull["node_id"])
        head = self.github.refs[BRANCH]
        await gh.update_pull_branch(self.github, OWNER_REPO, 1, head)
        sha = await gh.merge_pull_request(self.github, OWNER_REPO, 1, method="squash", expected_head=head, title="Fix (#1)")
        self.assertEqual(sha, "9" * 40)
        merge = next(p for m, path, p in self.github.calls if m == "PUT" and path.endswith("/merge"))
        self.assertEqual(merge, {"merge_method": "squash", "sha": head, "commit_title": "Fix (#1)"})
        self.assertTrue(self.github.pulls[1]["merged"])

    async def test_a_merge_is_refused_when_the_head_moved_or_the_pull_request_is_a_draft(self):
        await self.open()
        head = self.github.refs[BRANCH]
        with self.assertRaisesRegex(GitHubError, "github_not_mergeable"):
            await gh.merge_pull_request(self.github, OWNER_REPO, 1, method="merge", expected_head=head)   # still a draft
        self.github.pulls[1]["draft"] = False
        with self.assertRaisesRegex(GitHubError, "github_write_conflict"):
            await gh.merge_pull_request(self.github, OWNER_REPO, 1, method="merge", expected_head="f" * 40)
        with self.assertRaisesRegex(GitHubError, "github_invalid_merge"):
            await gh.merge_pull_request(self.github, OWNER_REPO, 1, method="octopus", expected_head=head)

    async def test_closing_marks_the_pull_request_closed(self):
        await self.open()
        await gh.close_pull_request(self.github, OWNER_REPO, 1)
        self.assertEqual(self.github.pulls[1]["state"], "closed")


class StatusTests(IsolatedAsyncioTestCase):
    def setUp(self):
        self.github = FakeGitHub(FILES)
        self.github.refs[BRANCH] = self.github.base

    async def status(self, permissions=None):
        await gh.open_pull_request(self.github, OWNER_REPO, branch=BRANCH, base_branch="main", title="Fix", body="", draft=False)
        installation = {"permissions": permissions or self.github.permissions}
        return await gh.read_pull_status(self.github, OWNER_REPO, 1, installation=installation, repo_settings=None)

    def run_(self, id, name, status="completed", conclusion="success", app="GitHub Actions", **extra):
        return {"id": id, "name": name, "status": status, "conclusion": conclusion, "html_url": f"https://ci/{id}", "app": {"id": 1, "name": app}, **extra}

    async def test_a_rerun_replaces_the_earlier_run_and_failures_sort_first_with_their_reason(self):
        self.github.check_runs = [self.run_(1, "build", conclusion="failure"), self.run_(5, "build"),
            self.run_(2, "lint", conclusion="failure", output={"title": "2 errors", "summary": "E501 line too long"}),
            self.run_(3, "deploy", status="in_progress", conclusion=None), self.run_(4, "docs", conclusion="skipped")]
        checks = (await self.status())["checks"]
        self.assertEqual([(c["name"], c["status"]) for c in checks["items"]],
                         [("lint", "failed"), ("deploy", "pending"), ("build", "passed"), ("docs", "neutral")])
        self.assertEqual((checks["state"], checks["failed"], checks["pending"], checks["passed"]), ("failing", 1, 1, 1))
        self.assertIn("E501", checks["items"][0]["summary"])

    async def test_commit_statuses_are_included_once_and_never_duplicate_a_check_run(self):
        self.github.check_runs = [self.run_(1, "ci")]
        self.github.statuses = [{"context": "ci", "state": "failure"}, {"context": "vercel", "state": "pending", "target_url": "https://v"}]
        names = [(c["name"], c["status"]) for c in (await self.status())["checks"]["items"]]
        self.assertEqual(names, [("vercel", "pending"), ("ci", "passed")])

    async def test_checks_are_unavailable_not_absent_when_the_app_lacks_the_permission(self):
        status = await self.status({"contents": "write", "pull_requests": "write", "metadata": "read"})
        self.assertEqual(status["checks"]["state"], "unavailable")

    async def test_no_checks_at_all_is_none_and_all_passing_is_passing(self):
        self.assertEqual((await self.status())["checks"]["state"], "none")
        self.github.check_runs = [self.run_(1, "ci")]
        self.assertEqual((await self.status())["checks"]["state"], "passing")

    async def test_each_reviewers_latest_verdict_counts_and_a_comment_never_erases_it(self):
        self.github.reviews = [
            {"id": 1, "user": {"login": "bob"}, "state": "CHANGES_REQUESTED"}, {"id": 2, "user": {"login": "bob"}, "state": "COMMENTED"},
            {"id": 3, "user": {"login": "eve"}, "state": "APPROVED"}]
        reviews = (await self.status())["reviews"]
        self.assertEqual(reviews["decision"], "changes_requested")
        self.assertEqual(reviews["items"], [{"login": "bob", "state": "changes_requested"}, {"login": "eve", "state": "approved"}])
        self.github.reviews.append({"id": 4, "user": {"login": "bob"}, "state": "APPROVED"})
        self.assertEqual((await self.status())["reviews"]["decision"], "approved")

    async def test_state_mergeability_behind_count_and_merge_methods(self):
        self.github.behind_by = 3
        status = await self.status()
        self.assertEqual((status["state"], status["mergeable"], status["behind_base"], status["head_sha"]), ("open", "mergeable", 3, self.github.base))
        self.assertEqual(status["merge_methods"], ["squash", "merge"])   # rebase is switched off in this repository
        self.github.pulls[1]["draft"] = True
        self.assertEqual((await self.status())["state"], "draft")
        self.github.pulls[1].update(draft=False, merged=True, state="closed")
        merged = await self.status()
        self.assertEqual((merged["state"], merged["merged_at"] is not None), ("merged", True))


class ClassificationTests(TestCase):
    def test_check_runs_and_statuses_fall_into_four_classes(self):
        self.assertEqual([gh.classify_run({"status": s, "conclusion": c}) for s, c in
            [("queued", None), ("in_progress", None), ("completed", "success"), ("completed", "failure"), ("completed", "timed_out"),
             ("completed", "cancelled"), ("completed", "neutral"), ("completed", "skipped"), ("completed", "action_required")]],
            ["pending", "pending", "passed", "failed", "failed", "neutral", "neutral", "neutral", "failed"])
        self.assertEqual([gh.classify_status(s) for s in ("success", "pending", "failure", "error", "other")],
                         ["passed", "pending", "failed", "failed", "neutral"])

    def test_conflicting_is_reported_only_when_github_says_so(self):
        self.assertEqual(gh.mergeable_label({"mergeable": False}), "conflicting")
        self.assertEqual(gh.mergeable_label({"mergeable": None, "mergeable_state": "dirty"}), "conflicting")
        self.assertEqual(gh.mergeable_label({"mergeable": None, "mergeable_state": "unknown"}), "unknown")
        self.assertEqual(gh.mergeable_label({"mergeable": True}), "mergeable")
