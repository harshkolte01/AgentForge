"""Ownership and resource checks shared by spawn and every tool call.

Public URL adapters must resolve and pin public DNS addresses, disable automatic
redirects, and run validate_public_url again for every redirect/new connection.
URL lexical validation alone is deliberately not presented as SSRF protection.

Phase 2 repository grants authorize an entire repository ID. Ref and path checks
below enforce syntax and sensitive-path exclusions, not a branch/path allowlist
or a pinned commit. GitHub/E2B adapters must bind checkout/read operations to the
task's approved immutable base and enforce symlink containment before use.
"""

from dataclasses import dataclass
from collections.abc import Callable
from ipaddress import ip_address
from pathlib import PurePosixPath
import re
from typing import Any
from urllib.parse import unquote, urlsplit

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from agentforge.database.models import Project, Task
from agentforge.subagents.scope import EffectiveScope, ScopeDenied, TaskContext


class AuthorizationService:
    def __init__(self, session_factory: Callable[[], Session]):
        self.session_factory = session_factory

    async def check_context(self, ctx: TaskContext) -> None:
        # Query afresh on every boundary so cached contexts do not preserve
        # access after ownership changes. Membership expansion is not MVP scope.
        with self.session_factory() as session:
            owner = session.scalar(select(Project.id).where(
                Project.id == ctx.project_id, Project.owner_id == ctx.user_id,
            ))
            task = session.scalar(select(Task.id).where(
                Task.id == ctx.task_id, Task.project_id == ctx.project_id,
                Task.created_by == ctx.user_id,
            ))
            if owner is None or task is None:
                raise ScopeDenied("task/project ownership is not authorized")


def validate_relative_path(path: str) -> str:
    """Lexical guard; adapters must separately enforce real filesystem containment."""
    if not path or len(path) > 1024 or any(ord(c) < 32 for c in path):
        raise ScopeDenied("invalid resource path")
    decoded = unquote(path)
    parts = decoded.split("/")
    if decoded != path or "\\" in path or ":" in path or PurePosixPath(path).is_absolute():
        raise ScopeDenied("resource path must be a literal relative POSIX path")
    if any(part in ("", ".", "..") for part in parts):
        raise ScopeDenied("resource path traversal is forbidden")
    forbidden = {".git", ".ssh", ".aws", ".config", ".gnupg"}
    if any(part.lower() in forbidden or part.lower().startswith(".env") for part in parts):
        raise ScopeDenied("sensitive resource path is forbidden")
    return path


# The coding tools work inside the conversation's sandbox, which holds no credentials, so dotfiles such as .env.example
# and .github are ordinary files there. A path must still be a literal relative one that stays inside the workspace.
WORKSPACE_TOOLS = frozenset({"read", "write", "edit", "grep", "find", "ls"})


def validate_workspace_path(path: str) -> str:
    if not path or len(path) > 1024 or any(ord(c) < 32 for c in path) or "\\" in path or unquote(path) != path:
        raise ScopeDenied("invalid workspace path")
    if PurePosixPath(path).is_absolute() or ":" in path.split("/", 1)[0]:
        raise ScopeDenied("workspace path must be relative")
    parts = path.split("/")
    if ".." in parts or (parts != ["."] and any(part in ("", ".") for part in parts)):
        raise ScopeDenied("workspace path traversal is forbidden")
    return path


def validate_ref(ref: str) -> str:
    """Validate ref syntax; this does not establish approval or pin a commit."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,199}", ref):
        raise ScopeDenied("invalid repository ref")
    if any(value in ref for value in ("..", "//", "@{")) or ref.endswith(("/", ".", ".lock")):
        raise ScopeDenied("unsafe repository ref")
    if any(part.startswith(".") or part.endswith(".lock") for part in ref.split("/")):
        raise ScopeDenied("unsafe repository ref")
    return ref


@dataclass(frozen=True)
class PublicURL:
    url: str
    hostname: str
    addresses: tuple[str, ...]


def validate_public_url(
    url: str, allowed_hosts: tuple[str, ...], *, resolved_addresses: tuple[str, ...] | None = None,
) -> PublicURL:
    parsed_url = validate_public_url_syntax(url, allowed_hosts)
    host = parsed_url.hostname
    try:
        literal = ip_address(host)
    except ValueError:
        literal = None
    addresses = resolved_addresses if resolved_addresses is not None else ((str(literal),) if literal else ())
    if not addresses:
        raise ScopeDenied("public DNS validation is required before URL use")
    for address in addresses:
        try:
            ip = ip_address(address)
        except ValueError as exc:
            raise ScopeDenied("invalid DNS address") from exc
        if not ip.is_global or ip.is_multicast or ip.is_unspecified or ip.is_reserved or (ip.version == 6 and ip.ipv4_mapped):
            raise ScopeDenied("private or reserved network destinations are forbidden")
    return PublicURL(url, host, tuple(addresses))


def validate_public_url_syntax(url: str, allowed_hosts: tuple[str, ...]) -> PublicURL:
    """Lexical policy only; callers MUST resolve all IPs and pin the connection."""
    if len(url) > 4096 or any(ord(c) < 33 for c in url) or "\\" in url:
        raise ScopeDenied("invalid public URL")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise ScopeDenied("invalid public URL") from exc
    host = parsed.hostname
    if parsed.scheme != "https" or not host or parsed.username or parsed.password or parsed.fragment or port not in (None, 443):
        raise ScopeDenied("only credential-free HTTPS URLs on port 443 are allowed")
    if host != host.lower() or not re.fullmatch(r"[a-z0-9.-]+", host) or ".." in host or host.endswith("."):
        raise ScopeDenied("invalid public hostname")
    if host not in allowed_hosts or host in ("localhost", "metadata.google.internal") or host.endswith((".localhost", ".local", ".internal")):
        raise ScopeDenied("public hostname is outside the exact task allowlist")
    # Validate DNS label syntax as well; numeric host variants still undergo
    # the same mandatory all-address resolution/public-IP checks before use.
    if not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in host.split(".")):
        raise ScopeDenied("invalid public hostname")
    return PublicURL(url, host, ())


def authorize_tool(name: str, args: BaseModel | dict[str, Any], ctx: TaskContext, effective: EffectiveScope,
                   *, resolved_web_url: PublicURL | None = None) -> None:
    if name not in effective.allowed_tools:
        raise ScopeDenied("tool is not authorized for this run")
    values = args.model_dump() if isinstance(args, BaseModel) else args
    permissions = effective.permissions
    if "repo_id" in values and values["repo_id"] not in (permissions.allowed_repo_ids or ()):
        raise ScopeDenied("repository is outside this task's grant")
    if "sandbox_id" in values and values["sandbox_id"] not in (permissions.allowed_sandbox_ids or ()):
        raise ScopeDenied("sandbox is outside this task's grant")
    if "task_id" in values and str(values["task_id"]) != str(ctx.task_id):
        raise ScopeDenied("cross-task evidence access is forbidden")
    if "path" in values:
        if name in WORKSPACE_TOOLS:
            validate_workspace_path(values["path"])
        elif not (name == "list_repo_files" and values["path"] == "."):
            validate_relative_path(values["path"])
    if values.get("path_prefix"):
        validate_relative_path(values["path_prefix"])
    for field in ("ref", "base_ref"):
        if field in values:
            validate_ref(values[field])
    for field in ("command_id", "test_profile"):
        if field in values and values[field] not in (permissions.allowed_command_profiles or ()):
            raise ScopeDenied("execution profile is not authorized")
    # A web URL authorization is constructed only by the trusted resource
    # resolver. Model arguments cannot inject validated addresses. The adapter
    # receives this same pinned destination from the gateway.
    if name in ("fetch_webpage", "browser_public_page"):
        url = values.get("source_url", values.get("url", ""))
        if resolved_web_url is None or resolved_web_url.url != url:
            raise ScopeDenied("trusted resolved URL authorization is required")
        allowed = permissions.allowed_web_hosts or ()
        if ctx.source_discovery_enabled and name == "fetch_webpage" and not allowed:
            # Discovery must also have been approved by the resource resolver,
            # which checks exact URLs against immutable task-owned sources.
            allowed = (resolved_web_url.hostname,)
        validate_public_url(url, allowed, resolved_addresses=resolved_web_url.addresses)
