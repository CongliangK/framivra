import test from 'node:test'
import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { PreviewProgramRun } from '../src/client/preview-program-run.mjs'

const contentVersion = `sha256:${'a'.repeat(64)}`
const inputEpoch = `sha256:${'b'.repeat(64)}`
const bytes = new Uint8Array([1, 2, 3, 4])
const sha256 = createHash('sha256').update(bytes).digest('hex')

function bench({ maxWindows = 10, maxBytes = 100, duration = 139, first, override } = {}) {
  const stored = new Map(), order = [], calls = []
  const tab = new AbortController()
  const binding = { duration, span: 30, contentVersion, tabSignal: tab.signal, sessionId: 'session', grant: 'open', ownerId: 'owner', project: 'timeline.json',
    async call(sessionId, data) {
      calls.push({ sessionId, ...data })
      const answer = await override?.(data)
      if (answer) return answer
      if (data.op === 'authorize-preview-session') return { ok: true, grant: { id: 'run', openGrant: 'open', ownerId: 'owner', sessionId, project: binding.project,
        expiresAt: Date.now() + 60_000, maxWindows, programRun: { version: 1, contentVersion, duration, span: 30, frame: data.frame, maxBytes } } }
      if (data.op === 'program-check') return { ok: true, grantId: 'run', frame: data.frame, sha256: data.sha256 }
      if (data.op === 'cancel-program-authorization') return { ok: true, revoked: true, authorizationId: data.authorizationId }
      assert.equal(data.op, 'revoke-preview-session'); assert.equal(data.previewGrant, 'run')
      return { ok: true, revoked: true, grantId: 'run' }
    },
  }
  const controller = new PreviewProgramRun(() => {}, {
    open: async () => ({ put: async (start, blob) => stored.set(start, blob), get: async start => new Response(stored.get(start)), close: async () => stored.clear() }),
    async render(grant, id, frame, signal) {
      order.push(frame)
      if (order.length === 1 && first) await first.promise
      signal.throwIfAborted()
      return new Response(bytes, { headers: { 'Content-Type': 'video/mp4', 'Content-Length': String(bytes.length),
        'X-Framivra-Start': String(frame), 'X-Framivra-End': String(Math.min(duration, frame + 30)),
        'X-Framivra-Version': contentVersion, 'X-Framivra-Input-Epoch': inputEpoch, 'X-Framivra-SHA256': sha256,
        'X-Framivra-Operation-Id': id, 'X-Framivra-Media-Kind': 'program-preview-window/v1' } })
    },
  })
  return { controller, binding, tab, stored, order, calls }
}

test('whole-timeline run covers canonical tail then wraps the prefix and serves verified media', async t => {
  const b = bench(); t.after(() => b.controller.cancel())
  assert.equal(b.controller.start(b.binding, 79), true)
  await b.controller.run.finished
  assert.deepEqual(b.order, [60, 90, 120, 0, 30])
  assert.equal(b.controller.run.completedFrames, 139)
  assert.equal(b.controller.run.status, 'complete')
  const receipt = await b.controller.read(b.binding, 138)
  assert.deepEqual([receipt.start, receipt.end, receipt.coverageEnd, receipt.inputEpoch], [120, 139, 139, inputEpoch])
  assert.deepEqual(new Uint8Array(await receipt.blob.arrayBuffer()), bytes)
  assert.equal(b.calls.at(-1).op, 'program-check')
  await assert.rejects(b.controller.read({ ...b.binding }, 138), { code: 'PROGRAM_WINDOW_UNAVAILABLE' })
  assert.equal(await b.controller.cancel(), true)
  assert.equal(b.stored.size, 0)
})

test('foreground joins the first in-flight request, prioritizes the next grid entry, and reports partial storage quota', async t => {
  const first = Promise.withResolvers()
  const b = bench({ maxBytes: bytes.length * 2, first }); t.after(() => b.controller.cancel())
  assert(b.controller.start(b.binding, 79))
  const foreground = b.controller.read(b.binding, 17)
  first.resolve()
  const receipt = await foreground
  assert.equal(receipt.start, 0)
  await b.controller.run.finished
  assert.deepEqual(b.order, [60, 0])
  assert.equal(b.controller.run.status, 'partial')
  assert.equal(b.controller.run.completedFrames, 60)
  assert.equal(b.controller.run.code, 'PROGRAM_RUN_LIMIT')
  await assert.rejects(b.controller.read(b.binding, 136), { code: 'PROGRAM_WINDOW_UNAVAILABLE' })
  b.stored.set(0, new Blob([new Uint8Array([4, 3, 2, 1])]))
  await assert.rejects(b.controller.read(b.binding, 17), { code: 'PROGRAM_MEDIA_INVALID' })
  assert.equal(await b.controller.run.cleanup, true)
  assert.equal(b.controller.run.completedFrames, 0)
  assert.equal(b.stored.size, 0)
})

test('a cold canonical window is pending, not cached; cancelling clears its progress before settlement', async () => {
  const first = Promise.withResolvers(), started = Promise.withResolvers()
  const b = bench({ duration: 16_746, first })
  b.controller.notify = () => { if (b.controller.run.pendingWindow) started.resolve() }
  assert(b.controller.start(b.binding, 515))
  try {
    await started.promise
    assert.deepEqual(b.controller.run.pendingWindow, { start: 510, end: 540 })
    assert.equal(b.controller.run.completedFrames, 0)
    assert.deepEqual(b.order, [510])
    const cancelled = b.controller.cancel()
    assert.equal(b.controller.run.pendingWindow, undefined)
    first.resolve()
    assert.equal(await cancelled, true)
    assert.equal(b.stored.size, 0)
  } finally { first.resolve(); await b.controller.cancel() }
})

for (const expiresDuring of ['request', 'cache write']) {
  test(`a delayed expiry timer cannot publish progress or queue another window after expiry during ${expiresDuring}`, async t => {
    const b = bench({ duration: 60 }); t.after(() => b.controller.cancel())
    const { controller } = b, open = controller.open, render = controller.render
    const now = Date.now(); let writes = 0, progress = 0, expired = false
    controller.now = () => expired ? controller.run.grant.expiresAt : now
    controller.notify = () => { progress = Math.max(progress, controller.run.completedFrames) }
    controller.render = async (...args) => {
      const response = await render(...args)
      if (expiresDuring === 'request') expired = true
      return response
    }
    controller.open = async (...args) => {
      const cache = await open(...args)
      return { ...cache, async put(...values) {
        await cache.put(...values); writes++
        if (expiresDuring === 'cache write') expired = true
      } }
    }
    assert(controller.start(b.binding, 10))
    await controller.run.finished
    await controller.run.cleanup
    assert.deepEqual(b.order, [0])
    assert.equal(writes, expiresDuring === 'request' ? 0 : 1)
    assert.equal(progress, 0)
    assert.equal(controller.run.code, 'PROGRAM_RUN_EXPIRED')
    assert.equal(controller.run.cleaned, true)
    assert.equal(b.stored.size, 0)
  })
}

test('a late quota rejection purges earlier windows instead of offering partial playback', async t => {
  const b = bench(); t.after(() => b.controller.cancel())
  const { controller } = b, open = controller.open
  let now = Date.now(); controller.now = () => now
  controller.open = async (...args) => {
    const cache = await open(...args)
    return { ...cache, async put(start, blob) {
      if (start === 0) return cache.put(start, blob)
      now = controller.run.grant.expiresAt
      throw new DOMException('Storage is full', 'QuotaExceededError')
    } }
  }
  assert(controller.start(b.binding, 0))
  await controller.run.finished
  await controller.run.cleanup
  assert.deepEqual(b.order, [0, 30])
  assert.equal(controller.run.code, 'PROGRAM_RUN_EXPIRED')
  assert.equal(b.stored.size, 0)
})

test('cancel waits for a late grant and never reuses an unconfirmed cleanup', async () => {
  const issued = Promise.withResolvers(), acknowledge = Promise.withResolvers()
  let revokes = 0
  const b = bench({ async override(data) {
    if (data.op === 'authorize-preview-session') { issued.resolve(); await acknowledge.promise }
    if (data.op === 'revoke-preview-session' && ++revokes === 1) return { ok: false }
  } })
  assert(b.controller.start(b.binding, 10))
  await issued.promise
  const waiting = b.controller.read(b.binding, 10)
  const cancel = b.controller.cancel()
  assert.equal(b.controller.cancel(), cancel)
  await assert.rejects(waiting, { code: 'PROGRAM_RUN_CANCELLED' })
  assert.equal(b.controller.start(b.binding, 20), false)
  acknowledge.resolve()
  assert.equal(await cancel, false)
  assert.deepEqual(b.order, [])
  assert.equal(b.controller.run.code, 'PROGRAM_CLEANUP_UNCONFIRMED')
  assert.equal(b.controller.start(b.binding, 20), false)
  assert.equal(await b.controller.cancel(), true)
})

test('lost grant acknowledgement seals the exact attempt without minting again', async () => {
  const b = bench({ override(data) { if (data.op === 'authorize-preview-session') throw new Error('network disconnected') } })
  assert(b.controller.start(b.binding, 10))
  await b.controller.run.finished
  assert.equal(await b.controller.cancel(), true)
  assert.deepEqual(b.calls.map(item => item.op), ['authorize-preview-session', 'cancel-program-authorization'])
  assert.equal(b.calls[1].authorizationId, b.calls[0].authorizationId)
  assert.deepEqual(b.order, [])
})
