import assert from 'node:assert/strict';
import {test,afterEach} from 'node:test';
import {validateFiles,validAttachment,transferredFiles,uploadAttachment,attachmentGoal} from '../lib/attachments.ts';
const originalFetch=globalThis.fetch;
afterEach(()=>{globalThis.fetch=originalFetch;});
test('picker/drop/paste validate the same bounded formats',()=>{
 validateFiles([{name:'screen.PNG',size:100},{name:'report.pdf',size:100}],0);
 for(const file of [{name:'video.mp4',size:100},{name:'empty.txt',size:0},{name:'huge.pdf',size:9*1024*1024}])assert.throws(()=>validateFiles([file],0));
 assert.throws(()=>validateFiles([{name:'screen.png',size:100}],4));
});
test('clipboard extracts real files and leaves text/HTML alone',()=>{
 const file=new File(['image'],'image.png',{type:'image/png'});
 assert.deepEqual(transferredFiles({items:[{kind:'string',getAsFile:()=>null}],files:[]}),[]);
 assert.deepEqual(transferredFiles({items:[{kind:'file',getAsFile:()=>file}],files:[file]}),[file]);
 assert.deepEqual(transferredFiles({items:[],files:[file]}),[file]);
});
test('drafts retain complete media references and reject partial ones',()=>{
 assert.ok(validAttachment({name:'a.txt',content:'hello'}));
 const image={name:'image.png',content:'Image',upload_id:'11111111-1111-4111-8111-111111111111',upload_sha256:'a'.repeat(64),media_type:'image/png',byte_size:100};
 assert.ok(validAttachment(image));assert.ok(validAttachment(JSON.parse(JSON.stringify(image))));
 assert.equal(validAttachment({...image,upload_sha256:undefined}),false);
 assert.equal(validAttachment({...image,media_type:'image/svg+xml'}),false);
});
test('upload sends original bytes and surfaces useful server errors',async()=>{
 const file=new File(['pdf'],'report.pdf');
 globalThis.fetch=async(url,init)=>{
  assert.equal(url,'/api/v1/attachments?name=report.pdf');assert.equal(init.body,file);assert.equal(init.credentials,'same-origin');
  return new Response(JSON.stringify({detail:'Unlock this PDF before attaching it.'}),{status:422});
 };
 await assert.rejects(uploadAttachment(file,new AbortController().signal),/Unlock this PDF/);
});
test('file-only messages can be sent without losing their draft acknowledgement',()=>{
 assert.equal(attachmentGoal('',[{name:'image.png',content:'Image'}]),'Please review the attached files.');
 assert.equal(attachmentGoal('  Look here  ',[]),'Look here');assert.equal(attachmentGoal('',[]),'');
});

test('normal-sized Markdown is accepted above the old 16KB limit',()=>{
 validateFiles([{name:'project.md',size:100000},{name:'notes.markdown',size:40000}],0);
 assert.throws(()=>validateFiles([{name:'too-large.md',size:524289}],0),/512 KB/);
});

test('upload failures read as sentences, never as raw error codes',async()=>{
 const file=new File(['pdf'],'report.pdf');
 const answer=(detail,status)=>{globalThis.fetch=async()=>new Response(JSON.stringify({detail}),{status});};
 globalThis.window={dispatchEvent(){}};
 answer('session_required',401);
 await assert.rejects(uploadAttachment(file,new AbortController().signal),{message:'Your session has expired. Sign in again to continue.'});
 answer('attachment_unsupported_media',415);
 await assert.rejects(uploadAttachment(file,new AbortController().signal),{message:'Couldn’t attach report.pdf. Try again.'});
 answer({code:'nested'},400);
 await assert.rejects(uploadAttachment(file,new AbortController().signal),{message:'Couldn’t attach report.pdf. Try again.'});
 answer('This PDF has more than 20 pages.',422);
 await assert.rejects(uploadAttachment(file,new AbortController().signal),{message:'This PDF has more than 20 pages.'});
 delete globalThis.window;
});
