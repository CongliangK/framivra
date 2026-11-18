import { domTextExcerpt } from './dom-diagnostics.mjs'
import test from 'node:test'
import assert from 'node:assert/strict'
import React, { act } from 'react'
import { JSDOM } from 'jsdom'
import { writeFile, symlink, rename } from 'node:fs/promises'
import { join } from 'node:path'
import { PreviewBody } from '../src/client/index.mjs'
import { parseJSON, stringifyJSON } from '../src/json.mjs'
import { environment, fixture, realProvider, skip } from './manual-env.mjs'
const h=React.createElement
async function mount(t){const dom=new JSDOM('<div id="root"></div>',{url:'http://owner/'});globalThis.window=dom.window;globalThis.document=dom.window.document;globalThis.IS_REACT_ACT_ENVIRONMENT=true;const {createRoot}=await import('react-dom/client'),{Simulate}=await import('react-dom/test-utils');const root=createRoot(document.getElementById('root'));t.after(async()=>{await act(async()=>root.unmount());dom.window.close();delete globalThis.window;delete globalThis.document});return {dom,root,Simulate}}
const button=text=>[...document.querySelectorAll('button')].find(n=>n.textContent===text)
async function settle(predicate){for(let i=0;i<400;i++){if(predicate())return;await act(async()=>{await new Promise(r=>setTimeout(r,5))})}assert.fail(`DOM did not settle: ${domTextExcerpt(document)}`)}
async function click(text){const b=button(text);assert.equal(Boolean(b), true,text);assert.equal(b.disabled,false,text);await act(async()=>b.click())}
// Deleted human forms are not an authority surface. Preserve all semantic
// operation coverage through the actual Host and installed core contracts.
const operationCases = [
 {op:'source.add',data:{id:'new-source',path:'new.mov'}},
 {op:'source.remove',id:'spare'},
 {op:'source.update',id:'s',set:{path:'changed.mov'}},
 {op:'track.add',family:'video',index:0,data:{id:'new-track',name:'new',kind:'aroll',clips:[{id:'new-clip',src:'s',dst_in:0,dur:10,src_in:0,src_out:10}]}},
 {op:'track.remove',id:'v'},
 {op:'track.copy',id:'v',ids:{v:'copy-v',c:'copy-c',c2:'copy-c2'},name:'copy-main',index:0},
 {op:'track.reorder',family:'video',ids:['g','v']},
 {op:'track.rename',id:'v',name:'renamed'},
 {op:'track.references',id:'bed',set:{duck_by:['voice']}},
 {op:'block.insert',track_id:'v',data:{id:'inserted',src:'s',dst_in:30,dur:10,src_in:0,src_out:10}},
 {op:'block.remove',id:'c'},
 {op:'block.copy',id:'c',track_id:'v',at:90,new_id:'copy-c'},
 {op:'block.move',id:'c',track_id:'v',at:30},
 {op:'block.relink',id:'c',src:'spare'},
 {op:'block.trim',id:'c',start:2,end:28,fade_policy:'preserve-content'},
 {op:'block.split',id:'c',at:15,new_id:'split-c',fade_policy:'preserve-content'},
 {op:'parameters.update',id:'c',path:['opacity'],value:0.75},
 {op:'parameters.unset',id:'c',path:['opacity']},
 {op:'project.resize',duration_frames:130},
 {op:'range.ripple',scope:{track_ids:['v'],subtitles:false},mode:'shift',at:60,delta:10,crossing:'reject',resize:false},
]
test('20 semantic operations -> real Host -> frozen public provider -> installed CLI plan/commit -> core head undo',{skip,timeout:120000},async t=>{
 const coverage=[]
 for(const operation of operationCases){
  const doc=fixture();doc.sources.push({id:'spare',path:'unused.wav'});doc.video_tracks[0].clips[0].opacity=0.5
  const env=await environment(t,`contract-${operation.op}`,doc),grant=env.opened.grant
  const {writeGrant}=await env.call('one',{op:'authorize-write',grant}),before=await env.read()
  const plan=await env.call('one',{op:'plan',grant,writeGrant,base_revision:env.opened.snapshot.content_version,operations:[operation]})
  assert.equal(plan.ok,true,stringifyJSON(plan));assert.equal(await env.read(),before,'planning is zero-write')
  const opId=plan.submitted_request.op_id,commit=await env.call('one',{op:'commit',grant,writeGrant,planToken:plan.planToken})
  assert.equal(commit.ok,true,stringifyJSON(commit));assert.equal(commit.op_id,opId);assert.deepEqual(commit.submitted_request,plan.submitted_request)
  const after=parseJSON(await env.read());assert.deepEqual(after.unknown_root,doc.unknown_root);assert.equal(after._vtx.head,opId)
  assert.equal((await env.call('one',{op:'status',grant,writeGrant})).ok,true)
  const snapshot=await env.call('one',{op:'inspect',grant}),undo=await env.call('one',{op:'undo',grant,writeGrant,base_revision:snapshot.content_version,head:after._vtx.head})
  assert.equal(undo.ok,true,stringifyJSON(undo));const undone=parseJSON(await env.read());delete undone._vtx;assert.deepEqual(JSON.parse(stringifyJSON(undone)),doc)
  for(const child of env.handles)assert.equal(await child.waitForExit(AbortSignal.timeout(5000)),true)
  await env.save('contract-wire.json',env.wire);coverage.push({op:operation.op,op_id:opId,core_commit:true,core_undo:true,processes_reaped:env.handles.length});await env.host.dispose()
 }
 assert.equal(coverage.length,20)
 await writeFile(join(process.env.SIDEBAR_EVIDENCE_DIR,'manual-20ops.json'),stringifyJSON({adapter:'real public LocalSubprocessRuntime + installed vtx; direct Host contracts, not deleted UI forms',coverage},2))
})

test('actual PreviewBody gesture preview -> direct commit -> authoritative inspect/query and exact raw',{skip,timeout:30000},async t=>{
 const env=await environment(t,'gesture');const {root,Simulate}=await mount(t);const previous=globalThis.fetch;t.after(()=>{globalThis.fetch=previous})
 globalThis.fetch=async(_url,init)=>{const answer=await env.host.control(new Request('http://owner/api/framivra/control',{method:'POST',body:init.body,signal:init.signal}));env.wire.push({request:parseJSON(init.body),status:answer.status,response:parseJSON(await answer.clone().text())});return answer}
 const controller=new AbortController();await act(async()=>root.render(h(PreviewBody,{sessionId:'one',useTabInfo:()=>({tab:{signal:controller.signal}})})))
 await act(async()=>document.querySelector('form').dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true})));await settle(()=>document.querySelector('.fv-ruler'))
 const ruler=document.querySelector('.fv-ruler');ruler.getBoundingClientRect=()=>({left:0,width:1200})
 document.querySelectorAll('.fv-track').forEach((track,i)=>{track.getBoundingClientRect=()=>({top:i*100,bottom:(i+1)*100})})
 const clip=()=>document.querySelector('[aria-label^="选择块 /video_tracks/0/clips/0"]');await act(async()=>clip().click());await settle(()=>document.body.textContent.includes('引用就绪。'))
 const disabledBefore=await env.read(),disabledCalls=env.wire.length
 await act(async()=>Simulate.pointerDown(clip(),{pointerId:7,button:0,clientX:0,clientY:10}))
 await act(async()=>Simulate.pointerUp(clip(),{pointerId:7,clientX:900,clientY:10}))
 assert.equal(await env.read(),disabledBefore)
 assert.equal(env.wire.slice(disabledCalls).some(row=>['authorize-write','plan','commit'].includes(row.request.op)),false,'disabled drag cannot authorize or write')
 const bottomEnable=document.querySelector('.fv-drag-feedback button')
 assert.equal(bottomEnable?.textContent,'启用编辑')
 await act(async()=>bottomEnable.click());await settle(()=>button('退出编辑')?.disabled===false)
 assert.equal(env.wire.filter(row=>row.request.op==='authorize-write').length,1,'bottom enable uses the actual edit authorization')
 const history=document.querySelector('.fv-topbar .fv-history')
 assert.equal(history!==null,true);assert.equal(history.querySelector('summary').getAttribute('aria-label'),'历史与恢复')
 const before=await env.read(),count=env.specs.length
 await act(async()=>Simulate.pointerDown(clip(),{pointerId:1,button:0,clientX:0,clientY:10}));await act(async()=>Simulate.pointerMove(clip(),{pointerId:1,clientX:900,clientY:10}))
 assert.equal(Boolean(document.querySelector('[aria-label="未保存拖动 ghost"]')), true);assert.equal(await env.read(),before);assert.equal(env.specs.length,count)
 await act(async()=>Simulate.pointerUp(clip(),{pointerId:1,clientX:900,clientY:10}));assert.equal(Boolean(document.querySelector('[aria-label="未保存拖动 ghost"]') === null), true, "Expected absent DOM node")
 await settle(()=>env.wire.some(row=>row.request.op==='commit'))
 await settle(()=>document.body.textContent.includes('已保存。'))
 assert.equal(Boolean(document.querySelector('[aria-label="待保存操作"]') === null), true, "Expected absent DOM node")
 assert.equal(parseJSON(await env.read()).video_tracks[0].clips.find(b=>b.id==='c').dst_in,90)
 assert.equal(env.wire.filter(row=>row.request.op==='plan').length,1)
 assert.equal(env.wire.filter(row=>row.request.op==='commit').length,1)
 assert.equal(document.querySelector('.fv-topbar .fv-history')===history,true,'same topbar history survives authoritative refresh')
 assert.equal(env.wire.some(row=>row.request.op.startsWith('proposal-')),false)
 // Core move may reorder its array; refresh must preserve the stable ID, not
 // accidentally retain the old address now occupied by another clip.
 await settle(()=>document.querySelector('[aria-label="活动引用"]')?.textContent.includes('/video_tracks/0/clips/1'))
 const snapshot=await env.call('one',{op:'inspect',grant:env.opened.grant}),raw=await env.call('one',{op:'reference',grant:env.opened.grant,query:{block_ids:['c'],expected_content_version:snapshot.content_version}})
 assert.match(raw.selection.blocks[0].raw,/"dst_in":\s*90\b/)
 assert.equal(Boolean((await env.read()).includes(raw.selection.blocks[0].raw)), true,'reference must be an exact saved-source substring, not presentation serialization')
 await env.save('gesture-result.json',{before,after:await env.read(),raw:raw.selection.blocks[0].raw,adapter:'owned local geometry stub; real pointer handlers; not browser layout claim'})
})

test('real explicit write auth / stale CAS / invalidation / foreign session,cwd,canonical path / frozen request replay',{skip,timeout:30000},async t=>{
 const env=await environment(t,'negative'),grant=env.opened.grant,base=env.opened.snapshot.content_version
 await assert.rejects(()=>env.call('one',{op:'plan',grant,base_revision:base,operations:[{op:'project.resize',duration_frames:130}]}),/WRITE_DENIED/)
 const {writeGrant}=await env.call('one',{op:'authorize-write',grant})
 await assert.rejects(()=>env.call('two',{op:'status',grant,writeGrant}),/BAD_GRANT/)
 const operations=[{op:'project.resize',duration_frames:130}],plan=await env.call('one',{op:'plan',grant,writeGrant,base_revision:base,operations});assert.equal(plan.ok,true,stringifyJSON(plan))
 await env.call('one',{op:'invalidate-plan',grant,writeGrant});await assert.rejects(()=>env.call('one',{op:'commit',grant,writeGrant,planToken:plan.planToken}),/PLAN_INVALID/)
 const stale=await env.call('one',{op:'plan',grant,writeGrant,base_revision:base,operations}),external=(await env.read())+'\n';await writeFile(env.path,external)
 const rejected=await env.call('one',{op:'commit',grant,writeGrant,planToken:stale.planToken});assert.equal(rejected.ok,false);assert.equal(rejected.code,'conflict');assert.equal(await env.read(),external)
 const newBase=await env.call('one',{op:'inspect',grant}),fresh=await env.call('one',{op:'plan',grant,writeGrant,base_revision:newBase.content_version,operations}),committed=await env.call('one',{op:'commit',grant,writeGrant,planToken:fresh.planToken});assert.equal(committed.ok,true)
 const {TimelineReader}=await import('../src/protocol.mjs'),reader=new TimelineReader(await realProvider(t));t.after(()=>reader.dispose());const signal=AbortSignal.timeout(10000)
 const replay=await reader.store(process.env.SIDEBAR_REAL_VTX,env.path,'commit',fresh.submitted_request,signal);assert.equal(replay.ok,true);assert.equal(replay.replayed,true)
 const reuse=await reader.store(process.env.SIDEBAR_REAL_VTX,env.path,'commit',{...fresh.submitted_request,operations:[{op:'project.resize',duration_frames:140}]},signal);assert.equal(reuse.code,'idempotency.conflict')
 env.cwd.value=join(env.dir,'other');await assert.rejects(()=>env.call('one',{op:'status',grant,writeGrant}),/BAD_GRANT/);env.cwd.value=env.dir
 await assert.rejects(()=>env.call('one',{op:'status',grant,writeGrant}),/WRITE_DENIED/)
 const again=await env.call('one',{op:'authorize-write',grant}),decoy=join(env.dir,'decoy.json');await writeFile(decoy,await env.read());await rename(env.path,join(env.dir,'original.json'));await symlink(decoy,env.path)
 await assert.rejects(()=>env.call('one',{op:'status',grant,writeGrant:again.writeGrant}),/PATH_CHANGED/)
 await env.save('negative-wire.json',env.wire)
})

test('actual commit with dropped transport output is unknown, original op_id survives, explicit core status/recover and reopen',{skip,timeout:30000},async t=>{
 const env=await environment(t,'unknown'),grant=env.opened.grant,{writeGrant}=await env.call('one',{op:'authorize-write',grant})
 const plan=await env.call('one',{op:'plan',grant,writeGrant,base_revision:env.opened.snapshot.content_version,operations:[{op:'project.resize',duration_frames:130}]});assert.equal(plan.ok,true)
 env.faults.dropCommitOutput=true
 const result=await env.call('one',{op:'commit',grant,writeGrant,planToken:plan.planToken});assert.equal(result.code,'outcome_unknown');assert.equal(result.op_id,plan.submitted_request.op_id);assert.deepEqual(result.submitted_request,plan.submitted_request)
 env.faults.dropCommitOutput=false
 assert.equal(parseJSON(await env.read())._vtx.head,result.op_id,'the actual core committed despite deliberately lost process output')
 const status=await env.call('one',{op:'status',grant,writeGrant}),recover=await env.call('one',{op:'recover',grant,writeGrant});assert.equal(status.ok,true);assert.equal(recover.ok,true)
 await env.call('one',{op:'close',grant});const reopened=await env.call('one',{op:'open',timeline:'timeline.json'});assert.equal(reopened.snapshot.document._vtx.head,result.op_id)
 for(const child of env.handles)assert.equal(await child.waitForExit(AbortSignal.timeout(5000)),true)
 await env.save('unknown-wire.json',{fault:'test-only stdout lossy flag after actual provider runs actual kernel; not a simulated CLI response',wire:env.wire})
})

for(const mode of ['http-disconnect','revoke-write','host-dispose'])test(`actual running CLI ${mode} settles, waits for exit, reports possible commit honestly`,{skip,timeout:30000},async t=>{
 const env=await environment(t,`cancel-${mode}`),grant=env.opened.grant,{writeGrant}=await env.call('one',{op:'authorize-write',grant})
 const plan=await env.call('one',{op:'plan',grant,writeGrant,base_revision:env.opened.snapshot.content_version,operations:[{op:'project.resize',duration_frames:130}]});assert.equal(plan.ok,true)
 const controller=new AbortController();let cleanup
 env.faults.onSpawn=spec=>{if(!spec.argv.includes('--request-stdin')||spec.argv.includes('plan'))return;env.faults.onSpawn=undefined;queueMicrotask(()=>{if(mode==='http-disconnect')controller.abort();else if(mode==='host-dispose')cleanup=env.host.dispose();else cleanup=env.call('one',{op:'revoke-write',grant})})}
 const answer=await env.call('one',{op:'commit',grant,writeGrant,planToken:plan.planToken},controller.signal)
 assert.equal(answer.code,'outcome_unknown');assert.equal(answer.op_id,plan.submitted_request.op_id);await cleanup
 for(const child of env.handles)assert.equal(await child.waitForExit(AbortSignal.timeout(5000)),true)
 await env.save('cancel-wire.json',{mode,wire:env.wire,processes_reaped:env.handles.length})
})

test('legacy requires explicit adopt; incompatible existing IDs remain byte-identical, compatible adopt preserves unknown fields',{skip,timeout:30000},async t=>{
 for(const compatible of [false,true]){
  const doc=fixture();if(compatible){for(const track of [...doc.video_tracks,...doc.audio_tracks]){delete track.id;for(const block of track.clips??track.events)delete block.id}for(const sub of doc.subtitles)delete sub.id}
  const env=await environment(t,`legacy-${compatible}`,doc,{legacy:true}),grant=env.opened.grant,before=await env.read(),{writeGrant}=await env.call('one',{op:'authorize-write',grant})
  assert.equal(env.opened.snapshot.document._vtx,undefined)
  await assert.rejects(()=>env.call('one',{op:'adopt',grant,writeGrant,base_revision:env.opened.snapshot.content_version}),/ADOPT_CONFIRMATION/);assert.equal(await env.read(),before)
  const outcome=await env.call('one',{op:'adopt',grant,writeGrant,base_revision:env.opened.snapshot.content_version,confirmation:'stable-ids-and-history'})
  assert.equal(outcome.ok,compatible,stringifyJSON(outcome));if(compatible){const saved=parseJSON(await env.read());assert.equal(Boolean(saved._vtx), true);assert.deepEqual(saved.unknown_root,doc.unknown_root);assert.equal(saved.sources[0].id,'s')}else assert.equal(await env.read(),before)
  await env.save('legacy-wire.json',env.wire)
 }
})

test('old installed core lacking required write capabilities keeps read/copy usable and refuses write authorization',{skip:skip||(!process.env.SIDEBAR_OLD_VTX&&'Set SIDEBAR_OLD_VTX for real old-core negative'),timeout:30000},async t=>{
 const current=process.env.SIDEBAR_REAL_VTX;let env
 try{process.env.SIDEBAR_REAL_VTX=process.env.SIDEBAR_OLD_VTX;env=await environment(t,'old-core-readonly',fixture(),{legacy:true})}finally{process.env.SIDEBAR_REAL_VTX=current}
 const grant=env.opened.grant,before=await env.read();assert.equal(Boolean(env.result.blocks.length), true)
 await assert.rejects(()=>env.call('one',{op:'authorize-write',grant}),/WRITE_UNSUPPORTED/);assert.equal(await env.read(),before)
 const raw=await env.call('one',{op:'reference',grant,query:{block_addresses:['/video_tracks/0/clips/0'],expected_content_version:env.opened.snapshot.content_version}});assert.match(raw.selection.blocks[0].raw,/"id":"c"/)
 await env.save('old-core-wire.json',env.wire)
})

test('real property contract preserves unsafe/nested numeric tokens into stdin and refuses without writes; 0.1 and 0.5 plan normally',{skip,timeout:30000},async t=>{
 const env=await environment(t,'property-tokens'),grant=env.opened.grant,{writeGrant}=await env.call('one',{op:'authorize-write',grant}),before=await env.read()
 const cases=[['parameters.update','9007199254740993','opacity'],['parameters.update','0.12345678901234567890123456789','opacity'],['parameters.update','{"x":9007199254740993,"unknown":{"decimal":0.12345678901234567890123456789}}','transform'],['source.update','{"x":9007199254740993}'],['source.update','{"x":0.12345678901234567890123456789}'],['source.update','{"x":0.1,"unknown":{"integer":9007199254740993,"decimal":0.12345678901234567890123456789}}']]
 for(const [op,value,property] of cases){
  const operations=[op==='source.update'?{op,id:'s',set:{transform:parseJSON(value)}}:{op,id:'c',path:[property],value:parseJSON(value)}]
  const outcome=await env.call('one',{op:'plan',grant,writeGrant,base_revision:env.opened.snapshot.content_version,operations})
  assert.equal(outcome.ok,false,stringifyJSON(outcome));const data=env.specs.findLast(s=>s.argv.includes('plan')).stdio.stdin.data
  assert.equal(data.includes(value),true,'raw token survives the real Host stdin');assert.equal(await env.read(),before)
 }
 for(const value of [0.1,0.5]){
  const outcome=await env.call('one',{op:'plan',grant,writeGrant,base_revision:env.opened.snapshot.content_version,operations:[{op:'parameters.update',id:'c',path:['opacity'],value}]})
  assert.equal(outcome.ok,true,stringifyJSON(outcome));assert.equal(await env.read(),before);await env.call('one',{op:'invalidate-plan',grant,writeGrant})
 }
 assert.equal(env.wire.some(row=>row.request.op==='commit'),false);await env.save('property-token-wire.json',env.wire)
})
