"""Durable model-assisted conversation routing through the shared budget/runtime.

The model supplies intent, never repository IDs, command profiles or grants.
One deterministic run identity reuses the saved result after worker recovery.
"""
import json
import re
from uuid import NAMESPACE_URL, uuid5

from agentforge.contracts.coding import CodingTaskSpec, RepoSelection
from agentforge.contracts.subagent import RoutingResult, SpawnRequest
from agentforge.orchestration.context import research_context
from agentforge.repositories.research_inputs import ResearchInputRepository
from agentforge.subagents.scope import intersect_and_validate, ScopeDenied


def greeting_response(goal):
    # Whole-message equality only: "hey, fix login" must reach semantic routing.
    text = re.sub(r"[!.,?\s]+$", "", goal.strip().casefold())
    if text in {"hi", "hey", "hello", "hiya", "good morning", "good evening", "good afternoon"}:
        return "Hi! What can I help you with?"
    return None


def infer_profiles(files):
    """Select only shipped argv profiles from concrete manifest/test evidence."""
    package = files.get("package.json")
    if package:
        try:
            data = json.loads(package)
            scripts = data.get("scripts", {})
            if isinstance(scripts, dict) and isinstance(scripts.get("test"), str) and scripts["test"].strip():
                return ("node_npm_test",), (("node_build",) if isinstance(scripts.get("build"), str) and scripts["build"].strip() else ())
            if any(re.search(r"(?:^|/)[^/]+\.(?:test|spec)\.(?:js|mjs|cjs)$", path) for path in files):
                return ("node_test",), ()
        except (ValueError, TypeError, AttributeError):
            return None
    pyproject = files.get("pyproject.toml")
    if pyproject:
        try:
            import tomllib
            data = tomllib.loads(pyproject.decode("utf-8"))
            project = data.get("project", {})
            dependencies = [*project.get("dependencies", []), *sum(project.get("optional-dependencies", {}).values(), [])]
            if "pytest" in data.get("tool", {}) or any(re.match(r"pytest(?:\W|$)", dependency) for dependency in dependencies if isinstance(dependency, str)):
                return ("python_pytest",), ()
        except (ValueError, TypeError, AttributeError, UnicodeError):
            return None
    tests = [value for path, value in files.items() if re.search(r"(?:^|/)test_[^/]+\.py$", path)]
    if tests and any(re.search(rb"(?:import unittest|from unittest import)", value) for value in tests):
        return ("python_unittest",), ()
    if tests and any(re.search(rb"(?:import pytest|from pytest import)", value) for value in tests):
        return ("python_pytest",), ()
    return None


async def route_turn(*, store, service, settings, github, authority, config):
    task = store.task()
    automatic = (task.permissions_json or {}).get("automatic")
    if not automatic or automatic.get("resolved"):
        return
    await authority()
    if task.status == "CREATED":
        store.transition("PLANNING")
    ctx = research_context(task)
    run_id = uuid5(NAMESPACE_URL, f"agentforge:{task.id}:intent-router:1")
    saved = store.completed_run(run_id)
    allowed_agents = {}
    # Bounded authorized metadata only. Resolve again before persisting selection.
    for entry in await service.registry.list_for_task(ctx):
        if not entry.key.startswith("project/"):
            continue
        try:
            resolved = await service.registry.resolve_for_task(entry.key, entry.version, ctx)
            if resolved.definition.output_schema_id != "research_result_v1":
                continue
            scope = intersect_and_validate(resolved.definition, ctx, ())
            if not service.catalog.available(scope.allowed_tools):
                continue
            allowed_agents[(entry.key, entry.version)] = entry
        except ScopeDenied:
            continue
    allowed_agents = dict(list(allowed_agents.items())[:20])
    if saved is None:
        manifest = ResearchInputRepository(store.sessions).input_manifest(ctx)
        brief = json.dumps({"instruction": "Understand the full user message and conversation. Read all inputs before routing. Select a custom agent only when its description closely matches a research goal; otherwise leave agent_key/agent_version null. Never select a key/version outside this catalog.",
            "selected_repository": automatic.get("repository"),
            "inputs": [{key: item[key] for key in ("input_kind", "attachment_index", "name")} for item in manifest],
            "available_capabilities": {"public_web_search": bool(settings.tavily_api_key),
                "repository_selected": bool(automatic.get("repository")),
                "coding_sandbox": bool(settings.e2b_api_key and settings.e2b_template_id and settings.e2b_template_qualified)},
            "agents": [{"key": entry.key, "version": entry.version, "name": entry.name,
                "description": entry.description, "tools": entry.allowed_tools} for entry in list(allowed_agents.values())[:20]]}, ensure_ascii=False)
        # The catalog is optional; never truncate goal/history to fit it.
        if len(brief) > 7800:
            brief = json.dumps({"instruction": "Read all complete owned inputs and route this message. Agent catalog omitted because its bounded metadata exceeded the limit; choose no custom agent.",
                "selected_repository": automatic.get("repository"),
                "inputs": [{key: item[key] for key in ("input_kind", "attachment_index", "name")} for item in manifest]}, ensure_ascii=False)
            allowed_agents = {}
        await service.spawn_subagent(SpawnRequest(type="builtin/intent-router", version=1,
            prompt=brief, requested_budget_usd=min(float(task.max_cost_usd) / 4, 0.12)),
            ctx, config=config, run_id=run_id, authority=authority)
        saved = store.completed_run(run_id)
    try:
        result = RoutingResult.model_validate_json(json.dumps(saved))
        if result.status != "PASS":
            raise ValueError("unresolved routing")
        manifest = ResearchInputRepository(store.sessions).input_manifest(ctx)
        requirements = {"source:" + item["id"]: (item["content_sha256"], len(item["content"])) for item in manifest}
        if ResearchInputRepository(store.sessions).consumed(ctx, run_id, requirements) != set(requirements):
            raise ValueError("incomplete routing inputs")
    except (ValueError, TypeError):
        store.finish("PARTIAL", "I couldn't reliably interpret that message. Could you restate what you'd like me to do?", error_code="INTENT_UNRESOLVED")
        return
    if result.intent in {"conversation", "clarification"}:
        store.finish("PARTIAL" if result.intent == "clarification" else "COMPLETED", result.response,
            {"conversation": True, **({"needs_input": True} if result.intent == "clarification" else {})})
        return
    if (result.intent == "coding" or result.use_repository) and not automatic.get("repository"):
        store.finish("PARTIAL", "Select a repository so I can inspect its files. You can also paste code here if you'd like advice.", {"conversation": True, "needs_input": True})
        return
    selection = None
    if automatic.get("repository") and (result.use_repository or result.intent == "coding"):
        selected = RepoSelection.model_validate(automatic["repository"])
        if result.intent == "coding" and automatic.get("continue_from_task_id"):
            from sqlalchemy import select
            from uuid import UUID
            from agentforge.database.models import Task
            with store.sessions() as db:
                previous = db.scalar(select(Task).where(Task.id == UUID(automatic["continue_from_task_id"]),
                    Task.created_by == task.created_by, Task.project_id == task.project_id))
                previous_selection = ((previous.permissions_json.get("coding") or {}).get("repository") if previous else None)
            if not previous_selection or previous_selection["repo_id"] != selected.repo_id:
                raise ScopeDenied("continuation repository selection changed")
            selected = RepoSelection.model_validate(previous_selection)
        try:
            resolved = await github.resolve(selected.repo_id, selected.ref)
        except Exception as exc:
            from agentforge.integrations.github_client import GitHubError
            if not isinstance(exc, GitHubError):
                raise
            store.finish("PARTIAL", "I couldn't access the selected repository. Check your GitHub connection and repository access in Settings, then try again.", {"conversation": True, "needs_input": True})
            return
        await authority()
        selection = RepoSelection(repo_id=resolved.repo_id, ref=resolved.commit_sha)
    coding = None
    if result.intent == "coding":
        if task.permissions_json.get("attachments") or task.permissions_json.get("agent_key"):
            store.finish("PARTIAL", "Please put the code or requirements in the selected repository before I make changes there. I can also help discuss the supplied material here.", {"conversation": True, "needs_input": True})
            return
        if not (settings.e2b_api_key and settings.e2b_template_id and settings.e2b_template_qualified):
            store.finish("PARTIAL", "I can discuss the change, but the execution environment needs to be configured before I can edit and test this repository.", {"conversation": True, "needs_input": True})
            return
        snapshot = await github.snapshot(selection.repo_id, selection.ref)
        await authority()
        if snapshot.commit_sha != selection.ref:
            raise ScopeDenied("repository snapshot differs from selected commit")
        profiles = infer_profiles(snapshot.files)
        if profiles is None:
            store.finish("PARTIAL", "I couldn't identify a supported test setup in this repository. Add a Python unittest/pytest setup or a Node test script so I can safely make and check the change.", {"conversation": True, "needs_input": True})
            return
        coding = CodingTaskSpec(repository=selection, test_profiles=profiles[0], command_profiles=profiles[1])
    agent = None
    if result.intent == "research" and result.agent_key:
        identity = (result.agent_key, result.agent_version)
        if identity not in allowed_agents:
            store.finish("PARTIAL", "I couldn't select an available agent for that request. Please try the request again.", error_code="INTENT_UNRESOLVED")
            return
        await service.registry.resolve_for_task(*identity, ctx)
        agent = identity
    store.resolve_automatic(result.intent, repository=selection, coding=coding, agent=agent)
