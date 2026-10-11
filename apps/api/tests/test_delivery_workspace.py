"""Measuring and moving the published marker inside the sandbox repository, with real git (delivery/workspace.py)."""
import tempfile
from pathlib import Path
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import patch

from agentforge.delivery import workspace as ws
from agentforge.integrations.e2b_sandbox import SandboxUnavailable

from delivery_fakes import LocalWorkspace, git

BASE_SHA = "a" * 40
FILES = {"a.py": b"one\ntwo\nthree\n", "b.py": b"keep\n", "src/c.py": b"print(1)\n", ".gitignore": b"*.log\n"}
ZERO = "0" * 40


class ParsingTests(TestCase):
    def test_raw_output_gives_status_mode_and_both_blobs(self):
        raw = f":100644 100644 {'1' * 40} {'2' * 40} M\0a.py\0:000000 100644 {ZERO} {'3' * 40} A\0new.py\0:100644 000000 {'4' * 40} {ZERO} D\0old.py\0"
        self.assertEqual(ws.parse_raw(raw), [("a.py", "modified", "100644", "1" * 40, "2" * 40), ("new.py", "added", "100644", None, "3" * 40),
                                             ("old.py", "deleted", "000000", "4" * 40, None)])
        self.assertEqual(ws.parse_raw(""), [])
        self.assertEqual(ws.parse_raw("\0"), [])

    def test_numstat_marks_binary_files(self):
        self.assertEqual(ws.parse_numstat("3\t1\ta.py\0-\t-\timg.png\0"), {"a.py": (3, 1, False), "img.png": (0, 0, True)})

    def test_a_torn_last_file_is_dropped_when_the_patch_hit_the_size_limit(self):
        text = "diff --git a/x b/x\n+1\ndiff --git a/y b/y\n+2\ndiff --git a/z b/z\n+3 (cut"
        self.assertEqual(len(ws.split_patches(text, True)), 2)
        self.assertEqual(len(ws.split_patches(text, False)), 3)

    def test_what_cannot_be_published_is_named_with_the_reason(self):
        self.assertEqual(ws.blocked_reason(".env", "100644"), "secret")
        self.assertEqual(ws.blocked_reason("keys/server.pem", "100644"), "secret")
        self.assertEqual(ws.blocked_reason("../x", "100644"), "path")
        self.assertEqual(ws.blocked_reason(".agentforge/repo", "100644"), "path")
        self.assertEqual(ws.blocked_reason("vendor/sub", "160000"), "submodule")
        self.assertEqual(ws.blocked_reason("link", "120000"), "symlink")
        self.assertIsNone(ws.blocked_reason("src/a.py", "100755"))


class GitTests(IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.work = LocalWorkspace(root / "work", FILES, BASE_SHA)
        for name, value in (("MEASURE_INDEX", str(root / "measure.index")), ("INDEX_INFO", str(root / "index-info"))):
            patcher = patch.object(ws, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    async def test_nothing_changed_means_no_files(self):
        measured = await ws.measure(self.work)
        self.assertEqual((measured.baseline, measured.files, measured.truncated), ("base", [], False))
        self.assertIsNone(ws.summary(measured))

    async def test_edits_additions_and_deletions_are_measured_with_counts_and_patches(self):
        self.work.put("a.py", b"one\nTWO\nthree\nfour\n")
        self.work.put("new/dir/n.py", b"x = 1\n")
        self.work.remove("b.py")
        self.work.put("debug.log", b"ignored by .gitignore\n")
        measured = await ws.measure(self.work)
        by_path = {f.path: f for f in measured.files}
        self.assertEqual(sorted(by_path), ["a.py", "b.py", "new/dir/n.py"])   # the ignored file never appears
        self.assertEqual((by_path["a.py"].status, by_path["a.py"].additions, by_path["a.py"].deletions), ("modified", 2, 1))
        self.assertEqual((by_path["new/dir/n.py"].status, by_path["new/dir/n.py"].additions), ("added", 1))
        self.assertEqual((by_path["b.py"].status, by_path["b.py"].deletions), ("deleted", 1))
        self.assertIn("+TWO", by_path["a.py"].patch)
        self.assertTrue(by_path["a.py"].patch.startswith("diff --git a/a.py b/a.py"))
        self.assertEqual(sum(f.additions for f in measured.files), 3)
        self.assertEqual(ws.summary(measured)["files"], 3)

    async def test_the_measurement_changes_with_the_files_and_not_otherwise(self):
        self.work.put("a.py", b"changed\n")
        first = (await ws.measure(self.work)).diff_version
        self.assertEqual((await ws.measure(self.work)).diff_version, first)
        self.work.put("a.py", b"changed again\n")
        self.assertNotEqual((await ws.measure(self.work)).diff_version, first)

    async def test_the_agents_own_git_state_is_never_touched_by_measuring(self):
        git(self.work.root, "add", "b.py")           # the agent staged something itself
        self.work.put("a.py", b"edited\n")
        await ws.measure(self.work)
        self.assertEqual(git(self.work.root, "diff", "--cached", "--name-only").split(), [])  # nothing staged: we added nothing...
        self.work.put("b.py", b"keep\nmore\n")
        git(self.work.root, "add", "b.py")
        await ws.measure(self.work)
        self.assertEqual(git(self.work.root, "diff", "--cached", "--name-only").split(), ["b.py"])  # ...and removed nothing
        self.assertFalse(Path(ws.MEASURE_INDEX).exists())
        self.assertFalse((self.work.root / ".git" / "index.lock").exists())

    async def test_binary_and_secret_files_are_flagged(self):
        self.work.put("logo.png", b"\x89PNG\x00\x01\x02")
        self.work.put(".env", b"TOKEN=1\n")
        files = {f.path: f for f in (await ws.measure(self.work)).files}
        self.assertTrue(files["logo.png"].binary)
        self.assertEqual((files["logo.png"].patch, files["logo.png"].blocked), ("", None))
        self.assertEqual(files[".env"].blocked, "secret")

    async def test_a_huge_patch_is_cut_per_file_but_still_counted(self):
        self.work.put("big.txt", ("line of text\n" * 20000).encode())
        file = next(f for f in (await ws.measure(self.work)).files if f.path == "big.txt")
        self.assertEqual((file.additions, file.patch, file.truncated), (20000, "", True))

    async def test_contents_are_read_exactly_and_a_file_that_changed_since_the_measurement_is_caught(self):
        self.work.put("a.py", b"edited\n")
        measured = await ws.measure(self.work, patches=False)
        self.assertEqual(await ws.read_contents(self.work, measured.files), {"a.py": b"edited\n"})
        self.work.put("a.py", b"edited, then the agent kept writing\n")
        with self.assertRaises(ws.WorkspaceChanged):
            await ws.read_contents(self.work, measured.files)

    async def test_after_a_push_only_newer_changes_remain_and_edits_made_meanwhile_are_not_swallowed(self):
        self.work.put("a.py", b"first\n")
        self.work.put("new.py", b"n\n")
        self.work.remove("b.py")
        measured = await ws.measure(self.work, patches=False)
        self.work.put("a.py", b"second edit made while pushing\n")     # the agent wrote after the measurement
        await ws.advance_baseline(self.work, measured.files, "c" * 40)
        after = await ws.measure(self.work)
        self.assertEqual(after.baseline, "published")
        self.assertEqual([(f.path, f.status) for f in after.files], [("a.py", "modified")])   # only the late edit is still unpublished
        self.assertIn("-first", after.files[0].patch)
        self.assertEqual(git(self.work.root, "rev-parse", "agentforge-base").strip(), git(self.work.root, "rev-parse", "agentforge-base").strip())
        # a second push builds on the first
        await ws.advance_baseline(self.work, after.files, "d" * 40)
        self.assertEqual((await ws.measure(self.work)).files, [])

    async def test_the_old_blob_of_a_file_is_the_one_last_published_not_the_original(self):
        self.work.put("a.py", b"first\n")
        await ws.advance_baseline(self.work, (await ws.measure(self.work, patches=False)).files, "c" * 40)
        self.work.put("a.py", b"second\n")
        file = (await ws.measure(self.work, patches=False)).files[0]
        import hashlib
        self.assertEqual(file.old, hashlib.sha1(b"blob 6\0first\n").hexdigest())

    async def test_discarding_restores_an_edited_file_and_removes_an_added_one(self):
        self.work.put("a.py", b"oops\n")
        self.work.put("added.py", b"x\n")
        for path in ("a.py", "added.py"):
            await ws.discard(self.work, path, baseline_has=await ws.baseline_has(self.work, path))
        self.assertEqual((self.work.root / "a.py").read_bytes(), FILES["a.py"])
        self.assertFalse((self.work.root / "added.py").exists())
        self.assertEqual((await ws.measure(self.work)).files, [])

    async def test_discarding_goes_back_to_the_published_state_not_the_original(self):
        self.work.put("a.py", b"published\n")
        await ws.advance_baseline(self.work, (await ws.measure(self.work, patches=False)).files, "c" * 40)
        self.work.put("a.py", b"unwanted\n")
        await ws.discard(self.work, "a.py", baseline_has=True)
        self.assertEqual((self.work.root / "a.py").read_bytes(), b"published\n")

    async def test_a_path_that_escapes_the_workspace_or_reaches_into_git_is_refused(self):
        for path in ("../outside", "/etc/passwd", "a/../../b", ".git/config", ".agentforge/repo", "a//b", "a\\b", "", "x\ny"):
            with self.subTest(path=path):
                with self.assertRaises(ValueError):
                    await ws.discard(self.work, path, baseline_has=False)
                with self.assertRaises(ValueError):
                    await ws.baseline_has(self.work, path)

    async def test_a_secret_file_can_be_discarded_so_it_stops_blocking_a_push(self):
        self.work.put(".env", b"TOKEN=1\n")
        await ws.discard(self.work, ".env", baseline_has=await ws.baseline_has(self.work, ".env"))
        self.assertFalse((self.work.root / ".env").exists())

    async def test_the_marker_names_the_repository_and_commit_the_checkout_came_from(self):
        self.assertEqual(await ws.read_marker(self.work), ("owner/repo", BASE_SHA))
        (self.work.root / ".agentforge" / "repo").write_text("garbage")
        self.assertIsNone(await ws.read_marker(self.work))

    async def test_a_folder_that_is_not_a_checkout_is_reported_not_measured_as_empty(self):
        empty = LocalWorkspace(Path(self.temp.name) / "empty", {}, BASE_SHA)
        import shutil
        shutil.rmtree(empty.root / ".git")
        with self.assertRaises(SandboxUnavailable):
            await ws.measure(empty)
