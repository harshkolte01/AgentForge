"""HTTPS retrieval with all-address validation, pinned DNS and redirect checks.

HTTPX sends only to the validated numeric IP. httpcore's sni_hostname extension
keeps TLS certificate verification bound to the original hostname. No shared
client, ambient proxy, cookies, netrc, Authorization or Referer follows redirects.
"""

import asyncio
from dataclasses import dataclass
from html.parser import HTMLParser
import socket
import ssl
from urllib.parse import urljoin, urlsplit

import httpx

from agentforge.security.policies import PublicURL, validate_public_url, validate_public_url_syntax
from agentforge.subagents.scope import ScopeDenied


class FetchFailure(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


async def resolve_public_url(url: str, allowed_hosts: tuple[str, ...]) -> PublicURL:
    parsed = validate_public_url_syntax(url, allowed_hosts)
    loop = asyncio.get_running_loop()
    # AF_UNSPEC + no AI_ADDRCONFIG: validate every returned A/AAAA destination,
    # including an unreachable private answer, before choosing any connection.
    answers = await loop.getaddrinfo(parsed.hostname, 443, family=socket.AF_UNSPEC,
                                    type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
    addresses = tuple(sorted({answer[4][0] for answer in answers}))
    return validate_public_url(url, allowed_hosts, resolved_addresses=addresses)


class _PlainHTML(HTMLParser):
    _blocked = frozenset({"script", "style", "noscript", "template", "svg", "math", "iframe", "object", "embed", "head"})
    _breaks = frozenset({"p", "div", "br", "li", "h1", "h2", "h3", "h4", "tr", "section", "article"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocked: list[str] = []
        self.parts: list[str] = []
        self.title_parts: list[str] = []
        self.in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "title":
            self.in_title = True
        if tag in self._blocked:
            self.blocked.append(tag)
        if not self.blocked and tag in self._breaks:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self.in_title = False
        if self.blocked and tag in self.blocked:
            # Browser recovery for malformed markup must never expose script
            # text to the report. Over-removal is preferable to active markup.
            index = len(self.blocked) - 1 - self.blocked[::-1].index(tag)
            del self.blocked[index:]
        if not self.blocked and tag in self._breaks:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title_parts.append(data)
        if not self.blocked:
            self.parts.append(data)


def sanitize_text(content: str, *, html: bool = True) -> tuple[str, str]:
    if html:
        parser = _PlainHTML()
        parser.feed(content)
        parser.close()
        title = " ".join("".join(parser.title_parts).split())[:1000]
        content = "".join(parser.parts)
    else:
        title = ""
    # Plain text only: the API never renders this as HTML. Strip control chars
    # and normalize whitespace while retaining paragraph boundaries.
    content = "".join(c for c in content if c in "\n\t" or (ord(c) >= 32 and ord(c) != 127))
    lines = [" ".join(line.split()) for line in content.splitlines()]
    return title, "\n".join(line for line in lines if line).strip()


@dataclass(frozen=True)
class FetchedPage:
    url: str
    title: str
    content: str
    status_code: int


class SafeWebFetcher:
    def __init__(self, *, timeout_sec: float = 15, max_bytes: int = 1_000_000,
                 max_redirects: int = 3) -> None:
        if not 0 < timeout_sec <= 30 or not 1024 <= max_bytes <= 2_000_000 or not 0 <= max_redirects <= 5:
            raise ValueError("invalid_fetch_limits")
        self.timeout_sec, self.max_bytes, self.max_redirects = timeout_sec, max_bytes, max_redirects

    async def fetch(self, url: str, *, allowed_hosts: tuple[str, ...],
                    authorized_url: PublicURL | None = None) -> FetchedPage:
        async with asyncio.timeout(self.timeout_sec):
            current = url
            for hop in range(self.max_redirects + 1):
                # Gateway-resolved initial URL is passed by trusted composition;
                # redirects are always newly resolved and checked at every hop.
                public = authorized_url if hop == 0 and authorized_url is not None else await resolve_public_url(current, allowed_hosts)
                if public.url != current:
                    raise ScopeDenied("resolved fetch destination does not match")
                public = validate_public_url(current, allowed_hosts, resolved_addresses=public.addresses)
                response_data = await self._get(public)
                status, headers, body = response_data
                if status in (301, 302, 303, 307, 308):
                    if hop >= self.max_redirects or not headers.get("location"):
                        raise FetchFailure("FETCH_REDIRECT_LIMIT")
                    current = urljoin(current, headers["location"])
                    # Lexical and exact-host check before any next DNS lookup.
                    validate_public_url_syntax(current, allowed_hosts)
                    continue
                if status != 200:
                    raise FetchFailure("FETCH_HTTP_ERROR")
                content_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
                if content_type not in ("text/html", "text/plain", "application/xhtml+xml"):
                    raise FetchFailure("FETCH_CONTENT_TYPE_UNSUPPORTED")
                title, content = sanitize_text(body.decode("utf-8", errors="replace"), html=content_type != "text/plain")
                if len(content.encode("utf-8")) > 2_000_000:
                    raise FetchFailure("FETCH_RESPONSE_TOO_LARGE")
                if not content:
                    raise FetchFailure("FETCH_EMPTY_CONTENT")
                return FetchedPage(current, title or urlsplit(current).hostname or "Public source", content, status)
        raise FetchFailure("FETCH_FAILED")

    async def _get(self, public: PublicURL) -> tuple[int, dict[str, str], bytes]:
        # No IP failover/retry: one bounded connection per hop. Every DNS answer
        # has already been checked. TLS still verifies the original host.
        url = httpx.URL(public.url).copy_with(host=public.addresses[0])
        request = httpx.Request("GET", url,
            headers={"Host": public.hostname, "User-Agent": "AgentForgeResearch/0.1",
                     "Accept": "text/html,text/plain;q=0.9", "Accept-Encoding": "identity"},
            extensions={"sni_hostname": public.hostname})
        transport = httpx.AsyncHTTPTransport(verify=ssl.create_default_context(), retries=0,
                                             trust_env=False, limits=httpx.Limits(max_connections=1, max_keepalive_connections=0))
        async with httpx.AsyncClient(transport=transport, trust_env=False,
                                     timeout=self.timeout_sec, follow_redirects=False) as client:
            # send() applies client timeouts but request has no environment auth
            # or cookies; manual redirects use another clean one-shot client.
            response = await client.send(request, stream=True)
            try:
                if response.headers.get("content-encoding", "identity").lower() not in ("", "identity"):
                    raise FetchFailure("FETCH_COMPRESSED_CONTENT_REJECTED")
                length = response.headers.get("content-length")
                if length and (not length.isdigit() or int(length) > self.max_bytes):
                    raise FetchFailure("FETCH_RESPONSE_TOO_LARGE")
                body = bytearray()
                async for chunk in response.aiter_raw():
                    body.extend(chunk)
                    if len(body) > self.max_bytes:
                        raise FetchFailure("FETCH_RESPONSE_TOO_LARGE")
                return response.status_code, dict(response.headers), bytes(body)
            finally:
                await response.aclose()
