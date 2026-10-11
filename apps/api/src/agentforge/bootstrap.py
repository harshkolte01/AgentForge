"""Trusted runtime composition; construction alone performs no provider requests."""
from dataclasses import dataclass
from agentforge.llm.router import ModelRouter
from agentforge.repositories.runs import AgentRunRepository, ToolCallRepository
from agentforge.security.policies import AuthorizationService
from agentforge.subagents.budget import BudgetService
from agentforge.subagents.registry import AgentRegistry
from agentforge.subagents.spawn_service import SpawnService
from agentforge.tools.approvals import ToolApprovals
from agentforge.tools.catalog import ToolCatalog, DESCRIPTIONS
from agentforge.tools.research import register_research
from agentforge.telemetry.runtime import ObservedModel, observed_tool
from sqlalchemy import select
from agentforge.database.models import User


def repository_client(settings, sessions, owner_id):
    """A shared worker must use the task owner's identity, not its HTTP login mode."""
    from agentforge.integrations.github_app import AuthorizedGitHubClient
    from agentforge.integrations.github_client import GitHubClient
    with sessions() as db:
        github_id = db.scalar(select(User.github_user_id).where(User.id == owner_id))
    # Local bootstrap identities use a negative sentinel; real GitHub IDs are positive.
    if settings.auth_mode == "github_app" or (github_id is not None and github_id > 0):
        return AuthorizedGitHubClient(sessions, settings, owner_id)
    return GitHubClient()


@dataclass
class Runtime:
    service: SpawnService
    models: ModelRouter
    repository_github: object | None = None
    plugins: object | None = None

    async def close(self):
        if self.plugins is not None:
            await self.plugins.aclose()
        close = getattr(self.models.model, "aclose", None)
        if close is not None:
            await close()


def compose_runtime(settings, sessions, telemetry, store) -> Runtime:
    """Fresh task binding; registered handlers + actual configured provider only."""
    models = ModelRouter.from_settings(settings)
    base_catalog = ToolCatalog()
    repository_github = github = None
    resources = register_research(base_catalog, sessions, settings)
    from agentforge.tools.general import register_general
    register_general(base_catalog, sessions, settings)
    task = store.task()
    selection = task.permissions_json.get("repository")
    if selection:
        from agentforge.tools.repository_research import register_repository_research
        github = repository_client(settings, sessions, task.created_by)
        register_repository_research(base_catalog, sessions, selection, github, resources)
        repository_github = github
    if settings.e2b_api_key:
        # The coding tools: a sandbox that persists for the conversation (see tools/sandbox_shell.py).
        from agentforge.integrations.e2b_sandbox import SandboxConfig, SandboxProvider
        from agentforge.tools.sandbox_shell import ConversationSandbox, register_sandbox_shell
        provider = SandboxProvider(SandboxConfig(api_key=settings.e2b_api_key.get_secret_value(), template=settings.e2b_template_id,
            ttl_sec=settings.e2b_sandbox_ttl_sec, request_timeout_sec=settings.e2b_request_timeout_sec))
        from agentforge.integrations.github_app import AuthorizedGitHubClient
        can_open_pull_requests = settings.enable_github_writes and isinstance(github, AuthorizedGitHubClient)
        from agentforge.integrations.attachment_storage import AttachmentStorage
        register_sandbox_shell(base_catalog, ConversationSandbox(sessions, provider, github, attachment_storage=AttachmentStorage(settings.research_storage_path)),
            pull_requests=github if can_open_pull_requests else None)
    from agentforge.tools.conversation import register_conversation
    from agentforge.database.sessions import TurnExecution
    from sqlalchemy import select
    with sessions() as db:
        conversation_task = db.scalar(select(TurnExecution.turn_id).where(TurnExecution.task_id == store.lease.task_id))
    if conversation_task:
        register_conversation(base_catalog, sessions, settings, resources)
    catalog = ToolCatalog()
    for tool in base_catalog.available(DESCRIPTIONS):
        catalog.register(tool.name, observed_tool(tool, telemetry, store),
            max_cost_usd=tool.max_cost_usd, timeout_sec=tool.timeout_sec)
    plugins = None
    if conversation_task and settings.plugins_enabled:
        # The plugins this conversation may use, as the person set them (see plugins/service.py). A failure here must not
        # stop the conversation: the turn simply runs without them.
        try:
            from agentforge.plugins.runtime import register_plugins
            from agentforge.plugins.service import connections_for_task
            connections = connections_for_task(sessions, store.lease.task_id, task.created_by)
            if connections:
                from types import SimpleNamespace
                plugins = register_plugins(catalog, connections, settings,
                    wrap=lambda name, handler: observed_tool(SimpleNamespace(name=name, handler=handler), telemetry, store))
        except Exception:  # noqa: BLE001
            import logging
            logging.getLogger(__name__).warning("plugins unavailable for this run: task_id=%s", store.lease.task_id, exc_info=True)
    authorizer = AuthorizationService(sessions)
    service = SpawnService(registry=AgentRegistry(sessions, authorizer), authorizer=authorizer,
        budgets=BudgetService(sessions), runs=AgentRunRepository(sessions),
        calls=ToolCallRepository(sessions), catalog=catalog,
        models=ModelRouter(ObservedModel(models.model, telemetry, store)), resources=resources,
        lifecycle=store.progress, approvals=ToolApprovals(sessions, store.progress), sessions=sessions)
    service.spawn_subagent = telemetry.decorate(service.spawn_subagent,
        kind="AGENT", name="spawn_subagent")
    return Runtime(service, models, repository_github, plugins)
