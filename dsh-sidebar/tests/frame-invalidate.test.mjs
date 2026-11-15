// Narrow regression: invalidate-plan stays available during in-flight reads/frames,
// while every mutating gate (plan/commit/frame/failed cleanup) keeps refusing.
import test from 'node:test'
import assert from 'node:assert/strict'
import { Readable } from 'node:stream'
import { randomUUID } from 'node:crypto'
import { createPreviewHost } from '../src/host.mjs'
import { FRAME_WIRE_LIMIT } from '../src/bounded.mjs'
import { MemoryFs } from './helpers.mjs'

const version = `sha256:${'a'.repeat(64)}`

async function bench(options = {}) {
  const fs = new MemoryFs(), sessions = new Map([['one', { id: 'one' }]]), cwd = { value: '/workspace' }, calls = []
  const resolves = {}
  const held = { terminated: 0 }
  held.started = new Promise(resolve => { held.startResolve = resolve })
  held.done = new Promise(resolve => { held.doneResolve = resolve })
  held.range = new Promise(resolve => { held.rangeResolve = resolve })
  let heldUsed = false
  const subprocess = {
    resolveExecutable: async () => '/opt/trusted/vtx',
    spawn(spec) {
      calls.push(spec)
      const store = spec.argv[3] === 'store'
      const operation = store ? spec.argv[4] : spec.argv[3] === 'timeline' ? spec.argv[4] : spec.argv[3]
      const value = store
        ? operation === 'capabilities'
          ? { schema: 'framivra.timeline.store/v1', operation, ok: true, diagnostics: [], operations: ['edit', 'commit', 'status', 'recover'], edit: { operations: ['project.resize'] }, request_transport: { stdin: { flag: '--request-stdin', max_bytes: 8388608 } } }
          : { schema: 'framivra.timeline.store/v1', operation, ok: true, diagnostics: [], op_id: randomUUID() }
        : operation === 'frame'
          ? { schema: 'framivra.timeline.frame/v1', operation, ok: true, diagnostics: [], content_version: version, image: { path: '/never/read.png' } }
        : operation === 'reference'
          ? { schema: 'framivra.timeline.reference/v1', operation, ok: true, diagnostics: [], content_version: version, document: { raw: '{}', content_version: version }, coverage: { complete: true, scope: 'unit fixture', unexpanded: [] }, image: { state: 'not-generated' }, selection: { tracks: [], blocks: [] } }
          : { schema: 'framivra.timeline.read/v1', operation, ok: true, diagnostics: [], content_version: version, document: {}, coverage: { complete: true, scope: 'unit fixture', unexpanded: [] }, capabilities: { operations: ['inspect', 'query'], frame: { schema: 'framivra.timeline.frame/v1', explicit_render_required: true, inline_png: { flag: '--include-png', encoding: 'base64', wire_limit_bytes: FRAME_WIRE_LIMIT } } } }
      const text = JSON.stringify(value) + '\n'
      if (options.hold === operation && !heldUsed) {
        heldUsed = true
        held.startResolve()
        let failedRange = options.failRangeOnce ? 1 : 0
        return {
          stdout: Readable.from([Buffer.from(text)]), done: held.done,
          terminate() { held.terminated++ }, waitForExit: () => failedRange-- > 0 ? Promise.resolve(false) : held.range,
          collected: { stdout: { readFrom: () => ({ text, lossy: false }) }, stderr: { readFrom: () => ({ text: '', lossy: false }) } },
        }
      }
      return { stdout: Readable.from([Buffer.from(text)]), done: Promise.resolve({ exitCode: 0 }), terminate() {}, waitForExit: async () => true, collected: { stdout: { readFrom: () => ({ text, lossy: false }) }, stderr: { readFrom: () => ({ text: '', lossy: false }) } } }
    },
  }
  const host = createPreviewHost({ fs, sessions, workingDirectory: { get: () => cwd.value }, subprocess }, { vtxExecutable: '/opt/trusted/vtx' })
  const control = data => host.control(new Request('http://unit/api/framivra/control', { method: 'POST', body: JSON.stringify({ sessionId: 'one', ...data }) }))
  const opened = await control({ op: 'open', timeline: 'timeline.json' }); assert.equal(opened.status, 200, await opened.clone().text())
  const { grant } = await opened.json()
  const authorized = await control({ op: 'authorize-write', grant })
  assert.equal(authorized.status, 200, await authorized.clone().text())
  const { writeGrant } = await authorized.json()
  const request = { op: 'render', sessionId: 'one', grant, frame: 0, contentVersion: version, renderConsent: 'cache/preview-only', operationId: randomUUID() }
  const frame = (data = {}) => host.frame(new Request('http://unit/api/framivra/frame', { method: 'POST', body: JSON.stringify({ ...request, operationId: randomUUID(), ...data }) }))
  const plan = async () => {
    const response = await control({ op: 'plan', grant, writeGrant, base_revision: version, operations: [{ op: 'project.resize', duration_frames: 130 }] })
    assert.equal(response.status, 200, await response.clone().text())
    return (await response.json()).planToken
  }
  return { host, fs, calls, held, grant, writeGrant, control, frame, plan }
}

test('in-flight reference: invalidate-plan succeeds, old plan token dies, no kernel write or spawn', async t => {
  const b = await bench({ hold: 'reference' })
  t.after(() => { b.held.doneResolve({ exitCode: 1 }); b.held.rangeResolve(true); return b.host.dispose() })
  const firstToken = await b.plan()
  const referencing = b.control({ op: 'reference', grant: b.grant, query: { expected_content_version: version, frame: 0 } })
  await b.held.started // The reference subprocess is now in flight in TimelineReader.active.
  const spawnsBefore = b.calls.length, storeSpawnsBefore = b.calls.filter(spec => spec.argv[3] === 'store').length
  const invalidated = await b.control({ op: 'invalidate-plan', grant: b.grant, writeGrant: b.writeGrant })
  assert.equal(invalidated.status, 200, await invalidated.clone().text())
  assert.equal((await invalidated.json()).ok, true)
  assert.equal(b.calls.length, spawnsBefore, 'invalidate-plan must not spawn')
  assert.equal(b.calls.filter(spec => spec.argv[3] === 'store').length, storeSpawnsBefore, 'invalidate-plan must not touch the store')
  // An ordinary read never blocks semantics: the invalidated token is refused
  // by the frozen-plan CAS itself, before any spawn or write.
  const staleDuringRead = await b.control({ op: 'commit', grant: b.grant, writeGrant: b.writeGrant, planToken: firstToken })
  assert.equal(staleDuringRead.status, 409)
  assert.match((await staleDuringRead.json()).message, /Plan expired or changed/)
  assert.equal(b.calls.length, spawnsBefore, 'refused commit must not spawn')
  // A fresh zero-write plan coexists with the in-flight reference.
  const freshDuringRead = await b.control({ op: 'plan', grant: b.grant, writeGrant: b.writeGrant, base_revision: version, operations: [{ op: 'project.resize', duration_frames: 130 }] })
  assert.equal(freshDuringRead.status, 200, await freshDuringRead.clone().text())
  const freshToken = (await freshDuringRead.json()).planToken
  // Once the read settles and is reaped, the frozen fresh token commits exactly once.
  b.held.doneResolve({ exitCode: 0 }); b.held.rangeResolve(true)
  assert.equal((await referencing).status, 200)
  const committed = await b.control({ op: 'commit', grant: b.grant, writeGrant: b.writeGrant, planToken: freshToken })
  assert.equal(committed.status, 200, await committed.clone().text())
  assert.equal((await committed.json()).ok, true)
  assert.equal(b.calls.filter(spec => spec.argv[3] === 'store' && spec.argv[4] === '--request-stdin').length, 1, 'exactly one kernel commit')
})

test('active actual frame: invalidate-plan succeeds, commit/other semantics stay blocked until cleanup is confirmed', async t => {
  const b = await bench({ hold: 'frame' })
  t.after(() => { b.held.doneResolve({ exitCode: 1 }); b.held.rangeResolve(true); return b.host.dispose() })
  // A held plan would itself refuse the frame route; start the real frame first.
  const rendering = b.frame(); await b.held.started
  const spawnsBefore = b.calls.length
  const invalidated = await b.control({ op: 'invalidate-plan', grant: b.grant, writeGrant: b.writeGrant })
  assert.equal(invalidated.status, 200, await invalidated.clone().text())
  assert.equal((await invalidated.json()).ok, true)
  assert.equal(b.calls.length, spawnsBefore, 'invalidate-plan must not spawn during a frame')
  const busyCommit = await b.control({ op: 'commit', grant: b.grant, writeGrant: b.writeGrant, planToken: randomUUID() })
  assert.equal(busyCommit.status, 409)
  assert.match((await busyCommit.json()).message, /Cancel the frame and confirm actual process reaping/)
  const busyAuthorize = await b.control({ op: 'authorize-write', grant: b.grant })
  assert.equal(busyAuthorize.status, 409)
  assert.equal(b.calls.length, spawnsBefore, 'blocked semantics must not spawn')
  // Cancel settled the child but the managed range has not exited: still blocked.
  const cancelling = b.frame({ op: 'cancel' })
  b.held.doneResolve({ exitCode: 0 }); await new Promise(resolve => setImmediate(resolve))
  const stillBusy = await b.control({ op: 'authorize-write', grant: b.grant })
  assert.equal(stillBusy.status, 409)
  // Actual range exit confirms cleanup and releases semantics again.
  b.held.rangeResolve(true)
  assert.equal((await cancelling).status, 200)
  assert.notEqual((await rendering).status, 200)
  const reauthorized = await b.control({ op: 'authorize-write', grant: b.grant })
  assert.equal(reauthorized.status, 200, await reauthorized.clone().text())
  const { writeGrant } = await reauthorized.json()
  const replanned = await b.control({ op: 'plan', grant: b.grant, writeGrant, base_revision: version, operations: [{ op: 'project.resize', duration_frames: 131 }] })
  assert.equal(replanned.status, 200, await replanned.clone().text())
})

test('ordinary read with failed cleanup stays semanticBusy and refuses plan/authorize until reaped', async t => {
  const b = await bench({ hold: 'reference', failRangeOnce: true })
  t.after(() => { b.held.doneResolve({ exitCode: 1 }); b.held.rangeResolve(true); return b.host.dispose() })
  const referencing = b.control({ op: 'reference', grant: b.grant, query: { expected_content_version: version, frame: 0 } })
  await b.held.started
  b.held.doneResolve({ exitCode: 0 })
  const failed = await referencing // Cleanup sees the managed range still occupied: PROCESS_CLEANUP_FAILED.
  assert.equal(failed.status, 502, await failed.clone().text())
  assert.match((await failed.json()).message, /could not be confirmed stopped/)
  const spawnsBefore = b.calls.length
  // The unreaped ordinary read must still refuse semantics — cleanup failure is
  // never classified away by the ordinary-read exemption.
  const busyPlan = await b.control({ op: 'plan', grant: b.grant, writeGrant: b.writeGrant, base_revision: version, operations: [{ op: 'project.resize', duration_frames: 130 }] })
  assert.equal(busyPlan.status, 409)
  assert.match((await busyPlan.json()).message, /wait for process reaping/)
  const busyAuthorize = await b.control({ op: 'authorize-write', grant: b.grant })
  assert.equal(busyAuthorize.status, 409)
  assert.equal(b.calls.length, spawnsBefore, 'blocked semantics must not spawn')
})
