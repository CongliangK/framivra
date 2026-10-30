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
 await act(async()=>Simulate.pointerDown(clip(0),{pointerId:4,button:0,clientX:0,clientY:10}));await act(async()=>Simulate.pointerMove(clip(0),{pointerId:4,clientX:30,clientY:10}));await act(async()=>Simulate.pointerCancel(clip(0),{}));assert.equal(document.querySelector('[aria-label="未保存拖动 ghost"]'),null);assert.equal(batches.length,0)
 await act(async()=>render(undefined));assert.equal(document.querySelector('[aria-label^="左 trim"]'),null)
 await act(async()=>Simulate.pointerDown(clip(0),{pointerId:5,button:0,clientX:0,clientY:10}));await act(async()=>Simulate.pointerUp(clip(0),{pointerId:5,clientX:30,clientY:10}));assert.equal(batches.length,0)
})

test('all dedicated forms exist, explicit write auth; in-flight plan cannot survive edited input or selection',{timeout:10000},async t=>{
 const {root,Simulate}=await mount(t),calls=[];let resolvePlan
 const call=async(_session,payload)=>{calls.push(payload);if(payload.op==='authorize-write')return {writeGrant:'explicit'};if(payload.op==='plan')return await new Promise(r=>{resolvePlan=r});return {ok:true}}
 const selected=[{kind:'block',address:blocks[0].address}]
 const render=selection=>root.render(h(ManualEditor,{sessionId:'one',opened:{grant:'read',snapshot},result,selection,selected,frame:15,tabSignal:new AbortController().signal,call,onCommitted:()=>{}}))
 await act(async()=>render(selected[0]));assert.equal(document.querySelector('fieldset').disabled,true);assert.equal(calls.length,0)
 await act(async()=>button('明确允许本会话向此工程及 core 历史写入').click());assert.equal(document.querySelector('fieldset').disabled,false)
 for(const op of EDIT_OPERATIONS){const select=[...document.querySelectorAll('label')].find(n=>n.firstChild?.textContent==='编辑操作').querySelector('select');await act(async()=>Simulate.change(select,{target:{value:op}}));assert.equal(select.value,op);assert(document.querySelector('[aria-label="未保存 typed intents"]'));assert.equal(document.querySelector('textarea'),null)}
 const select=[...document.querySelectorAll('label')].find(n=>n.firstChild?.textContent==='编辑操作').querySelector('select');await act(async()=>Simulate.change(select,{target:{value:'project.resize'}}));await act(async()=>button('加入未保存批次').click());await act(async()=>button('内核预检计划（零写）').click());assert(resolvePlan)
 const duration=[...document.querySelectorAll('label')].find(n=>n.firstChild?.textContent==='工程新时长帧（不改 FPS/分辨率）').querySelector('input');await act(async()=>Simulate.change(duration,{target:{value:'130'}}));await act(async()=>resolvePlan({ok:true,planToken:'obsolete',submitted_request:{op_id:'old'}}));assert.equal(button('确认提交此冻结计划（一次 revision）'),undefined)
 assert.equal(calls.filter(c=>c.op==='commit').length,0);assert(calls.some(c=>c.op==='invalidate-plan'))
 await act(async()=>button('撤销本会话/工程写许可').click());assert.equal(document.querySelector('fieldset').disabled,true)
})
