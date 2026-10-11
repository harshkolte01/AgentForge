"""The delivery workflow: what the Changes panel and the agent's `create_pull_request` tool both call (docs/delivery.md).

Rules: only the signed-in person's token touches GitHub; a branch moves forward only; every mutation carries an
idempotency key and at most one runs per delivery; a write whose outcome is unknown is never repeated blindly - the next
call with the same key finds what already exists (see integrations/github_delivery.py).
"""
import asyncio
import functools
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from uuid import UUID

from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert

from agentforge.database.delivery import ConversationDelivery, DeliveryCommit
from agentforge.database.identity import GitHubInstallation, GitHubRepositoryGrant
from agentforge.database.sessions import Conversation, ConversationMessage, TurnExecution
from agentforge.delivery import workspace as sandbox_git
from agentforge.integrations import github_delivery as gh
from agentforge.integrations.e2b_sandbox import SandboxUnavailable
from agentforge.integrations.github_app import SHA
from agentforge.integrations.github_client import GitHubError
from agentforge.repositories.sessions import bump_conversation, conversation_lock

log = logging.getLogger(__name__)

SYNC_TTL_SEC = 30          # an open pull request is re-read when older than this and somebody is looking
STALE_SYNC_MIN_SEC = 8     # a webhook marked it stale: re-read, but not more often than this
SETTINGS_TTL_SEC = 600     # merge methods rarely change
OPERATION_LEASE_SEC = 150  # a write that never finished blocks others this long
KEEP_OPERATIONS = 30
MAX_VIEWED_FILES = 200
BRANCH = re.compile(r"agentforge/[a-z0-9][a-z0-9._-]{0,80}\Z")
OPEN_STATES = ("open", "draft")

HTTP = {
    "session_not_found": 404, "github_reauthorization_required": 401, "github_access_denied": 403,
    "github_write_permission_required": 403, "github_writes_disabled": 403, "github_repository_not_authorized": 403,
    "github_contents_permission_required": 403, "github_rate_limited": 503, "github_unavailable": 503,
    "delivery_outcome_unknown": 502, "delivery_unavailable": 503,
}


class DeliveryError(Exception):
    """`code` is the machine-readable reason sent to the browser; most are conflicts (409)."""

    def __init__(self, code: str, status: int | None = None) -> None:
        super().__init__(code)
        self.code, self.status = code, status or HTTP.get(code.split(":")[0], 409)


def from_github(error: GitHubError, *, writing: bool = False) -> DeliveryError:
    code = str(error)
    if code == "github_unavailable" and writing:
        return DeliveryError("delivery_outcome_unknown")
    if code == "github_write_conflict":
        return DeliveryError("github_branch_moved")
    if code == "github_nothing_to_commit":
        return DeliveryError("nothing_to_publish")
    if code in {"github_pull_request_confirmation_failed", "github_response_invalid", "github_response_size_limit"}:
        return DeliveryError("delivery_outcome_unknown")
    return DeliveryError(code)


def typed_failures(call):
    """Whatever goes wrong inside a public operation reaches its caller as a DeliveryError with a stable code: a database or
    programming fault is `delivery_unavailable` (logged with its traceback), never a bare exception. For the agent's tool that
    matters most: an unexpected exception from an effect tool is reported as an unknown outcome and ends the whole run."""
    @functools.wraps(call)
    async def wrapper(*args, **kwargs):
        try:
            return await call(*args, **kwargs)
        except DeliveryError:
            raise
        except GitHubError as error:
            raise from_github(error) from None
        except Exception:
            log.error("delivery %s failed unexpectedly", call.__name__, exc_info=True)
            raise DeliveryError("delivery_unavailable") from None
    return wrapper


def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:40].strip("-") or "changes"


def concrete_branch(ref: str | None) -> str | None:
    """A ref that names a branch (not HEAD, not a commit)."""
    if not ref or ref == "HEAD" or SHA.fullmatch(ref):
        return None
    return ref.removeprefix("refs/heads/")


class DeliveryService:
    def __init__(self, sessions, settings, *, provider=None, github=None) -> None:
        self.sessions, self.settings, self.provider = sessions, settings, provider
        self._github = github          # async (user_id) -> client; defaults to the person's GitHub App client
        self._clients: dict[UUID, tuple[float, object]] = {}
        self._locks: dict[UUID, asyncio.Lock] = {}

    # --- context ------------------------------------------------------------------------------------------------------

    async def client(self, user_id: UUID):
        if self._github is not None:
            return await self._github(user_id)
        cached = self._clients.get(user_id)
        if cached and cached[0] > time.monotonic():
            return cached[1]
        from agentforge.integrations.github_app import github_for_user
        client = await github_for_user(self.sessions, self.settings, user_id)
        self._clients[user_id] = (time.monotonic() + 300, client)
        return client

    def _context(self, conversation_id: UUID, user_id: UUID) -> dict:
        with self.sessions() as db:
            row = db.get(Conversation, conversation_id)
            if row is None or row.owner_id != user_id:
                raise DeliveryError("session_not_found")
            repository = row.repository_json or None
            queued = db.scalar(select(TurnExecution.turn_id).where(TurnExecution.session_id == conversation_id,
                TurnExecution.state == "queued").limit(1))
            ctx = {"conversation_id": conversation_id, "user_id": user_id, "title": row.title, "repository": repository,
                   "sandbox_id": row.sandbox_id, "busy": bool(row.active_task_id or queued), "delivery": None,
                   "can_write": None, "checks_readable": None}
            if not repository:
                return ctx
            repo_id = repository["repo_id"]
            delivery = db.scalar(select(ConversationDelivery).where(ConversationDelivery.conversation_id == conversation_id,
                ConversationDelivery.status == "active"))
            if delivery is not None and delivery.repo_id.lower() != repo_id.lower():
                # The conversation moved to another repository before anything was published: that delivery is history.
                delivery.status = "archived"
                db.commit()
                delivery = None
            ctx["delivery"] = self._snapshot(delivery) if delivery else None
            grant = db.scalar(select(GitHubRepositoryGrant).where(GitHubRepositoryGrant.user_id == user_id,
                GitHubRepositoryGrant.revoked_at.is_(None), func.lower(GitHubRepositoryGrant.full_name) == repo_id.lower()))
            if grant is not None:
                installation = db.get(GitHubInstallation, grant.installation_id)
                permissions = (installation.permissions_json if installation else {}) or {}
                ctx["can_write"] = bool(grant.permissions_json.get("push") and permissions.get("contents") == "write"
                                        and permissions.get("pull_requests") == "write")
                ctx["checks_readable"] = permissions.get("checks") in {"read", "write"}
            return ctx

    @staticmethod
    def _snapshot(row: ConversationDelivery) -> dict:
        return {name: getattr(row, name) for name in (
            "id", "conversation_id", "user_id", "repo_id", "base_branch", "base_sha", "branch", "head_sha", "state", "pr_number",
            "pr_url", "pr_node_id", "pr_json", "unpublished_json", "pending_op_json", "sync_error", "synced_at", "version",
            "operations_json")}

    def _unavailable(self, ctx: dict) -> str | None:
        if not ctx["repository"]:
            return "no_repository"
        if getattr(self.settings, "auth_mode", None) != "github_app":
            return "github_not_connected"
        if self.provider is None:
            return "sandbox_not_configured"
        return None

    # --- the view -----------------------------------------------------------------------------------------------------

    async def view(self, conversation_id: UUID, user_id: UUID, *, refresh: bool = False) -> dict:
        ctx = await asyncio.to_thread(self._context, conversation_id, user_id)
        if self._unavailable(ctx) is None and ctx["delivery"]:
            if await self._maybe_sync(ctx, force=refresh):
                ctx = await asyncio.to_thread(self._context, conversation_id, user_id)
        return await asyncio.to_thread(self._build_view, ctx)

    def _build_view(self, ctx: dict) -> dict:
        reason = self._unavailable(ctx)
        d = ctx["delivery"]
        repo_id = ctx["repository"]["repo_id"] if ctx["repository"] else None
        pr = (d or {}).get("pr_json") or {}
        number = (d or {}).get("pr_number")
        pull = None
        if d and number:
            pull = {"number": number, "url": d["pr_url"], "title": pr.get("title") or "", "state": pr.get("state") or "open",
                    "head_sha": pr.get("head_sha") or d.get("head_sha") or "", "mergeable": pr.get("mergeable") or "unknown",
                    "behind_base": pr.get("behind_base"),
                    "checks": pr.get("checks") or {"state": "none", "passed": 0, "failed": 0, "pending": 0, "items": []},
                    "reviews": pr.get("reviews") or {"decision": "none", "items": []}, "merged_at": pr.get("merged_at"),
                    "merge_methods": pr.get("merge_methods") or list(gh.MERGE_METHODS)}
            # The failure summaries are for the agent (get_pr_status), not the panel.
            pull["checks"] = {**pull["checks"], "items": [{k: v for k, v in item.items() if k != "summary"}
                                                          for item in pull["checks"].get("items", [])]}
        commits = []
        if d:
            with self.sessions() as db:
                commits = [{"sha": c.sha, "message": c.message.split("\n", 1)[0][:200], "files": c.files, "additions": c.additions,
                            "deletions": c.deletions, "created_at": iso(c.created_at),
                            "url": f"https://github.com/{d['repo_id']}/commit/{c.sha}"}
                           for c in db.scalars(select(DeliveryCommit).where(DeliveryCommit.delivery_id == d["id"])
                                               .order_by(DeliveryCommit.created_at.desc()).limit(50))]
        unpublished = (d or {}).get("unpublished_json")
        pending = (d or {}).get("pending_op_json")
        return {
            "available": reason is None, "unavailable_reason": reason,
            "capabilities": {"writes_enabled": bool(self.settings.enable_github_writes), "can_write": ctx["can_write"],
                             "checks_readable": ctx["checks_readable"]},
            "busy": ctx["busy"], "repo_id": repo_id,
            "base_branch": (d or {}).get("base_branch") or concrete_branch((ctx["repository"] or {}).get("ref")),
            "base_sha": (d or {}).get("base_sha"), "branch": (d or {}).get("branch"),
            "branch_url": f"https://github.com/{repo_id}/tree/{gh.branch_ref(d['branch'])}" if d and d.get("branch") else None,
            "head_sha": (d or {}).get("head_sha"), "state": (d or {}).get("state") or "none", "pull_request": pull,
            "unpublished": ({k: unpublished[k] for k in ("files", "additions", "deletions", "updated_at")} if unpublished else None),
            "commits": commits,
            "pending_operation": ({"kind": pending["kind"], "started_at": pending["started_at"]} if pending else None),
            "synced_at": iso((d or {}).get("synced_at")), "sync_error": (d or {}).get("sync_error"),
            "version": (d or {}).get("version") or 0,
        }

    # --- following the pull request -------------------------------------------------------------------------------------

    async def _maybe_sync(self, ctx: dict, *, force: bool) -> bool:
        d = ctx["delivery"]
        if not d.get("pr_number"):
            if force and d.get("pending_op_json"):
                return await self._reconcile(ctx)
            return False
        pr = d["pr_json"] or {}
        age = (now() - d["synced_at"]).total_seconds() if d["synced_at"] else 10**9
        live = pr.get("state") in OPEN_STATES or not pr
        stale = bool(pr.get("stale"))
        if not (force or (stale and age > STALE_SYNC_MIN_SEC) or (live and age > SYNC_TTL_SEC)):
            return False
        return await self.sync(ctx["conversation_id"], ctx["user_id"], d)

    async def sync(self, conversation_id: UUID, user_id: UUID, d: dict) -> bool:
        """Reads the pull request from GitHub into the delivery. One read at a time per delivery; a failed read keeps the
        old values and records why (the panel says 'status unavailable', never 'no pull request')."""
        lock = self._locks.setdefault(d["id"], asyncio.Lock())
        if lock.locked():
            return False
        async with lock:
            try:
                client = await self.client(user_id)
                gh_client, repo, installation = await client.authorize(d["repo_id"])
                pr = d["pr_json"] or {}
                settings_fresh = pr.get("repo_settings") if pr.get("repo_settings_at", 0) > time.time() - SETTINGS_TTL_SEC else None
                status = await gh.read_pull_status(gh_client, repo["full_name"], d["pr_number"], installation=installation,
                                                   repo_settings=settings_fresh)
            except GitHubError as error:
                code = str(error)
                code = code if code in {"github_rate_limited", "github_reauthorization_required", "github_access_denied"} else "github_unavailable"
                await asyncio.to_thread(self._apply, d["id"], sync_error=code)
                return True
            except Exception:
                log.warning("delivery sync failed", exc_info=True)
                await asyncio.to_thread(self._apply, d["id"], sync_error="github_unavailable")
                return True
            stored = {k: v for k, v in status.items() if k not in {"node_id", "url", "number"}}
            stored["repo_settings_at"] = time.time() if settings_fresh is None else pr.get("repo_settings_at", time.time())
            await asyncio.to_thread(self._apply, d["id"], pr_json=stored, pr_node_id=status.get("node_id") or d.get("pr_node_id"),
                                    sync_error=None, synced_at=now())
            return True

    async def _reconcile(self, ctx: dict) -> bool:
        """A pull request was being opened when the answer was lost: look for it by its head branch."""
        d = ctx["delivery"]
        if not d.get("branch"):
            return False
        try:
            client = await self.client(ctx["user_id"])
            gh_client, repo, _ = await client.authorize(d["repo_id"])
            found = await gh.find_pull_request(gh_client, repo["full_name"], repo["full_name"].split("/")[0], d["branch"])
        except GitHubError:
            return False
        if found is None:
            return False
        await asyncio.to_thread(self._apply, d["id"], state="pull_request", pr_number=found["number"], pr_url=found["url"],
                                pr_node_id=found["node_id"], pending_op_json=None, pr_json={"state": "draft" if found["draft"] else "open", "stale": True})
        return True

    # --- database writes ----------------------------------------------------------------------------------------------

    def _apply(self, delivery_id: UUID, *, key: str | None = None, kind: str | None = None, done: bool = False,
               clear_pending: bool = False, notify: bool = True, **values) -> None:
        """Changes a delivery row in one transaction and tells the conversation's readers."""
        with self.sessions() as db, db.begin():
            row = db.execute(select(ConversationDelivery).where(ConversationDelivery.id == delivery_id).with_for_update()).scalar_one()
            for name, value in values.items():
                setattr(row, name, value)
            if done and key:
                operations = dict(row.operations_json or {})
                operations[key] = {"status": "done", "kind": kind, "at": now().isoformat()}
                row.operations_json = dict(list(operations.items())[-KEEP_OPERATIONS:])
            if clear_pending:
                row.pending_op_json = None
            row.version += 1
            row.updated_at = now()
            if notify:
                conversation = db.execute(select(Conversation).where(Conversation.id == row.conversation_id).with_for_update()).scalar_one()
                bump_conversation(db, conversation, "delivery.changed")

    def _ensure_delivery(self, ctx: dict, *, base_branch: str | None) -> dict:
        with self.sessions() as db, db.begin():
            conversation_lock(db, ctx["conversation_id"])
            row = db.scalar(select(ConversationDelivery).where(ConversationDelivery.conversation_id == ctx["conversation_id"],
                ConversationDelivery.status == "active"))
            if row is None:
                row = ConversationDelivery(conversation_id=ctx["conversation_id"], user_id=ctx["user_id"],
                    repo_id=ctx["repository"]["repo_id"], base_branch=base_branch)
                db.add(row)
                db.flush()
            elif row.base_branch is None and base_branch:
                row.base_branch = base_branch
            return self._snapshot(row)

    def _claim(self, delivery_id: UUID, key: str, kind: str) -> bool:
        """True to run, False when this exact request already finished (a retry)."""
        with self.sessions() as db, db.begin():
            row = db.execute(select(ConversationDelivery).where(ConversationDelivery.id == delivery_id).with_for_update()).scalar_one()
            if (row.operations_json or {}).get(key, {}).get("status") == "done":
                return False
            pending = row.pending_op_json
            if pending and pending.get("key") != key:
                started = datetime.fromisoformat(pending["started_at"])
                if now() - started < timedelta(seconds=OPERATION_LEASE_SEC):
                    raise DeliveryError("delivery_operation_in_progress")
            row.pending_op_json = {"key": key, "kind": kind, "started_at": now().isoformat()}
            row.version += 1
            return True

    def _record_commit(self, delivery_id: UUID, sha: str, message: str, files: list, task_id: UUID | None) -> None:
        with self.sessions() as db, db.begin():
            db.execute(insert(DeliveryCommit).values(delivery_id=delivery_id, sha=sha, message=message[:5000], files=len(files),
                additions=sum(f.additions for f in files), deletions=sum(f.deletions for f in files), task_id=task_id)
                .on_conflict_do_nothing(index_elements=["delivery_id", "sha"]))

    # --- changes ------------------------------------------------------------------------------------------------------

    async def _attach(self, ctx: dict):
        if self.provider is None:
            raise DeliveryError("delivery_workspace_expired")
        if not ctx["sandbox_id"]:
            return None
        try:
            workspace = await self.provider.attach(ctx["sandbox_id"])
        except SandboxUnavailable:
            raise DeliveryError("delivery_workspace_unavailable", 503) from None
        if workspace is None:
            raise DeliveryError("delivery_workspace_expired")
        return workspace

    async def changes(self, conversation_id: UUID, user_id: UUID) -> dict:
        ctx = await asyncio.to_thread(self._context, conversation_id, user_id)
        empty = {"workspace": "ready", "baseline": "base", "files": [], "additions": 0, "deletions": 0, "truncated": False, "diff_version": ""}
        reason = self._unavailable(ctx)
        if reason:
            return {**empty, "workspace": "no_repository" if reason == "no_repository" else "unavailable"}
        try:
            workspace = await self._attach(ctx)
        except DeliveryError as error:
            return {**empty, "workspace": "expired" if error.code == "delivery_workspace_expired" else "unavailable"}
        if workspace is None:
            return empty
        try:
            measured = await sandbox_git.measure(workspace)
        except SandboxUnavailable:
            return {**empty, "workspace": "unavailable"}
        await asyncio.to_thread(self._store_summary, ctx, sandbox_git.summary(measured))
        files = measured.files[:MAX_VIEWED_FILES]
        return {"workspace": "ready", "baseline": measured.baseline,
                "files": [{"path": f.path, "status": f.status, "additions": f.additions, "deletions": f.deletions, "binary": f.binary,
                           "truncated": f.truncated, "patch": f.patch, "blocked": f.blocked} for f in files],
                "additions": sum(f.additions for f in measured.files), "deletions": sum(f.deletions for f in measured.files),
                "truncated": measured.truncated or len(measured.files) > MAX_VIEWED_FILES, "diff_version": measured.diff_version}

    def _store_summary(self, ctx: dict, summary: dict | None) -> None:
        """Keeps the last measurement on the delivery so the card and header chip need not open the sandbox."""
        d = ctx["delivery"]
        if d is None and summary is None:
            return
        if d is None:
            d = self._ensure_delivery(ctx, base_branch=concrete_branch((ctx["repository"] or {}).get("ref")))
        previous = d.get("unpublished_json")
        if (previous or {}).get("diff_version") == (summary or {}).get("diff_version"):
            return
        self._apply(d["id"], unpublished_json=summary)

    async def record_summary(self, conversation_id: UUID, user_id: UUID, workspace) -> None:
        """Called by the worker when a turn ends, with the workspace the turn used."""
        ctx = await asyncio.to_thread(self._context, conversation_id, user_id)
        if not ctx["repository"]:
            return
        measured = await sandbox_git.measure(workspace, patches=False)
        await asyncio.to_thread(self._store_summary, ctx, sandbox_git.summary(measured))

    async def discard_file(self, conversation_id: UUID, user_id: UUID, path: str) -> dict:
        ctx = await asyncio.to_thread(self._context, conversation_id, user_id)
        self._require_available(ctx)
        if ctx["busy"]:
            raise DeliveryError("agent_running")
        workspace = await self._attach(ctx)
        if workspace is None:
            raise DeliveryError("delivery_workspace_expired")
        try:
            await sandbox_git.discard(workspace, path, baseline_has=await sandbox_git.baseline_has(workspace, path))
        except ValueError:
            raise DeliveryError("github_unsafe_change_path") from None
        except SandboxUnavailable:
            raise DeliveryError("delivery_workspace_unavailable", 503) from None
        return await self.changes(conversation_id, user_id)

    # --- suggestions --------------------------------------------------------------------------------------------------

    async def suggest(self, conversation_id: UUID, user_id: UUID) -> dict:
        """A commit message, pull request title and body, and branch name, from the conversation and the changes. Plain text
        assembly (no extra model call): the person edits them in the dialog."""
        ctx = await asyncio.to_thread(self._context, conversation_id, user_id)
        changes = await self.changes(conversation_id, user_id)
        answer = await asyncio.to_thread(self._last_answer, conversation_id)
        title = re.sub(r"\s+", " ", ctx["title"] or "").strip()[:100] or "Changes from AgentForge"
        listing = "\n".join(f"- `{f['path']}` ({f['status']}, +{f['additions']} −{f['deletions']})" for f in changes["files"][:30])
        more = f"\n- … and {len(changes['files']) - 30} more" if len(changes["files"]) > 30 else ""
        body = (answer + "\n\n" if answer else "") + (f"### Changes\n{listing}{more}" if listing else "")
        delivery = ctx["delivery"]
        branch = (delivery or {}).get("branch") or f"agentforge/{slug(title)}-{uuid4().hex[:6]}"
        return {"commit_message": title, "pr_title": title, "pr_body": body.strip()[:7000], "branch": branch}

    def _last_answer(self, conversation_id: UUID) -> str:
        with self.sessions() as db:
            # Only an answer the agent actually gave: the receipt of a failed or stopped run ("Something went wrong…") is not a summary.
            text = db.scalar(select(ConversationMessage.content)
                .join(TurnExecution, TurnExecution.assistant_message_id == ConversationMessage.id)
                .where(ConversationMessage.session_id == conversation_id, ConversationMessage.role == "assistant",
                       TurnExecution.state.in_(("completed", "partial")))
                .order_by(ConversationMessage.sequence.desc()).limit(1))
        return (text or "").strip()[:4000]

    # --- publishing ---------------------------------------------------------------------------------------------------

    def _require_available(self, ctx: dict) -> None:
        reason = self._unavailable(ctx)
        if reason == "no_repository":
            raise DeliveryError("delivery_no_repository")
        if reason == "sandbox_not_configured":
            raise DeliveryError("delivery_sandbox_not_configured")
        if reason:
            raise DeliveryError("github_not_connected", 403)

    async def push(self, conversation_id: UUID, user_id: UUID, *, key: str, message: str, branch: str | None = None) -> dict:
        return await self._publish_flow(conversation_id, user_id, key=key, kind="push", message=message, branch=branch)

    async def open_pull_request(self, conversation_id: UUID, user_id: UUID, *, key: str, title: str, body: str, draft: bool = True,
                                message: str | None = None, branch: str | None = None) -> dict:
        return await self._publish_flow(conversation_id, user_id, key=key, kind="pull-request", message=message or title,
                                        branch=branch, pr=(title, body, draft))

    async def publish_for_agent(self, conversation_id: UUID, user_id: UUID, workspace, *, key: str, title: str, body: str,
                                task_id: UUID) -> dict:
        """`create_pull_request`: the same flow with the workspace the agent already has open. A delivery that already has a
        branch gets a new commit on it, and one that already has a pull request is not opened twice."""
        await self._publish_flow(conversation_id, user_id, key=key, kind="pull-request", message=title, pr=(title, body, True),
                                 workspace=workspace, task_id=task_id, require_idle=False)
        ctx = await asyncio.to_thread(self._context, conversation_id, user_id)
        d = ctx["delivery"]
        return {"pr_url": d["pr_url"], "pr_number": d["pr_number"], "branch": d["branch"], "head_sha": d["head_sha"]}

    async def _publish_flow(self, conversation_id: UUID, user_id: UUID, *, key: str, kind: str, message: str, branch: str | None = None,
                            pr: tuple[str, str, bool] | None = None, workspace=None, task_id: UUID | None = None,
                            require_idle: bool = True) -> dict:
        message = (message or "").strip()
        if not message or len(message) > 5000:
            raise DeliveryError("delivery_message_required", 422)
        if branch is not None and not BRANCH.fullmatch(branch):
            raise DeliveryError("delivery_branch_invalid", 422)
        if not self.settings.enable_github_writes:
            raise DeliveryError("github_writes_disabled")
        ctx = await asyncio.to_thread(self._context, conversation_id, user_id)
        self._require_available(ctx)
        if require_idle and ctx["busy"]:
            raise DeliveryError("agent_running")
        repo_id = ctx["repository"]["repo_id"]
        try:
            client = await self.client(user_id)
            gh_client, repo, _ = await client.authorize(repo_id, write=True)
        except GitHubError as error:
            raise from_github(error) from None
        name = repo["full_name"]
        base_branch = concrete_branch(ctx["repository"].get("ref")) or repo["default_branch"]
        d = await asyncio.to_thread(self._ensure_delivery, ctx, base_branch=base_branch)
        if not await asyncio.to_thread(self._claim, d["id"], key, kind):
            return await self.view(conversation_id, user_id)
        ambiguous = False
        try:
            if workspace is None:
                workspace = await self._attach(ctx)
            if workspace is None:
                raise DeliveryError("nothing_to_publish")
            marker = await sandbox_git.read_marker(workspace)
            if marker is None or marker[0].lower() != name.lower():
                raise DeliveryError("delivery_no_repository")
            base_sha = d["base_sha"] or marker[1]
            measured = await sandbox_git.measure(workspace, patches=False)
            files = measured.files
            pushed = None
            if files:
                blocked = [f.path for f in files if f.blocked]
                if blocked:
                    raise DeliveryError("delivery_blocked_paths:" + ",".join(blocked[:20]))
                if len(files) > gh.MAX_CHANGED_FILES:
                    raise DeliveryError("github_change_too_large")
                try:
                    contents = await sandbox_git.read_contents(workspace, files)
                except sandbox_git.WorkspaceChanged:
                    raise DeliveryError("delivery_workspace_changed") from None
                changes = {f.path: gh.Change(f.mode, contents.get(f.path), f.old, f.new) for f in files}
                target = d["branch"] or branch or f"agentforge/{slug(message)}-{d['id'].hex[:6]}"
                ambiguous = True
                coauthor = await gh.coauthor_of(gh_client)
                pushed = await gh.publish_commit(gh_client, name, branch=target, base_sha=base_sha, changes=changes, message=message,
                    operation_key=key, branch_known=bool(d["head_sha"] and d["branch"]), coauthor=coauthor)
                ambiguous = False
                await asyncio.to_thread(self._apply, d["id"], notify=False, branch=target, head_sha=pushed.commit_sha, base_sha=base_sha,
                    state="pull_request" if d["pr_number"] else "branch", unpublished_json=None)
                await asyncio.to_thread(self._record_commit, d["id"], pushed.commit_sha, message, files, task_id)
                d = {**d, "branch": target, "head_sha": pushed.commit_sha, "base_sha": base_sha}
                try:
                    await sandbox_git.advance_baseline(workspace, files, pushed.commit_sha)
                except Exception:
                    log.warning("could not move the published marker in the sandbox", exc_info=True)
            elif not (pr and d["branch"] and d["head_sha"]):
                raise DeliveryError("nothing_to_publish")
            if pr and not d["pr_number"]:
                title, body, draft = pr
                footer = f"Opened by AgentForge from [this conversation]({str(self.settings.app_base_url).rstrip('/')}/chat/{conversation_id})."
                ambiguous = True
                opened = await gh.open_pull_request(gh_client, name, branch=d["branch"], base_branch=d["base_branch"] or base_branch,
                    title=title, body=body, draft=draft, footer=footer)
                ambiguous = False
                await asyncio.to_thread(self._apply, d["id"], notify=False, state="pull_request", pr_number=opened["number"], pr_url=opened["url"],
                    pr_node_id=opened["node_id"], pr_json={"state": "draft" if opened["draft"] else "open", "title": title, "stale": True})
            # A push to an existing pull request changes its head: what was known about its checks is out of date.
            stale = {"pr_json": {**(d["pr_json"] or {}), "stale": True}} if d["pr_number"] and pushed is not None else {}
            await asyncio.to_thread(self._apply, d["id"], key=key, kind=kind, done=True, clear_pending=True, **stale)
        except DeliveryError:
            await asyncio.to_thread(self._apply, d["id"], clear_pending=not ambiguous, notify=False)
            raise
        except GitHubError as error:
            risky = ambiguous and str(error) == "github_unavailable"
            await asyncio.to_thread(self._apply, d["id"], clear_pending=not risky, notify=False)
            raise from_github(error, writing=ambiguous) from None
        except SandboxUnavailable:
            await asyncio.to_thread(self._apply, d["id"], clear_pending=True, notify=False)
            raise DeliveryError("delivery_workspace_unavailable", 503) from None
        except Exception:
            # Anything unforeseen is still reported as what it is: a write that may have happened (so the same request finds
            # it again) or a failure before any write. It is never allowed to escape as a bare exception.
            log.error("delivery publish failed unexpectedly (ambiguous=%s)", ambiguous, exc_info=True)
            await asyncio.to_thread(self._apply, d["id"], clear_pending=not ambiguous, notify=False)
            raise DeliveryError("delivery_outcome_unknown" if ambiguous else "delivery_unavailable") from None
        # (A cancellation or crash in the middle leaves the pending marker: the same key finds what was already done.)
        return await self.view(conversation_id, user_id, refresh=bool(pr) or bool(d["pr_number"]))

    # --- pull request actions (human only) ----------------------------------------------------------------------------

    async def _pull_action(self, conversation_id: UUID, user_id: UUID, key: str, kind: str, action) -> dict:
        if not self.settings.enable_github_writes:
            raise DeliveryError("github_writes_disabled")
        ctx = await asyncio.to_thread(self._context, conversation_id, user_id)
        self._require_available(ctx)
        d = ctx["delivery"]
        if d is None or not d["pr_number"]:
            raise DeliveryError("delivery_not_published")
        try:
            client = await self.client(user_id)
            gh_client, repo, _ = await client.authorize(d["repo_id"], write=True)
        except GitHubError as error:
            raise from_github(error) from None
        if not await asyncio.to_thread(self._claim, d["id"], key, kind):
            return await self.view(conversation_id, user_id)
        try:
            await action(gh_client, repo["full_name"], d)
        except DeliveryError:
            await asyncio.to_thread(self._apply, d["id"], clear_pending=True, notify=False)
            raise
        except GitHubError as error:
            await asyncio.to_thread(self._apply, d["id"], clear_pending=str(error) != "github_unavailable", notify=False)
            raise from_github(error, writing=True) from None
        except Exception:
            log.error("delivery %s failed unexpectedly", kind, exc_info=True)
            raise DeliveryError("delivery_outcome_unknown") from None   # the marker stays: the same request re-checks
        await asyncio.to_thread(self._apply, d["id"], key=key, kind=kind, done=True, clear_pending=True,
                                pr_json={**(d["pr_json"] or {}), "stale": True})
        return await self.view(conversation_id, user_id, refresh=True)

    async def mark_ready(self, conversation_id: UUID, user_id: UUID, *, key: str) -> dict:
        async def act(client, name, d):
            if (d["pr_json"] or {}).get("state") not in {"draft", None}:
                return
            if not d["pr_node_id"]:
                raise DeliveryError("delivery_not_published")
            await gh.mark_ready(client, d["pr_node_id"])
        return await self._pull_action(conversation_id, user_id, key, "ready", act)

    async def merge(self, conversation_id: UUID, user_id: UUID, *, key: str, method: str, expected_head_sha: str) -> dict:
        async def act(client, name, d):
            state = (d["pr_json"] or {}).get("state")
            if state == "draft":
                raise DeliveryError("github_draft_pull_request")
            if state in {"merged", "closed"}:
                raise DeliveryError("github_not_mergeable")
            await gh.merge_pull_request(client, name, d["pr_number"], method=method, expected_head=expected_head_sha,
                                        title=(d["pr_json"] or {}).get("title"))
        return await self._pull_action(conversation_id, user_id, key, "merge", act)

    async def close(self, conversation_id: UUID, user_id: UUID, *, key: str) -> dict:
        async def act(client, name, d):
            await gh.close_pull_request(client, name, d["pr_number"])
        return await self._pull_action(conversation_id, user_id, key, "close", act)

    async def update_branch(self, conversation_id: UUID, user_id: UUID, *, key: str) -> dict:
        async def act(client, name, d):
            head = (d["pr_json"] or {}).get("head_sha") or d["head_sha"]
            await gh.update_pull_branch(client, name, d["pr_number"], head)
        return await self._pull_action(conversation_id, user_id, key, "update-branch", act)

    async def reset(self, conversation_id: UUID, user_id: UUID, *, key: str, discard_unpublished: bool = False) -> dict:
        """Starts over after a merge or close: the delivery is archived and the sandbox dropped, so the next message loads
        the repository again from its (updated) branch."""
        ctx = await asyncio.to_thread(self._context, conversation_id, user_id)
        self._require_available(ctx)
        if ctx["busy"]:
            raise DeliveryError("agent_running")
        d = ctx["delivery"]
        if d is None:
            return await self.view(conversation_id, user_id)
        state = (d["pr_json"] or {}).get("state")
        if d["pr_number"] and state in OPEN_STATES:
            raise DeliveryError("delivery_reset_not_allowed")
        if d.get("unpublished_json") and not discard_unpublished:
            raise DeliveryError("delivery_unpublished_changes")
        try:
            workspace = await self._attach(ctx)
        except DeliveryError:
            workspace = None
        if workspace is not None:
            await self.provider.kill(workspace)
        await asyncio.to_thread(self._archive, d["id"], conversation_id)
        return await self.view(conversation_id, user_id)

    def _archive(self, delivery_id: UUID, conversation_id: UUID) -> None:
        with self.sessions() as db, db.begin():
            conversation = db.execute(select(Conversation).where(Conversation.id == conversation_id).with_for_update()).scalar_one()
            db.execute(update(ConversationDelivery).where(ConversationDelivery.id == delivery_id).values(status="archived"))
            conversation.sandbox_id = None
            bump_conversation(db, conversation, "delivery.changed")

    # --- webhooks -----------------------------------------------------------------------------------------------------

    def invalidate(self, repo_full_name: str, *, pr_number: int | None = None, head_branch: str | None = None) -> int:
        """GitHub said something changed about a pull request: its stored status is marked stale (the next read re-reads it)
        and its conversation's readers are told. Nothing in the payload is trusted beyond choosing which deliveries to mark."""
        with self.sessions() as db, db.begin():
            query = select(ConversationDelivery).where(ConversationDelivery.status == "active",
                func.lower(ConversationDelivery.repo_id) == repo_full_name.lower(), ConversationDelivery.pr_number.is_not(None))
            if pr_number is not None:
                query = query.where(ConversationDelivery.pr_number == pr_number)
            elif head_branch is not None:
                query = query.where(ConversationDelivery.branch == head_branch)
            rows = list(db.scalars(query.with_for_update()))
            for row in rows:
                if (row.pr_json or {}).get("stale"):
                    continue   # already waiting to be re-read: a burst of CI events changes nothing more
                row.pr_json = {**(row.pr_json or {}), "stale": True}
                row.version += 1
                conversation = db.execute(select(Conversation).where(Conversation.id == row.conversation_id).with_for_update()).scalar_one()
                bump_conversation(db, conversation, "delivery.changed")
            return len(rows)


# Every operation a caller can start answers with a DeliveryError (or a result), never with a raw exception.
for _operation in ("view", "changes", "suggest", "discard_file", "push", "open_pull_request", "publish_for_agent", "mark_ready",
                   "merge", "close", "update_branch", "reset"):
    setattr(DeliveryService, _operation, typed_failures(getattr(DeliveryService, _operation)))
