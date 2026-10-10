"""The Windows development fallback keeps the same rules as the POSIX implementation where it can."""
import hashlib
import os
import tempfile
from pathlib import Path
from unittest import TestCase
from uuid import uuid4

from agentforge.integrations.artifact_storage import ArtifactStorage
from agentforge.subagents.scope import ScopeDenied


class LocalStorageTests(TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.storage = ArtifactStorage(Path(self.directory.name) / "artifacts")
        self.task, self.artifact = uuid4(), uuid4()

    def write(self, content=b"hello"):
        key = f"{self.task}/{self.artifact}.md"
        self.storage._write_local(key, content)
        return key, hashlib.sha256(content).hexdigest()

    def test_a_written_object_reads_back_and_is_never_overwritten(self):
        key, digest = self.write()
        self.assertEqual(self.storage._read_local(key, digest, 2_000_000), b"hello")
        with self.assertRaises(FileExistsError):
            self.write(b"other")

    def test_tampered_or_oversized_content_is_refused(self):
        key, digest = self.write()
        with self.assertRaises(ScopeDenied):
            self.storage._read_local(key, "0" * 64, 2_000_000)
        with self.assertRaises(ScopeDenied):
            self.storage._read_local(key, digest, 2)

    def test_only_server_generated_keys_are_accepted(self):
        for key in ("../x/y.md", f"{self.task}/../{self.artifact}.md", "plain.md"):
            with self.assertRaises(ScopeDenied):
                self.storage._read_local(key, "0" * 64, 10)

    def test_a_link_in_place_of_the_task_directory_is_refused(self):
        key, digest = self.write()
        folder = Path(self.directory.name) / "artifacts" / str(self.task)
        moved = folder.with_name("moved")
        folder.rename(moved)
        try:
            os.symlink(moved, folder, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("this account may not create symbolic links")
        with self.assertRaises(ScopeDenied):
            self.storage._read_local(key, digest, 2_000_000)

    def test_construction_is_inert_on_every_supported_host(self):
        self.assertFalse((Path(self.directory.name) / "never-created").exists())
        ArtifactStorage(Path(self.directory.name) / "never-created")
        self.assertFalse((Path(self.directory.name) / "never-created").exists())
