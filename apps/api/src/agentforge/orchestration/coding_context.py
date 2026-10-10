"""Concrete coding ceilings from persisted task selection, never model content."""
from agentforge.contracts.agent_definition import Budget, Permissions
from agentforge.contracts.coding import CodingTaskSpec
from agentforge.subagents.scope import ScopeDenied, TaskContext


def coding_context(task, workspace=None, *, references=(), readonly=False):
    grants = task.permissions_json or {}
    if grants.get("kind") != "coding" or not grants.get("coding"):
        raise ScopeDenied("task has no coding selection")
    spec = CodingTaskSpec.model_validate(grants["coding"])
    profiles = tuple(dict.fromkeys((*spec.test_profiles, *spec.command_profiles)))
    permissions = Permissions(github_read=True, sandbox_read=True,
        sandbox_exec=not readonly, sandbox_write=not readonly,
        allowed_repo_ids=(spec.repository.repo_id,),
        allowed_sandbox_ids=(workspace["id"],) if workspace else (),
        allowed_context_refs=tuple(references), allowed_command_profiles=profiles,
        allowed_web_hosts=())
    budget = Budget(max_steps=20, max_tool_calls=40, max_llm_cost_usd=min(float(task.max_cost_usd), 5),
        max_wall_time_sec=min(task.max_wall_time_sec, 1200))
    return TaskContext(user_id=task.created_by, project_id=task.project_id, task_id=task.id,
        platform_permissions=permissions, user_permissions=permissions, task_permissions=permissions,
        parent_permissions=permissions, platform_budget=budget, tier_budget=budget, task_budget=budget,
        parent_budget=budget, authorized_context_refs=tuple(references))
