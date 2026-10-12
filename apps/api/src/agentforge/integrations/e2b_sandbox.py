"""A persistent E2B sandbox for the coding agent: one Linux VM per conversation.

The VM keeps its files between messages (it is paused when idle and resumed on the next call), has internet access,
and holds no secrets: its environment is empty and GitHub credentials never enter it. Repository files are uploaded by
the backend and pull requests are opened by the backend (see tools/pull_request.py), so nothing inside the VM can act
on GitHub. The SDK is injected in tests; production uses the real pinned E2B `AsyncSandbox`.
"""
import asyncio
import json
import logging
import re
import shlex
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

WORKSPACE = "/home/user/workspace"
# A tool result is capped near 16 KB by the gateway; output is cut to fit, keeping the start and the end.
OUTPUT_CHARS = 12000
JSON_BYTES = 12500
COLLECT_CHARS = 2_000_000
# Past COLLECT_CHARS, a command's output keeps its last this many characters too, so the end it is cut to (usually the
# error) is the real end and not wherever the cap fell.
TAIL_CHARS = 100_000
KEEP_ALIVE_EVERY_SEC = 60
# Commands run without a terminal and nobody can answer a prompt: anything that would wait for input, open a pager or ask
# for credentials should fail fast or take the default instead. None of these is a secret.
COMMAND_ENV = {"CI": "1", "DEBIAN_FRONTEND": "noninteractive", "GIT_TERMINAL_PROMPT": "0", "GIT_PAGER": "cat", "PAGER": "cat",
               "TERM": "dumb", "PIP_NO_INPUT": "1", "PIP_DISABLE_PIP_VERSION_CHECK": "1", "npm_config_yes": "true",
               "NO_COLOR": "1", "PYTHONUNBUFFERED": "1"}
# The command's own deadline is enforced inside the VM by `timeout`, which signals the command's whole process group (its
# children too). The connection is given a little longer, so the command ends first and its output so far is returned.
GRACE_SEC = 15
PID_DIR = "/tmp/.agentforge-commands"
# Connecting to an existing VM is retried after a transient failure (the provider briefly unreachable, a 5xx).
OPEN_ATTEMPTS = 3
OPEN_RETRY_SEC = 1.0
# Creating is retried only when the provider says it refused the request (no capacity, rate limit): nothing was allocated then.
CREATE_ATTEMPTS = 3
CREATE_RETRY_SEC = 2.0
# Uploading a repository can take much longer than a small request.
UPLOAD_TIMEOUT_SEC = 180


class SandboxUnavailable(RuntimeError):
    """The sandbox could not be reached or created. `code` is safe to show."""

    def __init__(self, code: str = "SANDBOX_UNAVAILABLE") -> None:
        super().__init__(code)
        self.code = code


def _rejected_key(exc: BaseException) -> bool:
    from e2b import AuthenticationException
    return isinstance(exc, AuthenticationException)


def _failure_code(exc: BaseException, default: str) -> str:
    """A rejected API key will fail every call the same way: say so, instead of a generic failure."""
    return "SANDBOX_AUTH_FAILED" if _rejected_key(exc) else default


@dataclass(frozen=True)
class SandboxConfig:
    api_key: str = field(default="", repr=False)
    template: str | None = None
    ttl_sec: int = 3600
    request_timeout_sec: float = 30


def redact_output(text: str, *, secrets: tuple[str, ...] = ()) -> str:
    """Removes credentials from text a person or a model will read."""
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[REDACTED]")
    text = re.sub(r"-----BEGIN [^-\n]*PRIVATE KEY-----[\s\S]*?(?:-----END [^-\n]*PRIVATE KEY-----|$)",
                  "[REDACTED PRIVATE KEY]", text)
    text = re.sub(r"(?im)(authorization\s*[:=]\s*(?:bearer\s+|basic\s+)?)[^\r\n]+", r"\1[REDACTED]", text)
    text = re.sub(r"(?i)((?:api[_-]?key|access[_-]?token|password|secret)\s*[\"']?\s*[:=]\s*[\"']?)[^\s\"'`,;]+",
                  r"\1[REDACTED]", text)
    text = re.sub(r"\b(?:gh[pousr]_[A-Za-z0-9_]{15,}|github_pat_[A-Za-z0-9_]+|sk-[A-Za-z0-9_-]{16,}|AKIA[A-Z0-9]{16})\b",
                  "[REDACTED]", text)
    return text


def bound_output(text: str, limit: int = OUTPUT_CHARS) -> tuple[str, bool]:
    """Text that fits a tool result: at most `limit` characters and JSON_BYTES once escaped, keeping the first third and
    the last two thirds (the end is usually the error). The flag says something was cut."""
    shrunk = limit
    while True:
        if len(text) <= shrunk:
            candidate = text
        else:
            head, tail = shrunk // 3, shrunk - shrunk // 3
            candidate = f"{text[:head]}\n[… {len(text) - shrunk} characters omitted …]\n{text[-tail:]}"
        if len(json.dumps(candidate, ensure_ascii=False).encode()) <= JSON_BYTES or shrunk <= 200:
            return candidate, candidate != text
        shrunk = int(shrunk * 0.8)


@dataclass(frozen=True)
class CommandOutcome:
    exit_code: int | None
    output: str
    timed_out: bool
    truncated: bool


class _Collector:
    """A stream's output: its first COLLECT_CHARS characters and, with `tail`, a rolling window of its last TAIL_CHARS."""

    def __init__(self, observer: Callable[[str], None] | None = None, *, tail: bool = False) -> None:
        self.parts: list[str] = []
        self.size = 0
        self.observer = observer
        self.keep_tail = tail
        self.tail: list[str] = []
        self.tail_size = 0

    def add(self, chunk: str) -> None:
        if self.size < COLLECT_CHARS:
            self.parts.append(chunk[:COLLECT_CHARS - self.size])
            rest = chunk[COLLECT_CHARS - self.size:]
        else:
            rest = chunk
        self.size += len(chunk)
        if self.keep_tail and rest:
            self.tail.append(rest)
            self.tail_size += len(rest)
            if self.tail_size > 2 * TAIL_CHARS:  # trimmed in batches, so a long stream of small chunks stays linear
                kept = "".join(self.tail)[-TAIL_CHARS:]
                self.tail, self.tail_size = [kept], len(kept)
        if self.observer is not None:
            try:
                self.observer(chunk)
            except Exception:
                pass  # Showing progress must never fail the command.

    def text(self) -> str:
        head = "".join(self.parts)
        if not self.tail:
            return head
        tail = "".join(self.tail)[-TAIL_CHARS:]
        omitted = self.size - len(head) - len(tail)
        return f"{head}\n[… {omitted} characters omitted …]\n{tail}" if omitted else head + tail


class Workspace:
    """One live sandbox. Every operation is a plain shell command or a file transfer inside the VM."""

    def __init__(self, sandbox: Any, config: SandboxConfig, *, clock=time.monotonic) -> None:
        self.sandbox, self.config, self._clock = sandbox, config, clock
        self._alive_at = clock()
        self.recreated = False

    @property
    def sandbox_id(self) -> str:
        return self.sandbox.sandbox_id

    async def keep_alive(self, needed_sec: float = 0) -> None:
        """Pushes the idle timer out, at most once a minute, and always far enough for a command of `needed_sec` to finish
        (a long build must not outlive the VM that runs it)."""
        lifetime = max(self.config.ttl_sec, int(needed_sec) + 300)
        if self._clock() - self._alive_at < KEEP_ALIVE_EVERY_SEC and lifetime <= self.config.ttl_sec:
            return
        self._alive_at = self._clock()
        try:
            await self.sandbox.set_timeout(lifetime, request_timeout=self.config.request_timeout_sec)
        except Exception:
            log.debug("could not extend the sandbox lifetime", exc_info=True)

    async def _stop(self, marker: str) -> None:
        """Ends a command the run gave up on (Stop, or the connection's deadline): its whole process group, then its pid file.
        Best effort and bounded; a VM that does not answer keeps the command until its in-VM deadline ends it."""
        script = (f"p=$(cat {marker} 2>/dev/null) && [ -n \"$p\" ] && {{ kill -TERM -- -$p 2>/dev/null; sleep 2; "
                  f"kill -KILL -- -$p 2>/dev/null; }}; rm -f {marker}; true")
        try:
            await asyncio.wait_for(self.sandbox.commands.run(f"bash -c {shlex.quote(script)}", cwd="/", envs=COMMAND_ENV,
                timeout=20, request_timeout=self.config.request_timeout_sec), timeout=25)
        except BaseException:
            log.debug("could not stop a sandbox command", exc_info=True)

    async def run(self, command: str, *, timeout_sec: float = 120, cwd: str = WORKSPACE,
                  on_output: Callable[[str], None] | None = None) -> CommandOutcome:
        """Runs `command` with bash. A nonzero exit is a normal outcome; only a broken sandbox raises.

        `on_output` receives each piece of the command's output (both streams, as it arrives) while it runs. It is for
        showing progress: it gets the raw text, and anything that raises is ignored.
        """
        from e2b import CommandExitException, SandboxNotRunningException, SandboxUnreachableException, TimeoutException
        out, err = _Collector(on_output, tail=True), _Collector(on_output, tail=True)
        exit_code: int | None = None
        timed_out = False
        limit = max(1, int(timeout_sec))
        marker = f"{PID_DIR}/{uuid.uuid4().hex}"
        # `timeout` becomes the leader of a new process group, so on expiry its TERM (then KILL 5s later) reaches every child.
        # Its pid is recorded first, so a Stop can end the same group from outside.
        wrapped = (f"mkdir -p {PID_DIR} && echo $$ > {marker} && exec timeout -k 5 {limit} bash -c {shlex.quote(command)}")
        await self.keep_alive(limit + GRACE_SEC)
        started = time.monotonic()
        try:
            try:
                result = await self.sandbox.commands.run(f"bash -c {shlex.quote(wrapped)}", cwd=cwd, envs=COMMAND_ENV,
                    timeout=limit + GRACE_SEC, request_timeout=self.config.request_timeout_sec,
                    on_stdout=out.add, on_stderr=err.add)
                exit_code = result.exit_code
            except CommandExitException as exc:
                exit_code = exc.exit_code
            # 124: `timeout` ended the command; 137 at the deadline: its follow-up KILL did (137 earlier is the command's own).
            timed_out = exit_code == 124 or (exit_code == 137 and time.monotonic() - started >= limit)
            if timed_out:
                exit_code = None
        except (SandboxNotRunningException, SandboxUnreachableException) as exc:
            # These are TimeoutExceptions to the SDK, but they say the VM is gone or unreachable, not that the command ran long.
            raise SandboxUnavailable("SANDBOX_COMMAND_FAILED") from exc
        except (TimeoutException, TimeoutError):
            timed_out = True
            await asyncio.shield(self._stop(marker))
        except asyncio.CancelledError:
            await asyncio.shield(self._stop(marker))
            raise
        except Exception as exc:
            raise SandboxUnavailable("SANDBOX_COMMAND_FAILED") from exc
        if timed_out:
            err.add(f"\n[The command was stopped after {limit} seconds. Run long jobs in the background or pass a larger timeout_sec.]")
        stdout, stderr = out.text(), err.text()
        text = stdout + (("\n" if stdout and not stdout.endswith("\n") else "") + stderr if stderr else "")
        text, truncated = bound_output(redact_output(text, secrets=(self.config.api_key,)))
        return CommandOutcome(exit_code, text, timed_out, truncated)

    async def capture(self, command: str, *, timeout_sec: float = 120) -> tuple[int | None, str]:
        """Exact stdout of a trusted command, neither redacted nor shortened. For the backend's own use, never shown as is."""
        from e2b import CommandExitException
        out = _Collector()
        try:
            result = await self.sandbox.commands.run(f"bash -c {shlex.quote(command)}", cwd=WORKSPACE, envs=COMMAND_ENV, timeout=timeout_sec,
                request_timeout=self.config.request_timeout_sec, on_stdout=out.add)
            return result.exit_code, out.text()
        except CommandExitException as exc:
            return exc.exit_code, out.text()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise SandboxUnavailable("SANDBOX_COMMAND_FAILED") from exc

    async def read_bytes(self, path: str) -> bytes:
        return bytes(await self.sandbox.files.read(self.absolute(path), format="bytes", request_timeout=self.config.request_timeout_sec))

    async def read_text(self, path: str) -> str:
        return await self.sandbox.files.read(self.absolute(path), request_timeout=self.config.request_timeout_sec)

    async def write(self, path: str, data: str | bytes, *, timeout_sec: float | None = None) -> None:
        """`timeout_sec` lengthens the request for a large upload; it never shortens the configured one."""
        await self.sandbox.files.write(self.absolute(path), data,
                                       request_timeout=max(self.config.request_timeout_sec, timeout_sec or 0))

    @staticmethod
    def absolute(path: str) -> str:
        return path if path.startswith("/") else f"{WORKSPACE}/{path}"


class SandboxProvider:
    def __init__(self, config: SandboxConfig, *, factory: Any = None) -> None:
        self.config, self._factory = config, factory

    def _sdk(self):
        if self._factory is not None:
            return self._factory
        from e2b import AsyncSandbox
        return AsyncSandbox

    async def open(self, existing_id: str | None, *, metadata: dict[str, str]) -> tuple[Workspace, bool]:
        """The sandbox with `existing_id` (resumed if it was paused), or a new one. The flag says it is new."""
        if not self.config.api_key:
            raise SandboxUnavailable("SANDBOX_NOT_CONFIGURED")
        sdk = self._sdk()
        options = {"api_key": self.config.api_key, "request_timeout": self.config.request_timeout_sec}
        if existing_id:
            for attempt in range(OPEN_ATTEMPTS):
                try:
                    sandbox = await sdk.connect(existing_id, timeout=self.config.ttl_sec, **options)
                    return Workspace(sandbox, self.config), False
                except Exception as exc:
                    from e2b import NotFoundException
                    if isinstance(exc, NotFoundException):
                        log.info("sandbox %s is gone; creating a new one", existing_id)
                        break
                    if _rejected_key(exc) or attempt + 1 >= OPEN_ATTEMPTS:
                        raise SandboxUnavailable(_failure_code(exc, "SANDBOX_CONNECT_FAILED")) from exc
                    await asyncio.sleep(OPEN_RETRY_SEC * (attempt + 1))
        # Not retried after an unknown failure: a failed create may still have allocated (and be billing for) a VM nobody would
        # know about. A refusal (no capacity, rate limit) allocated nothing, so only that is retried.
        from e2b import RateLimitException, ServiceBusyException
        sandbox = None
        for attempt in range(CREATE_ATTEMPTS):
            try:
                sandbox = await sdk.create(template=self.config.template, timeout=self.config.ttl_sec, metadata=metadata,
                    envs={}, secure=True, allow_internet_access=True,
                    lifecycle={"on_timeout": "pause", "auto_resume": True}, **options)
                break
            except (RateLimitException, ServiceBusyException) as exc:
                if attempt + 1 >= CREATE_ATTEMPTS:
                    raise SandboxUnavailable("SANDBOX_BUSY") from exc
                await asyncio.sleep(CREATE_RETRY_SEC * (attempt + 1))
            except Exception as exc:
                raise SandboxUnavailable(_failure_code(exc, "SANDBOX_CREATE_FAILED")) from exc
        workspace = Workspace(sandbox, self.config)
        # The conversation had a sandbox and it is gone: whatever earlier turns left in it is gone too, and the agent is told.
        workspace.recreated = bool(existing_id)
        try:
            for attempt in range(OPEN_ATTEMPTS):
                try:
                    await workspace.run(f"mkdir -p {WORKSPACE}", cwd="/")
                    break
                except SandboxUnavailable:
                    # A new VM can need a moment before it answers.
                    if attempt + 1 >= OPEN_ATTEMPTS:
                        raise
                    await asyncio.sleep(OPEN_RETRY_SEC * (attempt + 1))
        except BaseException:
            # A VM that cannot run its first command is of no use and nobody knows its id yet: stop it rather than leak it.
            await asyncio.shield(self.kill(workspace))
            raise
        return workspace, True

    async def attach(self, existing_id: str | None) -> Workspace | None:
        """The conversation's existing sandbox (resumed if paused), or None when there is none any more.

        Unlike `open` it never creates one: the API uses it to look at a conversation's files, and must not start (and pay
        for) an empty VM just because somebody opened a panel. Any other failure raises `SandboxUnavailable`.
        """
        if not self.config.api_key:
            raise SandboxUnavailable("SANDBOX_NOT_CONFIGURED")
        if not existing_id:
            return None
        sdk = self._sdk()
        options = {"api_key": self.config.api_key, "request_timeout": self.config.request_timeout_sec}
        for attempt in range(OPEN_ATTEMPTS):
            try:
                return Workspace(await sdk.connect(existing_id, timeout=self.config.ttl_sec, **options), self.config)
            except Exception as exc:
                from e2b import NotFoundException
                if isinstance(exc, NotFoundException):
                    return None
                if _rejected_key(exc) or attempt + 1 >= OPEN_ATTEMPTS:
                    raise SandboxUnavailable(_failure_code(exc, "SANDBOX_CONNECT_FAILED")) from exc
                await asyncio.sleep(OPEN_RETRY_SEC * (attempt + 1))
        return None

    async def kill(self, workspace: Workspace) -> None:
        try:
            await workspace.sandbox.kill(request_timeout=self.config.request_timeout_sec)
        except Exception:
            log.debug("could not stop the sandbox", exc_info=True)
