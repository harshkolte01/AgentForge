# Phase 3 research evidence boundary

The research slice registers Tavily Search when `TAVILY_API_KEY` is configured.
It uses fixed basic search with automatic parameter upgrades disabled, bounded
request/result sizes and timeouts, no automatic retries, and no ambient proxies,
`.netrc` credentials, shared cookies or credential-bearing redirects. Optional
`TAVILY_USD_PER_CREDIT` converts provider-observed credits using the operator's
rate; it is an estimate rather than an invoice amount. Missing usage or rate
retains the configured maximum operation charge in the budget ledger.

Search results are immutable task-owned source snapshots. Fetch accepts an
exact stored URL only. Discovery is enabled by a server-built research task
context, never source text. Explicit nonempty host limits apply to both search
results and fetch. Fetch validates every resolved IPv4/IPv6 address, connects to
one validated numeric address, and verifies TLS using the original hostname.
Every redirect receives fresh URL, host and DNS checks. Redirect destinations
stay within task-owned source hosts and explicit limits. Compressed responses
are rejected; wire bytes, decoded content, redirect count and total time are
bounded. HTML becomes plain text without scripts, styles or embedded content.

The verifier reads actual structured claims from specialized durable child run
history. Each claim records missing, unfetched, uncertain, unsupported or verbatim
quote-supported citations. Ownership establishes access only. Quote support
establishes occurrence in the stored fetched snapshot, not semantic truth,
source reliability, contradiction resolution or fulfillment of the user's goal.
Broad acceptance criteria and missing goal coverage remain unresolved. Phase 3
therefore returns **PARTIAL**, including when all individual quotations match.
A later semantic verification implementation is needed before labeling broad
research objectives complete. Search snippets never become fetched evidence.

Sources, retrieval evidence and report references are append-only PostgreSQL
records protected by immutable-row triggers. Markdown reports escape external
text and include claims, citation checks, source IDs, URLs, retrieval timestamps,
hashes and explicit limitations. Public API reads enforce current task, project
and owner ownership. Download paths never come from a user or model argument.

The local artifact backend currently requires **POSIX** `dir_fd`, `O_DIRECTORY`
and `O_NOFOLLOW` support. The configured root (default `.local/artifacts`) and
its task directories must have mode `0700`; files use mode `0600`. Storage walks
ancestor directory file descriptors, rejects symlinks, creates server-generated
UUID paths exclusively, fsyncs writes and verifies hashes on reads. Windows
artifact storage requires a separate supported backend; it fails with a clear
configuration error rather than weakening path checks. Keep the private storage
volume with the database so report references remain downloadable after restart.

No execution, migration, import, build, browser or live-provider qualification
was performed for this implementation under the user's verification boundary.

Official references consulted:

- [Tavily Search API](https://docs.tavily.com/documentation/api-reference/endpoint/search)
- [Tavily credits and pricing](https://docs.tavily.com/documentation/api-credits)
- [HTTPX transports](https://www.python-httpx.org/advanced/transports/)
- [HTTPX environment variables](https://www.python-httpx.org/environment_variables/)
- [HTTPCore SNI hostname extension](https://www.encode.io/httpcore/extensions/#sni_hostname)
- [OWASP SSRF prevention](https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html)
