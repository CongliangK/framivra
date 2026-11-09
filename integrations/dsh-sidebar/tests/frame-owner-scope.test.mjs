// R3 regression: frame owner release must be scoped to the grant's own
// children. A SUCCESSFUL render that overlapped an unrelated grant/session
// read must not leave a sticky global frame owner (cross-session FRAME_BUSY
// wedge), while a FAILED cleanup must keep the owner until an actual reap.
import test from 'node:test'
import assert from 'node:assert/strict'
import { Readable } from 'node:stream'
import { randomUUID } from 'node:crypto'
import { createPreviewHost } from '../src/host.mjs'
import { MemoryFs } from './helpers.mjs'
import { FRAME_WIRE_LIMIT } from '../src/bounded.mjs'

const version = `sha256:${'a'.repeat(64)}`
function readValue(operation) {
  return operation === 'frame'
    ? { schema: 'framivra.timeline.frame/v1', operation, ok: true, diagnostics: [], content_version: version, image: { path: '/never/read.png' } }
    : { schema: 'framivra.timeline.read/v1', operation, ok: true, diagnostics: [], content_version: version, document: {}, coverage: { complete: true, scope: 'unit fixture', unexpanded: [] }, capabilities: { operations: ['inspect', 'query'], frame: { schema: 'framivra.timeline.frame/v1', explicit_render_required: true, inline_png: { flag: '--include-png', encoding: 'base64', wire_limit_bytes: FRAME_WIRE_LIMIT } } } }
}
function bench(handle) {
  const sessions = new Map([['one', { id: 'one' }], ['two', { id: 'two' }]])
  const host = createPreviewHost({ fs: new MemoryFs(), sessions, workingDirectory: { get: () => '/workspace' }, subprocess: {
    resolveExecutable: async () => '/opt/trusted/vtx',
    spawn(spec) {
      const operation = spec.argv[3] === 'timeline' ? spec.argv[4] : spec.argv[3]
      const text = JSON.stringify(readValue(operation)) + '\n'
      const readOut = value => ({ readFrom: () => ({ text: value, lossy: false }) })
      const base = extra => ({ terminate() {}, collected: { stdout: readOut(text), stderr: readOut('') }, ...extra })
      const custom = handle?.(operation)
      if (custom) return base(custom)
      return base({ stdout: Readable.from([Buffer.from(text)]), done: Promise.resolve({ exitCode: 0 }), waitForExit: async () => true })
    },
  } }, { vtxExecutable: '/opt/trusted/vtx' })
  const control = (data, sessionId = 'one') => host.control(new Request('http://unit/api/framivra/control', { method: 'POST', body: JSON.stringify({ sessionId, ...data }) }))
  const frame = (sessionId, grant) => host.frame(new Request('http://unit/api/framivra/frame', { method: 'POST', body: JSON.stringify({ op: 'render', sessionId, grant, frame: 0, contentVersion: version, renderConsent: 'cache/preview-only', operationId: randomUUID() }) }))
  const cancel = (sessionId, grant) => host.frame(new Request('http://unit/api/framivra/frame', { method: 'POST', body: JSON.stringify({ op: 'cancel', sessionId, grant }) }))
  const open = async (sessionId, timeline) => { const r = await control({ op: 'open', timeline }, sessionId); assert.equal(r.status, 200, await r.clone().text()); return (await r.json()).grant }
  return { host, control, frame, cancel, open }
}

test('successful render overlapping an UNRELATED grant read releases the frame owner (no global wedge)', async () => {
  let releaseFrame, releaseRead, readStarted
  const frameDone = new Promise(r => { releaseFrame = r }), readDone = new Promise(r => { releaseRead = r }), started = new Promise(r => { readStarted = r })
  const b = bench(operation => {
    if (operation === 'frame') return { stdout: Readable.from([Buffer.from(JSON.stringify(readValue(operation)) + '\n')]), done: frameDone, waitForExit: async () => true }
    if (operation === 'query') { readStarted(); return { stdout: Readable.from([Buffer.from(JSON.stringify(readValue(operation)) + '\n')]), done: readDone, waitForExit: async () => true } }
  })
  try {
    const grantA = await b.open('one', 'timeline.json')
    const grantB = await b.open('two', 'sub/nested.json')
    // Session A's render is admitted; session B's plain read overlaps it.
    const rendering = b.frame('one', grantA)
    await new Promise(r => setImmediate(r))
    const reading = b.control({ op: 'query', grant: grantB, query: {} }, 'two')
    await started
    // A's frame child exits cleanly while B's read is still active.
    releaseFrame({ exitCode: 0 })
    assert.equal((await rendering).status, 200)
    // Red condition under the old global check: the owner stayed sticky and
    // BOTH grants were then refused with 409 FRAME_BUSY forever.
    releaseRead({ exitCode: 0 }); await reading
    assert.equal((await b.frame('one', grantA)).status, 200, 'same grant must render again after its own child exited')
    assert.equal((await b.frame('two', grantB)).status, 200, 'unrelated grant must not inherit the wedge')
  } finally { releaseFrame({ exitCode: 1 }); releaseRead({ exitCode: 1 }); await b.host.dispose() }
})

test('failed cleanup keeps the frame owner until an actual reap succeeds', async () => {
  let exits = 0
  const b = bench(operation => operation === 'frame'
    ? { stdout: Readable.from([Buffer.from(JSON.stringify(readValue(operation)) + '\n')]), done: Promise.resolve({ exitCode: 0 }), waitForExit: async () => ++exits > 1 } // first managed-range exit fails, retry succeeds
    : undefined)
  try {
    const grant = await b.open('one', 'timeline.json')
    const first = await b.frame('one', grant)
    assert.notEqual(first.status, 200) // PROCESS_CLEANUP_FAILED surfaces on the render itself
    // Owner retained: own grant refused until a real reap.
    assert.equal((await b.frame('one', grant)).status, 409)
    const cancelled = await b.cancel('one', grant)
    assert.equal(cancelled.status, 200)
    assert.equal((await b.frame('one', grant)).status, 200, 'retry reap must clear the retained owner')
    assert.ok(exits >= 2)
  } finally { await b.host.dispose() }
})

test('successful render overlapping the SAME grant\'s plain read releases the frame owner (no same-grant wedge)', async () => {
  let releaseFrame, releaseRead, readStarted
  const frameDone = new Promise(r => { releaseFrame = r }), readDone = new Promise(r => { releaseRead = r }), started = new Promise(r => { readStarted = r })
  const b = bench(operation => {
    if (operation === 'frame') return { stdout: Readable.from([Buffer.from(JSON.stringify(readValue(operation)) + '\n')]), done: frameDone, waitForExit: async () => true }
    if (operation === 'query') { readStarted(); return { stdout: Readable.from([Buffer.from(JSON.stringify(readValue(operation)) + '\n')]), done: readDone, waitForExit: async () => true } }
  })
  try {
    const grant = await b.open('one', 'timeline.json')
    // One grant: its render is admitted, then its OWN plain query overlaps.
    const rendering = b.frame('one', grant)
    await new Promise(r => setImmediate(r))
    const reading = b.control({ op: 'query', grant, query: {} })
    await started
    // The frame child exits cleanly while the same grant's read is active.
    releaseFrame({ exitCode: 0 })
    assert.equal((await rendering).status, 200)
    // Red condition under the unfiltered activeFor(owner) check: this grant's
    // own read kept grant.frame set, so the next render was refused 409 forever.
    releaseRead({ exitCode: 0 }); await reading
    assert.equal((await b.frame('one', grant)).status, 200, 'same grant must render again once its own read finished')
  } finally { releaseFrame({ exitCode: 1 }); releaseRead({ exitCode: 1 }); await b.host.dispose() }
})

test('same-grant plain read with a FAILED cleanup still blocks renders via global admission until cancel reaps it', async () => {
  let exits = 0
  const b = bench(operation => operation === 'query'
    ? { stdout: Readable.from([Buffer.from(JSON.stringify(readValue(operation)) + '\n')]), done: Promise.resolve({ exitCode: 0 }), waitForExit: async () => ++exits > 1 } // first managed-range exit fails, retry succeeds
    : undefined)
  try {
    const grant = await b.open('one', 'timeline.json')
    const read = await b.control({ op: 'query', grant, query: {} })
    assert.notEqual(read.status, 200) // PROCESS_CLEANUP_FAILED: the read child did not exit
    // The retained failed-cleanup handle is still in the global active map:
    // renders must stay refused (admission unchanged, NOT relaxed by the
    // kind-scoped owner release) until an actual reap succeeds.
    assert.equal((await b.frame('one', grant)).status, 409, 'global admission must keep blocking while cleanupFailed read is retained')
    assert.equal((await b.cancel('one', grant)).status, 200)
    assert.equal((await b.frame('one', grant)).status, 200, 'cancel-reap clears the retained read; render admitted')
    assert.ok(exits >= 2)
  } finally { await b.host.dispose() }
})
