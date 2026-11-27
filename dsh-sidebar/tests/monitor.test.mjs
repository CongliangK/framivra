import test from 'node:test'
import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import React, { act, useState } from 'react'
import { JSDOM } from 'jsdom'
import { FrameEpoch } from '../src/client/frame.mjs'
import { ProgramMonitor, RenderedPlayer, useWindowMonitor } from '../src/client/monitor.mjs'
import { VisualTimeline } from '../src/client/timeline.mjs'
import { usePreviewSessionAccess } from '../src/client/preview-session-access.mjs'

const h = React.createElement
const version = `sha256:${'a'.repeat(64)}`
const output = mediaId => ({ mediaId, label: `${mediaId}.mp4` })
const midpoint = (frame, num = 30, den = 1) => (frame + 0.5) * den / num

// Actual React DOM and browser event handlers; only decoding/clock advancement
// are adapted. These tests do not certify native browser decoding or seek accuracy.
async function fixture(t, Component = RenderedPlayer, initial = {}, { quantizeToMicroseconds = false, mockBlobURLs = false, deferNativePins = false } = {}) {
  const dom = new JSDOM('<div id="root"></div>', { url: 'http://monitor.invalid/' })
  const saved = Object.fromEntries(['window', 'document', 'IS_REACT_ACT_ENVIRONMENT', 'fetch', 'URL', 'Image', 'requestAnimationFrame', 'cancelAnimationFrame'].map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]))
  globalThis.window = dom.window; globalThis.document = dom.window.document
  globalThis.IS_REACT_ACT_ENVIRONMENT = true
  const requests = [], urls = [], revoked = []
  if (mockBlobURLs) globalThis.URL = class extends URL {
    static createObjectURL(blob) { const url = `blob:http://monitor.invalid/${urls.length}`; urls.push({ blob, url }); return url }
    static revokeObjectURL(url) { revoked.push(url) }
  }
  globalThis.fetch = (...args) => { requests.push(args); throw new Error('Monitor must not silently render/upload/send') }
  const states = new WeakMap(), nodes = [], writes = [], plays = [], pauses = [], pins = [], cancelled = []
  let nextCallback = 0
  function state(node) {
    if (!states.has(node)) {
      states.set(node, { time: 0, duration: NaN, readyState: 0, paused: true, seeking: false, ended: false, callbacks: new Map() })
      nodes.push(node)
    }
    return states.get(node)
  }
  const proto = dom.window.HTMLMediaElement.prototype
  for (const key of ['duration', 'readyState', 'paused', 'seeking', 'ended']) Object.defineProperty(proto, key, { configurable: true, get() { return state(this)[key] } })
  Object.defineProperty(proto, 'currentTime', {
    configurable: true, get() { return state(this).time },
    set(value) { const time = Number(value); assert(Number.isFinite(time), 'media currentTime requires a finite double'); const s = state(this); writes.push({ node: this, time }); s.time = quantizeToMicroseconds ? Math.floor(time * 1e6) / 1e6 : time; s.seeking = true },
  })
  Object.defineProperty(proto, 'currentSrc', { configurable: true, get() {
    const media = state(this)
    return media.currentSrc ?? (media.readyState > 0 ? this.getAttribute('src') ?? '' : '')
  } })
  Object.defineProperty(proto, 'seekable', { configurable: true, get() {
    const media = state(this), ranges = media.seekable ?? (Number.isFinite(media.duration) ? [[0, media.duration]] : [])
    return { length: ranges.length, start: i => ranges[i][0], end: i => ranges[i][1] }
  } })
  proto.pause = function () { state(this).paused = true; pauses.push(this) }
  proto.play = function () { plays.push(this); state(this).paused = false; this.dispatchEvent(new dom.window.Event('play')); return Promise.resolve() }
  proto.load = function () {}
  for (const key of ['videoWidth', 'videoHeight']) Object.defineProperty(dom.window.HTMLVideoElement.prototype, key, { configurable: true, get() { return state(this)[key] ?? 0 } })
  proto.requestVideoFrameCallback = function (callback) { const id = ++nextCallback; state(this).callbacks.set(id, callback); return id }
  proto.cancelVideoFrameCallback = function (id) { state(this).callbacks.delete(id); cancelled.push({ node: this, id }) }
  const { createRoot } = await import('react-dom/client'), { Simulate } = await import('react-dom/test-utils')
  const root = createRoot(document.getElementById('root')), owner = new FrameEpoch(), tab = new AbortController()
  let props = {
    owner, opened: { grant: 'grant-a', media: [output('final')], snapshot: { content_version: version },
      capabilities: { capabilities: { frame: { inline_png: { flag: '--include-png' } } } } },
    sessionId: 'session', frame: 0, fps: 30, duration: 300,
    tabSignal: tab.signal, call: () => assert.fail('No control request expected'), ...initial,
  }, refresh, pendingPin
  function pin(value, origin) {
    if (!owner.pin(Number(value), origin)) return
    pins.push(value)
    const commit = () => { props = { ...props, frame: Number(value) }; refresh() }
    if (origin && deferNativePins) pendingPin = commit
    else commit()
  }
  function Harness() {
    const [, update] = useState(0); refresh = () => update(value => value + 1)
    return h(Component, { ...props, onFrame: pin })
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
  const button = text => [...document.querySelectorAll('button')].find(node => node.textContent === text)
  return { dom, root, Simulate, owner, tab, pins, writes, plays, pauses, requests, cancelled, nodes, state, render, emit, metadata, settle, nativeSeek, decoded, urls, revoked, button,
    video: () => document.querySelector('video'), frame: () => props.frame,
    pinNow: pin, pin: async value => act(async () => pin(String(value))),
    flushNativePin: async () => act(async () => { const commit = pendingPin; pendingPin = undefined; commit() }),
    click: async text => act(async () => button(text).click()),
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
  await f.click('固定帧预览')
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
  const follow = () => inputs().find(node => node.parentElement.textContent.includes('随播放头更新固定帧'))
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

const windowOpened = {
  grant: 'grant-window', media: [output('final')], snapshot: { content_version: version },
  host: { ops: ['preview-window'] }, capabilities: { capabilities: {
    frame: { inline_png: { flag: '--include-png' } },
    preview_window: { schema: 'framivra.timeline.preview-window/v1', inline_media: true, prepare_inputs: true },
  } },
}
const windowPayload = new Uint8Array([1, 8, 2, 7, 1, 8])
const windowReply = (request, end, options = {}) => new Response(options.body ?? windowPayload, { headers: {
  'Content-Type': 'video/mp4', 'X-Framivra-Version': request.contentVersion,
  'X-Framivra-Start': String(request.frame), 'X-Framivra-End': String(end),
  'X-Framivra-SHA256': createHash('sha256').update(windowPayload).digest('hex'), ...options.headers,
} })
const reaped = () => Response.json({ ok: true, reaped: true })
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done }); return { promise, resolve } }
const windowInputs = () => [...document.querySelectorAll('[aria-label="单窗口音画监看"] input')]
const generateWindow = '生成当前音画窗口'
const toggle = async input => act(async () => input.click())
function serveWindows(f, responder) {
  globalThis.fetch = async (path, init) => {
    assert.equal(path, '/api/framivra/window')
    assert.equal(init.method, 'POST')
    const request = JSON.parse(init.body)
    f.requests.push({ request, signal: init.signal })
    return responder(request, init.signal)
  }
}
async function untilWindow(predicate) {
  for (let attempts = 0; attempts < 100 && !predicate(); attempts++) await act(() => new Promise(done => setTimeout(done, 5)))
  assert(predicate(), 'window DOM state did not settle')
}

test('preview Play waits for verified decode, handles browser refusal, and ignores a superseded rejection', async t => {
  const f = await fixture(t, ProgramMonitor, { opened: windowOpened, timeline: 'timeline.json', frame: 25 }, { mockBlobURLs: true })
  serveWindows(f, request => request.op === 'render' ? windowReply(request, request.frame + 30) : reaped())
  await f.click('播放预览')
  for (const input of windowInputs()) await toggle(input)
  await f.click('播放预览')
  await untilWindow(f.video)
  const node = f.video(), denied = deferred(), nativePlay = node.play
  let attempts = 0
  node.play = () => { attempts++; return denied.promise.then(() => { throw new Error('NotAllowedError') }) }
  assert.equal(attempts, 0, 'receiving verified bytes cannot play before decoder qualification')
  Object.assign(f.state(node), { videoWidth: 1920, videoHeight: 1080, duration: 1, readyState: 3 })
  await f.emit(node, 'loadeddata')
  assert.equal(attempts, 1)
  assert(f.button('取消待播放'))
  assert.equal(node.muted, false, 'no silent autoplay fallback')
  await act(async () => denied.resolve())
  assert.match(document.body.textContent, /浏览器未开始播放；请手动播放/)
  assert(node.paused && node.controls)
  node.play = nativePlay
  await f.click('播放预览')
  assert.deepEqual(f.plays, [node])
  assert.doesNotMatch(document.body.textContent, /浏览器未开始播放/)
  await f.click('暂停预览')
  assert(node.paused)

  const stale = deferred()
  node.play = () => stale.promise.then(() => { throw new Error('obsolete attempt') })
  await f.click('播放预览')
  await f.pin(26)
  await act(async () => stale.resolve())
  assert.equal(f.video(), null)
  assert.equal(f.frame(), 26)
  assert.doesNotMatch(document.body.textContent, /浏览器未开始播放/)
  assert.deepEqual(f.requests.map(({ request }) => request.op), ['render', 'cancel'], 'no denial or pause retries the render')
})

test('audio window requires both capabilities and both click-time permissions; success uses one decoded Blob and shares the frame without echo seeking', async t => {
  const f = await fixture(t, ProgramMonitor, { opened: { ...windowOpened, host: { ops: [] } }, frame: 290 }, { mockBlobURLs: true, deferNativePins: true })
  assert.equal(f.button('音画窗口'), undefined, 'kernel advertisement alone is insufficient')
  await f.render({ opened: { ...windowOpened, capabilities: { capabilities: { frame: windowOpened.capabilities.capabilities.frame } } } })
  assert.equal(f.button('音画窗口'), undefined, 'Host advertisement alone is insufficient')
  await f.render({ opened: windowOpened })
  await f.click('音画窗口')
  assert.equal(f.video(), null)
  assert.equal(f.button(generateWindow).disabled, true)
  assert.deepEqual(f.requests, [])
  await toggle(windowInputs()[0]); assert.equal(f.button(generateWindow).disabled, true)
  await toggle(windowInputs()[1]); assert.equal(f.button(generateWindow).disabled, false)
  assert.deepEqual(f.requests, [])
  const stop = deferred()
  serveWindows(f, request => request.op === 'render' ? windowReply(request, Math.min(request.frame + 30, 300)) : stop.promise)
  await f.click(generateWindow)
  const { request, signal } = f.requests[0]
  assert.match(request.operationId, /^[0-9a-f-]{36}$/)
  assert.deepEqual({ ...request, operationId: null }, { sessionId: 'session', grant: windowOpened.grant, op: 'render', operationId: null,
    frame: 290, contentVersion: version, renderConsent: 'cache/preview-only', prepareInputs: true })
  await untilWindow(f.video)
  const node = f.video()
  assert.equal(node.controls, false)
  assert.equal(visible(node), false)
  assert.equal(node.autoplay, false)
  assert.equal(f.urls.length, 1)
  assert.equal(node.getAttribute('src'), f.urls[0].url)
  assert.equal(f.urls[0].blob.type, 'video/mp4')
  Object.assign(f.state(node), { videoWidth: 1920, videoHeight: 1080, duration: 10 / 30, readyState: 2 })
  await f.emit(node, 'loadeddata')
  assert(node.controls && visible(node))
  await act(async () => node.play())
  await f.decoded(node, 0.1)
  assert.equal(f.frame(), 290, 'hold the project commit after the native callback')
  await f.render({ selectionDisplay: 'unrelated committed render' })
  assert.equal(f.video(), node, 'older committed frame must not replace this playing element')
  assert.equal(node.getAttribute('src'), f.urls[0].url)
  assert.equal(node.paused, false)
  await f.flushNativePin()
  assert.equal(f.frame(), 293)
  await f.nativeSeek(node, 0.22)
  await f.flushNativePin()
  assert.deepEqual(f.pins, ['293', '296'])
  assert.deepEqual(f.writes, [], 'native playback and native seek do not assign currentTime')
  assert.equal(f.requests.length, 1)
  await f.pin(45)
  assert.equal(signal.aborted, true)
  assert.equal(node.paused, true)
  assert.equal(node.muted, true)
  assert.equal(f.video(), null)
  assert.equal(node.hasAttribute('src'), false)
  assert.deepEqual(f.revoked, [f.urls[0].url])
  assert.equal(f.state(node).callbacks.size, 0)
  assert.deepEqual(f.requests[1].request, { sessionId: 'session', grant: windowOpened.grant, op: 'cancel', operationId: request.operationId })
  assert.equal(f.requests[1].signal.aborted, false)
  await f.click(generateWindow)
  assert.equal(f.requests.length, 2, 'no successor before confirmed reap')
  await act(async () => stop.resolve(reaped()))
  await untilWindow(() => !f.button(generateWindow).disabled)
  assert.equal(f.requests.length, 2, 'no automatic continuation')
  await f.click(generateWindow)
  await untilWindow(f.video)
  await f.emit(f.video(), 'error')
  assert.equal(f.video(), null)
  assert.deepEqual(f.revoked, f.urls.map(entry => entry.url))
  assert.equal(f.requests[3].request.operationId, f.requests[2].request.operationId)
})

const archiveOpened = { ...windowOpened, previewSession: { capability: 'preview-session', version: 1, ownerId: 'owner', formalArchive: { version: 1 } },
  capabilities: { capabilities: { ...windowOpened.capabilities.capabilities,
    preview_window: { ...windowOpened.capabilities.capabilities.preview_window, formal_archive: { version: 1 } } } } }

test('qualified archive is a default-off one-shot, plays in the window video and never produces live coverage', async t => {
  const opened = archiveOpened
  const f = await fixture(t, ProgramMonitor, { opened: windowOpened, timeline: 'timeline.json', frame: 25 }, { mockBlobURLs: true })
  const read = '读取一次已核归档 A/V', stop = deferred(), payload = deferred()
  serveWindows(f, request => request.op === 'formal-archive-read' ? payload.promise : stop.promise)
  await f.click('音画窗口')
  assert.equal(f.button(read), undefined)
  await f.render({ opened, sessionControls: { pending: true } })
  assert.equal(f.button(read).disabled, true)
  await f.render({ sessionControls: undefined })
  await f.click('播放预览')
  assert.deepEqual(f.requests, [], 'ordinary Play cannot select an available archive')
  await f.click(read)
  const { request, signal } = f.requests[0], { operationId } = request
  assert.deepEqual(request, { op: 'formal-archive-read', grant: opened.grant, ownerId: 'owner', sessionId: 'session', operationId,
    contentVersion: version, frame: 25 })
  assert.equal(f.button(read).disabled, true)
  const monitor = f.owner.previewWindow
  const coverage = () => monitor.coverage({ project: 'timeline.json', grant: opened.grant, sessionId: 'session', contentVersion: version,
    fps: 30, duration: 300, tabSignal: f.tab.signal }, monitor.state)
  assert.deepEqual(coverage(), [])
  await act(async () => payload.resolve(windowReply(request, 85, { headers: {
    'Content-Length': String(windowPayload.length), 'X-Framivra-Media-Kind': 'approved-formal-archive/v1',
    'X-Framivra-Operation-Id': operationId, 'X-Framivra-FPS': '30/1',
  } })))
  await untilWindow(f.video)
  const node = f.video()
  assert.equal(node.controls, false)
  Object.assign(f.state(node), { videoWidth: 1920, videoHeight: 1080, duration: 2, readyState: 3 })
  await f.emit(node, 'canplay')
  await f.emit(node, 'loadeddata')
  assert(node.controls && visible(node))
  assert.equal(node.getAttribute('aria-label'), '已核归档 A/V [25, 85)')
  assert.equal(f.button('播放预览').disabled, true, 'archive playback stays with native controls')
  assert.equal(node.autoplay, false)
  assert.equal(node.muted, false)
  await act(async () => node.play())
  await f.decoded(node, 1)
  assert.equal(f.frame(), 55)
  assert.equal(f.requests.length, 1, 'native play does not prefetch')
  assert.deepEqual(coverage(), [])
  await f.pin(65)
  assert.equal(signal.aborted, true)
  assert.equal(node.paused, true)
  assert.equal(node.hasAttribute('src'), false)
  assert.equal(f.video(), null)
  assert.deepEqual(f.revoked, [f.urls[0].url])
  assert.deepEqual(f.requests[1].request, { op: 'formal-archive-cancel', grant: opened.grant, ownerId: 'owner', sessionId: 'session', operationId })
  assert.equal(f.button(read).disabled, true)
  await act(async () => stop.resolve(Response.json({ ok: true, reaped: true, operationId, ownerId: 'owner', openGrant: opened.grant })))
  await untilWindow(() => !f.button(read).disabled)
  assert.equal(f.requests.length, 2, 'settlement never re-reads or renders')
})

test('archive refusals show a local Chinese diagnosis through exact cancellation and retry', async t => {
  const f = await fixture(t, ProgramMonitor, { opened: archiveOpened, timeline: 'timeline.json', frame: 25 }, { mockBlobURLs: true })
  const read = '读取一次已核归档 A/V', retry = '重试回收归档读取'
  const status = () => document.querySelector('[aria-label="单窗口音画监看"] [role="status"]')?.textContent ?? ''
  const cases = [
    ['ARCHIVE_SELECTOR_MISMATCH', /项目、起点或保存版本与归档批准不符/],
    ['ARCHIVE_INPUTS_MISMATCH', /无法核对工程声明的输入/],
    ['ARCHIVE_UNAVAILABLE', /无法复核归档批准、媒体或部署/],
    ['CLI_FAILED', /CLI_FAILED: 未取得该精确点的已核归档/, 502],
  ]
  let failure, cancel
  serveWindows(f, request => request.op === 'formal-archive-read'
    ? Response.json({ ok: false, error: failure[0], message: 'DO_NOT_SHOW' }, { status: failure[2] ?? 409 }) : cancel.promise)
  await f.click('音画窗口')
  for (const entry of cases) {
    failure = entry; cancel = deferred()
    await f.click(read)
    await untilWindow(() => /正在等待宿主确认回收/.test(status()))
    assert.match(status(), entry[1])
    const { operationId } = f.requests.at(-1).request
    const receipt = Response.json({ ok: true, reaped: true, operationId, ownerId: 'owner', openGrant: archiveOpened.grant })
    if (entry[0] === 'ARCHIVE_SELECTOR_MISMATCH') {
      await act(async () => cancel.resolve(Response.json({ ok: false }, { status: 502 })))
      await untilWindow(() => f.button(retry))
      assert.match(status(), entry[1])
      cancel = deferred()
      await f.click(retry)
      assert.equal(f.requests.at(-1).request.operationId, operationId)
    }
    await act(async () => cancel.resolve(receipt))
    await untilWindow(() => !f.button(read).disabled)
    assert.match(status(), entry[1])
    assert.match(status(), /宿主已确认回收/)
    assert.doesNotMatch(status(), /窗口已取消|DO_NOT_SHOW/)
  }
  assert.equal(f.video(), null)
  assert.deepEqual(f.urls, [])
  assert.deepEqual(f.requests.map(({ request }) => request.op), [
    'formal-archive-read', 'formal-archive-cancel', 'formal-archive-cancel',
    ...Array.from({ length: 3 }, () => ['formal-archive-read', 'formal-archive-cancel']).flat(),
  ])
})

test('a superseded response installs no media; unconfirmed cancel stays blocked after grant change until explicit retry', async t => {
  const f = await fixture(t, ProgramMonitor, { opened: windowOpened, frame: 25 }, { mockBlobURLs: true })
  const render = deferred(), firstStop = deferred()
  let cancels = 0
  serveWindows(f, request => request.op === 'render' ? (request.grant === windowOpened.grant ? render.promise : windowReply(request, 55))
    : ++cancels === 1 ? firstStop.promise : reaped())
  await f.click('音画窗口')
  for (const input of windowInputs()) await toggle(input)
  await f.click(generateWindow)
  await act(async () => f.owner.editChanged({ busy: true }))
  await f.render({ opened: { ...windowOpened, grant: 'replacement', snapshot: { content_version: `sha256:${'b'.repeat(64)}` } } })
  await act(async () => f.owner.editChanged({ busy: false }))
  for (const input of windowInputs()) await toggle(input)
  await act(async () => render.resolve(windowReply(f.requests[0].request, 55))) // transport ignored the old abort
  await act(async () => firstStop.resolve(Response.json({ ok: false, error: 'BAD_GRANT' }, { status: 403 })))
  await untilWindow(() => f.button('重试回收音画窗口'))
  assert.equal(f.button(generateWindow).disabled, true)
  assert.equal(f.urls.length, 0)
  assert.equal(f.video(), null)
  await f.click('重试回收音画窗口')
  await untilWindow(() => !f.button(generateWindow).disabled)
  const oldRequest = f.requests[0].request
  assert.deepEqual(f.requests[1].request, { sessionId: oldRequest.sessionId, grant: oldRequest.grant, op: 'cancel', operationId: oldRequest.operationId })
  assert.deepEqual(f.requests[1].request, f.requests[2].request, 'retry is bound to the old operation, not the new grant')
  await f.click(generateWindow)
  await untilWindow(f.video)
  assert.equal(f.requests.length, 4)
  assert.equal(f.requests[3].request.grant, 'replacement')
  assert.notEqual(f.requests[3].request.operationId, oldRequest.operationId)
  assert.equal(f.requests[3].signal.aborted, false, 'old cleanup must not abort the replacement request')
})

test('rejects unknown window bounds, bad checksum and oversized stream before creating media', async t => {
  const f = await fixture(t, ProgramMonitor, { opened: windowOpened, frame: 25 }, { mockBlobURLs: true })
  const responses = [request => windowReply(request, 56), request => windowReply(request, 55, { headers: { 'X-Framivra-SHA256': '0'.repeat(64) } }),
    request => windowReply(request, 55, { body: new ReadableStream({ start: stream => stream.enqueue(new Uint8Array(5 * 1024 * 1024 + 1)) }) }),
    request => windowReply(request, 55, { headers: { 'X-Framivra-Program-Audio': '1' } }),
    () => new Response('INTERNAL_SERVER_SENTINEL', { status: 502 })]
  serveWindows(f, request => request.op === 'render' ? responses.shift()(request) : reaped())
  await f.click('音画窗口')
  for (const input of windowInputs()) await toggle(input)
  for (const message of [/区间/, /SHA-256/, /5 MiB/, /窗口音频模式或输入身份不符/, /读取或校验窗口失败/]) {
    await f.click(generateWindow)
    await untilWindow(() => !f.button(generateWindow).disabled)
    assert.match(document.querySelector('[aria-label="单窗口音画监看"] [role="status"]').textContent, message)
    assert.equal(f.video(), null)
    assert.equal(f.urls.length, 0)
  }
  assert(!document.body.textContent.includes('INTERNAL_SERVER_SENTINEL'))
  assert.deepEqual(f.requests.map(({ request }) => request.op), Array.from({ length: 5 }, () => ['render', 'cancel']).flat())
})

function sessionAdapter(request, patch = {}) {
  const revoked = new AbortController()
  const grant = { id: 'session-only', ownerId: 'owner', sessionId: 'session', project: 'timeline.json', openGrant: windowOpened.grant, expiresAt: Date.now() + 60_000 }
  return { revoked, adapter: { qualification: { capability: 'preview-session', version: 1 }, ownerId: 'owner', grant,
    revoked: revoked.signal, request, ...patch } }
}

function sessionReap(request, patch) {
  const { operationId, grant: { id: grantId, ownerId, openGrant } } = request
  return Response.json({ ok: true, reaped: true, operationId, grantId, ownerId, openGrant, ...patch })
}

test('session preview gates on distinct qualified authority; a ready window covers external seeks without playback feedback', async t => {
  const calls = []
  const { revoked, adapter } = sessionAdapter((op, request, signal) => {
    calls.push({ op, request, signal })
    return op === 'render' ? windowReply(request, request.end) : sessionReap(request)
  })
  const f = await fixture(t, ProgramMonitor, { opened: windowOpened, timeline: 'timeline.json', frame: 15 }, { mockBlobURLs: true })
  await f.click('音画窗口')
  await f.pin(16)
  assert.equal(calls.length, 0)
  for (const rejected of [
    { ...adapter, qualification: { ...adapter.qualification, version: 2 } },
    { ...adapter, ownerId: 'another-owner' },
    { ...adapter, grant: { ...adapter.grant, id: windowOpened.grant } },
    { ...adapter, grant: { ...adapter.grant, project: 'other.json' } },
    { ...adapter, grant: { ...adapter.grant, expiresAt: Date.now() - 1 } },
  ]) {
    await f.render({ previewSession: rejected })
    assert.equal(calls.length, 0)
  }
  await f.render({ previewSession: adapter })
  await untilWindow(f.video)
  const node = f.video()
  Object.assign(f.state(node), { videoWidth: 1920, videoHeight: 1080, duration: 1, readyState: 2 })
  await f.emit(node, 'loadeddata')
  await f.pin(25)
  assert.equal(f.video(), node)
  assert.deepEqual(f.writes.map(({ time }) => time), [midpoint(9)])
  await f.settle(node)
  await act(async () => node.play())
  await f.decoded(node, 0.4)
  assert.deepEqual(f.pins, ['16', '25', '28'])
  assert.equal(f.writes.length, 1, 'playback observation did not seek itself')
  assert.equal(node.paused, false)
  await act(async () => revoked.abort())
  assert.equal(calls[0].signal.aborted, true)
  assert.equal(calls[1].signal.aborted, false, 'cleanup uses its own lifetime')
  assert.equal(f.video(), null)
  assert.equal(node.paused, true)
  assert.deepEqual(f.revoked, [f.urls[0].url])
  await f.pin(46) // exact end of the retired window
  assert.deepEqual(calls.map(({ op }) => op), ['render', 'cancel'])
  assert.deepEqual(f.requests, [], 'never uses the manual endpoint or toggles')
})

for (const latest of [39, 10]) test(`pending auto render keeps its original window and shows only pin ${latest}`, async t => {
  const calls = [], terminal = deferred()
  const { adapter } = sessionAdapter((op, request, signal) => {
    calls.push({ op, request, signal })
    return op === 'render' ? terminal.promise : sessionReap(request)
  }, { canDispatch: () => calls.length === 0 })
  const f = await fixture(t, ProgramMonitor, { opened: windowOpened, timeline: 'timeline.json', frame: 10, previewSession: adapter },
    { mockBlobURLs: true, quantizeToMicroseconds: true })
  await f.click('音画窗口')
  const [original] = calls, monitor = f.owner.previewWindow, { job } = monitor.state
  const coverage = () => monitor.coverage({ ...job, previewSession: adapter }, monitor.state)
  const pins = ['23', String(latest)]
  for (const pin of pins) await f.pin(pin)
  assert.equal(calls.length, 1, 'in-window pins do not consume another render or request a cancel')
  assert.equal(original.signal.aborted, false)
  assert.equal(f.video(), null)
  assert.deepEqual(coverage(), [{ status: 'rendering', range: [10, 40] }])
  await act(async () => terminal.resolve(windowReply(original.request, 40, { headers: { 'X-Framivra-Input-Epoch': inputEpoch } })))
  await untilWindow(f.video)
  const node = f.video(), browser = f.state(node)
  assert(!visible(node) && !node.controls)
  browser.currentSrc = node.getAttribute('src')
  assert.equal(browser.readyState, 0)
  await f.emit(node, 'progress')
  assert.equal(f.video(), node, 'early download progress is not a bad decoder metadata certificate')
  assert.equal(calls.length, 1)
  browser.seekable = []
  await loadWindow(f, node)
  browser.readyState = 3
  await f.emit(node, 'canplay')
  assert.deepEqual(f.writes, [], 'opening the original start or an unreachable target needs no seek')
  if (latest !== 10) {
    assert(!visible(node) && !node.controls)
    assert.deepEqual(coverage(), [], 'playable at the old start is not ready at the requested frame')
    browser.seekable = [[0, 1]]
    await f.emit(node, 'progress')
    assert.equal(f.writes.length, 1)
    assert.equal(f.writes[0].node, node)
    assert(Math.abs(f.writes[0].time - midpoint(latest - 10)) < 1e-12)
    await f.emit(node, 'timeupdate')
    assert(!visible(node) && !node.controls)
    assert.deepEqual(coverage(), [])
    await f.settle(node)
  }
  assert.equal(f.video(), node)
  assert(node.controls && visible(node) && node.paused)
  assert.deepEqual(coverage(), [{ status: 'ready', range: [10, 40] }])
  assert.equal(f.frame(), latest)
  assert.deepEqual(f.pins, pins, 'the initial seek and its observations never write the project cursor')
  assert.deepEqual(f.plays, [])
  assert.equal(calls.length, 1)
})

test('retargeted ordinary playback waits for target PTS and native seek; a revoked paint cannot play', async t => {
  for (const ptsFirst of [true, false]) await t.test(ptsFirst ? 'PTS before seeked' : 'seeked before PTS', async t => {
    const terminal = deferred(), calls = []
    const { adapter, revoked } = sessionAdapter((op, request, signal) => {
      calls.push({ op, request, signal })
      return op === 'render' ? terminal.promise : sessionReap(request)
    })
    const f = await fixture(t, ProgramMonitor, { opened: windowOpened, timeline: 'timeline.json', frame: 10, previewSession: adapter }, { mockBlobURLs: true })
    const paints = new Map(); let seq = 0
    globalThis.requestAnimationFrame = callback => { paints.set(++seq, callback); return seq }
    globalThis.cancelAnimationFrame = id => paints.delete(id)
    async function paint() {
      const [id, callback] = paints.entries().next().value
      paints.delete(id)
      await act(async () => callback())
    }
    await f.click('播放预览')
    assert.deepEqual(calls.map(({ op }) => op), ['render'], 'existing consent starts playback without Generate click')
    await f.pin(15)
    assert.deepEqual(calls.map(({ op }) => op), ['render'])
    await f.click('播放预览') // The new pin retired the old play intent, not the qualified render.
    await act(async () => terminal.resolve(windowReply(calls[0].request, 40)))
    await untilWindow(f.video)
    const node = f.video(), time = midpoint(5)
    await loadWindow(f, node)
    assert.equal(node.currentTime, time)
    await f.decoded(node, 0, time)
    assert(!visible(node) && node.muted && node.paused)
    if (ptsFirst) await f.decoded(node, 5 / 30, time)
    else await f.settle(node)
    assert(!visible(node) && node.muted && node.paused)
    if (ptsFirst) await f.settle(node)
    else await f.decoded(node, 5 / 30, time)
    assert(visible(node) && node.muted && node.paused && !node.controls)
    await paint()
    assert(node.paused && node.muted)
    if (ptsFirst) {
      const late = paints.values().next().value
      await act(async () => revoked.abort())
      await act(async () => late())
      assert.equal(f.video(), null)
      assert.deepEqual(f.plays, [])
      assert.deepEqual(calls.map(({ op }) => op), ['render', 'cancel'])
    } else {
      await paint()
      assert.deepEqual(f.plays, [node]); assert(!node.muted && node.controls)
      assert.equal(f.frame(), 15)
      await f.click('暂停预览')
      assert(node.paused)
      await f.click('播放预览')
      assert.deepEqual(f.plays, [node, node])
      // A same-window drag and immediately queued Play also use the fence.
      await f.pin(20)
      await f.click('播放预览')
      await f.settle(node)
      assert(!visible(node) && node.muted && node.paused)
      await f.decoded(node, 5 / 30, midpoint(10))
      assert(!visible(node) && node.paused)
      await f.decoded(node, 10 / 30, midpoint(10))
      assert(visible(node) && node.muted && node.paused && !node.controls)
      await paint()
      await paint()
      assert.deepEqual(f.plays, [node, node, node]); assert.equal(f.frame(), 20)
      assert.deepEqual(calls.map(({ op }) => op), ['render'])
    }
  })
})

test('an off-target initial seek stays hidden; pinning the original start after receipt reaps the old operation', async t => {
  const calls = [], terminal = deferred()
  const { adapter } = sessionAdapter((op, request, signal) => {
    calls.push({ op, request, signal })
    return op === 'render' ? (calls.length === 1 ? terminal.promise : deferred().promise) : sessionReap(request)
  })
  const f = await fixture(t, ProgramMonitor, { opened: windowOpened, timeline: 'timeline.json', frame: 10, previewSession: adapter }, { mockBlobURLs: true })
  await f.click('音画窗口')
  const [original] = calls, monitor = f.owner.previewWindow, { job } = monitor.state
  await f.pin(23)
  await act(async () => terminal.resolve(windowReply(original.request, 40, { headers: { 'X-Framivra-Input-Epoch': inputEpoch } })))
  await untilWindow(f.video)
  const node = f.video()
  await loadWindow(f, node)
  Object.assign(f.state(node), { time: 0, readyState: 3 }) // Simulate a clamped seek to the obsolete start.
  await f.settle(node)
  await f.emit(node, 'canplay')
  assert(!visible(node) && !node.controls)
  assert.deepEqual(monitor.coverage({ ...job, previewSession: adapter }, monitor.state), [])
  assert.deepEqual(f.pins, ['23'])
  await f.pin(10)
  await untilWindow(() => calls.length === 3)
  assert.deepEqual(calls.map(({ op, request }) => [op, request.frame]), [['render', 10], ['cancel', 10], ['render', 10]])
  assert.deepEqual(calls[1].request, original.request)
  assert.notEqual(calls[2].request.operationId, original.request.operationId)
  assert.equal(original.signal.aborted, true)
  assert.equal(f.video(), null)
})

test('only the latest pin survives a wrong reap receipt, and a late response cannot install stale media', async t => {
  const calls = [], firstRender = deferred(), firstReap = deferred()
  const { adapter } = sessionAdapter((op, request, signal) => {
    calls.push({ op, request, signal })
    if (op === 'render') return calls.length === 1 ? firstRender.promise : windowReply(request, request.end)
    return calls.length === 2 ? firstReap.promise : sessionReap(request)
  })
  const f = await fixture(t, ProgramMonitor, { opened: windowOpened, timeline: 'timeline.json', frame: 10, previewSession: adapter }, { mockBlobURLs: true })
  await f.click('音画窗口')
  await f.pin(23)
  await f.pin(40) // half-open coverage, not inside [10, 40)
  assert.equal(calls[0].signal.aborted, true)
  await f.pin(10) // Reversing to the old start cannot revive the aborted request.
  await act(async () => firstReap.resolve(sessionReap(calls[1].request, { openGrant: 'other-project-open' })))
  await untilWindow(() => f.button('重试回收音画窗口'))
  await f.pin(29)
  assert.deepEqual(calls.map(({ op, request }) => [op, request.frame]), [['render', 10], ['cancel', 10]])
  await f.click('重试回收音画窗口')
  await untilWindow(f.video)
  await act(async () => firstRender.resolve(windowReply(calls[0].request, 40)))
  assert.deepEqual(calls.map(({ op, request }) => [op, request.frame]), [['render', 10], ['cancel', 10], ['cancel', 10], ['render', 29]])
  assert.deepEqual(calls[1].request, calls[2].request)
  assert.notEqual(calls[3].request.operationId, calls[0].request.operationId)
  assert.equal(f.urls.length, 1)
})

const inputEpoch = `sha256:${'e'.repeat(64)}`
test('client verifies program audio headers before creating a playable Blob', async t => {
  const opened = { ...windowOpened, previewSession: { capability: 'preview-session', version: 1, ownerId: 'owner' },
    capabilities: { capabilities: { ...windowOpened.capabilities.capabilities,
      preview_window: { ...windowOpened.capabilities.capabilities.preview_window, program_audio: true } } } }
  const header = 'X-Framivra-Program-Audio', epoch = 'X-Framivra-Input-Epoch'
  const headers = [{ [epoch]: inputEpoch }, { [header]: '0', [epoch]: inputEpoch }, { [header]: '1' },
    { [header]: '1', [epoch]: inputEpoch, 'X-Framivra-Continuation-From': 'some-operation' },
    { [header]: '1', [epoch]: inputEpoch, 'X-Framivra-Media-Kind': 'approved-formal-archive/v1' },
    { [header]: '1', [epoch]: inputEpoch }]
  const calls = [], { adapter, revoked } = sessionAdapter((op, request) => {
    calls.push({ op, request })
    return op === 'render' ? windowReply(request, request.end, { headers: headers.shift() }) : sessionReap(request)
  })
  adapter.grant = { ...adapter.grant, useProgramAudio: true }
  const f = await fixture(t, ProgramMonitor, { opened, timeline: 'timeline.json', frame: 10, previewSession: adapter }, { mockBlobURLs: true })
  await f.click('音画窗口')
  for (const frame of [20, 30, 40, 50, 60]) {
    const mismatch = frame === 60 ? /窗口媒体类别不符/ : /窗口音频模式或输入身份不符/
    await untilWindow(() => !f.owner.previewWindow.state.job && mismatch.test(f.owner.previewWindow.state.message))
    assert.equal(f.video(), null)
    assert.equal(f.urls.length, 0)
    await f.pin(frame)
  }
  await untilWindow(f.video)
  await loadWindow(f, f.video())
  assert(f.video().controls && visible(f.video()))
  assert.equal(f.urls.length, 1)
  await act(async () => revoked.abort())
  assert.equal(f.video(), null)
  assert.deepEqual(f.revoked, [f.urls[0].url])
  assert.deepEqual(calls.map(({ op, request }) => [op, request.frame]),
    [10, 20, 30, 40, 50, 60].flatMap(frame => [['render', frame], ['cancel', frame]]))
})

const continuationQualification = { capability: 'preview-session', version: 1, ownerId: 'owner', continuation: { version: 1 } }
const continuationOpened = { ...windowOpened, previewSession: continuationQualification }
function continuationAdapter(request) {
  const { adapter } = sessionAdapter(request, { qualification: continuationQualification, canDispatch: () => true })
  return { ...adapter, grant: { ...adapter.grant, continuation: true } }
}
const continuityReply = (request, epoch = inputEpoch, headers = {}) => windowReply(request, request.end, { headers: {
  'X-Framivra-Input-Epoch': epoch,
  ...(request.previousOperationId ? { 'X-Framivra-Continuation-From': request.previousOperationId } : {}), ...headers,
} })
async function loadWindow(f, node, duration = 1) {
  Object.assign(f.state(node), { videoWidth: 1920, videoHeight: 1080, duration, readyState: 2 })
  await f.emit(node, 'loadeddata')
}
async function endWindow(f, node) {
  Object.assign(f.state(node), { paused: true, ended: true, time: node.duration })
  await f.emit(node, 'pause') // HTML media pauses on natural completion before `ended`.
  await f.emit(node, 'ended')
}

test('continuation is an unchecked opt-in and the real authorization adapter requires Host confirmation', async t => {
  function MonitorWithAccess(props) {
    const { adapter, controls } = usePreviewSessionAccess({ ...props, project: props.timeline })
    return h(ProgramMonitor, { ...props, previewSession: adapter, sessionControls: controls })
  }
  const commands = []
  const f = await fixture(t, MonitorWithAccess, { opened: continuationOpened, timeline: 'timeline.json', frame: 10,
    call(_sessionId, request) {
      commands.push(request)
      if (request.op === 'authorize-preview-session') return { ok: true, grant: {
        id: request.continuation ? 'not-confirmed' : 'base', ownerId: 'owner', openGrant: continuationOpened.grant, sessionId: 'session',
        project: 'timeline.json', expiresAt: Date.now() + 60_000, maxWindows: 120,
        ...(!request.continuation ? { continuation: true } : {}), // Host cannot opt the user in.
      } }
      assert.equal(request.op, 'revoke-preview-session')
      return { ok: true, revoked: true, grantId: request.previewGrant }
    },
  }, { mockBlobURLs: true })
  serveWindows(f, request => request.op === 'session-render' ? windowReply(request, request.frame + 30) : Response.json({
    ok: true, reaped: true, operationId: request.operationId, ownerId: request.ownerId, openGrant: request.grant, grantId: request.previewGrant,
  }))
  const authorize = '开启当前工程自动预览'
  const permissionInputs = () => [...document.querySelectorAll('[aria-label="单窗口音画监看"] fieldset input')]
  await f.click('音画窗口')
  assert.deepEqual(permissionInputs().map(input => input.checked), [false, false, false])
  await toggle(permissionInputs()[0]); await toggle(permissionInputs()[1]); await f.click(authorize)
  assert.equal(Object.hasOwn(commands[0], 'continuation'), false)
  await untilWindow(f.video)
  const node = f.video()
  await loadWindow(f, node); await act(async () => node.play()); await endWindow(f, node)
  assert.deepEqual(f.requests.map(({ request }) => request.op), ['session-render'], 'legacy playback does not use continuation or early reap')
  await f.click('停止并撤销自动预览')
  await untilWindow(() => permissionInputs().length === 3 && f.video() === null)
  for (const input of permissionInputs()) await toggle(input)
  await f.click(authorize)
  assert.equal(commands[2].continuation, true)
  assert.equal(commands[3].previewGrant, 'not-confirmed')
  assert.equal(f.video(), null)
  assert.deepEqual(f.requests.map(({ request }) => request.op), ['session-render', 'session-cancel'])
  assert(document.body.textContent.includes('宿主未确认连续播放的独立许可'))
})

const firstFrameOpened = { ...continuationOpened, previewSession: { ...continuationQualification, firstFrame: { version: 1 } },
  capabilities: { capabilities: { ...windowOpened.capabilities.capabilities, preview_window: {
    ...windowOpened.capabilities.capabilities.preview_window, first_frame: { version: 1 }, warm_svg_media: true,
  } } } }

const programAudioOpened = { ...firstFrameOpened, previewSession: { ...firstFrameOpened.previewSession, programAudioContinuation: { version: 1 } },
  capabilities: { capabilities: { ...firstFrameOpened.capabilities.capabilities,
    preview_window: { ...firstFrameOpened.capabilities.capabilities.preview_window, input_epoch: true, program_audio: true } } } }
const programMarker = { 'X-Framivra-Program-Audio': '1' }
test('qualified program audio with continuation uses two opt-ins, the exact wire mode and native Play after reap', async t => {
  function MonitorWithAccess(props) {
    const { adapter, controls } = usePreviewSessionAccess({ ...props, project: props.timeline })
    return h(ProgramMonitor, { ...props, previewSession: adapter, sessionControls: controls })
  }
  const opened = programAudioOpened
  const commands = [], firstReap = deferred(), epoch = `sha256:${'d'.repeat(64)}`
  const f = await fixture(t, MonitorWithAccess, { opened, timeline: 'timeline.json', frame: 10, duration: 70,
    call(_sessionId, request) {
      commands.push(request)
      if (request.op === 'authorize-preview-session') return { ok: true, grant: {
        id: 'mixed', ownerId: 'owner', openGrant: opened.grant, sessionId: 'session', project: 'timeline.json',
        expiresAt: Date.now() + 60_000, maxWindows: 3, useProgramAudio: request.useProgramAudio, continuation: request.continuation,
      } }
      return { ok: true, revoked: true, grantId: request.previewGrant }
    },
  }, { mockBlobURLs: true })
  const reap = request => Response.json({ ok: true, reaped: true,
    operationId: request.operationId, ownerId: request.ownerId, openGrant: request.grant, grantId: request.previewGrant })
  serveWindows(f, request => {
    if (request.op === 'session-cancel') return f.requests.length === 2 ? firstReap.promise : reap(request)
    return continuityReply({ ...request, end: request.frame + 30 }, epoch, programMarker)
  })
  await f.click('音画窗口')
  const [render, prepare, warm, continuation, provisional, program] = document.querySelectorAll('fieldset input')
  assert([render, prepare, warm, continuation, provisional, program].every(input => !input.checked))
  await toggle(render); await toggle(prepare); await toggle(program)
  assert(!continuation.checked && !continuation.disabled && provisional.disabled)
  await toggle(continuation)
  assert(program.checked && continuation.checked && !program.disabled)
  assert.deepEqual(commands, []); assert.deepEqual(f.requests, [])
  await f.click('开启当前工程自动预览')
  assert.equal(commands[0].useProgramAudio, true); assert.equal(commands[0].continuation, true)
  await untilWindow(() => f.video() && f.requests.length === 2)
  const video = f.video(), [initial, cleanup] = f.requests.map(({ request }) => request)
  assert.equal(initial.useProgramAudio, true)
  assert.equal(Object.hasOwn(cleanup, 'useProgramAudio'), false)
  await loadWindow(f, video)
  f.state(video).readyState = 3
  await f.emit(video, 'canplay')
  await act(async () => firstReap.resolve(reap(cleanup)))
  await untilWindow(() => !f.owner.previewWindow.state.job)
  assert(video.paused, 'authorization is not native Play')
  assert.deepEqual(f.requests.map(({ request }) => request.op), ['session-render', 'session-cancel'])
  await act(async () => video.play())
  await untilWindow(() => f.requests.length === 4)
  const successor = f.requests[2].request
  assert.deepEqual(successor, { ...initial, op: 'session-continue', operationId: successor.operationId, frame: 40,
    previousOperationId: initial.operationId, inputEpoch: epoch })
  assert.notEqual(successor.operationId, initial.operationId)
  assert.equal(f.urls.length, 2, 'matching mode and predecessor allow the successor Blob')
})

// The browser decode is mocked here, as for the MP4 fixture. Still exercise the
// real client response/body binding, signature and SHA-256 checks.
function firstFrameReply(request) {
  const png = Buffer.alloc(33)
  png.set([137,80,78,71,13,10,26,10])
  png.writeUInt32BE(13, 8); png.write('IHDR', 12); png.writeUInt32BE(1920, 16); png.writeUInt32BE(1080, 20)
  return new Response(png, { headers: {
    'Content-Type': 'image/png', 'Content-Length': png.length, 'X-Framivra-Version': request.contentVersion,
    'X-Framivra-Start': request.frame, 'X-Framivra-End': request.end,
    'X-Framivra-SHA256': createHash('sha256').update(png).digest('hex'),
  } })
}

const firstImage = () => document.querySelector('.fv-window-first-frame')

async function firstFrameFixture(t, respond, opened = firstFrameOpened) {
  function MonitorWithAccess(props) {
    const { adapter, controls } = usePreviewSessionAccess({ ...props, project: props.timeline })
    return h(ProgramMonitor, { ...props, previewSession: adapter, sessionControls: controls })
  }
  const commands = []
  const f = await fixture(t, MonitorWithAccess, { opened, timeline: 'timeline.json', frame: 10,
    call(_sessionId, request) {
      commands.push(request)
      if (request.op === 'authorize-preview-session') return { ok: true, grant: {
        id: 'temporary', ownerId: 'owner', openGrant: opened.grant, sessionId: 'session',
        project: 'timeline.json', expiresAt: Date.now() + 60_000, maxWindows: 3,
        ...(request.firstFrame ? { firstFrame: true } : {}),
      } }
      assert.equal(request.op, 'revoke-preview-session')
      return { ok: true, revoked: true, grantId: request.previewGrant }
    },
  }, { mockBlobURLs: true })
  serveWindows(f, (request, signal) => request.op !== 'session-cancel' ? respond(request, signal) : Response.json({ ok: true, reaped: true,
    operationId: request.operationId, ownerId: request.ownerId, openGrant: request.grant, grantId: request.previewGrant }))
  await f.click('音画窗口')
  const checkboxes = () => [...document.querySelectorAll('fieldset input')]
  const authorize = async () => {
    await toggle(checkboxes()[0]); await toggle(checkboxes()[1]); await f.click('开启当前工程自动预览')
  }
  return { ...f, commands, checkboxes, authorize }
}

test('temporary first picture uses one authorized operation, does not count or play, and yields only to playable video', async t => {
  const terminal = deferred()
  let polls = 0
  const f = await firstFrameFixture(t, request => request.op === 'session-render' ? terminal.promise :
    ++polls === 1 ? new Response(null, { status: 204 }) : firstFrameReply(request))
  globalThis.Image = class { naturalWidth = 1920; naturalHeight = 1080; async decode() {} }
  const inputs = f.checkboxes()
  assert.deepEqual(inputs.map(input => input.checked), [false, false, false, false, false])
  await toggle(inputs[2]); assert(inputs[4].disabled, 'warm SVG excludes first-frame consent'); await toggle(inputs[2])
  await toggle(inputs[4]); assert(inputs[2].disabled && inputs[3].disabled)
  assert.deepEqual(f.commands, []); assert.deepEqual(f.requests, [], 'checking consent does not warm or poll')
  await f.authorize()
  await untilWindow(firstImage)
  assert.equal(f.commands[0].firstFrame, true)
  const [render, pending, ready] = f.requests.map(({ request }) => request)
  assert.equal(render.firstFrame, true)
  const { firstFrame: _, ...identity } = render
  assert.deepEqual(pending, { ...identity, op: 'session-first-frame', project: 'timeline.json', end: 40 })
  assert.deepEqual(ready, pending)
  const image = firstImage(), url = image.getAttribute('src')
  assert(document.body.textContent.includes('正在合成音画，当前仅显示已验首帧'))
  assert(document.body.textContent.includes('已请求 1/3 窗'))
  assert.equal(f.video(), null)
  assert.equal(f.owner.generated, undefined, 'temporary image cannot become a clipboard/draft frame')
  const monitor = f.owner.previewWindow, { job } = monitor.state
  const coverage = () => monitor.coverage({ ...job, previewSession: job.automatic.adapter }, monitor.state)
  assert.deepEqual(coverage(), [{ status: 'rendering', range: [10, 40] }])
  assert.deepEqual(f.pins, [])
  await act(async () => terminal.resolve(windowReply(render, 40, { headers: { 'X-Framivra-Input-Epoch': inputEpoch } })))
  await untilWindow(f.video)
  await loadWindow(f, f.video())
  assert.equal(firstImage(), image); assert.equal(f.video().controls, false)
  f.state(f.video()).readyState = 3
  await f.emit(f.video(), 'canplay')
  assert(f.video().controls && visible(f.video()))
  assert.deepEqual(coverage(), [{ status: 'ready', range: [10, 40] }])
  assert.equal(firstImage(), null); assert.equal(image.hasAttribute('src'), false); assert(f.revoked.includes(url))
  assert.equal(f.requests.length, 3, 'success retains the non-continuation reap lifecycle')
  await act(async () => f.video().play())
  await f.emit(f.video(), 'waiting')
  assert.equal(firstImage(), null, 'a later video stall cannot flash back to the first image')
})

test('an overtaken first-frame decode stays retired; an exact new seek synchronously clears a shown URL', async t => {
  const terminals = new Map(), entered = deferred(), oldDecode = deferred()
  let decodes = 0
  const f = await firstFrameFixture(t, request => {
    if (request.op === 'session-render') { const terminal = deferred(); terminals.set(request.frame, terminal); return terminal.promise }
    return firstFrameReply(request)
  })
  globalThis.Image = class {
    naturalWidth = 1920; naturalHeight = 1080
    async decode() { if (++decodes === 1) { entered.resolve(); await oldDecode.promise } }
  }
  await toggle(f.checkboxes()[4]); await f.authorize()
  await act(() => entered.promise)
  await act(async () => terminals.get(10).resolve(windowReply(f.requests[0].request, 40)))
  await untilWindow(f.video)
  await act(async () => oldDecode.resolve())
  assert.equal(f.requests[1].signal.aborted, true)
  assert.equal(firstImage(), null, 'W stopped the still-pending F decode before it could reach the stage')
  await f.pin(60)
  await untilWindow(firstImage)
  const image = firstImage(), url = image.getAttribute('src')
  await act(async () => {
    assert(f.owner.pin(90))
    assert.equal(image.hasAttribute('src'), false, 'clear the old node before a React commit')
    assert(f.revoked.includes(url))
  })
  const render = f.requests.find(({ request }) => request.op === 'session-render' && request.frame === 60)
  assert.equal(render.signal.aborted, true)
  await act(async () => terminals.get(60).resolve(windowReply(render.request, 90)))
  assert.notEqual(firstImage(), image)
})

test('authorized playback waits together at the boundary; only the reaped adjacent spare becomes audible and enters at its first frame', async t => {
  const calls = [], firstReap = deferred(), secondReap = deferred(), nextWindow = deferred()
  const adapter = continuationAdapter((op, request) => {
    calls.push({ op, request })
    if (op === 'render') return continuityReply(request)
    if (op === 'continue') return nextWindow.promise
    return calls.length === 2 ? firstReap.promise : secondReap.promise
  })
  const result = { content_version: version, tracks: [], blocks: [] }
  const snapshot = { ...result, document: { project: { fps: 30, duration_frames: 70 } } }
  function MonitorWithBand(props) {
    const [monitor, state] = useWindowMonitor(props.owner)
    const previewCoverage = monitor.coverage({ ...props, project: props.timeline, grant: props.opened.grant, contentVersion: version }, state)
    return h(React.Fragment, null, h(ProgramMonitor, props), h(VisualTimeline, { snapshot, result, previewCoverage }))
  }
  const bandStatus = () => document.querySelector('.fv-preview-status[role="status"]').textContent
  const f = await fixture(t, MonitorWithBand, { opened: continuationOpened, timeline: 'timeline.json', frame: 10, duration: 70, previewSession: adapter }, { mockBlobURLs: true })
  const playable = async node => { f.state(node).readyState = 3; await f.emit(node, 'canplay') }
  await f.click('音画窗口')
  await untilWindow(() => f.video() && calls.length === 2)
  const active = f.video()
  await loadWindow(f, active)
  await playable(active)
  await act(async () => active.play())
  assert.deepEqual(calls.map(({ op }) => op), ['render', 'cancel'], 'first reap is mandatory even while verified media plays')
  await f.decoded(active, 0.5)
  assert.equal(f.frame(), 25)
  await act(async () => firstReap.resolve(sessionReap(calls[1].request)))
  const { request: successor } = calls[2]
  assert.deepEqual([calls[2].op, successor.contentVersion, successor.frame, successor.end, successor.previousOperationId, successor.inputEpoch],
    ['continue', version, 40, 70, calls[0].request.operationId, inputEpoch])
  await endWindow(f, active)
  assert.equal(f.frame(), 39)
  assert.match(bandStatus(), /音画就绪 \[10, 40\).*已请求合成 \[40, 70\)/)
  assert(f.button('暂停连续播放'))
  assert(document.body.textContent.includes('音画正在一起等待下一窗'))
  await act(async () => nextWindow.resolve(continuityReply(successor)))
  const videos = () => [...document.querySelectorAll('video')]
  await untilWindow(() => videos().length === 2 && calls.length === 4)
  const spare = videos()[1]
  await loadWindow(f, spare)
  await playable(spare)
  assert(spare.muted && spare.paused && !visible(spare))
  assert.equal(f.frame(), 39, 'decoding alone does not authorize handoff')
  assert.doesNotMatch(bandStatus(), /\[40, 70\)/, 'spare is not ready for handoff before the Host reaps')
  await act(async () => secondReap.resolve(sessionReap(calls[3].request)))
  await untilWindow(() => f.frame() === 40)
  assert.match(bandStatus(), /音画就绪 \[40, 70\)/)
  assert.doesNotMatch(bandStatus(), /\[10, 40\)/, 'handed-off coverage does not retain the evicted interval')
  assert.deepEqual(videos(), [spare], 'promote the decoded element, not a third element')
  assert(!spare.muted && !spare.paused && visible(spare))
  assert(active.muted && active.paused && !active.hasAttribute('src'))
  assert.deepEqual(f.pins, ['25', '39', '40'])
  assert.deepEqual(f.writes, [], 'window handoff must not skip the initial audio samples by midpoint seeking')
  assert.deepEqual(calls.map(({ op }) => op), ['render', 'cancel', 'continue', 'cancel'])
})

async function bufferedSpare(t, acknowledge) {
  const calls = []
  let canDispatch = true
  const adapter = { ...continuationAdapter((op, request) => {
    calls.push({ op, request })
    if (op !== 'cancel') return continuityReply(request)
    return request.frame === 40 && acknowledge ? acknowledge(request) : sessionReap(request)
  }), canDispatch: () => canDispatch }
  const f = await fixture(t, ProgramMonitor, { opened: continuationOpened, timeline: 'timeline.json', frame: 10,
    fps: '30000/1001', previewSession: adapter }, { mockBlobURLs: true })
  await f.click('音画窗口'); await untilWindow(f.video)
  const active = f.video()
  async function playable(node) {
    await loadWindow(f, node, 1.001)
    f.state(node).readyState = 3
    await f.emit(node, 'canplay')
  }
  await playable(active)
  await act(async () => active.play())
  await untilWindow(() => document.querySelectorAll('video').length === 2 && calls.length === 4)
  const spare = [...document.querySelectorAll('video')][1]
  const retiredCallback = f.state(active).callbacks.values().next().value
  await act(async () => active.pause()); await f.emit(active, 'pause')
  await playable(spare)
  const monitor = f.owner.previewWindow
  const coverage = () => monitor.coverage({ ...monitor.state.active, previewSession: adapter }, monitor.state)
  return { ...f, calls, active, spare, monitor, retiredCallback, coverage, exhaustBudget: () => { canDispatch = false } }
}

test('explicit pins reuse the parked reaped successor node and URL, and the latest target wins before the DOM commit', async t => {
  const f = await bufferedSpare(t)
  const { active, spare, monitor } = f
  await untilWindow(() => !monitor.state.job)
  assert.deepEqual(f.coverage(), [{ status: 'ready', range: [10, 40] }, { status: 'ready', range: [40, 70] }])
  const source = spare.currentSrc
  await act(async () => {
    f.pinNow('44')
    assert.equal(spare.currentTime, midpoint(4, 30000, 1001))
    assert.equal(monitor.state.active.node, spare, 'handoff is visible to the next pin even before React reuses the key')
    f.pinNow('52')
    f.retiredCallback(100, { mediaTime: 0.9 })
    spare.dispatchEvent(new f.dom.window.Event('play')) // Already queued, not a new user play.
  })
  await f.settle(spare)
  assert.equal(f.frame(), 52)
  assert.deepEqual(f.pins, ['44', '52'])
  assert.deepEqual(f.writes, [4, 12].map(frame => ({ node: spare, time: midpoint(frame, 30000, 1001) })))
  assert.deepEqual([...document.querySelectorAll('video')], [spare])
  assert.equal(spare.currentSrc, source)
  assert(spare.paused && !spare.muted && visible(spare))
  assert(active.paused && active.muted && !active.hasAttribute('src'))
  assert.deepEqual(f.plays, [active], 'promotion and stale native events cannot play or prepare another window')
  assert.deepEqual(f.coverage(), [{ status: 'ready', range: [40, 70] }])
  assert.deepEqual(f.revoked, [f.urls[0].url])
  assert.deepEqual(f.calls.map(({ op }) => op), ['render', 'cancel', 'continue', 'cancel'])

  f.exhaustBudget() // Keep later intentional play local while testing the old callback.
  await act(async () => spare.play())
  const replacedCallback = f.state(spare).callbacks.values().next().value
  await f.pin(55); await f.settle(spare)
  await act(async () => spare.play())
  const currentCallback = f.state(spare).callbacks.values().next().value
  await act(async () => replacedCallback(100, { mediaTime: 0 }))
  assert.equal(f.frame(), 55, 'an already queued callback from the same reused video cannot undo a pin')
  assert.deepEqual([...f.state(spare).callbacks.values()], [currentCallback])
})

test('a decoded successor under a pending or mismatched reap cannot be promoted, and only the last pin renders after retry', async t => {
  const pending = deferred()
  let first = true
  const f = await bufferedSpare(t, request => {
    if (!first) return sessionReap(request)
    first = false
    return pending.promise
  })
  assert.deepEqual(f.coverage(), [{ status: 'ready', range: [10, 40] }])
  await f.pin(44)
  assert.equal(f.video(), null)
  assert(!f.spare.hasAttribute('src'))
  assert.equal(f.calls.length, 4, 'wait for the existing cleanup, not a new request')
  await act(async () => pending.resolve(sessionReap(f.calls[3].request, { operationId: f.calls[0].request.operationId })))
  await untilWindow(() => f.monitor.state.failed)
  await f.pin(52)
  assert.deepEqual(f.calls.map(({ op }) => op), ['render', 'cancel', 'continue', 'cancel'])
  await f.click('重试回收音画窗口')
  await untilWindow(f.video)
  assert.notEqual(f.video(), f.spare)
  assert(f.video().paused)
  assert.deepEqual(f.calls.slice(4, 6).map(({ op, request }) => [op, request.frame]), [['cancel', 40], ['render', 52]])
})

test('a reaped spare whose native source no longer matches is not green and falls back to exact render', async t => {
  const f = await bufferedSpare(t)
  await untilWindow(() => !f.monitor.state.job)
  f.state(f.spare).currentSrc = 'blob:http://monitor.invalid/not-the-held-source'
  assert.deepEqual(f.coverage(), [{ status: 'ready', range: [10, 40] }])
  await f.pin(44)
  await untilWindow(f.video)
  assert.notEqual(f.video(), f.spare)
  assert(f.video().paused)
  assert.deepEqual(f.calls.slice(4, 5).map(({ op, request }) => [op, request.frame]), [['render', 44]])
})

test('natural handoff refuses a reaped spare with a mismatched source or a detached node', async t => {
  for (const reason of ['source', 'detached']) await t.test(reason, async t => {
    const f = await bufferedSpare(t)
    await untilWindow(() => !f.monitor.state.job)
    await act(async () => f.active.play())
    if (reason === 'source') f.state(f.spare).currentSrc = 'blob:http://monitor.invalid/not-the-held-source'
    else Object.defineProperty(f.spare, 'isConnected', { value: false })
    await endWindow(f, f.active)
    assert.equal(f.video(), null)
    assert.match(document.body.textContent, /下一窗的来源或播放状态已失效/)
    assert(!f.plays.includes(f.spare), 'the invalid spare must not be promoted or played')
    assert.deepEqual(new Set(f.revoked), new Set(f.urls.map(({ url }) => url)))
    assert.deepEqual(f.calls.map(({ op }) => op), ['render', 'cancel', 'continue', 'cancel'])
  })
})

test('a superseded native resume rejection cannot report denial for the latest pin', async t => {
  for (const superseded of [false, true]) await t.test(superseded ? 'superseded' : 'live attempt', async t => {
    const f = await bufferedSpare(t)
    await untilWindow(() => !f.monitor.state.job)
    const reject = deferred()
    let attempts = 0
    f.spare.play = () => { attempts++; return reject.promise.then(() => { throw new Error('play rejected') }) }
    await act(async () => f.active.play())
    await endWindow(f, f.active)
    assert.equal(f.video(), f.spare)
    assert.equal(attempts, 1)
    if (superseded) { await f.pin(52); await f.settle(f.spare) }
    await act(async () => reject.resolve())
    if (superseded) {
      assert.equal(f.frame(), 52)
      assert.doesNotMatch(document.body.textContent, /浏览器未自动播放下一窗/)
    } else assert.match(document.body.textContent, /浏览器未自动播放下一窗/)
    assert.equal(attempts, 1)
  })
})

test('pause at the boundary prevents late automatic resume; a new playhead pin supersedes preparation but waits for its reap', async t => {
  const calls = [], nextWindow = deferred(), pendingReap = deferred(), displacedWindow = deferred(), displacedReap = deferred()
  const adapter = continuationAdapter((op, request, signal) => {
    calls.push({ op, request, signal })
    if (op === 'render') return continuityReply(request)
    if (op === 'continue') return request.frame === 40 ? nextWindow.promise : displacedWindow.promise
    if (request.frame === 40) return pendingReap.promise
    if (request.operationId === calls[6]?.request.operationId) return displacedReap.promise
    return sessionReap(request)
  })
  const f = await fixture(t, ProgramMonitor, { opened: continuationOpened, timeline: 'timeline.json', frame: 10, previewSession: adapter }, { mockBlobURLs: true })
  await f.click('音画窗口'); await untilWindow(f.video)
  const active = f.video()
  await loadWindow(f, active)
  await act(async () => active.play())
  await untilWindow(() => calls.length === 3)
  await endWindow(f, active)
  await f.click('暂停连续播放')
  await act(async () => nextWindow.resolve(continuityReply(calls[2].request)))
  await untilWindow(() => document.querySelectorAll('video').length === 2 && calls.length === 4)
  const spare = [...document.querySelectorAll('video')][1]
  await loadWindow(f, spare)
  await act(async () => pendingReap.resolve(sessionReap(calls[3].request)))
  assert.equal(f.frame(), 39, 'a ready and reaped spare still cannot override explicit pause')
  assert.equal(f.video(), active)
  assert(active.paused && spare.paused && spare.muted)

  await f.pin(110)
  await untilWindow(() => f.video() && f.video() !== active)
  const replacement = f.video()
  await loadWindow(f, replacement)
  await act(async () => replacement.play())
  await untilWindow(() => calls.length === 7)
  assert(active.muted && spare.muted && !spare.hasAttribute('src'))
  await f.pin(140) // Even a pin exactly at the successor start is position intent, not autoplay.
  assert.equal(f.video(), null)
  assert.equal(calls[6].signal.aborted, true)
  assert.equal(calls.length, 8, 'the playhead queues while the displaced successor reaps')
  await act(async () => displacedWindow.resolve(continuityReply(calls[6].request)))
  assert.equal(f.urls.length, 3, 'late superseded bytes never allocate another video')
  await act(async () => displacedReap.resolve(sessionReap(calls[7].request)))
  await untilWindow(() => f.video() && calls.length === 10)
  assert.equal(f.frame(), 140)
  assert(f.video().paused, 'an explicit playhead pin never resumes automatically')
  assert.notEqual(calls[8].request.operationId, calls[6].request.operationId)
  assert.deepEqual(calls.map(({ op, request }) => [op, request.frame]), [
    ['render', 10], ['cancel', 10], ['continue', 40], ['cancel', 40], ['render', 110], ['cancel', 110],
    ['continue', 140], ['cancel', 140], ['render', 140], ['cancel', 140],
  ])
})

for (const [name, program, badEpoch, badMode] of [
  ['ordinary epoch differs', false, true, false], ['program epoch differs', true, true, false], ['program marker absent', true, false, true],
]) test(`an adjacent MP4 with a bad continuation receipt never becomes a spare (${name})`, async t => {
  const calls = []
  const adapter = continuationAdapter((op, request) => {
    calls.push(op)
    if (op === 'render') return continuityReply(request, inputEpoch, program ? programMarker : {})
    if (op === 'continue') return continuityReply(request, badEpoch ? `sha256:${'f'.repeat(64)}` : inputEpoch, program && !badMode ? programMarker : {})
    return sessionReap(request)
  })
  if (program) {
    adapter.qualification = programAudioOpened.previewSession
    adapter.grant.useProgramAudio = true
  }
  const f = await fixture(t, ProgramMonitor, { opened: program ? programAudioOpened : continuationOpened,
    timeline: 'timeline.json', frame: 10, previewSession: adapter }, { mockBlobURLs: true })
  await f.click('音画窗口'); await untilWindow(f.video)
  const node = f.video()
  await loadWindow(f, node)
  await act(async () => node.play())
  await untilWindow(() => !f.video() && calls.length === 4)
  assert(node.muted && node.paused)
  assert.equal(f.urls.length, 1)
  assert.deepEqual(f.revoked, [f.urls[0].url])
  assert.deepEqual(calls, ['render', 'cancel', 'continue', 'cancel'])
  assert(document.body.textContent.includes(badMode ? '窗口音频模式或输入身份不符' : '窗口输入身份或续窗前驱不符'))
})
