"""Trusted runtime composition; construction alone performs no provider requests."""
from dataclasses import dataclass
from agentforge.llm.router import ModelRouter
from agentforge.repositories.runs import AgentRunRepository, ToolCallRepository
from agentforge.security.policies import AuthorizationService
from agentforge.subagents.budget import BudgetService
from agentforge.subagents.registry import AgentRegistry
from agentforge.subagents.spawn_service import SpawnService
from agentforge.tools.catalog import ToolCatalog, DESCRIPTIONS
from agentforge.tools.research import register_research
from agentforge.telemetry.runtime import ObservedModel, observed_tool


@dataclass
class Runtime:
    service: SpawnService
    models: ModelRouter
    coding: object | None = None

    async def close(self):
        close = getattr(self.models.model, "aclose", None)
        if close is not None:
            await close()


def compose_runtime(settings, sessions, telemetry, store) -> Runtime:
    """Fresh task binding; registered handlers + actual configured provider only."""
    models = ModelRouter.from_settings(settings)
    base_catalog = ToolCatalog()
    is_coding = store.task().permissions_json.get("kind") == "coding"
    coding = None
    if is_coding:
        from types import SimpleNamespace
        from agentforge.integrations.artifact_storage import ArtifactStorage
        from agentforge.integrations.github_client import GitHubClient
        from agentforge.integrations.e2b_client import E2BConfig, E2BRunner
        from agentforge.repositories.coding import CodingRepository
        from agentforge.subagents.coding_resources import CodingResourceResolver
        from agentforge.tools.repository_read import register_repository_read
        from agentforge.tools.sandbox_files import register_sandbox_files
        from agentforge.tools.sandbox_exec import CodingExecution, register_sandbox_exec
        from agentforge.tools.test_tools import register_test_results
        repository = CodingRepository(sessions, ArtifactStorage(settings.research_storage_path), lease=store.lease)
        task = store.task()
        if settings.auth_mode == "github_app":
            from agentforge.integrations.github_app import AuthorizedGitHubClient
            github = AuthorizedGitHubClient(sessions, settings, task.created_by)
        else:
            github = GitHubClient()
        configured = bool(settings.e2b_api_key and settings.e2b_template_id and settings.e2b_template_qualified)
        runner = E2BRunner(E2BConfig(api_key=settings.e2b_api_key.get_secret_value() if settings.e2b_api_key else None,
            template_id=settings.e2b_template_id, template_qualified=settings.e2b_template_qualified,
            command_timeout_sec=settings.e2b_command_timeout_sec, request_timeout_sec=settings.e2b_request_timeout_sec,
            ttl_sec=settings.e2b_sandbox_ttl_sec, max_output_bytes=settings.e2b_max_output_bytes))
        execution = CodingExecution(repository, runner, max_cost_usd=settings.e2b_execution_max_cost_usd, configured=configured,
            timeout_sec=settings.e2b_command_timeout_sec + 4 * settings.e2b_request_timeout_sec + 10)
        register_repository_read(base_catalog, repository, github)
        register_sandbox_files(base_catalog, repository)
        register_sandbox_exec(base_catalog, execution)
        register_test_results(base_catalog, repository)
        resources = CodingResourceResolver(repository, AuthorizationService(sessions))
        coding = SimpleNamespace(repository=repository, execution=execution, github=github)
    else:
        resources = register_research(base_catalog, sessions, settings)
    catalog = ToolCatalog()
    for tool in base_catalog.available(DESCRIPTIONS):
        catalog.register(tool.name, observed_tool(tool, telemetry, store),
            max_cost_usd=tool.max_cost_usd, timeout_sec=tool.timeout_sec)
    authorizer = AuthorizationService(sessions)
    service = SpawnService(registry=AgentRegistry(sessions, authorizer), authorizer=authorizer,
        budgets=BudgetService(sessions, max_task_spawns=32 if is_coding else 8), runs=AgentRunRepository(sessions),
        calls=ToolCallRepository(sessions), catalog=catalog,
        models=ModelRouter(ObservedModel(models.model, telemetry, store)), resources=resources)
    service.spawn_subagent = telemetry.decorate(service.spawn_subagent,
        kind="AGENT", name="spawn_subagent")
    return Runtime(service, models, coding)
