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
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

WORKSPACE = "/home/user/workspace"
# A tool result is capped near 16 KB by the gateway; output is cut to fit, keeping the start and the end.
OUTPUT_CHARS = 12000
JSON_BYTES = 12500
COLLECT_CHARS = 2_000_000
KEEP_ALIVE_EVERY_SEC = 60


class SandboxUnavailable(RuntimeError):
    """The sandbox could not be reached or created. `code` is safe to show."""

    def __init__(self, code: str = "SANDBOX_UNAVAILABLE") -> None:
        super().__init__(code)
        self.code = code


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
    def __init__(self) -> None:
        self.parts: list[str] = []
        self.size = 0

    def add(self, chunk: str) -> None:
        if self.size < COLLECT_CHARS:
            self.parts.append(chunk[:COLLECT_CHARS - self.size])
            self.size += len(chunk)

    def text(self) -> str:
        return "".join(self.parts)


class Workspace:
    """One live sandbox. Every operation is a plain shell command or a file transfer inside the VM."""

    def __init__(self, sandbox: Any, config: SandboxConfig, *, clock=time.monotonic) -> None:
        self.sandbox, self.config, self._clock = sandbox, config, clock
        self._alive_at = clock()

    @property
    def sandbox_id(self) -> str:
        return self.sandbox.sandbox_id

    async def keep_alive(self) -> None:
        """Pushes the idle timer out, at most once a minute."""
        if self._clock() - self._alive_at < KEEP_ALIVE_EVERY_SEC:
            return
        self._alive_at = self._clock()
        try:
            await self.sandbox.set_timeout(self.config.ttl_sec, request_timeout=self.config.request_timeout_sec)
        except Exception:
            log.debug("could not extend the sandbox lifetime", exc_info=True)

    async def run(self, command: str, *, timeout_sec: float = 120, cwd: str = WORKSPACE) -> CommandOutcome:
        """Runs `command` with bash. A nonzero exit is a normal outcome; only a broken sandbox raises."""
        from e2b import CommandExitException, TimeoutException
        out, err = _Collector(), _Collector()
        exit_code: int | None = None
        timed_out = False
        try:
            result = await self.sandbox.commands.run(f"bash -c {shlex.quote(command)}", cwd=cwd, envs={}, timeout=timeout_sec,
                request_timeout=self.config.request_timeout_sec, on_stdout=out.add, on_stderr=err.add)
            exit_code = result.exit_code
        except CommandExitException as exc:
            exit_code = exc.exit_code
        except (TimeoutException, TimeoutError):
            timed_out = True
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise SandboxUnavailable("SANDBOX_COMMAND_FAILED") from exc
        stdout, stderr = out.text(), err.text()
        text = stdout + (("\n" if stdout and not stdout.endswith("\n") else "") + stderr if stderr else "")
        text, truncated = bound_output(redact_output(text, secrets=(self.config.api_key,)))
        return CommandOutcome(exit_code, text, timed_out, truncated)

    async def capture(self, command: str, *, timeout_sec: float = 120) -> tuple[int | None, str]:
        """Exact stdout of a trusted command, neither redacted nor shortened. For the backend's own use, never shown as is."""
        from e2b import CommandExitException
        out = _Collector()
        try:
            result = await self.sandbox.commands.run(f"bash -c {shlex.quote(command)}", cwd=WORKSPACE, envs={}, timeout=timeout_sec,
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

    async def write(self, path: str, data: str | bytes) -> None:
        await self.sandbox.files.write(self.absolute(path), data, request_timeout=self.config.request_timeout_sec)

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
            try:
                sandbox = await sdk.connect(existing_id, timeout=self.config.ttl_sec, **options)
                return Workspace(sandbox, self.config), False
            except Exception as exc:
                from e2b import NotFoundException
                if not isinstance(exc, NotFoundException):
                    raise SandboxUnavailable("SANDBOX_CONNECT_FAILED") from exc
                log.info("sandbox %s is gone; creating a new one", existing_id)
        try:
            sandbox = await sdk.create(template=self.config.template, timeout=self.config.ttl_sec, metadata=metadata,
                envs={}, secure=True, allow_internet_access=True,
                lifecycle={"on_timeout": "pause", "auto_resume": True}, **options)
        except Exception as exc:
            raise SandboxUnavailable("SANDBOX_CREATE_FAILED") from exc
        workspace = Workspace(sandbox, self.config)
        await workspace.run(f"mkdir -p {WORKSPACE}", cwd="/")
        return workspace, True

    async def kill(self, workspace: Workspace) -> None:
        try:
            await workspace.sandbox.kill(request_timeout=self.config.request_timeout_sec)
        except Exception:
            log.debug("could not stop the sandbox", exc_info=True)
