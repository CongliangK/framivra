// Host hardening regressions: cancel/reap ownership scope, monitor provider
// failures, buffered request bounds, optional media skips. In-process Host
// boundary fixtures only — no web server, no installed vtx.
import test from 'node:test'
import assert from 'node:assert/strict'
import { Readable } from 'node:stream'
import { createPreviewHost } from '../src/host.mjs'
import { MemoryFs } from './helpers.mjs'
import * as bounded from '../src/bounded.mjs'

const version = `sha256:${'a'.repeat(64)}`
const wire = value => JSON.stringify(value) + '\n'
const readEnvelope = operation => ({
  schema: 'framivra.timeline.read/v1', operation, ok: true, content_version: version, diagnostics: [],
  document: {}, coverage: { complete: true, scope: 'unit fixture', unexpanded: [] },
  ...(operation === 'capabilities'
    ? { content_version: null, capabilities: { operations: ['inspect', 'query', 'capabilities'], frame: { schema: 'framivra.timeline.frame/v1', explicit_render_required: true, inline_png: { flag: '--include-png', encoding: 'base64', wire_limit_bytes: 8 * 1024 * 1024 } } } }
    : {}),
})
const frameEnvelope = () => ({ schema: 'framivra.timeline.frame/v1', operation: 'frame', ok: true, content_version: version, diagnostics: [], image: { path: '/never/read.png' } })
const storeEnvelope = operation => ({ schema: 'framivra.timeline.store/v1', operation: operation.split(':')[1], ok: true, ...(operation === 'store:capabilities' ? { operations: ['edit'], edit: { operations: ['edit'] }, request_transport: { stdin: { flag: '--request-stdin', max_bytes: 8388608 } } } : {}) })

/** Deferred manual child handles so tests control exit/managed-range timing exactly.
 *  waitForExit consumes one outcome per cleanup attempt (default true), modeling
 *  a provider whose managed-range confirmation can fail and later recover. */
function makeBench() {
  const fs = new MemoryFs()
  const sessions = new Map([['one', { id: 'one' }], ['two', { id: 'two' }]])
  sessions.list = () => [...sessions.values()]
  const cwds = { one: '/workspace', two: '/workspace' }
  const workingDirectory = { get: session => cwds[session.id] }
  const bench = { manual: new Set(['frame']), spawned: [], waiters: [] }
  bench.onSpawned = op => new Promise(resolve => bench.waiters.push({ op, resolve }))
  function spawn(spec) {
    const argv = spec.argv.slice(1) // drop executable
    const op = argv[0] === '-t' ? (argv[2] === 'timeline' ? 'frame' : argv[2] === 'store' ? `store:${argv[3]}` : argv[2]) : 'unknown'
    const text = wire(op === 'frame' ? frameEnvelope() : op.startsWith('store:') ? storeEnvelope(op) : readEnvelope(op))
    let doneResolve, terminated = 0
    const rangeOutcomes = []
    const done = new Promise(resolve => { doneResolve = resolve })
    const handle = {
      op, spec, terminated: () => terminated,
      stdout: Readable.from([Buffer.from(text)]),
      done,
      terminate() { terminated++; doneResolve({ exitCode: null, signal: 'SIGTERM' }) },
      async waitForExit() { await done; return rangeOutcomes.shift() ?? true },
      settle() { doneResolve({ exitCode: 0 }) },
      failRange(times = 1) { for (let i = 0; i < times; i++) rangeOutcomes.push(false) },
      collected: { stdout: { readFrom: () => ({ text, lossy: false }) }, stderr: { readFrom: () => ({ text: '', lossy: false }) } },
    }
    bench.spawned.push(handle)
    for (const waiter of bench.waiters.filter(w => w.op === op)) waiter.resolve(handle)
    if (!bench.manual.has(op)) handle.settle()
    return handle
  }
  const subprocess = { resolveExecutable: async () => '/opt/trusted/vtx', spawn }
  const host = createPreviewHost({ fs, sessions, workingDirectory, subprocess }, { vtxExecutable: '/opt/trusted/vtx' })
  const control = (data, sessionId = 'one') => host.control(new Request('http://unit/api/framivra/control', { method: 'POST', body: typeof data === 'string' ? data : JSON.stringify({ sessionId, ...data }) }))
  const frame = (data, sessionId = 'one') => host.frame(new Request('http://unit/api/framivra/frame', { method: 'POST', body: JSON.stringify({ op: 'render', sessionId, frame: 0, contentVersion: version, renderConsent: 'cache/preview-only', operationId: crypto.randomUUID(), ...data }) }))
  const open = async sessionId => {
    const response = await control({ op: 'open', timeline: 'timeline.json' }, sessionId)
    assert.equal(response.status, 200, await response.clone().text())
    return (await response.json()).grant
  }
  return { host, fs, sessions, cwds, workingDirectory, ...bench, control, frame, open }
}

test('F1: frame cancel must not terminate another Session\'s in-flight read', async () => {
  const b = makeBench()
  try {
    const grantA = await b.open('one')
    const grantB = await b.open('two')
    b.manual.add('inspect')
    const rendering = b.frame({ grant: grantB }, 'two')
    const frameChild = await b.onSpawned('frame')
    // Session one's inspect starts while session two renders — reads coexist with a frame.
    const inspecting = b.control({ op: 'inspect', grant: grantA })
    const readChild = await b.onSpawned('inspect')
    const cancelling = b.frame({ op: 'cancel', grant: grantB }, 'two')
    await new Promise(resolve => setImmediate(resolve))
    // The cancel must scope its reaping to grant two's own children only.
    assert.equal(readChild.terminated(), 0, 'cross-Session read child was terminated by an unrelated frame cancel')
    frameChild.settle(); readChild.settle()
    assert.equal((await cancelling).status, 200)
    assert.notEqual((await rendering).status, 200)
    assert.equal((await inspecting).status, 200, 'the unrelated Session read must complete normally')
  } finally { await b.host.dispose().catch(() => {}) }
})

test('F1: failed cleanup retains ownership and a targeted retry reaps it without touching other Session children', async () => {
  const b = makeBench()
  try {
    const grantA = await b.open('one')
    const grantB = await b.open('two')
    b.manual.add('inspect')
    const rendering = b.frame({ grant: grantB }, 'two')
    const frameChild = await b.onSpawned('frame')
    frameChild.failRange(2) // render cleanup AND the first cancel retry both fail.
    const inspecting = b.control({ op: 'inspect', grant: grantA })
    const readChild = await b.onSpawned('inspect')
    const firstCancel = b.frame({ op: 'cancel', grant: grantB }, 'two')
    assert.equal((await firstCancel).status, 502, 'unconfirmed managed range must fail closed')
    assert.equal(readChild.terminated(), 0, 'a failed cleanup retry must stay scoped to its owner')
    const secondCancel = b.frame({ op: 'cancel', grant: grantB }, 'two')
    assert.equal((await secondCancel).status, 200, 'a targeted retry reaps the retained child once the range confirms')
    readChild.settle()
    assert.equal((await inspecting).status, 200)
    assert.notEqual((await rendering).status, 200)
  } finally { await b.host.dispose().catch(() => {}) }
})

test('F2: a throwing workingDirectory provider inside the frame monitor aborts fail-closed instead of escaping the timer', async () => {
  const b = makeBench()
  const realSetInterval = globalThis.setInterval
  const timers = []
  globalThis.setInterval = (fn, ms, ...rest) => { timers.push(fn); const timer = realSetInterval(() => {}, 1 << 30, ...rest); timer.unref(); return timer }
  try {
    const grant = await b.open('one')
    const rendering = b.frame({ grant })
    await b.onSpawned('frame')
    await new Promise(resolve => setImmediate(resolve))
    const probe = timers.at(-1)
    assert(typeof probe === 'function')
    b.workingDirectory.get = () => { throw new Error('projection fold failed') }
    assert.doesNotThrow(() => probe(), 'a provider failure must not escape as an uncaught timer exception')
    b.spawned.at(-1).settle()
    const response = await rendering
    assert.equal(response.status, 499, `fail-closed abort expected, got ${response.status}: ${await response.text()}`)
  } finally {
    globalThis.setInterval = realSetInterval
    await b.host.dispose()
  }
})

test('F2: a throwing sessions provider inside the editControl monitor aborts the store child fail-closed', async () => {
  const b = makeBench()
  const realSetInterval = globalThis.setInterval
  const timers = []
  globalThis.setInterval = (fn, ms, ...rest) => { timers.push(fn); const timer = realSetInterval(() => {}, 1 << 30, ...rest); timer.unref(); return timer }
  try {
    const grant = await b.open('one')
    const authorized = await b.control({ op: 'authorize-write', grant })
    assert.equal(authorized.status, 200, await authorized.clone().text())
    const { writeGrant } = await authorized.json()
    b.manual.add('store:status')
    const status = b.control({ op: 'status', grant, writeGrant })
    await b.onSpawned('store:status')
    await new Promise(resolve => setImmediate(resolve))
    const probe = timers.at(-1)
    assert(typeof probe === 'function')
    b.sessions.get = () => { throw new Error('session store failed') }
    assert.doesNotThrow(() => probe())
    b.spawned.at(-1).settle()
    const response = await status
    assert.equal(response.status, 200, await response.clone().text())
    assert.equal((await response.json()).code, 'outcome_unknown', 'an aborted mutating store reports outcome_unknown, never a hang or a crash')
  } finally {
    globalThis.setInterval = realSetInterval
    await b.host.dispose()
  }
})

test('F3: control and diagnostics bodies are bounded before text/JSON allocation', async () => {
  const limit = bounded.CONTROL_REQUEST_LIMIT ?? 16 * 1024 * 1024
  assert.equal(bounded.CONTROL_REQUEST_LIMIT, 8 * 1024 * 1024 + 64 * 1024, 'the control wire cap must exceed the 8 MiB semantic stdin request maximum')
  const b = makeBench()
  try {
    const oversized = ' '.repeat(limit + 1)
    const control = await b.control(oversized)
    assert.equal(control.status, 413, await control.clone().text())
    assert.equal((await control.json()).error, 'REQUEST_TOO_LARGE')
    const diagnostics = await b.host.diagnostics(new Request('http://unit/api/framivra/diagnostics', { method: 'POST', body: oversized }))
    assert.equal(diagnostics.status, 413, await diagnostics.clone().text())
    assert.equal((await diagnostics.json()).error, 'REQUEST_TOO_LARGE')
  } finally { await b.host.dispose().catch(() => {}) }
})

test('F4: an optional media path of the wrong type is skipped, not fatal; containment failures still fail', async () => {
  const b = makeBench()
  try {
    b.fs.files.set('/workspace/out/draft_final.mp4', { type: 'directory', version: 'draft-dir' })
    const response = await b.control({ op: 'open', timeline: 'timeline.json' })
    assert.equal(response.status, 200, await response.clone().text())
    const { media } = await response.json()
    assert.equal(media.length, 1)
    assert.equal(media[0].label, 'out/final.mp4')
    // A real containment violation is never skipped.
    b.fs.aliases.set('/workspace/out/final.mp4', '/outside/final.mp4')
    const escaped = await b.control({ op: 'open', timeline: 'timeline.json' })
    assert.equal(escaped.status, 403)
    assert.equal((await escaped.json()).error, 'PATH_ESCAPE')
  } finally { await b.host.dispose().catch(() => {}) }
})

test('F1: cancel-write is scoped to its own grant and leaves other Session reads in flight', async () => {
  const b = makeBench()
  try {
    const grantA = await b.open('one')
    const grantB = await b.open('two')
    const authorized = await b.control({ op: 'authorize-write', grant: grantB }, 'two')
    assert.equal(authorized.status, 200, await authorized.clone().text())
    const { writeGrant } = await authorized.json()
    b.manual.add('store:plan')
    b.manual.add('inspect')
    const planning = b.control({ op: 'plan', grant: grantB, writeGrant, base_revision: version, operations: [{ op: 'source.add', path: 'a.md', body: 'x' }] }, 'two')
    const planChild = await b.onSpawned('store:plan')
    const inspecting = b.control({ op: 'inspect', grant: grantA })
    const readChild = await b.onSpawned('inspect')
    const cancelling = b.control({ op: 'cancel-write', grant: grantB, writeGrant }, 'two')
    const cancelResponse = await cancelling
    assert.equal(cancelResponse.status, 200, await cancelResponse.clone().text())
    assert.equal(planChild.terminated(), 1, 'the canceled grant\'s own store child must be terminated')
    assert.equal(readChild.terminated(), 0, 'another Session\'s read child must not be terminated by cancel-write')
    readChild.settle()
    assert.equal((await inspecting).status, 200)
    assert.notEqual((await planning).status, 200)
  } finally { await b.host.dispose().catch(() => {}) }
})

test('agent-path ownership: a failed-cleanup plan stays retryable via cancel-write without wedging semanticBusy', async () => {
  const b = makeBench()
  try {
    const grant = await b.open('two')
    const authorized = await b.control({ op: 'authorize-write', grant }, 'two')
    assert.equal(authorized.status, 200, await authorized.clone().text())
    const { writeGrant } = await authorized.json()
    b.manual.add('store:plan')
    const planRequest = { op: 'plan', grant, writeGrant, base_revision: version, operations: [{ op: 'source.add', path: 'a.md', body: 'x' }] }
    const firstPlanPromise = b.control(planRequest, 'two')
    const planChild = await b.onSpawned('store:plan')
    planChild.failRange(1) // plan child exits fine but managed-range confirmation fails once
    planChild.settle()
    const firstPlan = await firstPlanPromise
    assert.equal(firstPlan.status, 502, 'unconfirmed managed range must fail closed')
    assert.equal((await firstPlan.json()).error, 'PROCESS_CLEANUP_FAILED')
    // While the failed cleanup is retained, another grant's semantic op is gated...
    // but cancel-write itself must remain available as THE explicit retry path.
    const cancelled = await b.control({ op: 'cancel-write', grant, writeGrant }, 'two')
    assert.equal(cancelled.status, 200, await cancelled.clone().text())
    assert.equal(planChild.terminated() >= 1, true, 'the targeted retry re-attempts the retained child')
    const secondPlan = b.control(planRequest, 'two')
    const retryChild = await b.onSpawned('store:plan')
    retryChild.settle()
    const response = await secondPlan
    assert.equal(response.status, 200, `semantic gate must be released after a confirmed retry, got ${response.status}: ${await response.text()}`)
  } finally { await b.host.dispose().catch(() => {}) }
})

test('open probes are owned by the pre-minted grant id: a failed open does not wedge semanticBusy', async () => {
  const b = makeBench()
  try {
    b.manual.add('capabilities'); b.manual.add('inspect')
    const opening = b.control({ op: 'open', timeline: 'timeline.json' }, 'two')
    const capChild = await b.onSpawned('capabilities')
    capChild.settle()
    const inspChild = await b.onSpawned('inspect')
    inspChild.failRange(1) // inspect cleanup fails once; open's scoped reap retry clears it
    inspChild.settle()
    const response = await opening
    assert.equal(response.status, 502, 'the failed probe must still surface as PROCESS_CLEANUP_FAILED')
    assert.equal((await response.json()).error, 'PROCESS_CLEANUP_FAILED')
    b.manual.delete('capabilities'); b.manual.delete('inspect') // session-one open children may auto-settle
    // Global semantic gate must not be wedged: another session's authorize+plan succeeds.
    const grant = await b.open('one')
    const authorized = await b.control({ op: 'authorize-write', grant })
    assert.equal(authorized.status, 200, await authorized.clone().text())
    const { writeGrant } = await authorized.json()
    b.manual.add('store:plan')
    const planning = b.control({ op: 'plan', grant, writeGrant, base_revision: version, operations: [{ op: 'source.add', path: 'a.md', body: 'x' }] })
    const planChild = await b.onSpawned('store:plan')
    planChild.settle()
    const planResponse = await planning
    assert.equal(planResponse.status, 200, `semanticBusy must be clear after the failed open's scoped retry, got ${await planResponse.text()}`)
  } finally { await b.host.dispose().catch(() => {}) }
})
