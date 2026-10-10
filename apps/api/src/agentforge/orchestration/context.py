"""Build concrete read-only research ceilings from current persisted task grants."""
from agentforge.contracts.agent_definition import Budget, Permissions
from agentforge.subagents.scope import ScopeDenied, TaskContext


def research_context(task) -> TaskContext:
    grants = task.permissions_json or {}
    if grants.get("web") is not True:
        raise ScopeDenied("task_has_no_research_grant")
    permissions = Permissions(web=True, allowed_repo_ids=(), allowed_sandbox_ids=(),
        allowed_context_refs=(), allowed_command_profiles=(),
        allowed_web_hosts=tuple(grants.get("allowed_web_hosts", ())))
    budget = Budget(max_steps=12, max_tool_calls=20,
        max_llm_cost_usd=min(float(task.max_cost_usd), 5.0),
        max_wall_time_sec=min(task.max_wall_time_sec, 1200))
    return TaskContext(user_id=task.created_by, project_id=task.project_id, task_id=task.id,
        platform_permissions=permissions, user_permissions=permissions,
        task_permissions=permissions, parent_permissions=permissions,
        platform_budget=budget, tier_budget=budget, task_budget=budget, parent_budget=budget,
        source_discovery_enabled=True)
