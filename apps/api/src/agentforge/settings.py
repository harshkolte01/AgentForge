"""Typed configuration loaded from environment variables and local .env files."""

import logging
from functools import lru_cache
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AliasChoices, Field, HttpUrl, SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

API_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = API_DIR.parent.parent
logger = logging.getLogger(__name__)


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
    # Unset means the origin of `app_base_url` (see `check_consistency`).
    allowed_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)
    database_url: str = Field(
        default="postgresql+psycopg://agentforge:change-me@localhost:5432/agentforge",
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
    # A single tool call can carry a whole file (write, create_task_artifact): 16K tokens leaves room for about 50 KB of
    # text. A final answer longer than this is continued, not cut.
    llm_max_output_tokens: int = Field(default=16384, ge=128, le=128000)
    # The longest wait for the next piece of a reply (a reasoning model may think a while before its first token).
    llm_timeout_sec: float = Field(default=180, gt=0, le=900)
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
    llm_max_retries: int = Field(default=4, ge=0, le=8)
    llm_reasoning_effort: Literal["none", "minimal", "low", "medium", "high", "xhigh"] | None = None
    worker_lease_sec: int = Field(default=90, ge=30, le=300)
    worker_heartbeat_sec: int = Field(default=10, ge=2, le=30)
    worker_poll_sec: float = Field(default=2, ge=0.2, le=30)
    # Tasks one worker process runs at once: a run waiting on an "Ask me" approval must not hold up everyone else.
    worker_concurrency: int = Field(default=4, ge=1, le=32)
    # Seconds a stopping worker lets running tasks finish before cancelling them (each is then recorded as interrupted).
    worker_drain_sec: float = Field(default=25, ge=0, le=300)
    database_pool_size: int = Field(default=10, ge=1, le=100)
    database_max_overflow: int = Field(default=10, ge=0, le=100)
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
    # GitHub logins allowed to sign in (case-insensitive); they get an account on first sign-in even with public
    # signup off. Empty means no allowlist: existing accounts, plus anyone while public_signup_enabled.
    github_allowed_logins: Annotated[list[str], NoDecode] = Field(default_factory=list)
    # GitHub user tokens last 8h and refresh silently, so the session is what bounds a sign-in.
    session_absolute_ttl_sec: int = Field(default=604800, ge=300, le=2592000)
    session_idle_ttl_sec: int = Field(default=86400, ge=60, le=604800)
    local_user_id: UUID | None = None
    local_bootstrap_token: SecretStr | None = Field(default=None, repr=False, min_length=32)
    local_session_ttl_sec: int = Field(default=86400, ge=60, le=604800)

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def parse_allowed_origins(cls, value: object) -> object:
        """Accept comma-separated origins in .env or a list in Python."""
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @field_validator("github_allowed_logins", mode="before")
    @classmethod
    def parse_allowed_logins(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.split(",")
        if isinstance(value, (list, tuple)):
            return [str(login).strip().removeprefix("@").lower() for login in value if str(login).strip()]
        return value

    @model_validator(mode="after")
    def check_consistency(self) -> "Settings":
        """Fail fast on unusable combinations. Messages name fields only, never values."""
        app_origin = origin(self.app_base_url)
        if "allowed_origins" not in self.model_fields_set:
            self.allowed_origins = [app_origin]
        elif app_origin not in self.allowed_origins:
            logger.warning("ALLOWED_ORIGINS does not include the origin of APP_BASE_URL; the web app's requests will be refused.")
        problems = []
        if self.auth_mode == "github_app":
            missing = [name for name in ("github_client_id", "github_client_secret", "github_app_id") if not getattr(self, name)]
            if missing:
                problems.append("AUTH_MODE=github_app requires " + ", ".join(name.upper() for name in missing))
            key = self.github_token_encryption_key
            if key is None or len(key.get_secret_value()) < 32:
                problems.append("AUTH_MODE=github_app requires GITHUB_TOKEN_ENCRYPTION_KEY of at least 32 characters")
            if not self.github_app_slug:
                logger.warning("GITHUB_APP_SLUG is not set; Settings cannot link users to install the GitHub App.")
        if self.app_env == "production":
            if self.auth_mode != "github_app":
                problems.append("APP_ENV=production requires AUTH_MODE=github_app")
            for name in ("app_base_url", "api_public_base_url"):
                if getattr(self, name).scheme != "https":
                    problems.append(f"APP_ENV=production requires an https {name.upper()}")
            if any(not value.startswith("https://") for value in self.allowed_origins):
                problems.append("APP_ENV=production requires every ALLOWED_ORIGINS entry to use https")
        if problems:
            raise ValueError("; ".join(problems))
        return self


def origin(url: HttpUrl) -> str:
    default = {"http": 80, "https": 443}.get(url.scheme)
    return f"{url.scheme}://{url.host}" + ("" if url.port in {None, default} else f":{url.port}")


def validation_summary(error: ValidationError) -> str:
    """One line per invalid setting, without the rejected values (they may be secrets)."""
    return "\n".join(f"  {'.'.join(str(part) for part in item['loc']) or 'settings'}: {item['msg']}"
        for item in error.errors(include_input=False, include_url=False))


@lru_cache
def get_settings() -> Settings:
    """Load settings once per process; clear the cache after configuration changes."""
    return Settings()
