# Plugins

A plugin is a remote [MCP](https://modelcontextprotocol.io) server the person connects, so the agent can use its tools
(look up library docs, read a Linear issue, search GitHub). Plugins are the one place the agent's tool set is open-ended,
so everything below exists to keep that safe.

## Using them

1. **The Plugins page** (sidebar → Plugins; there is no copy in Settings, and the old `/settings?section=plugins` link redirects here) is a quiet list: **Connected** plugins, then
   **Available** ones (about fifty, grouped by category, plus "Your own server"), each with its logo. Search and the category
   pills filter the list; **Add** (top right) or a row's **+** connects one. A plugin that signs in with OAuth sends the person to
   the service to sign in; one that takes a token asks for it in a dialog. Nothing is saved unless the server answers and lists
   at least one tool.
2. **The switch** on a connected plugin chooses whether new conversations start with it. A conversation keeps the plugins it
   started with. The message box has no plugin picker.
3. **Opening a connected plugin** shows its tools, a Refresh, its token and Remove. Each tool can be switched off there. Use it to drop tools the agent does not
   need (every tool adds to each request) or to keep a plugin read-only.
4. In **Ask me** mode a plugin tool waits for approval unless the server marks it read-only (`readOnlyHint`); the card shows
   the plugin, the tool and every argument. **Approve for me** never asks.

## How it works

| Piece | Where |
| --- | --- |
| Catalog (closed list we reviewed) | `plugins/catalog.py` |
| MCP client: Streamable HTTP, JSON or SSE answers, sessions, 404 → new session | `plugins/mcp.py` |
| OAuth: discovery, registration, PKCE, tokens | `plugins/oauth.py` |
| OAuth sign-in flow, callback binding, token renewal, revocation | `plugins/oauth_service.py`, `api/routes_plugins.py` (`/oauth/*`) |
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

## Signing in with OAuth

Most hosted MCP servers sign people in with OAuth instead of a pasted token. This app is the client, following the
[MCP authorization specification](https://modelcontextprotocol.io/specification/latest/basic/authorization) (OAuth 2.1):

1. **Discovery.** An unauthenticated request is answered 401 with a `WWW-Authenticate` challenge; the server's Protected Resource
   Metadata (RFC 9728, from the challenge or the well-known address, with or without the path) names its authorization server,
   whose metadata (RFC 8414, then OpenID Connect Discovery, in the specification's order) gives the endpoints. The metadata must
   name the server's own address and the issuer it was found by; the authorization server must support PKCE `S256`. A server
   that publishes anything else is not used.
2. **Registration**, by the specification's order: an OAuth app the person registered themselves (Advanced, for "Your own
   server"), a **Client ID Metadata Document** when the server supports one (served at `/api/v1/plugins/oauth/client.json`, https
   only), else **Dynamic Client Registration** (kept per issuer and return address, and renewed before it expires). Servers that
   do neither are not in the catalog; they can still be connected with the person's own OAuth app.
3. **Authorization.** The browser goes to the server's authorization page with PKCE, the RFC 8707 `resource`, the scopes the
   server asks for (plus `offline_access` where offered, so the agent keeps working), and a `state`. The way back is
   `/api/v1/plugins/oauth/callback`, which is bound three ways: the `state` (stored hashed, single use, ten minutes), a cookie
   set when the sign-in started (stored hashed) and the signed-in person. The issuer in the response (RFC 9207) is checked before
   the code is used. A sign-in that fails for any reason is spent. The callback always redirects back to `/plugins?oauth=…`.
4. **Tokens** are exchanged with the verifier, encrypted (`plugin_oauth_grants`), sent only to that server, and never returned
   by any endpoint. An access token is renewed before it expires, once when the server refuses it, and never twice at once for
   one connection (a refresh token that rotates would be spent by the first). A sign-in the server has withdrawn, or one that
   needs more access (`insufficient_scope`), marks the connection **Sign in again**; the agent is told to ask the person to
   reconnect, and Reconnect asks again for the earlier scopes plus the new ones.
5. **Removing** a connection asks the authorization server to revoke its tokens (RFC 7009), after the answer is sent.

Every address fetched along the way comes from a remote document, so each is held to the network rules below. A network that
gives IPv4-only hosts NAT64 addresses (`64:ff9b::/96`) is handled by judging the address inside them.

**The catalog** (`plugins/catalog.py`) lists servers that were checked live: each answers 401 with metadata naming its own
address, and accepted a real registration of this app (or lists tools with no sign-in). `examples/plugin_catalog_check.py` repeats
that (add `--register` to register for real). Servers that only let approved apps register (Vercel, Figma, Intercom, Dropbox),
that publish metadata that does not match their address (Semgrep), or that need an app registered by hand (Slack, HubSpot, Box,
PagerDuty, Render, Asana) are left out. **Logos** are the marks their owners publish (Simple Icons, LobeHub, or the vendor's own
site), fetched by `apps/web/scripts/plugin-logos.mjs` into `public/plugins/`; they are trademarks of their owners and are shown only
to name the service. A test fails when a catalog plugin has no logo.

## Not included

stdio servers (a hosted service cannot run arbitrary processes), the older
HTTP+SSE transport, and server-initiated requests (sampling, elicitation). Tools are discovered when a plugin is connected or
refreshed, not on every run.

## Operating it

Settings are in the [configuration reference](configuration.md#plugins-and-skills): `PLUGINS_ENABLED`, `PLUGIN_ENCRYPTION_KEY`, `PLUGIN_CALL_TIMEOUT_SEC`,
`PLUGIN_MAX_CONNECTIONS`, `PLUGIN_ALLOW_PRIVATE_NETWORK`, `PLUGIN_OAUTH_ENABLED`, `PLUGIN_OAUTH_REGISTRATION`. Signing in needs
the encryption key and an `API_PUBLIC_BASE_URL` the person's browser can reach (the return address is built from it; a
registration made for another address is made again). Run migrations (`alembic upgrade head`, which includes
`0019_plugin_oauth`) before starting the new API and worker. `examples/plugins_smoke.py` and `examples/plugin_oauth_smoke.py` are
the disposable end-to-end checks and run with the other release checks.
