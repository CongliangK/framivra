import test from 'node:test'
import assert from 'node:assert/strict'
import React, { act, useState } from 'react'
import { JSDOM } from 'jsdom'
import { FrameEpoch } from '../src/client/frame.mjs'
import { ProgramMonitor, RenderedPlayer } from '../src/client/monitor.mjs'

const h = React.createElement
const version = `sha256:${'a'.repeat(64)}`
const output = mediaId => ({ mediaId, label: `${mediaId}.mp4` })
const midpoint = (frame, num = 30, den = 1) => (frame + 0.5) * den / num

// Actual React DOM and browser event handlers; only decoding/clock advancement
// are adapted. These tests do not certify native browser decoding or seek accuracy.
async function fixture(t, Component = RenderedPlayer, initial = {}, { quantizeToMicroseconds = false } = {}) {
  const dom = new JSDOM('<div id="root"></div>', { url: 'http://monitor.invalid/' })
  const saved = Object.fromEntries(['window', 'document', 'IS_REACT_ACT_ENVIRONMENT', 'fetch'].map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]))
  globalThis.window = dom.window; globalThis.document = dom.window.document
  globalThis.IS_REACT_ACT_ENVIRONMENT = true
  const requests = []
  globalThis.fetch = (...args) => { requests.push(args); throw new Error('Monitor must not silently render/upload/send') }
  const states = new WeakMap(), nodes = [], writes = [], pauses = [], pins = [], cancelled = []
  let nextCallback = 0
  function state(node) {
    if (!states.has(node)) {
      states.set(node, { time: 0, duration: NaN, readyState: 0, paused: true, seeking: false, callbacks: new Map() })
      nodes.push(node)
    }
    return states.get(node)
  }
  const proto = dom.window.HTMLMediaElement.prototype
  for (const key of ['duration', 'readyState', 'paused', 'seeking']) Object.defineProperty(proto, key, { configurable: true, get() { return state(this)[key] } })
  Object.defineProperty(proto, 'currentTime', {
    configurable: true, get() { return state(this).time },
    set(value) { const time = Number(value); assert(Number.isFinite(time), 'media currentTime requires a finite double'); const s = state(this); writes.push({ node: this, time }); s.time = quantizeToMicroseconds ? Math.floor(time * 1e6) / 1e6 : time; s.seeking = true },
  })
  proto.pause = function () { state(this).paused = true; pauses.push(this) }
  proto.play = function () { state(this).paused = false; this.dispatchEvent(new dom.window.Event('play')); return Promise.resolve() }
  proto.requestVideoFrameCallback = function (callback) { const id = ++nextCallback; state(this).callbacks.set(id, callback); return id }
  proto.cancelVideoFrameCallback = function (id) { state(this).callbacks.delete(id); cancelled.push({ node: this, id }) }
  const { createRoot } = await import('react-dom/client'), { Simulate } = await import('react-dom/test-utils')
  const root = createRoot(document.getElementById('root')), owner = new FrameEpoch(), tab = new AbortController()
  let props = {
    owner, opened: { grant: 'grant-a', media: [output('final')], snapshot: { content_version: version },
      capabilities: { capabilities: { frame: { inline_png: { flag: '--include-png' } } } } },
    sessionId: 'session', frame: 0, fps: 30, duration: 300,
    tabSignal: tab.signal, call: () => assert.fail('No control request expected'), ...initial,
  }, refresh
  function Harness() {
    const [, update] = useState(0); refresh = () => update(value => value + 1)
    return h(Component, { ...props, onFrame: value => { pins.push(value); props = { ...props, frame: Number(value) }; refresh() } })
  }
  await act(async () => root.render(h(Harness)))
  const render = async patch => { props = { ...props, ...patch }; await act(async () => refresh()) }
  const emit = async (node, type) => act(async () => node.dispatchEvent(new dom.window.Event(type)))
  const metadata = async (node, duration = 10) => { Object.assign(state(node), { readyState: 1, duration }); await emit(node, 'loadedmetadata') }
  const settle = async node => { state(node).seeking = false; await emit(node, 'seeked'); await emit(node, 'timeupdate') }
  const nativeSeek = async (node, time) => { Object.assign(state(node), { time, seeking: true }); await emit(node, 'seeking'); await settle(node) }
  const decoded = async (node, mediaTime, currentTime = mediaTime) => {
    const s = state(node), entry = s.callbacks.entries().next().value
    assert(entry, 'the currently mounted video must own a decoded-frame callback')
    s.callbacks.delete(entry[0]); s.time = currentTime
    await act(async () => entry[1](100, { mediaTime }))
  }
  t.after(async () => {
    await act(async () => { tab.abort(); root.unmount() })
    dom.window.close()
    for (const [key, descriptor] of Object.entries(saved)) { if (descriptor) Object.defineProperty(globalThis, key, descriptor); else delete globalThis[key] }
  })
  return { dom, root, Simulate, owner, pins, writes, pauses, requests, cancelled, nodes, state, render, emit, metadata, settle, nativeSeek, decoded,
    video: () => document.querySelector('video'), frame: () => props.frame,
    click: async text => act(async () => [...document.querySelectorAll('button')].find(node => node.textContent === text).click()),
    change: async (node, value) => act(async () => Simulate.change(node, { target: { value } })),
  }
}

function visible(node) {
  for (let parent = node; parent; parent = parent.parentElement) if (parent.hidden || parent.style.display === 'none' || parent.style.visibility === 'hidden') return false
  return true // jsdom has no layout: only explicit DOM visibility is asserted.
}

test('program monitor switches one visible stage without auto rendering; live consent survives the mode round trip', async t => {
  const f = await fixture(t, ProgramMonitor, { frame: undefined, blank: true })
  const stages = () => [...document.querySelectorAll('.fv-program-stage')].filter(visible)
  assert.deepEqual(stages().map(node => node.dataset.mode), ['live'])
  const live = document.querySelector('.fv-frame-tools')
  const consent = live.querySelector('input[type="checkbox"]')
  await act(async () => f.Simulate.change(consent, { target: { checked: true } }))
  const before = f.owner.epoch
  await f.click('成片渲染')
  assert(f.owner.epoch > before, 'mode change invalidates any previous verified image')
  assert.deepEqual(f.pins, ['0'], 'selecting a mode with no fixed frame explicitly initializes the canonical cursor')
  assert.deepEqual(stages().map(node => node.dataset.mode), ['rendered'])
  assert.equal(live.hidden, true)
  await f.metadata(f.video()); await f.settle(f.video())
  await f.click('实时真预览')
  assert(document.querySelector('video') === null, "Expected absent DOM node: document.querySelector('video')")
  assert.deepEqual(stages().map(node => node.dataset.mode), ['live'])
  assert(document.querySelector('.fv-frame-tools') === live, "Expected same DOM node: document.querySelector('.fv-frame-tools')")
  assert.equal(consent.checked, true)
  await f.render({ frame: undefined, blank: false })
  const pinCount = f.pins.length
  await f.click('成片渲染')
  assert.equal(f.pins.length, pinCount, 'invalid non-blank input must not be silently replaced by frame zero')
  assert.deepEqual(stages().map(node => node.dataset.mode), ['rendered'])
  assert.deepEqual(f.requests, [])
})

test('storage suspension reaches live follow without hiding its stage or losing consent', async t => {
  const f = await fixture(t, ProgramMonitor)
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const inputs = () => [...document.querySelectorAll('.fv-frame-tools input')]
  const follow = () => inputs().find(node => node.parentElement.textContent.includes('随播放头实时生成'))
  await act(async () => inputs()[0].click())
  await act(async () => follow().click())
  await f.render({ suspended: true, frame: 10 })
  await act(async () => t.mock.timers.tick(1000))
  assert.deepEqual(f.requests, [])
  assert.equal(follow().checked, true)
  assert.equal(visible(document.querySelector('.fv-frame-tools')), true)
})

test('canonical integer frame seeks its rational project-frame midpoint, pauses, and latest rapid seek wins without feedback', async t => {
  const f = await fixture(t, RenderedPlayer, { frame: 30, fps: '30000/1001', duration: 300 })
  const node = f.video()
  await f.metadata(node)
  assert.equal(f.writes.at(-1).time, midpoint(30, 30000, 1001))
  await f.settle(node)
  assert.deepEqual(f.pins, [], 'programmatic seek completion does not write the canonical cursor back')
  await act(async () => node.play())
  await f.render({ frame: 45 })
  assert.equal(node.paused, true, 'timeline scrubbing interrupts playback')
  assert.equal(f.writes.at(-1).time, midpoint(45, 30000, 1001))
  await f.render({ frame: 75 })
  assert.equal(f.writes.at(-1).time, midpoint(75, 30000, 1001))
  // Decoder observations during the outstanding seek cannot replace its target.
  Object.assign(f.state(node), { paused: false })
  await f.decoded(node, 0.2)
  assert.equal(f.frame(), 75)
  assert.deepEqual(f.pins, [])
  // The browser settles at the latest requested target, not a prior request.
  Object.assign(f.state(node), { paused: true, time: midpoint(75, 30000, 1001) })
  await f.settle(node)
  assert.deepEqual(f.pins, [])
  assert.equal(f.writes.length, 3)
  assert(f.pauses.length >= 3)
})

test('microsecond-quantized seeks retain the requested frame, while decoded PTS quantization selects the nearest project frame', async t => {
  const f = await fixture(t, RenderedPlayer, { frame: 29 }, { quantizeToMicroseconds: true })
  const node = f.video()
  await f.metadata(node, 20)
  for (const [fps, num, den] of [[30, 30, 1], ['24000/1001', 24000, 1001], ['30000/1001', 30000, 1001], ['120000/1001', 120000, 1001]]) {
    for (const frame of [29, 59, 299]) {
      await f.render({ frame, fps })
      const target = midpoint(frame, num, den)
      assert(Math.abs(f.writes.at(-1).time - target) <= Number.EPSILON * Math.max(1, target), 'requested double equals the rational midpoint within one representational ULP')
      assert.equal(node.currentTime, Math.floor(target * 1e6) / 1e6, 'simulate browser truncation rather than echoing the requested double')
      assert(node.currentTime >= frame * den / num && node.currentTime < (frame + 1) * den / num, 'quantized midpoint remains inside the requested project frame')
      const before = f.pins.length
      await f.settle(node)
      assert.equal(f.frame(), frame)
      assert.equal(f.pins.length, before, 'programmatic seeked/timeupdate must never decrement a canonical frame')
    }
    await f.render({ frame: 0 }); await f.settle(node)
    await act(async () => node.play())
    const writes = f.writes.length
    for (const frame of [1, 2, 29]) {
      const pts = Math.floor(frame * den / num * 1e6) / 1e6
      await f.decoded(node, pts)
      assert.equal(f.frame(), frame, `decoded PTS ${pts} at ${fps} must not report the previous project frame`)
      assert.equal(f.writes.length, writes, 'decoded PTS reporting never reverse-seeks')
      assert.equal(node.paused, false)
    }
  }
})

test('native seek pins the containing frame; playing decoded mediaTime advances the canonical cursor without reverse seek or pause', async t => {
  const f = await fixture(t)
  const node = f.video()
  await f.metadata(node); await f.settle(node)
  const initialWrites = f.writes.length
  await f.nativeSeek(node, 1.52)
  assert.deepEqual(f.pins, ['45'])
  assert.equal(f.writes.length, initialWrites, 'native controls must not bounce into a programmatic seek')
  await act(async () => node.play())
  const pauseCount = f.pauses.length
  // currentTime can lead the displayed decoded frame. rVFC's mediaTime owns playback reporting.
  await f.decoded(node, 2.01, 2.2)
  assert.equal(f.frame(), 60)
  assert.equal(node.paused, false)
  assert.equal(f.writes.length, initialWrites)
  assert.equal(f.pauses.length, pauseCount)
  await f.emit(node, 'timeupdate')
  assert.equal(f.frame(), 60, 'timeupdate must not race the decoded-frame clock while playing')
  await f.decoded(node, 2.04, 2.25)
  assert.equal(f.frame(), 61)
  assert.deepEqual(f.pins, ['45', '60', '61'])
  await act(async () => f.root.render(null))
  assert.equal(f.state(node).callbacks.size, 0, 'unmount cancels the owned decoded-frame loop')
  assert.equal(node.paused, true)
})

test('empty or invalid frame hides and stops playback; a recovered valid cursor still follows decoded frames', async t => {
  const f = await fixture(t, RenderedPlayer, { frame: 30 })
  let node = f.video()
  await f.metadata(node); await f.settle(node)
  for (const frame of [undefined, NaN, -1, 1.5]) {
    const retired = node
    await act(async () => retired.play())
    const before = f.pins.length, seekCount = f.writes.length
    await f.render({ frame })
    assert(!f.video() || !visible(f.video()), 'an invalid cursor cannot leave an old picture visible')
    assert.equal(retired.paused, true, 'invalid canonical input stops playback')
    if (f.state(retired).callbacks.size) await f.decoded(retired, 8)
    assert.equal(f.pins.length, before, 'decoder callbacks cannot replace invalid input with an implicit old frame')
    assert.equal(f.writes.length, seekCount, 'invalid input cannot seek using an old canonical frame')
    await f.render({ frame: 60 })
    node = f.video(); assert(node)
    if (node !== retired) await f.metadata(node)
    await f.settle(node)
    assert.equal(f.writes.at(-1).time, midpoint(60))
    await act(async () => node.play())
    await f.decoded(node, 2.2)
    assert.equal(f.frame(), 66, 'playback must still synchronize after invalid-to-valid recovery')
  }
})

test('output switch and new grant rebind media to the canonical cursor; a short output never clamps the project frame', async t => {
  const opened = { grant: 'grant-a', media: [output('final'), output('draft')], snapshot: { content_version: version } }
  const f = await fixture(t, RenderedPlayer, { opened, frame: 150 })
  const node = f.video()
  await f.metadata(node, 10); await f.settle(node)
  await act(async () => node.play())
  await f.change(document.querySelector('select'), 'draft')
  assert.equal(node.paused, true)
  assert.equal(new URL(f.video().src).searchParams.get('mediaId'), 'draft')
  assert.equal(f.writes.at(-1).time, midpoint(150))
  await f.metadata(f.video(), 2)
  assert.equal(f.frame(), 150, 'the authoritative project position remains outside a short output')
  assert.deepEqual(f.pins, [])
  assert.equal(f.video().controls, false)
  assert.equal(f.video().style.visibility, 'hidden')
  assert(document.body.textContent.includes('该工程位置没有对应的成片画面'))
  const shortWrites = f.writes.length
  await f.render({ frame: 30 }); await f.settle(f.video())
  assert.equal(f.writes.length, shortWrites + 1)
  assert.equal(f.writes.at(-1).time, midpoint(30))
  assert.equal(f.video().controls, true)
  await f.render({ opened: { ...opened, grant: 'grant-b' } })
  assert.equal(new URL(f.video().src).searchParams.get('grant'), 'grant-b')
  await f.metadata(f.video(), 10); await f.settle(f.video())
  assert.equal(f.writes.at(-1).time, midpoint(30))
  assert.deepEqual(f.pins, [])
  await act(async () => f.video().play())
  await f.decoded(f.video(), 1.5)
  assert.equal(f.frame(), 45)
})
