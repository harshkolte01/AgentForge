"""Typed tool errors the model can act on.

Adapters raise ToolError for a deterministic, effect-free rejection (a missing file, a stale hash,
an ambiguous edit). The gateway turns it into a normal error outcome carrying a stable code plus a
short detail written by our own code, never provider text or user content, so the model can correct
itself instead of guessing why a call failed.

Raise ToolError only before the adapter has caused any external effect: the gateway treats it as a
definite no-op, whereas any other failure of an effectful tool is an UNKNOWN remote outcome.
"""

from typing import Any

from pydantic import ValidationError

MAX_DETAIL = 500
MAX_VALIDATION_ISSUES = 6


class ToolError(ValueError):
    """A definite, effect-free failure with a stable code and a model-safe explanation."""

    def __init__(self, code: str, detail: str, *, message: str | None = None,
                 issues: list[dict[str, str]] | None = None) -> None:
        super().__init__(message or detail)
        self.code = code
        self.detail = detail[:MAX_DETAIL]
        self.issues = issues


def _location(parts: tuple[Any, ...]) -> str:
    return ".".join(str(part) for part in parts) or "(arguments)"


def validation_issues(error: ValidationError) -> list[dict[str, str]]:
    """Field-level problems with the offending input removed.

    Pydantic messages describe the constraint ("String should have at most 100 characters"), not the
    value, and input values are dropped explicitly, so no argument content is echoed back or stored.
    """
    issues: list[dict[str, str]] = []
    for item in error.errors(include_input=False, include_url=False, include_context=False):
        issues.append({"field": _location(tuple(item.get("loc", ()))), "problem": str(item.get("msg", "invalid"))[:160]})
        if len(issues) >= MAX_VALIDATION_ISSUES:
            break
    return issues


def render_issues(issues: list[dict[str, str]]) -> str:
    return "; ".join(f"{issue['field']}: {issue['problem']}" for issue in issues)[:MAX_DETAIL]
