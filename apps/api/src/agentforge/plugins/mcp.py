"""A small Model Context Protocol client for remote servers (Streamable HTTP), with the network rules a user-supplied URL needs.

A plugin's address is typed in by a person, so it is treated like any other URL the agent could be steered to:
  * https only (plain http and private addresses only with PLUGIN_ALLOW_PRIVATE_NETWORK, a development setting);
  * every address the name resolves to must be public, and the connection goes to that checked address with TLS bound to the
    original host name (resolved again for every request, so a name cannot change its answer between check and use);
  * redirects are never followed, proxies and cookies are never used, and a response is read only up to a size limit.
Errors carry a stable code and a message written here, never the server's text, a URL or a header, so no credential can leak.
"""
import asyncio
import functools
import itertools
import json
import re
import socket
import ssl
from dataclasses import dataclass
from ipaddress import ip_address, ip_network
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx

PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26")
CLIENT_NAME, CLIENT_VERSION = "AgentForge", "0.1.0"
MAX_RESPONSE_BYTES = 4_000_000
# A token sent as a header can be long (a signed token carries claims); this is far above any real one and below what servers accept.
MAX_CREDENTIAL_CHARS = 8200
MAX_PAGES, MAX_LISTED = 10, 500
HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]{1,64}$")
RESERVED_HEADERS = frozenset({"host", "content-length", "content-type", "accept", "accept-encoding", "mcp-session-id", "mcp-protocol-version",
                              "transfer-encoding", "connection", "upgrade", "te", "trailer", "cookie", "user-agent", "origin"})
LOCAL_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home.arpa")


@functools.lru_cache(maxsize=1)
def _tls() -> ssl.SSLContext:
    """One verifying TLS context for the process (building one reads the system certificates, which is slow)."""
    return ssl.create_default_context()


class McpError(Exception):
    """A failure with a stable machine code and a message safe to show a person (or the model)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


@dataclass(frozen=True)
class Target:
    url: str
    host: str
    port: int
    secure: bool
    addresses: tuple[str, ...]

    @property
    def host_header(self) -> str:
        default = 443 if self.secure else 80
        host = f"[{self.host}]" if ":" in self.host else self.host
        return host if self.port == default else f"{host}:{self.port}"


def validate_url(url: str, *, allow_private: bool) -> tuple[str, str, int, bool]:
    """(url, host, port, secure) of an acceptable server address, or McpError."""
    reject = lambda why: McpError("PLUGIN_URL_REJECTED", why)  # noqa: E731
    if not isinstance(url, str) or not url.strip() or len(url) > 2048 or any(ord(char) < 33 or ord(char) == 127 for char in url) or "\\" in url:
        raise reject("Enter the server's full web address, for example https://example.com/mcp.")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise reject("That is not a valid web address.") from None
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("https", "http") or not host:
        raise reject("The address must start with https://.")
    if parts.scheme == "http" and not allow_private:
        raise reject("The address must use https://.")
    if parts.username is not None or parts.password is not None or parts.fragment:
        raise reject("The address must not contain a user name, password or #fragment. Credentials are entered separately.")
    if not allow_private:
        if host in ("localhost", "metadata.google.internal") or host.endswith(LOCAL_SUFFIXES) or "." not in host and ":" not in host:
            raise reject("That address points to a private network, which plugins cannot reach.")
    if not re.fullmatch(r"[a-z0-9.:-]+", host):
        raise reject("That is not a valid host name.")
    secure = parts.scheme == "https"
    return url, host, port or (443 if secure else 80), secure


NAT64 = ip_network("64:ff9b::/96")


def _public(address: str) -> bool:
    ip = ip_address(address)
    # On an IPv6-only network a resolver (DNS64) gives IPv4-only hosts synthesized addresses in 64:ff9b::/96 (RFC 6052), which
    # carry the IPv4 address in their last 32 bits. What matters is that address: a public one is as reachable as any, and one that
    # names a private or local machine stays refused.
    if ip.version == 6 and ip in NAT64:
        return _public(str(ip_address(int(ip) & 0xFFFFFFFF)))
    return ip.is_global and not (ip.is_multicast or ip.is_unspecified or ip.is_reserved or (ip.version == 6 and ip.ipv4_mapped))


async def resolve_target(url: str, *, allow_private: bool) -> Target:
    url, host, port, secure = validate_url(url, allow_private=allow_private)
    loop = asyncio.get_running_loop()
    try:
        answers = await loop.getaddrinfo(host, port, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
    except OSError:
        raise McpError("PLUGIN_UNREACHABLE", "The server's address could not be found.") from None
    addresses = sorted({answer[4][0] for answer in answers}, key=lambda value: (":" in value, value))
    if not addresses:
        raise McpError("PLUGIN_UNREACHABLE", "The server's address could not be found.")
    # Every answer is checked, including ones that will not be tried, so a name that mixes public and private answers is refused.
    if not allow_private and not all(_public(address.split("%", 1)[0]) for address in addresses):
        raise McpError("PLUGIN_URL_REJECTED", "That address points to a private network, which plugins cannot reach.")
    return Target(url, host, port, secure, tuple(addresses))


def clean_headers(headers: dict[str, str], *, limit: int = 4096) -> dict[str, str]:
    """Credential headers, checked: a plain header name and a value that cannot break out of its line."""
    out: dict[str, str] = {}
    for name, value in headers.items():
        if not isinstance(name, str) or not HEADER_NAME.fullmatch(name) or name.lower() in RESERVED_HEADERS:
            raise McpError("PLUGIN_URL_REJECTED", "That header name cannot be used.")
        if not isinstance(value, str) or not value or len(value) > limit or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise McpError("PLUGIN_AUTH_INVALID", "The credential contains characters that cannot be sent.")
        out[name] = value
    return out


_PARAM = re.compile(r'([A-Za-z0-9_-]+)\s*=\s*(?:"((?:[^"\\]|\\.)*)"|([^\s,]+))')


def challenge_params(values: list[str]) -> dict[str, str]:
    """`resource_metadata`, `scope`, `error` and the like from the Bearer challenge(s) of a 401/403 (RFC 6750 section 3)."""
    found: dict[str, str] = {}
    for value in values:
        start = value.lower().find("bearer")
        if start < 0:
            continue
        for match in _PARAM.finditer(value[start + 6:]):
            found.setdefault(match.group(1).lower(), (match.group(2) if match.group(2) is not None else match.group(3)).replace('\\"', '"'))
    return found


def normalize_result(result: Any) -> dict[str, Any]:
    """A tools/call result as {text, is_error, structured}: text parts joined, other media named but not carried."""
    if not isinstance(result, dict):
        raise McpError("PLUGIN_PROTOCOL_ERROR", "The server sent a result that is not an MCP tool result.")
    parts: list[str] = []
    for item in result.get("content") if isinstance(result.get("content"), list) else []:
        if not isinstance(item, dict):
            continue
        kind = item.get("type")
        if kind == "text" and isinstance(item.get("text"), str):
            parts.append(item["text"])
        elif kind in ("image", "audio"):
            parts.append(f"[{kind} content not shown]")
        elif kind == "resource_link" and isinstance(item.get("uri"), str):
            parts.append(f"[resource: {item['uri'][:300]}]")
        elif kind == "resource" and isinstance(item.get("resource"), dict):
            resource = item["resource"]
            parts.append(resource["text"] if isinstance(resource.get("text"), str) else f"[resource: {str(resource.get('uri', ''))[:300]}]")
    structured = result.get("structuredContent")
    return {"text": "\n\n".join(parts), "is_error": result.get("isError") is True,
            "structured": structured if isinstance(structured, (dict, list)) else None}


class TokenSource(Protocol):
    """Credentials that change over time, such as an OAuth access token."""

    async def headers(self) -> dict[str, str]:
        """The credential header for the next request. McpError when there is none that can be used."""

    async def rejected(self) -> bool:
        """The server answered 401 to what `headers` gave. Gets a new credential if it can; True when asking again is worthwhile."""

    async def insufficient(self, scope: str | None) -> None:
        """The server answered 403 `insufficient_scope`: the sign-in does not cover what was asked (`scope` is what it needs)."""


class McpClient:
    """One connection to one server. Initializes lazily, keeps its session between calls, and starts a new one if it expires."""

    def __init__(self, url: str, *, headers: dict[str, str] | None = None, allow_private: bool = False, timeout: float = 60,
                 auth: "TokenSource | None" = None) -> None:
        self.url, self.allow_private, self.timeout = url, allow_private, timeout
        self.headers = clean_headers(headers or {})
        # A source of credentials that change over time (an OAuth token): asked for the header on every request.
        self.auth = auth
        self.session_id: str | None = None
        self.version: str | None = None
        self.server_name: str | None = None
        self._ids = itertools.count(1)
        self._ready = False
        # Only starting a session is exclusive; calls on a started session may overlap.
        self._init_lock = asyncio.Lock()

    # --- transport ---------------------------------------------------------------------------------------------------
    def _request_headers(self, target: Target, bare: bool = False, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {"Host": target.host_header, "User-Agent": f"{CLIENT_NAME}/{CLIENT_VERSION}", "Content-Type": "application/json",
                   "Accept": "application/json, text/event-stream", **self.headers, **(extra or {})}
        if self.version and not bare:
            headers["MCP-Protocol-Version"] = self.version
        if self.session_id and not bare:
            headers["Mcp-Session-Id"] = self.session_id
        return headers

    async def _post(self, target: Target, address: str, body: bytes | None, method: str, expect_id: int | None, bare: bool,
                    extra: dict[str, str] | None = None) -> tuple[int, httpx.Headers, Any]:
        url = httpx.URL(target.url).copy_with(host=address.split("%", 1)[0])
        extensions = {"sni_hostname": target.host} if target.secure else {}
        transport = httpx.AsyncHTTPTransport(verify=_tls(), retries=0, trust_env=False,
                                             limits=httpx.Limits(max_connections=1, max_keepalive_connections=0))
        timeout = httpx.Timeout(connect=10, read=self.timeout, write=10, pool=10)
        async with httpx.AsyncClient(transport=transport, trust_env=False, timeout=timeout, follow_redirects=False) as client:
            async with client.stream(method, url, content=body, headers=self._request_headers(target, bare, extra), extensions=extensions) as response:
                status, headers = response.status_code, response.headers
                if status >= 300 or expect_id is None:
                    # Nothing useful is read from an error or a notification's acknowledgement beyond a small bounded amount.
                    async for _ in response.aiter_bytes():
                        break
                    return status, headers, None
                kind = headers.get("content-type", "").split(";", 1)[0].strip().lower()
                if kind == "text/event-stream":
                    return status, headers, await self._read_events(response, expect_id)
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > MAX_RESPONSE_BYTES:
                        raise McpError("PLUGIN_RESPONSE_TOO_LARGE", "The server's answer was too large to read.")
                return status, headers, self._pick(data, expect_id)

    @staticmethod
    def _pick(data: bytes | bytearray | str, expect_id: int) -> Any:
        try:
            decoded = json.loads(bytes(data) if isinstance(data, bytearray) else data)
        except ValueError:
            raise McpError("PLUGIN_PROTOCOL_ERROR", "The server did not answer in the MCP format.") from None
        for message in decoded if isinstance(decoded, list) else [decoded]:
            if isinstance(message, dict) and message.get("id") == expect_id and ("result" in message or "error" in message):
                return message
        return None

    async def _read_events(self, response: httpx.Response, expect_id: int) -> Any:
        total, buffer = 0, []
        async for line in response.aiter_lines():
            total += len(line) + 1
            if total > MAX_RESPONSE_BYTES:
                raise McpError("PLUGIN_RESPONSE_TOO_LARGE", "The server's answer was too large to read.")
            if line == "":
                if buffer:
                    try:
                        found = self._pick("\n".join(buffer), expect_id)
                    except McpError:
                        found = None  # an event that is not the answer (a log line, a progress notice) is skipped
                    buffer = []
                    if found is not None:
                        return found
            elif line.startswith("data:"):
                buffer.append(line[5:].removeprefix(" "))
        if buffer:
            return self._pick("\n".join(buffer), expect_id)
        return None

    async def _send(self, payload: dict | None, *, method: str = "POST", expect_id: int | None = None, bare: bool = False) -> tuple[int, httpx.Headers, Any]:
        body = json.dumps(payload, separators=(",", ":")).encode() if payload is not None else None
        # A credential that changes (an OAuth token) is read fresh for every request, so a refreshed one is used at once.
        extra = clean_headers(await self.auth.headers(), limit=MAX_CREDENTIAL_CHARS) if self.auth is not None else None
        try:
            async with asyncio.timeout(self.timeout + 15):
                target = await resolve_target(self.url, allow_private=self.allow_private)
                failure: Exception | None = None
                for address in target.addresses:
                    try:
                        return await self._post(target, address, body, method, expect_id, bare, extra)
                    except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                        failure = exc  # that address did not answer; the rest were checked just as carefully
                raise McpError("PLUGIN_UNREACHABLE", "The server could not be reached.") from failure
        except McpError:
            raise
        except TimeoutError:
            raise McpError("PLUGIN_TIMEOUT", "The server did not answer in time.") from None
        except httpx.TimeoutException:
            raise McpError("PLUGIN_TIMEOUT", "The server did not answer in time.") from None
        except ssl.SSLError:
            raise McpError("PLUGIN_TLS_ERROR", "The server's security certificate could not be verified.") from None
        except httpx.HTTPError:
            raise McpError("PLUGIN_UNREACHABLE", "The connection to the server failed.") from None

    @staticmethod
    def _check_status(status: int, *, session: bool) -> None:
        if status in (200, 202):
            return
        if status in (401, 403):
            raise McpError("PLUGIN_AUTH_FAILED", "The server did not accept the credential. Check that it is correct and has the access the plugin needs.")
        if status == 404 and not session:
            raise McpError("PLUGIN_NOT_FOUND", "Nothing answered at that address. Check the server's URL.")
        if status == 429:
            raise McpError("PLUGIN_RATE_LIMITED", "The server is limiting requests. Try again shortly.")
        if 300 <= status < 400:
            raise McpError("PLUGIN_REDIRECT", "The address redirects somewhere else. Use the final address.")
        if status >= 500:
            raise McpError("PLUGIN_SERVER_ERROR", "The server reported an error.")
        if status in (400, 404, 405, 406, 415):
            raise McpError("PLUGIN_PROTOCOL_ERROR", "That address does not look like a remote MCP server (Streamable HTTP).")
        raise McpError("PLUGIN_REJECTED", f"The server refused the request (HTTP {status}).")

    # --- protocol ----------------------------------------------------------------------------------------------------
    async def _initialize(self) -> None:
        """Starts a session. The one being replaced (if any) stays in use until the new one is agreed, so calls that overlap
        this never go out without a session."""
        retried = False
        while True:
            request_id = next(self._ids)
            status, headers, message = await self._send({"jsonrpc": "2.0", "id": request_id, "method": "initialize", "params": {
                "protocolVersion": PROTOCOL_VERSION, "capabilities": {}, "clientInfo": {"name": CLIENT_NAME, "version": CLIENT_VERSION}}},
                expect_id=request_id, bare=True)
            if status == 401 and self.auth is not None and not retried:
                retried = True
                if await self.auth.rejected():
                    continue
            break
        await self._after_challenge(status, headers)
        self._check_status(status, session=False)
        result = self._result(message)
        version = result.get("protocolVersion") if isinstance(result, dict) else None
        if version not in SUPPORTED_VERSIONS:
            raise McpError("PLUGIN_PROTOCOL_ERROR", "The server uses a version of the protocol that is not supported.")
        session = headers.get("mcp-session-id")
        if session is not None and not re.fullmatch(r"[\x21-\x7e]{1,512}", session):
            raise McpError("PLUGIN_PROTOCOL_ERROR", "The server sent an invalid session id.")
        self.version, self.session_id = version, session
        info = result.get("serverInfo") if isinstance(result.get("serverInfo"), dict) else {}
        self.server_name = (str(info.get("title") or info.get("name") or "")[:80] or None)
        status, _, _ = await self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self._check_status(status, session=True)
        self._ready = True

    @staticmethod
    def _result(message: Any) -> Any:
        if not isinstance(message, dict):
            raise McpError("PLUGIN_PROTOCOL_ERROR", "The server did not answer the request.")
        if "error" in message:
            error = message["error"] if isinstance(message["error"], dict) else {}
            text = " ".join(str(error.get("message") or "").split())[:300]
            raise McpError("PLUGIN_REQUEST_FAILED", f"The server returned an error: {text}" if text else "The server returned an error.")
        return message.get("result")

    async def request(self, method: str, params: dict | None = None) -> Any:
        if not self._ready:
            async with self._init_lock:
                if not self._ready:
                    await self._initialize()
        new_session = new_token = False
        while True:
            request_id = next(self._ids)
            payload: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
            if params is not None:
                payload["params"] = params
            session = self.session_id
            status, headers, message = await self._send(payload, expect_id=request_id)
            if status == 404 and session and not new_session:
                # The session expired: start another, once. A call that overlapped this one may already have done it.
                new_session = True
                async with self._init_lock:
                    if self.session_id == session:
                        await self._initialize()
                continue
            if status == 401 and self.auth is not None and not new_token:
                # The token was refused (revoked, or expired sooner than it said): get another, once, and try again.
                new_token = True
                if await self.auth.rejected():
                    continue
            await self._after_challenge(status, headers)
            self._check_status(status, session=bool(session))
            return self._result(message)

    async def _after_challenge(self, status: int, headers: httpx.Headers) -> None:
        """What a 401 or 403 means for a client that signs in with OAuth: sign in again, or sign in with more access."""
        if self.auth is None or status not in (401, 403):
            return
        challenge = challenge_params(headers.get_list("www-authenticate"))
        if status == 403 and challenge.get("error") == "insufficient_scope":
            await self.auth.insufficient(challenge.get("scope"))
            raise McpError("PLUGIN_SCOPE_INSUFFICIENT", "This needs more access than was granted. Open the plugin and choose Reconnect to grant it.")
        if status == 401:
            raise McpError("PLUGIN_REAUTHORIZE", "The server no longer accepts this sign-in. Open the plugin and choose Reconnect.")

    async def list_tools(self) -> list[dict]:
        tools: list[dict] = []
        cursor: str | None = None
        for _ in range(MAX_PAGES):
            result = await self.request("tools/list", {"cursor": cursor} if cursor else {})
            if not isinstance(result, dict) or not isinstance(result.get("tools"), list):
                raise McpError("PLUGIN_PROTOCOL_ERROR", "The server did not list its tools in the MCP format.")
            tools.extend(item for item in result["tools"] if isinstance(item, dict))
            cursor = result.get("nextCursor") if isinstance(result.get("nextCursor"), str) else None
            if not cursor or len(tools) >= MAX_LISTED:
                break
        return tools[:MAX_LISTED]

    async def call_tool(self, name: str, arguments: dict) -> dict[str, Any]:
        return normalize_result(await self.request("tools/call", {"name": name, "arguments": arguments}))

    async def aclose(self) -> None:
        """Ends the session if the server gave one. Best effort: a server may refuse, and nothing depends on it."""
        if not (self._ready and self.session_id):
            return
        self._ready = False
        try:
            async with asyncio.timeout(5):
                await self._send(None, method="DELETE")
        except Exception:  # noqa: BLE001
            pass
