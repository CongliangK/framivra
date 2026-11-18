import test from 'node:test'
import assert from 'node:assert/strict'
import { selectionEnvironment, skip } from './selection-env.mjs'
const address=index=>`/video_tracks/0/clips/${index}`
const clean=text=>{const doc=JSON.parse(text);delete doc._vtx;return JSON.stringify(doc)}
const clips=async ui=>JSON.parse(await ui.read()).video_tracks[0].clips
const remove=()=>document.querySelector('[aria-label="移除选中片段"]')
const split=()=>document.querySelector('[aria-label="在播放头分割选中片段"]')
function geometry(){
 document.querySelector('.fv-ruler').getBoundingClientRect=()=>({left:0,width:1200})
 document.querySelectorAll('.fv-track').forEach((node,i)=>{node.getBoundingClientRect=()=>({top:i*100,bottom:(i+1)*100})})
}
for(const kind of ['source','zip']){
 const options={skip:skip||(kind==='zip'&&!process.env.SIDEBAR_MANUAL_ZIP&&'Set SIDEBAR_MANUAL_ZIP'),timeout:120000}
 test(`${kind}: visual A -> B and reverse direct removal use only current reference target; exact business undo`,options,async t=>{
  const ui=await selectionEnvironment(t,kind),before=await ui.read()
  for(const [visual,next,target,untouched] of [[0,1,'c2','c'],[1,0,'c','c2']]){
   await ui.click(ui.clip(visual));await ui.click(ui.clip(next));assert.equal(JSON.parse(await ui.referenceRaw()).id,target)
   await ui.saved(()=>ui.click(remove()))
   const plan=ui.wire.filter(row=>row.request.op==='plan').at(-1),saved=await clips(ui)
   assert.equal(plan.request.operations.length,1);assert.equal(plan.request.operations[0].op,'block.remove');assert.equal(plan.request.operations[0].id,target)
   assert.equal(saved.some(clip=>clip.id===target),false);assert.equal(JSON.stringify(saved.find(clip=>clip.id===untouched))===JSON.stringify(JSON.parse(before).video_tracks[0].clips.find(clip=>clip.id===untouched)),true)
   await ui.undo();assert.equal(clean(await ui.read())===clean(before),true,'undo restores all business fields')
  }
  assert.equal(ui.wire.some(row=>row.request.op.startsWith('proposal-')),false)
  await ui.save('selection-removal.json',{wire:ui.wire,saved:JSON.parse(await ui.read())})
 })
 test(`${kind}: track selection clears group; multi-block direct move retains stable group through authoritative refresh`,options,async t=>{
  const ui=await selectionEnvironment(t,kind),before=await ui.read()
  await ui.click(ui.clip(0));await ui.click(ui.clip(1),{ctrlKey:true});assert.equal(document.querySelectorAll('.fv-clip[aria-pressed="true"]').length,2)
  await ui.click('选择轨道 /video_tracks/1');assert.equal(document.querySelectorAll('.fv-clip[aria-pressed="true"]').length,0)
  assert.equal(ui.button('选择轨道 /video_tracks/1').getAttribute('aria-pressed'),'true');assert.equal(remove().disabled,true)
  await ui.click(ui.clip(0));await ui.click(ui.clip(1),{ctrlKey:true});geometry()
  await ui.saved(async()=>{await ui.event(ui.clip(0),'pointerdown',{button:0,clientX:0,clientY:10});await ui.event(ui.clip(0),'pointerup',{clientX:10,clientY:10})})
  const operations=ui.wire.filter(row=>row.request.op==='plan').at(-1).request.operations
  assert.equal(operations.length,2);assert.equal(operations[0].id,'c');assert.equal(operations[0].at,1);assert.equal(operations[1].id,'c2');assert.equal(operations[1].at,61)
  assert.equal(document.querySelectorAll('.fv-clip[aria-pressed="true"]').length,2)
  const moved=await clips(ui);assert.equal(moved.find(clip=>clip.id==='c').dst_in,1);assert.equal(moved.find(clip=>clip.id==='c2').dst_in,61)
  await ui.undo();assert.equal(clean(await ui.read())===clean(before),true)
  await ui.click(ui.clip(1));await ui.change('帧（留空为全部） ','70');await ui.saved(()=>ui.click(split()))
  const saved=await clips(ui);assert.equal(saved.length,3);assert.equal(saved.find(clip=>clip.id==='c').dur,30);assert.equal(saved.find(clip=>clip.id==='c2').dur,10)
  assert.equal(ui.wire.filter(row=>row.request.op==='plan').at(-1).request.operations[0].id,'c2')
  await ui.undo();assert.equal(clean(await ui.read())===clean(before),true)
  await ui.save('retained-group.json',{operations,saved:JSON.parse(await ui.read())})
 })
 test(`${kind}: Ctrl/Shift, keyboard focus, late raw answer and cancelled ghost cannot retarget; path draft survives selection`,options,async t=>{
  const ui=await selectionEnvironment(t,kind),before=await ui.read()
  await ui.click(ui.clip(0));await ui.click(ui.clip(1),{shiftKey:true});assert.equal(document.querySelectorAll('.fv-clip[aria-pressed="true"]').length,2)
  await ui.click(ui.clip(1),{ctrlKey:true});assert.equal(document.querySelectorAll('.fv-clip[aria-pressed="true"]').length,1)
  geometry();const commits=ui.wire.filter(row=>row.request.op==='commit').length
  await ui.event(ui.clip(0),'pointerdown',{button:0,clientX:0,clientY:10});await ui.event(ui.clip(0),'pointermove',{clientX:10,clientY:10})
  assert.equal(document.querySelector('[aria-label="未保存拖动 ghost"]')!==null,true)
  await ui.click(ui.clip(1));assert.equal(document.querySelector('[aria-label="未保存拖动 ghost"]') === null, true)
  await ui.event(ui.clip(0),'pointerup',{clientX:10,clientY:10});assert.equal(ui.wire.filter(row=>row.request.op==='commit').length,commits)
  ui.clip(1).focus();await ui.event(ui.clip(1),'keydown',{key:'Enter'});await ui.event(ui.clip(1),'keyup',{key:'Enter'});await ui.click(document.activeElement,{detail:0})
  assert.equal(JSON.parse(await ui.referenceRaw()).id,'c2')
  await ui.change('时间线路径 ','draft-path.json')
  ui.faults.hold=request=>request.op==='reference'&&request.query.block_addresses?.[0]===address(0)
  await ui.click(ui.clip(0));await ui.settle(()=>ui.held.length===1)
  await ui.click(ui.clip(1));await ui.settle(()=>ui.wire.some(row=>row.request.op==='reference'&&row.request.query.block_addresses?.[0]===address(1)))
  ui.faults.hold=undefined;ui.held.splice(0).forEach(resolve=>resolve());await ui.flush()
  assert.equal(JSON.parse(await ui.referenceRaw()).id,'c2');assert.equal(document.querySelector('input[name="framivra-timeline"]').value,'draft-path.json')
  await ui.change('帧（留空为全部） ','70');assert.equal(document.querySelector('[aria-label="选中对象 raw JSON"]') === null, true)
  await ui.click('复制 raw JSON');assert.equal(JSON.parse(ui.copies.at(-1)).id,'c2');assert.equal(clean(await ui.read())===clean(before),true)
  await ui.save('retained-selection.json',{copies:ui.copies,wire:ui.wire,keyboard:'native key events + explicit native default-click adapter; not Chrome'})
 })
}
