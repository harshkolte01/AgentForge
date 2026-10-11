"""Manual Phase 2 composition example; authored but deliberately not executed.

Requires an isolated PostgreSQL database with 0002_phase2 already applied. This
script writes a fresh fixture user/project/task plus durable child audit rows.
By default it uses an injected mock model and fixture search, makes no provider
request, and does not qualify Gate P2. It is an example, not a test suite.

After independently reviewing configuration and applying the migration:
    cd apps/api
    uv run python examples/phase2_mock_child.py

For a paid real-model request with the same synthetic tool, configure LLM_*
settings and explicitly pass --real-model. Sources remain synthetic; this alone
does not prove forbidden-call behavior or qualify Gate P2.

Do not point DATABASE_URL at production. No migration is applied by this script.
"""

import asyncio
import argparse
import json
from decimal import Decimal
from uuid import uuid4

from sqlalchemy.orm import sessionmaker

from agentforge.contracts.agent_definition import Budget, Permissions
from agentforge.contracts.subagent import SpawnRequest
from agentforge.database.engine import create_db_engine
from agentforge.database.models import Project, Task, User
from agentforge.llm.models import ModelResponse, ModelUsage
from agentforge.llm.router import ModelRouter
from agentforge.repositories.runs import AgentRunRepository, ToolCallRepository
from agentforge.security.policies import AuthorizationService
from agentforge.settings import get_settings
from agentforge.subagents.budget import BudgetService
from agentforge.subagents.registry import AgentRegistry
from agentforge.subagents.scope import ScopeDenied, TaskContext
from agentforge.subagents.spawn_service import SpawnService
from agentforge.tools.catalog import ToolCatalog
from agentforge.tools.contracts import ToolArguments, ToolOutcome, WebSearchArgs
from agentforge.tools.gateway import ToolExecutionContext


class FixtureResources:
    """A single in-memory fixture bound to an exact server-created principal."""

    def __init__(self, user_id, project_id, task_id):
        self.identity = (user_id, project_id, task_id)
        self.evidence_id = f"fixture:{task_id}:source:one"

    def _check(self, ctx):
        if (ctx.user_id, ctx.project_id, ctx.task_id) != self.identity:
            raise ScopeDenied("fixture belongs to another principal")

    async def load_context(self, ctx, references):
        self._check(ctx)
        if references:
            raise ScopeDenied("fixture has no context references")
        return {}

    async def verify_references(self, ctx, *, evidence_ids, artifact_ids):
        self._check(ctx)
        if artifact_ids or any(item != self.evidence_id for item in evidence_ids):
            raise ScopeDenied("unknown fixture evidence")

    async def verify_tool_inputs(self, ctx, name, args):
        self._check(ctx)
        if name != "web_search" or not isinstance(args, WebSearchArgs):
            raise ScopeDenied("fixture only permits typed search")

    async def search(self, args: ToolArguments, execution: ToolExecutionContext) -> ToolOutcome:
        await self.verify_tool_inputs(execution.task, "web_search", args)
        return ToolOutcome(status="ok", cost_usd=Decimal(0),
            evidence_ids=(self.evidence_id,), data={
                "fixture": True, "evidence_id": self.evidence_id,
                "text": "Synthetic fixture: source says a durable intent precedes an adapter call.",
            })


class FixtureModel:
    """Deterministic injected model; no network, provider SDK or credential use."""

    model_id = "fixture/mock-researcher-v1"

    def __init__(self, evidence_id):
        self.evidence_id = evidence_id
        self.calls = 0

    def maximum_request_cost(self, messages, tools):
        return Decimal("0.0010")

    async def complete(self, messages, tools, **_):
        self.calls += 1
        if self.calls == 1:
            message = {"role": "assistant", "content": None, "tool_calls": [{
                "id": "fixture_search_1", "type": "function", "function": {
                    "name": "web_search",
                    "arguments": json.dumps({"query": "synthetic audit intent example", "max_results": 1}),
                },
            }]}
            reason = "tool_calls"
        else:
            # A plain-text answer that says the evidence is synthetic.
            message = {"role": "assistant", "content":
                "Mock fixture completed. The synthetic source says a durable intent precedes an adapter call. "
                "No live provider or public source was queried, so this is not real research."}
            reason = "stop"
        return ModelResponse(message=message, usage=ModelUsage(10, 10, Decimal("0.0001")),
                             model_id=self.model_id, finish_reason=reason)


async def main(*, real_model: bool = False):
    settings = get_settings()
    # Validate provider configuration before writing fixture records.
    configured_models = ModelRouter.from_settings(settings) if real_model else None
    engine = create_db_engine(settings.database_url)
    sessions = sessionmaker(engine, expire_on_commit=False)
    user_id, project_id, task_id = uuid4(), uuid4(), uuid4()
    try:
        with sessions.begin() as session:
            session.add(User(id=user_id, github_user_id=uuid4().int % (2**62),
                             display_name="Phase 2 synthetic fixture"))
            session.flush()
            session.add(Project(id=project_id, owner_id=user_id, name="Phase 2 fixture"))
            session.flush()
            session.add(Task(id=task_id, project_id=project_id, created_by=user_id,
                goal="Run a synthetic researcher fixture with audit history.", status="RUNNING",
                thread_id=f"phase2-fixture-{task_id}", max_cost_usd=Decimal("0.35"),
                max_wall_time_sec=180))
        permissions = Permissions(web=True, allowed_repo_ids=(), allowed_sandbox_ids=(),
            allowed_context_refs=(), allowed_command_profiles=(), allowed_web_hosts=())
        budget = Budget()
        ctx = TaskContext(user_id=user_id, project_id=project_id, task_id=task_id,
            platform_permissions=permissions, user_permissions=permissions,
            task_permissions=permissions, parent_permissions=permissions,
            platform_budget=budget, tier_budget=budget, task_budget=budget, parent_budget=budget)
        resources, catalog = FixtureResources(user_id, project_id, task_id), ToolCatalog()
        catalog.register("web_search", resources.search, max_cost_usd=Decimal(0))
        authorizer = AuthorizationService(sessions)
        service = SpawnService(registry=AgentRegistry(sessions, authorizer), authorizer=authorizer,
            budgets=BudgetService(sessions), runs=AgentRunRepository(sessions),
            calls=ToolCallRepository(sessions), catalog=catalog,
            models=configured_models or ModelRouter(FixtureModel(resources.evidence_id)), resources=resources)
        result = await service.spawn_subagent(SpawnRequest(type="builtin/agent", version=1,
            prompt="Use the synthetic search fixture and say what it shows. "
                   "All tool evidence is synthetic: do not claim real-world research was verified."), ctx)
        print(json.dumps({"fixture": True, "task_id": str(task_id),
                          "model_mode": "real-provider" if real_model else "mock",
                          "result": result.model_dump(mode="json")}, indent=2))
    finally:
        engine.dispose()
        if configured_models is not None:
            await configured_models.model.aclose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--real-model", action="store_true",
                        help="Call the configured paid provider; tool sources remain synthetic")
    asyncio.run(main(real_model=parser.parse_args().real_model))
