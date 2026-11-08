import test from 'node:test'
import assert from 'node:assert/strict'
import React, { act } from 'react'
import { JSDOM } from 'jsdom'
import { stepFrame, Transport, LayoutDivider, SelectionSummary } from '../src/client/editor-controls.mjs'
import { ManualEditor } from '../src/client/manual.mjs'

const h = React.createElement
const MAX = Number.MAX_SAFE_INTEGER

async function mount(t) {
  const dom = new JSDOM('<div id="root"></div>', { url: 'http://owner/' })
  globalThis.window = dom.window
  globalThis.document = dom.window.document
  globalThis.IS_REACT_ACT_ENVIRONMENT = true
  const { createRoot } = await import('react-dom/client')
  const { Simulate } = await import('react-dom/test-utils')
  const root = createRoot(document.getElementById('root'))
  t.after(async () => { await act(async () => root.unmount()); dom.window.close(); delete globalThis.window; delete globalThis.document })
  const byLabel = name => document.querySelector(`[aria-label="${name}"]`)
  return { root, dom, Simulate, byLabel }
}

// ---------- stepFrame: overflow-safe navigation math ----------

test('stepFrame: MAX_SAFE duration never overflows; result clamps to duration-1', () => {
  assert.equal(stepFrame(MAX - 1, MAX, 1), MAX - 1, 'at last frame stays at duration-1')
  assert.equal(stepFrame(MAX - 3, MAX, 1, 5), MAX - 1, 'large distance clamps to duration-1, no overflow')
  assert.equal(stepFrame(0, MAX, -1), 0, 'backward from 0 clamps at 0')
  assert.equal(stepFrame(MAX - 2, MAX, 1), MAX - 1)
  assert.equal(stepFrame(5, MAX, -1, 2), 3)
})

test('stepFrame: malformed duration/frame/distance return undefined, never a wrong frame', () => {
  for (const bad of ['x', '', null, undefined, 0, -3, 1.5, NaN, Infinity]) assert.equal(stepFrame(5, bad, 1), undefined, `duration ${String(bad)}`)
  for (const bad of [0, -1, 1.5, '1', NaN]) assert.equal(stepFrame(5, 10, 1, bad), undefined, `distance ${String(bad)}`)
  assert.equal(stepFrame(undefined, undefined, 1), undefined)
  assert.equal(stepFrame('5', 10, 1), 1, 'string frame is not safe → falls back to current 0')
})

test('stepFrame: negative/empty/out-of-range frames fall back to 0 as current frame', () => {
  assert.equal(stepFrame(-1, 10, -1), 0)
  assert.equal(stepFrame(10, 10, 1), 1, 'frame === duration is out of range → current 0 → forward 1')
  assert.equal(stepFrame(99, 10, 1), 1)
  assert.equal(stepFrame(0, 10, 0), 1, 'direction 0 is treated as forward')
})

// ---------- Transport: navigation only, zero network ----------

test('Transport: buttons and input drive callbacks only; no fetch or any network IO', async t => {
  const { root, Simulate } = await mount(t)
  const oldFetch = globalThis.fetch
  globalThis.fetch = async () => { throw new Error('NETWORK_FORBIDDEN') }
  t.after(() => { globalThis.fetch = oldFetch })
  const frames = [], texts = [], queries = []
  await act(async () => root.render(h(Transport, { frame: 5, frameText: '5', duration: 30, onFrame: v => frames.push(v), onText: v => texts.push(v), onQuery: () => queries.push(1) })))
  const buttons = [...document.querySelectorAll('button')]
  await act(async () => { buttons.find(b => b.getAttribute('aria-label') === '定位首帧').click() })
  await act(async () => { buttons.find(b => b.getAttribute('aria-label') === '监视器前一帧').click() })
  await act(async () => { buttons.find(b => b.getAttribute('aria-label') === '监视器后一帧').click() })
  await act(async () => { buttons.find(b => b.getAttribute('aria-label') === '定位末帧').click() })
  assert.deepEqual(frames, ['0', '4', '6', '29'])
  const input = document.querySelector('input[name="framivra-frame"]')
  await act(async () => Simulate.change(input, { target: { value: '12' } }))
  assert.deepEqual(texts, ['12'])
  const form = document.querySelector('form')
  await act(async () => form.dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true })))
  assert.deepEqual(queries, [1], 'form submit only queries; no network')
})

test('Transport: boundary disables and invalid-duration presentation', async t => {
  const { root } = await mount(t)
  await act(async () => root.render(h(Transport, { frame: 0, frameText: '0', duration: 30, onFrame: () => {}, onText: () => {}, onQuery: () => {} })))
  assert(document.querySelector('[aria-label="监视器前一帧"]').disabled, 'frame 0 disables previous')
  assert(!document.querySelector('[aria-label="监视器后一帧"]').disabled)
  await act(async () => root.render(h(Transport, { frame: 29, frameText: '29', duration: 30, onFrame: () => {}, onText: () => {}, onQuery: () => {} })))
  assert(document.querySelector('[aria-label="监视器后一帧"]').disabled, 'last frame disables next')
  await act(async () => root.render(h(Transport, { frame: undefined, frameText: '', duration: 'x', onFrame: () => {}, onText: () => {}, onQuery: () => {} })))
  assert(document.querySelector('[aria-label="定位首帧"]').disabled)
  assert(document.querySelector('[aria-label="定位末帧"]').disabled)
  assert(document.body.textContent.includes('时长不可显示'))
})

// ---------- LayoutDivider: pointer ownership, cancel/blur retirement, keyboard ----------

test('LayoutDivider: first pointer owns the drag; wrong pointerId moves are ignored', async t => {
  const { root, Simulate } = await mount(t)
  const values = []
  const box = { bottom: 400, height: 200 }
  await act(async () => root.render(h(LayoutDivider, { value: 38, onChange: v => values.push(v) })))
  const divider = document.querySelector('.fv-layout-divider')
  divider.parentElement.getBoundingClientRect = () => box
  await act(async () => Simulate.pointerDown(divider, { button: 0, pointerId: 1, clientY: 200 }))
  await act(async () => Simulate.pointerMove(divider, { pointerId: 2, clientY: 100 })) // wrong pointer
  assert.deepEqual(values, [], 'wrong pointerId produced no change')
  await act(async () => Simulate.pointerMove(divider, { pointerId: 1, clientY: 100 })) // (400-100)*100/200 = 150 → clamp 65
  assert.deepEqual(values, [65])
  await act(async () => Simulate.pointerDown(divider, { button: 0, pointerId: 3, clientY: 300 })) // second down ignored while dragging
  await act(async () => Simulate.pointerMove(divider, { pointerId: 1, clientY: 300 })) // (400-300)*100/200 = 50
  assert.deepEqual(values, [65, 50], 'second pointerDown did not steal the drag')
})

test('LayoutDivider: pointercancel and window blur retire the drag; later moves are no-ops', async t => {
  const { root, Simulate } = await mount(t)
  const values = []
  await act(async () => root.render(h(LayoutDivider, { value: 38, onChange: v => values.push(v) })))
  const divider = document.querySelector('.fv-layout-divider')
  divider.parentElement.getBoundingClientRect = () => ({ bottom: 400, height: 200 })
  await act(async () => Simulate.pointerDown(divider, { button: 0, pointerId: 7, clientY: 200 }))
  await act(async () => Simulate.pointerCancel(divider, { pointerId: 7 }))
  await act(async () => Simulate.pointerMove(divider, { pointerId: 7, clientY: 100 }))
  assert.deepEqual(values, [], 'moves after cancel are ignored')
  await act(async () => Simulate.pointerDown(divider, { button: 0, pointerId: 8, clientY: 200 }))
  await act(async () => window.dispatchEvent(new window.Event('blur')))
  await act(async () => Simulate.pointerMove(divider, { pointerId: 8, clientY: 100 }))
  assert.deepEqual(values, [], 'moves after window blur are ignored')
})

test('LayoutDivider: keyboard and double-click resize locally, clamped to 26..65; no network', async t => {
  const { root, Simulate } = await mount(t)
  const oldFetch = globalThis.fetch
  globalThis.fetch = async () => { throw new Error('NETWORK_FORBIDDEN') }
  t.after(() => { globalThis.fetch = oldFetch })
  const values = []
  const render = value => act(async () => root.render(h(LayoutDivider, { value, onChange: v => values.push(v) })))
  await render(38)
  const divider = document.querySelector('.fv-layout-divider')
  const key = async k => { await act(async () => Simulate.keyDown(divider, { key: k })); await render(values.at(-1)) }
  await key('ArrowUp')    // 43
  await key('ArrowDown')  // 38
  await key('ArrowDown')  // 33
  await key('Home')       // 38 reset
  await key('End')        // 65
  await act(async () => Simulate.doubleClick(divider)); await render(values.at(-1)) // 38
  await key('ArrowDown')  // 33
  assert.deepEqual(values, [43, 38, 33, 38, 65, 38, 33], 'keyboard steps ±5 from latest value; Home resets; double-click resets')
})

// ---------- SelectionSummary: values only from the saved projection ----------

const version = `sha256:${'a'.repeat(64)}`
const mkResult = (kind, name) => ({
  content_version: version, coverage: { complete: true, scope: 'stored', unexpanded: [] }, diagnostics: [],
  tracks: [{ family: 'video', track_index: 0, address: '/t/0', data: { name: 'MAIN', ...(kind === 'gfx' ? { kind: 'gfx' } : {}) } }],
  blocks: [{ family: 'video', track_index: 0, block_index: 0, address: '/t/0/b/0', range: [10, 40], data: { name, kind } }],
})

test('SelectionSummary: shows the saved clip name and range; split gated by bounds, gfx, count and write grant', async t => {
  const { root } = await mount(t)
  const result = mkResult('aroll', 'A-roll 开场')
  const quickCalls = []
  const render = (props) => act(async () => root.render(h(SelectionSummary, { selection: { kind: 'block', address: '/t/0/b/0' }, selected: [{ kind: 'block', address: '/t/0/b/0' }], result, frame: 20, onFrame: () => {}, onClear: () => {}, onProperties: () => {}, quick: op => quickCalls.push(op), ...props })))
  await render({})
  assert(document.body.textContent.includes('A-roll 开场'), 'name comes from saved block data')
  assert(document.body.textContent.includes('10–40 · 30 帧'), 'range from saved projection')
  const split = document.querySelector('[aria-label="在播放头分割选中片段"]')
  assert(!split.disabled, 'interior frame + write grant enables split')
  await act(async () => split.click())
  assert.deepEqual(quickCalls, ['block.split'])
  await render({ frame: 10 }); assert(document.querySelector('[aria-label="在播放头分割选中片段"]').disabled, 'frame === range[0] blocked')
  await render({ frame: 40 }); assert(document.querySelector('[aria-label="在播放头分割选中片段"]').disabled, 'frame === range[1] blocked')
  await render({ frame: 'x' }); assert(document.querySelector('[aria-label="在播放头分割选中片段"]').disabled, 'malformed frame blocked')
  await render({ quick: undefined })
  const readonly = document.querySelector('[aria-label="在播放头分割选中片段"]')
  assert(readonly.disabled && readonly.title.includes('请先在属性面板明确授予工程写许可'), 'no quick → readonly with grant hint')
  assert(document.querySelector('[aria-label="将移除选中片段加入批次"]').disabled)
  await render({ selected: [{ kind: 'block', address: '/t/0/b/0' }, { kind: 'block', address: '/t/0/b/1' }], result: { ...result, blocks: [...result.blocks, { ...result.blocks[0], address: '/t/0/b/1' }] } })
  assert(document.querySelector('[aria-label="在播放头分割选中片段"]').disabled, 'multi-selection cannot split')
})

test('SelectionSummary: gfx blocks and gfx tracks disable split but keep remove', async t => {
  const { root } = await mount(t)
  const gfxBlock = mkResult('gfx', '动态图表')
  await act(async () => root.render(h(SelectionSummary, { selection: { kind: 'block', address: '/t/0/b/0' }, selected: [{ kind: 'block', address: '/t/0/b/0' }], result: gfxBlock, frame: 20, onFrame: () => {}, onClear: () => {}, onProperties: () => {}, quick: () => { throw new Error('must not run') } })))
  assert(document.querySelector('[aria-label="在播放头分割选中片段"]').disabled, 'gfx block cannot split')
  assert(!document.querySelector('[aria-label="将移除选中片段加入批次"]').disabled, 'gfx can still be removed')
  // gfx track via the owning track, even when the block itself is not kind gfx
  const gfxTrack = mkResult('aroll', '普通块')
  gfxTrack.tracks[0].data.kind = 'gfx'
  await act(async () => root.render(h(SelectionSummary, { selection: { kind: 'block', address: '/t/0/b/0' }, selected: [{ kind: 'block', address: '/t/0/b/0' }], result: gfxTrack, frame: 20, onFrame: () => {}, onClear: () => {}, onProperties: () => {}, quick: () => { throw new Error('must not run') } })))
  assert(document.querySelector('[aria-label="在播放头分割选中片段"]').disabled, 'block on a gfx track cannot split')
})

// ---------- ManualEditor quick: stale retained handlers refuse ----------

const openedFixture = { grant: 'g1', snapshot: { content_version: version, document: { _vtx: { protocol: 1 }, sources: [] } }, media: [] }

async function editorMount(t, quicks) {
  const { root, ...ui } = await mount(t)
  const calls = []
  const call = async (_sessionId, payload) => { calls.push(payload); if (payload.op === 'authorize-write') return { ok: true, writeGrant: 'wg' }; return { ok: true } }
  const props = { sessionId: 'one', opened: openedFixture, selection: { kind: 'block', address: '/t/0/b/0' }, selected: [{ kind: 'block', address: '/t/0/b/0' }], frame: 20, tabSignal: new AbortController().signal, call, onCommitted: async () => {}, onQuickReady: q => quicks.push(q) }
  const render = extra => act(async () => root.render(h(ManualEditor, { ...props, ...extra })))
  return { root, calls, render, ...ui }
}

test('ManualEditor quick: retained callback refuses after selection/frame/grant change (EDIT_STALE) and rejects unsupported ops', async t => {
  const quicks = []
  const { root, calls, render, byLabel } = await editorMount(t, quicks)
  const result = mkResult('aroll', '块一')
  await render({ result })
  await act(async () => { [...document.querySelectorAll('button')].find(b => b.textContent === '明确允许本会话向此工程及 core 历史写入').click() })
  for (let i = 0; i < 100 && !(quicks.at(-1) instanceof Function); i++) await act(async () => { await new Promise(r => setTimeout(r, 1)) })
  const quick = quicks.find(q => q instanceof Function)
  assert(quick, 'quick became available after explicit write authorization')
  // change the frame: the retained callback must refuse
  await render({ result, frame: 21 })
  assert.throws(() => quick('block.split'), /EDIT_STALE/)
  // refresh and capture the new callback; then change selection target
  const quick2 = quicks.at(-1)
  assert(quick2 instanceof Function)
  await render({ result, selection: { kind: 'track', address: '/t/0' }, selected: [{ kind: 'track', address: '/t/0' }] })
  assert.throws(() => quick2('block.split'), /EDIT_STALE/)
  // unsupported operations and gfx split are refused even on a fresh callback
  const quick3 = quicks.at(-1)
  assert(quick3 instanceof Function)
  assert.throws(() => quick3('track.rename'), /EDIT_UNSUPPORTED/)
  const gfx = mkResult('gfx', '图形块')
  await render({ result: gfx, frame: 20 })
  await render({ result: gfx, frame: 20 })
  const fresh = quicks.at(-1)
  assert(fresh instanceof Function)
  assert.throws(() => fresh('block.split'), /EDIT_UNSUPPORTED/, 'fresh gfx callback must specifically refuse this unsupported operation')
})

test('ManualEditor quick: split outside block bounds refuses with EDIT_BOUNDS; no plan or commit is ever issued by quick', async t => {
  const quicks = []
  const { render, calls } = await editorMount(t, quicks)
  const result = mkResult('aroll', '块一')
  await render({ result, frame: 5 }) // frame 5 <= range[0] 10
  await act(async () => { [...document.querySelectorAll('button')].find(b => b.textContent === '明确允许本会话向此工程及 core 历史写入').click() })
  for (let i = 0; i < 100 && !(quicks.at(-1) instanceof Function); i++) await act(async () => { await new Promise(r => setTimeout(r, 1)) })
  const quick = quicks.at(-1)
  assert(quick instanceof Function)
  // re-render at an out-of-bounds frame, then grab the fresh callback
  await render({ result, frame: 5 })
  const fresh = quicks.at(-1)
  assert(fresh instanceof Function)
  assert.throws(() => fresh('block.split'), /EDIT_BOUNDS/)
  assert(!calls.some(c => c.op === 'plan' || c.op === 'commit'), 'quick never plans or commits on its own')
})
