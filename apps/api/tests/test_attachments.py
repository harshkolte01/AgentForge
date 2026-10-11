"""Attachment ingestion, account isolation, media transport and unchanged legacy inputs."""
import base64
import io
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
from unittest import TestCase
from unittest.mock import patch
from uuid import uuid4

from PIL import Image
from pypdf import PdfWriter
from fastapi import FastAPI
from fastapi.testclient import TestClient
from agentforge.api.routes_attachments import router
from agentforge.auth.session import current_user
from agentforge.contracts.tasks import TextAttachment
from agentforge.integrations.attachment_storage import AttachmentStorage, InvalidAttachment, inspect_attachment, workspace_attachment_path
from agentforge.orchestration.agent_turn import conversation_for
from agentforge.llm.models import message_input_tokens
from agentforge.security.request_limits import RequestBodyLimitMiddleware


def picture():
    out=io.BytesIO();Image.new('RGB',(80,80),'blue').save(out,format='PNG');return out.getvalue()


def pdf(pages=1,password=None):
    out=io.BytesIO();writer=PdfWriter()
    for _ in range(pages):writer.add_blank_page(width=200,height=200)
    if password:writer.encrypt(password)
    writer.write(out);return out.getvalue()


class AttachmentsTests(TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();self.storage=AttachmentStorage(self.root);self.owner=uuid4()

    def test_text_contract_and_serialization_remain_backward_compatible(self):
        value={'name':'notes.txt','content':'hello'}
        self.assertEqual(TextAttachment(**value).model_dump(mode='json'),{'name':'notes.txt','content':'hello'})

    def test_image_and_pdf_use_native_provider_parts_and_durable_references(self):
        for name,data,kind in [('screen.png',picture(),'image_url'),('document.pdf',pdf(),'file')]:
            with self.subTest(name=name):
                value=self.storage.save(self.owner,name,data)
                attachment=TextAttachment(**value).model_dump(mode='json')
                self.assertNotIn('data',attachment)
                task=NS(goal='Explain this',created_by=self.owner,permissions_json={'attachments':[attachment]})
                part=conversation_for(task,attachment_storage=self.storage)[-1]['content'][1]
                self.assertEqual(part['type'],kind)
                url=part['image_url']['url'] if kind=='image_url' else part['file']['file_data']
                self.assertEqual(base64.b64decode(url.split(',')[1]),data)
                self.assertEqual(self.storage.load(self.owner,attachment)['name'],name)

    def test_cross_account_and_tampered_references_are_rejected(self):
        value=self.storage.save(self.owner,'screen.png',picture())
        for owner,attachment in [(uuid4(),value),(self.owner,{**value,'upload_sha256':'0'*64}),(self.owner,{**value,'name':'other.png'})]:
            with self.assertRaises(InvalidAttachment):self.storage.load(owner,attachment)

    def test_rejects_unsupported_invalid_animated_and_encrypted_files(self):
        for name,data in [('image.png',b'bad'),('script.svg',b'<svg/>'),('empty.txt',b''),('binary.txt',b'\0'),('../x.txt',b'hi'),('locked.pdf',pdf(password='secret')),('large.pdf',pdf(21))]:
            with self.subTest(name=name),self.assertRaises(InvalidAttachment):inspect_attachment(name,data)

    def test_quota_failure_leaves_no_upload(self):
        with patch('agentforge.integrations.attachment_storage.MAX_OWNER_BYTES',1),self.assertRaises(InvalidAttachment):
            self.storage.save(self.owner,'screen.png',picture())
        self.assertFalse(list((self.root/'attachments').glob('*/*.md')))

    def test_visual_budget_is_not_base64_text_length(self):
        for name,data,tokens in [('image.png',picture(),8192),('file.pdf',pdf(2),65536)]:
            value=self.storage.save(self.owner,name,data)
            messages=conversation_for(NS(goal='Describe',created_by=self.owner,permissions_json={'attachments':[value]}),attachment_storage=self.storage)
            self.assertGreater(message_input_tokens(messages),tokens)
            self.assertLess(message_input_tokens(messages),tokens+1000)

    def test_workspace_names_cannot_escape_attachment_directory(self):
        path=workspace_attachment_path(uuid4(),0,'../../file.txt')
        self.assertEqual(len(path.split('/')),4)
        self.assertTrue(path.startswith('.agentforge/attachments/'))

    def client(self):
        app=FastAPI();app.include_router(router)
        app.add_middleware(RequestBodyLimitMiddleware)
        app.state.settings=NS(research_storage_path=self.root)
        app.dependency_overrides[current_user]=lambda:NS(id=self.owner)
        return TestClient(app)

    def test_http_upload_preview_and_other_owner_denial(self):
        with self.client() as client:
            response=client.post('/api/v1/attachments?name=screenshot.png',content=picture())
            self.assertEqual(response.status_code,201)
            attachment=response.json();url=f"/api/v1/attachments/{attachment['upload_id']}?sha256={attachment['upload_sha256']}"
            response=client.get(url);self.assertEqual(response.status_code,200)
            self.assertEqual(response.content,picture());self.assertIn('no-store',response.headers['cache-control'])
            self.owner=uuid4();self.assertEqual(client.get(url).status_code,404)

    def test_large_file_is_rejected_before_buffering(self):
        with self.client() as client:
            response=client.post('/api/v1/attachments?name=large.pdf',content=b'',headers={'Content-Length':str(8*1024*1024+1)})
            self.assertEqual(response.status_code,413)

    def test_large_valid_image_uses_upload_limit_not_artifact_limit(self):
        import os
        out=io.BytesIO();Image.frombytes('RGB',(900,900),os.urandom(900*900*3)).save(out,format='PNG')
        data=out.getvalue();self.assertGreater(len(data),2_000_000)
        attachment=self.storage.save(self.owner,'large.png',data)
        self.assertEqual(self.storage.load(self.owner,attachment)['byte_size'],len(data))


    def test_large_markdown_is_preserved_in_full_beyond_old_inline_limit(self):
        content = '# Heading\n' + ('Detailed markdown paragraph.\n' * 3000) + 'END_OF_DOCUMENT'
        value = self.storage.save(self.owner,'notes.md',content.encode())
        self.assertLess(len(value['content']), 8000)
        self.assertEqual(TextAttachment(**value).media_type,'text/plain')
        self.assertIn(content,self.storage.part(self.owner,value)['text'])
        self.assertEqual(base64.b64decode(self.storage.load(self.owner,value)['data']).decode(),content)

    def test_utf16_markdown_with_bom_is_supported(self):
        value=self.storage.save(self.owner,'notes.md','# Notes\nHello'.encode('utf-16'))
        self.assertIn('# Notes\nHello', self.storage.part(self.owner,value)['text'])

    def test_four_markdown_files_fit_the_durable_turn_envelope(self):
        from agentforge.contracts.sessions import AutoTurnCreate
        values=[self.storage.save(self.owner,f'note-{i}.md',b'a'*20000) for i in range(4)]
        turn=AutoTurnCreate(routing='auto',idempotency_key=uuid4(),goal='Read these',attachments=values)
        self.assertEqual(len(turn.attachments),4)
