"""Bounded coding task selection. Only the server resolves refs to immutable SHAs."""
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator

ProfileId = Literal["python_unittest", "python_pytest", "node_test", "node_npm_test", "node_build"]

class RepoSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    repo_id: str = Field(min_length=3, max_length=200, pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    ref: str = Field(default="main", min_length=1, max_length=200)

    @field_validator("ref")
    @classmethod
    def safe_ref(cls, value):
        from agentforge.security.policies import validate_ref
        try:
            return validate_ref(value)
        except PermissionError as exc:
            raise ValueError("unsafe repository ref") from exc

class CodingTaskSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    repository: RepoSelection
    issue_number: int | None = Field(default=None, ge=1)
    test_profiles: Annotated[tuple[ProfileId, ...], Field(min_length=1, max_length=4)] = ("python_pytest",)
    command_profiles: Annotated[tuple[ProfileId, ...], Field(max_length=4)] = ()
    max_repair_attempts: int = Field(default=2, ge=0, le=2)

    @field_validator("test_profiles", "command_profiles")
    @classmethod
    def distinct_profiles(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("duplicate execution profiles")
        return value

    @field_validator("test_profiles")
    @classmethod
    def tests_only(cls, value):
        if "node_build" in value:
            raise ValueError("build belongs in command_profiles; select at least one test profile")
        return value
