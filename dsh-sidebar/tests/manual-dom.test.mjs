import test from 'node:test'
import assert from 'node:assert/strict'
import React, { act } from 'react'
import { JSDOM } from 'jsdom'
import { VisualTimeline } from '../src/client/timeline.mjs'
import { ManualEditor } from '../src/client/manual.mjs'
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
 await act(async()=>Simulate.pointerMove(clip(0),{pointerId:1,clientX:100,clientY:10}));assert.equal(Boolean(document.querySelector('[aria-label="未保存拖动 ghost"]')), true);assert.equal(batches.length,0);assert.match(clip(0).getAttribute('aria-label'),/帧 \[0, 30\)/)
 await act(async()=>Simulate.pointerUp(clip(0),{pointerId:1,clientX:150,clientY:10}));assert.deepEqual(batches.pop(),[{op:'block.move',id:'c',track_id:'v',at:15},{op:'block.move',id:'d',track_id:'v',at:75}])
 await act(async()=>Simulate.click(clip(0),{}));await act(async()=>Simulate.pointerDown(clip(0),{pointerId:2,button:0,clientX:0,clientY:10}));await act(async()=>Simulate.pointerUp(clip(0),{pointerId:2,clientX:0,clientY:110}));assert.deepEqual(batches.pop(),[{op:'block.move',id:'c',track_id:'v2',at:0}])
 for(const [side,x,expected] of [['左',50,{start:5,end:30}],['右',-50,{start:0,end:25}]]){
  const handle=document.querySelector(`[aria-label="${side} trim 手柄 ${blocks[0].address}"]`),origin=side==='左'?0:300
  await act(async()=>Simulate.pointerDown(handle,{pointerId:3,button:0,clientX:origin,clientY:10}));await act(async()=>Simulate.pointerUp(handle,{pointerId:3,clientX:origin+x,clientY:10}));assert.deepEqual(batches.pop(),[{op:'block.trim',id:'c',...expected,fade_policy:'preserve-content'}])
 }
 await act(async()=>Simulate.pointerDown(clip(0),{pointerId:4,button:0,clientX:0,clientY:10}));await act(async()=>Simulate.pointerMove(clip(0),{pointerId:4,clientX:30,clientY:10}));await act(async()=>Simulate.pointerCancel(clip(0),{}));assert.equal(Boolean(document.querySelector('[aria-label="未保存拖动 ghost"]') === null), true, "Expected absent DOM node: document.querySelector('[aria-label=\"未保存拖动 ghost\"]')");assert.equal(batches.length,0)
 await act(async()=>render(undefined));assert.equal(Boolean(document.querySelector('[aria-label^="左 trim"]') === null), true, "Expected absent DOM node: document.querySelector('[aria-label^=\"左 trim\"]')")
 await act(async()=>Simulate.pointerDown(clip(0),{pointerId:5,button:0,clientX:0,clientY:10}));await act(async()=>Simulate.pointerUp(clip(0),{pointerId:5,clientX:30,clientY:10}));assert.equal(batches.length,0)
})

test('topbar edit permission is explicit, has no forms or proposal polling, and exit retires callbacks',{timeout:10000},async t=>{
 const {root}=await mount(t),calls=[],states=[];let enable,gesture,quick
 const call=async(_session,payload)=>{calls.push(payload);return payload.op==='authorize-write'?{ok:true,writeGrant:'explicit'}:{ok:true}}
 await act(async()=>root.render(h(ManualEditor,{sessionId:'one',opened:{grant:'read',snapshot},result,frame:15,tabSignal:new AbortController().signal,call,onCommitted:()=>{},onEnableReady:callback=>{enable=callback},onGestureReady:callback=>{gesture=callback},onQuickReady:callback=>{quick=callback},onEditState:state=>states.push(state)})))
 assert.equal(document.querySelector('[aria-label="时间线编辑"]')!==null,true);assert.equal(calls.length,0);assert.equal(typeof enable,'function');assert.equal(gesture,undefined)
 assert.equal(document.querySelector('textarea,select,fieldset') === null, true);assert.equal(document.querySelector('.fv-edit-advanced') === null, true)
 await act(async()=>button('启用编辑').click());assert.equal(calls.filter(call=>call.op==='authorize-write').length,1);assert.equal(typeof gesture,'function');assert.equal(typeof quick,'function')
 assert.equal(document.querySelector('.fv-history').open,false);assert.equal(document.querySelector('.fv-history summary').textContent.includes('历史与恢复'),true)
 assert.equal(calls.some(call=>call.op.startsWith('proposal-')),false)
 const retained=gesture;await act(async()=>button('退出编辑').click());assert.equal(gesture,undefined);assert.equal(quick,undefined)
 await assert.rejects(retained([{op:'block.move',id:'c',track_id:'v',at:1}]),/编辑状态已变化/)
 assert.equal(states.at(-1).busy,false)
})

test('direct gesture freezes plan/commit, spans busy through refresh, ignores selection invalidation after commit dispatch',{timeout:10000},async t=>{
 const {root}=await mount(t),calls=[],states=[],commits=[];let gesture,undo,resolvePlan,resolveCommit,resolveRefresh,transaction
 const operations=[{op:'block.move',id:'c',track_id:'v2',at:10}]
 const call=async(_session,payload)=>{calls.push(payload);if(payload.op==='authorize-write')return {ok:true,writeGrant:'explicit'};if(payload.op==='plan')return await new Promise(resolve=>{resolvePlan=resolve});if(payload.op==='commit')return await new Promise(resolve=>{resolveCommit=resolve});return {ok:true}}
 const props={sessionId:'one',opened:{grant:'read',snapshot},result,frame:15,tabSignal:new AbortController().signal,call,onGestureReady:callback=>{gesture=callback},onUndoReady:callback=>{undo=callback},onEditState:state=>states.push(state),onCommitted:options=>{commits.push(options);return new Promise(resolve=>{resolveRefresh=resolve})}}
 await act(async()=>root.render(h(ManualEditor,props)));await act(async()=>button('启用编辑').click());assert.equal(typeof undo,'function');const retained=gesture
 await act(async()=>{transaction=gesture(operations,{focusId:'c'})});assert.equal(Boolean(resolvePlan), true);assert.equal(gesture,undefined);assert.equal(undo,undefined);assert.equal(states.at(-1).busy,true)
 await assert.rejects(retained(operations),/编辑状态已变化/)
 await act(async()=>resolvePlan({ok:true,planToken:'frozen',submitted_request:{op_id:'direct-id',operations}}));assert.equal(Boolean(resolveCommit), true)
 const invalidations=calls.filter(call=>call.op==='invalidate-plan').length
 await act(async()=>root.render(h(ManualEditor,{...props,frame:16})));assert.equal(calls.filter(call=>call.op==='invalidate-plan').length,invalidations)
 const revision=`sha256:${'b'.repeat(64)}`
 await act(async()=>resolveCommit({ok:true,op_id:'direct-id',revision}));assert.deepEqual(commits,[{focusId:'c',expectedRevision:revision}]);assert.equal(states.at(-1).busy,true)
 await act(async()=>resolveRefresh());assert.equal((await transaction).ok,true);assert.equal(states.at(-1).busy,false)
 assert.deepEqual(calls.find(call=>call.op==='commit'),{op:'commit',grant:'read',writeGrant:'explicit',planToken:'frozen'})
 assert.equal(Boolean(button('保存更改') === undefined), true,'direct transaction never exposes a second confirmation button')
})

test('advertised edit-view sends one fixed request and hands its saved view to the editor', async t => {
  const { root } = await mount(t)
  const calls = [], saved = [], view = { snapshot, result }
  let gesture
  const call = async (_session, payload) => {
    calls.push(payload)
    if (payload.op === 'authorize-write') return { ok: true, writeGrant: 'explicit', editView: true }
    assert.equal(payload.op, 'edit-view')
    assert.match(payload.op_id, /^[0-9a-f-]{36}$/)
    return { ok: true, revision: version, view }
  }
  const operations = [{ op: 'block.move', id: 'c', track_id: 'v', at: 10 }]
  await act(async () => root.render(h(ManualEditor, {
    sessionId: 'one', opened: { grant: 'read', snapshot }, result, call,
    tabSignal: new AbortController().signal, onGestureReady: callback => { gesture = callback },
    onCommitted: answer => saved.push(answer),
  })))
  await act(async () => button('启用编辑').click())
  await act(async () => gesture(operations, { focusId: 'c' }))
  assert.deepEqual(calls.map(payload => payload.op), ['authorize-write', 'edit-view'])
  assert.deepEqual(calls[1], { op: 'edit-view', grant: 'read', writeGrant: 'explicit', op_id: calls[1].op_id, base_revision: version, operations })
  assert.deepEqual(saved, [{ focusId: 'c', view, expectedRevision: version }])
})

test('direct preparation cancels on frame fence change; rejected plan never commits',{timeout:10000},async t=>{
 const {root}=await mount(t),calls=[];let gesture,resolvePlan,transaction
 const operations=[{op:'block.trim',id:'c',start:2,end:28,fade_policy:'preserve-content'}]
 const call=async(_session,payload)=>{calls.push(payload);if(payload.op==='authorize-write')return {ok:true,writeGrant:'explicit'};if(payload.op==='plan')return await new Promise(resolve=>{resolvePlan=resolve});return {ok:true}}
 const props={sessionId:'one',opened:{grant:'read',snapshot},result,frame:15,tabSignal:new AbortController().signal,call,onGestureReady:callback=>{gesture=callback},onCommitted:()=>{throw new Error('must not refresh')}}
 await act(async()=>root.render(h(ManualEditor,props)));await act(async()=>button('启用编辑').click())
 await act(async()=>{transaction=gesture(operations);transaction.catch(()=>{})});await act(async()=>root.render(h(ManualEditor,{...props,frame:16})))
 await act(async()=>resolvePlan({ok:true,planToken:'stale',submitted_request:{op_id:'stale-id',operations}}));await assert.rejects(transaction,/编辑状态已变化/);assert.equal(calls.filter(call=>call.op==='commit').length,0);assert.equal(Boolean(calls.some(call=>call.op==='invalidate-plan')), true)
 await act(async()=>{transaction=gesture(operations);transaction.catch(()=>{})});await act(async()=>resolvePlan({ok:false,code:'edit.operation',message:'outward trim requires authoritative source bounds'}));await assert.rejects(transaction,/只能向内裁剪/);assert.equal(calls.filter(call=>call.op==='commit').length,0)
})

test('suspension immediately retires direct callbacks without authorizing or writing',{timeout:10000},async t=>{
 const {root}=await mount(t),calls=[];let gesture,quick,undo
 const call=async(_session,payload)=>{calls.push(payload);return payload.op==='authorize-write'?{ok:true,writeGrant:'explicit'}:{ok:true}}
 const props={sessionId:'one',opened:{grant:'read',snapshot},result,frame:15,tabSignal:new AbortController().signal,call,onCommitted:()=>{},onGestureReady:callback=>{gesture=callback},onQuickReady:callback=>{quick=callback},onUndoReady:callback=>{undo=callback}}
 await act(async()=>root.render(h(ManualEditor,props)));await act(async()=>button('启用编辑').click());const retained=gesture
 await act(async()=>root.render(h(ManualEditor,{...props,suspended:true})));assert.equal(gesture,undefined);assert.equal(quick,undefined);assert.equal(undo,undefined)
 await assert.rejects(retained([{op:'block.move',id:'c',track_id:'v',at:1}]),/编辑状态已变化/)
 assert.equal(calls.some(call=>['plan','commit'].includes(call.op)),false)
 await act(async()=>root.render(h(ManualEditor,props)));assert.equal(typeof gesture,'function');assert.equal(calls.filter(call=>call.op==='authorize-write').length,1)
})

test('direct entry rejects non-gesture intents and locks recovery-required plan without any commit',{timeout:10000},async t=>{
 const {root}=await mount(t),calls=[];let gesture
 const call=async(_session,payload)=>{calls.push(payload);return payload.op==='authorize-write'?{ok:true,writeGrant:'explicit'}:payload.op==='plan'?{ok:false,code:'recovery_required',message:'unresolved intent'}:{ok:true}}
 await act(async()=>root.render(h(ManualEditor,{sessionId:'one',opened:{grant:'read',snapshot},result,frame:15,tabSignal:new AbortController().signal,call,onCommitted:()=>{throw new Error('must not refresh')},onGestureReady:callback=>{gesture=callback}})))
 await act(async()=>button('启用编辑').click())
 await assert.rejects(gesture([{op:'project.resize',duration_frames:200}]),/此入口仅支持/);assert.equal(calls.filter(call=>call.op==='plan').length,0)
 await act(async()=>assert.rejects(gesture([{op:'block.move',id:'c',track_id:'v',at:10}]),error=>error.code==='recovery_required'&&error.recoveryRequired))
 assert.equal(calls.filter(call=>call.op==='commit').length,0);assert.equal(gesture,undefined);assert.equal(button('撤销当前版本').disabled,true)
})

test('known-applied direct save with failed authoritative refresh locks stale view without relabeling unknown',{timeout:10000},async t=>{
 const {root}=await mount(t),states=[];let gesture,undo
 const operations=[{op:'block.move',id:'c',track_id:'v',at:10}]
 const call=async(_session,payload)=>payload.op==='authorize-write'?{ok:true,writeGrant:'explicit'}:payload.op==='plan'?{ok:true,planToken:'frozen',submitted_request:{op_id:'applied-id',operations}}:{ok:true}
 await act(async()=>root.render(h(ManualEditor,{sessionId:'one',opened:{grant:'read',snapshot},result,frame:15,tabSignal:new AbortController().signal,call,onGestureReady:callback=>{gesture=callback},onUndoReady:callback=>{undo=callback},onEditState:state=>states.push(state),onCommitted:()=>{throw new Error('inspect failed')}})))
 await act(async()=>button('启用编辑').click());await act(async()=>assert.rejects(gesture(operations),error=>error.applied===true&&!error.outcomeUnknown&&error.op_id==='applied-id'))
 assert.equal(gesture,undefined);assert.equal(undo,undefined);assert.equal(states.at(-1).unknown,false);assert.equal(states.at(-1).dirty,true);assert.equal(button('撤销当前版本').disabled,true)
 assert.equal(Boolean(/内核已保存.*刷新保存版本/.test(document.body.textContent)), true, "DOM text did not match expected pattern")
})

test('unknown direct commit locks writes until explicit settled status; pending status never unlocks',{timeout:10000},async t=>{
 const {root}=await mount(t),states=[];let gesture,statusPending=true
 const operations=[{op:'range.ripple',scope:{track_ids:['v'],subtitles:false},mode:'delete',start:30,end:60,crossing:'reject',resize:false}],tabSignal=new AbortController().signal
 const call=async(_session,payload)=>{
  if(payload.op==='authorize-write')return {ok:true,writeGrant:'explicit'}
  if(payload.op==='plan')return {ok:true,planToken:'frozen',submitted_request:{op_id:'original-id',operations}}
  if(payload.op==='commit')throw new Error('connection lost')
  if(payload.op==='status')return {ok:true,pending:statusPending}
  return {ok:true}
 }
 await act(async()=>root.render(h(ManualEditor,{sessionId:'one',opened:{grant:'read',snapshot},result,frame:15,tabSignal,call,onCommitted:()=>{},onEditState:state=>states.push(state),onGestureReady:callback=>{gesture=callback}})))
 await act(async()=>button('启用编辑').click());await act(async()=>assert.rejects(gesture(operations),error=>error.outcomeUnknown&&error.op_id==='original-id'))
 assert.equal(gesture,undefined);assert.equal(button('撤销当前版本').disabled,true);assert.equal(states.at(-1).unknown,true);assert.equal(document.body.textContent.includes('original-id'),true)
 await act(async()=>button('读取状态').click());assert.equal(states.at(-1).unknown,true);assert.equal(gesture,undefined)
 statusPending=false;await act(async()=>button('读取状态').click());assert.equal(states.at(-1).unknown,false);assert.equal(typeof gesture,'function')
})

test('unknown result permits revoke and explicit recovery-only reauthorization, never new edits before authoritative status',{timeout:10000},async t=>{
 const {root}=await mount(t),calls=[],states=[];let gesture,quick,undo,enable,statusPending=true,refreshes=0
 const operations=[{op:'block.move',id:'c',track_id:'v',at:10}],tabSignal=new AbortController().signal
 const call=async(_session,payload)=>{
  calls.push(payload)
  if(payload.op==='authorize-write')return {ok:true,writeGrant:`explicit-${calls.filter(item=>item.op==='authorize-write').length}`}
  if(payload.op==='plan')return {ok:true,planToken:'original-plan',submitted_request:{op_id:'original-id',operations}}
  if(payload.op==='commit')throw Error('connection lost')
  if(payload.op==='status')return {ok:true,pending:statusPending}
  return {ok:true}
 }
 await act(async()=>root.render(h(ManualEditor,{sessionId:'one',opened:{grant:'read',snapshot},result,frame:15,tabSignal,call,onCommitted:()=>{refreshes++},onEditState:state=>states.push(state),onGestureReady:callback=>{gesture=callback},onQuickReady:callback=>{quick=callback},onUndoReady:callback=>{undo=callback},onEnableReady:callback=>{enable=callback}})))
 await act(async()=>button('启用编辑').click());const retained=gesture
 await act(async()=>assert.rejects(gesture(operations),error=>error.outcomeUnknown===true&&error.op_id==='original-id'))
 assert.equal(states.at(-1).unknown,true);assert.equal(button('退出编辑').disabled,false)
 await act(async()=>button('退出编辑').click());assert.equal(calls.filter(item=>item.op==='revoke-write').length,1);assert.equal(states.at(-1).unknown,true)
 assert.equal(button('启用编辑').disabled,false,'explicit recovery authorization remains reachable after revoking an unknown write')
 assert.equal(button('读取状态').disabled,true)
 await act(async()=>button('启用编辑').click())
 assert.equal(calls.filter(item=>item.op==='authorize-write').length,2);assert.equal(states.at(-1).unknown,true)
 assert.equal(gesture,undefined);assert.equal(quick,undefined);assert.equal(undo,undefined);assert.equal(enable,undefined,'bottom direct-edit enable never advertises unknown state as editable')
 assert.equal(button('撤销当前版本').disabled,true);assert.equal(button('读取状态').disabled,false)
 await assert.rejects(retained(operations),/编辑状态已变化/)
 await act(async()=>button('读取状态').click());assert.equal(states.at(-1).unknown,true);assert.equal(gesture,undefined)
 assert.equal(calls.filter(item=>item.op==='status').at(-1).writeGrant,'explicit-2')
 statusPending=false;await act(async()=>button('读取状态').click())
 assert.equal(states.at(-1).unknown,false);assert.equal(typeof gesture,'function');assert.equal(typeof quick,'function');assert.equal(typeof undo,'function')
 assert.equal(refreshes,2);assert.equal(calls.filter(item=>item.op==='plan').length,1);assert.equal(calls.filter(item=>item.op==='commit').length,1,'unknown original write was never retried')
})
