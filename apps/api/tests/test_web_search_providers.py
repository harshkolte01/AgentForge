"""Web search: Exa first, Tavily as the fallback; what each provider is sent, how its answer is read, and what a search costs."""
import json
from decimal import Decimal
from types import SimpleNamespace as NS
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import patch

import httpx
from pydantic import SecretStr

from agentforge.integrations import exa_client
from agentforge.integrations.exa_client import ExaClient, parse_response, reported_cost
from agentforge.integrations.web_search import KeyFailure, SearchFailure, SearchResponse, UnbilledSearchFailure
from agentforge.settings import Settings
from agentforge.tools import research


def exa_body(results, cost=None):
    return json.dumps({"requestId": "r", "results": results, **({"costDollars": cost} if cost is not None else {})}).encode()


def reply(status=200, content=b"", headers=None, **body):
    """A response whose body is a stream, as one from the network is (a plain `content=` would be read before the client streams it)."""
    data = json.dumps(body["json"]).encode() if "json" in body else content
    return httpx.Response(status, headers=headers, stream=httpx.ByteStream(data))


def serving(handler):
    """`httpx.AsyncClient` as the Exa client builds it, answered by `handler` instead of the network."""
    real = httpx.AsyncClient
    return patch.object(exa_client.httpx, "AsyncClient", lambda **kw: real(**{**kw, "transport": httpx.MockTransport(handler)}))


class ParseTests(TestCase):
    def test_highlights_become_the_snippet_and_the_reported_cost_is_kept(self):
        response = parse_response(exa_body([{"url": "https://a.test/x", "title": "A", "highlights": ["one", " two ", ""], "text": "ignored"}],
                                           {"total": 0.007}), 5)
        self.assertEqual(response.results, ({"url": "https://a.test/x", "title": "A", "content": "one … two"},))
        self.assertEqual((response.provider, response.credits, response.cost_usd), ("exa", None, Decimal("0.007")))

    def test_text_is_the_snippet_when_there_are_no_highlights_and_a_bare_lead_is_still_a_lead(self):
        results = parse_response(exa_body([{"url": "https://a.test/1", "title": "T", "text": "page text"},
                                           {"url": "https://a.test/2", "title": None}]), 5).results
        self.assertEqual([r["content"] for r in results], ["page text", ""])
        self.assertEqual(results[1]["title"], "")

    def test_unusable_results_are_skipped_and_the_count_is_bounded(self):
        raw = [{"title": "no url"}, {"url": 5}, {"url": "https://a.test/" + "x" * 2100}, "junk"] + [{"url": f"https://a.test/{i}"} for i in range(6)]
        self.assertEqual([r["url"] for r in parse_response(exa_body(raw), 3).results], ["https://a.test/0", "https://a.test/1", "https://a.test/2"])

    def test_snippets_are_capped(self):
        self.assertEqual(len(parse_response(exa_body([{"url": "https://a.test/", "text": "x" * 40000}]), 1).results[0]["content"]), 16000)

    def test_an_answer_that_is_not_a_result_list_is_invalid(self):
        for body in (b"not json", b"[]", b'{"results": "x"}', b'{"error": "boom"}'):
            with self.subTest(body=body), self.assertRaises(SearchFailure) as caught:
                parse_response(body, 5)
            self.assertEqual(caught.exception.args[0], "SEARCH_RESPONSE_INVALID")

    def test_a_cost_is_only_trusted_when_it_is_a_sane_number(self):
        self.assertEqual(reported_cost({"total": 0.0123}), Decimal("0.0123"))
        self.assertEqual(reported_cost({"total": 0}), Decimal("0"))
        for bad in (None, {}, {"total": None}, {"total": "0.01"}, {"total": True}, {"total": -1}, {"total": float("nan")}, {"total": float("inf")}, {"total": 1e9}, "x"):
            self.assertIsNone(reported_cost(bad), bad)


class RequestTests(IsolatedAsyncioTestCase):
    async def test_the_request_is_sent_to_the_fixed_endpoint_with_the_key_in_a_header_and_highlights_asked_for(self):
        seen = []
        def handler(request):
            seen.append(request)
            return reply(200, exa_body([{"url": "https://a.test/", "title": "A", "highlights": ["h"]}], {"total": 0.007}))
        client = ExaClient(api_key="secret-key", max_cost_usd=Decimal("0.03"), search_type="fast")
        with serving(handler):
            response = await client.search("latest model", 4, allowed_hosts=("a.test",))
        request = seen[0]
        self.assertEqual((request.method, str(request.url)), ("POST", "https://api.exa.ai/search"))
        self.assertEqual((request.headers["x-api-key"], request.headers["accept-encoding"]), ("secret-key", "identity"))
        self.assertNotIn("authorization", request.headers)
        self.assertEqual(json.loads(request.content), {"query": "latest model", "type": "fast", "numResults": 4, "includeDomains": ["a.test"],
                                                       "contents": {"highlights": {"maxCharacters": 2000}}})
        self.assertEqual(response.results[0]["content"], "h")

    async def test_no_host_restriction_means_no_includeDomains(self):
        seen = []
        def handler(request):
            seen.append(json.loads(request.content))
            return reply(200, exa_body([]))
        with serving(handler):
            await ExaClient(api_key="k", max_cost_usd=Decimal("0.03")).search("q", 5)
        self.assertNotIn("includeDomains", seen[0])
        self.assertEqual(seen[0]["type"], "auto")

    async def test_a_refusal_before_any_work_is_unbilled_and_anything_else_may_have_cost_something(self):
        client = ExaClient(api_key="k", max_cost_usd=Decimal("0.03"))
        expected = {401: "SEARCH_PROVIDER_AUTH", 402: "SEARCH_CREDIT_EXHAUSTED", 403: "SEARCH_PROVIDER_AUTH", 429: "SEARCH_RATE_LIMITED"}
        for status, code in expected.items():
            with self.subTest(status=status), serving(lambda request, s=status: reply(s, json={"error": "private provider text"})):
                with self.assertRaises(KeyFailure) as caught:
                    await client.search("q", 3)
                self.assertEqual(caught.exception.args[0], code)
                self.assertNotIn("private", str(caught.exception))
        with serving(lambda request: reply(400, json={"error": "bad"})):
            with self.assertRaises(UnbilledSearchFailure) as caught:
                await client.search("q", 3)
        self.assertNotIsInstance(caught.exception, KeyFailure)   # about the request, not the key: another key would fare no better
        for status in (500, 503, 504):
            with self.subTest(status=status), serving(lambda request, s=status: reply(s)):
                with self.assertRaises(SearchFailure) as caught:
                    await client.search("q", 3)
                self.assertNotIsInstance(caught.exception, UnbilledSearchFailure)
                self.assertEqual(caught.exception.args[0], "SEARCH_PROVIDER_ERROR")

    async def test_compressed_and_oversized_answers_are_refused(self):
        client = ExaClient(api_key="k", max_cost_usd=Decimal("0.03"))
        with serving(lambda request: reply(200, b"{}", {"content-encoding": "gzip"})):
            with self.assertRaises(SearchFailure) as caught:
                await client.search("q", 3)
        self.assertEqual(caught.exception.args[0], "SEARCH_COMPRESSED_CONTENT_REJECTED")
        with serving(lambda request: reply(200, b"x" * 300000)):
            with self.assertRaises(SearchFailure) as caught:
                await client.search("q", 3)
        self.assertEqual(caught.exception.args[0], "SEARCH_RESPONSE_TOO_LARGE")

    def test_the_client_refuses_a_configuration_that_cannot_be_budgeted(self):
        for options in ({"api_key": ""}, {"max_cost_usd": Decimal(0)}, {"max_cost_usd": Decimal("NaN")}, {"timeout_sec": 0}):
            with self.assertRaises(ValueError):
                ExaClient(**{"api_key": "k", "max_cost_usd": Decimal("0.03"), **options})


class ProviderOrderTests(TestCase):
    def settings(self, **keys):
        return Settings(_env_file=None, **{k: SecretStr(v) for k, v in keys.items()})

    def test_exa_is_first_and_tavily_the_fallback_with_each_given_a_share_of_the_tool_timeout(self):
        providers = research.search_providers(self.settings(exa_api_key="exa-key-0001", tavily_api_key="tavily-key-0001"))
        self.assertEqual([name for name, _ in providers], ["exa", "tavily"])
        self.assertEqual([client.timeout_sec for _, client in providers], [research.FIRST_PROVIDER_SEC, research.FALLBACK_PROVIDER_SEC])
        self.assertLess(sum(client.timeout_sec for _, client in providers), research.SEARCH_TOOL_TIMEOUT_SEC)

    def test_a_single_key_serves_every_search_with_nearly_all_the_time(self):
        for keys, name in (({"exa_api_key": "exa-key-0001"}, "exa"), ({"tavily_api_key": "tavily-key-0001"}, "tavily")):
            providers = research.search_providers(self.settings(**keys))
            self.assertEqual([n for n, _ in providers], [name])
            self.assertEqual(providers[0][1].timeout_sec, research.SOLE_PROVIDER_SEC)
        self.assertEqual(research.search_providers(self.settings()), [])

    def test_the_defaults_budget_a_search_and_the_key_never_shows_in_the_settings(self):
        settings = self.settings(exa_api_key="exa-private-value")
        self.assertEqual((settings.exa_search_max_cost_usd, settings.exa_search_type), (Decimal("0.03"), "auto"))
        self.assertNotIn("exa-private-value", repr(settings))


class FakeCatalog:
    def __init__(self):
        self.tools = {}

    def register(self, name, handler, **options):
        self.tools[name] = (handler, options)


class FakeRepository:
    def __init__(self, _sessionmaker):
        self.added = []

    def add_source(self, task, **fields):
        self.added.append(fields)
        return {"id": f"s{len(self.added)}", **fields}


class StubProvider:
    def __init__(self, outcome, max_cost="0.03"):
        self.outcome, self.max_cost_usd, self.calls = outcome, Decimal(max_cost), []

    async def search(self, query, max_results, *, allowed_hosts=()):
        self.calls.append((query, max_results, allowed_hosts))
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


def answer(provider, cost, urls=("https://a.test/1",)):
    return SearchResponse(tuple({"url": u, "title": "T", "content": "snippet"} for u in urls), None, cost, provider)


class FailoverTests(IsolatedAsyncioTestCase):
    def register(self, providers):
        catalog = FakeCatalog()
        with patch.object(research, "search_providers", return_value=providers), patch.object(research, "ResearchRepository", FakeRepository), \
                patch.object(research, "DatabaseResourceResolver"), patch.object(research, "AuthorizationService"), patch.object(research, "SafeWebFetcher"):
            research.register_research(catalog, None, NS(research_fetch_timeout_sec=15, research_max_response_bytes=1000))
        return catalog

    def execution(self, hosts=()):
        return NS(scope=NS(permissions=NS(allowed_web_hosts=hosts)), task=NS(source_discovery_enabled=True), agent_run_id="run",
                  operation_key="op", authorize_current=None)

    async def run_search(self, providers, hosts=()):
        catalog = self.register(providers)
        handler, options = catalog.tools["web_search"]
        return await handler(NS(query="latest model", max_results=3), self.execution(hosts)), options

    async def test_without_any_provider_there_is_no_search_tool(self):
        self.assertNotIn("web_search", self.register([]).tools)
        self.assertIn("fetch_webpage", self.register([]).tools)

    async def test_exa_answers_and_tavily_is_never_called(self):
        exa, tavily = StubProvider(answer("exa", Decimal("0.007"))), StubProvider(answer("tavily", Decimal("0.008")), "0.02")
        outcome, options = await self.run_search([("exa", exa), ("tavily", tavily)])
        self.assertEqual((exa.calls, tavily.calls), ([("latest model", 3, ())], []))
        self.assertEqual((outcome.status, outcome.cost_usd, outcome.data["provider"], outcome.data["billing"]), ("ok", Decimal("0.007"), "exa", "provider_reported"))
        self.assertEqual(len(outcome.data["sources"]), 1)

    async def test_a_refusal_from_exa_falls_back_to_tavily_and_only_tavilys_charge_is_booked(self):
        for failure in (UnbilledSearchFailure("SEARCH_PROVIDER_AUTH"), UnbilledSearchFailure("SEARCH_RATE_LIMITED")):
            exa, tavily = StubProvider(failure), StubProvider(answer("tavily", Decimal("0.008")), "0.02")
            with self.assertLogs(research.logger, "WARNING") as logged:
                outcome, _ = await self.run_search([("exa", exa), ("tavily", tavily)])
            self.assertEqual((outcome.status, outcome.cost_usd, outcome.data["provider"]), ("ok", Decimal("0.008"), "tavily"))
            self.assertIn(f"provider=exa reason={failure.args[0]}", logged.output[0])

    async def test_a_failure_that_may_have_been_billed_keeps_the_cap_even_when_tavily_answers(self):
        for failure in (SearchFailure("SEARCH_PROVIDER_ERROR"), TimeoutError(), httpx.ConnectError("down")):
            exa, tavily = StubProvider(failure), StubProvider(answer("tavily", Decimal("0.008")), "0.02")
            with self.assertLogs(research.logger, "WARNING"):
                outcome, _ = await self.run_search([("exa", exa), ("tavily", tavily)])
            self.assertEqual((outcome.status, outcome.cost_usd, outcome.data["billing"]), ("ok", None, "unknown_cap_retained"), failure)

    async def test_the_tool_is_budgeted_for_every_provider_it_may_try(self):
        _, options = await self.run_search([("exa", StubProvider(answer("exa", None))), ("tavily", StubProvider(answer("tavily", None), "0.02"))])
        self.assertEqual((options["max_cost_usd"], options["timeout_sec"]), (Decimal("0.05"), research.SEARCH_TOOL_TIMEOUT_SEC))
        _, options = await self.run_search([("tavily", StubProvider(answer("tavily", None), "0.02"))])
        self.assertEqual(options["max_cost_usd"], Decimal("0.02"))

    async def test_when_every_provider_fails_the_error_is_stable_and_the_charge_known_only_when_nothing_ran(self):
        refused = [("exa", StubProvider(UnbilledSearchFailure("SEARCH_PROVIDER_AUTH"))), ("tavily", StubProvider(UnbilledSearchFailure("SEARCH_RATE_LIMITED"), "0.02"))]
        with self.assertLogs(research.logger, "WARNING"):
            outcome, _ = await self.run_search(refused)
        self.assertEqual((outcome.status, outcome.error_code, outcome.cost_usd), ("error", "SEARCH_PROVIDER_ERROR", Decimal(0)))
        unclear = [("exa", StubProvider(UnbilledSearchFailure("SEARCH_PROVIDER_AUTH"))), ("tavily", StubProvider(SearchFailure("SEARCH_PROVIDER_ERROR"), "0.02"))]
        with self.assertLogs(research.logger, "WARNING"):
            outcome, _ = await self.run_search(unclear)
        self.assertEqual((outcome.error_code, outcome.cost_usd), ("SEARCH_PROVIDER_ERROR", None))

    async def test_a_lone_tavily_behaves_as_it_always_did(self):
        outcome, _ = await self.run_search([("tavily", StubProvider(answer("tavily", None), "0.02"))])
        self.assertEqual((outcome.status, outcome.cost_usd, outcome.data["billing"]), ("ok", None, "unknown_cap_retained"))
        outcome, _ = await self.run_search([("tavily", StubProvider(answer("tavily", Decimal("0.008")), "0.02"))])
        self.assertEqual((outcome.cost_usd, outcome.data["billing"]), (Decimal("0.008"), "configured_credit_conversion"))
        with self.assertLogs(research.logger, "WARNING"):
            outcome, _ = await self.run_search([("tavily", StubProvider(SearchFailure("SEARCH_PROVIDER_ERROR"), "0.02"))])
        self.assertEqual((outcome.status, outcome.cost_usd), ("error", None))

    async def test_results_outside_the_allowed_hosts_are_dropped_whichever_provider_found_them(self):
        exa = StubProvider(answer("exa", Decimal("0.007"), ("https://docs.a.test/x", "https://elsewhere.test/y")))
        outcome, _ = await self.run_search([("exa", exa)], hosts=("docs.a.test",))
        self.assertEqual([s["url"] for s in outcome.data["sources"]], ["https://docs.a.test/x"])
        self.assertEqual(exa.calls[0][2], ("docs.a.test",))


class ReportingTests(TestCase):
    """Whether web search is on, as the configuration route and the doctor report it."""

    def configuration(self, **keys):
        from agentforge.api.routes_configuration import SEARCH_SETTING, configuration
        fields = dict(llm_model="test", llm_api_key="private-model-secret", llm_context_window_tokens=10000, llm_input_usd_per_million=1,
                      llm_output_usd_per_million=1, llm_supports_tools=True, llm_supports_json=True, neatlogs_api_key=None, e2b_api_key=None,
                      exa_api_key=None, tavily_api_key=None, e2b_template_id=None, e2b_template_qualified=False)
        settings = NS(**{**fields, **keys})
        return SEARCH_SETTING, configuration(NS(app=NS(state=NS(settings=settings))), NS(github_user_id=1))

    def test_either_key_turns_web_research_on_and_neither_leaves_chat_working(self):
        for keys, on in (({"exa_api_key": "exa-key-0001"}, True), ({"tavily_api_key": "tavily-key-0001"}, True), ({"exa_api_key": "exa-key-0001", "tavily_api_key": "tavily-key-0001"}, True), ({}, False)):
            with self.subTest(keys=keys):
                setting, result = self.configuration(**keys)
                self.assertEqual(result["web_research_configured"], on)
                self.assertEqual(result["research_configured"], on)
                self.assertEqual("web_research" in result["general_capabilities"], on)
                self.assertEqual(setting in result["missing_settings"], not on)
                self.assertTrue(result["general_configured"])
                self.assertNotIn("private-", str(result))

    def test_the_doctor_names_the_first_choice_and_the_fallback(self):
        from agentforge import doctor
        def rows(**keys):
            return {name: (level, text) for level, name, text in doctor.check_optional(Settings(_env_file=None, **{k: SecretStr(v) for k, v in keys.items()}))
                    if name in ("exa", "tavily")}
        both = rows(exa_api_key="exa-key-0001", tavily_api_key="tavily-key-0001")
        self.assertEqual((both["exa"][0], both["tavily"][0]), ("PASS", "PASS"))
        self.assertIn("fallback", both["tavily"][1])
        self.assertEqual(rows(exa_api_key="exa-key-0001")["tavily"][0], "WARN")
        self.assertIn("falls back to Tavily", rows(tavily_api_key="tavily-key-0001")["exa"][1])
        neither = rows()
        self.assertEqual((neither["exa"][0], neither["tavily"][0]), ("WARN", "WARN"))
