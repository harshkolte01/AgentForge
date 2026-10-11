"""Requested download names survive creation and delivery without becoming paths or headers."""
from contextlib import contextmanager
from tempfile import TemporaryDirectory
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import TestCase, IsolatedAsyncioTestCase
from unittest.mock import patch
from uuid import uuid4
from pydantic import ValidationError
from agentforge.contracts.artifact_names import artifact_filename
from agentforge.tools.contracts import TaskArtifactCreateArgs
from agentforge.tools.catalog import ToolCatalog
from agentforge.tools.general import register_general
from agentforge.api.routes_artifacts import download

class FilenameTests(TestCase):
    def test_requested_names_and_legacy_defaults(self):
        for format, name in [('text','notes.txt'),('markdown','project notes.md'),('csv','table.csv')]:
            args=TaskArtifactCreateArgs(format=format,title='Notes',content='hello',filename=name)
            self.assertEqual(artifact_filename(format,args.filename,args.title),name)
        self.assertEqual(artifact_filename('text',title='notes.txt'),'notes.txt')
        self.assertEqual(artifact_filename('text',title='Some notes'),'general-output.txt')

    def test_paths_headers_executables_and_reserved_names_are_rejected(self):
        for name in ['../notes.txt','dir/notes.txt','dir\\notes.txt','a.txt\r\nInjected: yes','a".txt','file.html','a.csv','CON.txt','.env','a..txt','a .txt']:
            with self.subTest(name=name), self.assertRaises(ValidationError):
                TaskArtifactCreateArgs(format='text',title='Notes',content='hello',filename=name)
            self.assertEqual(artifact_filename('text',name),'general-output.txt')

    def test_download_uses_validated_name_and_rejects_stored_header_injection(self):
        task_id, owner, artifact_id = uuid4(),uuid4(),uuid4()
        task=NS(id=task_id,project_id=uuid4(),kind='general')
        item={'kind':'general_text','filename':'notes.txt','storage_key':'private','sha256':'a'*64}
        request=NS(app=NS(state=NS(settings=NS(research_storage_path='/unused'))))
        with patch('agentforge.api.routes_artifacts.sessions'), patch('agentforge.api.routes_artifacts.ResearchRepository') as repo, patch('agentforge.api.routes_artifacts.ArtifactStorage') as storage:
            repo.return_value.get_artifact.return_value=item
            storage.return_value.read.return_value=b'hello'
            result=download(task_id,artifact_id,request,NS(id=owner),NS(get=lambda **kw:task))
            self.assertEqual(result.headers['content-disposition'],'attachment; filename="notes.txt"')
            item['filename']='evil.txt\r\nInjected: yes'
            result=download(task_id,artifact_id,request,NS(id=owner),NS(get=lambda **kw:task))
            self.assertEqual(result.headers['content-disposition'],'attachment; filename="general-output.txt"')

class CreationTests(IsolatedAsyncioTestCase):
    async def test_create_preserves_name_and_replay_without_another_artifact(self):
        records=[]
        task=NS(task_id=uuid4())
        @contextmanager
        def sessions(): yield NS()
        def add(_task,**kw):
            item={'id':str(kw['artifact_id']),**kw}
            records.append(item)
            return item
        repo=NS(_scope=lambda task:{},_owned=lambda *a,**kw:NS(permissions_json={'kind':'general'}),
                artifact_for_operation=lambda *a:records[0] if records else None,add_artifact=add)
        with TemporaryDirectory() as root, patch('agentforge.tools.general.ResearchInputRepository',return_value=repo):
            catalog=ToolCatalog(); register_general(catalog,sessions,NS(research_storage_path=Path(root).resolve()))
            handler=catalog.get('create_task_artifact').handler
            args=TaskArtifactCreateArgs(format='text',title='Notes',filename='notes.txt',content='hello')
            execution=NS(task=task,operation_key='one',authorize_current=None)
            first=await handler(args,execution); second=await handler(args,execution)
            self.assertEqual(first.data['filename'],'notes.txt')
            self.assertEqual(first,second)
            self.assertEqual(len(records),1)
