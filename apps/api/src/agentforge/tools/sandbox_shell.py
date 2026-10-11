"""The coding tools: bash, read, write, edit, grep, find and ls, all working in the conversation's sandbox.

The sandbox is created on the first call of a conversation and kept: files, installed packages and edits stay between
messages. When a repository is selected, the backend uploads its pinned commit into the workspace and makes it a git
repository whose first commit is tagged `agentforge-base`, so `git diff agentforge-base` is exactly what the agent changed.
Nothing here holds a credential, and nothing in the sandbox can reach GitHub as the user.
"""
import asyncio
import io
import logging
import shlex
import tarfile
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select, update

from agentforge.database.models import Task
from agentforge.database.sessions import Conversation, TurnExecution
from agentforge.integrations.e2b_sandbox import (
    WORKSPACE, SandboxProvider, SandboxUnavailable, Workspace, bound_output,
)

from .contracts import ToolOutcome
from .errors import ToolError

log = logging.getLogger(__name__)

BASE_TAG = "agentforge-base"
MARKER = ".agentforge/repo"
# Folders no search or listing should wander into.
NOISE = ("node_modules", ".git", ".agentforge", "__pycache__", ".venv", "dist", "build")
READ_CHARS = 12000
# Starting a VM and loading a repository snapshot into it (download, upload, git init) can take minutes for a large repository.
SETUP_SEC = 600


def _tarball(files: dict[str, bytes], modes: dict[str, int] | None = None) -> bytes:
    """A gzip tarball of the snapshot. Executable files stay executable (scripts such as ./gradlew must still run)."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for path, data in files.items():
            info = tarfile.TarInfo(path)
            info.size = len(data)
            info.mode = 0o755 if (modes or {}).get(path) == 0o755 else 0o644
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


class ConversationSandbox:
    """Opens the conversation's sandbox once per run and loads the selected repository into it."""

    def __init__(self, sessions, provider: SandboxProvider, github=None, *, attachment_storage=None) -> None:
        self.sessions, self.provider, self.github = sessions, provider, github
        self.attachment_storage = attachment_storage
        self._lock = asyncio.Lock()
        self._workspace: Workspace | None = None
        # Said once, with the first result after it happened, so the agent never assumes earlier work is still there.
        self.notice: str | None = None

    def forget(self) -> None:
        """The workspace stopped answering: the next call reconnects instead of reusing a dead connection."""
        self._workspace = None

    def take_notice(self) -> str | None:
        notice, self.notice = self.notice, None
        return notice

    def _lookup(self, task_id: UUID) -> tuple[UUID | None, str | None, dict | None]:
        with self.sessions() as db:
            session_id = db.scalar(select(TurnExecution.session_id).where(TurnExecution.task_id == task_id))
            sandbox_id = db.scalar(select(Conversation.sandbox_id).where(Conversation.id == session_id)) if session_id else None
            grants = db.scalar(select(Task.permissions_json).where(Task.id == task_id)) or {}
        return session_id, sandbox_id, grants.get("repository")

    def _remember(self, session_id: UUID, sandbox_id: str | None) -> None:
        with self.sessions() as db, db.begin():
            db.execute(update(Conversation).where(Conversation.id == session_id).values(sandbox_id=sandbox_id))

    async def workspace(self, task_id: UUID) -> Workspace:
        async with self._lock:
            if self._workspace is None:
                self._workspace = await self._open(task_id)
            await self._workspace.keep_alive()
            return self._workspace

    async def _open(self, task_id: UUID) -> Workspace:
        session_id, sandbox_id, repository = await asyncio.to_thread(self._lookup, task_id)
        workspace, created = await self.provider.open(sandbox_id, metadata={"agentforge_task": str(task_id)})
        loaded = None if created else await self._loaded_repository(workspace)
        if repository and loaded and loaded != repository["repo_id"]:
            # The person switched repositories: files of one must never be mixed into the other.
            await self.provider.kill(workspace)
            workspace, created = await self.provider.open(None, metadata={"agentforge_task": str(task_id)})
            loaded = None
        if session_id and (created or workspace.sandbox_id != sandbox_id):
            await asyncio.to_thread(self._remember, session_id, workspace.sandbox_id)
        if repository and not loaded:
            await self._load_repository(workspace, repository)
        if getattr(workspace, "recreated", False):
            self.notice = ("This conversation's earlier sandbox had expired, so a new one was created: files, installed packages and "
                           "changes from earlier turns are gone" + (" (the repository was checked out again at its pinned commit)"
                           if repository else "") + ". Check what is there before relying on earlier work.")
        if self.attachment_storage is not None:
            await self._stage_attachments(workspace, task_id)
        return workspace

    async def _stage_attachments(self, workspace: Workspace, task_id: UUID) -> None:
        import base64
        from agentforge.integrations.attachment_storage import workspace_attachment_path
        def inputs():
            with self.sessions() as db:
                task = db.get(Task, task_id)
                return task.created_by, (task.permissions_json or {}).get("attachments", [])
        owner, files = await asyncio.to_thread(inputs)
        for index, file in enumerate(files):
            if file.get("upload_id"):
                record = await asyncio.to_thread(self.attachment_storage.load, owner, file)
                data = base64.b64decode(record["data"])
            else:
                data = file["content"].encode()
            await workspace.write(workspace_attachment_path(task_id, index, file["name"]), data)

    async def _loaded_repository(self, workspace: Workspace) -> str | None:
        outcome = await workspace.run(f"cat {MARKER} 2>/dev/null || true", timeout_sec=30)
        text = outcome.output.strip()
        return text.split("@", 1)[0] if "@" in text else None

    async def _load_repository(self, workspace: Workspace, repository: dict) -> None:
        if self.github is None:
            raise SandboxUnavailable("REPOSITORY_UNAVAILABLE")
        snapshot = await self.github.snapshot(repository["repo_id"], repository["ref"])
        # Compressing a large repository takes a while; other runs on this worker keep streaming meanwhile.
        await workspace.write("/tmp/agentforge-repo.tgz", await asyncio.to_thread(_tarball, snapshot.files,
                                                                               getattr(snapshot, "modes", None)))
        script = (f"set -e; tar -xzf /tmp/agentforge-repo.tgz -C {WORKSPACE}; rm -f /tmp/agentforge-repo.tgz; cd {WORKSPACE}; "
                  f"mkdir -p .agentforge; printf '%s' {shlex.quote(repository['repo_id'] + '@' + snapshot.commit_sha)} > {MARKER}; "
                  "git init -q; git config user.email agentforge@users.noreply.github.com; git config user.name AgentForge; "
                  "printf '.agentforge/\\n' >> .git/info/exclude; git add -A; git commit -q -m base --allow-empty; "
                  f"git tag -f {BASE_TAG} >/dev/null")
        outcome = await workspace.run(script, timeout_sec=300, cwd="/")
        if outcome.exit_code != 0:
            raise SandboxUnavailable("REPOSITORY_LOAD_FAILED")

    def repository(self, task_id: UUID) -> dict | None:
        with self.sessions() as db:
            grants = db.scalar(select(Task.permissions_json).where(Task.id == task_id)) or {}
        repository = grants.get("repository")
        if not repository:
            return None
        return {**repository, "base_ref": ((grants.get("automatic") or {}).get("repository") or {}).get("ref")}


def parse_raw_diff(text: str) -> dict[str, tuple[str, str]]:
    """`git diff --raw -z` output as {path: (status, new mode)}."""
    parts = text.split("\0")
    changes: dict[str, tuple[str, str]] = {}
    for index in range(0, len(parts) - 1, 2):
        meta, path = parts[index], parts[index + 1]
        fields = meta.lstrip(":").split(" ")
        if len(fields) >= 5 and path:
            changes[path] = (fields[4][:1], fields[1])
    return changes


def register_sandbox_shell(catalog, sandbox: ConversationSandbox, *, pull_requests=None) -> None:
    """`pull_requests` is a GitHub client able to write (see integrations/github_app.py), or None: then no pull request tool exists."""
    async def opened(execution) -> Workspace:
        try:
            return await sandbox.workspace(execution.task.task_id)
        except SandboxUnavailable as exc:
            raise ToolError(exc.code, "The sandbox could not be started, so no file or command tool can run right now.") from None
        except Exception:
            log.warning("sandbox start failed", exc_info=True)
            raise ToolError("SANDBOX_UNAVAILABLE", "The sandbox could not be started, so no file or command tool can run right now.") from None

    async def shell(workspace: Workspace, command: str, *, timeout_sec: float = 60):
        try:
            return await workspace.run(command, timeout_sec=timeout_sec)
        except SandboxUnavailable as exc:
            sandbox.forget()
            raise ToolError(exc.code, "The sandbox did not answer. Try again; it reconnects on the next call.") from None

    def ok(**data) -> ToolOutcome:
        notice = sandbox.take_notice()
        return ToolOutcome(status="ok", cost_usd=Decimal(0), data={**data, **({"notice": notice} if notice else {})})

    async def bash(args, execution):
        workspace = await opened(execution)
        try:
            outcome = await workspace.run(args.command, timeout_sec=args.timeout_sec)
        except SandboxUnavailable:
            # Only the sandbox could have been affected, and it can be inspected: the agent checks and decides, the run goes on.
            sandbox.forget()
            raise ToolError("SANDBOX_DISCONNECTED", "The connection to the sandbox dropped, so the command may or may not have run. "
                            "Check its effects (files, processes) before running it again.") from None
        return ok(exit_code=outcome.exit_code, output=outcome.output, timed_out=outcome.timed_out, truncated=outcome.truncated)

    async def read(args, execution):
        workspace = await opened(execution)
        try:
            text = await workspace.read_text(args.path)
        except Exception:
            raise ToolError("FILE_NOT_FOUND", f"{args.path} does not exist or is not a text file. Use ls or find to see what is there.") from None
        lines = text.splitlines()
        if args.offset > max(1, len(lines)):
            raise ToolError("INVALID_RANGE", f"{args.path} has {len(lines)} lines; offset {args.offset} is past the end.")
        end = min(len(lines), args.offset + args.limit - 1)
        chosen, size, next_offset = [], 0, None
        for number in range(args.offset, end + 1):
            row = f"{number}\t{lines[number - 1]}"
            if chosen and size + len(row) + 1 > READ_CHARS:
                next_offset = number
                break
            chosen.append(row[:READ_CHARS])
            size += len(row) + 1
        if next_offset is None and end < len(lines):
            next_offset = end + 1
        content, _ = bound_output("\n".join(chosen))
        return ok(path=args.path, content=content, total_lines=len(lines), next_offset=next_offset)

    async def write(args, execution):
        workspace = await opened(execution)
        existed = await shell(workspace, f"test -e {shlex.quote(args.path)}")
        await store(workspace, args.path, args.content)
        return ok(path=args.path, bytes=len(args.content.encode()), created=existed.exit_code != 0)

    async def store(workspace: Workspace, path: str, content: str) -> None:
        try:
            await workspace.write(path, content)
        except asyncio.CancelledError:
            raise
        except Exception:
            sandbox.forget()
            raise ToolError("SANDBOX_DISCONNECTED", f"The sandbox did not confirm the write, so {path} may or may not have changed. "
                            "Read it before writing again.") from None

    async def edit(args, execution):
        workspace = await opened(execution)
        try:
            text = await workspace.read_text(args.path)
        except Exception:
            raise ToolError("FILE_NOT_FOUND", f"{args.path} does not exist or is not a text file.") from None
        count = text.count(args.old_string)
        if count == 0:
            raise ToolError("EDIT_NO_MATCH", "old_string was not found. Read the file again and copy the text exactly, including whitespace.")
        if count > 1 and not args.replace_all:
            raise ToolError("EDIT_AMBIGUOUS", f"old_string matches {count} places. Add surrounding lines to make it unique, or set replace_all.")
        await store(workspace, args.path, text.replace(args.old_string, args.new_string) if args.replace_all else text.replace(args.old_string, args.new_string, 1))
        return ok(path=args.path, replacements=count if args.replace_all else 1)

    def noise_filter() -> str:
        return " ".join(f"-not -path {shlex.quote('*/' + name + '/*')}" for name in NOISE)

    async def grep(args, execution):
        workspace = await opened(execution)
        excludes = " ".join(f"--exclude-dir={shlex.quote(name)}" for name in NOISE)
        include = f"--include={shlex.quote(args.glob)}" if args.glob else ""
        flags = "-rnIE" + ("i" if args.ignore_case else "")
        command = (f"grep {flags} {excludes} {include} -e {shlex.quote(args.pattern)} -- {shlex.quote(args.path)} "
                   f"| head -n {args.max_results}")
        outcome = await shell(workspace, command)
        text, truncated = bound_output(outcome.output)
        return ok(matches=text, count=len(outcome.output.splitlines()) if outcome.output else 0, truncated=truncated)

    async def find(args, execution):
        workspace = await opened(execution)
        command = (f"find {shlex.quote(args.path)} {noise_filter()} -name {shlex.quote(args.pattern)} -print 2>/dev/null "
                   f"| head -n {args.max_results}")
        outcome = await shell(workspace, command)
        text, truncated = bound_output(outcome.output)
        return ok(paths=text, truncated=truncated)

    async def ls(args, execution):
        workspace = await opened(execution)
        outcome = await shell(workspace, f"ls -lAh -- {shlex.quote(args.path)} 2>&1")
        if outcome.exit_code not in (0, None):
            raise ToolError("FILE_NOT_FOUND", f"{args.path} does not exist.")
        text, truncated = bound_output(outcome.output)
        return ok(path=args.path, listing=text, truncated=truncated)

    async def changed_files(args, execution) -> str:
        """For the approval card: every file the pull request would publish, with line counts (read only; the index is restored)."""
        workspace = await opened(execution)
        code, stat = await workspace.capture(f"git add -A && git diff --cached --stat=200 {BASE_TAG}; git reset -q", timeout_sec=60)
        if code != 0 or not stat.strip():
            return "Changed files: none found compared with the repository's pinned commit."
        # CI configuration runs with the repository's own secrets once the branch is pushed: worth a second look.
        warning = ("\n\nNote: this changes CI configuration (.github/), which runs with the repository's secrets."
                   if ".github/" in stat else "")
        return "Changed files:\n" + stat.rstrip() + warning

    async def create_pull_request(args, execution):
        from agentforge.integrations.github_client import GitHubError
        from agentforge.integrations.github_pr import open_draft_pull_request
        repository = await asyncio.to_thread(sandbox.repository, execution.task.task_id)
        if not repository:
            raise ToolError("NO_REPOSITORY", "No repository is selected for this conversation, so there is nothing to open a pull request against.")
        workspace = await opened(execution)
        try:
            # A later turn can pin a newer GitHub commit, while the conversation
            # still holds its original checkout and edits. Publish against that
            # checkout's source commit, never graft its diff onto the new pin.
            try:
                marker = (await workspace.read_text(MARKER)).strip()
                source_repo, base_sha = marker.rsplit("@", 1)
                from agentforge.integrations.github_app import SHA
                if source_repo != repository["repo_id"] or not SHA.fullmatch(base_sha):
                    raise ValueError()
            except (ValueError, OSError):
                raise ToolError("NO_REPOSITORY", "The workspace's original repository commit could not be verified. Keep the files and reconnect the workspace before publishing.") from None
            code, raw = await workspace.capture(f"git add -A && git diff --cached --raw -z --no-renames {BASE_TAG}")
            if code != 0:
                raise ToolError("NO_REPOSITORY", "The workspace is not a checkout of the selected repository, so its changes cannot be compared.")
            changed = parse_raw_diff(raw)
            changes: dict[str, tuple[str, bytes] | None] = {}
            for path, (status, mode) in changed.items():
                if mode == "160000":
                    raise ToolError("UNSUPPORTED_CHANGE", f"{path} is a git submodule; submodule changes cannot be opened as a pull request here.")
                changes[path] = None if status == "D" else (mode, await workspace.read_bytes(path))
        except SandboxUnavailable as exc:
            raise ToolError(exc.code, "The sandbox did not answer.") from None
        if not changes:
            raise ToolError("NOTHING_TO_COMMIT", "The workspace has no changes compared with the repository's pinned commit.")
        try:
            result = await open_draft_pull_request(pull_requests, repository["repo_id"], base_sha=base_sha, changes=changes,
                title=args.title, body=args.body, branch=f"agentforge/task-{execution.task.task_id.hex}", base_ref=repository.get("base_ref"))
        except GitHubError as exc:
            if str(exc) == "github_base_branch_changed":
                raise ToolError("GITHUB_BASE_CHANGED", "The target branch moved since this workspace was loaded. Your sandbox edits are preserved. Reconcile them with the updated branch before publishing; do not blindly retry.") from None
            raise ToolError("GITHUB_WRITE_FAILED", f"GitHub publication could not be confirmed ({exc}). A branch or draft pull request may already exist; do not retry blindly.") from None
        return ok(pr_url=result["pr_url"], pr_number=result["pr_number"], branch=result["branch"], files=len(changes))

    # Whichever call comes first also starts the sandbox and loads the repository into it, so every deadline includes that
    # setup; each operation inside is bounded on its own.
    for name, handler, timeout in (("bash", bash, 3700), ("read", read, 60), ("write", write, 90), ("edit", edit, 90),
                                   ("grep", grep, 90), ("find", find, 90), ("ls", ls, 60)):
        catalog.register(name, handler, max_cost_usd=Decimal(0), timeout_sec=timeout + SETUP_SEC)
    if pull_requests is not None:
        catalog.register("create_pull_request", create_pull_request, max_cost_usd=Decimal(0), timeout_sec=300 + SETUP_SEC,
                         preview=changed_files)
