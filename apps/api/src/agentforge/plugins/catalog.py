"""The plugins offered in the Plugins page. A closed list reviewed by us; a person can also connect a server of their own.

Every entry is a remote MCP server reached over Streamable HTTP. Two things say how a person signs in:
  * `oauth`: the server signs people in with OAuth (the MCP authorization specification), through a Client ID Metadata Document or
    Dynamic Client Registration, so no setup is needed on this side. Servers that need an app registered by hand are not listed;
    a person can still connect them as their own server with their own OAuth app.
  * `auth`: what a pasted token does. "none" (nothing), "optional" (a token raises limits or is an alternative to signing in) or
    "required" (a token is the only way in). A token is sent as `Authorization: Bearer <token>`.
Each entry was checked against the live server: it answers 401 with Protected Resource Metadata naming this address (OAuth) and
accepted a real registration of this app, or it lists its tools without signing in (no sign-in), and every address is the one the
vendor documents. `apps/api/examples/plugin_catalog_check.py` repeats that check.
Left out on purpose: servers that only let approved apps register (Vercel, Figma, Intercom, Dropbox refuse unknown return addresses
or are limited to named partners), servers whose sign-in information does not match their address (Semgrep), and servers that need an
app registered by hand (Slack, HubSpot, Box, PagerDuty, Render, Asana). Those can be connected as a server of one's own.
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
    oauth: bool = False


def _oauth(key: str, name: str, description: str, url: str, category: str, docs: str | None = None) -> CatalogPlugin:
    return CatalogPlugin(key, name, description, url, "none", category, docs_url=docs, oauth=True)


def _open(key: str, name: str, description: str, url: str, category: str, docs: str | None = None) -> CatalogPlugin:
    return CatalogPlugin(key, name, description, url, "none", category, docs_url=docs)


CATALOG: tuple[CatalogPlugin, ...] = (
    _oauth("cfo-ai", "CFO.ai", "Read and build financial models, plans and scenarios in your CFO.ai account.",
           "https://api.cfo.ai/mcp", "Business", "https://docs.cfo.ai/integrations/mcp-server"),
    # --- Code ---------------------------------------------------------------------------------------------------------
    CatalogPlugin("github", "GitHub", "Search code, read issues and pull requests, and work with repositories through GitHub's own server.",
        "https://api.githubcopilot.com/mcp/", "required", "Code", "Personal access token",
        "Create a fine-grained personal access token on GitHub and grant only what the agent needs.",
        "https://github.com/github/github-mcp-server"),
    CatalogPlugin("linear", "Linear", "Find, create and update issues, projects and cycles in your Linear workspace.",
        "https://mcp.linear.app/mcp", "optional", "Code", "API key",
        "Optional. Sign in with Linear, or paste a personal API key from Linear under Settings → Security & access.",
        "https://linear.app/docs/mcp", oauth=True),
    _oauth("sentry", "Sentry", "Look into errors, issues and releases, and ask Seer about them.", "https://mcp.sentry.dev/mcp", "Code",
           "https://docs.sentry.io/product/sentry-mcp/"),
    _oauth("gitlab", "GitLab", "Work with projects, issues and merge requests on GitLab.com.", "https://gitlab.com/api/v4/mcp", "Code",
           "https://docs.gitlab.com/user/gitlab_duo/model_context_protocol/mcp_server/"),
    _oauth("buildkite", "Buildkite", "Inspect pipelines, builds and job logs.", "https://mcp.buildkite.com/mcp", "Code", "https://buildkite.com/docs/apis/mcp-server"),
    _open("deepwiki", "DeepWiki", "Ask questions about any public GitHub repository and read its generated documentation.", "https://mcp.deepwiki.com/mcp", "Code",
          "https://docs.devin.ai/work-with-devin/deepwiki-mcp"),
    # --- Docs and knowledge -------------------------------------------------------------------------------------------
    CatalogPlugin("context7", "Context7", "Up-to-date documentation and code examples for libraries and frameworks.",
        "https://mcp.context7.com/mcp", "optional", "Docs", "API key", "Optional. A free key from context7.com raises the rate limit.",
        "https://github.com/upstash/context7"),
    _open("microsoft-learn", "Microsoft Learn", "Search Microsoft's official documentation and code samples.", "https://learn.microsoft.com/api/mcp", "Docs",
          "https://learn.microsoft.com/training/support/mcp"),
    _open("cloudflare-docs", "Cloudflare Docs", "Search Cloudflare's documentation.", "https://docs.mcp.cloudflare.com/mcp", "Docs",
          "https://developers.cloudflare.com/agents/model-context-protocol/mcp-servers-for-cloudflare/"),
    CatalogPlugin("hugging-face", "Hugging Face", "Search models, datasets, Spaces and papers on the Hugging Face Hub.",
        "https://huggingface.co/mcp", "optional", "Docs", "Access token", "Optional. A token from huggingface.co/settings/tokens opens your private repositories.",
        "https://huggingface.co/settings/mcp"),
    # --- Cloud --------------------------------------------------------------------------------------------------------
    _oauth("netlify", "Netlify", "Create and deploy sites, and manage their settings.", "https://netlify-mcp.netlify.app/mcp", "Cloud",
           "https://docs.netlify.com/build/build-with-ai/netlify-mcp-server/"),
    _oauth("railway", "Railway", "Create projects and services, and manage deployments.", "https://mcp.railway.com", "Cloud", "https://docs.railway.com/ai/mcp-server"),
    _oauth("heroku", "Heroku", "Manage apps, dynos, add-ons and releases.", "https://mcp.heroku.com/mcp", "Cloud", "https://devcenter.heroku.com/articles/heroku-mcp-server"),
    _oauth("cloudflare-observability", "Cloudflare Observability", "Query logs and analytics for your Workers.", "https://observability.mcp.cloudflare.com/mcp", "Cloud",
           "https://developers.cloudflare.com/agents/model-context-protocol/mcp-servers-for-cloudflare/"),
    _oauth("clerk", "Clerk", "Look up the Clerk SDK snippets and your instance's users.", "https://mcp.clerk.com/mcp", "Cloud", "https://clerk.com/docs/guides/ai/mcp/clerk-mcp-server"),
    _oauth("workos", "WorkOS", "Manage WorkOS organizations, connections and the docs.", "https://mcp.workos.com/mcp", "Cloud", "https://workos.com/docs/mcp"),
    # --- Data ---------------------------------------------------------------------------------------------------------
    _oauth("supabase", "Supabase", "Design tables, run SQL, manage branches and read project logs.", "https://mcp.supabase.com/mcp", "Data", "https://supabase.com/docs/guides/getting-started/mcp"),
    _oauth("neon", "Neon", "Create Postgres projects and branches, and run SQL and migrations.", "https://mcp.neon.tech/mcp", "Data", "https://neon.com/docs/ai/neon-mcp-server"),
    _oauth("planetscale", "PlanetScale", "Inspect databases and branches, and run read queries.", "https://mcp.pscale.dev/mcp/planetscale", "Data", "https://planetscale.com/docs/connect/mcp"),
    _oauth("mongodb", "MongoDB", "Explore Atlas clusters and query collections.", "https://mcp.mongodb.com", "Data", "https://www.mongodb.com/docs/mcp-server/"),
    _oauth("prisma", "Prisma", "Manage Prisma Postgres databases and migrations.", "https://mcp.prisma.io/mcp", "Data", "https://www.prisma.io/docs/postgres/integrations/mcp-server"),
    # --- Observability and analytics ----------------------------------------------------------------------------------
    _oauth("datadog", "Datadog", "Search logs, metrics, monitors and incidents on the US1 site.", "https://mcp.datadoghq.com/api/unstable/mcp-server/mcp", "Observability",
           "https://docs.datadoghq.com/bits_ai/mcp_server/"),
    _oauth("grafana", "Grafana", "Query dashboards, alerts and data sources.", "https://mcp.grafana.com/mcp", "Observability", "https://github.com/grafana/mcp-grafana"),
    _oauth("posthog", "PostHog", "Query product analytics, feature flags and experiments.", "https://mcp.posthog.com/mcp", "Observability", "https://posthog.com/docs/model-context-protocol"),
    _oauth("mixpanel", "Mixpanel", "Ask questions of your product analytics.", "https://mcp.mixpanel.com/mcp", "Observability", "https://docs.mixpanel.com/docs/mcp"),
    _oauth("amplitude", "Amplitude", "Explore charts, cohorts and experiments.", "https://mcp.amplitude.com/mcp", "Observability", "https://amplitude.com/docs/amplitude-ai/amplitude-mcp"),
    _oauth("axiom", "Axiom", "Query event data and logs with APL.", "https://mcp.axiom.co/mcp", "Observability", "https://axiom.co/docs/console/intelligence/mcp-server"),
    # --- Planning and files -------------------------------------------------------------------------------------------
    _oauth("notion", "Notion", "Search, read and edit pages and databases in your workspace.", "https://mcp.notion.com/mcp", "Planning", "https://developers.notion.com/docs/mcp"),
    _oauth("monday", "monday.com", "Work with boards, items and updates.", "https://mcp.monday.com/mcp", "Planning", "https://developer.monday.com/apps/docs/mondaycom-mcp-integration"),
    _oauth("clickup", "ClickUp", "Find and update tasks, docs and time tracking.", "https://mcp.clickup.com/mcp", "Planning", "https://developer.clickup.com/docs/connect-an-ai-assistant-to-clickups-mcp-server"),
    _oauth("todoist", "Todoist", "Add, find and complete tasks and projects.", "https://ai.todoist.net/mcp", "Planning", "https://developer.todoist.com/api/v1/"),
    _oauth("airtable", "Airtable", "Read and write records in your bases.", "https://mcp.airtable.com/mcp", "Planning", "https://support.airtable.com/docs/using-the-airtable-mcp-server"),
    _oauth("miro", "Miro", "Read boards and create diagrams and frames.", "https://mcp.miro.com", "Planning", "https://developers.miro.com/docs/miro-mcp"),
    # --- Design and content -------------------------------------------------------------------------------------------
    _oauth("canva", "Canva", "Create, review and edit designs.", "https://mcp.canva.com/mcp", "Design", "https://www.canva.dev/docs/connect/canva-mcp-server-setup/"),
    _oauth("webflow", "Webflow", "Manage sites, CMS collections and pages.", "https://mcp.webflow.com/mcp", "Design", "https://developers.webflow.com/data/docs/ai-tools"),
    _oauth("wix", "Wix", "Build and manage Wix sites and apps.", "https://mcp.wix.com/mcp", "Design", "https://dev.wix.com/docs/sdk/articles/use-the-wix-mcp/about-the-wix-mcp"),
    _oauth("cloudinary", "Cloudinary", "Upload, search and transform images and video.", "https://asset-management.mcp.cloudinary.com/mcp", "Design",
           "https://cloudinary.com/documentation/cloudinary_llm_mcp"),
    _oauth("sanity", "Sanity", "Query and edit content in your Sanity projects.", "https://mcp.sanity.io", "Design", "https://www.sanity.io/docs/compute-and-ai/mcp-server"),
    _oauth("contentful", "Contentful", "Manage spaces, entries and content models.", "https://mcp.contentful.com/mcp", "Design", "https://www.contentful.com/developers/docs/tools/mcp-server/"),
    # --- Payments and business ----------------------------------------------------------------------------------------
    _oauth("stripe", "Stripe", "Manage customers, payments and subscriptions, and search Stripe's docs.", "https://mcp.stripe.com", "Business", "https://docs.stripe.com/mcp"),
    _oauth("paypal", "PayPal", "Work with invoices, orders and transactions.", "https://mcp.paypal.com/mcp", "Business", "https://developer.paypal.com/tools/mcp-server/"),
    _oauth("ramp", "Ramp", "Look into spend, cards and bills.", "https://ramp-mcp-remote.ramp.com/mcp", "Business", "https://docs.ramp.com/developer-api/v1/guides/mcp-server"),    # --- Search and automation ----------------------------------------------------------------------------------------
    _open("exa", "Exa", "Search the web and fetch pages with an AI-native search engine.", "https://mcp.exa.ai/mcp", "Search", "https://docs.exa.ai/reference/exa-mcp"),
    _oauth("tavily", "Tavily", "Search, extract and crawl the web for research.", "https://mcp.tavily.com/mcp", "Search", "https://docs.tavily.com/documentation/mcp"),
    _oauth("apify", "Apify", "Run web scrapers and automations from the Apify Store.", "https://mcp.apify.com", "Search", "https://docs.apify.com/platform/integrations/mcp"),
    _oauth("parallel", "Parallel", "Web search and research built for agents.", "https://search-mcp.parallel.ai/mcp", "Search", "https://docs.parallel.ai/integrations/mcp/search-mcp"),
    _oauth("mapbox", "Mapbox", "Geocoding, directions and map data.", "https://mcp.mapbox.com/mcp", "Search", "https://docs.mapbox.com/api/guides/mcp-server/"),
    _oauth("zapier", "Zapier", "Connect the agent to thousands of apps through your Zapier actions.", "https://mcp.zapier.com/api/mcp/mcp", "Search", "https://zapier.com/mcp"),
)

BY_KEY = {plugin.key: plugin for plugin in CATALOG}


def catalog_view() -> list[dict]:
    return [{"key": p.key, "name": p.name, "description": p.description, "server_url": p.server_url, "auth": p.auth, "oauth": p.oauth,
             "category": p.category, "token_label": p.token_label, "token_help": p.token_help, "docs_url": p.docs_url}
            for p in CATALOG]
