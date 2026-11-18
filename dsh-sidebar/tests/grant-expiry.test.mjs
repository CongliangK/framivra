import { domTextExcerpt } from './dom-diagnostics.mjs'
import test from 'node:test'
import assert from 'node:assert/strict'
import React, { act } from 'react'
import { JSDOM } from 'jsdom'
import { stringifyJSON } from '../src/json.mjs'
import { ManualEditor } from '../src/client/manual.mjs'
import { PreviewBody } from '../src/client/index.mjs'

const h = React.createElement

// Terminal grant death must lift to PreviewBody; only explicit reopening
// recovers. Removed proposals must not cause polling or authorization.
const version=`sha256:${'a'.repeat(64)}`
const openedFor=grant=>({grant,snapshot:{content_version:version,document:{project:{duration_frames:30},_vtx:{protocol:1,head:'h'}}}})

async function mount(t) {
  const dom = new JSDOM('<div id="root"></div>', { url: 'http://owner/' })
  globalThis.window = dom.window; globalThis.document = dom.window.document; globalThis.IS_REACT_ACT_ENVIRONMENT = true
  const { createRoot } = await import('react-dom/client')
  const root = createRoot(document.getElementById('root'))
  t.after(async () => { await act(async () => root.unmount()); dom.window.close(); delete globalThis.window; delete globalThis.document })
  return root
}
const button = text => [...document.querySelectorAll('button')].find(n => n.textContent === text)
const text = () => document.body.textContent
const wait = async ms => act(async () => { await new Promise(resolve => setTimeout(resolve, ms)) })

test('terminal direct authorization failure lifts exact grant death, with no polling or automatic retry',{timeout:15000},async t=>{
 const root=await mount(t),calls=[],deaths=[]
 const call=async(_session,payload)=>{calls.push(payload.op);throw Error('BAD_GRANT: Preview grant expired')}
 await act(async()=>root.render(h(ManualEditor,{sessionId:'one',opened:openedFor('read'),result:{tracks:[],blocks:[]},tabSignal:new AbortController().signal,call,onCommitted:()=>{},onGrantDead:(code,grant)=>deaths.push({code,grant})})))
 assert.equal(calls.length,0)
 await act(async()=>button('启用编辑').click());assert.equal(calls.length,1);assert.equal(calls[0],'authorize-write');assert.equal(deaths.length,1);assert.equal(deaths[0].code,'BAD_GRANT');assert.equal(deaths[0].grant,'read')
 await wait(60);assert.equal(calls.length,1);assert.equal(document.body.textContent.includes('BAD_GRANT'),true)
})

test('ordinary direct transport error is not grant death and retries only on another explicit click',async t=>{
 const root=await mount(t),calls=[],deaths=[]
 const call=async(_session,payload)=>{calls.push(payload.op);throw Error('连接已断开')}
 await act(async()=>root.render(h(ManualEditor,{sessionId:'one',opened:openedFor('read'),result:{tracks:[],blocks:[]},tabSignal:new AbortController().signal,call,onCommitted:()=>{},onGrantDead:(...args)=>deaths.push(args)})))
 await act(async()=>button('启用编辑').click());await wait(60)
 assert.equal(calls.length,1);assert.equal(deaths.length,0);assert.equal(document.body.textContent.includes('连接已断开'),true)
 await act(async()=>button('启用编辑').click());assert.equal(calls.length,2);assert.equal(calls.every(op=>op==='authorize-write'),true)
})

// ---------- PreviewBody: host TTL deadline locks the stale view ----------

const coverage = { complete: true, scope: 'stored', unexpanded: [] }
const snapshotFor = grant => ({ content_version: version, coverage, diagnostics: [], document: { project: { name: `p-${grant}`, duration_frames: 30, fps: 30 }, _vtx: { protocol: 1, head: 'h' }, sources: [{ id: 's', path: 'a.mov' }] } })
const tracks = [{ family: 'video', track_index: 0, address: '/t/0', data: { name: 'MAIN_TRACK', id: 'trk' } }]
const blocks = [{ family: 'video', track_index: 0, block_index: 0, address: '/t/0/b/0', range: [0, 20], data: { id: 'blk1', src: 's.mp4' } }]
const full = { content_version: version, coverage, diagnostics: [], tracks, blocks }

test('host expires_in_ms deadline: banner, tool teardown, no polling; an explicit refresh mints a new grant and recovers', { timeout: 20000 }, async t => {
  const dom = new JSDOM('<div id="root"></div>', { url: 'http://owner/' })
  globalThis.window = dom.window; globalThis.document = dom.window.document; globalThis.IS_REACT_ACT_ENVIRONMENT = true
  const { createRoot } = await import('react-dom/client')
  const root = createRoot(document.getElementById('root'))
  t.after(async () => { await act(async () => root.unmount()); dom.window.close(); delete globalThis.window; delete globalThis.document })
  const oldFetch = globalThis.fetch
  t.after(() => { globalThis.fetch = oldFetch })
  const requests = []
  let grantCounter = 0
  globalThis.fetch = async (_url, init) => {
    const request = JSON.parse(init.body)
    requests.push(request)
    let result
    if (request.op === 'open') result = { grant: `g${++grantCounter}`, snapshot: snapshotFor(`g${grantCounter}`), expires_in_ms: grantCounter === 1 ? 400 : 400000, media: [], capabilities: { capabilities: { frame: { inline_png: { flag: '--include-png' } } } } }
    else if (request.op === 'query') result = full
    else result = { ok: true }
    return new Response(stringifyJSON(result))
  }
  const sessions = { binding: id => ({ sessionId: id }), list: { getSnapshot: () => ({ byId: { one: '/w' } }), subscribe: () => () => {} } }
  const uiSession = { adapter: { current: { getSnapshot: () => ({ key: 'one' }), subscribe: () => () => {} } } }
  const tabSignal = new AbortController().signal
  const host = { getDraftServices: () => ({ active: true, conversation: {}, sessions, uiSession }), useTabInfo: () => ({ tab: { signal: tabSignal } }) }
  await act(async () => root.render(h(PreviewBody, { ...host, sessionId: 'one', inputActions: {}, useAccess: () => true })))
  await act(async () => document.querySelectorAll('form')[0].dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true })))
  for (let i = 0; i < 200 && !document.querySelector('.fv-ruler'); i++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 5)) })
  assert.equal(Boolean(document.querySelector('.fv-ruler')), true, 'preview reached ready')
  assert.equal(Boolean(document.querySelector('[aria-label="时间线编辑"]') !== null), true, 'manual editor mounted on the live grant')
  assert.equal(Boolean(document.querySelector('[aria-label="当前合成帧与草稿"]')), true, 'frame tools mounted on the live grant')
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 3000)) }) // past the 400ms TTL
  assert.equal(Boolean(document.body.textContent.includes('预览授权已失效')), true, 'expiry banner is visible: ' + domTextExcerpt(document))
  assert.equal(Boolean(document.body.textContent.includes('（EXPIRED）')), true, 'banner carries the expiry code')
  assert.equal(Boolean(document.querySelector('[aria-label="时间线编辑"]') === null), true, 'manual editor torn down')
  assert.equal(Boolean(document.querySelector('[aria-label="当前合成帧与草稿"]') === null), true, 'frame tools torn down — no stale verified PNG stays actionable')
  assert.equal(requests.some(r => r.op.startsWith('proposal-')),false)
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 2000)) })
  assert.equal(requests.some(r => r.op.startsWith('proposal-')),false,'no removed proposal polling')
  assert.equal(requests.some(r => r.op==='authorize-write'),false,'opening and TTL recovery do not authorize writes')
  // explicit refresh (the banner's action) mints a NEW grant and recovers
  const refresh = document.querySelector('[aria-label="刷新保存版本"]')
  assert.equal(Boolean(refresh && !refresh.disabled), true, 'explicit refresh stays available as the way out')
  await act(async () => refresh.click())
  for (let i = 0; i < 200 && !requests.some(r => r.op === 'open' && r.grant === undefined); i++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 5)) })
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 500)) })
  assert.equal(requests.filter(r => r.op === 'open').length, 2, 'refresh reopened the timeline (new grant)')
  assert.equal(Boolean(!document.body.textContent.includes('预览授权已失效')), true, 'banner cleared by the new grant')
  assert.equal(Boolean(document.querySelector('[aria-label="时间线编辑"]') !== null), true, 'editing recovered on the new grant')
})
