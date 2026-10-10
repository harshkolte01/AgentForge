"""Ownership boundary for immutable context, evidence and artifact references."""

from typing import Protocol

from agentforge.tools.contracts import ToolArguments

from .scope import ScopeDenied, TaskContext


class ResourceResolver(Protocol):
    async def load_context(self, ctx: TaskContext, references: tuple[str, ...]) -> dict[str, str]: ...

    async def verify_references(self, ctx: TaskContext, *, evidence_ids: tuple[str, ...], artifact_ids: tuple[str, ...]) -> None: ...

    async def verify_tool_inputs(self, ctx: TaskContext, name: str, args: ToolArguments) -> None: ...


class DenyUnknownResources:
    """Phase 2 has no evidence store. Missing ownership integration denies IDs."""

    async def load_context(self, ctx: TaskContext, references: tuple[str, ...]) -> dict[str, str]:
        if references:
            raise ScopeDenied("context ownership resolver is not installed")
        return {}

    async def verify_references(self, ctx: TaskContext, *, evidence_ids: tuple[str, ...], artifact_ids: tuple[str, ...]) -> None:
        if evidence_ids or artifact_ids:
            raise ScopeDenied("evidence ownership resolver is not installed")

    async def verify_tool_inputs(self, ctx: TaskContext, name: str, args: ToolArguments) -> None:
        if name == "get_test_results":
            raise ScopeDenied("test-run ownership resolver is not installed")
