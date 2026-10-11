"""Typed configuration loaded from environment variables and local .env files."""

from functools import lru_cache
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AliasChoices, Field, HttpUrl, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

API_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = API_DIR.parent.parent


class Settings(BaseSettings):
    """Process environment overrides API .env, which overrides repository .env."""

    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", API_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        env_ignore_empty=True,
    )

    app_env: Literal["development", "test", "production"] = "development"
    app_base_url: HttpUrl = "http://localhost:3000"
    api_public_base_url: HttpUrl = "http://localhost:8000"
    allowed_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000"]
    )
    database_url: str = Field(
        default="postgresql+psycopg://agentforge:change-me@localhost:5432/agentforge",
        min_length=1,
        repr=False,
    )
    langgraph_checkpoint_db_url: str = Field(
        default="postgresql://agentforge:change-me@localhost:5432/agentforge",
        min_length=1,
        repr=False,
    )
    # Phase 2 is deliberately unconfigured until the operator verifies a model,
    # its feature support, context limit and current pricing. Health routes do
    # not create a provider client or make paid requests.
    llm_provider: Literal["openai"] = "openai"
    llm_model: str | None = Field(default=None, min_length=1, max_length=200)
    llm_api_key: SecretStr | None = Field(default=None, repr=False)
    llm_supports_tools: bool = False
    llm_supports_json: bool = False
    llm_context_window_tokens: int | None = Field(default=None, ge=8192, le=2_000_000)
    # A single tool call can carry a whole file (write_sandbox_file, create_task_artifact), so the
    # default leaves room for roughly 25 KB of text; 2048 truncated anything larger mid-call.
    llm_max_output_tokens: int = Field(default=8192, ge=128, le=16384)
    llm_timeout_sec: float = Field(default=60, gt=0, le=120)
    llm_input_usd_per_million: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    llm_output_usd_per_million: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    llm_long_context_input_usd_per_million: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    llm_long_context_output_usd_per_million: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    # Let the model request several independent read-only tools in one turn.
    llm_parallel_tool_calls: bool = True
    # Send tool schemas in the provider's strict structured-output form. Verify the configured
    # model accepts it before enabling; the gateway validates every call either way.
    llm_strict_tools: bool = False
    # Retries for failures that produced no completion (rate limit, 5xx, connection refused).
    llm_max_retries: int = Field(default=2, ge=0, le=5)
    llm_reasoning_effort: Literal["none", "minimal", "low", "medium", "high", "xhigh"] | None = None
    worker_lease_sec: int = Field(default=90, ge=30, le=300)
    worker_heartbeat_sec: int = Field(default=10, ge=2, le=30)
    worker_poll_sec: float = Field(default=2, ge=0.2, le=30)
    conversation_queue_enabled: bool = True
    conversation_summaries_enabled: bool = True
    # Name a new conversation from what it is about, with one small model call before its first reply.
    conversation_titles_enabled: bool = True
    neatlogs_api_key: SecretStr | None = Field(default=None, repr=False)
    neatlogs_workflow_name: str = Field(default="agentforge-ai", min_length=1, max_length=100)
    tavily_api_key: SecretStr | None = Field(default=None, repr=False)
    tavily_search_max_cost_usd: Decimal = Field(default=Decimal("0.02"), gt=0, allow_inf_nan=False)
    tavily_usd_per_credit: Decimal | None = Field(default=None, gt=0, allow_inf_nan=False)
    research_storage_path: Path = REPO_ROOT / ".local" / "artifacts"
    research_fetch_timeout_sec: float = Field(default=15, gt=0, le=30)
    research_max_response_bytes: int = Field(default=1_000_000, ge=1024, le=2_000_000)
    # The coding sandbox. With a key, the agent gets bash, file and search tools; the template is optional (E2B's default
    # image has git, Python and Node). The sandbox is paused after `e2b_sandbox_ttl_sec` idle seconds and resumed on use.
    e2b_api_key: SecretStr | None = Field(default=None, repr=False)
    e2b_template_id: str | None = Field(default=None, min_length=1, max_length=200)
    e2b_request_timeout_sec: float = Field(default=30, gt=0, le=120)
    e2b_sandbox_ttl_sec: int = Field(default=3600, ge=60, le=86400)
    auth_mode: Literal["disabled", "local_single_user", "github_app"] = "disabled"
    github_client_id: str | None = Field(default=None, min_length=1, max_length=200)
    github_client_secret: SecretStr | None = Field(default=None, repr=False)
    github_app_id: int | None = Field(default=None, gt=0)
    github_app_slug: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9-]+$", max_length=100)
    github_webhook_secret: SecretStr | None = Field(default=None, repr=False, min_length=32)
    github_token_encryption_key: SecretStr | None = Field(default=None, repr=False,
        validation_alias=AliasChoices("GITHUB_TOKEN_ENCRYPTION_KEY", "ENCRYPTION_KEY", "github_token_encryption_key"))
    github_oauth_callback_url: HttpUrl | None = None
    enable_github_writes: bool = False
    github_api_version: str = Field(default="2026-03-10", pattern=r"^\d{4}-\d{2}-\d{2}$")
    public_signup_enabled: bool = True
    session_absolute_ttl_sec: int = Field(default=86400, ge=300, le=604800)
    session_idle_ttl_sec: int = Field(default=1800, ge=60, le=86400)
    local_user_id: UUID | None = None
    local_bootstrap_token: SecretStr | None = Field(default=None, repr=False, min_length=32)
    local_session_ttl_sec: int = Field(default=3600, ge=60, le=86400)

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def parse_allowed_origins(cls, value: object) -> object:
        """Accept comma-separated origins in .env or a list in Python."""
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    """Load settings once per process; clear the cache after configuration changes."""
    return Settings()
