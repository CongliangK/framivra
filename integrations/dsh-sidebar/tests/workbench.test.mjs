import test from 'node:test'
import assert from 'node:assert/strict'
import React, { act } from 'react'
import { JSDOM } from 'jsdom'
import { stringifyJSON } from '../src/json.mjs'
import { FileBrowser } from '../src/client/workbench.mjs'
import { PreviewBody } from '../src/client/index.mjs'

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
  const button = text => [...document.querySelectorAll('button')].find(node => node.textContent === text)
  const label = name => document.querySelector(`[aria-label="${name}"]`)
  const change = async (input, value) => act(async () => Simulate.change(input, { target: { value } }))
  const submit = async index => act(async () => document.querySelectorAll('form')[index].dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true })))
  const settle = async (predicate, label) => {
    for (let i = 0; i < 100; i++) {
      if (predicate()) return
      await act(async () => { await new Promise(resolve => setTimeout(resolve, 1)) })
    }
    assert.fail(`DOM did not settle${label ? ` (${label})` : ''}: ${document.body.textContent.slice(0, 300)}`)
  }
  return { root, dom, button, label, change, submit, settle, Simulate }
}

const browseOK = (path, entries, extra = {}) => ({ schema: 'framivra.browse/v1', path, entries, ...extra })
const dir = (parent, name) => ({ name, type: 'directory', path: parent ? `${parent}/${name}` : name })
const file = (parent, name) => ({ name, type: 'file', path: parent ? `${parent}/${name}` : name })

// ---------- FileBrowser ----------

test('FileBrowser: initial load browses session workspace root, renders folders then files, marks current timeline', async t => {
  const { root, settle, label } = await mount(t)
  const calls = []
  const entries = [file('', 'a.json'), dir('', 'sub'), file('', 'b.json')]
  const call = async (sessionId, payload, signal) => {
    calls.push({ sessionId, payload, aborted: signal.aborted })
    return browseOK(payload.path, entries)
  }
  await act(async () => root.render(h(FileBrowser, { sessionId: 'one', tabSignal: new AbortController().signal, call, selectedPath: 'a.json', onChoose: () => {}, onClose: () => {} })))
  await settle(() => document.querySelector('.fv-file-list'))
  assert.deepEqual(calls, [{ sessionId: 'one', payload: { op: 'browse', path: '' }, aborted: false }])
  const names = [...document.querySelectorAll('.fv-file-list span')].map(node => node.textContent)
  assert.deepEqual(names, ['a.json', 'sub', 'b.json']) // Host order preserved verbatim
  assert.equal(label('打开时间线 a.json').getAttribute('aria-current'), 'true')
  assert.equal(label('打开时间线 b.json').getAttribute('aria-current'), null)
  assert.equal(document.querySelector('.fv-file-location span').textContent, '当前会话工作区')
})

test('FileBrowser: folder navigation and up re-browse with exact relative paths; filter is local; file click passes the workspace-relative path verbatim', async t => {
  const { root, settle, change, label } = await mount(t)
  const calls = []
  const listings = {
    '': [dir('', 'sub'), file('', 'a.json')],
    sub: [file('sub', 'inner project.json'), dir('sub', 'deep')],
  }
  const call = async (_sessionId, payload) => {
    calls.push(payload.path)
    return browseOK(payload.path, listings[payload.path])
  }
  const chosen = []
  await act(async () => root.render(h(FileBrowser, { sessionId: 'one', tabSignal: new AbortController().signal, call, onChoose: path => chosen.push(path), onClose: () => {} })))
  await settle(() => document.querySelector('.fv-file-list'))
  await act(async () => label('打开文件夹 sub').click())
  await settle(() => document.querySelector('.fv-file-location span').textContent === 'sub')
  assert.deepEqual(calls, ['', 'sub'])
  // filter is client-side only: no new browse call
  const before = calls.length
  await change(document.querySelector('.fv-file-search'), 'inner')
  assert.deepEqual([...document.querySelectorAll('.fv-file-list span')].map(n => n.textContent), ['inner project.json'])
  assert(document.body.textContent.includes('没有匹配') === false)
  await change(document.querySelector('.fv-file-search'), 'zzz')
  assert(document.body.textContent.includes('没有匹配的文件。'))
  assert.equal(calls.length, before)
  await change(document.querySelector('.fv-file-search'), '')
  await act(async () => label('打开时间线 inner project.json').click())
  assert.deepEqual(chosen, ['sub/inner project.json'])
  await act(async () => label('上一级文件夹').click())
  await settle(() => document.querySelector('.fv-file-location span').textContent === '当前会话工作区')
  assert.deepEqual(calls, ['', 'sub', ''])
})

test('FileBrowser: loading status, empty folder, truncated and escaped notices come from the Host response only', async t => {
  const { root, settle } = await mount(t)
  const responses = [
    browseOK('', []),
    browseOK('', [file('', 'x.json')], { truncated: true, limit: 500, escaped: true }),
  ]
  const call = async (_s, payload) => {
    assert.equal(payload.op, 'browse')
    return responses.shift()
  }
  await act(async () => root.render(h(FileBrowser, { sessionId: 'one', tabSignal: new AbortController().signal, call, onChoose: () => {}, onClose: () => {} })))
  await settle(() => document.body.textContent.includes('此文件夹没有 JSON 文件或子文件夹。'))
  assert.equal(document.querySelectorAll('.fv-file-list button').length, 0)
  await act(async () => document.querySelector('[aria-label="刷新文件列表"]').click())
  await settle(() => document.body.textContent.includes('目录过大'))
  assert(document.body.textContent.includes('仅显示前 500 项'))
  assert(document.body.textContent.includes('已隐藏指向工作区外的链接。'))
  assert.equal(document.querySelectorAll('.fv-file-list span').length, 1)
})

test('FileBrowser: transport failure shows the message with retry; retry re-browses the same folder', async t => {
  const { root, settle, button } = await mount(t)
  let failures = 1
  const calls = []
  const call = async (_s, payload) => {
    calls.push(payload.path)
    if (failures-- > 0) throw new Error('HOST_DOWN: control unavailable')
    return browseOK(payload.path, [file(payload.path, 'ok.json')])
  }
  await act(async () => root.render(h(FileBrowser, { sessionId: 'one', tabSignal: new AbortController().signal, call, onChoose: () => {}, onClose: () => {} })))
  await settle(() => document.querySelector('[role="alert"]'))
  assert(document.body.textContent.includes('HOST_DOWN'))
  assert.equal(document.querySelectorAll('.fv-file-list button').length, 0)
  await act(async () => button('重试读取文件夹').click())
  await settle(() => document.querySelector('.fv-file-list'))
  assert.deepEqual(calls, ['', ''])
})

test('FileBrowser: malformed Host responses fail closed with BROWSE_INVALID and never render partial entries', async t => {
  const { root, settle, button } = await mount(t)
  const bad = [
    { schema: 'framivra.browse/other', path: '', entries: [] },
    { schema: 'framivra.browse/v1', path: '', entries: 'no' },
    { schema: 'framivra.browse/v1', path: '', entries: [file('', 'a/../escape.json')] },
    { schema: 'framivra.browse/v1', path: '', entries: [{ name: 'mismatch.json', type: 'file', path: 'elsewhere/mismatch.json' }] },
    { schema: 'framivra.browse/v1', path: '', entries: [{ name: 'x', type: 'symlink', path: 'x' }] },
    { schema: 'framivra.browse/v1', path: '', entries: Array.from({ length: 501 }, (_, i) => file('', `f${i}.json`)) },
    { schema: 'framivra.browse/v1', path: 'sub', entries: [] }, // path echo mismatch
  ]
  let served = 0
  const total = bad.length
  const call = async (_s, payload) => { served++; return bad[served - 1] ?? browseOK(payload.path, [file(payload.path, 'good.json')]) }
  await act(async () => root.render(h(FileBrowser, { sessionId: 'one', tabSignal: new AbortController().signal, call, onChoose: () => {}, onClose: () => {} })))
  for (let i = 1; i < total; i++) { // mount consumed case 0; one retry per remaining malformation
    await settle(() => document.querySelector('[role="alert"]') && document.body.textContent.includes('BROWSE_INVALID'))
    assert.equal(document.querySelectorAll('.fv-file-list button').length, 0, `case ${i} renders no list`)
    await act(async () => button('重试读取文件夹').click())
    await settle(() => document.querySelector('[role="alert"]') === null || document.body.textContent.includes('BROWSE_INVALID'))
  }
  // every queued malformation consumed; the next retry receives a valid document
  await act(async () => button('重试读取文件夹').click())
  await settle(() => document.querySelector('.fv-file-list'))
  assert(document.body.textContent.includes('good.json'))
})

test('FileBrowser: switching session aborts the pending browse and its late response can never publish; a new root load starts clean', async t => {
  const { root, settle } = await mount(t)
  const pending = []
  let session = 'one'
  const call = (_sessionId, payload, signal) => new Promise((resolve, reject) => {
    const entry = { payload, signal, resolve, reject, session: _sessionId }
    pending.push(entry)
    if (_sessionId !== 'one') resolve(browseOK(payload.path, [file('', 'new.json')]))
  })
  const render = () => act(async () => root.render(h(FileBrowser, { sessionId: session, tabSignal: new AbortController().signal, call, onChoose: () => {}, onClose: () => {} })))
  await render()
  await settle(() => pending.length === 1)
  assert.equal(pending[0].session, 'one')
  session = 'two'
  await render()
  await settle(() => document.querySelector('.fv-file-list'))
  assert.equal(pending[0].signal.aborted, true, 'old browse aborted on session switch')
  await act(async () => pending[0].resolve(browseOK('', [file('', 'STALE_ENTRY.json')])))
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 5)) })
  assert(!document.body.textContent.includes('STALE_ENTRY'), 'aborted late response did not publish')
  assert(document.body.textContent.includes('new.json'))
})

// ---------- PreviewBody integrated shell ----------

const version = `sha256:${'a'.repeat(64)}`
const coverage = { complete: true, scope: 'stored', unexpanded: [] }
const snapshot = { content_version: version, coverage, diagnostics: [], document: { project: { name: 'p', duration_frames: 30, fps: 30 } } }
const tracks = [{ family: 'video', track_index: 0, address: '/t/0', data: { name: 'MAIN_TRACK' } }]
const blocks = [{ family: 'video', track_index: 0, block_index: 0, address: '/t/0/b/0', range: [0, 20], data: { src: 's.mp4' } }]
const full = { content_version: version, coverage, diagnostics: [], tracks, blocks }
const opened = { grant: 'grant', snapshot, media: [] }

function hostFixture() {
  const requests = [], forbidden = []
  const store = { cwd: { one: '/w', two: '/other' }, key: 'one' }
  const observers = new Set()
  const sessions = {
    binding: id => ({ sessionId: id }),
    list: { getSnapshot: () => ({ byId: store.cwd }), subscribe: fn => { observers.add(fn); return () => observers.delete(fn) } },
  }
  const uiSession = { adapter: { current: { getSnapshot: () => ({ key: store.key }), subscribe: fn => { observers.add(fn); return () => observers.delete(fn) } } } }
  const notify = () => { for (const fn of observers) fn() }
  globalThis.fetch = async (_url, init) => {
    const request = JSON.parse(init.body)
    requests.push(request)
    let result
    if (request.op === 'open') result = opened
    else if (request.op === 'query') result = full
    else if (request.op === 'browse') result = browseOK(request.path, request.path === '' ? [dir('', 'sub'), file('', 'timeline.json'), file('', 'proj.json')] : [file(request.path, 'nested.json')])
    else if (request.op === 'proposal-list') result = { ok: true, proposals: [] }
    else result = { ok: true }
    return new Response(stringifyJSON(result))
  }
  const getDraftServices = () => ({ active: true, conversation: {}, sessions, uiSession })
  const tabSignal = new AbortController().signal // stable across renders, like the real Host tab
  const useTabInfo = () => ({ tab: { signal: tabSignal } })
  return { requests, forbidden, notify, store, getDraftServices, useTabInfo }
}

function installDialogAdapter(dom) {
  const calls = []
  const proto = dom.window.HTMLDialogElement.prototype
  proto.showModal = function () { calls.push('showModal'); this.setAttribute('open', '') }
  proto.close = function () { calls.push('close'); this.removeAttribute('open'); this.dispatchEvent(new dom.window.Event('close')) }
  return calls
}

test('PreviewBody: initial render performs no browse/open/query/write — only the idle shell', async t => {
  const { root, settle } = await mount(t)
  const oldFetch = globalThis.fetch
  t.after(() => { globalThis.fetch = oldFetch })
  const requests = []
  globalThis.fetch = async (_url, init) => { requests.push(JSON.parse(init.body)); return new Response(stringifyJSON({ ok: true })) }
  const host = { getDraftServices: () => ({}), useTabInfo: () => ({ tab: { signal: new AbortController().signal } }) }
  await act(async () => root.render(h(PreviewBody, { ...host, sessionId: 'one' })))
  await settle(() => document.querySelector('.fv-empty'))
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)) })
  assert.deepEqual(requests, [], 'no auto browse/render/write on mount')
  assert(document.body.textContent.includes('从一条时间线开始'))
  assert(document.querySelector('.fv-ruler') === null)
  assert.equal(document.querySelectorAll('.fv-file-list button').length, 0)
  assert.equal(document.querySelector('[role="alert"]'), null)
})

test('PreviewBody: expand/shrink drives the native dialog via public showModal/close; ruler and editor DOM nodes are retained, no reopened/read side effects', async t => {
  const { root, dom, settle, label } = await mount(t)
  const dialogCalls = installDialogAdapter(dom)
  const host = hostFixture()
  const oldFetch = globalThis.fetch
  t.after(() => { globalThis.fetch = oldFetch })
  await act(async () => root.render(h(PreviewBody, { ...host, sessionId: 'one', inputActions: {}, useAccess: () => true })))
  await act(async () => document.querySelectorAll('form')[0].dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true })))
  await settle(() => document.querySelector('.fv-ruler'))
  await settle(() => host.requests.some(r => r.op === 'proposal-list'))
  const afterLoad = host.requests.length
  const dialog = document.querySelector('dialog.fv-workbench')
  assert.equal(dialog.open, false)
  assert.equal(dialog.getAttribute('role'), 'region')
  assert.equal(label('展开工作台').getAttribute('aria-expanded'), null)
  const ruler = document.querySelector('.fv-ruler')
  const editorSummary = [...document.querySelectorAll('.fv-inspector summary')].find(n => n.textContent === '剪辑编辑与提交')
  const monitor = document.querySelector('.fv-monitor')
  assert(editorSummary, 'ManualEditor section rendered')
  await act(async () => label('展开工作台').click())
  assert.deepEqual(dialogCalls, ['showModal'])
  assert.equal(dialog.open, true)
  assert.equal(dialog.getAttribute('role'), 'dialog')
  assert.equal(dialog.getAttribute('aria-modal'), 'true')
  assert(document.querySelector('[aria-label="收起工作台"]'))
  assert.strictEqual(document.querySelector('.fv-ruler'), ruler, 'SAME ruler node retained across expand')
  assert.strictEqual([...document.querySelectorAll('.fv-inspector summary')].find(n => n.textContent === '剪辑编辑与提交'), editorSummary, 'SAME ManualEditor DOM node retained')
  assert.strictEqual(document.querySelector('.fv-monitor'), monitor)
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 5)) })
  assert.equal(host.requests.length, afterLoad, 'expand issues no reopen/query/render IO')
  await act(async () => label('收起工作台').click())
  assert.deepEqual(dialogCalls, ['showModal', 'close'])
  assert.equal(dialog.open, false)
  assert(document.querySelector('[aria-label="展开工作台"]'))
  assert.equal(dialog.getAttribute('aria-modal'), null)
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 5)) })
  assert.equal(host.requests.length, afterLoad, 'shrink issues no IO either')
  assert.strictEqual(document.querySelector('.fv-ruler'), ruler, 'shrink also retains the ruler node')
})

test('PreviewBody: choosing a browser file opens through the Host control response and vtx projection, not a second parser', async t => {
  const { root, dom, settle, label } = await mount(t)
  const dialogCalls = installDialogAdapter(dom)
  const host = hostFixture()
  const oldFetch = globalThis.fetch
  t.after(() => { globalThis.fetch = oldFetch })
  await act(async () => root.render(h(PreviewBody, { ...host, sessionId: 'one', inputActions: {}, useAccess: () => true })))
  await settle(() => document.querySelector('.fv-empty'))
  await act(async () => label('浏览工作区打开时间线').click())
  await settle(() => document.querySelector('.fv-file-list'))
  assert.deepEqual(host.requests.map(r => r.op).slice(-1), ['browse'])
  await act(async () => label('打开时间线 proj.json').click())
  await settle(() => document.querySelector('.fv-ruler'))
  const ops = host.requests.map(r => r.op)
  assert.deepEqual(ops.slice(-3), ['open', 'query', 'proposal-list'])
  assert.equal(host.requests.at(-3).timeline, 'proj.json')
  assert.equal(host.requests.at(-3).sessionId, 'one')
  assert.equal(document.querySelector('.fv-title h2').textContent, 'proj.json')
  assert(document.body.textContent.includes('已保存视图 · 精确帧定位'))
  assert(document.body.textContent.includes('MAIN_TRACK'), 'projection content comes from the vtx query envelope')
  assert.equal(label('打开时间线 proj.json').getAttribute('aria-current'), 'true')
  assert.deepEqual(dialogCalls, [], 'file choice itself never toggles the dialog')
})

test('PreviewBody: editing the draft path input never relabels the loaded file until a new open succeeds', async t => {
  const { root, dom, settle, change } = await mount(t)
  const host = hostFixture()
  const oldFetch = globalThis.fetch
  t.after(() => { globalThis.fetch = oldFetch })
  await act(async () => root.render(h(PreviewBody, { ...host, sessionId: 'one', inputActions: {}, useAccess: () => true })))
  await act(async () => document.querySelectorAll('form')[0].dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true })))
  await settle(() => document.querySelector('.fv-ruler'))
  const afterLoad = host.requests.length
  await change(document.querySelector('input[name="framivra-timeline"]'), 'zzz-typo.json')
  assert.equal(document.querySelector('.fv-title h2').textContent, 'timeline.json', 'title keeps the LOADED file, not the draft input')
  assert.equal(document.querySelector('.fv-title h2').getAttribute('title'), 'timeline.json')
  assert(document.body.textContent.includes('已保存视图'))
  assert.equal(host.requests.length, afterLoad, 'typing issues no IO')
})

test('PreviewBody: session switch closes the grant and clears the shell; a cwd switch clears bound data, reports, and remounts the listing', async t => {
  const { root, dom, settle, label } = await mount(t)
  installDialogAdapter(dom)
  const host = hostFixture()
  const oldFetch = globalThis.fetch
  t.after(() => { globalThis.fetch = oldFetch })
  await act(async () => root.render(h(PreviewBody, { ...host, sessionId: 'one', inputActions: {}, useAccess: () => true })))
  await act(async () => label('浏览工作区打开时间线').click())
  await settle(() => document.querySelector('.fv-file-list'))
  await act(async () => label('打开时间线 timeline.json').click())
  await settle(() => document.querySelector('.fv-ruler'))
  const loaded = host.requests.length
  // cwd switch through the official sessions.list subscription
  host.store.cwd = { one: '/moved' }
  await act(async () => host.notify())
  await settle(() => document.querySelector('.fv-empty'))
  assert(document.body.textContent.includes('会话或工作区已切换'))
  assert(document.querySelector('.fv-ruler') === null)
  assert(host.requests.slice(loaded).some(r => r.op === 'close' && r.sessionId === 'one'), 'grant closed on cwd switch')
  const browseAfterCwd = host.requests.filter(r => r.op === 'browse').length
  assert(browseAfterCwd >= 2, 'browser remounted for the new workspace epoch and re-browsed root')
  assert.equal(host.requests.filter(r => r.op === 'browse').at(-1).path, '')
  // session switch
  host.store.cwd = { one: '/moved', two: '/other' }
  const beforeSwitch = host.requests.length
  await act(async () => root.render(h(PreviewBody, { ...host, sessionId: 'two', inputActions: {}, useAccess: () => true })))
  // the grant was already closed by the cwd observer; the session switch must not
  // reopen anything on its own — only the idle shell for the new session remains
  await settle(() => document.querySelector('.fv-empty'))
  assert(document.querySelector('.fv-ruler') === null)
  assert(document.querySelector('.fv-timeline-pane') === null)
  assert(!host.requests.slice(beforeSwitch).some(r => r.op === 'open' || r.op === 'query' || r.op === 'read'), 'no auto load on session switch')
})
