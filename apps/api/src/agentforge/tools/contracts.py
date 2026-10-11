"""Closed tool argument contracts; adapters receive validated models, never code."""

import json
from decimal import Decimal
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator


class ToolArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, allow_inf_nan=False)


class PluginArguments(BaseModel):
    """The arguments of a plugin's tool. Their shape belongs to the plugin's server, so any JSON object is accepted here and the
    server checks it; they never reach the built-in tools' path, repository or URL rules."""
    model_config = ConfigDict(extra="allow", frozen=True, allow_inf_nan=False)


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


def _directory_prefix(value: str | None) -> str | None:
    if not isinstance(value, str):
        return value
    cleaned = value.strip().removeprefix("./").rstrip("/")
    return None if cleaned in ("", ".") else cleaned


class RepoSearchArgs(RepoArgs):
    ref: Ref = Field(description="The pinned commit SHA from the brief.")
    query: str = Field(min_length=1, max_length=2000, description="Text to find, matched as a plain substring (not a regular expression).")
    path_prefix: Path | None = Field(default=None, description="Only search files under this directory, such as 'src/api'. Omit to search the whole repository.")
    case_sensitive: bool = Field(default=False, description="Match letter case exactly. Default is case-insensitive.")
    context_lines: int = Field(default=0, ge=0, le=3, description="Lines of surrounding code to return with each match (0-3).")
    max_results: int = Field(default=30, ge=1, le=50, description="Maximum matches to return (1-50).")

    _prefix = field_validator("path_prefix", mode="after")(_directory_prefix)


class RepoFilesArgs(RepoArgs):
    ref: Ref = Field(description="The pinned commit SHA from the brief.")
    path: Path = Field(default=".", description="Directory to list, relative to the repository root. Use '.' for the root.")
    max_depth: int | None = Field(default=None, ge=1, le=10, description="Show only this many directory levels; deeper folders are summarised as 'folder/ (N files)'. Omit to list every file.")


class IssueArgs(RepoArgs):
    issue_number: int = Field(ge=1, description="The issue number, without the leading #.")


class PRStatusArgs(RepoArgs):
    pr_number: int = Field(ge=1, description="The pull request number, without the leading #.")


class BashArgs(ToolArguments):
    command: str = Field(min_length=1, max_length=20_000, description="The shell command, run with bash in the workspace directory. Chain steps with && and avoid commands that wait for input.")
    timeout_sec: int = Field(default=120, ge=1, le=3600, description="Seconds before the command is stopped. Raise it for installs and long test runs.")


class FileReadArgs(ToolArguments):
    path: Path = Field(description="File path relative to the workspace root.")
    offset: int = Field(default=1, ge=1, description="One-based first line. Continue with next_offset from the previous result.")
    limit: int = Field(default=400, ge=1, le=2000, description="Maximum lines to return; the result is also capped at about 12000 characters.")


class FileWriteArgs(ToolArguments):
    path: Path = Field(description="File path relative to the workspace root. Missing folders are created.")
    content: str = Field(max_length=200_000, description="The complete new content of the file, not a patch. Replaces the file if it exists.")


class FileEditArgs(ToolArguments):
    path: Path = Field(description="File path relative to the workspace root.")
    old_string: str = Field(min_length=1, max_length=20_000, description="The exact existing text to replace, including whitespace and indentation, copied from the file. It must match one place unless replace_all is true, so include enough surrounding lines to be unique.")
    new_string: str = Field(max_length=50_000, description="The replacement text. Use an empty string to delete old_string.")
    replace_all: bool = Field(default=False, description="Replace every occurrence of old_string instead of requiring exactly one match.")


class GrepArgs(ToolArguments):
    pattern: str = Field(min_length=1, max_length=500, description="Regular expression (extended syntax) to search for in file contents.")
    path: Path = Field(default=".", description="File or folder to search, relative to the workspace root.")
    glob: str | None = Field(default=None, max_length=200, description="Only search files whose name matches this glob, such as '*.py'.")
    ignore_case: bool = Field(default=False, description="Match letter case loosely.")
    max_results: int = Field(default=100, ge=1, le=500, description="Maximum matching lines to return.")


class FindArgs(ToolArguments):
    pattern: str = Field(min_length=1, max_length=200, description="File name glob, such as '*.test.ts' or 'package.json'.")
    path: Path = Field(default=".", description="Folder to search under, relative to the workspace root.")
    max_results: int = Field(default=200, ge=1, le=1000, description="Maximum paths to return.")


class LsArgs(ToolArguments):
    path: Path = Field(default=".", description="Folder to list, relative to the workspace root.")


class PullRequestArgs(ToolArguments):
    title: str = Field(min_length=1, max_length=200, description="Pull request title.")
    body: str = Field(default="", max_length=8000, description="Pull request description: what changed and why, and how it was checked.")


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
    filename: str | None = Field(default=None, max_length=120, description="Download filename, not a workspace path. Use letters, numbers, spaces, underscores, hyphens and dots; end in .txt for text, .md for markdown or .csv for csv. Preserve the user's requested filename. Null uses the default name.")

    @model_validator(mode="after")
    def safe_filename(self) -> Self:
        from agentforge.contracts.artifact_names import valid_artifact_filename
        if self.filename is not None and not valid_artifact_filename(self.filename, self.format):
            raise ValueError("filename must be a safe basename with the extension matching format")
        return self


class ConversationSearchArgs(ToolArguments):
    query: str = Field(max_length=200, description="Words in an earlier message or an attachment/deliverable name. Empty lists messages.")
    cursor: int = Field(default=0, ge=0, description="Resume message results at next_cursor, or 0 for the first page.")
    limit: int = Field(default=10, ge=1, le=20, description="Maximum messages and resource references to return (1-20).")


class UseSkillArgs(ToolArguments):
    name: str = Field(min_length=1, max_length=64, description="The skill's exact name as listed in the system prompt, for example code-review, without a leading slash.")

    @field_validator("name", mode="before")
    @classmethod
    def _bare_name(cls, value):
        return value.strip().lstrip("/").strip() if isinstance(value, str) else value


class ContextResourceArgs(PageArgs):
    resource_id: ResourceId = Field(description="Opaque resource_id from history, search, or a compacted tool result.")


class DelegateTaskArgs(ToolArguments):
    title: str = Field(min_length=3, max_length=100, description="Short assignment label, e.g. Review authentication.")
    prompt: str = Field(min_length=10, max_length=8000, description="Self-contained assignment, relevant context, paths, constraints and expected result. The child does not receive your conversation.")
    mode: Literal["research", "work"] = Field(default="research", description="research allows reading and web research only; work also permits workspace edits and commands within your grants.")

    @field_validator("title", "prompt")
    @classmethod
    def meaningful(cls, value):
        if not value.strip():
            raise ValueError("must contain non-whitespace text")
        return value.strip()


ARGUMENT_MODELS: dict[str, type[ToolArguments]] = {
    "delegate_task": DelegateTaskArgs,
    "search_conversation_history": ConversationSearchArgs, "read_context_resource": ContextResourceArgs,
    "read_task_input": TaskInputArgs, "read_research_source": ResearchSourceArgs,
    "read_task_artifact": TaskArtifactReadArgs, "create_task_artifact": TaskArtifactCreateArgs,
    "web_search": WebSearchArgs, "fetch_webpage": FetchWebpageArgs,
    "read_repo_file": RepoFileArgs, "search_repo": RepoSearchArgs,
    "get_issue": IssueArgs, "list_repo_files": RepoFilesArgs,
    "bash": BashArgs, "read": FileReadArgs, "write": FileWriteArgs, "edit": FileEditArgs,
    "grep": GrepArgs, "find": FindArgs, "ls": LsArgs, "create_pull_request": PullRequestArgs,
    "get_pr_status": PRStatusArgs, "browser_public_page": BrowserArgs, "use_skill": UseSkillArgs,
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
    # Safe, human-written explanation of an error, shown to the model next to the static guidance.
    error_detail: str | None = Field(default=None, max_length=600)

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


class DelegateTaskRequest(_Request):
    name: Literal["delegate_task"]
    args: DelegateTaskArgs


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


class BashRequest(_Request):
    name: Literal["bash"]
    args: BashArgs


class ReadRequest(_Request):
    name: Literal["read"]
    args: FileReadArgs


class WriteRequest(_Request):
    name: Literal["write"]
    args: FileWriteArgs


class EditRequest(_Request):
    name: Literal["edit"]
    args: FileEditArgs


class GrepRequest(_Request):
    name: Literal["grep"]
    args: GrepArgs


class FindRequest(_Request):
    name: Literal["find"]
    args: FindArgs


class LsRequest(_Request):
    name: Literal["ls"]
    args: LsArgs


class PullRequestRequest(_Request):
    name: Literal["create_pull_request"]
    args: PullRequestArgs


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


class ConversationSearchRequest(_Request):
    name: Literal["search_conversation_history"]
    args: ConversationSearchArgs


class ContextResourceRequest(_Request):
    name: Literal["read_context_resource"]
    args: ContextResourceArgs


class UseSkillRequest(_Request):
    name: Literal["use_skill"]
    args: UseSkillArgs


ToolRequest = Annotated[
    DelegateTaskRequest | WebSearchRequest | FetchRequest | RepoFileRequest | RepoSearchRequest |
    IssueRequest | RepoFilesRequest | BashRequest | ReadRequest | WriteRequest | EditRequest |
    GrepRequest | FindRequest | LsRequest | PullRequestRequest |
    PRStatusRequest | BrowserRequest | TaskInputRequest | ResearchSourceRequest |
    TaskArtifactReadRequest | TaskArtifactCreateRequest | ConversationSearchRequest | ContextResourceRequest | UseSkillRequest,
    Field(discriminator="name"),
]
