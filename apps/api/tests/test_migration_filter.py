"""Framework exclusions never hide application or unknown schema objects."""
import unittest

from agentforge.database.migration_filter import CHECKPOINT_TABLES, include_autogenerate_object


class MigrationFilterTests(unittest.TestCase):
    def test_only_unmanaged_reflected_framework_tables_are_excluded(self):
        for name in CHECKPOINT_TABLES:
            with self.subTest(table=name):
                self.assertFalse(include_autogenerate_object(None, name, "table", True, None))
                self.assertTrue(include_autogenerate_object(None, name, "table", False, None))
                self.assertTrue(include_autogenerate_object(None, name, "table", True, object()))

    def test_unknown_application_and_non_table_objects_remain_visible(self):
        for name in ("tasks", "unknown_extra", "checkpoint_custom_table"):
            self.assertTrue(include_autogenerate_object(None, name, "table", True, None))
        self.assertTrue(include_autogenerate_object(None, "checkpoints", "index", True, None))
