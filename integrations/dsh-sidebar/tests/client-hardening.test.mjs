import test from 'node:test'
import assert from 'node:assert/strict'
import React, { act } from 'react'
import { JSDOM } from 'jsdom'
import { stringifyJSON } from '../src/json.mjs'
import { PreviewBody } from '../src/client/index.mjs'
import { CONTROL_REQUEST_LIMIT } from '../src/bounded.mjs'

const h = React.createElement

async function mount(t) {
  const dom = new JSDOM('<div id="root"></div>', { url: 'http://owner/' })
  globalThis.window = dom.window
  globalThis.document = dom.window.document
  globalThis.IS_REACT_ACT_ENVIRONMENT = true
  const { createRoot } = await import('react-dom/client')
  const { Simulate } = await import('react-dom/test-utils')
  const root = createRoot(document.getElementById('root'))
  t.after(async () => { await act(async () => root.unmount()); dom.window.close(); delete globalThis.window; delete globalThis.document })
  const label = name => document.querySelector(`[aria-label="${name}"]`)
  const settle = async (predicate, label2) => {
    for (let i = 0; i < 200; i++) {
      if (predicate()) return
      await act(async () => { await new Promise(resolve => setTimeout(resolve, 1)) })
    }
    assert.fail(`DOM did not settle${label2 ? ` (${label2})` : ''}: notices=${[...document.querySelectorAll('.fv-notice,[role=alert],.fv-message,p')].map(n => n.textContent).join('|').slice(0, 400)}`)
  }
  return { root, dom, Simulate, label, settle }
}

const version = `sha256:${'b'.repeat(64)}`
const coverage = { complete: true, scope: 'stored', unexpanded: [] }
const snapshotFor = grant => ({ content_version: version, coverage, diagnostics: [], document: { project: { name: `p-${grant}`, duration_frames: 30, fps: 30 }, _vtx: { protocol: 1, head: 'h' }, sources: [{ id: 's', path: 'a.mov' }] } })
const tracks = [{ family: 'video', track_index: 0, address: '/t/0', data: { name: 'MAIN_TRACK', id: 'trk' } }]
const blocks = [{ family: 'video', track_index: 0, block_index: 0, address: '/t/0/b/0', range: [0, 20], data: { id: 'blk1', src: 's.mp4' } }]
const full = { content_version: version, coverage, diagnostics: [], tracks, blocks }
const openedFor = grant => ({ grant, snapshot: snapshotFor(grant), media: [] })

function hostFixture() {
  const requests = []
  const store = { cwd: { one: '/w', two: '/other' }, key: 'one' }
  const observers = new Set()
  const sessions = {
    binding: id => ({ sessionId: id }),
    list: { getSnapshot: () => ({ byId: store.cwd }), subscribe: fn => { observers.add(fn); return () => observers.delete(fn) } },
  }
  const uiSession = { adapter: { current: { getSnapshot: () => ({ key: store.key }), subscribe: fn => { observers.add(fn); return () => observers.delete(fn) } } } }
  let grantCounter = 0
  globalThis.fetch = async (_url, init) => {
    const request = JSON.parse(init.body)
    requests.push(request)
    let result
    if (request.op === 'open') result = openedFor(`g${++grantCounter}`)
    else if (request.op === 'query') result = full
    else if (request.op === 'reference') result = { ok: true, content_version: version, selection: { tracks: [], blocks: [{ address: '/t/0/b/0', content_version: version, stable_id: null, raw: null }] }, diagnostics: [], coverage: { complete: true }, document: { raw: '{}', content_version: version } }
    else if (request.op === 'proposal-list') result = { ok: true, proposals: [] }
    else if (request.op === 'authorize-write') result = { ok: true, writeGrant: 'wg' }
    else result = { ok: true }
    return new Response(stringifyJSON(result))
  }
  const getDraftServices = () => ({ active: true, conversation: {}, sessions, uiSession })
  const tabSignal = new AbortController().signal
  const useTabInfo = () => ({ tab: { signal: tabSignal } })
  return { requests, getDraftServices, useTabInfo, store, notify: () => { for (const fn of observers) fn() } }
}

const openViaPathForm = async dom => act(async () => document.querySelectorAll('form')[0].dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true })))

// ---------- responses are bounded before decode ----------

test('control responses are bounded BEFORE decode: oversized body fails closed without a ready state', async t => {
  const { root, dom, settle } = await mount(t)
  const oldFetch = globalThis.fetch
  t.after(() => { globalThis.fetch = oldFetch })
  // A hostile/buggy Host answer larger than the wire cap must never reach JSON decode.
  globalThis.fetch = async () => new Response('x'.repeat(CONTROL_REQUEST_LIMIT + 64), { headers: { 'Content-Type': 'application/json' } })
  const host = { getDraftServices: () => ({}), useTabInfo: () => ({ tab: { signal: new AbortController().signal } }) }
  await act(async () => root.render(h(PreviewBody, { ...host, sessionId: 'one' })))
  await openViaPathForm(dom)
  await settle(() => document.body.textContent.includes('BODY_LIMIT'), 'oversized response surfaces the bound')
  assert(document.body.textContent.includes('BODY_LIMIT'), `error mentions the bound: ${document.body.textContent.slice(0, 200)}`)
  assert(document.querySelector('.fv-ruler') === null, 'oversized response never produces a ready timeline')
  assert.equal(document.querySelectorAll('video').length, 0)
})

// ---------- stale async results cannot replace newer state ----------

test('a late open response that minted a grant is closed and can never replace the newer project', async t => {
  const { root, dom, settle } = await mount(t)
  const oldFetch = globalThis.fetch
  t.after(() => { globalThis.fetch = oldFetch })
  const host = hostFixture() // sets its own fetch first
  const requests = host.requests
  const innerFetch = globalThis.fetch
  const pending = []
  globalThis.fetch = async (url, init) => {
    const request = JSON.parse(init.body)
    requests.push(request)
    if (request.op === 'open' && request.sessionId === 'one') {
      return new Promise((resolve, reject) => pending.push({ request, signal: init?.signal, resolve, reject }))
    }
    return innerFetch(url, init)
  }
  const props = sessionId => ({ ...host, sessionId, inputActions: {}, useAccess: () => true })
  await act(async () => root.render(h(PreviewBody, props('one'))))
  await openViaPathForm(dom)
  await settle(() => pending.length === 1)
  // switch sessions while the first open is still in flight
  await act(async () => root.render(h(PreviewBody, props('two'))))
  await openViaPathForm(dom)
  await settle(() => document.querySelector('.fv-ruler'), 'new session reaches ready')
  // the open fetch itself must ride the independent watchdog signal, not the UI signal
  assert(pending[0].signal && !pending[0].signal.aborted, 'UI session switch does NOT abort the in-flight open (it owns a minted resource)')
  // the stale minted grant finally resolves
  await act(async () => pending[0].resolve(new Response(stringifyJSON(openedFor('grant-stale')))))
  await settle(() => requests.some(r => r.op === 'close' && r.grant === 'grant-stale'), 'stale minted grant is explicitly closed')
  assert(!requests.some(r => r.op === 'query' && r.grant === 'grant-stale'), 'stale grant is never queried or published')
  assert(requests.some(r => r.op === 'query' && r.grant === 'g1'), `newer grant queried; ops=${requests.map(r => `${r.op}:${r.grant ?? ''}:${r.sessionId ?? ''}`).join(',')}`)
  assert(document.body.textContent.includes('p-g1'), 'published snapshot is the newer project')
  assert(!document.body.textContent.includes('p-grant-stale'))
})

test('unmounting mid-open still closes the grant the late response minted', async t => {
  const { root, dom, settle } = await mount(t)
  const oldFetch = globalThis.fetch
  t.after(() => { globalThis.fetch = oldFetch })
  const host = hostFixture()
  const requests = host.requests
  const innerFetch = globalThis.fetch
  const pending = []
  globalThis.fetch = async (url, init) => {
    const request = JSON.parse(init.body)
    requests.push(request)
    if (request.op === 'open') return new Promise(resolve => pending.push({ resolve }))
    return innerFetch(url, init)
  }
  await act(async () => root.render(h(PreviewBody, { ...host, sessionId: 'one', inputActions: {}, useAccess: () => true })))
  await openViaPathForm(dom)
  await settle(() => pending.length === 1)
  await act(async () => root.unmount())
  // the open completes after the component is gone; its grant must still be released
  await act(async () => pending[0].resolve(new Response(stringifyJSON(openedFor('grant-unmount')))))
  await settle(() => requests.some(r => r.op === 'close' && r.grant === 'grant-unmount'), 'unmounted open releases its minted grant')
  assert(!requests.some(r => r.op === 'query' && r.grant === 'grant-unmount'), 'never queried')
})

// ---------- integrated S / Delete shortcuts ----------

test('S/Delete act only on the current authorized managed selection and never plan or commit implicitly', async t => {
  const { root, dom, Simulate, settle } = await mount(t)
  const host = hostFixture()
  const requests = host.requests
  const props = { ...host, sessionId: 'one', inputActions: {}, useAccess: () => true }
  await act(async () => root.render(h(PreviewBody, props)))
  await openViaPathForm(dom)
  await settle(() => document.querySelector('.fv-ruler'))
  await settle(() => requests.some(r => r.op === 'proposal-list'))
  const monitor = document.querySelector('.fv-monitor')
  const key = async (target, k) => act(async () => Simulate.keyDown(target, { key: k }))
  // before authorization: S and Delete do nothing
  await key(monitor, 's'); await key(monitor, 'Delete')
  assert(!requests.some(r => r.op === 'invalidate-plan'), 'no quick action before write authorization')
  // authorize write, select the single managed block, pin an interior frame
  await act(async () => [...document.querySelectorAll('button')].find(b => b.textContent === '明确允许本会话向此工程及 core 历史写入').click())
  await settle(() => [...document.querySelectorAll('button')].some(b => b.textContent === '撤销本会话/工程写许可'), 'write grant active')
  const pickBlock = () => [...document.querySelectorAll('button')].find(b => b.textContent === '选择块 /t/0/b/0')
  await settle(() => pickBlock())
  await act(async () => pickBlock().click())
  await act(async () => Simulate.change(document.querySelector('input[name="framivra-frame"]'), { target: { value: '10' } }))
  const before = requests.filter(r => r.op === 'invalidate-plan').length
  await key(monitor, 's')
  await settle(() => requests.filter(r => r.op === 'invalidate-plan').length > before, 'quick split invalidates the old plan')
  assert(!requests.some(r => r.op === 'plan' || r.op === 'commit'), 'the shortcut never plans or commits by itself')
  assert(requests.filter(r => r.op === 'invalidate-plan').at(-1).writeGrant === 'wg', 'quick runs under the explicit write grant')
  // Delete with a block selected enqueues block.remove only
  const before2 = requests.filter(r => r.op === 'invalidate-plan').length
  await key(monitor, 'Backspace')
  await settle(() => requests.filter(r => r.op === 'invalidate-plan').length > before2)
  assert(!requests.some(r => r.op === 'plan' || r.op === 'commit'))
  // typing in the frame input is never hijacked as a shortcut
  const before3 = requests.filter(r => r.op === 'invalidate-plan').length
  await key(document.querySelector('input[name="framivra-frame"]'), 's')
  await key(document.querySelector('input[name="framivra-frame"]'), 'Delete')
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)) })
  assert.equal(requests.filter(r => r.op === 'invalidate-plan').length, before3, 'input fields are never intercepted')
  // no block selected (track selection): S does nothing
  const pickTrack = () => [...document.querySelectorAll('button')].find(b => b.textContent === '选择轨道 /t/0')
  await act(async () => pickTrack().click())
  const before4 = requests.filter(r => r.op === 'invalidate-plan').length
  await key(monitor, 's')
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)) })
  assert.equal(requests.filter(r => r.op === 'invalidate-plan').length, before4, 'track-only selection cannot split')
})

test('query-only refresh reuses the same grant and never re-opens or mints a replacement grant', async t => {
  const { root, dom, settle } = await mount(t)
  const host = hostFixture()
  const oldFetch = globalThis.fetch
  t.after(() => { globalThis.fetch = oldFetch })
  const requests = host.requests
  const props = { ...host, sessionId: 'one', inputActions: {}, useAccess: () => true }
  await act(async () => root.render(h(PreviewBody, props)))
  await openViaPathForm(dom)
  await settle(() => document.querySelector('.fv-ruler'))
  await settle(() => requests.some(r => r.op === 'proposal-list'))
  // a query-only refresh of the same frame view
  await act(async () => document.querySelector('form.fv-transport').dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true })))
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)) })
  const opens = requests.filter(r => r.op === 'open')
  assert.equal(opens.length, 1, 'query-only refresh never re-opens or mints a second grant')
  assert(requests.filter(r => r.op === 'query').every(r => r.grant === 'g1'), 'every query stays on the opened grant')
  assert.equal(document.querySelector('.fv-title h2').textContent, 'timeline.json')
})
