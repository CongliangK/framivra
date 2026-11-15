import test from 'node:test'
import assert from 'node:assert/strict'
import React, { act } from 'react'
import { JSDOM } from 'jsdom'
import { VisualTimeline } from '../src/client/timeline.mjs'
import { emptySelection, transitionSelection } from '../src/client/selection.mjs'

// Actual source handlers + React effects. Geometry/capture and events are explicit
// DOM adapters, NOT trusted native browser or kernel evidence.
const tracks = [{ family: 'video', track_index: 0, address: '/video_tracks/0', data: { id: 'v' } }]
const blocks = [0, 1].map(i => ({ family: 'video', track_index: 0, block_index: i, address: `/video_tracks/0/clips/${i}`, range: [i * 40, i * 40 + 30], data: { id: i ? 'b' : 'a' } }))
const result = { tracks, blocks }
const snapshot = { content_version: 'old', document: { project: { duration_frames: 100 } } }
const item = i => ({ kind: 'block', address: blocks[i].address })

async function fixture(t) {
  const dom = new JSDOM('<div id="root"></div>', { url: 'http://fixture/' })
  globalThis.window = dom.window; globalThis.document = dom.window.document; globalThis.IS_REACT_ACT_ENVIRONMENT = true
  const { createRoot } = await import('react-dom/client'), { Simulate } = await import('react-dom/test-utils')
  const root = createRoot(document.getElementById('root')), batches = [], captures = [], releases = []
  let props = { snapshot, result, selectionIntent: emptySelection(), frame: 0, onGesture: ops => batches.push(ops) }
  const render = async patch => { props = { ...props, ...patch }; await act(async () => root.render(React.createElement(VisualTimeline, { ...props, activeSelection: props.selectionIntent.primary, onSelect: select }))) }
  function select(value, event) { props = { ...props, selectionIntent: transitionSelection(props.selectionIntent, value, event, result) }; root.render(React.createElement(VisualTimeline, { ...props, activeSelection: props.selectionIntent.primary, onSelect: select })) }
  const clip = i => document.querySelectorAll('.fv-clip')[i]
  function geometry() {
    document.querySelector('.fv-ruler').getBoundingClientRect = () => ({ left: 0, width: 1000 })
    document.querySelector('.fv-track').getBoundingClientRect = () => ({ top: 0, bottom: 100 })
    for (const node of document.querySelectorAll('button')) {
      const held = new Set()
      node.setPointerCapture = id => { held.add(id); captures.push([node, id]) }
      node.hasPointerCapture = id => held.has(id)
      node.releasePointerCapture = id => { assert(held.has(id)); held.delete(id); releases.push([node, id]) }
    }
  }
  const event = (pointerId = 7, extra = {}) => ({ pointerId, button: 0, clientX: 100, clientY: 15, ...extra })
  const emit = async (type, node, data) => act(async () => Simulate[type](node, data))
  const down = async (i = 0, id = 7) => emit('pointerDown', clip(i), event(id))
  const move = async (i = 0, id = 7) => emit('pointerMove', clip(i), event(id, { clientX: 200 }))
  const up = async (i = 0, id = 7, x = 200) => emit('pointerUp', clip(i), event(id, { clientX: x }))
  // React click is backed by native PointerEvent in Chromium; React's MouseEvent
  // facade does not itself expose pointerId. Keyboard clicks have detail=0.
  const click = async (i = 0, id = 7, extra = {}) => emit('click', clip(i), { detail: 1, nativeEvent: { pointerId: id }, ...extra })
  await render({}); geometry()
  t.after(async () => { await act(async () => root.unmount()); dom.window.close(); delete globalThis.window; delete globalThis.document })
  return { root, render, down, move, up, click, emit, clip, event, batches, captures, releases, primary: () => props.selectionIntent.primary, items: () => props.selectionIntent.items, ghost: () => document.querySelector('[aria-label="未保存拖动 ghost"]') }
}

test('canceled pointer tail cannot undo explicit B; keyboard activation and fresh pointer still work', async t => {
  const f = await fixture(t)
  await f.click(); await f.down(); await f.move(); assert(f.ghost())
  await f.render({ selectionIntent: transitionSelection(emptySelection(), item(1), {}, result) })
  assert.equal(f.ghost(), null)
  await f.up(); await f.click()
  assert.deepEqual(f.primary(), item(1)); assert.deepEqual(f.batches, [])
  await f.click(0, -1, { detail: 0 }); assert.deepEqual(f.primary(), item(0))
  await f.down(1); await f.up(1, 7, 100); await f.click(1)
  assert.deepEqual(f.primary(), item(1)); assert.deepEqual(f.batches, [])
})

for (const boundary of ['frame', 'revision', 'handler']) test(`${boundary} invalidation consumes only that pointer sequence`, async t => {
  const f = await fixture(t)
  await f.click(1); await f.down(); await f.move()
  const patch = boundary === 'frame' ? { frame: 1 } : boundary === 'revision' ? { snapshot: { ...snapshot, content_version: 'new' } } : { onGesture: ops => f.batches.push(ops) }
  await f.render(patch); assert.equal(f.ghost(), null)
  await f.up(); await f.click(); assert.deepEqual(f.primary(), item(1)); assert.deepEqual(f.batches, [])
  await f.down(); await f.up(0, 7, 100); await f.click(); assert.deepEqual(f.primary(), item(0))
})

for (const type of ['pointerCancel', 'lostPointerCapture']) test(`${type} cancels ghost without poisoning keyboard or next pointer`, async t => {
  const f = await fixture(t)
  await f.click(1); await f.down(); await f.move()
  await f.emit(type, f.clip(0), f.event())
  assert.equal(f.ghost(), null); await f.up(); await f.click()
  assert.deepEqual(f.primary(), item(1)); assert.deepEqual(f.batches, [])
  await f.click(0, -1, { detail: 0 }); assert.deepEqual(f.primary(), item(0))
  await f.down(1); await f.up(1, 7, 100); await f.click(1); assert.deepEqual(f.primary(), item(1))
})

test('completed drag queues exactly once; duplicate end/lost capture and click do not collapse multi', async t => {
  const f = await fixture(t)
  await f.click(); await f.click(1, 7, { ctrlKey: true }); assert.equal(f.items().length, 2)
  await f.down(); await f.move(); await f.up(); await f.up()
  await f.emit('lostPointerCapture', f.clip(0), f.event()); await f.click()
  assert.deepEqual(f.batches, [[{ op: 'block.move', id: 'a', track_id: 'v', at: 10 }, { op: 'block.move', id: 'b', track_id: 'v', at: 50 }]])
  assert.equal(f.items().length, 2)
})

test('unrelated pointer end cannot clear/release new gesture, unmount releases its capture', async t => {
  const f = await fixture(t)
  await f.down(0, 1); await f.move(0, 1); await f.render({ frame: 1 })
  await f.down(1, 2); await f.move(1, 2)
  await f.up(0, 1); await f.emit('lostPointerCapture', f.clip(0), f.event(1))
  assert(f.ghost()); assert.deepEqual(f.batches, [])
  await act(async () => f.root.render(null))
  assert.equal(f.releases.filter(([, id]) => id === 2).length, 1)
})
