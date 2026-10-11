"""A fake OAuth-protected MCP server and its authorization server, on one localhost port, for the plugin sign-in tests.

`OAuthWorld` serves, under `base`:
  /mcp                                      the MCP server: 401 (with a Bearer challenge) until a valid access token is sent
  /.well-known/oauth-protected-resource...  its Protected Resource Metadata (RFC 9728)
  /.well-known/oauth-authorization-server/as and /as/register, /as/token, /as/revoke    the authorization server
The switches reproduce what real servers do (and get wrong); `authorize()` plays the person's browser at the consent page.
"""
import base64
import hashlib
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace as NS
from urllib.parse import parse_qs, parse_qsl, urlencode, urlsplit
from uuid import uuid4

TOOLS = [
    {"name": "echo", "description": "Echo.", "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}}, "annotations": {"readOnlyHint": True}},
    {"name": "admin", "description": "Needs the admin scope.", "inputSchema": {"type": "object"}},
]


def challenge_of(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")


class OAuthWorld:
    def __init__(self, *, challenge=True, resource_path="/.well-known/oauth-protected-resource/mcp", resource_declared=None, issuer_declared=None,
                 pkce=("S256",), dcr=True, cimd=False, iss_param=None, lifetime=3600, rotate=True, refresh_tokens=True, auth_methods=("none",),
                 scopes=("read", "write"), challenge_scope="read", token_type="Bearer", oidc_only=False, allowlist=None, mcp_status=None):
        self.challenge, self.resource_path, self.pkce, self.dcr, self.cimd = challenge, resource_path, tuple(pkce) if pkce is not None else None, dcr, cimd
        self.iss_param, self.lifetime, self.rotate, self.refresh_tokens, self.auth_methods = iss_param, lifetime, rotate, refresh_tokens, tuple(auth_methods)
        self.scopes, self.challenge_scope, self.token_type, self.oidc_only, self.allowlist, self.mcp_status = list(scopes), challenge_scope, token_type, oidc_only, allowlist, mcp_status
        self.resource_declared, self.issuer_declared = resource_declared, issuer_declared
        self.clients: dict[str, dict] = {}
        self.codes: dict[str, dict] = {}
        self.access: dict[str, dict] = {}      # access token -> {"scope": ..., "expires": ...}
        self.refresh: dict[str, dict] = {}     # refresh token -> {"client_id": ...}
        self.revoked: list[str] = []
        self.token_requests: list[dict] = []
        self.mcp_requests: list[dict] = []
        self.registrations = 0
        self.fail_refresh: str | None = None   # e.g. "invalid_grant": the next refresh is refused with it
        self.deny = False                      # the consent page says no
        self.counter = 0
        world = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def reply(self, status, body=b"", content_type="application/json", headers=None):
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                for key, value in (headers or {}).items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(body)

            def json(self, status, document, headers=None):
                self.reply(status, json.dumps(document).encode(), headers=headers)

            def do_GET(self):
                path = urlsplit(self.path).path
                if path == world.resource_path or (world.resource_path == "root" and path == "/.well-known/oauth-protected-resource"):
                    return self.json(200, {"resource": world.resource_declared or world.base + "/mcp", "authorization_servers": [world.issuer],
                                           "scopes_supported": world.scopes})
                if path == "/.well-known/oauth-authorization-server/as" and not world.oidc_only:
                    return self.json(200, world.server_metadata())
                if path == "/as/.well-known/openid-configuration" and world.oidc_only:
                    return self.json(200, world.server_metadata())
                if path == "/as/authorize":
                    # The consent page, for a real browser: approves (or refuses) at once and sends the browser back.
                    return self.reply(302, headers={"Location": world.authorize(world.base + self.path, approve=not world.deny)})
                self.reply(404)

            def do_POST(self):
                path = urlsplit(self.path).path
                raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                if path == "/mcp":
                    return self.mcp(raw)
                if path == "/as/register":
                    return self.register(raw)
                if path == "/as/token":
                    return self.token(raw)
                if path == "/as/revoke":
                    form = dict(parse_qsl(raw.decode()))
                    world.revoked.append(form.get("token", ""))
                    return self.reply(200)
                self.reply(404)

            def do_DELETE(self):
                self.reply(200)

            # --- the MCP server ---------------------------------------------------------------------------------
            def mcp(self, raw):
                body = json.loads(raw or b"{}")
                world.mcp_requests.append({"authorization": self.headers.get("Authorization"), "method": body.get("method")})
                if world.mcp_status:
                    return self.reply(world.mcp_status)
                token = (self.headers.get("Authorization") or "").removeprefix("Bearer ")
                grant = world.access.get(token)
                if grant is None or (grant["expires"] is not None and grant["expires"] < time.time()):
                    header = 'Bearer error="invalid_token"'
                    if world.challenge:
                        header += f', resource_metadata="{world.base}/.well-known/oauth-protected-resource/mcp"'
                        if world.challenge_scope:
                            header += f', scope="{world.challenge_scope}"'
                    return self.reply(401, b"", headers={"WWW-Authenticate": header})
                method, ident = body.get("method"), body.get("id")
                if method == "notifications/initialized":
                    return self.reply(202)
                if method == "initialize":
                    return self.json(200, {"jsonrpc": "2.0", "id": ident, "result": {"protocolVersion": "2025-06-18", "capabilities": {}, "serverInfo": {"name": "Protected Server"}}})
                if method == "tools/list":
                    return self.json(200, {"jsonrpc": "2.0", "id": ident, "result": {"tools": TOOLS}})
                if method == "tools/call":
                    name = body["params"]["name"]
                    if name == "admin" and "admin" not in grant["scope"].split():
                        return self.reply(403, b"", headers={"WWW-Authenticate": f'Bearer error="insufficient_scope", scope="admin", resource_metadata="{world.base}/.well-known/oauth-protected-resource/mcp"'})
                    return self.json(200, {"jsonrpc": "2.0", "id": ident, "result": {"content": [{"type": "text", "text": f"ok {name}"}]}})
                self.json(200, {"jsonrpc": "2.0", "id": ident, "error": {"code": -32601, "message": "no such method"}})

            # --- the authorization server -----------------------------------------------------------------------
            def register(self, raw):
                if not world.dcr:
                    return self.reply(404)
                body = json.loads(raw)
                world.registrations += 1
                if world.allowlist is not None and any(uri not in world.allowlist for uri in body.get("redirect_uris", [])):
                    return self.json(400, {"error": "invalid_redirect_uri"})
                client_id = "client-" + uuid4().hex[:12]
                method = body.get("token_endpoint_auth_method", "none")
                record = {"client_id": client_id, "redirect_uris": body["redirect_uris"], "method": method, "secret": "secret-" + uuid4().hex if method != "none" else None}
                world.clients[client_id] = record
                answer = {"client_id": client_id, "redirect_uris": body["redirect_uris"], "token_endpoint_auth_method": method, "client_secret_expires_at": 0}
                if record["secret"]:
                    answer["client_secret"] = record["secret"]
                self.json(201, answer)

            def token(self, raw):
                form = dict(parse_qsl(raw.decode()))
                world.token_requests.append({"form": dict(form), "authorization": self.headers.get("Authorization")})
                client_id = form.get("client_id")
                if self.headers.get("Authorization", "").startswith("Basic "):
                    pair = base64.b64decode(self.headers["Authorization"][6:]).decode()
                    client_id, secret = (part for part in pair.split(":", 1))
                    form["client_secret"] = secret
                client = world.clients.get(client_id or "")
                if client is None:
                    return self.json(401, {"error": "invalid_client"})
                if client["secret"] and form.get("client_secret") != client["secret"]:
                    return self.json(401, {"error": "invalid_client"})
                grant = form.get("grant_type")
                if grant == "authorization_code":
                    code = world.codes.pop(form.get("code", ""), None)
                    if code is None or code["client_id"] != client_id or code["redirect_uri"] != form.get("redirect_uri"):
                        return self.json(400, {"error": "invalid_grant"})
                    if challenge_of(form.get("code_verifier", "")) != code["challenge"]:
                        return self.json(400, {"error": "invalid_grant", "error_description": "PKCE"})
                    if form.get("resource") != code["resource"]:
                        return self.json(400, {"error": "invalid_target"})
                    return self.issue(client_id, code["scope"])
                if grant == "refresh_token":
                    if world.fail_refresh:
                        error, world.fail_refresh = world.fail_refresh, None
                        return self.json(400, {"error": error})
                    used = world.refresh.get(form.get("refresh_token", ""))
                    if used is None or used["client_id"] != client_id:
                        return self.json(400, {"error": "invalid_grant"})
                    if world.rotate:
                        del world.refresh[form["refresh_token"]]
                    return self.issue(client_id, used["scope"], previous=form["refresh_token"])
                self.json(400, {"error": "unsupported_grant_type"})

            def issue(self, client_id, scope, previous=None):
                world.counter += 1
                access = f"access-{world.counter}-{uuid4().hex[:8]}"
                world.access[access] = {"scope": scope, "expires": time.time() + world.lifetime if world.lifetime else None}
                answer = {"access_token": access, "token_type": world.token_type, "scope": scope}
                if world.lifetime:
                    answer["expires_in"] = world.lifetime
                if world.refresh_tokens:
                    refresh = f"refresh-{world.counter}-{uuid4().hex[:8]}" if (world.rotate or previous is None) else previous
                    world.refresh[refresh] = {"client_id": client_id, "scope": scope}
                    answer["refresh_token"] = refresh
                self.json(200, answer)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.httpd.server_port}"
        self.issuer = self.base + "/as"
        self.mcp_url = self.base + "/mcp"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def server_metadata(self) -> dict:
        document = {"issuer": self.issuer_declared or self.issuer, "authorization_endpoint": self.base + "/as/authorize", "token_endpoint": self.base + "/as/token",
                    "revocation_endpoint": self.base + "/as/revoke", "response_types_supported": ["code"],
                    "token_endpoint_auth_methods_supported": list(self.auth_methods), "scopes_supported": self.scopes + ["offline_access"]}
        if self.pkce is not None:
            document["code_challenge_methods_supported"] = list(self.pkce)
        if self.dcr:
            document["registration_endpoint"] = self.base + "/as/register"
        if self.cimd:
            document["client_id_metadata_document_supported"] = True
        if self.iss_param is not None:
            document["authorization_response_iss_parameter_supported"] = self.iss_param
        return document

    # --- the person's browser ----------------------------------------------------------------------------------------
    def authorize(self, authorization_url: str, *, approve=True, iss="default", scope=None) -> str:
        """Plays the consent page: checks the request the way a server does and returns the address the browser is sent back to."""
        query = dict(parse_qsl(urlsplit(authorization_url).query))
        for needed in ("response_type", "client_id", "redirect_uri", "state", "code_challenge", "code_challenge_method", "resource"):
            assert needed in query, f"authorization request lacks {needed}"
        assert query["response_type"] == "code" and query["code_challenge_method"] == "S256"
        assert query["client_id"] in self.clients or query["client_id"].startswith("https://"), "unknown client"
        if query["client_id"] in self.clients:
            assert query["redirect_uri"] in self.clients[query["client_id"]]["redirect_uris"], "redirect_uri not registered"
        if query["client_id"].startswith("https://") and query["client_id"] not in self.clients:
            self.clients[query["client_id"]] = {"client_id": query["client_id"], "redirect_uris": [query["redirect_uri"]], "method": "none", "secret": None}
        reply = {"state": query["state"]}
        if approve:
            code = "code-" + uuid4().hex
            self.codes[code] = {"client_id": query["client_id"], "challenge": query["code_challenge"], "redirect_uri": query["redirect_uri"],
                                "resource": query["resource"], "scope": scope or query.get("scope", "")}
            reply["code"] = code
        else:
            reply["error"] = "access_denied"
        if iss == "default":
            iss = self.issuer if self.iss_param else None
        if iss is not None:
            reply["iss"] = iss
        return query["redirect_uri"] + ("&" if "?" in query["redirect_uri"] else "?") + urlencode(reply)

    def callback_params(self, callback_url: str) -> dict:
        return {key: value[0] for key, value in parse_qs(urlsplit(callback_url).query).items()}

    def settings(self, **extra) -> NS:
        """The settings the OAuth client reads, for a local (http) world."""
        values = dict(plugin_allow_private_network=True, plugin_oauth_registration="auto", api_public_base_url="https://app.example.test",
                      app_base_url="https://app.example.test")
        values.update(extra)
        return NS(**values)
