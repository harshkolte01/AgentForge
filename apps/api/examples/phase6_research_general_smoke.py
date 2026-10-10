"""Actual shared-runtime graphs, mocked providers and disposable PostgreSQL only."""
import asyncio
from decimal import Decimal
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from _disposable_db import admin_engine
from agentforge.settings import Settings
import agentforge.settings as settings_module
ROOT = Path(__file__).resolve().parents[3]
name = "agentforge_research_smoke_" + uuid4().hex[:12]
admin = engine = None
created = False
stage = "setup"
QUOTE = "The fixture policy requires a complete review before delivery."
def check(condition, message):
    if not condition: raise AssertionError(message)
async def run(settings):
    global engine, stage
    from alembic import command
    from alembic.config import Config
    cfg = Config(str(ROOT / "apps/api/alembic.ini")); cfg.set_main_option("script_location", str(ROOT / "apps/api/alembic"))
    command.upgrade(cfg, "head")
    from agentforge.database.models import User, Project, Task, AgentRun, ToolCall
    from agentforge.contracts.tasks import TaskCreate, TextAttachment
    from agentforge.repositories.tasks import TaskRepository
    from agentforge.repositories.jobs import JobRepository
    from agentforge.repositories.research_inputs import ResearchInputRepository
    from agentforge.orchestration.store import WorkflowStore
    from agentforge.orchestration.context import research_context
    from agentforge.orchestration.graph import build_graph
    from agentforge.orchestration.verification import VerificationService
    from agentforge.bootstrap import compose_runtime
    from agentforge.telemetry.neatlogs_setup import Telemetry
    from agentforge.llm.models import ModelResponse, ModelUsage
    from agentforge.llm.router import ModelRouter
    from agentforge.integrations.tavily_client import SearchResponse, TavilyClient
    from agentforge.integrations.safe_fetch import SafeWebFetcher
    from agentforge.security.policies import PublicURL
    from agentforge.subagents.scope import ScopeDenied
    from langgraph.checkpoint.memory import InMemorySaver
    engine = create_engine(settings.database_url); sessions = sessionmaker(engine, expire_on_commit=False)
    user, outsider, project = uuid4(), uuid4(), uuid4()
    with sessions.begin() as db:
        db.add_all([User(id=user, github_user_id=-222, display_name="Fixture"), User(id=outsider, github_user_id=-223, display_name="Other")]); db.flush()
        db.add(Project(id=project, owner_id=user, name="Research fixture"))
    telemetry = Telemetry(settings); mode = "positive"
    class FixtureModel:
        model_id = "fixture/research-general"
        def maximum_request_cost(self, messages, tools): return Decimal("0.001")
        async def complete(self, messages, tools):
            brief = json.loads(messages[1]["content"]); role = brief["role"]["name"]
            verifier, general, routing = role == "verifier", role == "general-executor", role == "intent-router"
            content = brief["goal_and_acceptance_brief"]; handoff = json.loads(content[content.index("{"):])
            functions = {call["id"]: call["function"] for m in messages for call in m.get("tool_calls", [])}
            previous = [(functions[m["tool_call_id"]]["name"], json.loads(functions[m["tool_call_id"]]["arguments"]), json.loads(m["content"].split("\n", 1)[1])) for m in messages if m["role"] == "tool"]
            pending = []
            def read(tool, args):
                found = [o for n, a, o in previous if n == tool and all(a.get(k) == v for k, v in args.items())]
                if not found or found[-1]["data"].get("next_offset") is not None:
                    pending.append((tool, {**args, "offset": found[-1]["data"].get("next_offset", 0) if found else 0, "max_chars": 4000}))
            if not (verifier and mode == "fake_pass"):
                for item in handoff["inputs"]:
                    if not verifier and mode == "unread_attachment" and item["input_kind"] == "attachment": continue
                    read("read_task_input", {"input_kind": item["input_kind"], "attachment_index": item["attachment_index"]})
            if not pending and not verifier and not general and not routing and mode == "auto_repository":
                selected = handoff["selected_repository"]
                check("write_sandbox_file" not in brief["permitted_tools"] and "run_tests" not in brief["permitted_tools"], "repository research gained write/exec tools")
                repo_reads = [o for n, a, o in previous if n == "read_repo_file"]
                if not repo_reads:
                    pending.append(("read_repo_file", {"repo_id": selected["repo_id"], "ref": selected["ref"], "path": "policy #1.md"}))
                else:
                    read("read_research_source", {"source_id": repo_reads[-1]["data"]["source_id"]})
            if not pending and not verifier and not general and not routing and mode != "auto_repository":
                searches = [o for n, a, o in previous if n == "web_search"]
                if not searches: pending.append(("web_search", {"query": "fixture official policy", "max_results": 1}))
                else:
                    for source in searches[-1]["data"]["sources"]:
                        if not any(n == "fetch_webpage" and a["source_url"] == source["url"] for n, a, o in previous): pending.append(("fetch_webpage", {"source_url": source["url"]}))
            artifacts = [ref for _, _, o in previous for ref in o["artifact_ids"]]
            if not pending and general and not artifacts: pending.append(("create_task_artifact", {"format": "markdown", "title": "Fixture draft", "content": "# Requested draft\n\nA clear useful draft from the complete supplied attachment.\n"}))
            if not pending and general and artifacts: read("read_task_artifact", {"artifact_id": artifacts[0]})
            if verifier and mode != "fake_pass":
                for source_id in handoff["source_ids"]: read("read_research_source", {"source_id": source_id})
                for artifact_id in handoff["artifact_ids"]: read("read_task_artifact", {"artifact_id": artifact_id})
            if pending:
                message = {"role": "assistant", "content": None, "tool_calls": [{"id": "call" + str(len(previous) + i), "type": "function", "function": {"name": n, "arguments": json.dumps(a)}} for i, (n, a) in enumerate(pending)]}; reason = "tool_calls"
            else:
                evidence = sorted({ref for _, _, o in previous for ref in o["evidence_ids"]})
                if routing:
                    payload = {"status": "PASS", "summary": "Understood the complete supplied goal.", "evidence_ids": evidence, "intent": kind, "use_repository": mode == "auto_repository"}
                elif verifier:
                    refs = evidence or [handoff["inputs"][0]["id"]]
                    payload = {"status": "PASS", "summary": "Independent fixture assessment after bounded reads.", "evidence_ids": refs,
                        "criterion_checks": [{"criterion_index": i, "status": "SATISFIED", "reason": "The actual owned goal and deliverables satisfy this bounded fixture criterion.", "evidence_ids": refs[:1]} for i in range(len(handoff["criteria"]))],
                        "issue_checks": [{"issue_index": i, "status": "RESOLVED", "reason": "Later independent corroboration resolves this earlier bounded missing-evidence issue.", "evidence_ids": refs[:1]} for i in range(len(handoff.get("prior_stage_issues", [])))],
                        "claim_checks": [{"claim_index": i, "status": "SUPPORTED", "reason": "The exact quotation supports this fixture assertion in context.", "source_ids": claim["source_ids"]} for i, claim in enumerate(handoff["claims"])],
                        "conflict_source_ids": handoff["source_ids"], "conflict_status": "UNRESOLVED" if mode == "contradiction" else "NO_CONFLICT_FOUND", "conflict_reason": "Fixture sources were compared for disagreement and missing context."}
                    if mode == "missing_criterion": payload["criterion_checks"].pop()
                else:
                    sources = [o["data"]["source_id"] if n == "read_repo_file" else o["data"]["id"] for n, _, o in previous if n in {"fetch_webpage", "read_repo_file"}]
                    payload = {"status": "PASS", "summary": "A useful supported fixture answer with requested constraints.", "evidence_ids": evidence, "artifact_ids": artifacts,
                        "claims": [] if general else [{"claim": QUOTE if mode != "unsupported_quote" else "An invented statement with no support in the actual source.", "source_ids": sources[-1:], "confidence": "supported"}], "unknowns": []}
                if not verifier and mode in {"interim_partial", "unresolved_prior_issue"} and "Assigned bounded stage: Investigate" in content:
                    payload["status"] = "PARTIAL"
                    payload["unknowns"] = ["Independent corroboration is still required."]
                if verifier and mode == "unresolved_prior_issue" and payload["issue_checks"]:
                    payload["issue_checks"][0]["status"] = "UNRESOLVED"
                message = {"role": "assistant", "content": json.dumps(payload)}; reason = "stop"
            return ModelResponse(message, ModelUsage(10, 10, Decimal("0.0001")), self.model_id, reason)
    async def search(self, *args, **kwargs): return SearchResponse(({"url": "https://example.org/policy", "title": "Official fixture", "content": QUOTE},), 1, Decimal("0.0001"))
    async def fetch(self, *args, **kwargs): return SimpleNamespace(url="https://example.org/policy", title="Official fixture", content=QUOTE)
    async def resolve(url, hosts): return PublicURL(url, "example.org", ("1.1.1.1",))
    for test_mode, kind, expected in (("auto_repository", "research", "COMPLETED"), ("auto_positive", "general", "COMPLETED"), ("auto_positive", "research", "COMPLETED"), ("positive", "general", "COMPLETED"), ("fake_pass", "general", "PARTIAL"), ("unread_attachment", "general", "PARTIAL"), ("positive", "research", "COMPLETED"), ("contradiction", "research", "PARTIAL"), ("missing_criterion", "research", "PARTIAL"), ("unsupported_quote", "research", "PARTIAL"), ("interim_partial", "research", "COMPLETED"), ("unresolved_prior_issue", "research", "PARTIAL")):
        stage = test_mode + " " + kind; mode = "positive" if test_mode == "auto_positive" else test_mode
        goal = "Write a useful concise draft preserving the final constraint." if kind == "general" else "Compare the fixture policy; evaluate contrary sources and synthesize evidence."
        attachments = (TextAttachment(name="owned.txt", content="A" * 5000 + "\nFINAL CONSTRAINT: preserve this complete attachment."),) if kind == "general" else ()
        request = TaskCreate(project_id=project, kind="general" if test_mode.startswith("auto_") else kind, goal=goal, max_cost_usd=Decimal("0.8"), max_wall_time_sec=600, attachments=attachments)
        if test_mode.startswith("auto_"):
            with sessions.begin() as db:
                task = TaskRepository(sessions).create_in_session(db, user, request, automatic={"repository": {"repo_id": "fixture/repository", "ref": "main"} if mode == "auto_repository" else None})
        else:
            task = TaskRepository(sessions).create(user, request)
        jobs = JobRepository(sessions); lease = jobs.claim("research-general-fixture"); check(lease and lease.task_id == task.id, "fixture lease")
        store = WorkflowStore(sessions, lease)
        selected_settings = settings.model_copy(update={"tavily_api_key": None}) if kind == "general" or mode == "auto_repository" else settings
        from agentforge.integrations.github_client import GitHubClient, ResolvedRepository, RepositorySnapshot
        async def repo_resolve(self, repo_id, ref): return ResolvedRepository(repo_id, "a" * 40, "main")
        async def repo_snapshot(self, repo_id, ref): return RepositorySnapshot(repo_id, ref, {"policy #1.md": QUOTE.encode()})
        with patch.object(GitHubClient, "resolve", repo_resolve), patch.object(GitHubClient, "snapshot", repo_snapshot), patch.object(ModelRouter, "from_settings", return_value=ModelRouter(FixtureModel())), patch.object(TavilyClient, "search", search), patch.object(SafeWebFetcher, "fetch", fetch), patch("agentforge.integrations.safe_fetch.resolve_public_url", resolve):
            runtime = compose_runtime(selected_settings, sessions, telemetry, store)
            async def authority(): jobs.heartbeat(lease)
            config = {"configurable": {"thread_id": task.thread_id}, "recursion_limit": 40}
            if test_mode.startswith("auto_"):
                from agentforge.orchestration.automatic import route_turn
                github = GitHubClient() if mode == "auto_repository" else SimpleNamespace(resolve=lambda *args: (_ for _ in ()).throw(AssertionError("unrelated intent tried GitHub")))
                await route_turn(store=store, service=runtime.service, settings=selected_settings, github=github, authority=authority, config=config)
                check(store.task().permissions_json["kind"] == kind, "semantic intent was not persisted")
                check(not store.steps(), "routing polluted execution plan steps")
                await runtime.close()
                runtime = compose_runtime(selected_settings, sessions, telemetry, store)
            graph = build_graph(store=store, service=runtime.service, verifier=VerificationService(sessions), authority=authority, checkpointer=InMemorySaver())
            await graph.ainvoke({"task_id": str(task.id)}, config); await runtime.close()
        row = store.task()
        if row.status != expected: print("RECEIPT " + json.dumps(row.final_result, default=str), flush=True)
        check(row.status == expected, stage + " terminal status " + row.status)
        ctx = research_context(row); repository = ResearchInputRepository(sessions)
        if mode == "auto_repository":
            sources = repository.list_sources(**repository._scope(ctx))
            check(sources and all(source["source_type"] == "repository" for source in sources), "repository sources not persisted")
            check(all("policy%20%231.md" in source["url"] and "/blob/" + "a" * 40 in source["url"] for source in sources), "repository URL not pinned/escaped")
        if expected == "COMPLETED":
            check(not (await graph.aget_state(config)).next, "graph left pending work"); check(row.final_result["verification"]["status"] == "PASS", "missing verified receipt")
            if kind == "general":
                artifact = repository.get_artifact(row.final_result["research"]["artifact_ids"][0], **repository._scope(ctx)); check(artifact["kind"] == "general_markdown", "general artifact missing")
                try: repository.get_artifact(artifact["id"], task_id=row.id, owner_id=outsider, project_id=project)
                except ScopeDenied: pass
                else: raise AssertionError("cross-owner artifact access")
            else: check(row.final_result["research"]["stages"] == 3, "complex research was not decomposed")
        jobs.complete(lease); print("PASS shared runtime " + stage + " -> " + expected, flush=True)
    telemetry.shutdown()
try:
    source = make_url(Settings().database_url); check(source.host in ("localhost", "127.0.0.1", "::1"), "nonlocal configured DB")
    admin = admin_engine(source)
    with admin.connect() as connection:
        owner = connection.dialect.identifier_preparer.quote_identifier(source.username); connection.execute(text('CREATE DATABASE "' + name + '" OWNER ' + owner))
    created = True
    with tempfile.TemporaryDirectory(prefix="agentforge-research-fixture-", dir=ROOT / ".local") as artifact_dir:
        settings = Settings(_env_file=None, database_url=source.set(database=name).render_as_string(hide_password=False), langgraph_checkpoint_db_url=source.set(drivername="postgresql", database=name).render_as_string(hide_password=False), app_env="test", auth_mode="disabled", llm_api_key=None, tavily_api_key="fixture-only", neatlogs_api_key=None, research_storage_path=artifact_dir)
        with patch.object(settings_module, "get_settings", return_value=settings): asyncio.run(run(settings))
except BaseException as exc:
    import traceback
    print("FAIL stage=" + stage + " class=" + type(exc).__name__, flush=True)
    if isinstance(exc, AssertionError): print("ASSERTION " + str(exc), flush=True)
    for frame in traceback.extract_tb(exc.__traceback__): print(f"LOCATION {Path(frame.filename).name}:{frame.lineno} {frame.name}", flush=True)
    raise SystemExit(1) from None
finally:
    if engine is not None: engine.dispose()
    if created:
        with admin.connect() as connection: connection.execute(text('DROP DATABASE "' + name + '" WITH (FORCE)'))
        print("PASS disposable database removed", flush=True)
    if admin is not None: admin.dispose()
