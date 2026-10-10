"""Operational commands suppress configuration/URL exceptions before connecting."""
from contextlib import redirect_stdout, redirect_stderr
import io
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from agentforge.operations import business_metrics, check


class OperationalErrorTests(unittest.TestCase):
    def assert_private(self, module, argv):
        for failing in ("get_settings", "create_db_engine"):
            with self.subTest(command=module.__name__, failing=failing):
                output = io.StringIO()
                settings = SimpleNamespace(database_url="postgresql://fixture")
                with patch.object(module, "get_settings", return_value=settings), patch.object(module, failing, side_effect=ValueError("secret-bearing-private-value")), patch("sys.argv", argv), redirect_stdout(output), redirect_stderr(output):
                    self.assertEqual(module.main(), 1)
                self.assertNotIn("secret-bearing-private-value", output.getvalue())
                self.assertNotIn("Traceback", output.getvalue())
                self.assertIn("failed", output.getvalue())

    def test_check_configuration_and_engine_errors_are_private(self):
        self.assert_private(check, ["operations.check"])

    def test_export_configuration_and_engine_errors_are_private(self):
        self.assert_private(business_metrics, ["business_metrics", "--output", "/unused/private-output.json"])
