"""The plugins offered in Settings. A closed list reviewed by us; a person can also connect a server of their own.

Every entry is a remote MCP server reached over Streamable HTTP. `auth` says what the person must give:
"none" (nothing), "optional" (a token raises limits but is not needed) or "required" (a token is needed). A token is sent
as `Authorization: Bearer <token>`.
"""
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class CatalogPlugin:
    key: str
    name: str
    description: str
    server_url: str
    auth: Literal["none", "optional", "required"]
    category: str
    # Where a person gets the token, and what it should be allowed to do.
    token_label: str = "Access token"
    token_help: str = ""
    docs_url: str | None = None


CATALOG: tuple[CatalogPlugin, ...] = (
    CatalogPlugin("deepwiki", "DeepWiki", "Ask questions about any public GitHub repository and read its generated documentation.",
        "https://mcp.deepwiki.com/mcp", "none", "Code", docs_url="https://docs.devin.ai/work-with-devin/deepwiki-mcp"),
    CatalogPlugin("context7", "Context7", "Up-to-date documentation and code examples for libraries and frameworks.",
        "https://mcp.context7.com/mcp", "optional", "Docs", "API key", "Optional. A free key from context7.com raises the rate limit.",
        "https://github.com/upstash/context7"),
    CatalogPlugin("github", "GitHub", "Search code, read issues and pull requests, and work with repositories through GitHub's own server.",
        "https://api.githubcopilot.com/mcp/", "required", "Code", "Personal access token",
        "Create a fine-grained personal access token on GitHub and grant only what the agent needs.",
        "https://github.com/github/github-mcp-server"),
    CatalogPlugin("linear", "Linear", "Find, create and update issues, projects and cycles in your Linear workspace.",
        "https://mcp.linear.app/mcp", "required", "Planning", "API key",
        "Create a personal API key in Linear under Settings → Security & access.", "https://linear.app/docs/mcp"),
)

BY_KEY = {plugin.key: plugin for plugin in CATALOG}


def catalog_view() -> list[dict]:
    return [{"key": p.key, "name": p.name, "description": p.description, "server_url": p.server_url, "auth": p.auth,
             "category": p.category, "token_label": p.token_label, "token_help": p.token_help, "docs_url": p.docs_url}
            for p in CATALOG]
