import test from 'node:test'
import assert from 'node:assert/strict'
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
  const button = text => [...document.querySelectorAll('button')].find(node => node.textContent === text)
  const change = async (input, value) => act(async () => Simulate.change(input, { target: { value } }))
  const submit = async index => act(async () => document.querySelectorAll('form')[index].dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true })))
  return { root, dom, button, change, submit, Simulate }
}

async function settle(predicate) {
  for (let i = 0; i < 100; i++) {
    if (predicate()) return
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 1)) })
  }
  assert.fail(`DOM did not settle: ${document.body.textContent}`)
}

test('actual React DOM: full projection persists, hover is local, ruler/keys set canonical input and selection references click-time frame', async t => {
  const { root, dom, button, change, submit, Simulate } = await mount(t)
  const requests = [], copies = [], order = [], inserts = [], forbidden = []
  const oldFetch = globalThis.fetch, oldNavigator = Object.getOwnPropertyDescriptor(globalThis, 'navigator')
  t.after(() => { globalThis.fetch = oldFetch; if (oldNavigator) Object.defineProperty(globalThis, 'navigator', oldNavigator) })
  Object.defineProperty(globalThis, 'navigator', { configurable: true, value: { clipboard: { writeText: async text => copies.push(text) } } })
  let nextVersion = version
  globalThis.fetch = async (_url, init) => {
    const request = JSON.parse(init.body); requests.push(request); order.push(request.op)
    let result
    if (request.op === 'open') result = { grant: 'grant', snapshot, media: [] }
    else if (request.op === 'query') result = request.query.frame === undefined ? full : { ...filtered, content_version: nextVersion }
    else if (request.op === 'proposal-list') result = { ok: true, proposals: [] }
    else if (request.op === 'reference') {
      const kind = request.query.block_addresses ? 'blocks' : 'tracks'
      const address = (request.query.block_addresses ?? request.query.track_addresses)[0]
      result = { content_version: version, selection: { [kind]: [{ address, stable_id: address.endsWith('/0') ? 'stable-id' : null, raw: '{ "unsafe":9007199254740993 }' }] } }
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
  assert.deepEqual(requests.map(x => x.op), ['open', 'query', 'proposal-list'])
  assert.equal(document.querySelectorAll('.fv-clip').length, blocks.length)
  assert(document.body.textContent.includes('gfx / track 1'))
  assert.equal(document.querySelectorAll('[aria-label^="显示子行"]').length, 6) // video + gfx + 3 audio + subtitle
  const input = document.querySelector('input[inputmode="numeric"]')
  const ruler = document.querySelector('.fv-ruler')
  ruler.getBoundingClientRect = () => ({ left: 10, width: 200 }) // Geometry adapter; not a browser layout claim.
  const beforeHover = requests.length
  await act(async () => Simulate.pointerMove(ruler, { clientX: 110, pointerId: 1 }))
  assert(document.body.textContent.includes('悬停帧：50'))
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
  assert(document.querySelector('[aria-label="活动引用"]').textContent.includes('/t/2/b/1'))
  await act(async () => button('复制 raw JSON').click())
  await settle(() => copies.length === 1)
  assert.equal(copies[0], '{ "unsafe":9007199254740993 }')
  await change(input, '71')
  const beforeDraft = requests.length
  await act(async () => button('加入当前会话草稿（不发送）').click())
  await settle(() => document.body.textContent.includes('已通过官方顺序接口'))
  assert.deepEqual(order.slice(-3), ['capture', 'reference', 'insert'])
  assert.equal(requests[beforeDraft].query.frame, 71)
  assert.equal(inserts.length, 1)
  const { text: insertedText, span } = inserts[0]
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
  assert.equal(button('复制 raw JSON'), undefined)
  assert(document.body.textContent.includes('不会暗用旧帧'))
  assert.equal(document.querySelectorAll('.fv-clip').length, blocks.length)
  await act(async () => Simulate.keyDown(ruler, { key: 'Home', preventDefault() {} }))
  assert.equal(input.value, '0')
  await settle(() => document.body.textContent.includes('引用就绪。'))
  await act(async () => button('放大').click())
  assert(document.querySelector('[aria-label="可见帧范围"]').textContent.includes('[25, 75)'))
  await act(async () => button('向后平移').click())
  assert(document.querySelector('[aria-label="可见帧范围"]').textContent.includes('[50, 100)'))
  await act(async () => button('适配全片').click())
  assert.equal(document.querySelectorAll('.fv-clip').length, blocks.length)
  assert(document.body.textContent.includes('external.srt'))
  assert(document.body.textContent.includes('9007199254740993'))
  assert(document.body.textContent.includes('provenance UNKNOWN'))
  nextVersion = `sha256:${'b'.repeat(64)}`
  await submit(1)
  await settle(() => document.body.textContent.includes('旧块已隐藏'))
  assert.equal(document.querySelector('.fv-clip'), null)
  assert.equal(button('复制 raw JSON'), undefined)
  await act(async () => root.render(h(PreviewBody, { useTabInfo, sessionId: 'two' })))
  assert.equal(document.querySelector('.fv-timeline'), null)
  assert.equal(requests.at(-1).op, 'close')
  assert.equal(requests.at(-1).sessionId, 'one')
  assert(requests.every(x => ['open', 'query', 'reference', 'proposal-list', 'close'].includes(x.op)))
})

test('actual React DOM: pending references abort on selection/frame/session/unmount; late reads never publish old raw', async t => {
  const { root, button } = await mount(t)
  const oldFetch = globalThis.fetch
  t.after(() => { globalThis.fetch = oldFetch })
  const reads = []
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
  await act(async () => render(b, 2))
  assert.equal(reads[1].signal.aborted, true)
  assert.equal(reads.length, 2) // Moving playhead aborts old IO, never implicitly re-queries.
  await act(async () => reads[1].finish('OLD_B'))
  assert(!document.body.textContent.includes('OLD_B'))
  await act(async () => render(b, 2, 'two'))
  assert.equal(reads.length, 3)
  assert.equal(reads[2].request.sessionId, 'two')
  assert.equal(reads[2].request.query.frame, 2)
  await act(async () => reads[2].finish('CURRENT_B'))
  assert(document.body.textContent.includes('CURRENT_B'))
  // Missing public composer cannot accidentally append or issue IO; copying remains enabled.
  assert.equal(button('复制 raw JSON').disabled, false)
  await act(async () => button('加入当前会话草稿（不发送）').click())
  assert(document.body.textContent.includes('DRAFT_UNAVAILABLE'))
  assert.equal(reads.length, 3)
  await act(async () => button('复制 raw JSON').click())
  assert.equal(reads.length, 4)
  await act(async () => root.render(null))
  assert.equal(reads[3].signal.aborted, true)
  await act(async () => reads[3].finish('AFTER_UNMOUNT'))
  assert.equal(document.body.textContent, '')
})

test('actual React DOM: empty and unsafe displays retain diagnostics/raw; component CSS is self-contained and scoped', async t => {
  const { root } = await mount(t)
  await act(async () => root.render(h(TimelineView, { snapshot, result: { ...full, tracks: [], blocks: [] } })))
  assert(document.body.textContent.includes('空项目'))
  assert(document.querySelector('.fv-timeline style').textContent.includes('.fv-timeline'))
  assert.equal(document.querySelector('link[rel="stylesheet"]'), null)
  await act(async () => root.render(h(TimelineView, { snapshot: { ...snapshot, document: { ...snapshot.document, project: { duration_frames: JSON.rawJSON('9007199254740993') } } }, result: full })))
  assert(document.querySelector('[role="alert"]').textContent.length > 0)
  assert(document.body.textContent.includes('DISPLAY_PRECISION'))
  assert(document.body.textContent.includes('9007199254740993'))
  assert.equal(document.querySelector('.fv-ruler'), null)
})
