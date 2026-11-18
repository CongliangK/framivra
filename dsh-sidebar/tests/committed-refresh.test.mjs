import test from 'node:test'
import assert from 'node:assert/strict'
import React, { act } from 'react'
import { JSDOM } from 'jsdom'
import { ManualEditor } from '../src/client/manual.mjs'

// A confirmed write and a failed display refresh are different verdicts.
// No stale view may issue another write; status/recover are not write proof.
const h=React.createElement,version=`sha256:${'b'.repeat(64)}`
const opened={grant:'g1',snapshot:{content_version:version,document:{project:{duration_frames:120},_vtx:{protocol:1,head:'h1'},sources:[]}}}
const result={tracks:[{family:'video',track_index:0,address:'/t/0',data:{id:'trk'}}],blocks:[{family:'video',track_index:0,address:'/t/0/b/0',range:[0,30],data:{id:'c'}}]}
const operations=[{op:'block.move',id:'c',track_id:'trk',at:10}]
const button=text=>[...document.querySelectorAll('button')].find(node=>node.textContent===text)
async function mount(t,call,onCommitted,extra={}){
 const dom=new JSDOM('<div id="root"></div>',{url:'http://owner/'})
 globalThis.window=dom.window;globalThis.document=dom.window.document;globalThis.IS_REACT_ACT_ENVIRONMENT=true
 const {createRoot}=await import('react-dom/client'),root=createRoot(document.getElementById('root'))
 t.after(async()=>{await act(async()=>root.unmount());dom.window.close();delete globalThis.window;delete globalThis.document})
 const reports=[];let gesture,quick,undo
 const props={sessionId:'one',opened,result,frame:15,tabSignal:new AbortController().signal,call,onCommitted,onEditState:state=>reports.push(state),onGestureReady:callback=>{gesture=callback},onQuickReady:callback=>{quick=callback},onUndoReady:callback=>{undo=callback},...extra}
 await act(async()=>root.render(h(ManualEditor,props)));await act(async()=>button('启用编辑').click())
 return {reports,gesture:()=>gesture,quick:()=>quick,undo:()=>undo,render:async next=>act(async()=>root.render(h(ManualEditor,{...props,...next})))}
}
const settled=ui=>ui.reports.filter(state=>!state.busy).at(-1)

for(const write of ['commit','undo','adopt'])test(`${write}: confirmed receipt survives failed refresh; stale view is locked without auto retry`,async t=>{
 const calls=[];let refreshes=0
 const call=async(_session,payload)=>{calls.push(payload.op);if(payload.op==='authorize-write')return {ok:true,writeGrant:'wg'};if(payload.op==='plan')return {ok:true,planToken:'tok',submitted_request:{op_id:'op-9'}};return {ok:true}}
 const extra=write==='adopt'?{opened:{...opened,snapshot:{...opened.snapshot,document:{project:{duration_frames:120}}}}}:{}
 const ui=await mount(t,call,async()=>{refreshes++;throw Error('REFRESH_GONE')},extra)
 if(write==='commit')await act(async()=>assert.rejects(ui.gesture()(operations),error=>error.applied===true&&error.outcomeUnknown!==true))
 else await act(async()=>button(write==='undo'?'撤销当前版本':'确认接管旧工程').click())
 assert.equal(settled(ui).unknown,false);assert.equal(settled(ui).dirty,true);assert.equal(ui.gesture(),undefined);assert.equal(ui.quick(),undefined);assert.equal(ui.undo(),undefined)
 assert.equal(document.body.textContent.includes('刷新保存版本'),true);assert.equal(document.body.textContent.includes('结果未知'),false)
 assert.equal(button('读取状态').disabled,false);assert.equal(calls.filter(op=>op===write).length,1);assert.equal(refreshes,1)
 const before=calls.length;await act(async()=>{await new Promise(resolve=>setTimeout(resolve,60))});assert.equal(calls.length,before)
})

test('status ok with failed refresh does not clear a prior unknown write verdict',async t=>{
 const calls=[]
 const call=async(_session,payload)=>{calls.push(payload.op);if(payload.op==='authorize-write')return {ok:true,writeGrant:'wg'};if(payload.op==='undo')return {ok:false,code:'outcome_unknown'};return {ok:true,history:[],pending:false}}
 const ui=await mount(t,call,async()=>{throw Error('REFRESH_GONE')})
 await act(async()=>button('撤销当前版本').click());assert.equal(settled(ui).unknown,true)
 await act(async()=>button('读取状态').click());assert.equal(settled(ui).unknown,true);assert.equal(settled(ui).dirty,true)
 assert.equal(document.body.textContent.includes('原写入结果未重新判定'),true);assert.equal(document.body.textContent.includes('内核已确认本次写入'),false)
 assert.equal(button('读取状态').disabled,false);assert.equal(ui.gesture(),undefined);assert.equal(calls.filter(op=>op==='undo').length,1)
})

test('successful explicit status refresh releases stale-owned dirty and callbacks, without resubmitting the write',async t=>{
 const calls=[];let refreshes=0
 const committed=`sha256:${'c'.repeat(64)}`,observed=`sha256:${'d'.repeat(64)}`
 const call=async(_session,payload)=>{calls.push(payload.op);if(payload.op==='authorize-write')return {ok:true,writeGrant:'wg'};if(payload.op==='plan')return {ok:true,planToken:'tok',submitted_request:{op_id:'op-9'}};if(payload.op==='commit')return {ok:true,revision:committed,current_revision:observed};return {ok:true,history:[],pending:false,current_revision:observed}}
 const ui=await mount(t,call,async({expectedRevision})=>{if(++refreshes===1){assert.equal(expectedRevision,committed);throw Error('REFRESH_GONE')}assert.equal(expectedRevision,observed)})
 await act(async()=>assert.rejects(ui.gesture()(operations),error=>error.applied===true));assert.equal(settled(ui).dirty,true)
 await act(async()=>button('读取状态').click());assert.equal(settled(ui).dirty,false);assert.equal(settled(ui).unknown,false);assert.equal(typeof ui.gesture(),'function');assert.equal(typeof ui.undo(),'function')
 assert.equal(refreshes,2);assert.equal(calls.filter(op=>op==='plan').length,1);assert.equal(calls.filter(op=>op==='commit').length,1)
 assert.equal(calls.some(op=>op.startsWith('proposal-')),false)
})
