# Plugins

A plugin is a remote [MCP](https://modelcontextprotocol.io) server the person connects, so the agent can use its tools
(look up library docs, read a Linear issue, search GitHub). Plugins are the one place the agent's tool set is open-ended,
so everything below exists to keep that safe.

## Using them

1. **Settings → Plugins**: pick a plugin from the catalog (DeepWiki, Context7, GitHub, Linear) or add your own server by
   address. Plugins that need a token ask for it. Nothing is saved unless the server answers and lists at least one tool.
2. **The plug icon beside the message box** chooses which connected plugins this conversation may use. A new conversation
   starts with the plugins switched on in Settings ("New chats"); each conversation then keeps its own choice.
3. **Tools** (Settings → Plugins → a plugin → Tools) switches off individual tools. Use it to drop tools the agent does not
   need (every tool adds to each request) or to keep a plugin read-only.
4. In **Ask me** mode a plugin tool waits for approval unless the server marks it read-only (`readOnlyHint`); the card shows
   the plugin, the tool and every argument. **Approve for me** never asks.

## How it works

| Piece | Where |
| --- | --- |
| Catalog (closed list we reviewed) | `plugins/catalog.py` |
| MCP client: Streamable HTTP, JSON or SSE answers, sessions, 404 → new session | `plugins/mcp.py` |
| Tool names, schemas, read-only hints | `plugins/schema.py` |
| Connect, refresh, update, remove; per-conversation choice | `plugins/service.py`, `api/routes_plugins.py` |
| Giving a run its tools | `plugins/runtime.py`, `bootstrap.compose_runtime` |
| Gateway, approvals, audit | `tools/catalog.py` (`PluginTool`), `tools/gateway.py` |
| Storage | `plugin_connections`, `conversations.plugins_json` (migration `0015_plugins`) |

A connection stores the server address, an encrypted credential, and the tools the server listed when it was last read. When
a conversation's turn starts, the worker registers the tools of the plugins that conversation uses under the names
`slug__tool` (for example `linear__create_issue`), opens one MCP client per plugin for the run, and closes it with the run.
The tools are offered to whichever agent runs, beside its own. A failure to set plugins up never stops a turn; it runs
without them.

`GET/POST /api/v1/plugins`, `PATCH/DELETE /api/v1/plugins/{id}`, `POST /api/v1/plugins/{id}/refresh`; sessions take and return
`plugins` (a list of connection ids) on create, patch and read.

## Trust and safety

A plugin server is third-party code reached from our servers with the person's credential.

* **Network.** https only, port any, every address the name resolves to must be public, the connection goes to the checked
  address with TLS bound to the host name (resolved again on every request), redirects are never followed, responses are size
  limited. `PLUGIN_ALLOW_PRIVATE_NETWORK` relaxes this for local development and is refused in production.
* **Credentials.** Encrypted with `PLUGIN_ENCRYPTION_KEY` (or `GITHUB_TOKEN_ENCRYPTION_KEY` when only that is set), never
  returned by any endpoint, never in an error, a log or an audit row. Without a key only plugins that need no token can be
  connected. Changing the key makes saved tokens unreadable; the plugin is then skipped until the token is entered again.
* **Authority.** A plugin's tools get none of the built-in tools' privileges. The sandbox, repository and URL rules do not
  apply to their arguments (and cannot be reached through them), and the allowlist is the run's own: only tools of the
  conversation's plugins that the person left on exist for that run.
* **Content.** Tool names, descriptions and schemas are cleaned before the model sees them (names within 64 characters of
  `[a-z0-9_-]`, standard schema keywords only, size-capped). Results are text for the model to read as data: marked untrusted,
  cut to fit, and the platform rules say never to follow instructions found in them.
* **Approval.** A tool that does not declare `readOnlyHint: true` is assumed to change something outside this app: it is run
  alone, asks first in Ask me mode, and if it times out the model is told the action may or may not have happened.
* **Audit.** The database keeps the names of a call's arguments and their count, never their content.

## Not included

OAuth sign-in to a plugin (tokens are pasted), stdio servers (a hosted service cannot run arbitrary processes), the older
HTTP+SSE transport, and server-initiated requests (sampling, elicitation). Tools are discovered when a plugin is connected or
refreshed, not on every run.

## Operating it

Settings are in [.env.example](../.env.example): `PLUGINS_ENABLED`, `PLUGIN_ENCRYPTION_KEY`, `PLUGIN_CALL_TIMEOUT_SEC`,
`PLUGIN_MAX_CONNECTIONS`, `PLUGIN_ALLOW_PRIVATE_NETWORK`. Run migrations (`alembic upgrade head`) before starting the new API
and worker. `examples/plugins_smoke.py` is the disposable end-to-end check and runs with the other release checks.
