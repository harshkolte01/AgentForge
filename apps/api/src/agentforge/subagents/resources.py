"""Ownership boundary for immutable context, evidence and artifact references."""

from typing import TYPE_CHECKING, Protocol

from agentforge.tools.contracts import ToolArguments

from .scope import EffectiveScope, ScopeDenied, TaskContext

if TYPE_CHECKING:
    from agentforge.repositories.research import ResearchRepository
    from agentforge.security.policies import AuthorizationService, PublicURL


class ResourceResolver(Protocol):
    async def load_context(self, ctx: TaskContext, references: tuple[str, ...]) -> dict[str, str]: ...

    async def verify_references(self, ctx: TaskContext, *, evidence_ids: tuple[str, ...], artifact_ids: tuple[str, ...]) -> None: ...

    async def verify_tool_inputs(self, ctx: TaskContext, name: str, args: ToolArguments) -> None: ...


class DenyUnknownResources:
    """Phase 2 has no evidence store. Missing ownership integration denies IDs."""

    async def load_context(self, ctx: TaskContext, references: tuple[str, ...]) -> dict[str, str]:
        if references:
            raise ScopeDenied("context ownership resolver is not installed")
        return {}

    async def verify_references(self, ctx: TaskContext, *, evidence_ids: tuple[str, ...], artifact_ids: tuple[str, ...]) -> None:
        if evidence_ids or artifact_ids:
            raise ScopeDenied("evidence ownership resolver is not installed")

    async def verify_tool_inputs(self, ctx: TaskContext, name: str, args: ToolArguments) -> None:
        if name == "get_test_results":
            raise ScopeDenied("test-run ownership resolver is not installed")


class DatabaseResourceResolver:
    """Own references and allow fetch URLs only from trusted stored search results or the person's own message.

    A URL the person typed is as trusted as one a search returned: it is matched exactly against the stored task
    goal, so a model (or text it read) cannot add a query string to it, and every network rule still applies.
    """

    def __init__(self, repository: "ResearchRepository", authorizer: "AuthorizationService") -> None:
        self.repository, self.authorizer = repository, authorizer

    async def record_provider_input(self, ctx, operation_key, messages, tools):
        from agentforge.repositories.conversation_context import ConversationContextRepository
        import asyncio
        await asyncio.to_thread(ConversationContextRepository(self.repository.session_factory).record_provider_input,
            ctx.task_id, operation_key, messages, tools)

    async def load_context(self, ctx: TaskContext, references: tuple[str, ...]) -> dict[str, str]:
        await self.authorizer.check_context(ctx)
        if len(references) > 20 or not set(references).issubset(ctx.authorized_context_refs):
            raise ScopeDenied("context references are not authorized")
        # Bounded previews preserve IDs without injecting whole webpages.
        return {reference: self.repository.load_reference(ctx, reference) for reference in references}

    async def verify_references(self, ctx: TaskContext, *, evidence_ids: tuple[str, ...], artifact_ids: tuple[str, ...]) -> None:
        await self.authorizer.check_context(ctx)
        self.repository.verify_references(ctx, evidence_ids=evidence_ids, artifact_ids=artifact_ids)

    async def verify_tool_inputs(self, ctx: TaskContext, name: str, args: ToolArguments) -> None:
        await self.authorizer.check_context(ctx)
        if name == "get_test_results":
            raise ScopeDenied("test evidence integration is unavailable")
        if name == "fetch_webpage" and not self.repository.sources_for_url(ctx, args.source_url) and \
                not self._typed(ctx, args.source_url):
            raise ScopeDenied("fetch URL must reference an immutable source owned by this task")

    def _typed(self, ctx: TaskContext, url: str) -> bool:
        from agentforge.repositories.research import normalized_url
        return normalized_url(url) in self.repository.message_urls(ctx)

    async def resolve_web_url(self, ctx: TaskContext, name: str, args: ToolArguments,
                              scope: EffectiveScope) -> tuple["PublicURL", tuple[str, ...]]:
        from urllib.parse import urlsplit
        from agentforge.integrations.safe_fetch import resolve_public_url
        from agentforge.repositories.research import normalized_url

        await self.authorizer.check_context(ctx)
        if name != "fetch_webpage":
            raise ScopeDenied("browser network authorization is unavailable")
        url = args.source_url
        records = self.repository.sources_for_url(ctx, url)
        typed = self.repository.message_urls(ctx)
        if not records and normalized_url(url) not in typed:
            raise ScopeDenied("fetch URL was neither discovered for this task nor written by the person")
        trusted_hosts = {urlsplit(record["url"]).hostname for record in self.repository.list_sources(
            task_id=ctx.task_id, owner_id=ctx.user_id, project_id=ctx.project_id)
            if record["fetch_status"] in ("SEARCH_ONLY", "FETCHED")}
        trusted_hosts.update(urlsplit(typed_url).hostname for typed_url in typed)
        explicit = scope.permissions.allowed_web_hosts or ()
        if explicit:
            trusted_hosts.intersection_update(explicit)
        elif not ctx.source_discovery_enabled:
            trusted_hosts.clear()
        hosts = tuple(sorted(host for host in trusted_hosts if host))
        public = await resolve_public_url(url, hosts)
        return public, hosts
