"""OAuth for remote MCP servers: the client side of the MCP authorization specification (which builds on OAuth 2.1).

What happens, in order, when a person signs in to a server:
  1. Discovery. An unauthenticated request is answered 401 with a `WWW-Authenticate` challenge. The server's Protected Resource
     Metadata (RFC 9728) names its authorization server, whose metadata (RFC 8414, or OpenID Connect Discovery) gives the endpoints.
  2. Registration. This app is a client of that authorization server: through a Client ID Metadata Document when it supports
     them, else Dynamic Client Registration (RFC 7591), else a client the person registered themselves.
  3. Authorization. The person's browser goes to the authorization endpoint with PKCE (S256), the server's `resource` (RFC 8707)
     and a `state` that is checked on the way back, along with the issuer (`iss`, RFC 9207).
  4. Tokens. The code is exchanged for tokens, which are kept encrypted, sent only to this server, and refreshed before they expire.

Every URL here comes from a remote document, so each is held to the rules the plugin client holds a typed-in address to (https,
public addresses only, no redirects, bounded reads: see mcp.py). Errors carry a stable code and a message written here, never a
server's text, a URL, a token or a secret.
"""
import asyncio
import base64
import hashlib
import json
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

import httpx

from .mcp import CLIENT_NAME, CLIENT_VERSION, McpError, _tls, challenge_params, resolve_target

MAX_DOCUMENT_BYTES = 262_144
MAX_SCOPE_CHARS = 1000
MAX_TOKEN_CHARS = 8192
FLOW_TTL = timedelta(minutes=10)
# A token this close to its end is refreshed before it is used.
REFRESH_SKEW = timedelta(seconds=90)
CLIENT_METADATA_PATH = "/api/v1/plugins/oauth/client.json"
CALLBACK_PATH = "/api/v1/plugins/oauth/callback"


class OAuthError(Exception):
    """A failed sign-in step, with a stable code, a message a person can act on, and the HTTP status for the API."""

    def __init__(self, code: str, message: str, *, status: int = 422) -> None:
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


# --- plain HTTP, held to the plugin network rules ----------------------------------------------------------------------
@dataclass(frozen=True)
class Reply:
    status: int
    headers: httpx.Headers
    body: bytes


async def fetch(url: str, *, allow_private: bool, method: str = "GET", headers: dict[str, str] | None = None, content: bytes | None = None,
                timeout: float = 15, limit: int = MAX_DOCUMENT_BYTES) -> Reply:
    """One request to a URL taken from a remote document. Never follows a redirect; reads at most `limit` bytes."""
    try:
        async with asyncio.timeout(timeout + 10):
            target = await resolve_target(url, allow_private=allow_private)
            failure: Exception | None = None
            for address in target.addresses:
                try:
                    return await _once(target, address, method, headers or {}, content, timeout, limit)
                except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                    failure = exc
            raise OAuthError("plugin_oauth_unreachable", "The sign-in service could not be reached.") from failure
    except OAuthError:
        raise
    except McpError as error:
        raise OAuthError("plugin_oauth_unreachable" if error.code != "PLUGIN_URL_REJECTED" else "plugin_oauth_url_rejected", error.message) from None
    except (TimeoutError, httpx.TimeoutException):
        raise OAuthError("plugin_oauth_timeout", "The sign-in service did not answer in time.") from None
    except httpx.HTTPError:
        raise OAuthError("plugin_oauth_unreachable", "The connection to the sign-in service failed.") from None


async def _once(target, address: str, method: str, headers: dict[str, str], content: bytes | None, timeout: float, limit: int) -> Reply:
    url = httpx.URL(target.url).copy_with(host=address.split("%", 1)[0])
    sent = {"Host": target.host_header, "User-Agent": f"{CLIENT_NAME}/{CLIENT_VERSION}", **headers}
    extensions = {"sni_hostname": target.host} if target.secure else {}
    transport = httpx.AsyncHTTPTransport(verify=_tls(), retries=0, trust_env=False, limits=httpx.Limits(max_connections=1, max_keepalive_connections=0))
    async with httpx.AsyncClient(transport=transport, trust_env=False, timeout=httpx.Timeout(connect=10, read=timeout, write=10, pool=10), follow_redirects=False) as client:
        async with client.stream(method, url, content=content, headers=sent, extensions=extensions) as response:
            data = bytearray()
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if len(data) > limit:
                    raise OAuthError("plugin_oauth_too_large", "The sign-in service sent more than can be read.")
            return Reply(response.status_code, response.headers, bytes(data))


def _json(reply: Reply, *, accept: tuple[int, ...] = (200,), typed: bool = True) -> dict | None:
    """The JSON object a reply carries. `typed` also requires it to say it is JSON: documents found by address must, answers to
    requests this app made are read as JSON whatever they say."""
    if reply.status not in accept or (typed and "json" not in reply.headers.get("content-type", "").lower()):
        return None
    try:
        document = json.loads(reply.body)
    except ValueError:
        return None
    return document if isinstance(document, dict) else None


async def get_document(url: str, *, allow_private: bool) -> dict | None:
    """A JSON document at `url`, or None when there is none there (a missing document is an answer, not a failure)."""
    try:
        return _json(await fetch(url, allow_private=allow_private, headers={"Accept": "application/json"}))
    except OAuthError:
        return None


# --- discovery -------------------------------------------------------------------------------------------------------
def _origin_parts(url: str) -> tuple[str, str, int | None, str]:
    parts = urlsplit(url)
    default = {"https": 443, "http": 80}.get(parts.scheme)
    port = parts.port if parts.port is not None else default
    return parts.scheme.lower(), (parts.hostname or "").lower(), port, parts.path


def same_resource(declared: str, address: str) -> bool:
    """Whether the resource a server's metadata declares (RFC 9728) covers the address it was reached at: the same origin, and
    that address at or below the declared path. A server may name its whole origin (`https://host`, metadata at the root) or just
    its endpoint (`https://host/mcp`); one can never declare a resource on another origin, so a token cannot be steered elsewhere."""
    try:
        a, b = _origin_parts(declared), _origin_parts(address)
        base = a[3].rstrip("/")
        covers = a[:3] == b[:3] and (b[3].rstrip("/") == base or b[3].startswith(base + "/"))
        return covers and not urlsplit(declared).query and not urlsplit(declared).fragment
    except ValueError:
        return False


def same_issuer(declared: str, expected: str) -> bool:
    """RFC 8414 section 3.3: the issuer in the metadata is exactly the one the document was found by. For an issuer that is only an
    origin, `https://host` and `https://host/` are the same identifier (RFC 3986 section 6.2.3) and servers do mix them."""
    if declared == expected:
        return True
    try:
        a, b = _origin_parts(declared), _origin_parts(expected)
        return a[:3] == b[:3] and a[3] in ("", "/") and b[3] in ("", "/") and not urlsplit(declared).query and not urlsplit(expected).query
    except ValueError:
        return False


def resource_metadata_urls(server_url: str, challenged: str | None) -> list[str]:
    """Where the server's Protected Resource Metadata may be, in the order to try (RFC 9728, MCP authorization specification)."""
    parts = urlsplit(server_url)
    root = f"{parts.scheme}://{parts.netloc}"
    path = parts.path.rstrip("/")
    urls = [challenged] if challenged else []
    if path:
        urls.append(f"{root}/.well-known/oauth-protected-resource{path}")
    urls.append(f"{root}/.well-known/oauth-protected-resource")
    return list(dict.fromkeys(urls))


def authorization_server_metadata_urls(issuer: str) -> list[str]:
    """The metadata documents to try for an issuer, in the order the specification gives (OAuth 2.0 first, then OpenID Connect)."""
    parts = urlsplit(issuer)
    root = f"{parts.scheme}://{parts.netloc}"
    path = parts.path.rstrip("/")
    if path:
        return [f"{root}/.well-known/oauth-authorization-server{path}", f"{root}/.well-known/openid-configuration{path}",
                f"{root}{path}/.well-known/openid-configuration"]
    return [f"{root}/.well-known/oauth-authorization-server", f"{root}/.well-known/openid-configuration"]


@dataclass(frozen=True)
class Discovery:
    """What it takes to sign in to one server."""
    resource: str
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    revocation_endpoint: str | None
    registration_endpoint: str | None
    metadata_documents: bool
    iss_supported: bool
    auth_methods: tuple[str, ...]
    scope: str | None


def _https(value: object, *, allow_private: bool) -> str | None:
    """`value` when it is an http(s) URL fit to call (https unless private networks are allowed)."""
    if not isinstance(value, str) or not value or len(value) > 2048:
        return None
    parts = urlsplit(value)
    if parts.scheme == "https" or (allow_private and parts.scheme == "http"):
        return value if parts.hostname and not parts.fragment else None
    return None


def choose_scope(challenge: str | None, resource_scopes: object, server_scopes: object) -> str | None:
    """The scopes to ask for: the challenge's, else everything the resource lists (the specification's order), plus `offline_access`
    when the authorization server offers it, since an agent must keep working after the first token expires."""
    chosen: list[str] = []
    if challenge:
        chosen = challenge.split()
    elif isinstance(resource_scopes, list):
        chosen = [item for item in resource_scopes if isinstance(item, str) and item]
    if isinstance(server_scopes, list) and "offline_access" in server_scopes and "offline_access" not in chosen and chosen:
        chosen.append("offline_access")
    elif not chosen and isinstance(server_scopes, list) and "offline_access" in server_scopes and "openid" in server_scopes:
        chosen = ["openid", "offline_access"]
    text = " ".join(dict.fromkeys(chosen))
    return text[:MAX_SCOPE_CHARS].rsplit(" ", 1)[0] if len(text) > MAX_SCOPE_CHARS else (text or None)


async def _server_metadata(issuer: str, *, allow_private: bool) -> dict | None:
    for url in authorization_server_metadata_urls(issuer):
        document = await get_document(url, allow_private=allow_private)
        # A document naming another issuer than the one it was found by is not used (RFC 8414 section 3.3); the next place is tried.
        if document is not None and isinstance(document.get("issuer"), str) and same_issuer(document["issuer"], issuer):
            return document
    return None


async def discover(server_url: str, *, allow_private: bool) -> Discovery:
    """The sign-in details of a server that answers 401, or an OAuthError saying why they cannot be used."""
    probe = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": CLIENT_NAME, "version": CLIENT_VERSION}}}).encode()
    reply = await fetch(server_url, allow_private=allow_private, method="POST", content=probe, headers={
        "Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
    if reply.status in (200, 202):
        raise OAuthError("plugin_oauth_not_required", "This server does not ask for a sign-in.")
    if reply.status not in (401, 403):
        raise OAuthError("plugin_oauth_unsupported", "This server does not offer a sign-in this app can use.")
    challenge = challenge_params(reply.headers.get_list("www-authenticate"))
    declared: dict | None = None
    for url in resource_metadata_urls(server_url, _https(challenge.get("resource_metadata"), allow_private=allow_private)):
        document = await get_document(url, allow_private=allow_private)
        if document and isinstance(document.get("authorization_servers"), list):
            declared = document
            break
    if declared is None:
        raise OAuthError("plugin_oauth_unsupported", "This server does not offer a sign-in this app can use. Use an access token instead, if it takes one.")
    resource = declared.get("resource")
    if not isinstance(resource, str) or not same_resource(resource, server_url):
        raise OAuthError("plugin_oauth_metadata_invalid", "The server's sign-in information does not match its address, so it was not used.")
    servers = [item for item in declared["authorization_servers"] if isinstance(item, str)][:3]
    reason = "The server's sign-in service could not be read."
    for issuer in servers:
        if not _https(issuer, allow_private=allow_private):
            continue
        metadata = await _server_metadata(issuer, allow_private=allow_private)
        if metadata is None:
            continue
        authorization = _https(metadata.get("authorization_endpoint"), allow_private=allow_private)
        token = _https(metadata.get("token_endpoint"), allow_private=allow_private)
        if not authorization or not token:
            continue
        methods = metadata.get("code_challenge_methods_supported")
        if not isinstance(methods, list) or "S256" not in methods:
            # Without PKCE (S256) a stolen code could be redeemed by anyone: the specification says to refuse.
            reason = "The server's sign-in service does not support the protection (PKCE) this app requires."
            continue
        types = metadata.get("response_types_supported")
        if isinstance(types, list) and "code" not in types:
            continue
        supported = metadata.get("token_endpoint_auth_methods_supported")
        registration = _https(metadata.get("registration_endpoint"), allow_private=allow_private)
        return Discovery(
            resource=resource, issuer=metadata["issuer"], authorization_endpoint=authorization, token_endpoint=token,
            revocation_endpoint=_https(metadata.get("revocation_endpoint"), allow_private=allow_private), registration_endpoint=registration,
            metadata_documents=metadata.get("client_id_metadata_document_supported") is True,
            iss_supported=metadata.get("authorization_response_iss_parameter_supported") is True,
            auth_methods=tuple(item for item in supported if isinstance(item, str)) if isinstance(supported, list) else (),
            scope=choose_scope(challenge.get("scope"), declared.get("scopes_supported"), metadata.get("scopes_supported")))
    raise OAuthError("plugin_oauth_unsupported", reason)


# --- registration ----------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Client:
    """This app's identity at one authorization server."""
    client_id: str
    secret: str | None
    method: str
    kind: str
    expires_at: datetime | None = None


def callback_url(settings) -> str:
    return str(settings.api_public_base_url).rstrip("/") + CALLBACK_PATH


def metadata_document_url(settings) -> str | None:
    """The address of this app's Client ID Metadata Document, which must be https (an authorization server fetches it)."""
    base = str(settings.api_public_base_url).rstrip("/")
    return base + CLIENT_METADATA_PATH if base.startswith("https://") else None


def metadata_document(settings) -> dict:
    """What an authorization server reads at this app's client id (draft-ietf-oauth-client-id-metadata-document)."""
    document = {"client_id": metadata_document_url(settings), "client_name": "AgentForge", "client_uri": str(settings.app_base_url).rstrip("/"),
                "redirect_uris": [callback_url(settings)], "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"], "token_endpoint_auth_method": "none"}
    if str(settings.app_base_url).startswith("https://"):
        document["logo_uri"] = str(settings.app_base_url).rstrip("/") + "/brand/agentforge-icon-192.png"
    return document


def preferred_method(supported: tuple[str, ...]) -> str:
    """How this app authenticates at the token endpoint: as a public client when it may, else with the secret it is given."""
    if not supported or "none" in supported:
        return "none"
    return "client_secret_post" if "client_secret_post" in supported else "client_secret_basic"


async def register(discovery: Discovery, settings, *, allow_private: bool, given: tuple[str, str | None] | None = None,
                   cached: Client | None = None) -> Client:
    """The client to sign in with, by the specification's order: one the person registered, a Client ID Metadata Document, then
    Dynamic Client Registration (`cached` is a registration made earlier at this issuer, which the caller looks up: it owns the database)."""
    if given is not None:
        return Client(given[0], given[1], "client_secret_post" if given[1] else "none", "preregistered")
    document_url = metadata_document_url(settings)
    if discovery.metadata_documents and document_url and settings.plugin_oauth_registration == "auto":
        return Client(document_url, None, "none", "metadata_document")
    if cached is not None:
        return cached
    if not discovery.registration_endpoint:
        raise OAuthError("plugin_oauth_client_required", "This server does not register apps automatically. Sign in with your own OAuth app (Advanced) or use an access token.")
    redirect = callback_url(settings)
    method = preferred_method(discovery.auth_methods)
    body = {"client_name": "AgentForge", "client_uri": str(settings.app_base_url).rstrip("/"), "redirect_uris": [redirect],
            "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"], "token_endpoint_auth_method": method,
            "application_type": "web" if redirect.startswith("https://") else "native"}
    if str(settings.app_base_url).startswith("https://"):
        body["logo_uri"] = str(settings.app_base_url).rstrip("/") + "/brand/agentforge-icon-192.png"
    reply = await fetch(discovery.registration_endpoint, allow_private=allow_private, method="POST", content=json.dumps(body).encode(),
                        headers={"Content-Type": "application/json", "Accept": "application/json"})
    document = _json(reply, accept=(200, 201), typed=False)
    if document is None or not isinstance(document.get("client_id"), str) or not document["client_id"] or len(document["client_id"]) > 1024:
        raise OAuthError("plugin_oauth_registration_failed", "The server refused to register this app for sign-in.")
    registered = document.get("redirect_uris")
    if isinstance(registered, list) and redirect not in registered:
        raise OAuthError("plugin_oauth_registration_failed", "The server registered a different return address than the one asked for.")
    secret = document.get("client_secret") if isinstance(document.get("client_secret"), str) and document.get("client_secret") else None
    granted = document.get("token_endpoint_auth_method")
    method = granted if granted in ("none", "client_secret_post", "client_secret_basic") else method
    if method != "none" and not secret:
        raise OAuthError("plugin_oauth_registration_failed", "The server registered this app without the secret it needs.")
    expires = document.get("client_secret_expires_at")
    return Client(document["client_id"], secret if method != "none" else None, method, "dynamic",
                  datetime.fromtimestamp(expires, timezone.utc) if isinstance(expires, (int, float)) and expires > 0 else None)


# --- authorization ---------------------------------------------------------------------------------------------------
def new_state() -> str:
    return secrets.token_urlsafe(32)


def new_binding() -> str:
    return secrets.token_urlsafe(32)


def new_verifier() -> str:
    """A PKCE code verifier (RFC 7636): 43 to 128 characters of the unreserved set."""
    return secrets.token_urlsafe(64)


def code_challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")


def digest(value: str) -> str:
    """What is stored of a state or a binding: enough to compare, useless to replay."""
    return hashlib.sha256(value.encode()).hexdigest()


def authorization_url(discovery: Discovery, client_id: str, redirect_uri: str, state: str, verifier: str, scope: str | None) -> str:
    parts = urlsplit(discovery.authorization_endpoint)
    query = parse_qsl(parts.query, keep_blank_values=True)
    query += [("response_type", "code"), ("client_id", client_id), ("redirect_uri", redirect_uri), ("state", state),
              ("code_challenge", code_challenge(verifier)), ("code_challenge_method", "S256"), ("resource", discovery.resource)]
    if scope:
        query.append(("scope", scope))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query, quote_via=quote), ""))


def check_issuer(returned: str | None, expected: str, *, advertised: bool) -> None:
    """RFC 9207 (required by the MCP authorization specification): a response that names another issuer, or that should name one
    and does not, is refused before its code is used. Only an issuer is compared; what else the response says is never acted on."""
    if returned is None:
        if advertised:
            raise OAuthError("plugin_oauth_issuer_mismatch", "The sign-in response could not be verified, so it was not used.", status=400)
        return
    if returned != expected and not same_issuer(returned, expected):
        raise OAuthError("plugin_oauth_issuer_mismatch", "The sign-in response came from a different service than expected, so it was not used.", status=400)


# --- tokens ----------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Tokens:
    access: str
    refresh: str | None
    expires_at: datetime | None
    scope: str | None


@dataclass(frozen=True)
class TokenClient:
    """What a request to the token endpoint needs besides its grant."""
    token_endpoint: str
    client_id: str
    secret: str | None
    method: str
    resource: str


def _client_auth(client: TokenClient, form: dict[str, str]) -> dict[str, str]:
    headers = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}
    if client.method == "client_secret_basic" and client.secret:
        headers["Authorization"] = "Basic " + base64.b64encode(f"{quote(client.client_id, safe='')}:{quote(client.secret, safe='')}".encode()).decode()
    else:
        form["client_id"] = client.client_id
        if client.method == "client_secret_post" and client.secret:
            form["client_secret"] = client.secret
    return headers


def _parse_tokens(reply: Reply, *, previous_refresh: str | None = None, now: datetime | None = None) -> Tokens:
    document = _json(reply, typed=False)
    if document is None:
        raise OAuthError("plugin_oauth_exchange_failed", "The sign-in service did not return a usable answer.")
    access = document.get("access_token")
    kind = document.get("token_type")
    if not isinstance(access, str) or not access or len(access) > MAX_TOKEN_CHARS or any(ord(char) < 33 or ord(char) == 127 for char in access):
        raise OAuthError("plugin_oauth_exchange_failed", "The sign-in service did not return a usable access token.")
    if kind is not None and (not isinstance(kind, str) or kind.lower() != "bearer"):
        raise OAuthError("plugin_oauth_exchange_failed", "The sign-in service returned a kind of token this app cannot use.")
    refresh = document.get("refresh_token")
    refresh = refresh if isinstance(refresh, str) and refresh and len(refresh) <= MAX_TOKEN_CHARS else previous_refresh
    lifetime = document.get("expires_in")
    # `expires_in` may come as a number or numeric text; no lifetime means the token is used until the server rejects it.
    try:
        seconds = float(lifetime) if lifetime is not None and not isinstance(lifetime, bool) else None
    except (TypeError, ValueError):
        seconds = None
    scope = document.get("scope")
    return Tokens(access, refresh, (now or datetime.now(timezone.utc)) + timedelta(seconds=seconds) if seconds and seconds > 0 else None,
                  scope[:MAX_SCOPE_CHARS] if isinstance(scope, str) and scope else None)


def _token_error(reply: Reply, *, refreshing: bool) -> OAuthError:
    code = ""
    document = None
    try:
        document = json.loads(reply.body)
    except ValueError:
        pass
    if isinstance(document, dict) and isinstance(document.get("error"), str):
        code = document["error"] if re.fullmatch(r"[a-z_]{1,40}", document["error"]) else ""
    if code == "invalid_grant" or (refreshing and reply.status in (400, 401) and code in ("", "invalid_request")):
        return OAuthError("plugin_oauth_grant_rejected", "The sign-in has expired or was withdrawn. Sign in again.")
    if code in ("invalid_client", "unauthorized_client") or reply.status == 401:
        return OAuthError("plugin_oauth_client_rejected", "The sign-in service no longer accepts this app's registration.")
    return OAuthError("plugin_oauth_exchange_failed", "The sign-in service refused the request.")


async def _token_request(client: TokenClient, form: dict[str, str], *, allow_private: bool, refreshing: bool, previous_refresh: str | None = None) -> Tokens:
    form["resource"] = client.resource
    headers = _client_auth(client, form)
    reply = await fetch(client.token_endpoint, allow_private=allow_private, method="POST", content=urlencode(form).encode(), headers=headers, timeout=20)
    if reply.status != 200:
        raise _token_error(reply, refreshing=refreshing)
    return _parse_tokens(reply, previous_refresh=previous_refresh)


async def exchange_code(client: TokenClient, code: str, verifier: str, redirect_uri: str, *, allow_private: bool) -> Tokens:
    return await _token_request(client, {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri, "code_verifier": verifier},
                                allow_private=allow_private, refreshing=False)


async def refresh_tokens(client: TokenClient, refresh_token: str, *, allow_private: bool) -> Tokens:
    return await _token_request(client, {"grant_type": "refresh_token", "refresh_token": refresh_token}, allow_private=allow_private,
                                refreshing=True, previous_refresh=refresh_token)


async def revoke(endpoint: str, client: TokenClient, token: str, hint: str, *, allow_private: bool) -> None:
    """Tells the authorization server to forget a token (RFC 7009). Best effort: signing out never waits on, or fails because of, it."""
    form = {"token": token, "token_type_hint": hint}
    headers = _client_auth(client, form)
    try:
        await fetch(endpoint, allow_private=allow_private, method="POST", content=urlencode(form).encode(), headers=headers, timeout=8)
    except OAuthError:
        pass
