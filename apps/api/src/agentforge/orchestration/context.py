"""Build the concrete grants of one chat turn from the task's persisted grants."""
from agentforge.contracts.agent_definition import Budget, Permissions
from agentforge.subagents.scope import TaskContext


def agent_context(task) -> TaskContext:
    grants = task.permissions_json or {}
    repository = grants.get("repository") or {}
    # sandbox_*: the coding tools. They exist only where the sandbox is configured; this just stops a missing grant from hiding them.
    permissions = Permissions(web=True, github_read=bool(repository), sandbox_read=True, sandbox_write=True, sandbox_exec=True,
        allowed_repo_ids=(repository["repo_id"],) if repository else (), allowed_sandbox_ids=(),
        allowed_context_refs=(), allowed_command_profiles=(),
        allowed_web_hosts=tuple(grants.get("allowed_web_hosts", ())))
    budget = Budget()
    return TaskContext(user_id=task.created_by, project_id=task.project_id, task_id=task.id,
        platform_permissions=permissions, user_permissions=permissions,
        task_permissions=permissions, parent_permissions=permissions,
        platform_budget=budget, tier_budget=budget, task_budget=budget, parent_budget=budget,
        source_discovery_enabled=True)
