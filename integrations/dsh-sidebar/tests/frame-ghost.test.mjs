import test from 'node:test'
import assert from 'node:assert/strict'
import React, { act, useCallback, useState } from 'react'
import { JSDOM } from 'jsdom'
import { FrameEpoch, FrameTools } from '../src/client/frame.mjs'
import { VisualTimeline } from '../src/client/timeline.mjs'
import { emptySelection } from '../src/client/selection.mjs'

// Real React handlers/owner; geometry, capture, clipboard and draft boundaries
// below are explicit adapters, not native browser or applied-draft evidence.
const track = { family: 'video', track_index: 0, address: '/video_tracks/0', data: { id: 'v' } }
const block = { family: 'video', track_index: 0, address: '/video_tracks/0/clips/0', range: [0, 30], data: { id: 'a' } }
const snapshot = { content_version: 'old', document: { project: { duration_frames: 100 } } }
const result = { tracks: [track], blocks: [block] }
const cleanResult = () => ({ file: new File(['png'], 'frame.png', { type: 'image/png' }), certificate: { frame: 0, content_version: 'old' }, reference: { selection: { tracks: [], blocks: [] } } })
const handler = node => node[Object.keys(node).find(key => key.startsWith('__reactProps$'))].onClick

async function fixture(t) {
  const dom = new JSDOM('<div id="root"></div>', { url: 'http://ghost.invalid/' })
  const saved = Object.fromEntries(['window', 'document', 'navigator', 'fetch', 'ClipboardItem', 'IS_REACT_ACT_ENVIRONMENT'].map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]))
  globalThis.window = dom.window; globalThis.document = dom.window.document; globalThis.IS_REACT_ACT_ENVIRONMENT = true
  Object.defineProperty(globalThis, 'navigator', { configurable: true, value: dom.window.navigator })
  const { createRoot } = await import('react-dom/client'), { Simulate } = await import('react-dom/test-utils')
  const root = createRoot(document.getElementById('root')), owner = new FrameEpoch(), tab = new AbortController(), events = [], changes = []
  const binding = { session: { projections: { faceOf: () => ({ getSnapshot: () => ({ mediaTypes: ['image/png'], maxImageBytes: 100, maxMessageImageBytes: 100, maxImagesPerMessage: 1 }) }) } } }
  let referenceResolve, renderReject, stageResolve
  const faults = {}
  const services = { sessions: { binding: () => binding }, conversation: {
    captureDraftTarget(value) { assert.equal(value, binding); events.push('capture'); return { target: events.length } },
    async stageDraft(args) { events.push('stage'); faults.stageSignal = args.signal; if (faults.holdStage) await new Promise(resolve => { stageResolve = resolve }); return { status: 'rejected', reason: 'test-adapter-no-mutation' } },
  } }
  const call = async (_id, _body, signal) => { events.push('reference'); faults.referenceSignal = signal; if (faults.holdReference) await new Promise(resolve => { referenceResolve = resolve }); return { ok: true, content_version: 'old', selection: { global_frame: 0 } } }
  globalThis.fetch = async (_url, init) => { events.push('render'); faults.renderSignal = init.signal; return new Promise((_resolve, reject) => { renderReject = reject; init.signal.addEventListener('abort', () => reject(init.signal.reason), { once: true }) }) }
  Object.defineProperty(dom.window.navigator, 'clipboard', { value: { write: async () => { events.push('copy') } } })
  globalThis.ClipboardItem = class { static supports() { return true } }
  let props = { snapshot, result, frame: 0, selectionIntent: emptySelection() }, refresh
  const batch = operations => { assert(owner.ghost, 'dirty must be published before ghost exclusion ends'); events.push('dirty'); owner.editChanged({ dirty: true }); events.push(operations) }
  props.onGesture = batch
  function Harness() {
    const [, update] = useState(0); refresh = () => update(n => n + 1)
    const publish = useCallback((token, active) => {
      const changed = owner.gestureChanged(token, active)
      changes.push({ token, active, changed, dirty: owner.edit.dirty })
      if (changed) update(n => n + 1)
    }, [])
    return React.createElement(React.Fragment, null,
      React.createElement(VisualTimeline, { ...props, onGestureState: publish }),
      React.createElement(FrameTools, { owner, opened: { snapshot: props.snapshot, grant: 'grant' }, sessionId: 'one', frame: 0, tabSignal: tab.signal, getDraftServices: () => services, call }))
  }
  await act(async () => root.render(React.createElement(Harness)))
  document.querySelector('.fv-ruler').getBoundingClientRect = () => ({ left: 0, width: 1000 })
  document.querySelector('.fv-track').getBoundingClientRect = () => ({ top: 0, bottom: 100 })
  const clip = document.querySelector('.fv-clip'), held = new Set()
  clip.setPointerCapture = id => held.add(id); clip.hasPointerCapture = id => held.has(id)
  clip.releasePointerCapture = id => { held.delete(id); faults.onRelease?.() }
  const event = (id = 7, x = 100) => ({ pointerId: id, button: 0, clientX: x, clientY: 15 })
  const emit = async (type, id = 7, x = 100) => act(async () => Simulate[type](clip, event(id, x)))
  const button = start => [...document.querySelectorAll('[aria-label="当前合成帧与草稿"] button')].find(node => node.textContent.startsWith(start))
  const render = async patch => { props = { ...props, ...patch }; await act(async () => refresh()) }
  const generate = async () => { owner.generated = cleanResult(); await render({}) }
  const consent = async () => act(async () => Simulate.change(document.querySelector('[aria-label="当前合成帧与草稿"] input'), { target: { checked: true } }))
  t.after(async () => {
    referenceResolve?.(); stageResolve?.(); renderReject?.(new Error('fixture closed'))
    await act(async () => { tab.abort(); root.unmount(); await Promise.resolve() })
    assert.equal(owner.ghost, undefined); assert.equal(held.size, 0)
    dom.window.close()
    for (const [key, descriptor] of Object.entries(saved)) { if (descriptor) Object.defineProperty(globalThis, key, descriptor); else delete globalThis[key] }
  })
  return { owner, events, changes, faults, button, emit, clip, event, render, generate, consent, Simulate, root,
    releaseReference: () => referenceResolve?.(), releaseStage: () => stageResolve?.(),
    ghost: () => document.querySelector('[aria-label="未保存拖动 ghost"]') }
}

test('owner ghost exclusion aborts old operations and rejects only matching token release', () => {
  const owner = new FrameEpoch(), tab = new AbortController(), old = owner.begin(tab.signal), a = {}, b = {}
  owner.generated = cleanResult(); owner.gestureChanged(a, true)
  assert(old.signal.aborted); assert.equal(owner.generated, undefined)
  assert.throws(() => owner.check(owner.begin(tab.signal)), /FRAME_STALE/)
  owner.gestureChanged(b, true); assert.equal(owner.gestureChanged(a, false), false); assert.equal(owner.ghost, b)
  for (const key of ['dirty', 'busy', 'unknown']) {
    owner.editChanged({ [key]: true }); owner.gestureChanged(b, false)
    assert.equal(owner.edit[key], true); assert.throws(() => owner.assertReady(), /FRAME_STALE/)
    owner.editChanged({ [key]: false }); owner.gestureChanged(b, true)
  }
  owner.gestureChanged(b, false); owner.assertReady()
})

test('pointerdown synchronously guards retained generate/stage/copy handlers before capture or IO', async t => {
  const f = await fixture(t); await f.consent(); await f.generate()
  const stale = ['显式生成', '将该帧', '复制已生成'].map(start => handler(f.button(start)))
  await act(async () => {
    f.Simulate.pointerDown(f.clip, f.event())
    assert(f.owner.ghost, 'same event, before effects/render')
    for (const invoke of stale) invoke()
    assert.deepEqual(f.events, [])
  })
  await f.emit('pointerMove', 7, 200); assert(f.ghost())
  assert(f.button('显式生成').disabled && f.button('将该帧').disabled && f.button('复制已生成').disabled)
  // Bypass disabled attributes entirely with old closures, including detail=0.
  await act(async () => { for (const invoke of stale) invoke({ detail: 0 }) })
  assert.deepEqual(f.events, []); assert.equal(f.owner.generated, undefined)
  await f.emit('pointerCancel'); assert.equal(f.owner.ghost, undefined)
  assert.equal(f.owner.generated, undefined, 'cancellation never revives an old result')
  await f.generate(); await act(async () => handler(f.button('复制已生成'))()); assert.deepEqual(f.events, ['copy'])
})

test('completion publishes dirty before clearing ghost or releasing capture, then stale handlers stay blocked', async t => {
  const f = await fixture(t); await f.consent(); await f.generate()
  const stale = ['显式生成', '将该帧', '复制已生成'].map(start => handler(f.button(start)))
  f.faults.onRelease = () => { assert(f.owner.edit.dirty); for (const invoke of stale) invoke() }
  await f.emit('pointerDown'); await f.emit('pointerMove', 7, 200); await f.emit('pointerUp', 7, 200)
  assert.equal(f.owner.ghost, undefined); assert(f.owner.edit.dirty)
  assert.deepEqual(f.events, ['dirty', [{ op: 'block.move', id: 'a', track_id: 'v', at: 10 }]])
  assert.equal(f.changes.at(-1).dirty, true)
})

for (const boundary of ['zero-delta', 'reverse', 'pointerCancel', 'lostPointerCapture', 'frame', 'version', 'selection', 'handler', 'unmount']) test(`${boundary} cleans only gesture safety without reviving results`, async t => {
  const f = await fixture(t); await f.generate(); await f.emit('pointerDown')
  if (boundary !== 'zero-delta') await f.emit('pointerMove', 7, 200)
  assert(f.owner.ghost)
  if (boundary === 'zero-delta' || boundary === 'reverse') await f.emit('pointerUp', 7, 100)
  else if (boundary === 'pointerCancel' || boundary === 'lostPointerCapture') await f.emit(boundary)
  else if (boundary === 'frame') await f.render({ frame: 1 })
  else if (boundary === 'version') await f.render({ snapshot: { ...snapshot, content_version: 'new' } })
  else if (boundary === 'selection') await f.render({ selectionIntent: { items: [{ kind: 'block', address: block.address }], primary: { kind: 'block', address: block.address } } })
  else if (boundary === 'handler') await f.render({ onGesture: () => assert.fail('obsolete handler must not run') })
  else await act(async () => f.root.render(null))
  assert.equal(f.owner.ghost, undefined); assert.equal(f.owner.generated, undefined)
  assert.deepEqual(f.owner.edit, { dirty: false, busy: false, unknown: false }); assert.deepEqual(f.events, [])
})

test('fresh pointer and unrelated old tail cannot clear new gesture; stable owner rerenders do not cancel it', async t => {
  const f = await fixture(t); await f.emit('pointerDown', 1); await f.emit('pointerMove', 1, 200)
  const old = f.owner.ghost
  await f.emit('pointerDown', 2); await f.emit('pointerMove', 2, 200)
  const current = f.owner.ghost; assert.notEqual(current, old)
  await f.render({}); assert.equal(f.owner.ghost, current); assert(f.ghost())
  await f.emit('lostPointerCapture', 1); await f.emit('pointerCancel', 1); await f.emit('pointerUp', 1, 200)
  assert.equal(f.owner.ghost, current); assert(f.ghost()); assert.deepEqual(f.events, [])
  await f.emit('pointerCancel', 2); assert.equal(f.owner.ghost, undefined)
})

test('clean stage captures synchronously once; ghost aborts held reference before stage', async t => {
  const f = await fixture(t); await f.generate(); f.faults.holdReference = true
  await act(async () => { handler(f.button('将该帧'))(); assert.deepEqual(f.events, ['capture', 'reference']) })
  const signal = f.faults.referenceSignal; assert(!signal.aborted)
  await f.emit('pointerDown'); assert(signal.aborted)
  await act(async () => { f.releaseReference(); await Promise.resolve() })
  assert.deepEqual(f.events, ['capture', 'reference'])
})

test('ghost start aborts a render and an already pending public stage operation', async t => {
  const f = await fixture(t); await f.consent()
  await act(async () => handler(f.button('显式生成'))()); assert.deepEqual(f.events, ['render'])
  await f.emit('pointerDown'); assert(f.faults.renderSignal.aborted)
  await f.emit('pointerCancel'); await f.generate(); f.faults.holdStage = true
  await act(async () => { handler(f.button('将该帧'))(); await Promise.resolve() })
  assert.deepEqual(f.events, ['render', 'capture', 'reference', 'stage'])
  await f.emit('pointerDown'); assert(f.faults.stageSignal.aborted)
  await act(async () => { f.releaseStage(); await Promise.resolve() })
  assert.equal(f.owner.generated, undefined)
})
