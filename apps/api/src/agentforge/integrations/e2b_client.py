"""E2B execution: fresh, network-denied VM per command, always finite-lived.

No repository command runs in this process. SDK injected in tests; the production
default is the real pinned E2B AsyncSandbox, never an in-process emulator.
"""

import asyncio
import base64
import hashlib
import json
import os
import re
import shlex
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from agentforge.sandbox.contracts import CommandResult, SandboxUnavailable
from agentforge.sandbox.profiles import get_profile
from agentforge.sandbox.workspace import validate_workspace, workspace_revision


@dataclass(frozen=True)
class E2BConfig:
    api_key: str | None = field(default=None, repr=False)
    template_id: str | None = None
    template_qualified: bool = False
    command_timeout_sec: float = 120
    request_timeout_sec: float = 15
    ttl_sec: int = 300
    max_output_bytes: int = 65536

    def __post_init__(self) -> None:
        if not (1 <= self.command_timeout_sec <= 180 and 1 <= self.request_timeout_sec <= 30
                and 60 <= self.ttl_sec <= 600 and 1024 <= self.max_output_bytes <= 131072):
            raise ValueError("invalid_sandbox_limits")
        if self.ttl_sec < self.command_timeout_sec + 2 * self.request_timeout_sec + 10:
            raise ValueError("sandbox_ttl_too_short")


def redact_output(text: str, *, secrets: tuple[str, ...] = ()) -> str:
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


class _OutputLimit(RuntimeError):
    pass


class _BoundedOutput:
    def __init__(self, limit: int) -> None:
        self.limit, self.used, self.truncated = limit, 0, False
        self.stdout: list[str] = []
        self.stderr: list[str] = []

    def append(self, stream: str, data: str) -> None:
        raw = data.encode("utf-8")
        remaining = max(0, self.limit - self.used)
        getattr(self, stream).append(raw[:remaining].decode("utf-8", errors="ignore"))
        self.used += min(len(raw), remaining)
        if len(raw) > remaining:
            self.truncated = True
            raise _OutputLimit("sandbox_output_limit")


class E2BRunner:
    def __init__(self, config: E2BConfig, *, sandbox_factory: Any = None) -> None:
        self.config = config
        self._factory = sandbox_factory

    async def _terminate(self, sandbox: Any) -> bool:
        async def kill() -> bool:
            try:
                async with asyncio.timeout(self.config.request_timeout_sec):
                    # False means already absent, which is also confirmed cleanup.
                    await sandbox.kill(request_timeout=self.config.request_timeout_sec, retries=0)
                return True
            except Exception:
                return False

        task = asyncio.create_task(kill())
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            # Finish bounded cleanup even when the caller cancels execution.
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                pass  # task still has its own finite timeout; TTL is final backstop
            raise

    async def run(self, files: Mapping[str, bytes], profile_id: str, *, operation_id: str,
                  authority: Callable[[], Awaitable[None]]) -> CommandResult:
        snapshot = validate_workspace(files)
        profile = get_profile(profile_id)
        if not isinstance(operation_id, str) or not 1 <= len(operation_id) <= 256:
            raise ValueError("invalid_sandbox_operation")
        if not self.config.api_key or not self.config.template_id or not self.config.template_qualified:
            raise SandboxUnavailable("E2B_NOT_CONFIGURED")
        if os.environ.get("E2B_SANDBOX_URL"):
            raise SandboxUnavailable("E2B_UNSAFE_ENDPOINT_OVERRIDE")
        # Lazy import keeps health/read-only endpoints independent of paid providers.
        from e2b import AsyncSandbox, CommandExitException, TimeoutException

        factory = self._factory or AsyncSandbox
        revision = workspace_revision(snapshot)
        output = _BoundedOutput(self.config.max_output_bytes)
        sandbox = None
        exit_code = None
        timed_out = False
        cleanup_confirmed = False
        started = time.monotonic()
        await authority()
        try:
            async with asyncio.timeout(self.config.request_timeout_sec + 1):
                sandbox = await factory.create(
                    template=self.config.template_id, timeout=self.config.ttl_sec,
                    metadata={"agentforge_operation": hashlib.sha256(operation_id.encode()).hexdigest(),
                              "agentforge_revision": revision},
                    envs={}, secure=True, allow_internet_access=False,
                    # The hosted API rejects IPv6 CIDRs; trusted template preparation
                    # must disable IPv6 per invocation before untrusted execution.
                    network={"deny_out": ["0.0.0.0/0"], "allow_public_traffic": False},
                    lifecycle={"on_timeout": "kill", "auto_resume": False},
                    api_key=self.config.api_key, request_timeout=self.config.request_timeout_sec,
                    retries=0, debug=False, domain="e2b.app", api_url="https://api.e2b.app",
                )
            await authority()
            manifest = json.dumps({"version": 1, "revision": revision, "profile_id": profile.id,
                "files": {p: base64.b64encode(v).decode("ascii") for p, v in snapshot.items()}},
                separators=(",", ":"))
            async with asyncio.timeout(self.config.request_timeout_sec + 1):
                await sandbox.files.write("/tmp/agentforge-import.json", manifest,
                    user="root", request_timeout=self.config.request_timeout_sec)
            # Trusted root-owned helper only parses bounded bytes and fixes ownership.
            # Repository content is never executed with this user.
            async with asyncio.timeout(self.config.request_timeout_sec + 1):
                prepared = await sandbox.commands.run(
                    "/usr/local/bin/python3 -I /opt/agentforge/prepare_workspace.py",
                    user="root", cwd="/", envs={},
                    timeout=self.config.request_timeout_sec,
                    request_timeout=self.config.request_timeout_sec,
                    on_stdout=lambda chunk: output.append("stdout", chunk),
                    on_stderr=lambda chunk: output.append("stderr", chunk),
                )
            if prepared.exit_code != 0 or prepared.stdout.strip() != f"prepared:{revision}":
                raise SandboxUnavailable("E2B_TEMPLATE_INVALID")
            # Preparation diagnostics do not become command evidence.
            output = _BoundedOutput(self.config.max_output_bytes)
            await authority()
            try:
                async with asyncio.timeout(self.config.command_timeout_sec + 1):
                    result = await sandbox.commands.run(
                        "/usr/local/bin/python3 -I /opt/agentforge/run_profile.py " + shlex.quote(profile.id),
                        # Only the fixed trusted launcher starts as root; it drops to
                        # UID/GID 10001 before executing any repository code.
                        user="root", cwd="/", envs={},
                        timeout=self.config.command_timeout_sec,
                        request_timeout=self.config.request_timeout_sec,
                        on_stdout=lambda chunk: output.append("stdout", chunk),
                        on_stderr=lambda chunk: output.append("stderr", chunk),
                    )
                exit_code = result.exit_code
            except CommandExitException as exc:
                exit_code = exc.exit_code
            except (TimeoutError, TimeoutException):
                timed_out = True
            except Exception:
                # The SDK wraps callback exceptions; the local flag remains authoritative.
                if not output.truncated:
                    raise
        except asyncio.CancelledError:
            raise
        except (TimeoutError, TimeoutException):
            if sandbox is None:
                # Allocation outcome is ambiguous. No duplicate allocation retry.
                raise SandboxUnavailable("E2B_ALLOCATION_UNKNOWN") from None
            raise SandboxUnavailable("E2B_INITIALIZATION_TIMEOUT") from None
        except SandboxUnavailable:
            raise
        except Exception:
            if sandbox is None:
                raise SandboxUnavailable("E2B_ALLOCATION_UNKNOWN") from None
            raise SandboxUnavailable("E2B_OPERATION_UNKNOWN") from None
        finally:
            if sandbox is not None:
                cleanup_confirmed = await self._terminate(sandbox)
        stdout = redact_output("".join(output.stdout), secrets=(self.config.api_key,))
        stderr = redact_output("".join(output.stderr), secrets=(self.config.api_key,))
        # Redaction replacements can be longer than short secrets. Reapply the
        # shared byte ceiling after redaction before returning any stored output.
        bounded = _BoundedOutput(self.config.max_output_bytes)
        try:
            bounded.append("stdout", stdout)
            bounded.append("stderr", stderr)
        except _OutputLimit:
            output.truncated = True
        stdout, stderr = "".join(bounded.stdout), "".join(bounded.stderr)
        return CommandResult(provider="e2b", sandbox_id=sandbox.sandbox_id,
            profile_id=profile.id, argv=profile.argv, revision=revision, exit_code=exit_code,
            stdout=stdout, stderr=stderr, duration_sec=time.monotonic() - started,
            timed_out=timed_out, output_truncated=output.truncated,
            cleanup_confirmed=cleanup_confirmed, cost_usd=None)
