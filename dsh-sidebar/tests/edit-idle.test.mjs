import test from 'node:test'
import assert from 'node:assert/strict'

// Actual PreviewBody and frame epoch; transport stub only. There is no
// proposal poll, and read/display controls do not imply write authorization.
const version=`sha256:${'a'.repeat(64)}`
const snapshot={content_version:version,coverage:{complete:true},document:{project:{duration_frames:120},_vtx:{protocol:1}}}
const result={content_version:version,coverage:{complete:true},tracks:[{family:'video',track_index:0,address:'/t/0',data:{id:'v',name:'main'}}],blocks:[{family:'video',track_index:0,address:'/t/0/b/0',range:[0,30],data:{id:'c'}}]}
test('idle controls never poll or authorize; an actual direct transaction aborts an in-flight frame render',{timeout:20000},async t=>{
 const {JSDOM}=await import('jsdom'),React=(await import('react')).default,{act}=await import('react')
 const dom=new JSDOM('<div id="root"></div>',{url:'http://owner/'})
 globalThis.window=dom.window;globalThis.document=dom.window.document;globalThis.IS_REACT_ACT_ENVIRONMENT=true
 const {createRoot}=await import('react-dom/client'),{Simulate}=await import('react-dom/test-utils'),{PreviewBody}=await import('../src/client/index.mjs')
 const root=createRoot(document.getElementById('root')),requests=[];let renderSignal,resolvePlan
 const respond=payload=>new Response(JSON.stringify(payload)),oldFetch=globalThis.fetch
 globalThis.fetch=async(url,init={})=>{
  if(url==='/api/framivra/frame'){renderSignal=init.signal;return new Promise(()=>{})}
  const body=JSON.parse(init.body??'{}');requests.push(body.op)
  if(body.op==='open')return respond({grant:'read',snapshot,media:[],capabilities:{capabilities:{frame:{inline_png:{flag:'--include-png'}}}}})
  if(body.op==='query')return respond(result)
   if(body.op==='reference')return respond({ok:true,content_version:version,selection:{blocks:[{address:'/t/0/b/0',stable_id:'c'}]}})
  if(body.op==='authorize-write')return respond({ok:true,writeGrant:'explicit'})
  if(body.op==='plan')return new Promise(resolve=>{resolvePlan=answer=>resolve(respond(answer))})
  return respond({ok:true})
 }
 t.after(async()=>{globalThis.fetch=oldFetch;await act(async()=>root.unmount());dom.window.close();delete globalThis.window;delete globalThis.document})
 const tab=new AbortController(),flush=async()=>{for(let i=0;i<20;i++)await act(async()=>{await Promise.resolve()})}
 const button=text=>[...document.querySelectorAll('button')].find(node=>node.textContent===text)
 await act(async()=>root.render(React.createElement(PreviewBody,{sessionId:'one',useTabInfo:()=>({tab:{signal:tab.signal}})})))
 await act(async()=>document.querySelector('form').dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true})));await flush()
 assert.equal(document.querySelector('[aria-label="时间线编辑"]')!==null,true);assert.equal(requests.includes('authorize-write'),false);assert.equal(requests.some(op=>op.startsWith('proposal-')),false)
 const clip=document.querySelector('.fv-clip');await act(async()=>clip.click());await flush()
 await act(async()=>button('启用编辑').click());await flush()
 await act(async()=>Simulate.change(document.querySelector('input[name="framivra-frame"]'),{target:{value:'9'}}))
 const consent=[...document.querySelectorAll('label')].find(node=>node.textContent==='允许写入预览缓存（不改时间线）')?.querySelector('input[type=checkbox]')
 assert.equal(consent!==undefined&&consent!==null,true,'select preview-cache consent, not position-only or live-follow input')
 await act(async()=>Simulate.change(consent,{target:{checked:true}}))
 await act(async()=>button('显式生成并验证固定帧 PNG').click());await flush();assert.equal(renderSignal!==undefined,true);assert.equal(renderSignal.aborted,false)
 document.querySelector('.fv-ruler').getBoundingClientRect=()=>({left:0,width:1200});document.querySelector('.fv-track').getBoundingClientRect=()=>({top:0,bottom:100})
 await act(async()=>Simulate.pointerDown(clip,{pointerId:1,button:0,clientX:0,clientY:10}));await act(async()=>Simulate.pointerUp(clip,{pointerId:1,clientX:10,clientY:10}));await flush()
 assert.equal(typeof resolvePlan,'function');assert.equal(renderSignal.aborted,true,'owned direct transaction invalidates frame export')
 await act(async()=>resolvePlan({ok:false,code:'edit.operation',message:'rejected'}));await flush()
 assert.equal(requests.filter(op=>op==='authorize-write').length,1);assert.equal(requests.filter(op=>op==='plan').length,1);assert.equal(requests.includes('commit'),false);assert.equal(requests.some(op=>op.startsWith('proposal-')),false)
})
