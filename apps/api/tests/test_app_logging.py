"""The API gives the root logger a handler at startup when nothing else has, and never replaces one that is there."""
import logging
from unittest import TestCase

from agentforge.main import configure_logging


class ConfigureLoggingTests(TestCase):
    def setUp(self):
        root = logging.getLogger()
        saved = (root.handlers[:], root.level)
        root.handlers.clear()

        def restore():
            root.handlers[:] = saved[0]
            root.setLevel(saved[1])
        self.addCleanup(restore)

    def test_a_bare_root_logger_gets_one_info_handler(self):
        configure_logging()
        configure_logging()
        root = logging.getLogger()
        self.assertEqual((len(root.handlers), root.level), (1, logging.INFO))
        self.assertIn("%(name)s", root.handlers[0].formatter._fmt)

    def test_an_existing_handler_is_left_alone(self):
        root = logging.getLogger()
        existing = logging.NullHandler()
        root.addHandler(existing)
        root.setLevel(logging.WARNING)
        configure_logging()
        self.assertEqual((root.handlers, root.level), ([existing], logging.WARNING))
