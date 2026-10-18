"""Anonymous Parallel Search MCP, using AgentForge's bounded Streamable HTTP client.

Search snippets become the same task-owned sources as other providers. Page fetching remains with SafeWebFetcher.
No ambient Parallel credential, plugin connection or authenticated search override is used here.
"""
import asyncio
import json
from decimal import Decimal

from agentforge.integrations.web_search import SearchFailure, SearchResponse, UnbilledSearchFailure
from agentforge.plugins.mcp import McpClient, McpError

ENDPOINT = "https://search.parallel.ai/mcp"


class ParallelSearchClient:
    max_cost_usd = Decimal(0)

    def __init__(self, *, timeout_sec: float = 20, model_name: str | None = None) -> None:
        self.timeout_sec = timeout_sec
        # Analytics only: use the exact trusted configured identifier, or omit it if it exceeds the MCP limit.
        self.model_name = model_name if isinstance(model_name, str) and 0 < len(model_name) <= 100 else None

    async def search(self, query: str, max_results: int, *, allowed_hosts: tuple[str, ...] = (),
                     session_id: str | None = None) -> SearchResponse:
        arguments = {"objective": query, "search_queries": [query]}
        if allowed_hosts:
            # Anonymous MCP has no source-policy override. Search operators guide retrieval; the research tool still
            # enforces the exact host allowlist on every normalized result before storing it.
            arguments["search_queries"] = [query + " (" + " OR ".join(f"site:{host}" for host in allowed_hosts) + ")"]
        if session_id:
            arguments["session_id"] = session_id
        if self.model_name:
            arguments["model_name"] = self.model_name
        client = McpClient(ENDPOINT, timeout=self.timeout_sec)
        try:
            async with asyncio.timeout(self.timeout_sec):
                try:
                    result = await client.call_tool("web_search", arguments)
                finally:
                    await client.aclose()
        except McpError as exc:
            # Never forward remote bodies, prompts or request details to the tool's error/logging path.
            code = {"PLUGIN_RATE_LIMITED": "SEARCH_RATE_LIMITED", "PLUGIN_TIMEOUT": "SEARCH_TIMEOUT"}.get(
                exc.code, "SEARCH_PROVIDER_ERROR")
            raise UnbilledSearchFailure(code) from None
        except TimeoutError:
            raise UnbilledSearchFailure("SEARCH_TIMEOUT") from None
        except OSError:
            raise UnbilledSearchFailure("SEARCH_PROVIDER_ERROR") from None
        if result["is_error"]:
            raise UnbilledSearchFailure("SEARCH_PROVIDER_ERROR")
        data = result["structured"]
        if data is None:
            try:
                data = json.loads(result["text"])
            except (ValueError, TypeError):
                raise SearchFailure("SEARCH_RESPONSE_INVALID") from None
        if not isinstance(data, dict) or not isinstance(data.get("results"), list):
            raise SearchFailure("SEARCH_RESPONSE_INVALID")
        results = []
        for item in data["results"]:
            if not isinstance(item, dict) or not isinstance(item.get("url"), str) or len(item["url"]) > 2048:
                continue
            excerpts = item.get("excerpts")
            content = " … ".join(part for part in excerpts if isinstance(part, str)) if isinstance(excerpts, list) else ""
            results.append({"url": item["url"], "title": item.get("title", "")[:1000] if isinstance(item.get("title"), str) else "",
                            "content": content[:16000]})
            if len(results) >= max_results:
                break
        # Host restrictions are enforced on normalized URLs by the research tool before any source is saved.
        return SearchResponse(tuple(results), None, Decimal(0), "parallel")
