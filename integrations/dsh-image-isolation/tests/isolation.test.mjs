/**
 * dsh-image-isolation test suite.
 *
 * Runs the real official helpers against real raster work in forked child
 * processes. The parent test process never imports sharp directly, which the
 * final assertion proves via /proc/self/maps: no libvips, no sharp native
 * module — the containment property this package exists to deliver.
 *
 * Resource discipline: 8x8/32x32 fixtures, worker concurrency capped at 2,
 * no large images, per the host session's memory-pressure constraints.
 */

import { mkdtemp, readdir, readFile, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'
import assert from 'node:assert/strict'
import { Context } from '@deepseek-ai/cordis'
import { isAttachmentError } from '@deepseek-ai/dsh-attachment'
import IsolatedAttachmentStore, { makeSharedRequest } from '../src/index.mjs'
import { MAX_TOTAL_PENDING_BYTES, WorkerPool } from '../src/pool.mjs'

const here = fileURLToPath(new URL('.', import.meta.url))
const silentWorkerUrl = new URL('./fixtures/silent-worker.mjs', import.meta.url).href
const evilWorkerUrl = new URL('./fixtures/evil-worker.mjs', import.meta.url).href
const realWorkerUrl = new URL('../src/worker.mjs', import.meta.url).href

/** 8x8 opaque sRGB PNG. */
const OPAQUE_PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAgAAAAICAYAAADED76LAAAACXBIWXMAAAPoAAAD6AG1e1JrAAAAEklEQVQYlWM4IRf1Hx9mGBkKAAg+j8F41jRPAAAAAElFTkSuQmCC',
  'base64',
)
/** 8x8 fully transparent PNG. */
const TRANSPARENT_PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAgAAAAICAYAAADED76LAAAACXBIWXMAAAPoAAAD6AG1e1JrAAAADklEQVQYlWNgGAWgEAAAAQgAAa5MwN8AAAAASUVORK5CYII=',
  'base64',
)
/** 16x16 baseline JPEG. */
const SMALL_JPEG = Buffer.from(
  '/9j/2wBDAAYEBQYFBAYGBQYHBwYIChAKCgkJChQODwwQFxQYGBcUFhYaHSUfGhsjHBYWICwgIyYnKSopGR8tMC0oMCUoKSj/2wBDAQcHBwoIChMKChMoGhYaKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCj/wAARCAAQABADASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAb/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/8QAFAEBAAAAAAAAAAAAAAAAAAAABv/EABQRAQAAAAAAAAAAAAAAAAAAAAD/2gAMAwEAAhEDEQA/AJAAnHX/2Q==',
  'base64',
)

async function makeStore(overrides = {}) {
  const dshHome = await mkdtemp(join(tmpdir(), 'dsh-image-isolation-'))
  const app = new Context()
  const config = IsolatedAttachmentStore.Config({ dshHome, imageCompressionConcurrency: 2, ...overrides })
  const store = new IsolatedAttachmentStore(app, config)
  return { store, dshHome, cleanup: async () => { await store.workerPool.dispose().catch(() => {}); await rm(dshHome, { recursive: true, force: true }).catch(() => {}) } }
}

/** Count committed image files under the store root, for partial-commit checks. */
async function committedCount(dshHome) {
  const root = join(dshHome, 'attachments', 'v1')
  let total = 0
  async function walk(dir) {
    let entries = []
    try { entries = await readdir(dir, { withFileTypes: true }) } catch { return }
    for (const entry of entries) {
      if (entry.isDirectory()) await walk(join(dir, entry.name))
      else if (entry.isFile() && !entry.name.endsWith('.tmp')) total += 1
    }
  }
  await walk(root)
  return total
}

test('saveImage and readImage round-trip an opaque PNG through the worker', async () => {
  const { store, cleanup } = await makeStore()
  try {
    const ref = await store.saveImage({ data: OPAQUE_PNG, mediaType: 'image/png' })
    assert.equal(ref.mediaType, 'image/png')
    assert.equal(ref.width, 8)
    assert.equal(ref.height, 8)
    const stored = await store.readImage(ref)
    assert.equal(stored.ref.attachmentId, ref.attachmentId)
    assert.equal(stored.data.byteLength, ref.bytes)
  } finally { await cleanup() }
})

test('transparent PNG keeps its alpha through save and request encoding', async () => {
  const { store, cleanup } = await makeStore()
  try {
    const ref = await store.saveImage({ data: TRANSPARENT_PNG, mediaType: 'image/png' })
    const request = await store.readImageRequest(ref, { width: 8, height: 8, maxBytes: 1 << 20 })
    assert.equal(request.hasAlpha, true)
  } finally { await cleanup() }
})

test('JPEG admission works through the isolated validator', async () => {
  const { store, cleanup } = await makeStore()
  try {
    await store.validateImage({ data: SMALL_JPEG, mediaType: 'image/jpeg' })
    const ref = await store.saveImage({ data: SMALL_JPEG, mediaType: 'image/jpeg' })
    assert.equal(ref.width, 16)
  } finally { await cleanup() }
})

test('malformed image bytes are refused with a routing-stable error', async () => {
  const { store, cleanup } = await makeStore()
  try {
    await assert.rejects(
      store.saveImage({ data: Buffer.from('definitely not an image'), mediaType: 'image/png' }),
      (error) => isAttachmentError(error) && error.code === 'INVALID_IMAGE',
    )
  } finally { await cleanup() }
})

test('over-dimension images are refused at admission', async () => {
  const { store, cleanup } = await makeStore({ maxImageDimension: 4 })
  try {
    await assert.rejects(
      store.validateImage({ data: OPAQUE_PNG, mediaType: 'image/png' }),
      (error) => isAttachmentError(error),
    )
  } finally { await cleanup() }
})

test('batch save with one bad image commits nothing', async () => {
  const { store, dshHome, cleanup } = await makeStore()
  try {
    const before = await committedCount(dshHome)
    await assert.rejects(
      store.saveImages([
        { data: OPAQUE_PNG, mediaType: 'image/png' },
        { data: Buffer.from('broken'), mediaType: 'image/png' },
      ]),
      (error) => isAttachmentError(error),
    )
    assert.equal(await committedCount(dshHome), before)
  } finally { await cleanup() }
})

test('valid batch commits every image in order', async () => {
  const { store, cleanup } = await makeStore()
  try {
    const refs = await store.saveImages([
      { data: OPAQUE_PNG, mediaType: 'image/png' },
      { data: TRANSPARENT_PNG, mediaType: 'image/png' },
    ])
    assert.equal(refs.length, 2)
    assert.notEqual(refs[0].attachmentId, refs[1].attachmentId)
  } finally { await cleanup() }
})

test('request images hit the deterministic variant and re-request reuses it', async () => {
  const { store, cleanup } = await makeStore()
  try {
    const ref = await store.saveImage({ data: OPAQUE_PNG, mediaType: 'image/png' })
    const first = await store.readImageRequest(ref, { width: 4, height: 4, maxBytes: 1 << 20 })
    assert.equal(first.width, 4)
    assert.equal(first.height, 4)
    assert.equal(first.space, 'srgb')
    assert.equal(first.depth, 'uchar')
    const second = await store.readImageRequest(ref, { width: 4, height: 4, maxBytes: 1 << 20 })
    assert.equal(String(second.variantId), String(first.variantId))
  } finally { await cleanup() }
})

test('killing a busy worker fails only its job; the next job succeeds on a fresh worker', async () => {
  const { store, cleanup } = await makeStore()
  try {
    const doomed = store.saveImage({ data: OPAQUE_PNG, mediaType: 'image/png' })
    // The job starts synchronously in run(); kill the worker before it can reply.
    const busy = store.workerPool.active
    assert.equal(busy.length, 1)
    busy[0].child.kill('SIGKILL')
    await assert.rejects(doomed, (error) => isAttachmentError(error) && error.code === 'ATTACHMENT_WRITE_FAILED')
    const ref = await store.saveImage({ data: OPAQUE_PNG, mediaType: 'image/png' })
    assert.equal(ref.width, 8)
  } finally { await cleanup() }
})

test('per-job timeout kills the worker and fails the operation without retry', async () => {
  const pool = new WorkerPool({
    workerUrl: silentWorkerUrl,
    concurrency: 1,
    timeoutMs: 500,
    maxQueue: 2,
  })
  try {
    await assert.rejects(
      pool.run('validate', { input: { data: OPAQUE_PNG, mediaType: 'image/png' } }),
      (error) => isAttachmentError(error) && error.code === 'ATTACHMENT_WRITE_FAILED' && /timed out/.test(error.message),
    )
    // The record is fully reaped only after the real close was observed.
    assert.equal(pool.active.length, 0)
    assert.equal(pool.idle.length, 0)
    assert.equal(pool.workers.size, 0)
  } finally { await pool.dispose().catch(() => {}) }
})

test('aborting a job kills its worker, rejects with the caller reason, and frees the slot', async () => {
  const pool = new WorkerPool({
    workerUrl: silentWorkerUrl,
    concurrency: 1,
    timeoutMs: 30_000,
    maxQueue: 2,
  })
  try {
    const controller = new AbortController()
    const job = pool.run('validate', { input: { data: OPAQUE_PNG, mediaType: 'image/png' } }, controller.signal)
    const worker = pool.active[0]
    const exited = worker.exited
    const reason = new Error('caller cancelled')
    controller.abort(reason)
    await assert.rejects(job, (error) => error === reason)
    await exited
    assert.equal(worker.child.signalCode, 'SIGKILL')
    assert.equal(pool.active.length, 0)
  } finally { await pool.dispose().catch(() => {}) }
})

test('dispose rejects pending work and waits for real child exit', async () => {
  const pool = new WorkerPool({
    workerUrl: silentWorkerUrl,
    concurrency: 1,
    timeoutMs: 60_000,
    maxQueue: 4,
  })
  const running = pool.run('validate', { input: { data: OPAQUE_PNG, mediaType: 'image/png' } })
  const queued = pool.run('validate', { input: { data: OPAQUE_PNG, mediaType: 'image/png' } })
  const worker = pool.active[0]
  // Attach rejection handlers before dispose fires them, to avoid unhandled rejections.
  const runningCheck = assert.rejects(running, (error) => /disposed/.test(error.message) || isAttachmentError(error))
  const queuedCheck = assert.rejects(queued, (error) => /disposed/.test(error.message))
  await pool.dispose()
  await runningCheck
  await queuedCheck
  // The child has really exited by the time dispose resolves.
  assert.ok(worker.child.exitCode !== null || worker.child.signalCode !== null)
  assert.equal(pool.active.length, 0)
  assert.equal(pool.idle.length, 0)
  assert.equal(pool.workers.size, 0)
})

test('queue bound refuses work when workers and queue are saturated', async () => {
  const pool = new WorkerPool({
    workerUrl: silentWorkerUrl,
    concurrency: 1,
    timeoutMs: 60_000,
    maxQueue: 1,
  })
  try {
    const first = pool.run('validate', { input: { data: OPAQUE_PNG, mediaType: 'image/png' } })
    const second = pool.run('validate', { input: { data: OPAQUE_PNG, mediaType: 'image/png' } })
    await assert.rejects(
      pool.run('validate', { input: { data: OPAQUE_PNG, mediaType: 'image/png' } }),
      (error) => isAttachmentError(error) && /saturated/.test(error.message),
    )
    const firstCheck = assert.rejects(first, () => true)
    const secondCheck = assert.rejects(second, () => true)
    await pool.dispose()
    await firstCheck
    await secondCheck
  } finally { await pool.dispose().catch(() => {}) }
})

test('shared request-image: one waiter aborting leaves the operation for the others', async () => {
  const { store, cleanup } = await makeStore()
  try {
    const ref = await store.saveImage({ data: OPAQUE_PNG, mediaType: 'image/png' })
    const a = new AbortController()
    const b = new AbortController()
    const first = store.readImageRequest(ref, { width: 4, height: 4, maxBytes: 1 << 20 }, a.signal)
    const second = store.readImageRequest(ref, { width: 4, height: 4, maxBytes: 1 << 20 }, b.signal)
    a.abort(new Error('first waiter out'))
    await assert.rejects(first, (error) => /first waiter out/.test(error.message))
    const result = await second
    assert.equal(result.width, 4)
  } finally { await cleanup() }
})

test('oversized batches are refused by the shared admission policy', async () => {
  const { store, cleanup } = await makeStore({ maxImagesPerMessage: 1 })
  try {
    await assert.rejects(
      store.saveImages([
        { data: OPAQUE_PNG, mediaType: 'image/png' },
        { data: TRANSPARENT_PNG, mediaType: 'image/png' },
      ]),
      (error) => isAttachmentError(error) && error.code === 'TOO_MANY_IMAGES',
    )
  } finally { await cleanup() }
})

test('transfer-bound guard refuses oversized payloads before any IPC send', async () => {
  const { store, cleanup } = await makeStore()
  try {
    // A declared (not allocated) over-bound buffer: refused before any IPC.
    const huge = { byteLength: 257 * 1024 * 1024 }
    await assert.rejects(
      store.saveImage({ data: huge, mediaType: 'image/png' }),
      (error) => isAttachmentError(error) && /transfer bound/.test(error.message),
    )
    // Nothing was spawned for the refused job.
    assert.equal(store.workerPool.active.length, 0)
    assert.equal(store.workerPool.workers.size, 0)
  } finally { await cleanup() }
})

test('parent process maps contain no libvips and no sharp native module', async () => {
  const { store, cleanup } = await makeStore()
  try {
    await store.validateImage({ data: OPAQUE_PNG, mediaType: 'image/png' })
    await store.saveImage({ data: TRANSPARENT_PNG, mediaType: 'image/png' })
    const maps = await readFile('/proc/self/maps', 'utf8')
    assert.equal(/libvips/.test(maps), false, 'libvips must never load in the parent process')
    assert.equal(/sharp/.test(maps), false, 'sharp native module must never load in the parent process')
  } finally { await cleanup() }
})

// --- pool hardening: constructor validation, queued aborts, wire validation ---

test('WorkerPool constructor rejects invalid direct options', () => {
  const base = { workerUrl: silentWorkerUrl, concurrency: 1, timeoutMs: 1000, maxQueue: 0 }
  const variants = [
    [{ ...base, timeoutMs: undefined }, /timeoutMs/],
    [{ ...base, timeoutMs: 0 }, /timeoutMs/],
    [{ ...base, concurrency: 0 }, /concurrency/],
    [{ ...base, maxQueue: -1 }, /maxQueue/],
    [{ ...base, workerUrl: '' }, /workerUrl/],
    [{ ...base, warn: 123 }, /warn/],
  ]
  for (const [options, pattern] of variants) {
    assert.throws(() => new WorkerPool(options), pattern)
  }
})

test('a pre-aborted signal rejects the job without spawning any worker', async () => {
  const pool = new WorkerPool({ workerUrl: silentWorkerUrl, concurrency: 2, timeoutMs: 60_000, maxQueue: 4 })
  try {
    const controller = new AbortController()
    const reason = new Error('too late')
    controller.abort(reason)
    await assert.rejects(pool.run('validate', { input: { data: OPAQUE_PNG } }, controller.signal), (error) => error === reason)
    assert.equal(pool.workers.size, 0)
    assert.equal(pool.queue.length, 0)
  } finally { await pool.dispose().catch(() => {}) }
})

test('aborting a queued job removes it, refunds the byte budget, and never runs it', async () => {
  const pool = new WorkerPool({ workerUrl: silentWorkerUrl, concurrency: 1, timeoutMs: 500, maxQueue: 4 })
  try {
    const busy = pool.run('validate', { input: { data: OPAQUE_PNG } })
    const busyCheck = assert.rejects(busy, () => true)
    const controller = new AbortController()
    const queued = pool.run('validate', { input: { data: OPAQUE_PNG } }, controller.signal)
    assert.equal(pool.queue.length, 1)
    const reason = new Error('queued caller cancelled')
    controller.abort(reason)
    await assert.rejects(queued, (error) => error === reason)
    assert.equal(pool.queue.length, 0)
    assert.equal(pool.pendingBytes, 0)
    await busyCheck
  } finally { await pool.dispose().catch(() => {}) }
})

test('wire noise before a malformed reply is ignored; the malformed reply kills the worker', async () => {
  const pool = new WorkerPool({ workerUrl: evilWorkerUrl, concurrency: 1, timeoutMs: 5_000, maxQueue: 2 })
  try {
    await assert.rejects(
      pool.run('garbage', {}),
      (error) => isAttachmentError(error) && /malformed reply/.test(error.message),
    )
    assert.equal(pool.workers.size, 0)
    // A fresh worker is spawned for the next job and behaves the same way.
    await assert.rejects(
      pool.run('garbage', {}),
      (error) => isAttachmentError(error) && /malformed reply/.test(error.message),
    )
    assert.equal(pool.workers.size, 0)
  } finally { await pool.dispose().catch(() => {}) }
})

test('an over-bound worker result is refused, kills the worker, and never resolves the job', async () => {
  const pool = new WorkerPool({ workerUrl: evilWorkerUrl, concurrency: 1, timeoutMs: 5_000, maxQueue: 2 })
  try {
    await assert.rejects(
      pool.run('oversized', {}),
      (error) => isAttachmentError(error) && /transfer bound/.test(error.message),
    )
    assert.equal(pool.workers.size, 0)
    // The next job runs on a fresh worker; the pool is not poisoned.
    await assert.rejects(
      pool.run('oversized', {}),
      (error) => isAttachmentError(error) && /transfer bound/.test(error.message),
    )
    assert.equal(pool.workers.size, 0)
  } finally { await pool.dispose().catch(() => {}) }
})

test('the total pending byte budget refuses further queued work', async () => {
  const pool = new WorkerPool({ workerUrl: silentWorkerUrl, concurrency: 1, timeoutMs: 400, maxQueue: 8 })
  try {
    const busy = pool.run('validate', { input: { data: OPAQUE_PNG } })
    const busyCheck = assert.rejects(busy, () => true)
    const bigPayload = { input: { data: { byteLength: Math.floor(MAX_TOTAL_PENDING_BYTES * 0.75) } } }
    const queued = pool.run('validate', bigPayload) // fits (75% of budget)
    const queuedCheck = assert.rejects(queued, () => true)
    await assert.rejects(
      pool.run('validate', bigPayload), // would exceed the budget
      (error) => isAttachmentError(error) && /byte budget/.test(error.message),
    )
    assert.equal(pool.queue.length, 1)
    await busyCheck
    await queuedCheck
  } finally { await pool.dispose().catch(() => {}) }
})

test('a throwing warn sink never disturbs the pool lifecycle', async () => {
  // Borrow real limits/policy from a store so the worker op succeeds normally.
  const { store, cleanup } = await makeStore()
  let pool
  try {
    pool = new WorkerPool({
      workerUrl: realWorkerUrl,
      concurrency: 1,
      timeoutMs: 10_000,
      maxQueue: 1,
      warn: () => { throw new Error('sink exploded') },
    })
    await pool.run('validate', {
      input: { data: OPAQUE_PNG, mediaType: 'image/png' },
      limits: store.imageLimits,
      policy: store.normalizationPolicy,
    })
  } finally {
    if (pool !== undefined) await pool.dispose()
    await cleanup()
  }
  assert.equal(pool.workers.size, 0)
})

// --- SharedRequest single-lifecycle semantics ---

test('makeSharedRequest: settled outcome is replayed to late joiners with no dangling listeners', async () => {
  let runSignal
  const shared = makeSharedRequest((signal) => { runSignal = signal; return Promise.resolve(42) })
  await shared.finished
  const controller = new AbortController()
  const late = shared.join(controller.signal)
  assert.equal(await late, 42)
  assert.equal(controller.signal.aborted, false)
  // The late joiner's abort listener was removed on settle.
  controller.abort(new Error('after the fact'))
})

test('makeSharedRequest: the last waiter out cancels the operation, earlier aborts do not', async () => {
  let observedSignal
  const shared = makeSharedRequest((signal) => new Promise((resolve, reject) => {
    observedSignal = signal
    signal.addEventListener('abort', () => reject(new Error('operation cancelled by last waiter')), { once: true })
    setTimeout(() => resolve('done'), 2_000)
  }))
  const first = new AbortController()
  const second = new AbortController()
  const a = shared.join(first.signal)
  const b = shared.join(second.signal)
  first.abort(new Error('first out'))
  await assert.rejects(a, (error) => /first out/.test(error.message))
  assert.equal(observedSignal.aborted, false)
  const reason = new Error('last waiter out')
  second.abort(reason)
  await assert.rejects(b, (error) => error === reason)
  await shared.finished // resolves after storing the cancelled outcome
  assert.equal(observedSignal.aborted, true)
})

// --- config schema bounds ---

test('Config schema enforces upper bounds on the worker knobs', () => {
  assert.throws(() => IsolatedAttachmentStore.Config({ dshHome: '/tmp/x', workerMaxQueue: 1e9 }), /workerMaxQueue|expected|larger/)
  assert.throws(() => IsolatedAttachmentStore.Config({ dshHome: '/tmp/x', workerTimeoutMs: 999 }), /workerTimeoutMs|expected|larger/)
  const sane = IsolatedAttachmentStore.Config({ dshHome: '/tmp/x' })
  assert.equal(sane.workerTimeoutMs, 30_000)
  assert.equal(sane.workerMaxQueue, 64)
})

test('B1 regression: a new no-signal caller after last-waiter cancellation gets a fresh operation', async () => {
  const { store, cleanup } = await makeStore()
  try {
    const ref = await store.saveImage({ data: OPAQUE_PNG, mediaType: 'image/png' })
    const target = { width: 4, height: 4, maxBytes: 1 << 20 }
    // Controllable delay: the shared operation stays in flight long enough to
    // cancel and immediately re-request the same variant, as in the review repro.
    const innerRun = store.workerPool.run.bind(store.workerPool)
    store.workerPool.run = async (op, payload, signal) => {
      await new Promise((resolve) => setTimeout(resolve, 40))
      return innerRun(op, payload, signal)
    }
    const controller = new AbortController()
    const first = store.readImageRequest(ref, target, controller.signal)
    await new Promise((resolve) => setTimeout(resolve, 10)) // let the shared op start
    controller.abort(new Error('only waiter out'))
    await assert.rejects(first, (error) => /only waiter out/.test(error.message))
    const second = store.readImageRequest(ref, target) // no signal, same variant
    const result = await second
    assert.equal(result.width, 4)
    // The inflight map no longer holds the cancelled entry (allow cleanup microtask).
    await new Promise((resolve) => setTimeout(resolve, 5))
    assert.equal(store.requestInflight.size, 0)
  } finally { await cleanup() }
})

// --- m00828 review hardening: strict protocol, channel errors, listener hygiene ---

test('auditWireBytes counts UTF-8 strings, refuses nonsense, and never under-reports', async () => {
  const { auditWireBytes } = await import('../src/protocol.mjs')
  // Strings are not free.
  assert.equal(auditWireBytes({ note: 'héllo' }), Buffer.byteLength('héllo', 'utf8'))
  // Real typed buffers count their true length.
  assert.equal(auditWireBytes({ data: new Uint8Array(1024) }), 1024)
  // Declared byteLength on a plain object counts (no allocation needed).
  assert.equal(auditWireBytes({ data: { byteLength: 768 * 1024 * 1024 } }), 768 * 1024 * 1024)
  // ...even nested deep: depth alone must not zero out the audit (review repro: depth 9 declared 768MiB audited 0).
  const deep = { a: { b: { c: { d: { e: { f: { g: { h: { i: { byteLength: 768 * 1024 * 1024 } } } } } } } } } }
  assert.equal(auditWireBytes(deep), NaN)
  // Negative declarations are nonsense, not zero.
  assert.equal(auditWireBytes({ data: { byteLength: -5 } }), NaN)
  // Over-wide containers are nonsense too.
  const wide = {}
  for (let index = 0; index < 65; index += 1) wide[`k${index}`] = 1
  assert.equal(auditWireBytes(wide), NaN)
})

test('WorkerPool constructor caps public options with TypeErrors', () => {
  const base = { workerUrl: silentWorkerUrl }
  assert.throws(() => new WorkerPool({ ...base, concurrency: 9, timeoutMs: 1_000, maxQueue: 1 }), /concurrency/)
  assert.throws(() => new WorkerPool({ ...base, concurrency: 1, timeoutMs: 300_001, maxQueue: 1 }), /timeoutMs/)
  assert.throws(() => new WorkerPool({ ...base, concurrency: 1, timeoutMs: 1_000, maxQueue: 1_025 }), /maxQueue/)
  assert.throws(() => new WorkerPool({ ...base, concurrency: 1.5, timeoutMs: 1_000, maxQueue: 1 }), /concurrency/)
})

test('a channel error on an idle worker closes it via the real close event; the pool recovers', async () => {
  const pool = new WorkerPool({ workerUrl: realWorkerUrl, concurrency: 1, timeoutMs: 10_000, maxQueue: 1 })
  try {
    const limits = { maxImagesPerMessage: 4 }
    const policy = { maxPixels: 1 << 22, maxDimension: 8192, maxBytes: 20 * 1024 * 1024 }
    await pool.run('validate', { input: { data: OPAQUE_PNG, mediaType: 'image/png' }, limits, policy })
    assert.equal(pool.workers.size, 1)
    const [record] = pool.workers
    assert.equal(record.state, 'idle')
    // Synthetic channel error (send/kill failure, NOT an exit): must only
    // begin a close; the record may settle solely on the real close event.
    record.child.emit('error', new Error('EPIPE'))
    await record.exited
    assert.equal(pool.workers.size, 0)
    assert.equal(record.state, 'closed')
    // The pool is usable afterwards: a fresh worker serves the next job.
    await pool.run('validate', { input: { data: OPAQUE_PNG, mediaType: 'image/png' }, limits, policy })
    assert.equal(pool.workers.size, 1)
  } finally { await pool.dispose().catch(() => {}) }
})

test('a channel error on a busy worker fails its job without retry and frees the slot on close', async () => {
  const pool = new WorkerPool({ workerUrl: silentWorkerUrl, concurrency: 1, timeoutMs: 10_000, maxQueue: 1 })
  try {
    const job = pool.run('validate', { input: { data: OPAQUE_PNG } })
    const jobCheck = assert.rejects(job, (error) => isAttachmentError(error) && /killed without retry/.test(error.message))
    await new Promise((resolve) => setTimeout(resolve, 150)) // worker is busy by now
    const [record] = pool.workers
    assert.equal(record.state, 'busy')
    record.child.emit('error', new Error('EPIPE'))
    await jobCheck
    await record.exited
    assert.equal(pool.workers.size, 0)
    assert.equal(record.state, 'closed')
  } finally { await pool.dispose().catch(() => {}) }
})

test('caller signals retain zero abort listeners after success, failure, and abort', async () => {
  const { getEventListeners } = await import('node:events')
  const limits = { maxImagesPerMessage: 4 }
  const policy = { maxPixels: 1 << 22, maxDimension: 8192, maxBytes: 20 * 1024 * 1024 }

  { // success
    const pool = new WorkerPool({ workerUrl: realWorkerUrl, concurrency: 1, timeoutMs: 10_000, maxQueue: 1 })
    const controller = new AbortController()
    try {
      await pool.run('validate', { input: { data: OPAQUE_PNG, mediaType: 'image/png' }, limits, policy }, controller.signal)
    } finally { await pool.dispose().catch(() => {}) }
    assert.equal(getEventListeners(controller.signal, 'abort').length, 0)
  }
  { // failure (malformed reply kills the worker)
    const pool = new WorkerPool({ workerUrl: evilWorkerUrl, concurrency: 1, timeoutMs: 5_000, maxQueue: 1 })
    const controller = new AbortController()
    try {
      await assert.rejects(pool.run('garbage', {}, controller.signal), () => true)
    } finally { await pool.dispose().catch(() => {}) }
    assert.equal(getEventListeners(controller.signal, 'abort').length, 0)
  }
  { // mid-flight abort
    const pool = new WorkerPool({ workerUrl: silentWorkerUrl, concurrency: 1, timeoutMs: 10_000, maxQueue: 1 })
    const controller = new AbortController()
    const reason = new Error('caller gone')
    try {
      const job = pool.run('validate', { input: { data: OPAQUE_PNG } }, controller.signal)
      const jobCheck = assert.rejects(job, (error) => error === reason)
      await new Promise((resolve) => setTimeout(resolve, 100))
      controller.abort(reason)
      await jobCheck
    } finally { await pool.dispose().catch(() => {}) }
    assert.equal(getEventListeners(controller.signal, 'abort').length, 0)
  }
  { // queued abort
    const pool = new WorkerPool({ workerUrl: silentWorkerUrl, concurrency: 1, timeoutMs: 400, maxQueue: 4 })
    const controller = new AbortController()
    const reason = new Error('queued caller gone')
    try {
      const busy = pool.run('validate', { input: { data: OPAQUE_PNG } })
      const busyCheck = assert.rejects(busy, () => true)
      const queued = pool.run('validate', { input: { data: OPAQUE_PNG } }, controller.signal)
      const queuedCheck = assert.rejects(queued, (error) => error === reason)
      controller.abort(reason)
      await queuedCheck
      await busyCheck
    } finally { await pool.dispose().catch(() => {}) }
    assert.equal(getEventListeners(controller.signal, 'abort').length, 0)
  }
})

test('readImageRequest with a pre-aborted signal rejects before any worker run', async () => {
  const { store, cleanup } = await makeStore()
  try {
    const ref = await store.saveImage({ data: OPAQUE_PNG, mediaType: 'image/png' })
    const innerRun = store.workerPool.run.bind(store.workerPool)
    let runs = 0
    store.workerPool.run = async (...args) => { runs += 1; return innerRun(...args) }
    const controller = new AbortController()
    const reason = new Error('pre-aborted request')
    controller.abort(reason)
    await assert.rejects(store.readImageRequest(ref, { width: 4, height: 4, maxBytes: 1 << 20 }, controller.signal), (error) => error === reason)
    assert.equal(runs, 0)
  } finally { await cleanup() }
})

test('a schema-violating success reply ({} for prepare) kills the worker and never counts as success', async () => {
  const pool = new WorkerPool({ workerUrl: evilWorkerUrl, concurrency: 1, timeoutMs: 5_000, maxQueue: 2 })
  try {
    await assert.rejects(
      pool.run('schema', {}),
      (error) => isAttachmentError(error) && /schema/.test(error.message),
    )
    assert.equal(pool.workers.size, 0)
    // Not poisoned: the same violation on a fresh worker is caught again.
    await assert.rejects(
      pool.run('schema', {}),
      (error) => isAttachmentError(error) && /schema/.test(error.message),
    )
    assert.equal(pool.workers.size, 0)
  } finally { await pool.dispose().catch(() => {}) }
})

test('a malformed error envelope (bad code shape, over-long message) kills the worker', async () => {
  const pool = new WorkerPool({ workerUrl: evilWorkerUrl, concurrency: 1, timeoutMs: 5_000, maxQueue: 2 })
  try {
    await assert.rejects(
      pool.run('baderror', {}),
      (error) => isAttachmentError(error) && /malformed error envelope/.test(error.message),
    )
    assert.equal(pool.workers.size, 0)
    // The over-long message never reached the caller.
  } finally { await pool.dispose().catch(() => {}) }
})

test('the worker self-terminates when its IPC channel disconnects', async () => {
  const { fork } = await import('node:child_process')
  const child = fork(fileURLToPath(realWorkerUrl), [], { stdio: ['pipe', 'pipe', 'pipe', 'ipc'] })
  const exited = new Promise((resolve) => { child.once('exit', (code, signal) => resolve({ code, signal })) })
  // Give the worker a moment to install its disconnect handler, then sever
  // the channel exactly as a hard parent exit would.
  await new Promise((resolve) => setTimeout(resolve, 200))
  child.disconnect()
  const { code, signal } = await Promise.race([
    exited,
    new Promise((resolve) => setTimeout(() => resolve({ code: 'timeout', signal: null }), 5_000)),
  ])
  try { child.kill('SIGKILL') } catch { /* reaped */ }
  assert.equal(code, 75)
  assert.equal(signal, null)
})

test('the real requestImage result passes the strict schema including Uint8Array data and string depth', async () => {
  const { validateOpResult } = await import('../src/protocol.mjs')
  const result = {
    variantId: 'sha256:' + 'a'.repeat(64),
    attachment: { attachmentId: 'sha256:' + 'b'.repeat(64), mediaType: 'image/png', width: 8, height: 8, bytes: 96 },
    data: new Uint8Array(60), // official helper hands back a typed view, not always a Buffer
    mediaType: 'image/webp',
    bytes: 60,
    width: 4,
    height: 4,
    depth: 'uchar', // sharp reports depth as a name, not a number
    space: 'srgb',
    hasAlpha: false,
  }
  assert.equal(validateOpResult('requestImage', result), true)
  // Tampering with any field shape must fail.
  for (const mutate of [
    (r) => { r.depth = 8 },
    (r) => { r.data = { byteLength: 'lots' } },
    (r) => { delete r.hasAlpha },
    (r) => { r.bytes = 0 },
    (r) => { r.variantId = '' },
  ]) {
    const copy = structuredClone(result)
    copy.data = new Uint8Array(60)
    mutate(copy)
    assert.equal(validateOpResult('requestImage', copy), false)
  }
})

test('declared byteLength never hides sibling payloads or bypasses container limits', async () => {
  const { auditWireBytes, MAX_TRANSFER_BYTES } = await import('../src/protocol.mjs')
  assert.equal(auditWireBytes({ byteLength: 0, data: new Uint8Array(7), note: '中' }), 10)
  assert.equal(auditWireBytes({ byteLength: 0, data: { byteLength: MAX_TRANSFER_BYTES + 1 } }), MAX_TRANSFER_BYTES + 1)
  const wide = { byteLength: 0 }
  for (let i = 0; i < 64; i++) wide[`k${i}`] = null
  assert(Number.isNaN(auditWireBytes(wide)))
  assert(Number.isNaN(auditWireBytes({ ['x'.repeat(257)]: null })))
})

test('result metadata agrees with actual bytes and rejects non-byte typed views', async () => {
  const { validateOpResult } = await import('../src/protocol.mjs')
  const ref = { attachmentId: 'sha256:' + 'a'.repeat(64), mediaType: 'image/png', bytes: 3, width: 8, height: 8 }
  assert.equal(validateOpResult('prepare', { data: new Uint8Array(3), ref }), true)
  assert.equal(validateOpResult('prepare', { data: new Uint8Array(4), ref }), false)
  assert.equal(validateOpResult('readImage', { data: new Uint16Array(3), ref: { ...ref, bytes: 6 } }), false)
  assert.equal(validateOpResult('prepare', { data: new Uint8Array(3), ref: { ...ref, attachmentId: 'not-a-digest' } }), false)
  assert.equal(validateOpResult('prepare', { data: new Uint8Array(3), ref: { ...ref, width: Number.MAX_SAFE_INTEGER + 1 } }), false)
  const request = { variantId: 'variant', attachment: ref, data: new Uint8Array(3), mediaType: 'image/webp', bytes: 4, width: 4, height: 4, depth: 'uchar', space: 'srgb', hasAlpha: false }
  assert.equal(validateOpResult('requestImage', request), false)
  request.bytes = 3
  assert.equal(validateOpResult('requestImage', request), true)
  request.attachment = {}
  assert.equal(validateOpResult('requestImage', request), false)
})

test('hidden native containers audit as nonsense, never zero (fail-closed)', async () => {
  const { auditWireBytes } = await import('../src/protocol.mjs')
  // Raw containers whose payload lives outside own enumerable keys.
  assert(Number.isNaN(auditWireBytes(new Map([['x', new Uint8Array(7)]]))))
  assert(Number.isNaN(auditWireBytes(new Set([new Uint8Array(7)]))))
  assert(Number.isNaN(auditWireBytes(new WeakMap())))
  assert(Number.isNaN(auditWireBytes(new WeakSet())))
  assert(Number.isNaN(auditWireBytes(/hidden-source/)))
  assert(Number.isNaN(auditWireBytes(new Error('hidden message and stack'))))
  assert(Number.isNaN(auditWireBytes(new Date(0))))
  assert(Number.isNaN(auditWireBytes(new Number(7)))) // boxed primitive
  // Nested inside a wire-shaped object they still poison the audit.
  assert(Number.isNaN(auditWireBytes({ input: { data: new Set(['x'.repeat(100)]) } })))
  // Internal-slot judgment, not prototype cosmetics: a real Map with its
  // prototype swapped away is still refused; a plain object wearing
  // Map.prototype keeps auditing as a plain object (its keys are enumerable).
  const disguised = new Map([['x', new Uint8Array(7)]])
  Object.setPrototypeOf(disguised, null)
  assert(Number.isNaN(auditWireBytes(disguised)))
  const costume = Object.setPrototypeOf({ data: new Uint8Array(7) }, Map.prototype)
  assert.equal(auditWireBytes(costume), 7)
  // Legitimate wire containers do not regress.
  assert.equal(auditWireBytes({ input: { data: new Uint8Array(7) } }), 7)
  assert.equal(auditWireBytes(Object.assign(Object.create(null), { note: 'bcd' })), 3)
  assert.equal(auditWireBytes([new Uint8Array(4), 'xy']), 6)
  assert.equal(auditWireBytes(new ArrayBuffer(10)), 10)
})

test('public plugin disposal awaits the worker pool actual child close', async () => {
  const dshHome = await mkdtemp(join(tmpdir(), 'dsh-image-lifecycle-'))
  const app = new Context()
  const fork = app.plugin(IsolatedAttachmentStore, IsolatedAttachmentStore.Config({
    dshHome, imageCompressionConcurrency: 1,
  }))
  let store
  try {
    await fork
    assert.equal(fork.state, 2) // Public FiberState.ACTIVE const-enum value.
    store = app.attachments
    assert.ok(store instanceof IsolatedAttachmentStore)
    await store.validateImage({ data: OPAQUE_PNG, mediaType: 'image/png' })
    const ref = await store.saveImage({ data: OPAQUE_PNG, mediaType: 'image/png' })
    assert.equal(ref.width, 8)
    assert.equal((await store.saveImages([{ data: OPAQUE_PNG, mediaType: 'image/png' }])).length, 1)
    assert.equal((await store.readImage(ref)).ref.attachmentId, ref.attachmentId)
    assert.equal((await store.readImageRequest(ref, { width: 4, height: 4, maxBytes: 1 << 20 })).width, 4)
    const children = [...store.workerPool.workers].map(worker => worker.child)
    assert.equal(children.length, 1, 'exercise an actual persistent worker')
    await fork.dispose()
    assert.equal(fork.state, 4) // FiberState.DISPOSED.
    // Assert BEFORE teardown's safety cleanup: manual pool disposal must not
    // conceal a missing plugin-lifecycle hook.
    assert.equal(store.workerPool.workers.size, 0)
    assert.ok(children.every(child => child.exitCode !== null || child.signalCode !== null),
      'plugin disposal waits for every owned child real exit')
    assert.doesNotMatch(await readFile('/proc/self/maps', 'utf8'), /libvips|sharp-.*\.node/)
  } finally {
    await fork.dispose()
    if (store) await store.workerPool.dispose() // Safety cleanup after assertions.
    assert.ok(dshHome.startsWith(join(tmpdir(), 'dsh-image-lifecycle-')))
    await rm(dshHome, { recursive: true, force: true })
  }
})
