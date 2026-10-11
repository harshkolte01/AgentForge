"""Listing a user's repositories: pages and installations are read together, and the list is kept briefly per user.

Offline: GitHub is an httpx.MockTransport.
"""
import asyncio
import unittest
from urllib.parse import parse_qs

import httpx
from pydantic import SecretStr

from agentforge.api.repository_cache import RepositoryListCache
from agentforge.integrations.github_app import LISTING_CONCURRENCY, GitHubAppClient
from agentforge.integrations.github_client import GitHubError
from agentforge.settings import Settings


def settings():
    return Settings(_env_file=None, github_app_id=123, github_client_id="client", github_client_secret=SecretStr("fixture"))


def repository(ident):
    return {"id": ident, "full_name": f"owner/repo-{ident}", "private": False, "default_branch": "main", "permissions": {"pull": True}}


class FakeGitHub:
    """`/user/installations` and each installation's repositories, served in pages of 100."""

    def __init__(self, counts, *, report_total=True, delay=0.0):
        self.counts, self.report_total, self.delay = counts, report_total, delay
        self.requests, self.open, self.peak = [], 0, 0

    async def __call__(self, request):
        path, query = request.url.path, parse_qs(request.url.query.decode())
        page = int(query["page"][0])
        self.requests.append((path, page))
        self.open += 1
        self.peak = max(self.peak, self.open)
        await asyncio.sleep(self.delay)
        self.open -= 1
        if path == "/user/installations":
            return httpx.Response(200, json={"total_count": len(self.counts),
                "installations": [{"id": 100 + index, "app_id": 123} for index in range(len(self.counts))]})
        index = int(path.split("/")[3]) - 100
        total = self.counts[index]
        items = [repository(index * 10_000 + number + 1) for number in range(total)][(page - 1) * 100:page * 100]
        body = {"repositories": items}
        if self.report_total:
            body["total_count"] = total
        return httpx.Response(200, json=body)

    def client(self):
        return GitHubAppClient(settings(), token="ghu_x", transport=httpx.MockTransport(self))


class ListingTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_repositories_arrive_in_installation_and_page_order(self):
        github = FakeGitHub([250, 3, 100])
        pairs = await github.client().repositories()
        self.assertEqual(len(pairs), 353)
        self.assertEqual([repo["id"] for _, repo in pairs],
            [*range(1, 251), *(10_001 + n for n in range(3)), *(20_001 + n for n in range(100))])
        self.assertEqual([install["id"] for install, _ in pairs], [100] * 250 + [101] * 3 + [102] * 100)

    async def test_pages_after_the_first_are_requested_from_the_reported_total(self):
        github = FakeGitHub([450], delay=0.01)
        await github.client().repositories()
        pages = sorted(page for path, page in github.requests if path.endswith("/repositories"))
        self.assertEqual(pages, [1, 2, 3, 4, 5])   # no probe past the last page
        self.assertGreater(github.peak, 1)

    async def test_a_list_without_a_total_is_read_page_by_page(self):
        github = FakeGitHub([250], report_total=False)
        pairs = await github.client().repositories()
        self.assertEqual(len(pairs), 250)
        self.assertEqual([page for path, page in github.requests if path.endswith("/repositories")], [1, 2, 3])

    async def test_an_exact_multiple_of_a_page_ends_on_the_empty_page(self):
        github = FakeGitHub([200], report_total=False)
        self.assertEqual(len(await github.client().repositories()), 200)

    async def test_a_total_that_reads_short_is_followed_by_the_pages_that_remain(self):
        github = FakeGitHub([250])
        original = github.__call__

        async def understated(request):
            response = await original(request)
            if request.url.path.endswith("/repositories") and request.url.params["page"] == "1":
                body = {**response.json(), "total_count": 150}
                return httpx.Response(200, json=body)
            return response
        client = GitHubAppClient(settings(), token="ghu_x", transport=httpx.MockTransport(understated))
        self.assertEqual(len(await client.repositories()), 250)

    async def test_a_listing_past_the_page_limit_is_refused_before_every_page_is_read(self):
        github = FakeGitHub([1200])
        with self.assertRaisesRegex(GitHubError, "discovery_limit"):
            await github.client().repositories()
        self.assertEqual([page for path, page in github.requests if path.endswith("/repositories")], [1])

    async def test_more_than_a_thousand_repositories_across_installations_are_refused(self):
        with self.assertRaisesRegex(GitHubError, "discovery_limit"):
            await FakeGitHub([600, 600]).client().repositories()

    async def test_a_page_that_is_not_a_list_is_an_invalid_response(self):
        def respond(request):
            if request.url.path == "/user/installations":
                return httpx.Response(200, json={"installations": [{"id": 100, "app_id": 123}]})
            return httpx.Response(200, json={"repositories": "nope"})
        client = GitHubAppClient(settings(), token="ghu_x", transport=httpx.MockTransport(respond))
        with self.assertRaisesRegex(GitHubError, "response_invalid"):
            await client.repositories()

    async def test_the_first_failure_stops_the_other_requests(self):
        started, cancelled = [], []

        async def respond(request):
            if request.url.path == "/user/installations":
                return httpx.Response(200, json={"installations": [{"id": 100 + n, "app_id": 123} for n in range(3)]})
            started.append(request.url.path)
            if request.url.path.split("/")[3] == "100":
                return httpx.Response(403)
            try:
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                cancelled.append(request.url.path)
                raise
            return httpx.Response(200, json={"repositories": []})
        client = GitHubAppClient(settings(), token="ghu_x", transport=httpx.MockTransport(respond))
        with self.assertRaisesRegex(GitHubError, "access_denied"):
            await asyncio.wait_for(client.repositories(), 3)
        self.assertEqual(len(started), 3)
        self.assertEqual(len(cancelled), 2)

    async def test_requests_in_flight_stay_within_the_listing_limit(self):
        github = FakeGitHub([250] * 4, delay=0.01)
        await github.client().repositories()
        self.assertLessEqual(github.peak, LISTING_CONCURRENCY)
        self.assertGreater(github.peak, 1)


class RepositoryListCacheTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now, self.reads = 0.0, 0
        self.cache = RepositoryListCache(ttl=30, capacity=2, clock=lambda: self.now)

    def loader(self, value="list", *, gate=None, fail=None):
        async def load():
            self.reads += 1
            if gate is not None:
                await gate.wait()
            if fail is not None:
                raise fail
            return value
        return load

    async def test_a_list_is_kept_until_it_is_older_than_the_ttl(self):
        self.assertEqual(await self.cache.get("a", self.loader("one")), "one")
        self.now = 29
        self.assertEqual(await self.cache.get("a", self.loader("two")), "one")
        self.assertEqual(self.reads, 1)
        self.now = 31
        self.assertEqual(await self.cache.get("a", self.loader("two")), "two")
        self.assertEqual(self.reads, 2)

    async def test_each_user_has_their_own_list(self):
        await self.cache.get("a", self.loader("mine"))
        self.assertEqual(await self.cache.get("b", self.loader("yours")), "yours")

    async def test_concurrent_askers_share_one_read(self):
        gate = asyncio.Event()
        askers = [asyncio.create_task(self.cache.get("a", self.loader(gate=gate))) for _ in range(5)]
        await asyncio.sleep(0)
        gate.set()
        self.assertEqual(await asyncio.gather(*askers), ["list"] * 5)
        self.assertEqual(self.reads, 1)

    async def test_a_failure_reaches_every_waiter_and_is_not_kept(self):
        gate = asyncio.Event()
        failing = self.loader(gate=gate, fail=GitHubError("github_unavailable"))
        askers = [asyncio.create_task(self.cache.get("a", failing)) for _ in range(3)]
        await asyncio.sleep(0)
        gate.set()
        for result in await asyncio.gather(*askers, return_exceptions=True):
            self.assertIsInstance(result, GitHubError)
        self.assertEqual(self.reads, 1)
        self.assertEqual(await self.cache.get("a", self.loader("recovered")), "recovered")

    async def test_refresh_always_reads_anew(self):
        await self.cache.get("a", self.loader("one"))
        self.assertEqual(await self.cache.get("a", self.loader("two"), refresh=True), "two")
        self.assertEqual(await self.cache.get("a", self.loader("three")), "two")
        self.assertEqual(self.reads, 2)

    async def test_an_asker_that_gives_up_does_not_cancel_the_read_for_the_others(self):
        gate = asyncio.Event()
        impatient = asyncio.create_task(self.cache.get("a", self.loader(gate=gate)))
        patient = asyncio.create_task(self.cache.get("a", self.loader(gate=gate)))
        await asyncio.sleep(0)
        impatient.cancel()
        await asyncio.sleep(0)
        gate.set()
        self.assertEqual(await patient, "list")
        self.assertEqual(self.reads, 1)

    async def test_clear_drops_lists_and_a_read_that_began_before_it_is_not_kept(self):
        await self.cache.get("a", self.loader("old"))
        gate = asyncio.Event()
        slow = asyncio.create_task(self.cache.get("b", self.loader("stale", gate=gate)))
        await asyncio.sleep(0)
        self.cache.clear()
        gate.set()
        self.assertEqual(await slow, "stale")          # its own asker still gets what it read
        self.assertEqual(await self.cache.get("a", self.loader("new")), "new")
        self.assertEqual(await self.cache.get("b", self.loader("fresh")), "fresh")

    async def test_the_oldest_lists_are_dropped_past_the_capacity(self):
        for key in "abc":
            await self.cache.get(key, self.loader(key))
        self.reads = 0
        await self.cache.get("c", self.loader())
        await self.cache.get("b", self.loader())
        self.assertEqual(self.reads, 0)
        await self.cache.get("a", self.loader())
        self.assertEqual(self.reads, 1)


if __name__ == "__main__":
    unittest.main()
