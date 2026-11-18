import { domTextExcerpt } from './dom-diagnostics.mjs'
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
    assert.fail(`DOM did not settle${label ? ` (${label})` : ''}: ${domTextExcerpt(document)}`)
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
  assert.equal(label('上一级文件夹').title, '上一级文件夹 · 当前目录：sub')
  assert.equal(document.querySelector('.fv-file-search').title, '筛选当前文件夹 · sub')
  const toolbar = document.querySelector('.fv-file-toolbar')
  assert.equal(Boolean(toolbar.contains(label('刷新文件列表')) && toolbar.contains(label('收起项目文件'))), true)
  assert.equal(Boolean(label('上一级文件夹').getAttribute('aria-describedby')), true) // keyboard-focus help retains cwd
  // filter is client-side only: no new browse call
  const before = calls.length
  await change(document.querySelector('.fv-file-search'), 'inner')
  assert.deepEqual([...document.querySelectorAll('.fv-file-list span')].map(n => n.textContent), ['inner project.json'])
  assert.equal(Boolean(document.body.textContent.includes('没有匹配') === false), true)
  await change(document.querySelector('.fv-file-search'), 'zzz')
  assert.equal(Boolean(document.body.textContent.includes('没有匹配的文件。')), true)
  assert.equal(calls.length, before)
  await change(document.querySelector('.fv-file-search'), '')
  await act(async () => label('打开时间线 inner project.json').click())
  assert.deepEqual(chosen, ['sub/inner project.json'])
  await act(async () => label('上一级文件夹').click())
  await settle(() => document.querySelector('.fv-file-location span').textContent === '当前会话工作区')
  assert.deepEqual(calls, ['', 'sub', ''])
})

test('FileBrowser: refresh and retry preserve the active filter; folder navigation clears it', async t => {
  const { root, settle, change, label } = await mount(t)
  const listings = {
    '': [dir('', 'sub'), file('', 'a.json'), file('', 'b.json')],
    sub: [file('sub', 'inner project.json')],
  }
  const responses = { '': [...listings['']], sub: [...listings.sub] }
  const failNext = { '': false }
  const call = async (_sessionId, payload) => {
    if (failNext[payload.path]) { failNext[payload.path] = false; throw new Error('TRANSPORT_DOWN: 模拟刷新失败') }
    return browseOK(payload.path, responses[payload.path])
  }
  await act(async () => root.render(h(FileBrowser, { sessionId: 'one', tabSignal: new AbortController().signal, call, onChoose: () => {}, onClose: () => {} })))
  await settle(() => document.querySelector('.fv-file-list'))
  await change(document.querySelector('.fv-file-search'), '.json')
  await settle(() => [...document.querySelectorAll('.fv-file-list span')].every(n => n.textContent.includes('.json')) && document.querySelectorAll('.fv-file-list button').length === 2)
  // refresh preserves the filter and keeps filtering the fresh listing
  await act(async () => document.querySelector('[aria-label="刷新文件列表"]').click())
  await settle(() => document.querySelector('.fv-file-search').value === '.json')
  assert.equal(document.querySelectorAll('.fv-file-list button').length, 2, 'filter still applied to the refreshed listing')
  // a failing refresh keeps the filter; the next refresh (retry path, same folder) still preserves it
  failNext[''] = true
  await act(async () => document.querySelector('[aria-label="刷新文件列表"]').click())
  await settle(() => document.body.textContent.includes('TRANSPORT_DOWN'))
  assert.equal(document.querySelector('.fv-file-search').value, '.json', 'failure does not wipe the filter')
  await act(async () => document.querySelector('[aria-label="刷新文件列表"]').click())
  await settle(() => document.querySelector('.fv-file-search').value === '.json' && document.querySelectorAll('.fv-file-list button').length === 2)
  // folder navigation clears the filter (filter to the folder name so its entry is visible)
  await change(document.querySelector('.fv-file-search'), 'sub')
  await settle(() => document.querySelectorAll('.fv-file-list button').length === 1)
  await act(async () => label('打开文件夹 sub').click())
  await settle(() => document.querySelector('.fv-file-location span').textContent === 'sub')
  assert.equal(document.querySelector('.fv-file-search').value, '', 'navigation into a folder clears the filter')
  assert.equal(document.querySelectorAll('.fv-file-list button').length, 1, 'unfiltered listing of the new folder')
  await change(document.querySelector('.fv-file-search'), 'inner')
  await act(async () => label('上一级文件夹').click())
  await settle(() => document.querySelector('.fv-file-location span').textContent === '当前会话工作区')
  assert.equal(document.querySelector('.fv-file-search').value, '', 'navigation up clears the filter')
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
  assert.equal(Boolean(document.body.textContent.includes('仅显示前 500 项')), true)
  assert.equal(Boolean(document.body.textContent.includes('已隐藏指向工作区外的链接。')), true)
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
  assert.equal(Boolean(document.body.textContent.includes('HOST_DOWN')), true)
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
  assert.equal(Boolean(document.body.textContent.includes('good.json')), true)
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
  assert.equal(Boolean(!document.body.textContent.includes('STALE_ENTRY')), true, 'aborted late response did not publish')
  assert.equal(Boolean(document.body.textContent.includes('new.json')), true)
})

// ---------- PreviewBody integrated shell ----------

const version = `sha256:${'a'.repeat(64)}`
const coverage = { complete: true, scope: 'stored', unexpanded: [] }
const snapshot = { content_version: version, coverage, diagnostics: [], document: { project: { name: 'p', duration_frames: 30, fps: 30 } } }
const tracks = [{ family: 'video', track_index: 0, address: '/t/0', data: { name: 'MAIN_TRACK' } }]
const blocks = [{ family: 'video', track_index: 0, block_index: 0, address: '/t/0/b/0', range: [0, 20], data: { src: 's.mp4' } }]
const full = { content_version: version, coverage, diagnostics: [], tracks, blocks }
const opened = { grant: 'grant', snapshot, media: [], capabilities: { capabilities: { frame: { inline_png: { flag: '--include-png' } } } } }

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
    else result = { ok: true }
    return new Response(stringifyJSON(result))
  }
  const getDraftServices = () => ({ active: true, conversation: {}, sessions, uiSession })
  const tabSignal = new AbortController().signal // stable across renders, like the real Host tab
  const useTabInfo = () => ({ tab: { signal: tabSignal } })
  return { requests, forbidden, notify, store, getDraftServices, useTabInfo }
}

for (const observation of ['mixed', 'later', 'saved']) test(`post-edit reads: ${observation} revision pair`, async t => {
  const { root, button, submit, settle, Simulate } = await mount(t)
  const oldFetch = globalThis.fetch
  t.after(() => { globalThis.fetch = oldFetch })
  const managed = { ...snapshot, document: { ...snapshot.document, _vtx: { protocol: 1, head: 'head' } } }
  const editable = { ...full, tracks: [{ ...tracks[0], data: { id: 'track' } }], blocks: [{ ...blocks[0], data: { id: 'clip' } }] }
  const reply = value => new Response(stringifyJSON(value))
  const saved = `sha256:${'b'.repeat(64)}`, newer = `sha256:${'c'.repeat(64)}`
  let committed = false
  const reads = new Map()
  globalThis.fetch = async (_url, init) => {
    const { op } = JSON.parse(init.body)
    if (op === 'open') return reply({ ...opened, snapshot: managed })
    if (op === 'query' && !committed) return reply(editable)
    if (op === 'authorize-write') return reply({ ok: true, writeGrant: 'write' })
    if (op === 'plan') return reply({ ok: true, planToken: 'plan' })
    if (op === 'commit') { committed = true; return reply({ ok: true, revision: saved }) }
    if (op === 'reference') return reply({ content_version: committed ? saved : version, selection: { blocks: [{ address: '/t/0/b/0' }] } })
    if (op === 'inspect' || op === 'query') return new Promise(resolve => reads.set(op, resolve))
    return reply({ ok: true })
  }
  const tab = { signal: new AbortController().signal }
  await act(async () => root.render(h(PreviewBody, { sessionId: 'one', useTabInfo: () => ({ tab }) })))
  await submit(0)
  await settle(() => document.querySelector('.fv-clip'))
  await act(async () => button('启用编辑').click())
  const clip = document.querySelector('.fv-clip')
  document.querySelector('.fv-ruler').getBoundingClientRect = () => ({ left: 0, width: 300 })
  document.querySelector('.fv-track').getBoundingClientRect = () => ({ top: 0, bottom: 50 })
  await act(async () => Simulate.pointerDown(clip, { pointerId: 1, button: 0, clientX: 0, clientY: 10 }))
  await act(async () => Simulate.pointerUp(clip, { pointerId: 1, clientX: 50, clientY: 10, altKey: true }))
  await settle(() => reads.size === 2, 'both reads dispatched while neither has answered')
  const feedback = () => document.querySelector('.fv-drag-feedback')?.textContent
  const observed = observation === 'later' ? newer : saved
  await act(async () => reads.get('query')(reply({ ...editable, content_version: observed,
    blocks: [{ ...editable.blocks[0], range: [5, 25] }] })))
  assert.equal(feedback(), '正在保存…')
  assert.match(clip.getAttribute('aria-label'), /帧 \[0, 20\)/)
  await act(async () => reads.get('inspect')(reply({ ...managed, content_version: observation === 'mixed' ? version : observed })))
  if (observation === 'saved') {
    await settle(() => document.querySelector('.fv-edit-status')?.textContent === '已保存。')
    assert.match(document.querySelector('.fv-clip')?.getAttribute('aria-label') ?? '', /帧 \[5, 25\)/)
  } else {
    await settle(() => document.querySelector('.fv-edit-status')?.textContent.includes('内核已保存，但工程刷新失败'))
    await act(async () => Simulate.submit(document.querySelector('form.fv-frame-query')))
    assert.ok(document.body.textContent.includes('请先刷新保存版本，再查询播放头。'))
    assert.equal(document.querySelectorAll('.fv-clip').length, 0, 'position queries cannot authorize an unconfirmed view')
  }
})

test('unknown drag turns off its timeline warning only after settled recovery and matching readback', async t => {
  const { root, button, submit, settle, Simulate } = await mount(t)
  const oldFetch = globalThis.fetch
  t.after(() => { globalThis.fetch = oldFetch })
  const managed = { ...snapshot, document: { ...snapshot.document, _vtx: { protocol: 1, head: 'before' } } }
  const editable = { ...full, tracks: [{ ...tracks[0], data: { id: 'track' } }], blocks: [{ ...blocks[0], data: { id: 'clip' } }] }
  const savedVersion = `sha256:${'b'.repeat(64)}`
  const saved = { ...managed, content_version: savedVersion, document: { ...managed.document, _vtx: { protocol: 1, head: 'lost-receipt' } } }
  const reply = value => new Response(stringifyJSON(value))
  const calls = []
  let recovering = false, finishInspect
  globalThis.fetch = async (_url, init) => {
    const request = JSON.parse(init.body)
    calls.push(request)
    switch (request.op) {
      case 'open': return reply({ ...opened, snapshot: managed })
      case 'query': return reply(recovering ? { ...editable, content_version: savedVersion, blocks: [{ ...editable.blocks[0], range: [5, 25] }] } : editable)
      case 'inspect': return recovering ? new Promise(resolve => { finishInspect = () => resolve(reply(saved)) }) : reply(managed)
      case 'authorize-write': return reply({ ok: true, writeGrant: 'write' })
      case 'plan': return reply({ ok: true, planToken: 'plan', submitted_request: { op_id: 'lost-receipt' } })
      case 'commit': throw new Error('lost response')
      case 'status': return reply({ ok: true, pending: true, current_revision: version })
      case 'recover': recovering = true; return reply({ ok: true, pending: false, current_revision: savedVersion })
      default: return reply({ ok: true })
    }
  }
  const tab = { signal: new AbortController().signal }
  await act(async () => root.render(h(PreviewBody, { sessionId: 'one', useTabInfo: () => ({ tab }) })))
  await submit(0)
  await settle(() => document.querySelector('.fv-clip'))
  await act(async () => button('启用编辑').click())
  const clip = document.querySelector('.fv-clip')
  document.querySelector('.fv-ruler').getBoundingClientRect = () => ({ left: 0, width: 300 })
  document.querySelector('.fv-track').getBoundingClientRect = () => ({ top: 0, bottom: 50 })
  await act(async () => Simulate.pointerDown(clip, { pointerId: 1, button: 0, clientX: 0, clientY: 10 }))
  await act(async () => Simulate.pointerUp(clip, { pointerId: 1, clientX: 50, clientY: 10, altKey: true }))
  const feedback = () => document.querySelector('.fv-drag-feedback')?.textContent
  const warning = /保存结果未知（lost-receipt），请先读取状态或恢复/
  await settle(() => warning.test(feedback()))
  assert.equal(button('撤销当前版本').disabled, true)
  await act(async () => button('读取状态').click())
  assert.match(feedback(), warning, 'pending status does not dismiss the warning')
  assert.equal(button('撤销当前版本').disabled, true)

  await act(async () => button('恢复').click())
  await settle(() => finishInspect)
  assert.match(feedback(), warning, 'settled store result alone does not dismiss the warning')
  assert.equal(button('撤销当前版本').disabled, true)
  assert.match(clip.getAttribute('aria-label'), /帧 \[0, 20\)/)
  await act(async () => finishInspect())
  await settle(() => !button('撤销当前版本').disabled)
  assert.equal(feedback(), undefined)
  assert.match(document.querySelector('.fv-clip').getAttribute('aria-label'), /帧 \[5, 25\)/)
  assert.match(document.querySelector('.fv-edit-status').textContent, /原操作结果以历史为准/)
  assert.deepEqual(calls.filter(({ op }) => ['plan', 'commit'].includes(op)).map(({ op }) => op), ['plan', 'commit'])
})

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
  assert.equal(Boolean(document.body.textContent.includes('从一条时间线开始')), true)
  assert.equal(Boolean(document.querySelector('.fv-ruler') === null), true)
  assert.equal(document.querySelectorAll('.fv-file-list button').length, 0)
  assert.equal(Boolean(document.querySelector('[role="alert"]') === null), true, "Expected absent DOM node: document.querySelector('[role=\"alert\"]')")
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
  const afterLoad = host.requests.length
  const dialog = document.querySelector('dialog.fv-workbench')
  assert.equal(dialog.open, false)
  assert.equal(dialog.getAttribute('role'), 'region')
  assert.equal(label('展开工作台').getAttribute('aria-expanded'), null)
  const ruler = document.querySelector('.fv-ruler')
  const editorSummary = document.querySelector('[aria-label="时间线编辑"]')
  const monitor = document.querySelector('.fv-monitor')
  assert.equal(document.querySelector('.fv-frame-tools').closest('fieldset').style.display, 'contents', 'storage exclusion must not insert a flex box that shrinks the expanded monitor')
  assert.equal(Boolean(editorSummary), true, 'ManualEditor section rendered')
  await act(async () => label('展开工作台').click())
  assert.deepEqual(dialogCalls, ['showModal'])
  assert.equal(dialog.open, true)
  assert.equal(dialog.getAttribute('role'), 'dialog')
  assert.equal(dialog.getAttribute('aria-modal'), 'true')
  assert.equal(Boolean(document.querySelector('[aria-label="收起工作台"]')), true)
  assert.equal(Boolean(document.querySelector('.fv-ruler') === ruler), true, 'SAME ruler node retained across expand')
  assert.equal(Boolean(document.querySelector('[aria-label="时间线编辑"]') === editorSummary), true, 'SAME ManualEditor DOM node retained')
  assert.equal(Boolean(document.querySelector('.fv-monitor') === monitor), true, 'SAME program monitor retained')
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 5)) })
  assert.equal(host.requests.length, afterLoad, 'expand issues no reopen/query/render IO')
  await act(async () => label('收起工作台').click())
  assert.deepEqual(dialogCalls, ['showModal', 'close'])
  assert.equal(dialog.open, false)
  assert.equal(Boolean(document.querySelector('[aria-label="展开工作台"]')), true)
  assert.equal(dialog.getAttribute('aria-modal'), null)
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 5)) })
  assert.equal(host.requests.length, afterLoad, 'shrink issues no IO either')
  assert.equal(Boolean(document.querySelector('.fv-ruler') === ruler), true, 'shrink also retains the ruler node')
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
  assert.deepEqual(ops.slice(-2), ['open', 'query'])
  assert.equal(host.requests.at(-2).timeline, 'proj.json')
  assert.equal(host.requests.at(-2).sessionId, 'one')
  assert.equal(document.querySelector('.fv-title h2').textContent, 'proj.json')
  const savedView = document.querySelector('.fv-title .fv-subtitle')
  assert.equal(savedView.textContent, '已保存视图')
  assert.equal(savedView.title, '已保存视图 · 精确帧定位')
  assert.equal(Boolean(document.body.textContent.includes('MAIN_TRACK')), true, 'projection content comes from the vtx query envelope')
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
  assert.equal(Boolean(document.body.textContent.includes('已保存视图')), true)
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
  assert.equal(Boolean(document.body.textContent.includes('会话或工作区已切换')), true)
  assert.equal(Boolean(document.querySelector('.fv-ruler') === null), true)
  assert.equal(Boolean(host.requests.slice(loaded).some(r => r.op === 'close' && r.sessionId === 'one')), true, 'grant closed on cwd switch')
  const browseAfterCwd = host.requests.filter(r => r.op === 'browse').length
  assert.equal(Boolean(browseAfterCwd >= 2), true, 'browser remounted for the new workspace epoch and re-browsed root')
  assert.equal(host.requests.filter(r => r.op === 'browse').at(-1).path, '')
  // session switch
  host.store.cwd = { one: '/moved', two: '/other' }
  const beforeSwitch = host.requests.length
  await act(async () => root.render(h(PreviewBody, { ...host, sessionId: 'two', inputActions: {}, useAccess: () => true })))
  // the grant was already closed by the cwd observer; the session switch must not
  // reopen anything on its own — only the idle shell for the new session remains
  await settle(() => document.querySelector('.fv-empty'))
  assert.equal(Boolean(document.querySelector('.fv-ruler') === null), true)
  assert.equal(Boolean(document.querySelector('.fv-timeline-pane') === null), true)
  assert.equal(Boolean(!host.requests.slice(beforeSwitch).some(r => r.op === 'open' || r.op === 'query' || r.op === 'read')), true, 'no auto load on session switch')
})

test('PreviewBody: topbar path disclosure closes with Escape without closing the workbench; submit preserves the ordinary open contract', async t => {
  const { root, dom, label, Simulate, submit, settle } = await mount(t)
  const oldFetch = globalThis.fetch
  t.after(() => { globalThis.fetch = oldFetch })
  const host = hostFixture()
  installDialogAdapter(dom)
  await act(async () => root.render(h(PreviewBody, { ...host, sessionId: 'one' })))
  const menu = document.querySelector('.fv-topbar > .fv-path')
  assert.equal(Boolean(menu), true, 'rare path form belongs to the toolbar, not a permanent monitor row')
  await act(async () => label('展开工作台').click())
  menu.open = true
  await act(async () => Simulate.keyDown(menu.querySelector('input'), { key: 'Escape' }))
  assert.equal(menu.open, false)
  assert.equal(document.querySelector('.fv-workbench').open, true, 'Escape was owned by the disclosure')
  assert.equal(Boolean(document.activeElement === menu.querySelector('summary')), true, "Expected focus on menu.querySelector('summary')")
  assert.equal(host.requests.length, 0, 'opening/closing the disclosure performs no IO')
  menu.open = true
  await submit(0)
  await settle(() => document.querySelector('.fv-ruler'))
  assert.equal(menu.open, false, 'submit reclaims the overlay')
  assert.equal(host.requests.filter(r => r.op === 'open').length, 1)
  await act(async () => label('属性与编辑').click())
  const overview = [...document.querySelectorAll('.fv-inspector details')].find(node => node.querySelector('summary')?.textContent === '工程概览')
  assert.equal(overview !== undefined, true, 'explicit properties opens the saved project overview')
  assert.equal(overview.querySelector('.fv-inspector-overview').textContent.includes('轨道 / 片段1 / 1'), true)
  assert.equal(document.querySelector('.fv-inspector pre') === null, true, 'the compact inspector does not render a raw project dump; exact copy is covered separately')
  assert.equal(Boolean(document.querySelector('.fv-timeline-pane .fv-data') === null), true, 'data details do not consume lane height')
})
