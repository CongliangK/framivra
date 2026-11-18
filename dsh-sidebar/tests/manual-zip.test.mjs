import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile, mkdtemp } from 'node:fs/promises'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'
import { createRequire } from 'node:module'
import { commandFailure, spawnArchivePython } from '../scripts/archive-python.mjs'
import { runInNewContext } from 'node:vm'
import React, { act } from 'react'
import { JSDOM } from 'jsdom'
import { environment, fixture, skip } from './manual-env.mjs'
import { parseJSON } from '../src/json.mjs'
const archive=process.env.SIDEBAR_MANUAL_ZIP

test('candidate ZIP SDK-materialized PreviewBody and extracted Host actually plan/commit via installed core and refresh saved raw',{skip:skip||(!archive&&'Set SIDEBAR_MANUAL_ZIP to a fresh candidate ZIP'),timeout:30000},async t=>{
 const extracted=await mkdtemp(join(process.env.SIDEBAR_EVIDENCE_DIR,'manual-zip-'))
 const extraction=spawnArchivePython('import zipfile,sys;zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])',[archive,extracted]);assert.equal(extraction.status,0,commandFailure(extraction))
 const {createPreviewHost}=await import(pathToFileURL(join(extracted,'lib/host.js')))
 const env=await environment(t,'zip-core',fixture(),{createHost:createPreviewHost})
 const queue=[],target={mode:'queue',pendingQueue:queue,load:row=>queue.push(row)},copies=[]
 const sandbox={window:{__ModuleLoader__:target},navigator:{clipboard:{writeText:async text=>copies.push(text)}},TextEncoder,TextDecoder,DOMException,AbortController,AbortSignal,URLSearchParams,crypto:globalThis.crypto,setTimeout,clearTimeout,console}
 const require=createRequire(import.meta.url)
 runInNewContext(await readFile(require.resolve('@deepseek-ai/dsh-client-modules/client'),'utf8'),sandbox)
 const modules=queue.shift().factory(()=>{throw Error('unexpected bootstrap dependency')})
 runInNewContext(await readFile(join(extracted,'lib/client.js'),'utf8'),sandbox)
 const manifest=JSON.parse(await readFile(join(extracted,'package.json'),'utf8'))
 const system=modules.createClientModuleSystem(target,{id:'bootstrap',exports:{}},{boot:{rev:'0',entries:[],batches:[]},staticModules:{react:React}})
 const client=await system.import(manifest.name);assert.equal(typeof client.PreviewBody,'function')
 const requests=[]
 sandbox.fetch=async(_url,init)=>{requests.push(parseJSON(init.body));return env.host.control(new Request('http://owner/api/framivra/control',{method:'POST',body:init.body,signal:init.signal}))}
 const dom=new JSDOM('<div id="root"></div>',{url:'http://owner/'});globalThis.window=dom.window;globalThis.document=dom.window.document;globalThis.IS_REACT_ACT_ENVIRONMENT=true
 dom.window.__ModuleLoader__=sandbox.window.__ModuleLoader__;sandbox.window=dom.window;sandbox.document=dom.window.document
 const {createRoot}=await import('react-dom/client'),{Simulate}=await import('react-dom/test-utils'),root=createRoot(document.getElementById('root'))
 t.after(async()=>{await act(async()=>root.unmount());dom.window.close();delete globalThis.window;delete globalThis.document})
 const button=label=>[...document.querySelectorAll('button')].find(n=>n.textContent===label)
 async function click(label){assert(button(label),label);assert.equal(button(label).disabled,false);await act(async()=>button(label).click())}
 async function settle(predicate){for(let i=0;i<500;i++){if(predicate())return;await act(async()=>{await new Promise(r=>setTimeout(r,5))})}assert.fail(document.body.textContent)}
 async function change(label,value){const node=[...document.querySelectorAll('label')].find(n=>n.firstChild?.textContent===label)?.querySelector('input,select');assert(node,label);await act(async()=>Simulate.change(node,{target:{value}}))}
 const signal=new AbortController().signal
 await act(async()=>root.render(React.createElement(client.PreviewBody,{sessionId:'one',useTabInfo:()=>({tab:{signal}})})))
 await act(async()=>document.querySelector('form').dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true})));await settle(()=>document.querySelector('.fv-ruler'))
 const before=await env.read();await click('启用编辑');await settle(()=>document.body.textContent.includes('已允许本会话写入工程及历史'))
 await change('编辑操作','project.resize');await change('工程新时长帧（不改 FPS/分辨率）','130');await click('加入批次');await click('预检');await settle(()=>button('保存更改'));assert.equal(await env.read(),before)
 await click('保存更改');await settle(()=>document.querySelector('[aria-label="待保存操作"]')===null)
 await settle(()=>document.body.textContent.includes('全片 [00:00:00, 00:04:10)')&&!button('启用编辑')&&!document.querySelector('[aria-label="手动语义编辑"] fieldset').disabled)
 const saved=await env.read();assert.equal(parseJSON(saved).project.duration_frames,130)
 assert(requests.some(r=>r.op==='plan'));assert(requests.some(r=>r.op==='commit'));assert(requests.some(r=>r.op==='inspect'))
 await act(async()=>document.querySelector('.fv-clip[aria-label^="选择块 /video_tracks/0/clips/0 ·"]').click());await settle(()=>document.body.textContent.includes('引用就绪。'));await click('复制 raw JSON');await settle(()=>copies.length===1);assert(saved.includes(copies[0]),'packaged copy must retain exact saved source text')
 assert(env.specs.filter(s=>s.argv.includes('--request-stdin')).every(s=>typeof s.stdio.stdin.data==='string'));assert(env.specs.every(s=>!s.argv.includes('/dev/stdin')))
 await env.save('packaged-result.json',{archive,materializer:'public SDK browser entry',sharedReact:true,host:'extracted production closure',provider:'real LocalSubprocessRuntime',kernel:'installed candidate wheel',saved:parseJSON(saved),requests,raw:copies[0],authentication:'UNRUN',layout:'jsdom; not browser layout claim'})
})
