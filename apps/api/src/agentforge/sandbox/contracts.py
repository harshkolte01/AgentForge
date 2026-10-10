"""Execution consumes an immutable snapshot; providers never execute on the host."""

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol


class SandboxUnavailable(RuntimeError):
    """Provider is unavailable or an operation has an unknown remote outcome."""


@dataclass(frozen=True)
class CommandResult:
    provider: str
    sandbox_id: str
    profile_id: str
    argv: tuple[str, ...]
    # Digest of the uploaded, read-only input; never a claimed output revision.
    revision: str
    exit_code: int | None
    stdout: str
    stderr: str
    duration_sec: float
    timed_out: bool
    output_truncated: bool
    cleanup_confirmed: bool
    cost_usd: Decimal | None = None


class SandboxRunner(Protocol):
    async def run(self, files: Mapping[str, bytes], profile_id: str, *,
                  operation_id: str,
                  authority: Callable[[], Awaitable[None]]) -> CommandResult: ...
