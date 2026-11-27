import test from 'node:test'
import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import React, { act } from 'react'
import { JSDOM } from 'jsdom'
import { PreviewBody } from '../src/client/index.mjs'

const h = React.createElement, version = `sha256:${'c'.repeat(64)}`
const qualified = { capability: 'preview-session', version: 1, ownerId: 'open-owner' }
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done }); return { resolve, promise } }
const json = value => Response.json(value)

async function fixture(t, qualification, respond = () => {}, windowCapability = {}, enterWindow = true) {
  const dom = new JSDOM('<div id="root"></div>', { url: 'http://preview.invalid/' })
  const globals = ['window', 'document', 'IS_REACT_ACT_ENVIRONMENT', 'fetch']
  const saved = globals.map(key => Object.getOwnPropertyDescriptor(globalThis, key))
  globalThis.window = dom.window; globalThis.document = dom.window.document; globalThis.IS_REACT_ACT_ENVIRONMENT = true
  const { createRoot } = await import('react-dom/client'), { Simulate } = await import('react-dom/test-utils')
  const root = createRoot(document.querySelector('#root')), tab = new AbortController(), requests = []
  const grant = { id: 'auto', ownerId: 'open-owner', openGrant: 'open', sessionId: 'one', project: 'timeline.json', expiresAt: Date.now() + 60_000, maxWindows: 1 }
  const coverage = { scope: 'stored', complete: true }
  const project = { content_version: version, coverage, diagnostics: [] }
  const snapshot = { ...project, document: { project: { name: '工程', fps: 30, duration_frames: 180 }, _vtx: { protocol: 1, head: 'h1' } } }
  const opened = { grant: 'open', snapshot, media: [], previewSession: qualification, host: { ops: ['preview-window'] }, capabilities: { capabilities: {
    frame: { inline_png: { flag: '--include-png' } }, preview_window: { schema: 'framivra.timeline.preview-window/v1', inline_media: true, prepare_inputs: true, ...windowCapability },
  } } }
  globalThis.fetch = async (path, init) => {
    const body = JSON.parse(init.body), signal = init.signal
    requests.push({ path, body, signal })
    const replacement = respond(body, signal, grant)
    if (replacement) return replacement
    switch (body.op) {
      case 'open': return json(opened)
      case 'query': return json({ ...project, tracks: [], blocks: [] })
      case 'authorize-preview-session': return json({ ok: true, grant })
      case 'revoke-preview-session': return json({ ok: true, revoked: true, grantId: body.previewGrant })
      case 'close': return json({ ok: true })
      default: assert.fail(`unexpected request: ${body.op}`)
    }
  }
  t.after(async () => {
    await act(async () => { tab.abort(); root.unmount() })
    dom.window.close()
    for (const [i, key] of globals.entries()) { if (saved[i]) Object.defineProperty(globalThis, key, saved[i]); else delete globalThis[key] }
  })
  const button = name => [...document.querySelectorAll('button')].find(node => node.getAttribute('aria-label') === name || node.textContent === name)
  const click = name => act(async () => { const target = button(name); assert(target && !target.disabled, name); target.click() })
  const checkbox = label => [...document.querySelectorAll('label')].find(node => node.textContent.includes(label))?.querySelector('input')
  const consent = label => act(async () => {
    const target = checkbox(label)
    assert(target && !target.disabled, label)
    target.click()
  })
  const useTabInfo = () => ({ tab: { signal: tab.signal } })
  await act(async () => root.render(h(PreviewBody, { useTabInfo, sessionId: 'one' })))
  await act(async () => document.querySelector('form').dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true })))
  if (enterWindow) await click('音画窗口')
  async function space(target, options = {}) {
    const event = new dom.window.KeyboardEvent('keydown', { key: ' ', code: 'Space', bubbles: true, cancelable: true, ...options })
    await act(async () => target.dispatchEvent(event))
    return event.defaultPrevented
  }
  return { dom, button, click, checkbox, consent, requests, tab, space, pin: value => act(async () => Simulate.change(document.querySelector('input[inputmode="numeric"]'), { target: { value } })) }
}

const enable = '开启当前工程自动预览'
const revoke = '停止并撤销自动预览'
const allowRender = '允许自动生成窗口（仅 cache/preview）'
const allowPrepare = '允许自动预览按需冷准备普通输入'
const allowWarmSvg = '允许读取已验证的暖 SVG 包（仅命中的窗口，不生成图形）'
const allowProgramAudio = '允许整片音频基准混音（限短工程，仅在私有窗口工作区处理）'
const reap = request => json({ ok: true, reaped: true, operationId: request.operationId, ownerId: request.ownerId, openGrant: request.grant, grantId: request.previewGrant })

test('monitor and timeline Space share preview transport without stealing text, IME, or native activation', async t => {
  const late = deferred()
  const view = await fixture(t, qualified, body => {
    if (body.op === 'render') return late.promise
    if (body.op === 'cancel') return reap(body)
  }, {}, false)
  const monitor = document.querySelector('.fv-monitor')
  const ruler = document.querySelector('.fv-ruler')
  const timecode = document.querySelector('[name="framivra-timecode"]')
  const nativeButton = view.button('固定帧预览')
  for (const [target, options] of [[timecode], [nativeButton], [ruler, { isComposing: true }], [ruler, { keyCode: 229 }], [ruler, { repeat: true }]]) {
    assert.equal(await view.space(target, options), !!options?.repeat)
    assert.equal(document.querySelector('[data-mode="window"]'), null)
  }
  assert.equal(await view.space(monitor), true)
  const disclosure = document.querySelector('[aria-label="窗口预览授权"]').closest('details')
  assert.equal(disclosure.open, true)
  const ops = () => view.requests.map(({ body }) => body.op)
  assert.deepEqual(ops(), ['open', 'query'], 'Space reveals permission, but cannot grant it')
  await view.consent('允许写入 cache/preview')
  assert.equal(await view.space(ruler), true)
  assert.deepEqual(ops(), ['open', 'query'], 'one of two consents does not authorize playback')
  await view.consent('允许按需冷准备输入（不授权执行 HTML）')
  assert.equal(await view.space(ruler), true)
  const { body, signal } = view.requests.at(-1)
  assert.equal(body.op, 'render'); assert.equal(body.frame, 0)
  assert.equal(view.button('取消待播放').getAttribute('aria-keyshortcuts'), 'Space')
  assert.equal(await view.space(ruler, { repeat: true }), true)
  assert.equal(signal.aborted, false)
  assert.equal(await view.space(monitor), true)
  assert.equal(signal.aborted, true)
  assert.deepEqual(ops(), ['open', 'query', 'render', 'cancel'])
  assert.equal(view.requests.at(-1).body.operationId, body.operationId)
  await act(async () => late.resolve(new Response('late')))
  assert.equal(document.querySelector('video'), null)
  assert(view.button('播放预览'))
})

test('one Space after duplicate ruler move/release queues playback before the ready-window seek settles', async t => {
  const bytes = new Uint8Array([1, 2, 3])
  const view = await fixture(t, qualified, body => {
    if (body.op === 'session-render') return new Response(bytes, { headers: {
      'Content-Type': 'video/mp4', 'X-Framivra-Version': version, 'X-Framivra-Start': String(body.frame),
      'X-Framivra-End': String(body.frame + 30), 'X-Framivra-SHA256': createHash('sha256').update(bytes).digest('hex'),
    } })
    if (body.op === 'session-cancel') return reap(body)
  })
  const { dom } = view
  const proto = dom.window.HTMLMediaElement.prototype
  let paused = true, seeking = false, time = 0, plays = 0
  Object.defineProperties(proto, {
    currentSrc: { get() { return this.getAttribute('src') ?? '' } },
    currentTime: { get: () => time, set: value => { time = value; seeking = true } },
    duration: { get: () => 1 }, readyState: { get: () => 4 },
    seekable: { get: () => ({ length: 1, start: () => 0, end: () => 1 }) },
    seeking: { get: () => seeking }, paused: { get: () => paused },
  })
  proto.pause = () => { paused = true }
  proto.play = function () { plays++; paused = false; this.dispatchEvent(new dom.window.Event('play')); return Promise.resolve() }
  proto.load = () => {}
  Object.defineProperties(dom.window.HTMLVideoElement.prototype, { videoWidth: { get: () => 1920 }, videoHeight: { get: () => 1080 } })
  await view.pin('0')
  await view.consent(allowRender)
  await view.consent(allowPrepare)
  await view.click(enable)
  const untilVideo = () => document.querySelector('video')
  for (let i = 0; i < 50 && !untilVideo(); i++) await act(async () => new Promise(resolve => setImmediate(resolve)))
  const node = untilVideo()
  assert(node)
  await act(async () => node.dispatchEvent(new dom.window.Event('loadeddata')))
  const ruler = document.querySelector('.fv-ruler')
  ruler.getBoundingClientRect = () => ({ left: 0, width: 180 })
  async function rulerPointer(type, clientX) {
    const event = new dom.window.MouseEvent(type, { clientX, button: 0, bubbles: true })
    Object.defineProperty(event, 'pointerId', { value: 1 })
    await act(async () => ruler.dispatchEvent(event))
  }
  await rulerPointer('pointerdown', 1)
  await rulerPointer('pointermove', 5)
  await rulerPointer('pointerup', 5)
  assert.equal(ruler.getAttribute('aria-valuenow'), '5')
  assert(seeking && paused)
  assert.equal(await view.space(ruler), true)
  assert(view.button('取消待播放'), 'same-frame pointerup must not leave the Space handler one epoch behind')
  assert.equal(plays, 0)
  seeking = false
  await act(async () => node.dispatchEvent(new dom.window.Event('seeked')))
  assert.equal(plays, 1)
  await rulerPointer('pointerdown', 5)
  await rulerPointer('pointerup', 5)
  assert(paused, 'an intentional same-frame gesture still pauses playback')
  assert.equal(view.requests.filter(({ body }) => body.op === 'session-render').length, 1)
})

test('Space starts qualified ordinary program-audio continuation and pauses without revoking it', async t => {
  const bytes = Buffer.from('000000186674797069736f6d0000020069736f6d69736f32', 'hex'), epoch = `sha256:${'d'.repeat(64)}`
  const initialReap = deferred()
  const view = await fixture(t, { ...qualified, continuation: { version: 1 }, programAudioContinuation: { version: 1 } }, (body, _, grant) => {
    if (body.op === 'authorize-preview-session') return json({ ok: true, grant: { ...grant, maxWindows: 3, continuation: body.continuation, useProgramAudio: body.useProgramAudio } })
    if (body.op === 'session-cancel') return body.operationId === operations('session-render')[0]?.operationId ? initialReap.promise : reap(body)
    if (['session-render', 'session-continue'].includes(body.op)) return new Response(bytes, { headers: {
      'Content-Type': 'video/mp4', 'X-Framivra-Version': version, 'X-Framivra-Start': String(body.frame),
      'X-Framivra-End': String(body.frame + 30), 'X-Framivra-SHA256': createHash('sha256').update(bytes).digest('hex'),
      'X-Framivra-Program-Audio': '1', 'X-Framivra-Input-Epoch': epoch,
      ...(body.previousOperationId && { 'X-Framivra-Continuation-From': body.previousOperationId }),
    } })
  }, { input_epoch: true, program_audio: true })
  const operations = op => view.requests.map(({ body }) => body).filter(body => body.op === op)
  const { dom } = view, prototype = dom.window.HTMLMediaElement.prototype
  const playing = new WeakSet(), ended = new WeakSet()
  let plays = 0
  Object.defineProperties(prototype, {
    currentSrc: { get() { return this.getAttribute('src') ?? '' } }, duration: { get: () => 1 }, readyState: { get: () => 4 },
    paused: { get() { return !playing.has(this) } }, ended: { get() { return ended.has(this) } },
  })
  Object.defineProperties(dom.window.HTMLVideoElement.prototype, { videoWidth: { get: () => 1920 }, videoHeight: { get: () => 1080 } })
  prototype.load = () => {}
  prototype.play = function () { plays++; playing.add(this); this.dispatchEvent(new dom.window.Event('play')); return Promise.resolve() }
  prototype.pause = function () { playing.delete(this); this.dispatchEvent(new dom.window.Event('pause')) }
  const until = async predicate => {
    for (let i = 0; i < 50 && !predicate(); i++) await act(async () => new Promise(resolve => setImmediate(resolve)))
    assert(predicate(), 'expected playback transition')
  }
  await view.pin('0')
  await view.consent(allowRender)
  await view.consent(allowPrepare)
  await view.consent(allowProgramAudio)
  await view.consent('允许连续播放时准备下一窗')
  await view.click(enable)
  await until(() => document.querySelector('video') && operations('session-cancel').length === 1)
  const original = document.querySelector('video'), ruler = document.querySelector('.fv-ruler')
  await act(async () => original.dispatchEvent(new dom.window.Event('loadeddata')))
  assert.equal(plays, 0)
  assert.deepEqual(operations('session-continue'), [], 'authorization and readiness alone do not start lookahead')
  assert.equal(await view.space(ruler), true)
  assert.equal(plays, 1)
  assert.deepEqual(operations('session-continue'), [], 'Space still waits for the original provider range to be reaped')
  await act(async () => initialReap.resolve(reap(operations('session-cancel')[0])))
  await until(() => operations('session-continue').length === 1 && document.querySelectorAll('video').length === 2)
  const [authorization] = operations('authorize-preview-session'), [initial] = operations('session-render'), [successor] = operations('session-continue')
  assert.equal(authorization.continuation, true)
  assert.equal(authorization.useProgramAudio, true)
  assert.deepEqual([successor.frame, successor.previewGrant, successor.previousOperationId, successor.inputEpoch, successor.useProgramAudio],
    [30, initial.previewGrant, initial.operationId, epoch, true])
  const spare = [...document.querySelectorAll('video')].find(node => node !== original)
  await act(async () => spare.dispatchEvent(new dom.window.Event('loadeddata')))
  assert.equal(await view.space(ruler), true)
  assert(original.paused)
  await act(async () => { ended.add(original); original.dispatchEvent(new dom.window.Event('ended')) })
  assert.equal(plays, 1, 'a paused foreground must not start its successor on a late ended event')
  assert(spare.paused)
  assert.equal(operations('session-continue').length, 1)
  assert.deepEqual(operations('revoke-preview-session'), [])
  assert(view.button(revoke), 'the explicit revoke control remains available after pausing')
})

// Existing manual advertisement must never be interpreted as session capability.
test('manual window remains available without explicit v1 owner qualification', async t => {
  const view = await fixture(t, { ...qualified, version: 2 }, undefined, { program_audio: true })
  assert.equal(view.button(enable), undefined)
  assert.equal(view.checkbox(allowProgramAudio), undefined)
  assert(view.button('生成当前音画窗口'))
  assert.deepEqual(view.requests.map(({ body }) => body.op), ['open', 'query'])
})

test('real workbench opt-in uses separate authority; budget and ambiguous reaping never dispatch a replacement', async t => {
  let cancels = 0
  const view = await fixture(t, { ...qualified, firstFrame: { version: 1 } }, (body, signal) => {
    if (body.op === 'session-render') return new Promise(resolve => signal.addEventListener('abort', () => resolve(new Response()), { once: true }))
    if (body.op === 'session-cancel') return ++cancels === 1 ? json({ ok: true, reaped: true }) : reap(body)
  }, { first_frame: { version: 2 } })
  assert.equal(view.button(enable).disabled, true)
  assert.equal(view.checkbox(allowWarmSvg), undefined, 'a legacy wheel cannot expose warm consent')
  assert.equal(view.checkbox(allowProgramAudio), undefined, 'a legacy wheel cannot expose program-audio consent')
  assert.equal(view.checkbox('允许显示临时首图'), undefined, 'owner qualification cannot override a non-v1 producer')
  await view.consent('允许写入 cache/preview')
  await view.consent('允许按需冷准备输入（不授权执行 HTML）')
  assert.equal(view.button(enable).disabled, true)
  await view.consent(allowRender)
  assert.equal(view.button(enable).disabled, true)
  await view.consent(allowPrepare)
  assert.deepEqual(view.requests.map(({ body }) => body.op), ['open', 'query'], 'two consents alone do not mint a grant')
  await view.click(enable)
  const { requests } = view
  assert.deepEqual(requests[2].body, { op: 'authorize-preview-session', sessionId: 'one', grant: 'open', scope: 'current-project', renderConsent: 'cache/preview-only', prepareInputs: true })
  const { path, body: first, signal } = requests[3]
  assert.equal(path, '/api/framivra/window')
  assert.match(first.operationId, /^[0-9a-f-]{36}$/)
  assert.deepEqual(first, { op: 'session-render', sessionId: 'one', grant: 'open', previewGrant: 'auto', ownerId: 'open-owner', operationId: first.operationId, frame: 0, contentVersion: version })
  assert(document.body.textContent.includes('额度已用完'))
  await view.click(revoke)
  assert.equal(signal.aborted, true)
  const withdrawal = requests.find(({ body }) => body.op === 'revoke-preview-session')
  const cleanup = requests.find(({ body }) => body.op === 'session-cancel')
  assert.deepEqual(withdrawal.body, { op: 'revoke-preview-session', sessionId: 'one', grant: 'open', previewGrant: 'auto', ownerId: 'open-owner' })
  assert.deepEqual(cleanup.body, { ...withdrawal.body, op: 'session-cancel', operationId: first.operationId })
  assert.equal(cleanup.signal.aborted, false)
  await view.pin('60')
  await view.consent(allowRender); await view.consent(allowPrepare)
  assert.equal(view.button(enable).disabled, true, 'withdrawal acknowledgement does not prove the worker has been reaped')
  await view.click('重试回收音画窗口')
  assert.deepEqual(requests.at(-1).body, cleanup.body)
  assert.equal(view.button(enable).disabled, false)
  assert.equal(requests.filter(({ body }) => body.op === 'session-render' || body.op === 'authorize-preview-session').length, 2, 'never automatically reauthorizes or renders after stopping')
})

test('warm SVG is a separate default-off owner choice and must match the Host grant before dispatch', async t => {
  let mint = 0
  const view = await fixture(t, qualified, (body, signal, grant) => {
    if (body.op === 'authorize-preview-session') {
      mint++
      return json({ ok: true, grant: { ...grant, id: `auto-${mint}`, ...(mint !== 2 ? { readWarmSvg: true } : {}) } })
    }
    if (body.op === 'session-render') return new Promise(resolve => signal.addEventListener('abort', () => resolve(new Response()), { once: true }))
    if (body.op === 'session-cancel') return reap(body)
  }, { warm_svg_media: true })
  const requested = op => view.requests.filter(({ body }) => body.op === op).map(({ body }) => body)
  assert.equal(view.checkbox(allowWarmSvg).checked, false)
  await view.consent(allowRender); await view.consent(allowPrepare); await view.click(enable)
  assert.equal(Object.hasOwn(requested('authorize-preview-session')[0], 'readWarmSvg'), false)
  assert.equal(requested('revoke-preview-session')[0].previewGrant, 'auto-1', 'unsolicited warm authority is immediately revoked')
  assert.equal(requested('session-render').length, 0)

  await view.consent(allowWarmSvg)
  assert.equal(view.button(enable).disabled, true, 'warm consent cannot replace ordinary consents')
  await view.consent(allowRender); await view.consent(allowPrepare); await view.click(enable)
  assert.equal(requested('authorize-preview-session')[1].readWarmSvg, true)
  assert.equal(requested('revoke-preview-session')[1].previewGrant, 'auto-2', 'a missing echo also revokes without dispatch')
  assert.equal(requested('session-render').length, 0)
  assert.equal(view.checkbox(allowWarmSvg).checked, false, 'the previous choice is not carried forward')

  await view.consent(allowRender); await view.consent(allowPrepare); await view.consent(allowWarmSvg); await view.click(enable)
  const [first] = requested('session-render')
  assert.equal(first.previewGrant, 'auto-3')
  assert.equal(Object.hasOwn(first, 'readWarmSvg'), false, 'the operation uses Host grant authority, not a client flag')
  assert.equal(first.contentVersion, version)
  await view.click(revoke)
})

test('legacy Host keeps program audio independent and default off even when the wheel advertises both bits', async t => {
  let mint = 0
  const view = await fixture(t, { ...qualified, continuation: { version: 1 }, firstFrame: { version: 1 } }, (body, signal, grant) => {
    if (body.op === 'authorize-preview-session') {
      mint++
      return json({ ok: true, grant: { ...grant, id: `audio-${mint}`,
        ...(mint !== 2 && { useProgramAudio: true }), ...(body.readWarmSvg && { readWarmSvg: true }) } })
    }
    if (body.op === 'session-render') return new Promise(resolve => signal.addEventListener('abort', () => resolve(new Response()), { once: true }))
    if (body.op === 'session-cancel') return reap(body)
  }, { program_audio: true, input_epoch: true, warm_svg_media: true, first_frame: { version: 1 } })
  const requested = op => view.requests.filter(({ body }) => body.op === op).map(({ body }) => body)
  const selectBase = async () => { await view.consent(allowRender); await view.consent(allowPrepare) }
  assert.match(document.body.textContent, /当前宿主或内核不支持与续窗合用/)
  const first = '允许显示临时首图', next = '允许连续播放时准备下一窗'
  assert.equal(view.checkbox(allowProgramAudio).checked, false)
  assert.equal(view.checkbox(allowProgramAudio).disabled, true)
  await selectBase(); await view.click(enable)
  assert.equal(Object.hasOwn(requested('authorize-preview-session')[0], 'useProgramAudio'), false)
  assert.equal(requested('revoke-preview-session')[0].previewGrant, 'audio-1')
  assert.equal(requested('session-render').length, 0, 'unsolicited Host authority is not attached')

  await selectBase()
  for (const other of [first, next]) {
    await view.consent(other); assert(view.checkbox(allowProgramAudio).disabled); await view.consent(other)
  }
  await view.consent(allowProgramAudio)
  assert(view.checkbox(first).disabled && view.checkbox(next).disabled)
  await view.click(enable)
  assert.equal(requested('authorize-preview-session')[1].useProgramAudio, true)
  assert.equal(requested('revoke-preview-session')[1].previewGrant, 'audio-2')
  assert.equal(requested('session-render').length, 0, 'a missing echo is not silently downgraded')
  assert.equal(view.checkbox(allowProgramAudio).checked, false)

  await selectBase(); await view.consent(allowProgramAudio); await view.consent(allowWarmSvg)
  assert.equal(requested('authorize-preview-session').length, 2, 'checking options does not authorize')
  await view.click(enable)
  assert.equal(requested('authorize-preview-session')[2].readWarmSvg, true)
  const [render] = requested('session-render')
  assert.equal(render.useProgramAudio, true)
  assert.equal(render.previewGrant, 'audio-3')
  await view.click(revoke)
  assert.equal(requested('session-cancel')[0].operationId, render.operationId)
  assert.equal(Object.hasOwn(requested('session-cancel')[0], 'useProgramAudio'), false)
  assert.equal(Object.hasOwn(requested('revoke-preview-session')[2], 'useProgramAudio'), false)
})

test('a grant minted after tab retirement is revoked without ever rendering', async t => {
  const minting = deferred()
  const view = await fixture(t, qualified, body => { if (body.op === 'authorize-preview-session') return minting.promise })
  await view.consent(allowRender); await view.consent(allowPrepare); await view.click(enable)
  await act(async () => view.tab.abort())
  const authorization = view.requests.find(({ body }) => body.op === 'authorize-preview-session')
  assert.equal(authorization.signal.aborted, false, 'bounded authorization must settle to discover the grant to revoke')
  await act(async () => minting.resolve(json({ ok: true, grant: { id: 'late', ownerId: 'open-owner', openGrant: 'open', sessionId: 'one', project: 'timeline.json', expiresAt: Date.now() + 60_000, maxWindows: 1 } })))
  assert.deepEqual(view.requests.filter(({ body }) => body.op.includes('session') && body.op !== 'authorize-preview-session').map(({ body }) => body), [
    { op: 'revoke-preview-session', sessionId: 'one', grant: 'open', previewGrant: 'late', ownerId: 'open-owner' },
  ])
})

for (const revoked of [false, true]) test(`undo waits for exact window reap and verified readback (${revoked ? 'revoked lease' : 'existing lease, no re-pin'})`, async t => {
  const firstReap = deferred(), undoReap = deferred(), inspect = deferred(), query = deferred()
  const nextVersion = `sha256:${'d'.repeat(64)}`
  let cancels = 0, undo = false
  const view = await fixture(t, { ...qualified, formalArchive: { version: 1 } }, (body, signal, grant) => {
    switch (body.op) {
      case 'authorize-preview-session': return json({ ok: true, grant: { ...grant, maxWindows: 3 } })
      case 'session-render': return new Promise(resolve => signal.addEventListener('abort', () => resolve(new Response()), { once: true }))
      case 'session-cancel': {
        const count = ++cancels
        return count === 1 ? firstReap.promise : count === 3 ? undoReap.promise : reap(body)
      }
      case 'authorize-write': return json({ ok: true, writeGrant: 'independent-write' })
      case 'undo': undo = true; return json({ ok: true, revision: nextVersion })
      case 'inspect': return inspect.promise
      case 'query': if (undo) return query.promise
    }
  }, { formal_archive: { version: 1 } })
  const requested = op => view.requests.filter(({ body }) => body.op === op).map(({ body }) => body)
  assert.equal(view.button('读取一次已核归档 A/V').disabled, false)
  await view.consent(allowRender); await view.consent(allowPrepare); await view.click(enable)
  assert.equal(view.button('读取一次已核归档 A/V').disabled, true, 'archive capability does not change the ordinary lease')
  await view.click('启用编辑')
  const cancelled = requested('session-cancel')[0]
  assert.equal(requested('authorize-write').length, 0)
  await act(async () => firstReap.resolve(reap({ ...cancelled, operationId: 'wrong' })))
  assert.equal(requested('authorize-write').length, 0)
  await view.click('重试回收音画窗口')
  assert.equal(requested('session-render').length, 1, 'failed edit does not reconnect and launch a new window while recovering')
  await view.click('启用编辑')
  assert.equal(requested('authorize-write').length, 1)
  assert.equal(requested('session-render').length, 2)
  await view.click('撤销当前版本')
  assert.equal(requested('undo').length, 0)
  await act(async () => undoReap.resolve(reap(requested('session-cancel')[2])))
  assert.equal(requested('undo').length, 1)
  const verified = { content_version: nextVersion, coverage: { scope: 'stored', complete: true }, diagnostics: [] }
  await act(async () => inspect.resolve(json({ ...verified, document: { project: { name: '工程', fps: 30, duration_frames: 180 }, _vtx: { protocol: 1, head: 'h2' } } })))
  assert.equal(requested('session-render').length, 2)
  if (revoked) await view.click(revoke)
  await act(async () => query.resolve(json({ ...verified, tracks: [], blocks: [] })))
  const renders = view.requests.filter(({ body }) => body.op === 'session-render')
  assert.deepEqual(renders.map(({ body }) => body.contentVersion), revoked ? [version, version] : [version, version, nextVersion])
  assert(renders.every(({ path, body }) => path === '/api/framivra/window' && body.previewGrant === 'auto' && body.frame === 0))
  assert.equal(requested('authorize-preview-session').length, 1, 'save never renews or creates authority')
  assert.equal(requested('formal-archive-read').length, 0, 'save does not consult the archive')
  assert(view.button('固定帧预览'))
})
