"""Several keys for one search provider: how they are read, which refusals move on to the next key, how long a refused key rests,
and that nothing about a key (only its position) is ever logged."""
import json
import logging
from decimal import Decimal
from unittest import IsolatedAsyncioTestCase, TestCase

import httpx
from pydantic import SecretStr

from agentforge.integrations import exa_client, tavily_client, web_search
from agentforge.integrations.exa_client import ExaClient
from agentforge.integrations.tavily_client import TavilyClient
from agentforge.integrations.web_search import (EXHAUSTED, INVALID, MAX_KEYS, RATE_LIMITED, KeyFailure, KeyPool, PooledClient, SearchFailure,
                                                SearchResponse, UnbilledSearchFailure, pool_for, retry_after_seconds, split_keys)
from agentforge.settings import Settings
from agentforge.telemetry import neatlogs_setup

K1, K2, K3 = "exa-key-AAAA1111", "exa-key-BBBB2222", "exa-key-CCCC3333"


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class ReadingKeysTests(TestCase):
    def test_commas_spaces_semicolons_and_new_lines_separate_keys_and_blanks_and_repeats_go(self):
        self.assertEqual(split_keys(f" {K1} , {K2},,{K1};\n{K3}\n"), (K1, K2, K3))
        self.assertEqual(split_keys(K1), (K1,))
        self.assertEqual(split_keys("  , "), ())

    def test_a_key_that_cannot_be_sent_is_refused_without_being_echoed(self):
        for bad in ("abc12", "key-with-ünicode-1234", 'quote"d-key-12345', "x" * 600):
            with self.subTest(bad=bad[:12]), self.assertRaises(ValueError) as caught:
                split_keys(f"{K1},{bad}")
            self.assertNotIn(bad, str(caught.exception))
        with self.assertRaises(ValueError):
            split_keys(",".join(f"key-number-{i:04d}" for i in range(MAX_KEYS + 1)))
        self.assertEqual(len(split_keys(",".join(f"key-number-{i:04d}" for i in range(MAX_KEYS)))), MAX_KEYS)

    def test_settings_keep_a_clean_list_and_refuse_a_bad_one_at_start(self):
        settings = Settings(_env_file=None, exa_api_key=SecretStr(f" {K1} ,{K2},{K1}"), tavily_api_key=SecretStr("tavily-key-0001"))
        self.assertEqual(settings.exa_api_key.get_secret_value(), f"{K1},{K2}")
        self.assertEqual(Settings(_env_file=None, exa_api_key=SecretStr(" , ")).exa_api_key, None)
        with self.assertRaises(ValueError) as caught:
            Settings(_env_file=None, tavily_api_key=SecretStr(f"{K1},nope"))
        self.assertNotIn(K1, str(caught.exception))
        self.assertNotIn(K1, repr(settings))

    def test_the_environment_variable_takes_the_comma_separated_list(self):
        import os
        from unittest.mock import patch
        with patch.dict(os.environ, {"EXA_API_KEY": f"{K1}, {K2}", "TAVILY_API_KEY": ""}):
            self.assertEqual(split_keys(Settings(_env_file=None).exa_api_key.get_secret_value()), (K1, K2))

    def test_every_key_is_masked_on_its_own_in_traces_as_well_as_the_whole_setting(self):
        values = neatlogs_setup.configured_secrets(Settings(_env_file=None, exa_api_key=SecretStr(f"{K1},{K2}")))
        self.assertTrue({K1, K2, f"{K1},{K2}"} <= set(values))

    def test_retry_after_is_read_in_seconds_only(self):
        self.assertEqual((retry_after_seconds("12"), retry_after_seconds("0.5")), (12.0, 0.5))
        for value in (None, "", "soon", "-3", "Wed, 21 Oct 2026 07:28:00 GMT"):
            self.assertIsNone(retry_after_seconds(value))


class KeyPoolTests(TestCase):
    def pool(self, count=3):
        self.clock = Clock()
        return KeyPool("exa", (K1, K2, K3)[:count], self.clock)

    def test_every_key_is_usable_in_the_order_given_until_one_is_refused(self):
        pool = self.pool()
        self.assertEqual(pool.usable(), [0, 1, 2])
        with self.assertLogs(web_search.logger, "WARNING"):
            pool.failed(0, KeyFailure("SEARCH_PROVIDER_AUTH", INVALID))
        self.assertEqual(pool.usable(), [1, 2])

    def test_each_kind_of_refusal_rests_for_its_own_time_and_then_the_key_is_first_again(self):
        pool = self.pool()
        with self.assertLogs(web_search.logger, "WARNING"):
            pool.failed(0, KeyFailure("a", INVALID))
            pool.failed(1, KeyFailure("b", EXHAUSTED))
            pool.failed(2, KeyFailure("c", RATE_LIMITED))
        self.assertEqual(pool.usable(), [])
        self.clock.now += web_search.RATE_LIMIT_DEFAULT_SEC + 1
        self.assertEqual(pool.usable(), [2])
        self.clock.now += web_search.COOLDOWN_SEC[EXHAUSTED]
        self.assertEqual(pool.usable(), [1, 2])
        self.clock.now += web_search.COOLDOWN_SEC[INVALID]
        self.assertEqual(pool.usable(), [0, 1, 2])

    def test_a_providers_retry_after_decides_a_rate_limit_within_bounds(self):
        pool = self.pool(1)
        for given, rested in ((90, 90), (0, web_search.RATE_LIMIT_MIN_SEC), (99999, web_search.RATE_LIMIT_MAX_SEC)):
            self.clock.now = 5000.0
            with self.assertLogs(web_search.logger, "WARNING"):
                pool.failed(0, KeyFailure("x", RATE_LIMITED, given))
            self.clock.now += rested - 0.1
            self.assertEqual(pool.usable(), [], given)
            self.clock.now += 0.2
            self.assertEqual(pool.usable(), [0], given)

    def test_a_key_that_works_again_is_cleared_and_said_to_be(self):
        pool = self.pool(1)
        with self.assertLogs(web_search.logger, "WARNING"):
            pool.failed(0, KeyFailure("x", EXHAUSTED))
        with self.assertLogs(web_search.logger, "INFO") as logged:
            pool.worked(0)
        self.assertIn("usable again", logged.output[0])
        self.assertEqual(pool.usable(), [0])

    def test_pools_are_shared_for_the_same_keys_and_new_for_different_ones(self):
        self.assertIs(pool_for("exa", (K1, K2)), pool_for("exa", (K1, K2)))
        self.assertIsNot(pool_for("exa", (K1, K2)), pool_for("exa", (K1,)))
        self.assertIsNot(pool_for("exa", (K1,)), pool_for("tavily", (K1,)))


class Scripted:
    """A client per key whose answer is scripted by that key."""

    def __init__(self, script, cost="0.007"):
        self.script, self.cost, self.used = script, Decimal(cost), []

    def build(self, key):
        outer = self

        class One:
            async def search(self, query, max_results, *, allowed_hosts=()):
                outer.used.append(key)
                outcome = outer.script[key]
                if isinstance(outcome, BaseException):
                    raise outcome
                return SearchResponse((), None, outer.cost, "exa")
        return One()


def pooled(script, keys=(K1, K2, K3), timeout=5):
    clock = Clock()
    scripted = Scripted(script)
    return PooledClient(KeyPool("exa", keys, clock), scripted.build, max_cost_usd=Decimal("0.03"), timeout_sec=timeout), scripted, clock


class RotationTests(IsolatedAsyncioTestCase):
    async def test_the_first_key_serves_every_search_while_it_works(self):
        client, scripted, _ = pooled({K1: "ok", K2: "ok", K3: "ok"})
        for _ in range(3):
            await client.search("q", 3)
        self.assertEqual(scripted.used, [K1, K1, K1])

    async def test_an_expired_key_hands_the_search_to_the_next_and_is_not_tried_again(self):
        client, scripted, clock = pooled({K1: KeyFailure("SEARCH_PROVIDER_AUTH", INVALID), K2: "ok", K3: "ok"})
        with self.assertLogs(web_search.logger, "WARNING"):
            first = await client.search("q", 3)
        self.assertEqual((first.cost_usd, scripted.used), (Decimal("0.007"), [K1, K2]))
        await client.search("q", 3)   # the refused key rests: no wasted request
        self.assertEqual(scripted.used, [K1, K2, K2])

    async def test_a_key_out_of_credit_and_a_rate_limited_key_are_both_passed_over(self):
        client, scripted, _ = pooled({K1: KeyFailure("SEARCH_CREDIT_EXHAUSTED", EXHAUSTED), K2: KeyFailure("SEARCH_RATE_LIMITED", RATE_LIMITED, 20), K3: "ok"})
        with self.assertLogs(web_search.logger, "WARNING"):
            await client.search("q", 3)
        self.assertEqual(scripted.used, [K1, K2, K3])

    async def test_a_rested_key_gets_its_place_back_when_its_time_is_up(self):
        client, scripted, clock = pooled({K1: KeyFailure("SEARCH_RATE_LIMITED", RATE_LIMITED, 10), K2: "ok", K3: "ok"})
        with self.assertLogs(web_search.logger, "WARNING"):
            await client.search("q", 3)
        scripted.script[K1] = "ok"
        clock.now += 11
        with self.assertLogs(web_search.logger, "INFO"):
            await client.search("q", 3)
        self.assertEqual(scripted.used, [K1, K2, K1])

    async def test_when_every_key_is_refused_the_failure_is_unbilled_and_names_the_last_reason(self):
        client, scripted, _ = pooled({K1: KeyFailure("SEARCH_PROVIDER_AUTH", INVALID), K2: KeyFailure("SEARCH_CREDIT_EXHAUSTED", EXHAUSTED), K3: KeyFailure("SEARCH_RATE_LIMITED", RATE_LIMITED)})
        with self.assertLogs(web_search.logger, "WARNING"), self.assertRaises(UnbilledSearchFailure) as caught:
            await client.search("q", 3)
        self.assertEqual(caught.exception.args[0], "SEARCH_RATE_LIMITED")
        with self.assertRaises(UnbilledSearchFailure) as caught:   # all resting: nothing is sent at all
            await client.search("q", 3)
        self.assertEqual((caught.exception.args[0], scripted.used), ("SEARCH_KEYS_UNAVAILABLE", [K1, K2, K3]))

    async def test_trouble_that_is_not_about_the_key_does_not_try_the_other_keys(self):
        for trouble in (SearchFailure("SEARCH_PROVIDER_ERROR"), UnbilledSearchFailure("SEARCH_REQUEST_REJECTED"), TimeoutError(), httpx.ConnectError("down")):
            client, scripted, _ = pooled({K1: trouble, K2: "ok", K3: "ok"})
            with self.assertRaises(type(trouble)):
                await client.search("q", 3)
            self.assertEqual(scripted.used, [K1], trouble)
            self.assertEqual(client.pool.usable(), [0, 1, 2])   # and no key is blamed for it

    async def test_all_the_keys_together_get_only_the_providers_share_of_the_time(self):
        import asyncio

        class Slow:
            async def search(self, *args, **kwargs):
                await asyncio.sleep(5)

        client = PooledClient(KeyPool("exa", (K1, K2), Clock()), lambda key: Slow(), max_cost_usd=Decimal("0.03"), timeout_sec=0.05)
        with self.assertRaises(TimeoutError):
            await client.search("q", 3)

    async def test_nothing_about_a_key_but_its_position_is_logged(self):
        client, _, _ = pooled({K1: KeyFailure("SEARCH_PROVIDER_AUTH", INVALID), K2: "ok", K3: "ok"})
        with self.assertLogs(web_search.logger, "INFO") as logged:
            await client.search("q", 3)
        text = "\n".join(logged.output)
        self.assertIn("key=1/3", text)
        for secret in (K1, K2, K3, "AAAA", "BBBB"):
            self.assertNotIn(secret, text)


def serving(module, handler):
    from unittest.mock import patch
    real = httpx.AsyncClient
    return patch.object(module.httpx, "AsyncClient", lambda **kw: real(**{**kw, "transport": httpx.MockTransport(handler)}))


def reply(status, body=None, headers=None):
    return httpx.Response(status, headers=headers, stream=httpx.ByteStream(json.dumps(body if body is not None else {"results": []}).encode()))


class ProviderStatusTests(IsolatedAsyncioTestCase):
    async def test_exa_and_tavily_each_name_the_refusals_that_are_about_the_key(self):
        cases = ((exa_client, ExaClient(api_key=K1, max_cost_usd=Decimal("0.03")), {401: INVALID, 402: EXHAUSTED, 403: INVALID, 429: RATE_LIMITED}),
                 (tavily_client, TavilyClient(api_key=K1, max_cost_usd=Decimal("0.02")), {401: INVALID, 429: RATE_LIMITED, 432: EXHAUSTED, 433: EXHAUSTED}))
        for module, client, statuses in cases:
            for status, kind in statuses.items():
                with self.subTest(provider=module.__name__, status=status), serving(module, lambda request, s=status: reply(s, {"error": "private"})):
                    with self.assertRaises(KeyFailure) as caught:
                        await client.search("q", 3)
                    self.assertEqual(caught.exception.kind, kind)
                    self.assertNotIn("private", str(caught.exception))

    async def test_tavily_rejected_requests_are_unbilled_and_its_other_failures_may_have_cost_something(self):
        client = TavilyClient(api_key=K1, max_cost_usd=Decimal("0.02"))
        for status in (400, 422):
            with serving(tavily_client, lambda request, s=status: reply(s)), self.assertRaises(UnbilledSearchFailure) as caught:
                await client.search("q", 3)
            self.assertNotIsInstance(caught.exception, KeyFailure)
        with serving(tavily_client, lambda request: reply(500)), self.assertRaises(SearchFailure) as caught:
            await client.search("q", 3)
        self.assertNotIsInstance(caught.exception, UnbilledSearchFailure)

    async def test_a_providers_retry_after_is_passed_on(self):
        client = ExaClient(api_key=K1, max_cost_usd=Decimal("0.03"))
        with serving(exa_client, lambda request: reply(429, headers={"retry-after": "45"})), self.assertRaises(KeyFailure) as caught:
            await client.search("q", 3)
        self.assertEqual(caught.exception.retry_after, 45.0)

    async def test_each_key_goes_out_in_its_own_request_header(self):
        seen = []
        def handler(request):
            seen.append(request.headers["x-api-key"])
            return reply(401, {}) if request.headers["x-api-key"] == K1 else reply(200, {"results": [{"url": "https://a.test/", "title": "A"}], "costDollars": {"total": 0.007}})
        pool = KeyPool("exa", (K1, K2), Clock())
        client = PooledClient(pool, lambda key: ExaClient(api_key=key, max_cost_usd=Decimal("0.03")), max_cost_usd=Decimal("0.03"), timeout_sec=5)
        with serving(exa_client, handler), self.assertLogs(web_search.logger, "WARNING"):
            response = await client.search("q", 3)
        self.assertEqual((seen, response.cost_usd, response.results[0]["url"]), ([K1, K2], Decimal("0.007"), "https://a.test/"))
