"""One bounded Tavily basic search, without automatic upgrades or retries."""

import asyncio
from decimal import Decimal
import json
from typing import Any

import httpx

from agentforge.integrations.tls import outbound_tls
from agentforge.integrations.web_search import (EXHAUSTED, INVALID, RATE_LIMITED, KeyFailure, SearchFailure, SearchResponse,
                                                UnbilledSearchFailure, retry_after_seconds)

# Statuses Tavily documents (https://docs.tavily.com/documentation/api-reference/endpoint/search): 401 missing or invalid key,
# 429 rate limit, 432 the key's or plan's usage limit, 433 the pay-as-you-go limit. These are about the key, so another key may
# be tried; 400 and 422 are about the request. Tavily refuses all of them before searching, so none is charged.
KEY_STATUSES = {401: ("SEARCH_PROVIDER_AUTH", INVALID), 429: ("SEARCH_RATE_LIMITED", RATE_LIMITED),
                432: ("SEARCH_CREDIT_EXHAUSTED", EXHAUSTED), 433: ("SEARCH_CREDIT_EXHAUSTED", EXHAUSTED)}


class TavilyClient:
    def __init__(self, *, api_key: str, max_cost_usd: Decimal,
                 usd_per_credit: Decimal | None = None, timeout_sec: float = 20) -> None:
        if not api_key or not max_cost_usd.is_finite() or max_cost_usd <= 0:
            raise ValueError("invalid_search_configuration")
        if usd_per_credit is not None and (not usd_per_credit.is_finite() or usd_per_credit <= 0 or usd_per_credit > max_cost_usd):
            raise ValueError("search_credit_price_exceeds_reservation")
        self.api_key, self.max_cost_usd = api_key, max_cost_usd
        self.usd_per_credit, self.timeout_sec = usd_per_credit, timeout_sec

    async def search(self, query: str, max_results: int, *, allowed_hosts: tuple[str, ...] = ()) -> SearchResponse:
        payload = {"query": query, "max_results": max_results, "topic": "general",
                   "search_depth": "basic", "auto_parameters": False,
                   "include_answer": False, "include_raw_content": False,
                   "include_images": False, "include_usage": True}
        if allowed_hosts:
            payload["include_domains"] = list(allowed_hosts)
        if len(json.dumps(payload).encode()) > 16384:
            raise SearchFailure("SEARCH_REQUEST_TOO_LARGE")
        # Endpoint is constant. Credentials cannot follow redirects or enter
        # ambient proxies/.netrc. A fresh client retains no provider cookies.
        async with asyncio.timeout(self.timeout_sec):
            async with httpx.AsyncClient(trust_env=False, verify=outbound_tls(), follow_redirects=False,
                                         timeout=self.timeout_sec) as client:
                async with client.stream("POST", "https://api.tavily.com/search", json=payload,
                    headers={"Authorization": f"Bearer {self.api_key}", "Accept-Encoding": "identity"}) as response:
                    if response.status_code != 200:
                        if response.status_code in KEY_STATUSES:
                            code, kind = KEY_STATUSES[response.status_code]
                            raise KeyFailure(code, kind, retry_after_seconds(response.headers.get("retry-after")))
                        if response.status_code in (400, 422):
                            raise UnbilledSearchFailure("SEARCH_REQUEST_REJECTED")
                        raise SearchFailure("SEARCH_PROVIDER_ERROR")
                    if response.headers.get("content-encoding", "identity").lower() not in ("", "identity"):
                        raise SearchFailure("SEARCH_COMPRESSED_CONTENT_REJECTED")
                    length = response.headers.get("content-length")
                    if length and (not length.isdigit() or int(length) > 262144):
                        raise SearchFailure("SEARCH_RESPONSE_TOO_LARGE")
                    body = bytearray()
                    async for chunk in response.aiter_raw():
                        body.extend(chunk)
                        if len(body) > 262144:
                            raise SearchFailure("SEARCH_RESPONSE_TOO_LARGE")
        try:
            data: Any = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            raise SearchFailure("SEARCH_RESPONSE_INVALID") from None
        if not isinstance(data, dict) or not isinstance(data.get("results"), list):
            raise SearchFailure("SEARCH_RESPONSE_INVALID")
        results = []
        for result in data["results"][:max_results]:
            if not isinstance(result, dict) or not all(isinstance(result.get(key), str) for key in ("url", "title", "content")):
                raise SearchFailure("SEARCH_RESPONSE_INVALID")
            if len(result["url"]) > 2048:
                continue
            results.append({"url": result["url"], "title": result["title"][:1000], "content": result["content"][:16000]})
        usage = data.get("usage")
        credits = usage.get("credits") if isinstance(usage, dict) else None
        if type(credits) is not int or credits < 0 or credits > 1_000_000:
            credits = None
        # Missing billing configuration/observed usage stays unknown; the
        # durable budget ledger retains the configured maximum. Never invent $0.
        cost = Decimal(credits) * self.usd_per_credit if credits is not None and self.usd_per_credit is not None else None
        return SearchResponse(tuple(results), credits, cost, "tavily")
