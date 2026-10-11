"""The TLS context outbound API clients share.

Building an SSLContext loads a whole CA bundle (tens of milliseconds of CPU), and an httpx client left to its default builds a new
one for every client, on the event loop. The clients that used httpx's default share this one instead: the same trust store
(certifi's bundle, which is what httpx picks for a client with `trust_env=False`), built once per process. A context only reads
its certificates and settings during handshakes, so sharing it across clients and threads is safe.

safe_fetch keeps its own cached system-store context; it never used certifi.
"""
import functools
import ssl

import httpx


@functools.lru_cache(maxsize=1)
def outbound_tls() -> ssl.SSLContext:
    """httpx's own default verifying context for a client that ignores the environment (`trust_env=False`), made once."""
    return httpx.create_ssl_context(trust_env=False)
