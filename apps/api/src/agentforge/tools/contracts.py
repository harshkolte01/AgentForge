"""Closed tool argument contracts; adapters receive validated models, never code."""

import json
from decimal import Decimal
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


class ToolArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, allow_inf_nan=False)


ResourceId = Annotated[str, Field(min_length=1, max_length=256)]
Path = Annotated[str, Field(min_length=1, max_length=1024)]
Ref = Annotated[str, Field(min_length=1, max_length=256)]


class WebSearchArgs(ToolArguments):
    query: str = Field(min_length=1, max_length=2000)
    max_results: int = Field(default=5, ge=1, le=10)


class FetchWebpageArgs(ToolArguments):
    source_url: str = Field(min_length=8, max_length=2048)


class RepoArgs(ToolArguments):
    repo_id: ResourceId


class RepoFileArgs(RepoArgs):
    ref: Ref
    path: Path
    line_start: int = Field(default=1, ge=1, description="One-based first line; resume with next_line from the previous result.")
    max_lines: int = Field(default=200, ge=1, le=200, description="Complete lines requested; returned content is also capped at 10000 UTF-8 bytes.")


class RepoSearchArgs(RepoArgs):
    ref: Ref
    query: str = Field(min_length=1, max_length=2000)


class RepoFilesArgs(RepoArgs):
    ref: Ref
    path: Path = "."


class IssueArgs(RepoArgs):
    issue_number: int = Field(ge=1)


class PRStatusArgs(RepoArgs):
    pr_number: int = Field(ge=1)


class SandboxArgs(ToolArguments):
    sandbox_id: ResourceId


class SandboxFileArgs(SandboxArgs):
    path: Path


class SandboxReadArgs(SandboxFileArgs):
    line_start: int = Field(default=1, ge=1, description="One-based first line; resume with next_line from the previous result.")
    max_lines: int = Field(default=200, ge=1, le=200, description="Complete lines requested; returned content is also capped at 10000 UTF-8 bytes.")


class SandboxWriteArgs(SandboxFileArgs):
    content: str = Field(max_length=100_000)
    # Future sandbox adapters must compare this hash before a write.
    expected_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class GitDiffArgs(SandboxArgs):
    base_ref: Ref


class SandboxCommandArgs(SandboxArgs):
    command_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_\-]+$")
    args: Annotated[tuple[Annotated[str, Field(max_length=512)], ...], Field(strict=False, max_length=20)] = ()


class RunTestsArgs(SandboxArgs):
    test_profile: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_\-]+$")


class TestResultsArgs(ToolArguments):
    task_id: ResourceId
    run_id: ResourceId


class BrowserArgs(ToolArguments):
    url: str = Field(min_length=8, max_length=2048)
    checks: Annotated[tuple[Literal["title", "screenshot", "links", "accessibility"], ...], Field(strict=False, max_length=4)] = ("title",)


ARGUMENT_MODELS: dict[str, type[ToolArguments]] = {
    "web_search": WebSearchArgs, "fetch_webpage": FetchWebpageArgs,
    "read_repo_file": RepoFileArgs, "search_repo": RepoSearchArgs,
    "get_issue": IssueArgs, "list_repo_files": RepoFilesArgs,
    "read_sandbox_file": SandboxReadArgs, "write_sandbox_file": SandboxWriteArgs,
    "git_diff": GitDiffArgs, "run_sandbox_command": SandboxCommandArgs,
    "run_tests": RunTestsArgs, "get_test_results": TestResultsArgs,
    "get_pr_status": PRStatusArgs, "browser_public_page": BrowserArgs,
}


class ToolOutcome(BaseModel):
    """Adapter output. IDs require independent task ownership verification."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    status: Literal["ok", "error"]
    data: dict[str, JsonValue] = Field(default_factory=dict)
    evidence_ids: tuple[ResourceId, ...] = ()
    artifact_ids: tuple[ResourceId, ...] = ()
    # None means the remote charge is unknown, retaining the operation cap.
    cost_usd: Decimal | None
    error_code: str | None = Field(default=None, max_length=64, pattern=r"^[A-Z_]+$")

    @model_validator(mode="after")
    def bounded(self) -> Self:
        if self.cost_usd is not None and (not self.cost_usd.is_finite() or self.cost_usd < 0):
            raise ValueError("invalid tool cost")
        if self.status == "error" and self.error_code is None:
            raise ValueError("tool errors require a safe code")
        if len(self.evidence_ids) > 100 or len(self.artifact_ids) > 100:
            raise ValueError("too many result references")
        if len(json.dumps(self.model_dump(mode="json"), allow_nan=False).encode()) > 262144:
            raise ValueError("tool result exceeds bounded storage limit")
        return self


# Every request variant has its own argument schema; arbitrary tool names do not
# become executable merely because a provider emits them.
class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class WebSearchRequest(_Request):
    name: Literal["web_search"]
    args: WebSearchArgs


class FetchRequest(_Request):
    name: Literal["fetch_webpage"]
    args: FetchWebpageArgs


class RepoFileRequest(_Request):
    name: Literal["read_repo_file"]
    args: RepoFileArgs


class RepoSearchRequest(_Request):
    name: Literal["search_repo"]
    args: RepoSearchArgs


class IssueRequest(_Request):
    name: Literal["get_issue"]
    args: IssueArgs


class RepoFilesRequest(_Request):
    name: Literal["list_repo_files"]
    args: RepoFilesArgs


class SandboxFileRequest(_Request):
    name: Literal["read_sandbox_file"]
    args: SandboxReadArgs


class SandboxWriteRequest(_Request):
    name: Literal["write_sandbox_file"]
    args: SandboxWriteArgs


class DiffRequest(_Request):
    name: Literal["git_diff"]
    args: GitDiffArgs


class CommandRequest(_Request):
    name: Literal["run_sandbox_command"]
    args: SandboxCommandArgs


class TestsRequest(_Request):
    name: Literal["run_tests"]
    args: RunTestsArgs


class TestResultsRequest(_Request):
    name: Literal["get_test_results"]
    args: TestResultsArgs


class PRStatusRequest(_Request):
    name: Literal["get_pr_status"]
    args: PRStatusArgs


class BrowserRequest(_Request):
    name: Literal["browser_public_page"]
    args: BrowserArgs


ToolRequest = Annotated[
    WebSearchRequest | FetchRequest | RepoFileRequest | RepoSearchRequest |
    IssueRequest | RepoFilesRequest | SandboxFileRequest | SandboxWriteRequest |
    DiffRequest | CommandRequest | TestsRequest | TestResultsRequest |
    PRStatusRequest | BrowserRequest,
    Field(discriminator="name"),
]
