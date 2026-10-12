"""One bounded Exa search (https://exa.ai/docs/reference/search), without automatic upgrades or retries.

Exa is the first-choice web search; `tools/research.py` falls back to Tavily when it is not configured or cannot answer.
Each result carries the page's query-relevant highlights as its snippet and the response reports what the request cost, so the
budget ledger records the real charge instead of an estimate.
"""

import asyncio
from decimal import Decimal, InvalidOperation
import json
import math
from typing import Any

import httpx

from agentforge.integrations.tls import outbound_tls
from agentforge.integrations.web_search import (EXHAUSTED, INVALID, RATE_LIMITED, KeyFailure, SearchFailure, SearchResponse,
                                                UnbilledSearchFailure, retry_after_seconds)

ENDPOINT = "https://api.exa.ai/search"
MAX_RESPONSE_BYTES = 262144
SNIPPET_CHARS = 2000   # per result: enough to judge a lead, small enough that ten results stay cheap to read
# Statuses Exa documents for a request it refused before searching. 401 (missing or invalid key), 402 (out of credit or budget)
# and 403 (the team is blocked) and 429 (rate limit) are about the key, so another key may be tried; 400 is about the request.
KEY_STATUSES = {401: ("SEARCH_PROVIDER_AUTH", INVALID), 403: ("SEARCH_PROVIDER_AUTH", INVALID),
                402: ("SEARCH_CREDIT_EXHAUSTED", EXHAUSTED), 429: ("SEARCH_RATE_LIMITED", RATE_LIMITED)}


class ExaClient:
    def __init__(self, *, api_key: str, max_cost_usd: Decimal, search_type: str = "auto", timeout_sec: float = 20) -> None:
        if not api_key or not max_cost_usd.is_finite() or max_cost_usd <= 0 or timeout_sec <= 0:
            raise ValueError("invalid_search_configuration")
        self.api_key, self.max_cost_usd = api_key, max_cost_usd
        self.search_type, self.timeout_sec = search_type, timeout_sec

    async def search(self, query: str, max_results: int, *, allowed_hosts: tuple[str, ...] = ()) -> SearchResponse:
        payload: dict[str, Any] = {"query": query, "type": self.search_type, "numResults": max_results,
                                   "contents": {"highlights": {"maxCharacters": SNIPPET_CHARS}}}
        if allowed_hosts:
            payload["includeDomains"] = list(allowed_hosts)
        if len(json.dumps(payload).encode()) > 16384:
            raise SearchFailure("SEARCH_REQUEST_TOO_LARGE")
        # Endpoint is constant. Credentials cannot follow redirects or enter ambient proxies/.netrc. A fresh client retains
        # no provider cookies.
        async with asyncio.timeout(self.timeout_sec):
            async with httpx.AsyncClient(trust_env=False, verify=outbound_tls(), follow_redirects=False,
                                         timeout=self.timeout_sec) as client:
                async with client.stream("POST", ENDPOINT, json=payload,
                    headers={"x-api-key": self.api_key, "Accept-Encoding": "identity"}) as response:
                    if response.status_code != 200:
                        if response.status_code in KEY_STATUSES:
                            code, kind = KEY_STATUSES[response.status_code]
                            raise KeyFailure(code, kind, retry_after_seconds(response.headers.get("retry-after")))
                        if response.status_code == 400:
                            raise UnbilledSearchFailure("SEARCH_REQUEST_REJECTED")
                        raise SearchFailure("SEARCH_PROVIDER_ERROR")
                    if response.headers.get("content-encoding", "identity").lower() not in ("", "identity"):
                        raise SearchFailure("SEARCH_COMPRESSED_CONTENT_REJECTED")
                    length = response.headers.get("content-length")
                    if length and (not length.isdigit() or int(length) > MAX_RESPONSE_BYTES):
                        raise SearchFailure("SEARCH_RESPONSE_TOO_LARGE")
                    body = bytearray()
                    async for chunk in response.aiter_raw():
                        body.extend(chunk)
                        if len(body) > MAX_RESPONSE_BYTES:
                            raise SearchFailure("SEARCH_RESPONSE_TOO_LARGE")
        return parse_response(bytes(body), max_results)


def parse_response(body: bytes, max_results: int) -> SearchResponse:
    try:
        data: Any = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        raise SearchFailure("SEARCH_RESPONSE_INVALID") from None
    if not isinstance(data, dict) or not isinstance(data.get("results"), list):
        raise SearchFailure("SEARCH_RESPONSE_INVALID")
    results = []
    for item in data["results"]:
        # A result without a usable address is skipped, not fatal: Exa can return pages whose title or text is missing.
        if not isinstance(item, dict) or not isinstance(item.get("url"), str) or len(item["url"]) > 2048:
            continue
        title = item.get("title") if isinstance(item.get("title"), str) else ""
        results.append({"url": item["url"], "title": title[:1000], "content": snippet(item)})
        if len(results) == max_results:
            break
    return SearchResponse(tuple(results), None, reported_cost(data.get("costDollars")), "exa")


def snippet(item: dict[str, Any]) -> str:
    """The page's highlights joined, else its text, else nothing (the title and address still make it a lead)."""
    highlights = item.get("highlights")
    if isinstance(highlights, list):
        text = " … ".join(part.strip() for part in highlights if isinstance(part, str) and part.strip())
        if text:
            return text[:16000]
    text = item.get("text")
    return text[:16000] if isinstance(text, str) else ""


def reported_cost(cost_dollars: Any) -> Decimal | None:
    """What Exa says the request cost, or None when it does not say (the budget ledger then keeps the configured maximum
    rather than invent a charge). Exa describes `costDollars` as an estimate in dollars."""
    total = cost_dollars.get("total") if isinstance(cost_dollars, dict) else None
    if isinstance(total, bool) or not isinstance(total, (int, float)) or not math.isfinite(total) or not 0 <= total <= 100:
        return None
    try:
        return Decimal(str(total))
    except InvalidOperation:
        return None
