"""Runtime composition: the traced catalog the agent runs with keeps what each registered tool declared."""
from contextlib import contextmanager
from decimal import Decimal
from types import SimpleNamespace as NS
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch
from uuid import uuid4

from agentforge import bootstrap


class FakeDb:
    def scalar(self, *args, **kwargs):
        return None  # not a conversation turn: no conversation tools, skills or plugins


@contextmanager
def sessions():
    yield FakeDb()


class ComposeRuntimeTests(IsolatedAsyncioTestCase):
    async def test_the_traced_catalog_keeps_each_tools_approval_preview(self):
        async def changed_files(arguments, execution):
            return "src/a.py"

        async def handler(arguments, execution):
            return None

        def research(catalog, sessions, settings):
            # The pull-request tool is registered with the preview its approval card lists changed files from.
            catalog.register("create_pull_request", handler, max_cost_usd=Decimal(0), timeout_sec=30, preview=changed_files)
            catalog.register("web_search", handler, max_cost_usd=Decimal("0.01"), timeout_sec=20)
            return None

        store = NS(task=lambda: NS(permissions_json={}, created_by=uuid4()), lease=NS(task_id=uuid4()), progress=lambda *a: None)
        settings = NS(e2b_api_key=None, skills_enabled=False, plugins_enabled=False)
        with patch.object(bootstrap.ModelRouter, "from_settings", lambda settings: bootstrap.ModelRouter(NS(model_id="m"))), \
             patch.object(bootstrap, "register_research", research), \
             patch("agentforge.tools.general.register_general", lambda *a: None):
            runtime = bootstrap.compose_runtime(settings, sessions, NS(enabled=False, span=None), store)
        catalog = runtime.service.catalog
        self.assertIs(catalog.get("create_pull_request").preview, changed_files)
        self.assertIsNone(catalog.get("web_search").preview)
        self.assertEqual((catalog.get("web_search").max_cost_usd, catalog.get("web_search").timeout_sec), (Decimal("0.01"), 20))
        # The handler is the traced one, not the adapter itself.
        self.assertIsNot(catalog.get("create_pull_request").handler, handler)
