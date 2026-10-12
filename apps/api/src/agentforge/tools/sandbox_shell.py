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
import time
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select, update

from agentforge.database.models import Task
from agentforge.database.sessions import Conversation, TurnExecution
from agentforge.integrations.e2b_sandbox import (
    UPLOAD_TIMEOUT_SEC, WORKSPACE, SandboxProvider, SandboxUnavailable, Workspace, bound_output,
)
from agentforge.integrations.github_client import GitHubError, is_transient

from .contracts import ToolOutcome
from .errors import ToolError
from .repository_errors import describe_skipped, repository_error

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
    now = int(time.time())  # an mtime of 0 (1970) breaks tools that cannot represent it, e.g. building a wheel ("before 1980")
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for path, data in files.items():
            info = tarfile.TarInfo(path)
            info.size = len(data)
            info.mtime = now
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
        # A repository that can never be loaded (not found, too large) fails the same way every time: said once per run, not
        # downloaded again for every tool call. A transient failure (GitHub busy) is not kept, so the next call tries again.
        self._refused: GitHubError | None = None
        # Said once, with the first result after it happened, so the agent never assumes earlier work is still there.
        self.notice: str | None = None

    def forget(self) -> None:
        """The workspace stopped answering: the next call reconnects instead of reusing a dead connection."""
        self._workspace = None

    def add_notice(self, text: str) -> None:
        self.notice = f"{self.notice} {text}" if self.notice else text

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
                if self._refused is not None:
                    raise self._refused
                try:
                    self._workspace = await self._open(task_id)
                except GitHubError as error:
                    if not is_transient(error):
                        self._refused = error
                    raise
            await self._workspace.keep_alive()
            return self._workspace

    async def _open(self, task_id: UUID) -> Workspace:
        session_id, sandbox_id, repository = await asyncio.to_thread(self._lookup, task_id)
        workspace, created = await self.provider.open(sandbox_id, metadata={"agentforge_task": str(task_id)})
        # Remembered at once: a sandbox the conversation does not know about is a VM nobody can find again if a later step fails.
        if session_id and (created or workspace.sandbox_id != sandbox_id):
            await asyncio.to_thread(self._remember, session_id, workspace.sandbox_id)
        loaded = None if created else await self._loaded_repository(workspace)
        if repository and loaded and loaded != repository["repo_id"]:
            # The person switched repositories: files of one must never be mixed into the other.
            await self.provider.kill(workspace)
            workspace, created = await self.provider.open(None, metadata={"agentforge_task": str(task_id)})
            loaded = None
            if session_id:
                await asyncio.to_thread(self._remember, session_id, workspace.sandbox_id)
        if repository and not loaded:
            await self._load_repository(workspace, repository)
        if getattr(workspace, "recreated", False):
            self.add_notice("This conversation's earlier sandbox had expired, so a new one was created: files, installed packages and "
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
        failed = []
        for index, file in enumerate(files):
            try:
                if file.get("upload_id"):
                    record = await asyncio.to_thread(self.attachment_storage.load, owner, file)
                    data = base64.b64decode(record["data"])
                else:
                    data = file["content"].encode()
                await workspace.write(workspace_attachment_path(task_id, index, file["name"]), data, timeout_sec=UPLOAD_TIMEOUT_SEC)
            except Exception:
                # One attachment that cannot be placed must not take the whole sandbox away from the agent.
                log.warning("attachment %d could not be placed in the sandbox", index, exc_info=True)
                failed.append(str(file.get("name", index))[:80])
        if failed:
            self.add_notice("These attachments could not be copied into the workspace: " + ", ".join(failed) + ".")

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
                                                                               getattr(snapshot, "modes", None)),
                              timeout_sec=UPLOAD_TIMEOUT_SEC)
        # The marker is written last: it says the repository is completely loaded, so a failed attempt is redone, not trusted.
        script = (f"set -e; tar -xzf /tmp/agentforge-repo.tgz -C {WORKSPACE}; rm -f /tmp/agentforge-repo.tgz; cd {WORKSPACE}; "
                  "mkdir -p .agentforge; "
                  "git init -q; git config user.email agentforge@users.noreply.github.com; git config user.name AgentForge; "
                  "printf '.agentforge/\\n' >> .git/info/exclude; git add -A; git commit -q -m base --allow-empty; "
                  f"git tag -f {BASE_TAG} >/dev/null; "
                  f"printf '%s' {shlex.quote(repository['repo_id'] + '@' + snapshot.commit_sha)} > {MARKER}")
        outcome = await workspace.run(script, timeout_sec=300, cwd="/")
        if outcome.exit_code != 0:
            log.warning("loading the repository into the sandbox failed (exit %s): %s", outcome.exit_code, outcome.output[-1000:])
            raise SandboxUnavailable("REPOSITORY_LOAD_FAILED")
        note = describe_skipped(getattr(snapshot, "skipped", ()))
        if note:
            self.add_notice(note)

    def repository(self, task_id: UUID) -> dict | None:
        with self.sessions() as db:
            grants = db.scalar(select(Task.permissions_json).where(Task.id == task_id)) or {}
        repository = grants.get("repository")
        if not repository:
            return None
        return {**repository, "base_ref": ((grants.get("automatic") or {}).get("repository") or {}).get("ref")}

    def owner(self, task_id: UUID) -> tuple[UUID | None, UUID | None]:
        """(conversation, person) a task belongs to."""
        with self.sessions() as db:
            session_id = db.scalar(select(TurnExecution.session_id).where(TurnExecution.task_id == task_id))
            user_id = db.scalar(select(Task.created_by).where(Task.id == task_id))
        return session_id, user_id

    def opened_workspace(self) -> Workspace | None:
        """The workspace this run used, or None if it never touched the sandbox (nothing can have changed then)."""
        return self._workspace


# Refusals that happen before anything is written to GitHub: the model can say, truthfully, that nothing was published.
NOT_PUBLISHED = {
    "delivery_unavailable": "Publishing hit a server fault (the details are in the server log).",
    "github_writes_disabled": "Publishing to GitHub is turned off on this server (ENABLE_GITHUB_WRITES).",
    "github_not_connected": "GitHub is not connected for this account.",
    "delivery_sandbox_not_configured": "No sandbox is configured on this server.",
    "github_write_permission_required": "The person's GitHub connection or the App installation does not allow writing to this repository.",
    "github_repository_not_authorized": "The person's GitHub connection no longer gives access to this repository.",
    "github_contents_permission_required": "The GitHub App installation lacks the Contents permission.",
    "github_reauthorization_required": "The person must reconnect GitHub.",
    "github_access_denied": "GitHub refused access.",
    "github_rate_limited": "GitHub is rate limiting requests right now.",
    "delivery_operation_in_progress": "Another publish for this conversation is still being confirmed.",
}


def register_sandbox_shell(catalog, sandbox: ConversationSandbox, *, delivery=None) -> None:
    """`delivery` is a DeliveryService able to write to GitHub (delivery/service.py), or None: then no pull request tool exists."""
    async def opened(execution) -> Workspace:
        try:
            return await sandbox.workspace(execution.task.task_id)
        except SandboxUnavailable as exc:
            log.warning("sandbox unavailable (%s)", exc.code, exc_info=exc.__cause__ is not None)
            if exc.code.startswith("REPOSITORY_"):
                raise ToolError(exc.code, "The sandbox started, but the selected repository could not be loaded into it, so no file or "
                                "command tool can run right now.") from None
            raise ToolError(exc.code, "The sandbox could not be started, so no file or command tool can run right now.") from None
        except GitHubError as exc:
            log.warning("repository could not be loaded into the sandbox (%s)", exc)
            raise repository_error(exc) from None
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
            outcome = await workspace.run(args.command, timeout_sec=args.timeout_sec, on_output=execution.output)
        except SandboxUnavailable:
            # Only the sandbox could have been affected, and it can be inspected: the agent checks and decides, the run goes on.
            sandbox.forget()
            raise ToolError("SANDBOX_DISCONNECTED", "The connection to the sandbox dropped, so the command may or may not have run. "
                            "Check its effects (files, processes) before running it again.") from None
        return ok(exit_code=outcome.exit_code, output=outcome.output, timed_out=outcome.timed_out, truncated=outcome.truncated)

    async def read_file(workspace: Workspace, path: str, missing: str) -> str:
        """The file's text. Only an answer that the file is not there (or is not a file) is FILE_NOT_FOUND: a sandbox that
        timed out or failed says nothing about the file, and reading it as "missing" could lead the model to overwrite it."""
        from e2b import FileNotFoundException, InvalidArgumentException
        try:
            return await workspace.read_text(path)
        except asyncio.CancelledError:
            raise
        except (FileNotFoundException, InvalidArgumentException, FileNotFoundError, IsADirectoryError, UnicodeDecodeError):
            raise ToolError("FILE_NOT_FOUND", missing) from None
        except Exception:
            sandbox.forget()
            raise ToolError("SANDBOX_UNAVAILABLE", f"The sandbox did not answer, so {path} could not be read and nothing was changed. "
                            "Try again; it reconnects on the next call.") from None

    async def read(args, execution):
        workspace = await opened(execution)
        text = await read_file(workspace, args.path, f"{args.path} does not exist or is not a text file. Use ls or find to see what is there.")
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
        text = await read_file(workspace, args.path, f"{args.path} does not exist or is not a text file.")
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
        # pipefail keeps grep's own status (1 no match, 2 error) visible through `head`; -s hides messages about unreadable files.
        command = (f"set -o pipefail; grep {flags} -s {excludes} {include} -e {shlex.quote(args.pattern)} -- {shlex.quote(args.path)} "
                   f"| head -n {args.max_results}")
        outcome = await shell(workspace, command)
        # 2 is grep's "could not run", but it is also its status when some file was unreadable while others matched; 141 is
        # grep stopped by `head` once enough lines were found. Only 2 with nothing found (or only its own message) is a failure.
        if outcome.exit_code == 2 and (not outcome.output or outcome.output.startswith("grep:")):
            raise ToolError("INVALID_ARGUMENT", "grep could not run: the pattern is not a valid extended regular expression "
                            "(escape special characters such as ( [ { with a backslash), or the path does not exist.")
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
        """For the approval card: every file the publish would send, with line counts (read only: git works in a private index)."""
        from agentforge.delivery.workspace import measure
        workspace = await opened(execution)
        try:
            measured = await measure(workspace, patches=False)
        except SandboxUnavailable:
            return "Changed files: could not be listed because the sandbox did not answer."
        if not measured.files:
            return "Changed files: none compared with what is already published (an existing pull request is only opened or kept)."
        rows = [f"{f.path} | {f.status} +{f.additions} -{f.deletions}" + (f" [cannot be published: {f.blocked}]" if f.blocked else "")
                for f in measured.files[:200]]
        more = f"\n… and {len(measured.files) - 200} more" if len(measured.files) > 200 else ""
        # CI configuration runs with the repository's own secrets once the branch is pushed: worth a second look.
        warning = ("\n\nNote: this changes CI configuration (.github/), which runs with the repository's secrets."
                   if any(f.path.startswith(".github/") for f in measured.files) else "")
        return "Changed files:\n" + "\n".join(rows) + more + warning

    async def create_pull_request(args, execution):
        from agentforge.delivery.service import DeliveryError
        repository = await asyncio.to_thread(sandbox.repository, execution.task.task_id)
        if not repository:
            raise ToolError("NO_REPOSITORY", "No repository is selected for this conversation, so there is nothing to open a pull request against.")
        session_id, user_id = await asyncio.to_thread(sandbox.owner, execution.task.task_id)
        if session_id is None or user_id is None:
            raise ToolError("NO_REPOSITORY", "This task does not belong to a conversation, so there is nothing to publish.")
        workspace = await opened(execution)
        try:
            # The delivery commits on the conversation's own branch (created on first use) and opens the pull request once; a
            # later call adds a commit to the same branch. The same operation key finds an earlier attempt instead of repeating it.
            result = await delivery.publish_for_agent(session_id, user_id, workspace, key=execution.operation_key, title=args.title,
                body=args.body, task_id=execution.task.task_id)
        except DeliveryError as exc:
            code = exc.code
            if code == "nothing_to_publish":
                raise ToolError("NOTHING_TO_COMMIT", "The workspace has no changes compared with what is already published.") from None
            if code.startswith("delivery_blocked_paths:"):
                raise ToolError("UNSUPPORTED_CHANGE", "These changed files cannot be published (secrets, symlinks, submodules or unsafe paths): "
                                f"{code.split(':', 1)[1]}. Remove them from the workspace, then try again.") from None
            if code.startswith("github_path_conflict:"):
                raise ToolError("GITHUB_CONFLICT", "These files were changed on GitHub since the last push, so they were not overwritten: "
                                f"{code.split(':', 1)[1]}. Tell the person; they can update the branch or ask you to redo the change.") from None
            if code in NOT_PUBLISHED:
                raise ToolError("GITHUB_UNAVAILABLE", NOT_PUBLISHED[code] + " Nothing was published. Tell the person; do not retry unchanged.") from None
            if code in {"delivery_no_repository", "delivery_workspace_expired"}:
                raise ToolError("NO_REPOSITORY", "The workspace is not a checkout of the selected repository, so its changes cannot be compared.") from None
            if code in {"github_branch_moved", "github_branch_missing", "github_publish_branch_exists"}:
                raise ToolError("GITHUB_BASE_CHANGED", "The conversation's branch changed or disappeared on GitHub. Your sandbox edits are preserved. "
                                "Tell the person instead of retrying.") from None
            raise ToolError("GITHUB_WRITE_FAILED", f"GitHub publication could not be confirmed ({code}). A branch or draft pull request may already "
                            "exist; do not retry blindly.") from None
        except SandboxUnavailable as exc:
            raise ToolError(exc.code, "The sandbox did not answer.") from None
        return ok(pr_url=result["pr_url"], pr_number=result["pr_number"], branch=result["branch"])

    # Whichever call comes first also starts the sandbox and loads the repository into it, so every deadline includes that
    # setup; each operation inside is bounded on its own.
    for name, handler, timeout in (("bash", bash, 3700), ("read", read, 60), ("write", write, 90), ("edit", edit, 90),
                                   ("grep", grep, 90), ("find", find, 90), ("ls", ls, 60)):
        catalog.register(name, handler, max_cost_usd=Decimal(0), timeout_sec=timeout + SETUP_SEC)
    if delivery is not None:
        catalog.register("create_pull_request", create_pull_request, max_cost_usd=Decimal(0), timeout_sec=300 + SETUP_SEC,
                         preview=changed_files)
