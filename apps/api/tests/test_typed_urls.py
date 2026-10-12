"""A URL the person wrote can be fetched directly; a model-made URL still needs a search; failed fetches point at what is held.

Found with Neatlogs: in every baseline trace the first fetch_webpage call was refused (SCOPE_DENIED) because the page the
person had pasted had not come from a search yet, and a refused or blocked page sent the model into repeated searching.
"""
from types import SimpleNamespace as NS
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from agentforge.repositories.research import MAX_MESSAGE_URLS, message_urls, normalized_url
from agentforge.subagents.resources import DatabaseResourceResolver
from agentforge.subagents.scope import ScopeDenied
from agentforge.tools.catalog import DESCRIPTIONS, ERROR_GUIDANCE, error_guidance


class MessageUrlTests(TestCase):
    def test_addresses_are_found_as_people_write_them(self):
        text = ("Open https://www.npmjs.com/package/react, then (see https://docs.astral.sh/uv/). "
                "Also **https://example.com/a?b=1&c=2** and <https://example.org/x>.")
        self.assertEqual(message_urls(text), {
            "https://www.npmjs.com/package/react", "https://docs.astral.sh/uv/",
            "https://example.com/a?b=1&c=2", "https://example.org/x"})

    def test_a_bracket_the_address_opened_is_kept(self):
        self.assertEqual(message_urls("Read https://en.wikipedia.org/wiki/Mercury_(planet)."),
                         {"https://en.wikipedia.org/wiki/Mercury_(planet)"})

    def test_addresses_are_normalized_like_search_results(self):
        self.assertEqual(message_urls("HTTPS://Example.COM"), {normalized_url("https://example.com")})
        self.assertEqual(normalized_url("https://example.com"), "https://example.com")

    def test_only_web_addresses_count_and_there_is_a_limit(self):
        self.assertEqual(message_urls("ftp://files.example.com/a and javascript:alert(1) and no links"), frozenset())
        self.assertEqual(message_urls(None), frozenset())
        many = " ".join(f"https://site{i}.example.com/" for i in range(MAX_MESSAGE_URLS + 5))
        self.assertEqual(len(message_urls(many)), MAX_MESSAGE_URLS)
        self.assertIsNone(normalized_url("not a url"))


class FakeRepository:
    def __init__(self, goal="", sources=()):
        self.goal, self.sources = goal, list(sources)

    def sources_for_url(self, ctx, url):
        return [s for s in self.sources if s["url"] == url and s["fetch_status"] in ("SEARCH_ONLY", "FETCHED")]

    def list_sources(self, **_):
        return self.sources

    def message_urls(self, ctx):
        return message_urls(self.goal)


def context(discovery=True):
    return NS(task_id=uuid4(), user_id=uuid4(), project_id=uuid4(), source_discovery_enabled=discovery)


def scope(hosts=()):
    return NS(permissions=NS(allowed_web_hosts=hosts))


class ResolverTests(IsolatedAsyncioTestCase):
    def resolver(self, **repository):
        return DatabaseResourceResolver(FakeRepository(**repository), NS(check_context=AsyncMock()))

    async def resolve(self, resolver, url, *, ctx=None, hosts=()):
        public = NS(url=url)
        with patch("agentforge.integrations.safe_fetch.resolve_public_url", AsyncMock(return_value=public)) as resolve:
            result = await resolver.resolve_web_url(ctx or context(), "fetch_webpage", NS(source_url=url), scope(hosts))
        return result, resolve

    async def test_a_url_the_person_wrote_is_fetched_without_a_search_first(self):
        resolver = self.resolver(goal="Open https://www.npmjs.com/package/react and tell me the version.")
        (public, hosts), resolve = await self.resolve(resolver, "https://www.npmjs.com/package/react")
        self.assertEqual(public.url, "https://www.npmjs.com/package/react")
        self.assertEqual(hosts, ("www.npmjs.com",))
        resolve.assert_awaited_once_with("https://www.npmjs.com/package/react", ("www.npmjs.com",))
        await resolver.verify_tool_inputs(context(), "fetch_webpage", NS(source_url="https://www.npmjs.com/package/react"))

    async def test_a_url_the_model_made_up_is_still_refused(self):
        resolver = self.resolver(goal="Open https://www.npmjs.com/package/react please.")
        for url in ("https://www.npmjs.com/package/react?leak=secret", "https://attacker.example/collect",
                    "https://www.npmjs.com/package/vue"):
            with self.subTest(url=url):
                with self.assertRaises(ScopeDenied):
                    await self.resolve(resolver, url)
                with self.assertRaises(ScopeDenied):
                    await resolver.verify_tool_inputs(context(), "fetch_webpage", NS(source_url=url))

    async def test_search_results_still_work_and_both_kinds_of_host_are_trusted(self):
        resolver = self.resolver(goal="Compare with https://docs.astral.sh/uv/",
            sources=[{"url": "https://pypi.org/project/uv/", "fetch_status": "SEARCH_ONLY"}])
        (_, hosts), _ = await self.resolve(resolver, "https://pypi.org/project/uv/")
        self.assertEqual(hosts, ("docs.astral.sh", "pypi.org"))

    async def test_task_host_limits_still_apply_to_a_typed_url(self):
        resolver = self.resolver(goal="Open https://www.npmjs.com/package/react")
        (_, hosts), resolve = await self.resolve(resolver, "https://www.npmjs.com/package/react", hosts=("docs.astral.sh",))
        self.assertEqual(hosts, ())  # the network layer then refuses the host
        (_, hosts), _ = await self.resolve(resolver, "https://www.npmjs.com/package/react", ctx=context(discovery=False))
        self.assertEqual(hosts, ())


class GuidanceTests(TestCase):
    def test_a_failed_fetch_points_at_the_search_results_already_held(self):
        for code in ("FETCH_HTTP_ERROR", "FETCH_CONTENT_TYPE_UNSUPPORTED", "FETCH_COMPRESSED_CONTENT_REJECTED"):
            guidance = error_guidance(code)
            self.assertIn("read_research_source", guidance)
            self.assertIn("Do not search for the same page again", guidance)

    def test_a_repeated_failed_call_has_its_own_guidance(self):
        self.assertIn("REPEATED_FAILED_CALL", ERROR_GUIDANCE)
        self.assertEqual(error_guidance("REPEATED_FAILED_CALL"), ERROR_GUIDANCE["REPEATED_FAILED_CALL"])

    def test_the_fetch_tool_says_a_written_url_can_be_opened(self):
        self.assertIn("A URL the person wrote in their message can be opened directly", DESCRIPTIONS["fetch_webpage"])
