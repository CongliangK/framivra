import test from 'node:test'
import assert from 'node:assert/strict'
import React, { act } from 'react'
import { JSDOM } from 'jsdom'
import { writeFile, symlink, rename } from 'node:fs/promises'
import { join } from 'node:path'
import { ManualEditor, EDIT_OPERATIONS, compileIntent, integerToken } from '../src/client/manual.mjs'
import { PreviewBody } from '../src/client/index.mjs'
import { parseJSON, stringifyJSON } from '../src/json.mjs'
import { environment, fixture, realProvider, skip } from './manual-env.mjs'
const h=React.createElement
async function mount(t){const dom=new JSDOM('<div id="root"></div>',{url:'http://owner/'});globalThis.window=dom.window;globalThis.document=dom.window.document;globalThis.IS_REACT_ACT_ENVIRONMENT=true;const {createRoot}=await import('react-dom/client'),{Simulate}=await import('react-dom/test-utils');const root=createRoot(document.getElementById('root'));t.after(async()=>{await act(async()=>root.unmount());dom.window.close();delete globalThis.window;delete globalThis.document});return {dom,root,Simulate}}
const button=text=>[...document.querySelectorAll('button')].find(n=>n.textContent===text)
const input=label=>[...document.querySelectorAll('label')].find(n=>n.firstChild?.textContent===label)?.querySelector('input,select')
async function settle(predicate){for(let i=0;i<400;i++){if(predicate())return;await act(async()=>{await new Promise(r=>setTimeout(r,5))})}assert.fail(document.body.textContent)}
async function click(text){const b=button(text);assert(b,text);assert.equal(b.disabled,false,text);await act(async()=>b.click())}
async function change(Simulate,label,value){const node=input(label);assert(node,label);await act(async()=>Simulate.change(node,{target:{value}}))}

test('manual compiler exact integer tokens, typed props and no arbitrary JSON request authority',()=>{
 for(const token of ['9007199254740993','1.0','1e2',' 1','+1','-0','NaN',''])assert.throws(()=>integerToken(token),/不可编辑/)
 assert.equal(integerToken('-2',true),-2)
 const b={data:{id:'c'},range:[0,30],family:'video',track_index:0};const context={result:{tracks:[{data:{id:'v'},family:'video',track_index:0}]},blocks:[b],frame:15,makeId:()=> 'fixed-new'}
 assert.deepEqual(compileIntent('block.split',{fade_policy:'preserve-content'},context),[{op:'block.split',id:'c',at:15,new_id:'fixed-new',fade_policy:'preserve-content'}])
 assert.throws(()=>compileIntent('parameters.update',{property:'_vtx',value:'{}'},context),/白名单/)
 assert.match(stringifyJSON(compileIntent('parameters.update',{property:'opacity',value:'1.0000000000000001'},context)),/1\.0000000000000001/)
})

test('20 typed UI forms -> Host -> frozen public provider -> installed CLI plan/commit -> core head undo',{skip,timeout:120000},async t=>{
 const {root,Simulate}=await mount(t);const coverage=[]
 for(const op of EDIT_OPERATIONS){
  const doc=fixture();doc.sources.push({id:'spare',path:'unused.wav'});doc.video_tracks[0].clips[0].opacity=0.5
  const env=await environment(t,`ui-${op}`,doc);let refreshed=0
  const block=env.result.blocks.find(b=>b.data.id==='c'),track=env.result.tracks.find(t=>t.data.id===(op==='track.references'?'bed':'v'))
  const selection=op.startsWith('track.')?{kind:'track',address:track.address}:{kind:'block',address:block.address}
  await act(async()=>root.render(h(ManualEditor,{key:env.dir,sessionId:'one',opened:env.opened,result:env.result,selection,selected:[selection],frame:15,tabSignal:new AbortController().signal,call:env.call,onCommitted:async()=>{refreshed++}})))
  await click('明确允许本会话向此工程及 core 历史写入');await settle(()=>document.body.textContent.includes('写授权：已明确授予'))
  await change(Simulate,'编辑操作',op);const fill=async(k,v)=>change(Simulate,k,v)
  if(op==='source.add')await fill('素材路径（不复制媒体）','new.mov')
  if(op==='source.remove')await fill('素材 ID','spare')
  if(op==='source.update'){await fill('素材 ID','s');await fill('素材路径（不复制媒体）','changed.mov')}
  if(op==='track.add'){await fill('轨道名称','new-track');await fill('源 ID / gfx 文件路径（明确引用，不探素材）','s')}
  if(op==='track.copy')await fill('副本名称','copy-main')
  if(op==='track.rename')await fill('轨道新名称','renamed')
  if(op==='track.reorder'){await act(async()=>[...document.querySelectorAll('button')].filter(b=>b.textContent==='下移')[0].click());await click('采用此完整轨序')}
  if(op==='track.references')await fill('引用值（duck_by 为 JSON 字符串数组，如 ["voice"]）','["voice"]')
  if(op==='block.insert'){await fill('目标轨（空=原轨）','v');await fill('源 ID / gfx 文件路径（明确引用，不探素材）','s');await fill('全局起点帧','30')}
  if(op==='block.move'||op==='block.copy')await fill('首选块新起点帧（多选保持相对位置）',op==='block.copy'?'90':'30')
  if(op==='block.relink')await fill('新 source ID / gfx 文件路径','spare')
  if(op==='block.trim'){await fill('新的全局起点帧（仅 inward）','2');await fill('新的全局终点帧（仅 inward）','28')}
  if(op==='parameters.update')await fill('属性值（text 为文本，其余单个 JSON 值）','0.75')
  if(op==='project.resize')await fill('工程新时长帧（不改 FPS/分辨率）','130')
  if(op==='range.ripple'){const check=[...document.querySelectorAll('label')].find(n=>n.textContent==='video main v').querySelector('input');await act(async()=>Simulate.change(check,{target:{checked:true}}));await fill('shift 起点帧','60');await fill('shift 有符号整数偏移','10')}
  const before=await env.read();await click('加入未保存批次');const intents=parseJSON(document.querySelector('[aria-label="未保存 typed intents"]').textContent);assert.equal(intents[0]?.op,op,document.body.textContent)
  await click('内核预检计划（零写）');await settle(()=>button('确认提交此冻结计划（一次 revision）')||document.body.textContent.includes('"ok": false'))
  assert(button('确认提交此冻结计划（一次 revision）'),`${op}: ${document.body.textContent}`);assert.equal(await env.read(),before,'planning must not write')
  const plan=env.wire.findLast(w=>w.request.op==='plan').response,opId=plan.submitted_request.op_id
  await click('确认提交此冻结计划（一次 revision）');await settle(()=>refreshed===1)
  const commit=env.wire.findLast(w=>w.request.op==='commit').response;assert.equal(commit.ok,true,stringifyJSON(commit));assert.equal(commit.op_id,opId);assert.deepEqual(commit.submitted_request,plan.submitted_request)
  const after=parseJSON(await env.read());assert.deepEqual(after.unknown_root,doc.unknown_root);assert.equal(after._vtx.head,opId)
  const writeGrant=env.wire.find(w=>w.request.op==='authorize-write').response.writeGrant
  assert.equal((await env.call('one',{op:'status',grant:env.opened.grant,writeGrant})).ok,true)
  const snapshot=await env.call('one',{op:'inspect',grant:env.opened.grant});const undo=await env.call('one',{op:'undo',grant:env.opened.grant,writeGrant,base_revision:snapshot.content_version,head:after._vtx.head});assert.equal(undo.ok,true,stringifyJSON(undo))
  const undone=parseJSON(await env.read());delete undone._vtx;assert.deepEqual(JSON.parse(stringifyJSON(undone)),doc)
  for(const child of env.handles)assert.equal(await child.waitForExit(AbortSignal.timeout(5000)),true)
  await env.save('ui-wire.json',env.wire);coverage.push({op,op_id:opId,core_commit:true,core_undo:true,processes_reaped:env.handles.length})
  await act(async()=>root.render(null));await env.host.dispose()
 }
 await writeFile(join(process.env.SIDEBAR_EVIDENCE_DIR,'manual-20ops.json'),stringifyJSON({adapter:'React18/jsdom29; real public LocalSubprocessRuntime + installed vtx; not browser layout',coverage},2))
})

test('actual PreviewBody gesture ghost -> batch commit -> authoritative inspect/query and exact raw',{skip,timeout:30000},async t=>{
 const env=await environment(t,'gesture');const {root,Simulate}=await mount(t);const previous=globalThis.fetch;t.after(()=>{globalThis.fetch=previous})
 globalThis.fetch=async(_url,init)=>env.host.control(new Request('http://owner/api/framivra/control',{method:'POST',body:init.body,signal:init.signal}))
 const controller=new AbortController();await act(async()=>root.render(h(PreviewBody,{sessionId:'one',useTabInfo:()=>({tab:{signal:controller.signal}})})))
 await act(async()=>document.querySelector('form').dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true})));await settle(()=>document.querySelector('.fv-ruler'))
 await click('明确允许本会话向此工程及 core 历史写入');await settle(()=>document.body.textContent.includes('写授权：已明确授予'))
 const ruler=document.querySelector('.fv-ruler');ruler.getBoundingClientRect=()=>({left:0,width:1200})
 const clip=()=>document.querySelector('[aria-label^="选择块 /video_tracks/0/clips/0"]');await act(async()=>clip().click())
 const before=await env.read(),count=env.specs.length
 await act(async()=>Simulate.pointerDown(clip(),{pointerId:1,button:0,clientX:0,clientY:10}));await act(async()=>Simulate.pointerMove(clip(),{pointerId:1,clientX:300,clientY:10}))
 assert(document.querySelector('[aria-label="未保存拖动 ghost"]'));assert.equal(await env.read(),before);assert.equal(env.specs.length,count)
 await act(async()=>Simulate.pointerUp(clip(),{pointerId:1,clientX:300,clientY:10}));assert.equal(document.querySelector('[aria-label="未保存拖动 ghost"]'),null)
 assert.match(document.querySelector('[aria-label="未保存 typed intents"]').textContent,/"at": 30/)
 await click('内核预检计划（零写）');await settle(()=>button('确认提交此冻结计划（一次 revision）'));await click('确认提交此冻结计划（一次 revision）');await settle(()=>parseJSON(document.querySelector('[aria-label="未保存 typed intents"]').textContent).length===0)
 assert.equal(parseJSON(await env.read()).video_tracks[0].clips.find(b=>b.id==='c').dst_in,30)
 // Core move may reorder its array; refresh must preserve the stable ID, not
 // accidentally retain the old address now occupied by another clip.
 await settle(()=>document.querySelector('[aria-label="活动引用"]')?.textContent.includes('/video_tracks/0/clips/1'))
 const snapshot=await env.call('one',{op:'inspect',grant:env.opened.grant}),raw=await env.call('one',{op:'reference',grant:env.opened.grant,query:{block_ids:['c'],expected_content_version:snapshot.content_version}})
 assert.match(raw.selection.blocks[0].raw,/"dst_in":\s*30\b/)
 assert((await env.read()).includes(raw.selection.blocks[0].raw),'reference must be an exact saved-source substring, not presentation serialization')
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
  assert.equal(outcome.ok,compatible,stringifyJSON(outcome));if(compatible){const saved=parseJSON(await env.read());assert(saved._vtx);assert.deepEqual(saved.unknown_root,doc.unknown_root);assert.equal(saved.sources[0].id,'s')}else assert.equal(await env.read(),before)
  await env.save('legacy-wire.json',env.wire)
 }
})

test('old installed core lacking official stdin capability keeps read/copy usable and refuses write authorization',{skip:skip||(!process.env.SIDEBAR_OLD_VTX&&'Set SIDEBAR_OLD_VTX for real old-core negative'),timeout:30000},async t=>{
 const current=process.env.SIDEBAR_REAL_VTX;let env
 try{process.env.SIDEBAR_REAL_VTX=process.env.SIDEBAR_OLD_VTX;env=await environment(t,'old-core-readonly',fixture(),{legacy:true})}finally{process.env.SIDEBAR_REAL_VTX=current}
 const grant=env.opened.grant,before=await env.read();assert(env.result.blocks.length)
 await assert.rejects(()=>env.call('one',{op:'authorize-write',grant}),/WRITE_UNSUPPORTED/);assert.equal(await env.read(),before)
 const raw=await env.call('one',{op:'reference',grant,query:{block_addresses:['/video_tracks/0/clips/0'],expected_content_version:env.opened.snapshot.content_version}});assert.match(raw.selection.blocks[0].raw,/"id":"c"/)
 await env.save('old-core-wire.json',env.wire)
})

test('real JSON property forms preserve unsafe/nested numeric tokens into stdin and refuse without writes; 0.1 and 0.5 plan normally',{skip,timeout:30000},async t=>{
 const env=await environment(t,'property-tokens'),{root,Simulate}=await mount(t),block=env.result.blocks.find(b=>b.data.id==='c'),selection={kind:'block',address:block.address}
 await act(async()=>root.render(h(ManualEditor,{sessionId:'one',opened:env.opened,result:env.result,selection,selected:[selection],frame:15,tabSignal:new AbortController().signal,call:env.call,onCommitted:async()=>{}})))
 await click('明确允许本会话向此工程及 core 历史写入');await settle(()=>document.body.textContent.includes('写授权：已明确授予'))
 const before=await env.read(),cases=[['parameters.update','9007199254740993','opacity'],['parameters.update','0.12345678901234567890123456789','opacity'],['parameters.update','{"x":9007199254740993,"unknown":{"decimal":0.12345678901234567890123456789}}','transform'],['source.update','{"x":9007199254740993}'],['source.update','{"x":0.12345678901234567890123456789}'],['source.update','{"x":0.1,"unknown":{"integer":9007199254740993,"decimal":0.12345678901234567890123456789}}']]
 for(const [op,value,property] of cases){
  await change(Simulate,'编辑操作',op)
  if(op==='source.update'){await change(Simulate,'素材 ID','s');await change(Simulate,'更新字段','transform');await change(Simulate,'transform JSON 属性',value)}else{await change(Simulate,'白名单属性（不适用对象由内核精确拒绝）',property);await change(Simulate,'属性值（text 为文本，其余单个 JSON 值）',value)}
  await click('加入未保存批次');const oldCount=env.wire.length
  await click('内核预检计划（零写）');await settle(()=>env.wire.length>oldCount&&env.wire.at(-1).request.op==='plan')
  const outcome=env.wire.at(-1);assert.equal(outcome.response.ok,false,stringifyJSON(outcome));assert.equal(button('确认提交此冻结计划（一次 revision）'),undefined)
  const data=env.specs.findLast(s=>s.argv.includes('plan')).stdio.stdin.data;assert(data.includes(value),`raw typed token changed before core: ${value} vs ${data}`);assert.equal(await env.read(),before)
  await click('清空未保存批次')
 }
 await change(Simulate,'编辑操作','parameters.update');await change(Simulate,'白名单属性（不适用对象由内核精确拒绝）','opacity')
 for(const value of ['0.1','0.5']){
  await change(Simulate,'属性值（text 为文本，其余单个 JSON 值）',value);await click('加入未保存批次');await click('内核预检计划（零写）');await settle(()=>button('确认提交此冻结计划（一次 revision）'));assert.equal(env.wire.at(-1).response.ok,true);assert.equal(await env.read(),before);await click('清空未保存批次')
 }
 await env.save('property-token-wire.json',env.wire)
})
