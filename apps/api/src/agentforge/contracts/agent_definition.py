"""Immutable agent configurations. No contract can grant external writes."""

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, allow_inf_nan=False)


ToolName = Literal[
    "delegate_task",
    "web_search", "fetch_webpage", "read_repo_file", "search_repo", "get_issue",
    "list_repo_files", "read_sandbox_file", "write_sandbox_file", "edit_sandbox_file", "git_diff",
    "run_sandbox_command", "run_tests", "get_test_results", "get_pr_status",
    "browser_public_page", "read_task_input", "read_research_source", "read_task_artifact", "create_task_artifact",
    "search_conversation_history", "read_context_resource",
    # The coding tools, working in the conversation's sandbox. (The fixed-profile sandbox tools above are retired but
    # stay valid names, so definitions saved earlier keep loading.)
    "bash", "read", "write", "edit", "grep", "find", "ls", "create_pull_request",
    # Offered to every conversation run whatever its definition lists (see tools/catalog.py AMBIENT_TOOLS); valid here so a definition may name it.
    "use_skill",
]
# Every agent now answers in plain text. The older typed ids stay valid so stored definitions keep loading; they have no effect.
OutputSchemaId = Literal["text_v1", "research_result_v1", "execution_result_v1", "review_result_v1", "assessment_result_v1", "general_result_v1", "routing_result_v1"]
ResourceIds = Annotated[tuple[Annotated[str, Field(min_length=1, max_length=256)], ...], Field(strict=False, max_length=200)]


class Permissions(StrictContract):
    sandbox_read: bool = False
    sandbox_exec: bool = False
    sandbox_write: bool = False
    github_read: bool = False
    github_write: bool = False
    web: bool = False
    browser: bool = False
    # None means inherit resources ONLY in a definition. Authorization ceilings
    # must provide concrete sets; an empty set always denies that resource.
    allowed_repo_ids: ResourceIds | None = None
    allowed_sandbox_ids: ResourceIds | None = None
    allowed_context_refs: ResourceIds | None = None
    allowed_command_profiles: ResourceIds | None = None
    allowed_web_hosts: ResourceIds | None = None

    @model_validator(mode="after")
    def distinct_resources(self) -> Self:
        for name in type(self).model_fields:
            value = getattr(self, name)
            if isinstance(value, tuple) and len(value) != len(set(value)):
                raise ValueError(f"duplicate {name}")
        if self.github_write:
            raise ValueError("GitHub writes are not available to sub-agents")
        return self


class Budget(StrictContract):
    """Not a limit on a run. Usage is recorded against a reservation row that needs positive numbers, so these are
    set high enough never to stop anything; a person stops a run with Stop."""
    max_steps: int = Field(default=1_000_000, ge=1)
    max_llm_cost_usd: float = Field(default=100_000, gt=0)
    max_wall_time_sec: int = Field(default=7 * 24 * 3600, ge=1)
    max_tool_calls: int = Field(default=1_000_000, ge=1)


TOOL_CAPABILITIES: dict[str, tuple[str, ...]] = {
    "delegate_task": (),
    "web_search": ("web",), "fetch_webpage": ("web",),
    "read_repo_file": ("github_read",), "search_repo": ("github_read",),
    "get_issue": ("github_read",), "list_repo_files": ("github_read",),
    "get_pr_status": ("github_read",),
    "read_sandbox_file": ("sandbox_read",), "git_diff": ("sandbox_read",),
    "write_sandbox_file": ("sandbox_write",), "edit_sandbox_file": ("sandbox_write",),
    "run_sandbox_command": ("sandbox_exec",), "run_tests": ("sandbox_exec",),
    "get_test_results": (), "browser_public_page": ("web", "browser"),
    "read_task_input": (), "read_research_source": (), "read_task_artifact": (), "create_task_artifact": (),
    "search_conversation_history": (), "read_context_resource": (),
    "bash": ("sandbox_exec",), "read": ("sandbox_read",), "grep": ("sandbox_read",), "find": ("sandbox_read",),
    "ls": ("sandbox_read",), "write": ("sandbox_write",), "edit": ("sandbox_write",),
    "create_pull_request": ("sandbox_read", "github_read"),
    "use_skill": (),
}


class AgentDefinition(StrictContract):
    name: str = Field(min_length=3, max_length=64, pattern=r"^[a-z][a-z0-9-]+$")
    description: str = Field(min_length=10, max_length=400)
    version: int = Field(ge=1, le=2_147_483_647)  # an integer column: a larger value is a 422, not a database error
    system_prompt: str = Field(min_length=30, max_length=10000)
    allowed_tools: Annotated[tuple[ToolName, ...], Field(strict=False, min_length=1, max_length=40)]
    permissions: Permissions
    budget: Budget = Budget()
    output_schema_id: OutputSchemaId = "text_v1"
    model_profile: Literal["default", "research", "coding", "review"] = "default"
    enabled: bool = True

    @model_validator(mode="after")
    def validate_capabilities(self) -> Self:
        if len(self.allowed_tools) != len(set(self.allowed_tools)):
            raise ValueError("duplicate tool names")
        for tool in self.allowed_tools:
            if any(not getattr(self.permissions, flag) for flag in TOOL_CAPABILITIES[tool]):
                raise ValueError(f"tool {tool} requires its capability grants")
        return self
