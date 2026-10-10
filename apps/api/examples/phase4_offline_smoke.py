"""Disposable PostgreSQL + shared runtime offline coding qualification.
Only exact generated fixture programs run locally; real repositories never do.
"""
import asyncio
from datetime import datetime,timedelta,timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from unittest.mock import patch
from uuid import uuid4,UUID
from sqlalchemy import create_engine,select,text
from sqlalchemy.engine import make_url,URL
from sqlalchemy.orm import sessionmaker
from agentforge.settings import Settings
from _disposable_db import admin_engine
import agentforge.settings as settings_module
ROOT=Path(__file__).resolve().parents[3]
DB_NAME='agentforge_p4_smoke_'+uuid4().hex[:16]
ARTIFACTS=ROOT/'.local'/DB_NAME
admin=engine=None
created=False
stage='configuration'
def ok(message): print('PASS '+message,flush=True)
def check(condition,message):
    if not condition: raise AssertionError(message)
BASE=b'def add(a, b):\n    return a - b\n'
WRONG=b'def add(a, b):\n    return a * b\n'
FIXED=b'def add(a, b):\n    return a + b\n'
TEST=b'import unittest\nfrom app import add\nclass Addition(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(3, 2), 5)\n'
FILES={'app.py':BASE,'test_app.py':TEST,'asset.bin':b'\x00\x80'}
SHA='a'*40
async def run(settings):
    global stage,engine
    from alembic import command
    from alembic.config import Config
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    stage='migrations'
    config=Config(str(ROOT/'apps/api/alembic.ini'));config.set_main_option('script_location',str(ROOT/'apps/api/alembic'))
    command.upgrade(config,'head')
    from agentforge.database.models import Base,User,Project,Task,TaskJob,AgentRun,BudgetReservation,BudgetOperation
    from agentforge.database.coding import CodingSnapshot,CodingSession
    engine=create_engine(settings.database_url)
    with engine.connect() as c:
        differences=compare_metadata(MigrationContext.configure(c,opts={'compare_type':True,'compare_server_default':True}),Base.metadata)
        if differences: print('SCHEMA_DIFF '+repr(differences),flush=True)
        check(not differences,'migration metadata drift')
    ok('migration0005 and zero metadata drift on unique disposable DB')
    sessions=sessionmaker(engine,expire_on_commit=False)
    user,project=uuid4(),uuid4()
    with sessions.begin() as s:
        s.add(User(id=user,github_user_id=-4001,display_name='Fixture'));s.flush();s.add(Project(id=project,owner_id=user,name='Offline phase4'))
    from agentforge.contracts.tasks import TaskCreate
    from agentforge.contracts.coding import CodingTaskSpec,RepoSelection
    from agentforge.repositories.tasks import TaskRepository
    from agentforge.repositories.jobs import JobRepository,LeaseLost
    from agentforge.orchestration.store import WorkflowStore
    from agentforge.orchestration.coding_context import coding_context
    from agentforge.repositories.coding import CodingRepository
    from agentforge.integrations.artifact_storage import ArtifactStorage
    from agentforge.integrations.github_client import RepositorySnapshot,GitHubClient
    from agentforge.integrations.e2b_client import E2BRunner
    from agentforge.sandbox.contracts import CommandResult
    from agentforge.sandbox.workspace import workspace_revision
    from agentforge.llm.models import ModelResponse,ModelUsage
    from agentforge.llm.router import ModelRouter
    from agentforge.telemetry.neatlogs_setup import Telemetry
    from agentforge.workers.runner import execute
    from agentforge.workers.reconciliation import reconcile
    from agentforge.subagents.budget import BudgetService
    from agentforge.subagents.scope import ScopeDenied
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    jobs=JobRepository(sessions)
    telemetry=Telemetry(settings)
    check(telemetry.sdk is None,'telemetry must be off')
    role_calls=[];remote_calls=[]
    fixture_mode='normal'
    class FixtureModel:
        model_id='fixture/offline-phase4'
        def maximum_request_cost(self,messages,tools): return Decimal('0.001')
        async def complete(self,messages,tools):
            brief=json.loads(messages[1]['content']);role=brief['role']['name'];prompt=brief['goal_and_acceptance_brief']
            previous=[json.loads(m['content'].split('\n',1)[1]) for m in messages if m['role']=='tool']
            role_calls.append(role)
            workspace=CodingRepository(sessions).session(coding_context(WorkflowStore(sessions,current_lease).task()))
            refs=brief['authorized_context_refs']
            n=len(previous)
            if role=='general-executor':
                if n==0: name,args='read_sandbox_file',dict(sandbox_id=workspace['id'],path='app.py')
                elif n==1:
                    data=WRONG if 'Attempt 1;' in prompt or fixture_mode=='never-fix' else FIXED
                    name,args='write_sandbox_file',dict(sandbox_id=workspace['id'],path='app.py',content=data.decode(),expected_sha256=previous[-1]['data']['sha256'])
                elif n==2: name,args='run_tests',dict(sandbox_id=workspace['id'],test_profile='python_unittest')
                else:
                    check(previous[-1]['status']=='ok','executor actual fixture test tool')
                    ids=previous[-1]['evidence_ids']
                    payload=dict(status='PASS',summary='Fixture change made; actual tests recorded.',changed_paths=['app.py'],test_evidence_ids=ids,evidence_ids=ids)
                    return ModelResponse({'role':'assistant','content':json.dumps(payload)},ModelUsage(10,10,Decimal('0.0001')),self.model_id,'stop')
            else:
                if fixture_mode=='assertion-only-review':
                    payload=dict(status='PASS',summary='Unsubstantiated fixture assertion.',findings=[],acceptance_checks=['I assert correctness.'],evidence_ids=refs)
                    return ModelResponse({'role':'assistant','content':json.dumps(payload)},ModelUsage(10,10,Decimal('0.0001')),self.model_id,'stop')
                check('write_sandbox_file'  not in brief['permitted_tools'] and 'run_tests' not in brief['permitted_tools'],'reviewer readonly')
                if n==0: name,args='git_diff',dict(sandbox_id=workspace['id'],base_ref=SHA)
                elif n==1: name,args='read_sandbox_file',dict(sandbox_id=workspace['id'],path='app.py')
                elif n==2: name,args='get_test_results',dict(task_id=brief['parent_task_id'],run_id=refs[1])
                else:
                    check(all(p['status']=='ok' for p in previous),'review tool success')
                    payload=dict(status='PASS',summary='Fixture goal covered.',findings=[],acceptance_checks=['Actual current tests and source reviewed.'],evidence_ids=refs)
                    return ModelResponse({'role':'assistant','content':json.dumps(payload)},ModelUsage(10,10,Decimal('0.0001')),self.model_id,'stop')
            message={'role':'assistant','content':None,'tool_calls':[{'id':'fixture_'+str(n),'type':'function','function':{'name':name,'arguments':json.dumps(args)}}]}
            return ModelResponse(message,ModelUsage(10,10,Decimal('0.0001')),self.model_id,'tool_calls')
    async def snapshot(self,repo_id,ref): return RepositorySnapshot(repo_id,ref,dict(FILES))
    async def fixture_run(self,files,profile_id,*,operation_id,authority):
        await authority()
        # This adapter accepts generated fixture constants only, never a downloaded repository.
        check(files.keys()==FILES.keys() and files['app.py'] in {BASE,WRONG,FIXED} and files['test_app.py']==TEST and files['asset.bin']==FILES['asset.bin'],'only fixture programs run on host')
        with tempfile.TemporaryDirectory(prefix='agentforge_generated_fixture_') as tmp:
            for path,content in files.items(): Path(tmp,path).write_bytes(content)
            actual=subprocess.run([sys.executable,'-m','unittest','discover','-s',tmp,'-v'],cwd=tmp,env={'PATH':os.environ['PATH'],'PYTHONPATH':tmp},capture_output=True,text=True,timeout=10)
        remote_calls.append((operation_id,actual.returncode))
        return CommandResult('fixture-generated-local','fixture-sandbox',profile_id,('python','-m','unittest'),workspace_revision(files),actual.returncode,actual.stdout,actual.stderr,0.1,False,False,fixture_mode!='cleanup-unknown',Decimal('0.002'))
    async with AsyncPostgresSaver.from_conn_string(settings.langgraph_checkpoint_db_url) as saver:
        await saver.setup()
        spec=CodingTaskSpec(repository=RepoSelection(repo_id='fixture/repository',ref=SHA),test_profiles=('python_unittest',),max_repair_attempts=2)
        request=TaskCreate(project_id=project,kind='coding',coding=spec,goal='Fix add so adding three and two returns five and preserve the fixture test.',max_cost_usd=Decimal('3'))
        task=TaskRepository(sessions).create(user,request)
        current_lease=jobs.claim('fixture-worker')
        stage='shared workflow fail repair pass'
        with patch.object(ModelRouter,'from_settings',return_value=ModelRouter(FixtureModel())),patch.object(GitHubClient,'snapshot',snapshot),patch.object(E2BRunner,'run',fixture_run):
            await execute(settings,sessions,saver,telemetry,jobs,current_lease)
        result=TaskRepository(sessions).get(task.id,user)
        if result.status!='COMPLETED': print('DEBUG_RESULT '+str(result.final_result)+' ERROR '+str(result.error_code),flush=True)
        check(result.status=='COMPLETED','shared coding flow complete')
        check(result.final_result['coding']['repair_attempts']==1,'exactly one repair')
        check([code for _,code in remote_calls]==[1,1,0,0],'executor and parent actual fail then actual pass')
        check(set(role_calls)=={'general-executor','code-reviewer','verifier'},'three roles shared model/runtime')
        ok('real generated fixture exits 1->0; shared executor repair reviewer verifier; current-revision COMPLETED')
        stage='unsupported PASS and bounded repair'
        for mode in ('assertion-only-review','never-fix','cleanup-unknown'):
            fixture_mode=mode
            assertion_task=TaskRepository(sessions).create(user,request)
            current_lease=jobs.claim('negative-fixture-worker')
            before=len(remote_calls)
            with patch.object(ModelRouter,'from_settings',return_value=ModelRouter(FixtureModel())),patch.object(GitHubClient,'snapshot',snapshot),patch.object(E2BRunner,'run',fixture_run):
                await execute(settings,sessions,saver,telemetry,jobs,current_lease)
            receipt=TaskRepository(sessions).get(assertion_task.id,user)
            check(receipt.status=='PARTIAL',mode+' must not complete')
            if mode=='never-fix':
                check(receipt.final_result['coding']['repair_attempts']==2 and len(remote_calls)-before==6,'repair bound exactly two')
            if mode=='cleanup-unknown':
                check(receipt.error_code=='UNKNOWN_REMOTE_OUTCOME' and len(remote_calls)-before==1,'unknown cleanup stops all additional paid calls')
        fixture_mode='normal'
        ok('assertion-only reviewer PASS rejected; repairs capped at two; unknown cleanup stops after one execution')
        stage='storage and authority'

        with sessions() as database: original_task=database.get(Task,task.id)
        ctx=coding_context(original_task)
        repo=CodingRepository(sessions,ArtifactStorage(ARTIFACTS),lease=current_lease)
        artifacts=repo.list_artifacts(task_id=task.id,owner_id=user,project_id=project)
        check(any(a['kind']=='diff' for a in artifacts) and any(a['kind']=='test' for a in artifacts),'diff+log artifacts')
        for artifact in artifacts:
            full=repo.get_artifact(artifact['id'],task_id=task.id,owner_id=user,project_id=project)
            ArtifactStorage(ARTIFACTS).read(full['storage_key'],full['sha256'])
        check(repo.files(ctx)['asset.bin']==FILES['asset.bin'],'binaryasset retained')
        with sessions.begin() as s:
            try:
                s.execute(text("UPDATE coding_snapshots SET files_json='{}'::jsonb WHERE task_id=:task"),{'task':task.id})
            except Exception: pass
            else: raise AssertionError('immutable snapshot update allowed')
        other=TaskRepository(sessions).create(user,request)
        other_lease=jobs.claim('cas-worker');other_store=WorkflowStore(sessions,other_lease)
        other_store.transition('PLANNING');other_ctx=coding_context(other_store.task())
        other_repo=CodingRepository(sessions,ArtifactStorage(ARTIFACTS),lease=other_lease)
        other_repo.ensure_session(other_ctx,repo_id='fixture/repository',commit_sha=SHA,files=FILES)
        hashed=hashlib.sha256(BASE).hexdigest()
        written=other_repo.write_file(other_ctx,operation_key='cas-one',path='app.py',content=FIXED.decode(),expected_sha256=hashed)
        check(other_repo.write_file(other_ctx,operation_key='cas-one',path='app.py',content=FIXED.decode(),expected_sha256=hashed)==written,'idempotent CAS replay')
        for path,expected in [('app.py',hashed),('../bad',None),('asset.bin',hashlib.sha256(FILES['asset.bin']).hexdigest())]:
            try: other_repo.write_file(other_ctx,operation_key='deny-'+path,path=path,content='bad',expected_sha256=expected)
            except (ValueError,ScopeDenied): pass
            else: raise AssertionError('unsafe CAS allowed')
        try: other_repo.verify_references(other_ctx,evidence_ids=(result.final_result['coding']['diff_evidence_id'],),artifact_ids=())
        except ScopeDenied: pass
        else: raise AssertionError('cross-task evidence allowed')
        from agentforge.tools.sandbox_exec import CodingExecution
        async def other_authority(): await jobs.authority(other_lease)
        parent_execution=CodingExecution(other_repo,E2BRunner.__new__(E2BRunner),max_cost_usd=Decimal('0.1'))
        with patch.object(E2BRunner,'run',fixture_run):
            await parent_execution.parent_check(other_ctx,'python_unittest',operation_key='coding:parent:recovery:python_unittest',authority=other_authority)
        check(reconcile(other_store,BudgetService(sessions)) is False,'completed parent budget receipts reconcile without AgentRun')
        other_repo.begin_operation(other_ctx,operation_key='unknown-op',kind='test',inputs={'profile_id':'python_unittest'},revision=written['revision'])
        try: other_repo.begin_operation(other_ctx,operation_key='unknown-op',kind='test',inputs={'profile_id':'python_unittest'},revision=written['revision'])
        except RuntimeError: pass
        else: raise AssertionError('unknown operation replay allowed')
        TaskRepository(sessions).cancel(other.id,user)
        try: other_repo.write_file(other_ctx,operation_key='aftercancel',path='app.py',content=BASE.decode(),expected_sha256=hashlib.sha256(FIXED).hexdigest())
        except ScopeDenied: pass
        else: raise AssertionError('cancelled write allowed')
        await execute(settings,sessions,saver,telemetry,jobs,other_lease)
        ok('CAS conflict+replay, traversal, binaryedit, cross-task evidence, unknownpaidreplay, cancellation and immutable snapshots')
        stage='missing key'
        no_key=TaskRepository(sessions).create(user,request);key_lease=jobs.claim('no-key-worker')
        safe=settings.model_copy(update={'e2b_api_key':None,'e2b_template_id':None,'e2b_template_qualified':False})
        with patch.object(ModelRouter,'from_settings',return_value=ModelRouter(FixtureModel())),patch.object(E2BRunner,'run',side_effect=AssertionError('unconfigured paid call')):
            await execute(safe,sessions,saver,telemetry,jobs,key_lease)
        no_key_result=TaskRepository(sessions).get(no_key.id,user)
        check(no_key_result.error_code=='SANDBOX_CONFIGURATION_REQUIRED','missing E2B config receipt')
        with sessions() as s: check(not list(s.scalars(select(BudgetReservation).where(BudgetReservation.task_id==no_key.id))),'no paid budget ops with missing key')
        ok('missing E2B key/template blocks before model or paid sandbox calls')
    telemetry.shutdown()

try:
    configured = Settings()
    source = make_url(configured.database_url)
    check(source.host in ("localhost", "127.0.0.1", "::1"), "configured DB is not loopback; refusing")
    admin = admin_engine(source)
    with admin.connect() as connection:
        owner = connection.dialect.identifier_preparer.quote_identifier(source.username)
        connection.execute(text('CREATE DATABASE "' + DB_NAME + '" OWNER ' + owner))
    created = True
    safe = Settings(_env_file=None, database_url=source.set(database=DB_NAME).render_as_string(hide_password=False),
        langgraph_checkpoint_db_url=source.set(drivername="postgresql", database=DB_NAME).render_as_string(hide_password=False),
        app_env="test", auth_mode="local_single_user", local_user_id=uuid4(),
        local_bootstrap_token="offline-smoke-token-" + uuid4().hex,
        llm_api_key=None, tavily_api_key=None, neatlogs_api_key=None, research_storage_path=ARTIFACTS, e2b_api_key="fixture-key-never-remote", e2b_template_id="fixture-template", e2b_template_qualified=True)
    settings_module.get_settings = lambda: safe
    del configured
    asyncio.run(run(safe))
except BaseException as error:
    # Exception text/SQL/URLs could contain configured credentials; emit only class and source locations.
    import traceback
    print("FAIL stage=" + stage + " class=" + type(error).__name__, flush=True)
    if getattr(getattr(error, "orig", None), "sqlstate", None):
        print("SQLSTATE " + error.orig.sqlstate, flush=True)
    if isinstance(error, AssertionError):
        print("ASSERTION " + str(error), flush=True)
    for frame in traceback.extract_tb(error.__traceback__):
        print(f"LOCATION {Path(frame.filename).name}:{frame.lineno} {frame.name}", flush=True)
    sys.exitcode = 1
finally:
    if engine is not None:
        engine.dispose()
    if created:
        try:
            with admin.connect() as connection:
                connection.execute(text('DROP DATABASE "' + DB_NAME + '" WITH (FORCE)'))
            ok("disposable database dropped")
        except BaseException:
            print("CLEANUP_REQUIRED disposable database " + DB_NAME, flush=True)
    if admin is not None:
        admin.dispose()
    if ARTIFACTS.exists():
        shutil.rmtree(ARTIFACTS)
        ok("disposable artifact directory removed")
sys.exit(getattr(sys, "exitcode", 0))
