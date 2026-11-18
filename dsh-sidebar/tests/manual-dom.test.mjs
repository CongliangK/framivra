import test from 'node:test'
import assert from 'node:assert/strict'
import React, { act } from 'react'
import { JSDOM } from 'jsdom'
import { VisualTimeline } from '../src/client/timeline.mjs'
import { ManualEditor, EDIT_OPERATIONS } from '../src/client/manual.mjs'
const h=React.createElement
// Explicit display/control adapters only; these tests do NOT claim kernel or layout execution.
const version=`sha256:${'a'.repeat(64)}`
const tracks=[{family:'video',track_index:0,address:'/video_tracks/0',data:{id:'v',name:'main',kind:'aroll'}},{family:'video',track_index:1,address:'/video_tracks/1',data:{id:'v2',name:'other',kind:'broll'}}]
const blocks=[{family:'video',track_index:0,block_index:0,address:'/video_tracks/0/clips/0',range:[0,30],data:{id:'c',src:'s'}},{family:'video',track_index:0,block_index:1,address:'/video_tracks/0/clips/1',range:[60,90],data:{id:'d',src:'s'}}]
const snapshot={content_version:version,document:{project:{duration_frames:120},_vtx:{protocol:1,head:'head'},sources:[{id:'s',path:'absent.mov'}]}},result={content_version:version,tracks,blocks}
async function mount(t){const dom=new JSDOM('<div id="root"></div>',{url:'http://owner/'});globalThis.window=dom.window;globalThis.document=dom.window.document;globalThis.IS_REACT_ACT_ENVIRONMENT=true;const {createRoot}=await import('react-dom/client'),{Simulate}=await import('react-dom/test-utils');const root=createRoot(document.getElementById('root'));t.after(async()=>{await act(async()=>root.unmount());dom.window.close();delete globalThis.window;delete globalThis.document});return {root,Simulate}}
const button=text=>[...document.querySelectorAll('button')].find(n=>n.textContent===text)

test('actual pointer handlers: pure ghost, multi move one batch, vertical-only cross-track, left/right inward intents, cancel and readonly',{timeout:10000},async t=>{
 const {root,Simulate}=await mount(t),batches=[], selections=[]
 const render=onGesture=>root.render(h(VisualTimeline,{snapshot,result,frame:15,activeSelection:{kind:'block',address:blocks[0].address},onSelect:()=>{},onSelectionChange:s=>selections.push(s),onGesture}))
 await act(async()=>render(ops=>batches.push(ops)))
 document.querySelector('.fv-ruler').getBoundingClientRect=()=>({left:0,width:1200})
 document.querySelectorAll('.fv-track').forEach((n,i)=>{n.getBoundingClientRect=()=>({top:i*100,bottom:i*100+100})})
 const clip=i=>document.querySelectorAll('.fv-clip')[i]
 await act(async()=>Simulate.click(clip(0),{}));await act(async()=>Simulate.click(clip(1),{ctrlKey:true}));assert.equal(selections.at(-1).length,2)
 await act(async()=>Simulate.pointerDown(clip(0),{pointerId:1,button:0,clientX:0,clientY:10}))
 await act(async()=>Simulate.pointerMove(clip(0),{pointerId:1,clientX:100,clientY:10}));assert(document.querySelector('[aria-label="未保存拖动 ghost"]'));assert.equal(batches.length,0);assert.match(clip(0).getAttribute('aria-label'),/帧 \[0, 30\)/)
 await act(async()=>Simulate.pointerUp(clip(0),{pointerId:1,clientX:100,clientY:10}));assert.deepEqual(batches.pop(),[{op:'block.move',id:'c',track_id:'v',at:10},{op:'block.move',id:'d',track_id:'v',at:70}])
 await act(async()=>Simulate.click(clip(0),{}));await act(async()=>Simulate.pointerDown(clip(0),{pointerId:2,button:0,clientX:0,clientY:10}));await act(async()=>Simulate.pointerUp(clip(0),{pointerId:2,clientX:0,clientY:110}));assert.deepEqual(batches.pop(),[{op:'block.move',id:'c',track_id:'v2',at:0}])
 for(const [side,x,expected] of [['左',50,{start:5,end:30}],['右',-50,{start:0,end:25}]]){
  const handle=document.querySelector(`[aria-label="${side} trim 手柄 ${blocks[0].address}"]`),origin=side==='左'?0:300
  await act(async()=>Simulate.pointerDown(handle,{pointerId:3,button:0,clientX:origin,clientY:10}));await act(async()=>Simulate.pointerUp(handle,{pointerId:3,clientX:origin+x,clientY:10}));assert.deepEqual(batches.pop(),[{op:'block.trim',id:'c',...expected,fade_policy:'preserve-content'}])
 }
 await act(async()=>Simulate.pointerDown(clip(0),{pointerId:4,button:0,clientX:0,clientY:10}));await act(async()=>Simulate.pointerMove(clip(0),{pointerId:4,clientX:30,clientY:10}));await act(async()=>Simulate.pointerCancel(clip(0),{}));assert(document.querySelector('[aria-label="未保存拖动 ghost"]') === null, "Expected absent DOM node: document.querySelector('[aria-label=\"未保存拖动 ghost\"]')");assert.equal(batches.length,0)
 await act(async()=>render(undefined));assert(document.querySelector('[aria-label^="左 trim"]') === null, "Expected absent DOM node: document.querySelector('[aria-label^=\"左 trim\"]')")
 await act(async()=>Simulate.pointerDown(clip(0),{pointerId:5,button:0,clientX:0,clientY:10}));await act(async()=>Simulate.pointerUp(clip(0),{pointerId:5,clientX:30,clientY:10}));assert.equal(batches.length,0)
})

test('all dedicated forms exist, explicit write auth; in-flight plan cannot survive edited input or selection',{timeout:10000},async t=>{
 const {root,Simulate}=await mount(t),calls=[];let resolvePlan
 const call=async(_session,payload)=>{calls.push(payload);if(payload.op==='proposal-list')return {ok:true,proposals:[]};if(payload.op==='authorize-write')return {writeGrant:'explicit'};if(payload.op==='plan')return await new Promise(r=>{resolvePlan=r});return {ok:true}}
 const selected=[{kind:'block',address:blocks[0].address}]
 const render=selection=>root.render(h(ManualEditor,{sessionId:'one',opened:{grant:'read',snapshot},result,selection,selected,frame:15,tabSignal:new AbortController().signal,call,onCommitted:()=>{}}))
 await act(async()=>render(selected[0]));assert.equal(document.querySelector('fieldset').disabled,true);assert.deepEqual(calls.map(c=>c.op),['proposal-list'])
 await act(async()=>button('启用编辑').click());assert.equal(document.querySelector('fieldset').disabled,false)
 for(const op of EDIT_OPERATIONS){const select=[...document.querySelectorAll('label')].find(n=>n.firstChild?.textContent==='编辑操作').querySelector('select');await act(async()=>Simulate.change(select,{target:{value:op}}));assert.equal(select.value,op);assert(document.querySelector('.fv-edit-advanced'));assert.equal(document.querySelector('.fv-edit-advanced').open,false);assert(document.querySelector('[aria-label="手动语义编辑"] pre') === null, "Expected absent DOM node");assert(document.querySelector('textarea') === null, "Expected absent DOM node")}
 const select=[...document.querySelectorAll('label')].find(n=>n.firstChild?.textContent==='编辑操作').querySelector('select');await act(async()=>Simulate.change(select,{target:{value:'project.resize'}}));await act(async()=>button('加入批次').click());await act(async()=>button('预检').click());assert(resolvePlan)
 const duration=[...document.querySelectorAll('label')].find(n=>n.firstChild?.textContent==='工程新时长帧（不改 FPS/分辨率）').querySelector('input');await act(async()=>Simulate.change(duration,{target:{value:'130'}}));await act(async()=>resolvePlan({ok:true,planToken:'obsolete',submitted_request:{op_id:'old'}}));assert(button('保存更改') === undefined, "Expected absent DOM node")
 assert.equal(calls.filter(c=>c.op==='commit').length,0);assert(calls.some(c=>c.op==='invalidate-plan'))
 await act(async()=>button('撤销许可').click());assert.equal(document.querySelector('fieldset').disabled,true)
})

test('plan waits for every previous invalidation acknowledgement and fails closed on lost acknowledgement',{timeout:10000},async t=>{
 const {root,Simulate}=await mount(t),calls=[],acks=[]
 const call=async(_session,payload)=>{calls.push(payload);if(payload.op==='proposal-list')return {ok:true,proposals:[]};if(payload.op==='authorize-write')return {writeGrant:'explicit'};if(payload.op==='invalidate-plan')return await new Promise((resolve,reject)=>acks.push({resolve,reject}));if(payload.op==='plan')return {ok:true,planToken:'new',submitted_request:{op_id:'new'}};return {ok:true}}
 const selected=[{kind:'block',address:blocks[0].address}]
 await act(async()=>root.render(h(ManualEditor,{sessionId:'one',opened:{grant:'read',snapshot},result,selection:selected[0],selected,frame:15,tabSignal:new AbortController().signal,call,onCommitted:()=>{}})))
 await act(async()=>button('启用编辑').click())
 const select=[...document.querySelectorAll('label')].find(n=>n.firstChild?.textContent==='编辑操作').querySelector('select')
 await act(async()=>Simulate.change(select,{target:{value:'project.resize'}}));await act(async()=>button('加入批次').click());assert.equal(acks.length,2)
 await act(async()=>button('预检').click());assert.equal(calls.filter(c=>c.op==='plan').length,0)
 await act(async()=>acks[1].resolve({ok:true}));assert.equal(calls.filter(c=>c.op==='plan').length,0,'newer acknowledgement alone must not bypass older pending invalidation')
 await act(async()=>acks[0].resolve({ok:true}));assert.equal(calls.filter(c=>c.op==='plan').length,1);assert(button('保存更改'))
 await act(async()=>button('清空').click());await act(async()=>acks.at(-1).reject(Error('lost acknowledgement')))
 await act(async()=>button('加入批次').click());await act(async()=>acks.at(-1).resolve({ok:true}));await act(async()=>button('预检').click())
 assert.equal(calls.filter(c=>c.op==='plan').length,1);assert(document.body.textContent.includes('PLAN_INVALIDATION_FAILED'));assert(button('保存更改') === undefined, "Expected absent DOM node")
})

test('manual queue publishes only changes, exposes human details and supports individual removal',{timeout:10000},async t=>{
 const {root,Simulate}=await mount(t),published=[],ready=[];let gesture
 const call=async(_session,payload)=>payload.op==='proposal-list'?{proposals:[]}:payload.op==='authorize-write'?{writeGrant:'explicit'}:{ok:true}
 const tabSignal=new AbortController().signal,selected=blocks.map(block=>({kind:'block',address:block.address}))
 const props={sessionId:'one',opened:{grant:'read',snapshot},result,selection:selected[0],selected,frame:15,tabSignal,call,onCommitted:()=>{},onGestureReady:(callback,reason)=>{gesture=callback;ready.push({callback,reason})},onQueueChange:queue=>published.push(queue)}
 await act(async()=>root.render(h(ManualEditor,props)));assert.equal(ready.at(-1).reason,'请先启用编辑');assert.equal(published.length,0)
 await act(async()=>button('启用编辑').click());const retained=gesture
 await act(async()=>button('加入批次').click());assert.equal(published.length,1);assert.deepEqual(published[0],[{op:'block.move',id:'c',track_id:'v',at:0},{op:'block.move',id:'d',track_id:'v',at:60}])
 assert(/移动片段.*main.*0–30 帧/.test(document.querySelector('[aria-label="待保存操作"]').textContent), "DOM text did not match expected pattern")
 assert.equal(gesture,undefined);assert.equal(ready.at(-1).reason,'先清空待保存操作');await assert.rejects(retained([{op:'block.move',id:'c',track_id:'v',at:1}]),/编辑状态已变化/)
 await act(async()=>root.render(h(ManualEditor,{...props,frame:16,onQueueChange:queue=>published.push(queue)})));assert.equal(published.length,1)
 await act(async()=>document.querySelector('[aria-label^="移除第 1 项"]').click());assert.equal(published.length,2);assert.equal(published.at(-1)[0].id,'d')
 await act(async()=>button('清空').click());assert.equal(published.length,3);assert.deepEqual(published.at(-1),[]);assert.equal(typeof gesture,'function')
 await act(async()=>root.unmount());assert.equal(ready.at(-1).callback,undefined);assert.equal(ready.at(-1).reason,'编辑已关闭')
})

test('direct gesture freezes plan/commit, spans busy through refresh, ignores selection invalidation after commit dispatch',{timeout:10000},async t=>{
 const {root}=await mount(t),calls=[],states=[],published=[],commits=[];let gesture,undo,resolvePlan,resolveCommit,resolveRefresh,transaction
 const operations=[{op:'block.move',id:'c',track_id:'v2',at:10}]
 const call=async(_session,payload)=>{calls.push(payload);if(payload.op==='proposal-list')return {proposals:[]};if(payload.op==='authorize-write')return {writeGrant:'explicit'};if(payload.op==='plan')return await new Promise(resolve=>{resolvePlan=resolve});if(payload.op==='commit')return await new Promise(resolve=>{resolveCommit=resolve});return {ok:true}}
 const props={sessionId:'one',opened:{grant:'read',snapshot},result,frame:15,tabSignal:new AbortController().signal,call,onGestureReady:callback=>{gesture=callback},onUndoReady:callback=>{undo=callback},onQueueChange:queue=>published.push(queue),onEditState:state=>states.push(state),onCommitted:options=>{commits.push(options);return new Promise(resolve=>{resolveRefresh=resolve})}}
 await act(async()=>root.render(h(ManualEditor,props)));await act(async()=>button('启用编辑').click());assert.equal(typeof undo,'function');const retained=gesture
 await act(async()=>{transaction=gesture(operations,{focusId:'c'})});assert(resolvePlan);assert.equal(gesture,undefined);assert.equal(undo,undefined);assert.equal(states.at(-1).busy,true)
 await assert.rejects(retained(operations),/编辑状态已变化/)
 await act(async()=>resolvePlan({ok:true,planToken:'frozen',submitted_request:{op_id:'direct-id',operations}}));assert(resolveCommit)
 const invalidations=calls.filter(call=>call.op==='invalidate-plan').length
 await act(async()=>root.render(h(ManualEditor,{...props,frame:16})));assert.equal(calls.filter(call=>call.op==='invalidate-plan').length,invalidations)
 await act(async()=>resolveCommit({ok:true,op_id:'direct-id'}));assert.deepEqual(commits,[{focusId:'c'}]);assert.equal(states.at(-1).busy,true)
 await act(async()=>resolveRefresh());assert.equal((await transaction).ok,true);assert.equal(states.at(-1).busy,false);assert.equal(published.length,0)
 assert.deepEqual(calls.find(call=>call.op==='commit'),{op:'commit',grant:'read',writeGrant:'explicit',planToken:'frozen'})
 assert(button('保存更改') === undefined,'direct transaction never exposes a second confirmation button')
})

test('direct preparation cancels on frame fence change; rejected plan never commits',{timeout:10000},async t=>{
 const {root}=await mount(t),calls=[];let gesture,resolvePlan,transaction
 const operations=[{op:'block.trim',id:'c',start:2,end:28,fade_policy:'preserve-content'}]
 const call=async(_session,payload)=>{calls.push(payload);if(payload.op==='proposal-list')return {proposals:[]};if(payload.op==='authorize-write')return {writeGrant:'explicit'};if(payload.op==='plan')return await new Promise(resolve=>{resolvePlan=resolve});return {ok:true}}
 const props={sessionId:'one',opened:{grant:'read',snapshot},result,frame:15,tabSignal:new AbortController().signal,call,onGestureReady:callback=>{gesture=callback},onCommitted:()=>{throw new Error('must not refresh')}}
 await act(async()=>root.render(h(ManualEditor,props)));await act(async()=>button('启用编辑').click())
 await act(async()=>{transaction=gesture(operations);transaction.catch(()=>{})});await act(async()=>root.render(h(ManualEditor,{...props,frame:16})))
 await act(async()=>resolvePlan({ok:true,planToken:'stale',submitted_request:{op_id:'stale-id',operations}}));await assert.rejects(transaction,/编辑状态已变化/);assert.equal(calls.filter(call=>call.op==='commit').length,0);assert(calls.some(call=>call.op==='invalidate-plan'))
 await act(async()=>{transaction=gesture(operations);transaction.catch(()=>{})});await act(async()=>resolvePlan({ok:false,code:'edit.operation',message:'outward trim requires authoritative source bounds'}));await assert.rejects(transaction,/只能向内裁剪/);assert.equal(calls.filter(call=>call.op==='commit').length,0)
})

test('child-only settings drafts reactively disable direct gestures and enable clear without parent state updates',{timeout:10000},async t=>{
 const {root,Simulate}=await mount(t);let gesture,undo
 const call=async(_session,payload)=>payload.op==='proposal-list'?{proposals:[]}:payload.op==='authorize-write'?{writeGrant:'explicit'}:{ok:true}
 await act(async()=>root.render(h(ManualEditor,{sessionId:'one',opened:{grant:'read',snapshot},result,frame:15,tabSignal:new AbortController().signal,call,onCommitted:()=>{},onEditState:()=>{},onGestureReady:callback=>{gesture=callback},onUndoReady:callback=>{undo=callback}})))
 await act(async()=>button('启用编辑').click());assert.equal(typeof gesture,'function');assert.equal(button('清空').disabled,true)
 const duration=document.querySelector('input[aria-label="工程时长"]');assert(duration)
 await act(async()=>Simulate.change(duration,{target:{value:'130'}}));assert.equal(gesture,undefined);assert.equal(undo,undefined);assert.equal(button('清空').disabled,false)
 await act(async()=>button('清空').click());assert.equal(typeof gesture,'function');assert.equal(typeof undo,'function')
})

test('direct entry rejects non-gesture intents and locks recovery-required plan without any commit',{timeout:10000},async t=>{
 const {root}=await mount(t),calls=[];let gesture
 const call=async(_session,payload)=>{calls.push(payload);return payload.op==='proposal-list'?{proposals:[]}:payload.op==='authorize-write'?{writeGrant:'explicit'}:payload.op==='plan'?{ok:false,code:'recovery_required',message:'unresolved intent'}:{ok:true}}
 await act(async()=>root.render(h(ManualEditor,{sessionId:'one',opened:{grant:'read',snapshot},result,frame:15,tabSignal:new AbortController().signal,call,onCommitted:()=>{throw new Error('must not refresh')},onGestureReady:callback=>{gesture=callback}})))
 await act(async()=>button('启用编辑').click())
 await assert.rejects(gesture([{op:'project.resize',duration_frames:200}]),/此入口仅支持/);assert.equal(calls.filter(call=>call.op==='plan').length,0)
 await act(async()=>assert.rejects(gesture([{op:'block.move',id:'c',track_id:'v',at:10}]),error=>error.code==='recovery_required'&&error.recoveryRequired))
 assert.equal(calls.filter(call=>call.op==='commit').length,0);assert.equal(gesture,undefined);assert.equal(button('预检').disabled,true)
})

test('known-applied direct save with failed authoritative refresh locks stale view without relabeling unknown',{timeout:10000},async t=>{
 const {root}=await mount(t),states=[];let gesture,undo
 const operations=[{op:'block.move',id:'c',track_id:'v',at:10}]
 const call=async(_session,payload)=>payload.op==='proposal-list'?{proposals:[]}:payload.op==='authorize-write'?{writeGrant:'explicit'}:payload.op==='plan'?{ok:true,planToken:'frozen',submitted_request:{op_id:'applied-id',operations}}:{ok:true}
 await act(async()=>root.render(h(ManualEditor,{sessionId:'one',opened:{grant:'read',snapshot},result,frame:15,tabSignal:new AbortController().signal,call,onGestureReady:callback=>{gesture=callback},onUndoReady:callback=>{undo=callback},onEditState:state=>states.push(state),onCommitted:()=>{throw new Error('inspect failed')}})))
 await act(async()=>button('启用编辑').click());await act(async()=>assert.rejects(gesture(operations),error=>error.applied===true&&!error.outcomeUnknown&&error.op_id==='applied-id'))
 assert.equal(gesture,undefined);assert.equal(undo,undefined);assert.equal(states.at(-1).unknown,false);assert.equal(states.at(-1).dirty,true);assert.equal(button('预检').disabled,true)
 assert(/内核已保存.*旧视图已锁定/.test(document.body.textContent), "DOM text did not match expected pattern")
})

test('unknown direct commit locks writes until explicit settled status; pending status never unlocks',{timeout:10000},async t=>{
 const {root}=await mount(t),states=[];let gesture,statusPending=true
 const operations=[{op:'range.ripple',scope:{track_ids:['v'],subtitles:false},mode:'delete',start:30,end:60,crossing:'reject',resize:false}],tabSignal=new AbortController().signal
 const call=async(_session,payload)=>{
  if(payload.op==='proposal-list')return {proposals:[]}
  if(payload.op==='authorize-write')return {writeGrant:'explicit'}
  if(payload.op==='plan')return {ok:true,planToken:'frozen',submitted_request:{op_id:'original-id',operations}}
  if(payload.op==='commit')throw new Error('connection lost')
  if(payload.op==='status')return {ok:true,pending:statusPending}
  return {ok:true}
 }
 await act(async()=>root.render(h(ManualEditor,{sessionId:'one',opened:{grant:'read',snapshot},result,frame:15,tabSignal,call,onCommitted:()=>{},onEditState:state=>states.push(state),onGestureReady:callback=>{gesture=callback}})))
 await act(async()=>button('启用编辑').click());await act(async()=>assert.rejects(gesture(operations),error=>error.outcomeUnknown&&error.op_id==='original-id'))
 assert.equal(gesture,undefined);assert.equal(button('预检').disabled,true);assert.equal(states.at(-1).unknown,true);assert(document.body.textContent.includes('original-id'))
 await act(async()=>button('读取状态').click());assert.equal(states.at(-1).unknown,true);assert.equal(gesture,undefined)
 statusPending=false;await act(async()=>button('读取状态').click());assert.equal(states.at(-1).unknown,false);assert.equal(typeof gesture,'function')
})
