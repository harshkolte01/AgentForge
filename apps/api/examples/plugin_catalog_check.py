"""Checks every plugin in the catalog against its live server. Needs network access; changes nothing; exits 1 when an entry fails.

    uv run python examples/plugin_catalog_check.py            # all entries
    uv run python examples/plugin_catalog_check.py linear     # some entries
    PLUGIN_CHECK_BASE_URL=https://app.example.com uv run python examples/plugin_catalog_check.py --register
        # also registers this app (Dynamic Client Registration) with each server that has no metadata-document support, to see that
        # the server accepts it. Each registration is a real, harmless client at the vendor, with that base URL's return address.

What is checked, per entry:
  * a plugin that signs in with OAuth: the server answers 401 with Protected Resource Metadata that names its own address, its
    authorization server publishes valid metadata with PKCE (S256), and this app can register with it (a Client ID Metadata Document
    or Dynamic Client Registration);
  * a plugin that needs no sign-in: it initializes and lists tools;
  * a plugin that takes a token: it turns a request without one away (401/403);
  * the documentation link answers.
"""
import asyncio
import os
import sys

import httpx

from agentforge.plugins.catalog import CATALOG, CatalogPlugin
from agentforge.plugins.mcp import McpClient, McpError
from agentforge.plugins.oauth import OAuthError, discover, register

BASE = os.environ.get("PLUGIN_CHECK_BASE_URL", "https://example.invalid")


class Fake:
    """Just enough settings for the checks."""
    plugin_oauth_registration = "dcr"
    api_public_base_url = BASE
    app_base_url = BASE


async def check(entry: CatalogPlugin, web: httpx.AsyncClient, register_app: bool = False) -> tuple[bool, str]:
    notes: list[str] = []
    ok = True
    try:
        if entry.oauth:
            found = await discover(entry.server_url, allow_private=False)
            how = "metadata document" if found.metadata_documents else "dynamic registration" if found.registration_endpoint else None
            if how is None:
                ok = False
                notes.append("cannot register this app (no metadata documents, no dynamic registration)")
            else:
                notes.append(f"oauth ok ({how}, scope={found.scope or '-'})")
                if register_app and found.registration_endpoint:
                    client = await register(found, Fake, allow_private=False)
                    notes.append(f"registration accepted (auth={client.method})")
        elif entry.auth in ("none", "optional"):
            client = McpClient(entry.server_url, timeout=30)
            try:
                tools = await client.list_tools()
            finally:
                await client.aclose()
            notes.append(f"open ok ({len(tools)} tools)")
        else:
            client = McpClient(entry.server_url, timeout=30)
            try:
                await client.list_tools()
                ok = False
                notes.append("answers without a token, but is listed as needing one")
            except McpError as error:
                if error.code == "PLUGIN_AUTH_FAILED":
                    notes.append("token required (401/403) ok")
                else:
                    ok = False
                    notes.append(f"unexpected: {error.code}")
            finally:
                await client.aclose()
    except OAuthError as error:
        ok = False
        notes.append(f"{error.code}: {error.message}")
    except McpError as error:
        ok = False
        notes.append(f"{error.code}: {error.message}")
    if entry.docs_url:
        try:
            reply = await web.get(entry.docs_url, headers={"User-Agent": "Mozilla/5.0 agentforge-catalog-check"})
            if reply.status_code >= 400:
                ok = False
                notes.append(f"docs link answers {reply.status_code}")
        except httpx.HTTPError as error:
            ok = False
            notes.append(f"docs link failed ({type(error).__name__})")
    return ok, "; ".join(notes)


async def main(args: list[str]) -> int:
    register_app = "--register" in args
    keys = [item for item in args if not item.startswith("--")]
    entries = [entry for entry in CATALOG if not keys or entry.key in keys]
    failed = 0
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as web:
        gate = asyncio.Semaphore(8)

        async def one(entry: CatalogPlugin) -> tuple[CatalogPlugin, bool, str]:
            async with gate:
                ok, note = await check(entry, web, register_app)
                return entry, ok, note

        for entry, ok, note in await asyncio.gather(*(one(entry) for entry in entries)):
            failed += not ok
            print(f"{'ok  ' if ok else 'FAIL'} {entry.key:26} {note}")
    print(f"\n{len(entries) - failed} of {len(entries)} entries ok")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
