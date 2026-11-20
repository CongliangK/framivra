import { domTextExcerpt } from './dom-diagnostics.mjs'
import test from 'node:test'
import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import React, { act } from 'react'
import { JSDOM } from 'jsdom'
import { stringifyJSON } from '../src/json.mjs'
import { PreviewBody, ReferenceTools, TimelineView } from '../src/client/index.mjs'

const h = React.createElement
const version = `sha256:${'a'.repeat(64)}`
const coverage = { complete: false, scope: 'stored', unexpanded: [{ address: '/subtitles_src', reference: 'external.srt' }] }
const snapshot = { content_version: version, coverage, diagnostics: [], document: { project: { name: '超长项目'.repeat(80), duration_frames: 100, fps: 30 }, extension: JSON.rawJSON('9007199254740993') } }
const tracks = ['video', 'video', 'audio', 'subtitles'].map((family, i) => ({ family, track_index: i, address: `/t/${i}`, data: { name: '超长轨道'.repeat(80), kind: i === 1 ? 'gfx' : 'video' } }))
const blocks = tracks.flatMap((track, i) => (i === 2 ? [[0, 70], [10, 40], [20, 50]] : [[0, 20], [60, 100]]).map((range, block_index) => ({ family: track.family, track_index: track.track_index, block_index, address: `${track.address}/b/${block_index}`, range, data: { src: `source-${i}-${block_index}${'长'.repeat(200)}`, unknown: 'preserve me' } })))
const full = { content_version: version, coverage, diagnostics: [], tracks, blocks }
const filtered = { ...full, tracks: [tracks[0]], blocks: [blocks[0]] } // Fixed envelope adapter, not a second query implementation.

async function mount(t) {
  const dom = new JSDOM('<div id="root"></div>', { url: 'http://owner/' })
  globalThis.window = dom.window
  globalThis.document = dom.window.document
  globalThis.IS_REACT_ACT_ENVIRONMENT = true
  const { createRoot } = await import('react-dom/client')
  const { Simulate } = await import('react-dom/test-utils')
  const root = createRoot(document.getElementById('root'))
  t.after(async () => { await act(async () => root.unmount()); dom.window.close(); delete globalThis.window; delete globalThis.document })
  const button = text => [...document.querySelectorAll('button')].find(node => node.textContent === text || node.getAttribute('aria-label') === text)
  const change = async (input, value) => act(async () => Simulate.change(input, { target: { value } }))
  const submit = async index => act(async () => document.querySelectorAll('form')[index].dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true })))
  return { root, dom, button, change, submit, Simulate }
}

async function settle(predicate) {
  for (let i = 0; i < 100; i++) {
    if (predicate()) return
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 1)) })
  }
  assert.fail(`DOM did not settle: ${domTextExcerpt(document)}`)
}

test('actual React DOM: full projection persists, hover is local, ruler/keys set canonical input and selection references stay independent of the playhead', async t => {
  const { root, dom, button, change, submit, Simulate } = await mount(t)
  const requests = [], copies = [], order = [], inserts = [], forbidden = []
  const oldFetch = globalThis.fetch, oldNavigator = Object.getOwnPropertyDescriptor(globalThis, 'navigator')
  t.after(() => { globalThis.fetch = oldFetch; if (oldNavigator) Object.defineProperty(globalThis, 'navigator', oldNavigator) })
  Object.defineProperty(globalThis, 'navigator', { configurable: true, value: { clipboard: { writeText: async text => copies.push(text) } } })
  let nextVersion = version
  globalThis.fetch = async (_url, init) => {
    const request = JSON.parse(init.body); requests.push(request); order.push(request.op)
    let result
    if (request.op === 'open') result = { grant: 'grant', snapshot, media: [], capabilities: { capabilities: { frame: { inline_png: { flag: '--include-png' } } } } }
    else if (request.op === 'query') result = request.query.frame === undefined ? full : { ...filtered, content_version: nextVersion }
    else if (request.op === 'reference') {
      const kind = request.query.block_addresses ? 'blocks' : 'tracks'
      const address = (request.query.block_addresses ?? request.query.track_addresses)[0]
      result = { content_version: version, selection: { [kind]: [{ address, stable_id: address.endsWith('/0') ? 'stable-id' : null, range: [10, 40], raw: '{ "unsafe":9007199254740993 }' }] } }
    } else result = { ok: true }
    return new Response(stringifyJSON(result))
  }
  const tab = new AbortController()
  // Official public seams only: SessionBinding (ctx + session lifecycle + imageLimits
  // projection), sessions.list, uiSession.adapter.current.key, real InputActions on the
  // inputActions prop, conversation.input.for / resolveDraftAttachments. No host shim.
  const PRE = 'SENTINEL_KEEP ', POST = '[[ref-chip]] tail'
  const draft = { text: PRE + POST, rev: 3, phase: 'plain', ids: [] }
  const inputActions = {
    captureInsertion: () => { order.push('capture'); return { start: PRE.length, end: PRE.length, draftRev: draft.rev } },
    insertText: (text, span) => {
      if (span.draftRev !== draft.rev) return false
      order.push('insert'); inserts.push({ text, span })
      draft.text = draft.text.slice(0, span.start) + text + draft.text.slice(span.end)
      draft.rev += 1
      return true
    },
    addAttachments: ids => { draft.ids = [...draft.ids, ...ids]; return true },
    removeAttachment: () => { forbidden.push('removeAttachment') },
    setDraft: () => { forbidden.push('setDraft') },
    persistDraft: () => { forbidden.push('persistDraft') },
    submit: () => { forbidden.push('submit') },
  }
  const lifecycle = { getSnapshot: () => ({ removed: false, openState: 'open' }) }
  const limits = { getSnapshot: () => ({ maxImageBytes: 1 << 20, maxImagesPerMessage: 2, maxMessageImageBytes: 2 << 20, mediaTypes: ['image/png'] }) }
  const binding = { sessionId: 'one', ctx: { officialScope: true }, session: { getSnapshot: lifecycle.getSnapshot, projections: { faceOf: name => name === 'imageLimits' ? limits : undefined } } }
  const inputState = { getSnapshot: () => ({ draft: draft.text, attachmentIds: [...draft.ids], draftRev: draft.rev, phase: draft.phase }) }
  const conversation = {
    input: { for: ctx => { if (ctx !== binding.ctx) throw new Error('conversation.input.for requires a retained Session scope'); return { state: inputState } } },
    resolveDraftAttachments: ids => ids.map(id => ({ id, kind: 'image' })),
    releaseDraftAttachment: () => { forbidden.push('releaseDraftAttachment') },
    createDrafts: () => { forbidden.push('createDrafts') },
    sendSession: () => { forbidden.push('sendSession') },
  }
  const sessions = { binding: id => id === 'one' ? binding : undefined, list: { getSnapshot: () => ({ byId: { one: { cwd: '/w' } } }), subscribe: () => () => {} } }
  const uiSession = { adapter: { current: { getSnapshot: () => ({ key: 'one' }), subscribe: () => () => {} } } }
  const useTabInfo = () => ({ tab: { signal: tab.signal } })
  await act(async () => root.render(h(PreviewBody, { useTabInfo, sessionId: 'one', getDraftServices: () => ({ active: true, conversation, sessions, uiSession }), inputActions, useAccess: () => true })))
  await submit(0)
  await settle(() => document.querySelector('.fv-ruler'))
  assert.deepEqual(requests.map(x => x.op), ['open', 'query'])
  assert.equal(document.querySelectorAll('.fv-clip').length, blocks.length)
  assert.equal(document.querySelectorAll('.fv-track').length, tracks.length)
  assert.equal(document.querySelectorAll('.fv-gfx').length, 2, 'gfx track retains its semantic clip type')
  assert.equal(document.querySelectorAll('[aria-label^="显示子行"]').length, 6) // video + gfx + 3 audio + subtitle
  const input = document.querySelector('input[inputmode="numeric"]')
  const ruler = document.querySelector('.fv-ruler')
  ruler.getBoundingClientRect = () => ({ left: 10, width: 200 }) // Geometry adapter; not a browser layout claim.
  const beforeHover = requests.length
  await act(async () => Simulate.pointerMove(ruler, { clientX: 110, pointerId: 1 }))
  assert(document.body.textContent.includes('悬停时间码：00:01:20'))
  assert.equal(input.value, '')
  assert.equal(requests.length, beforeHover)
  await act(async () => Simulate.pointerDown(ruler, { button: 0, clientX: 110, pointerId: 1 }))
  assert.equal(input.value, '50')
  await act(async () => Simulate.pointerMove(ruler, { clientX: 150, pointerId: 1 }))
  await act(async () => Simulate.pointerUp(ruler, { clientX: 150, pointerId: 1 }))
  assert.equal(input.value, '70')
  await act(async () => Simulate.keyDown(ruler, { key: 'ArrowLeft', preventDefault() {} }))
  assert.equal(input.value, '69')
  await act(async () => button('后一帧').click())
  assert.equal(input.value, '70')
  assert.equal(requests.length, beforeHover)
  await change(input, '10')
  await submit(1)
  await settle(() => document.querySelector('[data-hit="true"]'))
  assert.equal(requests.at(-1).query.frame, 10)
  assert.equal(document.querySelectorAll('.fv-clip').length, blocks.length)
  assert.equal(document.querySelectorAll('[data-hit="true"]').length, 1)
  const audioButtons = [...document.querySelectorAll('.fv-audio')]
  await act(async () => audioButtons[0].click())
  await settle(() => document.body.textContent.includes('引用就绪。'))
  await act(async () => audioButtons[1].dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true, ctrlKey: true })))
  await settle(() => requests.at(-1)?.query?.block_addresses?.[0] === audioButtons[1].getAttribute('aria-label').split(' · ')[0].slice(4))
  assert.equal(document.querySelectorAll('.fv-clip[aria-pressed="true"]').length, 2)
  assert.equal(requests.at(-1).query.block_addresses[0], '/t/2/b/1')
  assert.equal(document.querySelector('[aria-label="选中对象 raw JSON"]'), null, 'raw is available only through explicit copy, never rendered')
  await act(async () => button('复制 raw JSON').click())
  await settle(() => copies.length === 1)
  assert.equal(copies[0], '{ "unsafe":9007199254740993 }')
  await change(input, '71')
  const beforeDraft = requests.length
  await act(async () => button('加入当前会话草稿（不发送）').click())
  await settle(() => document.body.textContent.includes('已加入会话草稿。'))
  assert.deepEqual(order.slice(-3), ['capture', 'reference', 'insert'])
  assert.deepEqual(requests[beforeDraft].query, { block_addresses: ['/t/2/b/1'], expected_content_version: version }, 'stored reference is not scoped by the playhead')
  assert.equal(inserts.length, 1)
  const { text: insertedText, span } = inserts[0]
  assert(insertedText.length <= 300, 'even a very long source name cannot bloat the draft')
  assert.match(insertedText, /timeline=«timeline\.json»/)
  assert.match(insertedText, /address=«\/t\/2\/b\/1».*range=\[10,40\)/)
  assert(!insertedText.includes('unsafe'), 'raw JSON was not included in the draft')
  assert.ok(Object.isFrozen(span), 'official captured span is frozen')
  assert.equal(span.end, span.start, 'captured span is zero-width at the collapsed caret')
  assert.deepEqual(span, { start: PRE.length, end: PRE.length, draftRev: 3 }, 'span keeps the click-time caret location and the ORIGINAL draftRev, not the post-insert rev')
  assert.equal(draft.text, PRE + insertedText + POST, 'sentinel and selected text/chips retained; insert at the captured caret, no replacement')
  assert.equal(inserts.length, 1)
  assert.deepEqual(draft.ids, [], 'text-only reference adds no attachments')
  assert.deepEqual(forbidden, [], 'no setDraft/persistDraft/submit/removeAttachment and no send/upload/createDrafts surfaces touched')
  await change(input, '1.0')
  const beforeInvalid = requests.length
  await submit(1)
  assert.equal(requests.length, beforeInvalid)
  assert.equal(input.value, '1.0')
  assert(button('复制 raw JSON'), 'invalid playhead does not revoke a stored selection reference')
  assert(document.body.textContent.includes('不会暗用旧帧'))
  assert.equal(document.querySelectorAll('.fv-clip').length, blocks.length)
  await act(async () => Simulate.keyDown(ruler, { key: 'Home', preventDefault() {} }))
  assert.equal(input.value, '0')
  assert(button('复制 raw JSON'), 'stored reference remains usable after playhead reset')
  await act(async () => button('放大').click())
  assert(document.querySelector('[aria-label="可见帧范围"]').textContent.includes('[00:00:25, 00:02:15)'))
  await act(async () => button('向后平移').click())
  assert(document.querySelector('[aria-label="可见帧范围"]').textContent.includes('[00:01:20, 00:03:10)'))
  await act(async () => button('适配全片').click())
  assert.equal(document.querySelectorAll('.fv-clip').length, blocks.length)
  assert(document.body.textContent.includes('external.srt'))
  assert(!document.body.textContent.includes('9007199254740993'), 'raw snapshot JSON is not rendered')
  assert.equal(copies[0], '{ "unsafe":9007199254740993 }', 'exact integer remains available through Clipboard')
  assert(document.querySelector('video') === null, 'no native video in live mode')
  await act(async () => button('成片渲染').click())
  assert(document.body.textContent.includes('暂无成片；不会自动启动整片渲染。'))
  await act(async () => button('固定帧预览').click())
  nextVersion = `sha256:${'b'.repeat(64)}`
  await submit(1)
  await settle(() => document.body.textContent.includes('旧块已隐藏'))
  assert(document.querySelector('.fv-clip') === null, "Expected absent DOM node: document.querySelector('.fv-clip')")
  assert(button('复制 raw JSON') === undefined, "Expected absent DOM node: button('复制 raw JSON')")
  await act(async () => root.render(h(PreviewBody, { useTabInfo, sessionId: 'two' })))
  assert(document.querySelector('.fv-timeline') === null, "Expected absent DOM node: document.querySelector('.fv-timeline')")
  assert.equal(requests.at(-1).op, 'close')
  assert.equal(requests.at(-1).sessionId, 'one')
  assert(requests.every(x => ['open', 'query', 'reference', 'close'].includes(x.op)))
})

test('actual React DOM: references abort on selection/session/unmount, not playhead changes; late reads never publish or copy old raw', async t => {
  const { root, button } = await mount(t)
  const oldFetch = globalThis.fetch, oldNavigator = Object.getOwnPropertyDescriptor(globalThis, 'navigator')
  t.after(() => { globalThis.fetch = oldFetch; if (oldNavigator) Object.defineProperty(globalThis, 'navigator', oldNavigator) })
  const reads = [], copies = []
  Object.defineProperty(globalThis, 'navigator', { configurable: true, value: { clipboard: { writeText: async text => copies.push(text) } } })
  globalThis.fetch = async (_url, init) => new Promise(resolve => {
    const request = JSON.parse(init.body)
    reads.push({ request, signal: init.signal, finish: raw => resolve(new Response(stringifyJSON({ content_version: version, selection: { blocks: [{ address: request.query.block_addresses[0], stable_id: null, raw }] } }))) })
  })
  const signal = new AbortController().signal
  const opened = { grant: 'grant', snapshot }
  const a = { kind: 'block', address: '/a' }, b = { kind: 'block', address: '/b' }
  const render = (selection, frame, sessionId = 'one') => root.render(h(ReferenceTools, { selection, frame, sessionId, opened, tabSignal: signal, onStale: () => assert.fail('unexpected stale') }))
  await act(async () => render(a, 1))
  assert.equal(reads.length, 1)
  await act(async () => render(b, 1))
  assert.equal(reads[0].signal.aborted, true)
  assert.equal(reads.length, 2)
  await act(async () => reads[0].finish('OLD_A'))
  assert(!document.body.textContent.includes('OLD_A'))
  assert(button('复制 raw JSON').disabled, 'late A cannot unlock pending B actions')
  await act(async () => render(b, 2))
  assert.equal(reads[1].signal.aborted, false, 'stored references are independent of playhead changes')
  assert.equal(reads.length, 2, 'moving playhead neither aborts nor implicitly re-queries')
  await act(async () => render(b, 2, 'two'))
  assert.equal(reads[1].signal.aborted, true, 'session change aborts the retained B read')
  assert.equal(reads.length, 3)
  assert.equal(reads[2].request.sessionId, 'two')
  assert.deepEqual(reads[2].request.query, { block_addresses: ['/b'], expected_content_version: version })
  await act(async () => reads[1].finish('OLD_B'))
  assert(button('复制 raw JSON').disabled, 'late prior-session B cannot unlock current actions')
  await act(async () => reads[2].finish('CURRENT_B'))
  assert(document.body.textContent.includes('引用就绪。'))
  assert(!document.body.textContent.includes('CURRENT_B'), 'raw is never rendered')
  assert.deepEqual(copies, [], 'background reads never copy')
  // Missing public composer cannot accidentally append or issue IO; copying remains enabled.
  assert.equal(button('复制 raw JSON').disabled, false)
  await act(async () => button('加入当前会话草稿（不发送）').click())
  assert(document.body.textContent.includes('请先打开一个已有会话，再加入草稿。'))
  assert.equal(reads.length, 3)
  await act(async () => button('复制 raw JSON').click())
  assert.equal(reads.length, 4)
  await act(async () => reads[3].finish('CURRENT_B'))
  assert.deepEqual(copies, ['CURRENT_B'], 'only an explicit fresh action copies the exact current raw')
  await act(async () => button('复制 raw JSON').click())
  assert.equal(reads.length, 5)
  await act(async () => root.render(null))
  assert.equal(reads[4].signal.aborted, true)
  await act(async () => reads[4].finish('AFTER_UNMOUNT'))
  assert(document.body.textContent === '', 'body remains empty after unmount and a late response')
  assert.deepEqual(copies, ['CURRENT_B'], 'late unmounted copy cannot publish to Clipboard')
})

test('window recovery remains available without a valid view and only closes/cancels the retired grant', async t => {
  const { root, button, submit } = await mount(t)
  const oldFetch = globalThis.fetch, tab = new AbortController()
  t.after(() => { globalThis.fetch = oldFetch })
  const windowCap = { schema: 'framivra.timeline.preview-window/v1', inline_media: true, prepare_inputs: true }
  const opened = grant => ({ grant, snapshot, media: [], host: { ops: ['preview-window'] }, capabilities: { capabilities: { preview_window: windowCap } } })
  const calls = []
  let opens = 0, cancels = 0, finishOpen
  const pendingOpen = new Promise(resolve => { finishOpen = resolve })
  globalThis.fetch = async (path, init) => {
    const request = JSON.parse(init.body)
    calls.push({ path, request })
    if (request.op === 'open') return Response.json(++opens === 1 ? opened('old') : await pendingOpen)
    if (request.op === 'query') return new Response(stringifyJSON(full))
    if (request.op === 'close') return Response.json({ ok: true })
    if (request.op === 'cancel') return ++cancels === 1 ? Response.json({ ok: false }, { status: 502 }) : Response.json({ ok: true, reaped: true })
    assert.equal(request.op, 'render')
    return Response.json({ ok: false, error: 'BAD_GRANT' }, { status: 403 })
  }
  const useTabInfo = () => ({ tab: { signal: tab.signal } })
  const click = label => act(async () => button(label).click())
  const configure = async () => {
    await click('音画窗口')
    for (const input of document.querySelectorAll('[aria-label="单窗口音画监看"] input')) await act(async () => input.click())
  }
  await act(async () => root.render(h(PreviewBody, { useTabInfo, sessionId: 'one' })))
  await submit(0)
  await settle(() => button('音画窗口'))
  await configure(); await click('生成当前音画窗口')
  await settle(() => !button('音画窗口') && button('重试回收音画窗口'))
  const initial = calls.find(call => call.request.op === 'render').request
  await click('刷新保存版本')
  assert.deepEqual(calls.filter(call => call.request.op === 'close').map(call => call.request.grant), ['old'], 'failed cleanup does not delay revoking old grant')
  assert.equal(button('音画窗口'), undefined, 'replacement is still loading')
  await click('重试回收音画窗口')
  assert.deepEqual(calls.filter(call => call.request.op === 'cancel'), Array.from({ length: 2 }, () => ({ path: '/api/framivra/window',
    request: { sessionId: 'one', grant: 'old', op: 'cancel', operationId: initial.operationId } })))
  await act(async () => finishOpen(opened('new')))
  await settle(() => button('音画窗口'))
  await configure(); await click('生成当前音画窗口')
  const newRender = calls.filter(call => call.request.op === 'render').at(-1).request
  assert.equal(newRender.grant, 'new')
  assert.notEqual(newRender.operationId, initial.operationId)
  await act(async () => tab.abort())
})

test('undo waits for the one in-flight manual window cancellation', async t => {
  const { root, button, submit, change } = await mount(t)
  const oldFetch = globalThis.fetch, tab = new AbortController()
  t.after(() => { globalThis.fetch = oldFetch })
  const revised = `sha256:${'b'.repeat(64)}`
  const managed = { ...snapshot, document: { ...snapshot.document, _vtx: { protocol: 1, head: 'saved-head' } } }
  const opened = { grant: 'open', snapshot: managed, media: [], host: { ops: ['preview-window'] }, capabilities: { capabilities: {
    preview_window: { schema: 'framivra.timeline.preview-window/v1', inline_media: true, prepare_inputs: true },
  } } }
  const calls = []
  let acknowledge, renderSignal, saved = version
  globalThis.fetch = async (_url, init) => {
    const request = JSON.parse(init.body)
    calls.push(request)
    switch (request.op) {
    case 'open': return Response.json(opened)
    case 'query': return Response.json({ ...full, content_version: saved })
    case 'inspect': return Response.json({ ...managed, content_version: saved })
    case 'authorize-write': return Response.json({ ok: true, writeGrant: 'write' })
    case 'render':
      renderSignal = init.signal
      return new Promise(resolve => renderSignal.addEventListener('abort', () => resolve(new Response()), { once: true }))
    case 'cancel': return new Promise(resolve => { acknowledge = () => resolve(Response.json({ ok: true, reaped: true })) })
    case 'undo':
      saved = revised
      return Response.json({ ok: true, revision: revised })
    case 'close': return Response.json({ ok: true })
    default: assert.fail(`unexpected request: ${request.op}`)
    }
  }
  const click = label => act(async () => {
    const action = button(label)
    assert(action && !action.disabled, `expected enabled action: ${label}`)
    action.click()
  })
  await act(async () => root.render(h(PreviewBody, { useTabInfo: () => ({ tab: { signal: tab.signal } }), sessionId: 'one' })))
  await submit(0)
  await settle(() => button('音画窗口'))
  await change(document.querySelector('input[inputmode="numeric"]'), '5')
  await click('启用编辑')
  await click('音画窗口')
  for (const input of document.querySelectorAll('[aria-label="单窗口音画监看"] input')) await act(async () => input.click())
  await click('生成当前音画窗口')
  await settle(() => renderSignal)
  await click('撤销当前版本')
  await settle(() => acknowledge)
  const render = calls.find(({ op }) => op === 'render')
  assert.equal(renderSignal.aborted, true)
  assert.deepEqual(calls.filter(({ op }) => op === 'cancel' || op === 'undo'), [
    { op: 'cancel', sessionId: 'one', grant: 'open', operationId: render.operationId },
  ], 'undo does not race the pending reap or send another cancellation')
  await act(async () => acknowledge())
  await settle(() => calls.some(({ op }) => op === 'undo'))
  assert.deepEqual(calls.filter(({ op }) => ['render', 'cancel', 'undo'].includes(op)).map(({ op }) => op), ['render', 'cancel', 'undo'])
  await act(async () => tab.abort())
})

test('automatic window stays held after a drag conflict, then uses a verified same-track save and rejects an unrelated undo readback', async t => {
  const { root, button, submit, change, Simulate } = await mount(t)
  const oldFetch = globalThis.fetch, tab = new AbortController()
  t.after(() => { globalThis.fetch = oldFetch })
  const committed = `sha256:${'b'.repeat(64)}`, undone = `sha256:${'c'.repeat(64)}`, unrelated = `sha256:${'d'.repeat(64)}`
  const inspected = revision => ({ ...snapshot, content_version: revision, document: { ...snapshot.document, _vtx: { protocol: 1, head: revision } } })
  const queried = revision => ({ ...full, content_version: revision,
    tracks: tracks.map(track => ({ ...track, data: { ...track.data, id: track.address } })),
    blocks: blocks.map(block => ({ ...block, data: { ...block.data, id: block.address },
      range: revision === committed && block.address === '/t/0/b/0' ? [15, 35] : block.range })) })
  const windowCap = { schema: 'framivra.timeline.preview-window/v1', inline_media: true, prepare_inputs: true }
  const opened = { grant: 'open', snapshot: inspected(version), media: [], host: { ops: ['preview-window'] }, capabilities: { capabilities: {
    frame: { inline_png: { flag: '--include-png' } }, preview_window: windowCap,
  } } }
  const reads = new Map(), rendered = [], plans = []
  let write, savedRevision = version, conflict = true, reaps = 0
  globalThis.fetch = async (path, init) => {
    assert.equal(path, '/api/framivra/control')
    const { op, query, operations } = JSON.parse(init.body)
    if (op === 'open') return Response.json(opened)
    if (op === 'reference') return Response.json({ content_version: savedRevision,
      selection: { blocks: query.block_addresses.map(address => ({ address, stable_id: address })) } })
    if (op === 'authorize-write') return Response.json({ ok: true, writeGrant: 'independent-write' })
    if (op === 'plan') { plans.push(operations); return Response.json({ ok: true, planToken: 'plan' }) }
    if (op === 'commit' && conflict) return Response.json({ ok: false, code: 'conflict' })
    if (op === 'commit' || op === 'undo') {
      write = op; reads.clear()
      savedRevision = op === 'commit' ? committed : unrelated
      return Response.json({ ok: true, revision: op === 'commit' ? committed : undone, current_revision: unrelated })
    }
    if (write && ['inspect', 'query'].includes(op)) return new Promise(resolve => reads.set(op, value => resolve(Response.json(value))))
    if (op === 'query') return Response.json(queried(version))
    return Response.json({ ok: true })
  }
  const previewSession = {
    qualification: { capability: 'preview-session', version: 1 }, ownerId: 'host-owner', revoked: tab.signal,
    grant: { id: 'auto', ownerId: 'host-owner', sessionId: 'one', project: 'timeline.json', openGrant: 'open', expiresAt: Date.now() + 60_000 },
    request(op, request, signal) {
      if (op === 'cancel') {
        reaps++
        return Response.json({ ok: true, reaped: true, operationId: request.operationId, grantId: 'auto', ownerId: 'host-owner', openGrant: 'open' })
      }
      assert.equal(op, 'render')
      rendered.push([request.contentVersion, request.frame]); return new Promise(resolve => signal.addEventListener('abort', () => resolve(new Response()), { once: true }))
    },
  }
  const useTabInfo = () => ({ tab: { signal: tab.signal } })
  const click = label => act(async () => {
    const action = button(label)
    assert(action && !action.disabled, `expected enabled action: ${label}`)
    action.click()
  })
  await act(async () => root.render(h(PreviewBody, { useTabInfo, sessionId: 'one', previewSession })))
  await submit(0)
  await settle(() => button('音画窗口'))
  const frameInput = () => document.querySelector('input[inputmode="numeric"]')
  await change(frameInput(), '5')
  await click('启用编辑')
  await act(async () => document.querySelector('.fv-clip[aria-label^="选择块 /t/0/b/0"]').click())
  await click('音画窗口')
  assert.deepEqual(rendered, [[version, 5]])
  const move = async () => {
    const clip = document.querySelector('.fv-clip[aria-label^="选择块 /t/0/b/0"]')
    assert.equal(clip.getAttribute('data-movable'), 'true')
    document.querySelector('.fv-ruler').getBoundingClientRect = () => ({ left: 0, width: 200 })
    clip.closest('.fv-track').getBoundingClientRect = () => ({ top: 0, bottom: 20 })
    const target = { pointerId: 1, clientX: 40, clientY: 10, altKey: true }
    await act(async () => Simulate.pointerDown(clip, { pointerId: 1, button: 0, clientX: 10, clientY: 10 }))
    await act(async () => Simulate.pointerMove(clip, target))
    // A real drag can hold the ghost long enough for the old worker to be fully reaped before pointer-up.
    await settle(() => !button('取消并回收音画窗口'))
    assert(document.querySelector('[aria-label="未保存拖动 ghost"]'))
    await act(async () => Simulate.pointerUp(clip, target))
  }
  await move()
  assert.equal(reaps, 1)
  assert.deepEqual(plans, [[{ op: 'block.move', id: '/t/0/b/0', track_id: '/t/0', at: 15 }]])
  await settle(() => button('退出编辑')?.disabled === false)
  assert.deepEqual(rendered, [[version, 5]], 'reconnecting after a conflict must not render the stale saved binding')
  assert.equal(reads.size, 0)
  conflict = false
  await move()
  await settle(() => reads.size === 2)
  await change(frameInput(), '7')
  await change(frameInput(), '8')
  await act(async () => reads.get('inspect')(inspected(committed)))
  assert.deepEqual(rendered, [[version, 5]], 'one read cannot authorize the next version')
  await act(async () => reads.get('query')(queried(committed)))
  await settle(() => rendered.length === 2)
  assert.deepEqual(rendered, [[version, 5], [committed, 8]])
  await click('撤销当前版本')
  await settle(() => write === 'undo' && reads.size === 2)
  await act(async () => { reads.get('inspect')(inspected(unrelated)); reads.get('query')(queried(unrelated)) })
  await settle(() => !button('音画窗口'))
  assert.deepEqual(rendered, [[version, 5], [committed, 8]], 'matching the undo receipt is mandatory even when the two reads agree')
  await act(async () => tab.abort())
})

test('preview band follows the held verified A/V only, across playability, superseded requests, the last frame and revocation', async t => {
  const { root, dom, button, submit, change } = await mount(t)
  const oldFetch = globalThis.fetch, tab = new AbortController()
  t.after(() => { globalThis.fetch = oldFetch })
  const { Event, HTMLMediaElement } = dom.window
  HTMLMediaElement.prototype.pause = HTMLMediaElement.prototype.load = function () {}
  const quality = { capability: 'preview-session', version: 1, ownerId: 'owner' }
  const opened = { grant: 'open', snapshot, media: [], host: { ops: ['preview-window'] }, previewSession: quality, capabilities: { capabilities: {
    preview_window: { schema: 'framivra.timeline.preview-window/v1', inline_media: true, prepare_inputs: true, input_epoch: true },
  } } }
  const grant = { id: 'auto', ownerId: 'owner', sessionId: 'one', project: 'timeline.json', openGrant: 'open', expiresAt: Date.now() + 60_000, maxWindows: 10 }
  const renders = new Map(), inputEpoch = `sha256:${'e'.repeat(64)}`
  const bytes = Buffer.from('fixture window bytes'), digest = createHash('sha256').update(bytes).digest('hex')
  let acknowledgeRevoke
  globalThis.fetch = (_path, init) => {
    const request = JSON.parse(init.body)
    if (request.op === 'open') return Response.json(opened)
    if (request.op === 'query') return Response.json(full)
    if (request.op === 'authorize-preview-session') return Response.json({ ok: true, grant })
    if (request.op === 'revoke-preview-session') return new Promise(resolve => { acknowledgeRevoke = () => resolve(Response.json({ ok: true, revoked: true, grantId: grant.id })) })
    if (['session-render', 'render'].includes(request.op)) return new Promise(resolve => renders.set(request.frame, () => resolve(new Response(bytes, { headers: {
      'Content-Type': 'video/mp4', 'X-Framivra-Version': version, 'X-Framivra-SHA256': digest,
      'X-Framivra-Start': String(request.frame), 'X-Framivra-End': String(Math.min(100, request.frame + 30)),
      ...(request.op === 'session-render' ? { 'X-Framivra-Input-Epoch': inputEpoch } : {}),
    } })))) // Intentionally permits an aborted request to complete late.
    return Response.json({ ok: true, reaped: true, operationId: request.operationId, ownerId: grant.ownerId, grantId: grant.id, openGrant: grant.openGrant })
  }
  const useTabInfo = () => ({ tab: { signal: tab.signal } })
  const click = label => act(async () => { const node = button(label); assert(node && !node.disabled, label); node.click() })
  const pin = frame => change(document.querySelector('input[inputmode="numeric"]'), String(frame))
  const status = () => document.querySelector('[aria-label="预览状态带"] [role="status"]').textContent
  const segment = state => document.querySelector(`.fv-preview-band [data-status="${state}"]`)
  const video = () => document.querySelector('.fv-program-stage[data-mode="window"] video')
  const emit = (node, name) => act(async () => node.dispatchEvent(new Event(name)))
  async function decoded(node, duration) {
    for (const [key, value] of Object.entries({ videoWidth: 1920, videoHeight: 1080, currentSrc: node.getAttribute('src'), duration, readyState: 2 })) {
      Object.defineProperty(node, key, { value, writable: true, configurable: true })
    }
    await emit(node, 'loadeddata')
  }
  const canplay = node => { node.readyState = 3; return emit(node, 'canplay') }

  await act(async () => root.render(h(PreviewBody, { useTabInfo, sessionId: 'one' })))
  await submit(0)
  await settle(() => button('音画窗口'))
  assert.match(status(), /未就绪/)
  assert.match(document.querySelector('[aria-label="预览状态带"]').textContent, /仅视频.*不计入音画/)
  await pin(39)
  await click('音画窗口')
  for (const input of document.querySelectorAll('[aria-label="单窗口音画监看"] fieldset input')) await act(async () => input.click())
  await click('开启当前工程自动预览')
  await settle(() => renders.has(39))
  assert.match(status(), /已请求合成 \[39, 69\)/)
  assert(segment('rendering'))
  await pin(69)
  await settle(() => renders.has(69))
  await act(async () => renders.get(69)())
  await settle(video)
  const current = video()
  assert.equal(segment('rendering'), null, 'transferred bytes and decode do not count as synthesis')
  await decoded(current, 1)
  assert.equal(segment('ready'), null, 'loadeddata is not a playability proof')
  await canplay(current)
  assert.match(status(), /音画就绪 \[69, 99\)/)
  assert.equal(segment('ready').style.left, '69%')
  assert.equal(segment('ready').style.width, '30%')
  await emit(current, 'pause')
  assert(segment('ready'), 'a paused but playable window is still held')
  current.readyState = 2
  await emit(current, 'waiting')
  assert.match(status(), /未就绪/)
  assert.equal(segment('ready'), null)
  await canplay(current)
  await act(async () => renders.get(39)())
  assert.equal(video(), current)
  assert.match(status(), /音画就绪 \[69, 99\)/, 'late bytes cannot replace the current green range')
  await pin(98)
  assert.equal(renders.size, 2, 'last included frame reuses the held window')
  await pin(99)
  await settle(() => renders.has(99))
  assert.equal(segment('ready'), null, 'the exclusive end evicts the old interval')
  assert.match(status(), /已请求合成 \[99, 100\)/)
  await act(async () => renders.get(99)())
  await settle(video)
  const last = video()
  await decoded(last, 1 / 30)
  await canplay(last)
  assert.match(status(), /音画就绪 \[99, 100\)/)
  assert.equal(segment('ready').style.left, '99%')
  assert.equal(segment('ready').style.width, '1%')
  await click('停止并撤销自动预览')
  assert.match(status(), /未就绪/)
  assert.equal(segment('ready'), null, 'revoke hides green before the Host acknowledges it')
  await canplay(last)
  assert.equal(segment('ready'), null, 'late canplay after revoke does not restore green')
  await act(async () => acknowledgeRevoke())

  for (const input of [...document.querySelectorAll('[aria-label="单窗口音画监看"] input')].slice(0, 2)) await act(async () => input.click())
  renders.delete(99)
  await click('生成当前音画窗口')
  await settle(() => renders.has(99))
  await act(async () => renders.get(99)())
  await settle(video)
  await decoded(video(), 1 / 30)
  await canplay(video())
  assert.equal(segment('ready'), null, 'a playable legacy window without input_epoch cannot paint green')
  await act(async () => tab.abort())
})

test('actual React DOM: empty and unsafe displays retain diagnostics/raw; component CSS is self-contained and scoped', async t => {
  const { root } = await mount(t)
  await act(async () => root.render(h(TimelineView, { snapshot, result: { ...full, tracks: [], blocks: [] } })))
  assert(document.body.textContent.includes('空项目'))
  assert(document.querySelector('.fv-timeline style').textContent.includes('.fv-timeline'))
  assert(document.querySelector('link[rel="stylesheet"]') === null, "Expected absent DOM node: document.querySelector('link[rel=\"stylesheet\"]')")
  await act(async () => root.render(h(TimelineView, { snapshot: { ...snapshot, document: { ...snapshot.document, project: { duration_frames: JSON.rawJSON('9007199254740993') } } }, result: full })))
  assert(document.querySelector('[role="alert"]').textContent.length > 0)
  assert(document.body.textContent.includes('DISPLAY_PRECISION'))
  assert(document.body.textContent.includes('9007199254740993'))
  assert(document.querySelector('.fv-ruler') === null, "Expected absent DOM node: document.querySelector('.fv-ruler')")
})
