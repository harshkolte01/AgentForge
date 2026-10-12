"""Immutable packaged built-ins plus project-private definition versions.

Only namespace-qualified keys and explicit versions can resolve. Source files
are authoritative for built-ins; stored snapshots must match them byte-for-byte
after canonical JSON normalization. Custom updates always create a new version.
"""

from dataclasses import dataclass
from collections.abc import Callable
import hashlib
from importlib.resources import files
import json
import time
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select
from sqlalchemy.orm import Session

from agentforge.contracts.agent_definition import AgentDefinition, Budget, Permissions
from agentforge.database.models import AgentDefinition as DefinitionRow, Project
from agentforge.repositories.definitions import DefinitionRepository
from agentforge.security.policies import AuthorizationService
from agentforge.subagents.scope import ScopeDenied, TaskContext


BUILTIN_KEYS = frozenset({"agent"})
# Stored built-in snapshots are immutable: a changed prompt is a new version file, and older tasks keep resolving theirs.
AGENT_VERSION = 4
AGENT_KEY = "builtin/agent"
BUILTIN_CACHE_SEC = 60
_builtin_cache: dict[tuple[str, int], tuple[float, "ResolvedDefinition"]] = {}   # (key, version) -> (valid until, result)


def canonical_snapshot(definition: AgentDefinition) -> tuple[dict, str]:
    validated = AgentDefinition.model_validate_json(definition.model_dump_json())
    snapshot = validated.model_dump(mode="json")
    encoded = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    return snapshot, hashlib.sha256(encoded).hexdigest()


def load_builtin(name: str, version: int) -> AgentDefinition:
    if name not in BUILTIN_KEYS or type(version) is not int or version < 1:
        raise ScopeDenied("unknown built-in definition version")
    resource = files("agentforge.subagents.definitions").joinpath(f"{name}.v{version}.json")
    if not resource.is_file():
        raise ScopeDenied("unknown built-in definition version")
    definition = AgentDefinition.model_validate_json(resource.read_text(encoding="utf-8"))
    if definition.name != name or definition.version != version or not definition.enabled:
        raise ScopeDenied("built-in resource identity is invalid")
    return definition


@dataclass(frozen=True)
class ResolvedDefinition:
    definition_id: UUID
    version_id: UUID
    key: str
    definition: AgentDefinition
    sha256: str


class AgentRegistry:
    def __init__(self, session_factory: Callable[[], Session], authorizer: AuthorizationService | None = None):
        self.session_factory = session_factory
        self.authorizer = authorizer or AuthorizationService(session_factory)

    async def resolve_for_task(self, key: str, version: int, ctx: TaskContext) -> ResolvedDefinition:
        await self.authorizer.check_context(ctx)
        if type(version) is not int or version < 1:
            raise ScopeDenied("explicit definition version is required")
        parts = key.split("/")
        if len(parts) == 2 and parts[0] == "builtin":
            # Re-resolved before every model step and tool call. A built-in is an immutable package file, so once its
            # row is seeded and verified the result is kept per process for a minute, and then re-verified by a plain
            # read; the seeding write (which locks the one row every run shares) happens only when that read fails.
            cached = _builtin_cache.get((key, version))
            if cached is not None and cached[0] > time.monotonic():
                return cached[1]
            resolved = self._resolve_builtin(key, parts[1], version)
            _builtin_cache[(key, version)] = (time.monotonic() + BUILTIN_CACHE_SEC, resolved)
            return resolved
        with self.session_factory() as session, session.begin():
            repository = DefinitionRepository(session)
            if len(parts) == 3 and parts[0] == "project":
                try:
                    project_id = UUID(parts[1])
                except ValueError as exc:
                    raise ScopeDenied("invalid project namespace") from exc
                if str(project_id) != parts[1] or project_id != ctx.project_id:
                    raise ScopeDenied("definition belongs to another project")
                name = parts[2]
                if name in BUILTIN_KEYS:
                    raise ScopeDenied("project agents cannot impersonate built-ins")
                row = repository.resolve_project(project_id=project_id, agent_key=name, version=version)
                if row is None:
                    raise ScopeDenied("definition version is not available")
                definition = AgentDefinition.model_validate_json(json.dumps(row.definition_json, allow_nan=False))
                snapshot, digest = canonical_snapshot(definition)
                if definition.name != name or definition.version != version or not definition.enabled:
                    raise ScopeDenied("definition identity or enabled state is invalid")
                if snapshot != row.definition_json or digest != row.sha256:
                    raise ScopeDenied("stored definition snapshot integrity check failed")
                # Custom snapshots must contain explicit resource restrictions.
                if any(getattr(definition.permissions, field) is None for field in Permissions.model_fields if field.startswith("allowed_")):
                    raise ScopeDenied("custom definition has unbounded resource scope")
            else:
                raise ScopeDenied("namespace-qualified definition key is required")
            return ResolvedDefinition(row.definition_id, row.id, key, definition, digest)

    def _resolve_builtin(self, key: str, name: str, version: int) -> ResolvedDefinition:
        definition = load_builtin(name, version)
        snapshot, digest = canonical_snapshot(definition)
        definition_id = uuid5(NAMESPACE_URL, f"https://agentforge.local/definitions/builtin/{name}")
        identity = dict(definition_id=definition_id, agent_key=name, version=version, definition_json=snapshot, sha256=digest)
        with self.session_factory() as session:
            row = DefinitionRepository(session).seeded_builtin(**identity)
        if row is None:
            with self.session_factory() as session, session.begin():
                row = DefinitionRepository(session).ensure_builtin(**identity)
                parent = session.get(DefinitionRow, row.definition_id)
                if parent is None or parent.disabled_at is not None:
                    raise ScopeDenied("built-in definition is disabled")
        return ResolvedDefinition(row.definition_id, row.id, key, definition, digest)

    async def save_project_version(
        self, definition: AgentDefinition, ctx: TaskContext, *, grant_ceiling: Permissions,
        budget_ceiling: Budget,
    ) -> ResolvedDefinition:
        """Internal owner-only management service; no HTTP endpoint in Phase 2.

        Project ownership supplies agent:manage in the single-owner MVP. The
        caller must obtain ceilings from server policy, never model input.
        """
        await self.authorizer.check_context(ctx)
        snapshot, digest = canonical_snapshot(definition)
        definition = AgentDefinition.model_validate_json(json.dumps(snapshot))
        if definition.name in BUILTIN_KEYS:
            raise ScopeDenied("built-in names are reserved")
        ceilings = [Permissions.model_validate_json(value.model_dump_json()) for value in (
            grant_ceiling, ctx.platform_permissions, ctx.user_permissions,
            ctx.task_permissions, ctx.parent_permissions,
        )]
        budgets = [Budget.model_validate_json(value.model_dump_json()) for value in (
            budget_ceiling, ctx.platform_budget, ctx.tier_budget, ctx.task_budget, ctx.parent_budget,
        )]
        for field in Permissions.model_fields:
            requested = getattr(definition.permissions, field)
            for ceiling in ceilings:
                permitted = getattr(ceiling, field)
                if field.startswith("allowed_"):
                    if requested is None or permitted is None or not set(requested).issubset(permitted):
                        raise ScopeDenied("custom resource scope exceeds the owner grant ceiling")
                elif requested and not permitted:
                    raise ScopeDenied("custom capability exceeds the owner grant ceiling")
        if any(getattr(definition.budget, field) > min(getattr(budget, field) for budget in budgets) for field in Budget.model_fields):
            raise ScopeDenied("custom budget exceeds the server ceiling")
        with self.session_factory() as session, session.begin():
            # Recheck and lock the owner project in the write transaction.
            project = session.scalar(select(Project).where(Project.id == ctx.project_id, Project.owner_id == ctx.user_id).with_for_update())
            if project is None:
                raise ScopeDenied("project management is not authorized")
            row = DefinitionRepository(session).save_project(
                project_id=ctx.project_id, agent_key=definition.name,
                definition_json=snapshot, sha256=digest, created_by=ctx.user_id,
                version=definition.version,
            )
            return ResolvedDefinition(row.definition_id, row.id, f"project/{ctx.project_id}/{definition.name}", definition, digest)

    async def disable_project_definition(self, key: str, ctx: TaskContext) -> None:
        """Soft-disable a private agent while retaining all version history."""
        await self.authorizer.check_context(ctx)
        parts = key.split("/")
        if len(parts) != 3 or parts[:2] != ["project", str(ctx.project_id)] or parts[2] in BUILTIN_KEYS:
            raise ScopeDenied("project definition management requires its exact namespace")
        with self.session_factory() as session, session.begin():
            row = session.scalar(select(DefinitionRow).where(
                DefinitionRow.namespace == "project", DefinitionRow.project_id == ctx.project_id,
                DefinitionRow.agent_key == parts[2],
            ))
            if row is None:
                raise ScopeDenied("project definition is not available")
            DefinitionRepository(session).disable_project(
                project_id=ctx.project_id, definition_id=row.id, owner_id=ctx.user_id,
            )
