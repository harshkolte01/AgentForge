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
    query: str = Field(min_length=1, max_length=2000, description="What to search for. Be specific: include names, versions, dates or exact terms. Do not repeat a query that already ran.")
    max_results: int = Field(default=5, ge=1, le=10, description="How many results to return (1-10). The default of 5 is usually enough.")


class FetchWebpageArgs(ToolArguments):
    source_url: str = Field(min_length=8, max_length=2048, description="Full https URL of the public page to open, copied exactly from a search result or from the user.")


class RepoArgs(ToolArguments):
    repo_id: ResourceId = Field(description="Repository identifier in owner/name form, exactly as given in the brief.")


class RepoFileArgs(RepoArgs):
    ref: Ref = Field(description="The pinned commit SHA from the brief. Do not use a branch name.")
    path: Path = Field(description="File path relative to the repository root, as returned by list_repo_files or search_repo.")
    line_start: int = Field(default=1, ge=1, description="One-based first line; resume with next_line from the previous result.")
    max_lines: int = Field(default=200, ge=1, le=200, description="Complete lines requested; returned content is also capped at 10000 UTF-8 bytes.")


class RepoSearchArgs(RepoArgs):
    ref: Ref = Field(description="The pinned commit SHA from the brief.")
    query: str = Field(min_length=1, max_length=2000, description="Text to find. Case-insensitive substring match, not a regular expression.")


class RepoFilesArgs(RepoArgs):
    ref: Ref = Field(description="The pinned commit SHA from the brief.")
    path: Path = Field(default=".", description="Directory to list, relative to the repository root. Use '.' for the root.")


class IssueArgs(RepoArgs):
    issue_number: int = Field(ge=1, description="The issue number, without the leading #.")


class PRStatusArgs(RepoArgs):
    pr_number: int = Field(ge=1, description="The pull request number, without the leading #.")


class SandboxArgs(ToolArguments):
    sandbox_id: ResourceId = Field(description="The sandbox_id given in the brief. Never invent one.")


class SandboxFileArgs(SandboxArgs):
    path: Path = Field(description="File path relative to the workspace root.")


class SandboxReadArgs(SandboxFileArgs):
    line_start: int = Field(default=1, ge=1, description="One-based first line; resume with next_line from the previous result.")
    max_lines: int = Field(default=200, ge=1, le=200, description="Complete lines requested; returned content is also capped at 10000 UTF-8 bytes.")


class SandboxWriteArgs(SandboxFileArgs):
    content: str = Field(max_length=100_000, description="The complete new content of the file, not a patch.")
    # Future sandbox adapters must compare this hash before a write.
    expected_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$", description="Required to overwrite an existing file: the sha256 returned by your most recent read_sandbox_file of that path. Omit it only to create a new file.")


class GitDiffArgs(SandboxArgs):
    base_ref: Ref = Field(description="The base commit SHA given in the brief.")


class SandboxCommandArgs(SandboxArgs):
    command_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_\-]+$", description="One of the command profiles listed in the brief, such as node_build.")
    args: Annotated[tuple[Annotated[str, Field(max_length=512)], ...], Field(strict=False, max_length=20)] = Field(default=(), description="Must stay empty. Command profiles do not accept arguments.")


class RunTestsArgs(SandboxArgs):
    test_profile: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_\-]+$", description="One of the test profiles listed in the brief, such as python_pytest or node_npm_test.")


class TestResultsArgs(ToolArguments):
    task_id: ResourceId = Field(description="The parent_task_id given in the brief.")
    run_id: ResourceId = Field(description="The evidence id of the test or build run, as returned by run_tests or run_sandbox_command, or listed in the brief.")


class BrowserArgs(ToolArguments):
    url: str = Field(min_length=8, max_length=2048, description="Full https URL of the public page to render.")
    checks: Annotated[tuple[Literal["title", "screenshot", "links", "accessibility"], ...], Field(strict=False, max_length=4)] = Field(default=("title",), description="Which observations to return. Request only what you need.")


class PageArgs(ToolArguments):
    offset: int = Field(default=0, ge=0, le=2_000_000, description="Character offset to start from. Start at 0, then pass the previous result's next_offset.")
    max_chars: int = Field(default=6000, ge=1, le=8000, description="Page size in characters (1-8000).")


class TaskInputArgs(PageArgs):
    input_kind: Literal["goal", "attachment", "history"] = Field(description="goal = the user's current message; attachment = a file they attached; history = the earlier conversation, oldest first.")
    attachment_index: int = Field(default=0, ge=0, le=3, description="Zero-based attachment number. Only used when input_kind is attachment.")


class ResearchSourceArgs(PageArgs):
    source_id: ResourceId = Field(description="A source_id returned by web_search, fetch_webpage or a repository tool in this task.")


class TaskArtifactReadArgs(PageArgs):
    artifact_id: ResourceId = Field(description="An artifact id returned by create_task_artifact in this task.")


class TaskArtifactCreateArgs(ToolArguments):
    format: Literal["text", "markdown", "csv"] = Field(description="markdown for documents and reports, csv for tables, text for plain text.")
    content: str = Field(min_length=1, max_length=60000, description="The complete finished content of the file.")
    title: str = Field(min_length=1, max_length=120, description="A short human-readable title for the file.")


ARGUMENT_MODELS: dict[str, type[ToolArguments]] = {
    "read_task_input": TaskInputArgs, "read_research_source": ResearchSourceArgs,
    "read_task_artifact": TaskArtifactReadArgs, "create_task_artifact": TaskArtifactCreateArgs,
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


class TaskInputRequest(_Request):
    name: Literal["read_task_input"]
    args: TaskInputArgs


class ResearchSourceRequest(_Request):
    name: Literal["read_research_source"]
    args: ResearchSourceArgs


class TaskArtifactReadRequest(_Request):
    name: Literal["read_task_artifact"]
    args: TaskArtifactReadArgs


class TaskArtifactCreateRequest(_Request):
    name: Literal["create_task_artifact"]
    args: TaskArtifactCreateArgs


ToolRequest = Annotated[
    WebSearchRequest | FetchRequest | RepoFileRequest | RepoSearchRequest |
    IssueRequest | RepoFilesRequest | SandboxFileRequest | SandboxWriteRequest |
    DiffRequest | CommandRequest | TestsRequest | TestResultsRequest |
    PRStatusRequest | BrowserRequest | TaskInputRequest | ResearchSourceRequest |
    TaskArtifactReadRequest | TaskArtifactCreateRequest,
    Field(discriminator="name"),
]
