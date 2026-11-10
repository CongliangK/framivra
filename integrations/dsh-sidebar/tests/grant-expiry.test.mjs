import test from 'node:test'
import assert from 'node:assert/strict'
import React, { act } from 'react'
import { JSDOM } from 'jsdom'
import { stringifyJSON } from '../src/json.mjs'
import { AgentProposals } from '../src/client/proposals.mjs'
import { PreviewBody } from '../src/client/index.mjs'

const h = React.createElement

// Terminal grant death: after the Host's fixed TTL expires (or config /
// workspace moves under the grant), every further grant-bound call can only
// repeat BAD_GRANT / CONFIG_CHANGED / PATH_CHANGED. The client must stop
// polling, show ONE actionable banner, invalidate frame/draft/edit/proposal
// actions, and recover only through an explicit refresh / reopen (new grant).

const version = `sha256:${'a'.repeat(64)}`
const opId = '11111111-2222-4333-8444-555555555555'
const pendingRow = { proposal_id: 'p1', state: 'pending', timeline: 'timeline.json', base_revision: version,
  plan: { submitted_request: { op_id: opId, base_revision: version, operations: [{ op: 'project.resize', duration_frames: 130 }] } },
  confirmation: null, outcome: null }
const openedFor = grant => ({ grant, snapshot: { content_version: version, document: { project: {} } } })

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

test('BAD_GRANT stops the proposal poll permanently: one banner, lifted death, disabled actions, no 403 hammering', { timeout: 15000 }, async t => {
  const root = await mount(t)
  const calls = [], deaths = []
  const call = async (_session, payload) => {
    calls.push(payload.op)
    if (payload.op === 'proposal-list') {
      if (calls.length === 1) return { proposals: [{ ...pendingRow }] }
      throw new Error('BAD_GRANT: Preview grant expired, closed, belongs to another Session, or workspace changed')
    }
    throw new Error(`unexpected op ${payload.op}`)
  }
  await act(async () => root.render(h(AgentProposals, { sessionId: 'one', opened: openedFor('read'), tabSignal: new AbortController().signal, call, writeGrant: 'explicit', manualBlocked: false, onCommitted: () => {}, onEditState: () => {}, onGrantDead: (code, grant) => deaths.push({ code, grant }) })))
  assert(text().includes('等待您确认 · 尚未保存'), 'first poll rendered the pending row')
  await wait(1700) // second poll (1.5s cadence) now answered BAD_GRANT
  assert.match(text(), /预览授权已失效（BAD_GRANT）：BAD_GRANT: Preview grant expired[^\n]*已停止轮询/, 'terminal banner explains the death and the stopped poll')
  assert.deepEqual(deaths, [{ code: 'BAD_GRANT', grant: 'read' }], 'death lifted exactly once with the exact grant id')
  assert.equal(button('确认提交此智能体冻结提案').disabled, true, 'confirm invalidated after terminal death')
  assert.equal(button('拒绝此提案（不保存）').disabled, true, 'reject invalidated after terminal death')
  const stopped = calls.length
  await wait(3200) // two full former cadences
  assert.equal(calls.length, stopped, `no further proposal-list polls (calls=${calls.join(',')})`)
})

test('non-terminal failures keep the ordinary retry cadence and never lift a grant death', { timeout: 15000 }, async t => {
  const root = await mount(t)
  const calls = [], deaths = []
  const call = async (_session, payload) => { calls.push(payload.op); throw new Error('连接已断开') }
  await act(async () => root.render(h(AgentProposals, { sessionId: 'one', opened: openedFor('read'), tabSignal: new AbortController().signal, call, writeGrant: 'explicit', manualBlocked: false, onCommitted: () => {}, onEditState: () => {}, onGrantDead: (...args) => deaths.push(args) })))
  await wait(3200)
  assert.ok(calls.filter(op => op === 'proposal-list').length >= 3, 'transport errors still retry on cadence')
  assert.match(text(), /无法读取智能体提案：连接已断开。请检查连接；不会自动提交。/)
  assert.deepEqual(deaths, [], 'no death lifted for a non-terminal failure')
})

// ---------- PreviewBody: host TTL deadline locks the stale view ----------

const coverage = { complete: true, scope: 'stored', unexpanded: [] }
const snapshotFor = grant => ({ content_version: version, coverage, diagnostics: [], document: { project: { name: `p-${grant}`, duration_frames: 30, fps: 30 }, _vtx: { protocol: 1, head: 'h' }, sources: [{ id: 's', path: 'a.mov' }] } })
const tracks = [{ family: 'video', track_index: 0, address: '/t/0', data: { name: 'MAIN_TRACK', id: 'trk' } }]
const blocks = [{ family: 'video', track_index: 0, block_index: 0, address: '/t/0/b/0', range: [0, 20], data: { id: 'blk1', src: 's.mp4' } }]
const full = { content_version: version, coverage, diagnostics: [], tracks, blocks }

test('host expires_in_ms deadline: banner, tool teardown, stopped proposal poll; an explicit refresh mints a new grant and recovers', { timeout: 20000 }, async t => {
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
    if (request.op === 'open') result = { grant: `g${++grantCounter}`, snapshot: snapshotFor(`g${grantCounter}`), expires_in_ms: grantCounter === 1 ? 400 : 400000, media: [] }
    else if (request.op === 'query') result = full
    else if (request.op === 'proposal-list') result = { ok: true, proposals: [] }
    else result = { ok: true }
    return new Response(stringifyJSON(result))
  }
  const sessions = { binding: id => ({ sessionId: id }), list: { getSnapshot: () => ({ byId: { one: '/w' } }), subscribe: () => () => {} } }
  const uiSession = { adapter: { current: { getSnapshot: () => ({ key: 'one' }), subscribe: () => () => {} } } }
  const host = { getDraftServices: () => ({ active: true, conversation: {}, sessions, uiSession }), useTabInfo: () => ({ tab: { signal: new AbortController().signal } }) }
  await act(async () => root.render(h(PreviewBody, { ...host, sessionId: 'one', inputActions: {}, useAccess: () => true })))
  await act(async () => document.querySelectorAll('form')[0].dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true })))
  for (let i = 0; i < 200 && !document.querySelector('.fv-ruler'); i++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 5)) })
  assert(document.querySelector('.fv-ruler'), 'preview reached ready')
  assert(document.body.textContent.includes('剪辑编辑与提交'), 'manual editor mounted on the live grant')
  assert(document.querySelector('[aria-label="当前合成帧与草稿"]'), 'frame tools mounted on the live grant')
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 3000)) }) // past the 400ms TTL
  assert(document.body.textContent.includes('预览授权已失效'), 'expiry banner is visible: ' + document.body.textContent.slice(0, 200))
  assert(document.body.textContent.includes('（EXPIRED）'), 'banner carries the expiry code')
  assert(!document.body.textContent.includes('剪辑编辑与提交'), 'manual editor (and its proposal poller) torn down')
  assert(document.querySelector('[aria-label="当前合成帧与草稿"]') === null, 'frame tools torn down — no stale verified PNG stays actionable')
  const pollsAtDeath = requests.filter(r => r.op === 'proposal-list').length
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 2000)) })
  assert.equal(requests.filter(r => r.op === 'proposal-list').length, pollsAtDeath, 'proposal polling stopped with the dead grant')
  // explicit refresh (the banner's action) mints a NEW grant and recovers
  const refresh = document.querySelector('[aria-label="刷新保存版本"]')
  assert(refresh && !refresh.disabled, 'explicit refresh stays available as the way out')
  await act(async () => refresh.click())
  for (let i = 0; i < 200 && !requests.some(r => r.op === 'open' && r.grant === undefined); i++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 5)) })
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 500)) })
  assert.equal(requests.filter(r => r.op === 'open').length, 2, 'refresh reopened the timeline (new grant)')
  assert(!document.body.textContent.includes('预览授权已失效'), 'banner cleared by the new grant')
  assert(document.body.textContent.includes('剪辑编辑与提交'), 'editing recovered on the new grant')
})
