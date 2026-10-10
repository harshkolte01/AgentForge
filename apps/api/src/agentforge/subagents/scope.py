"""Bind capability templates to concrete, server-authorized task resources."""

from dataclasses import dataclass
from uuid import UUID

from agentforge.contracts.agent_definition import AgentDefinition, Budget, Permissions, TOOL_CAPABILITIES, ToolName


class ScopeDenied(PermissionError):
    """A capability or a referenced resource is outside the current task."""


@dataclass(frozen=True)
class TaskContext:
    """Internal service context; never deserialize this from model/tool arguments.

    Resource sets in every grant ceiling must be concrete. Context references
    are opaque immutable IDs validated by the application's resource resolver.
    """

    user_id: UUID
    project_id: UUID
    task_id: UUID
    platform_permissions: Permissions
    user_permissions: Permissions
    task_permissions: Permissions
    parent_permissions: Permissions
    platform_budget: Budget
    tier_budget: Budget
    task_budget: Budget
    parent_budget: Budget
    authorized_context_refs: tuple[str, ...] = ()
    depth: int = 0


@dataclass(frozen=True)
class EffectiveScope:
    permissions: Permissions
    allowed_tools: tuple[ToolName, ...]
    budget: Budget
    context_refs: tuple[str, ...]


def intersect_and_validate(
    definition: AgentDefinition, ctx: TaskContext, context_refs: tuple[str, ...],
) -> EffectiveScope:
    if ctx.depth != 0:
        raise ScopeDenied("nested sub-agent delegation is disabled")
    # Round-trip through JSON revalidates even models constructed with model_copy.
    definition = AgentDefinition.model_validate_json(definition.model_dump_json())
    ceilings = [Permissions.model_validate_json(p.model_dump_json()) for p in (
        ctx.platform_permissions, ctx.user_permissions, ctx.task_permissions, ctx.parent_permissions,
    )]
    values: dict[str, object] = {}
    for name in Permissions.model_fields:
        if name.startswith("allowed_"):
            grants = [getattr(p, name) for p in ceilings]
            if any(grant is None for grant in grants):
                raise ScopeDenied(f"missing concrete authorization ceiling: {name}")
            resource_set = set(grants[0])
            for grant in grants[1:]:
                resource_set.intersection_update(grant)
            requested = getattr(definition.permissions, name)
            if requested is not None:
                resource_set.intersection_update(requested)
            values[name] = tuple(sorted(resource_set))
        else:
            values[name] = all(getattr(p, name) for p in [*ceilings, definition.permissions])
    permissions = Permissions.model_validate(values)
    references = set(context_refs)
    if not references.issubset(ctx.authorized_context_refs) or not references.issubset(permissions.allowed_context_refs or ()):
        raise ScopeDenied("context reference is not authorized for this task")
    if len(references) != len(context_refs):
        raise ScopeDenied("duplicate context references")
    budgets = [Budget.model_validate_json(b.model_dump_json()) for b in (
        ctx.platform_budget, ctx.tier_budget, ctx.task_budget, ctx.parent_budget, definition.budget,
    )]
    budget = Budget.model_validate({name: min(getattr(b, name) for b in budgets) for name in Budget.model_fields})
    tools = tuple(tool for tool in definition.allowed_tools if all(
        getattr(permissions, flag) for flag in TOOL_CAPABILITIES[tool]
    ))
    if not tools:
        raise ScopeDenied("no tools remain within the authorized grant intersection")
    return EffectiveScope(permissions, tools, budget, context_refs)
