// Narrow regression: an unknown store outcome unlocks ONLY on a settled core
// status (ok:true, pending:false) followed by a fresh inspect — and a later
// pending:true status revokes not-yet-consumed settled evidence.
import test from 'node:test'
import assert from 'node:assert/strict'
import { Readable } from 'node:stream'
import { randomUUID } from 'node:crypto'
import { createPreviewHost } from '../src/host.mjs'
import { MemoryFs } from './helpers.mjs'

const version = `sha256:${'a'.repeat(64)}`
const storeStatus = over => ({ schema: 'framivra.timeline.store/v1', operation: 'status', ok: true, code: 'ok',
  current_revision: version, head: null, evidence_path: '/evidence', operations: [], ...over })

async function bench(options = {}) {
  const fs = new MemoryFs(), sessions = new Map([['one', { id: 'one' }]]), cwd = { value: '/workspace' }, calls = []
  const statusResults = [...options.statusResults ?? []]
  const subprocess = {
    resolveExecutable: async () => '/opt/trusted/vtx',
    spawn(spec) {
      calls.push(spec)
      const store = spec.argv[3] === 'store'
      const operation = store ? spec.argv[4] : spec.argv[3] === 'timeline' ? spec.argv[4] : spec.argv[3]
      let value
      if (store && operation === 'status') {
        value = statusResults.shift() ?? storeStatus({ pending: false })
      } else if (store && operation === 'capabilities') {
        value = { schema: 'framivra.timeline.store/v1', operation, ok: true, diagnostics: [], operations: ['edit', 'commit', 'status', 'recover'], edit: { operations: ['project.resize'] }, request_transport: { stdin: { flag: '--request-stdin', max_bytes: 8388608 } } }
      } else if (store) {
        // plan/commit: killed child when crashing, UUID receipt otherwise.
        if (options.crashCommit && operation !== 'plan') {
          const text = ''
          return { stdout: Readable.from([]), done: Promise.resolve({ exitCode: null, signal: 'SIGKILL' }), terminate() {},
            waitForExit: async () => true, collected: { stdout: { readFrom: () => ({ text, lossy: false }) }, stderr: { readFrom: () => ({ text: '', lossy: false }) } } }
        }
        value = { schema: 'framivra.timeline.store/v1', operation, ok: true, diagnostics: [], op_id: randomUUID() }
      } else if (operation === 'reference') {
        value = { schema: 'framivra.timeline.reference/v1', operation, ok: true, diagnostics: [], content_version: version, document: { raw: '{}', content_version: version }, coverage: { complete: true, scope: 'unit fixture', unexpanded: [] }, image: { state: 'not-generated' }, selection: { tracks: [], blocks: [] } }
      } else {
        value = { schema: 'framivra.timeline.read/v1', operation, ok: true, diagnostics: [], content_version: version, document: {}, coverage: { complete: true, scope: 'unit fixture', unexpanded: [] }, capabilities: { operations: ['inspect', 'query'] } }
      }
      const text = JSON.stringify(value) + '\n'
      return { stdout: Readable.from([Buffer.from(text)]), done: Promise.resolve({ exitCode: 0 }), terminate() {},
        waitForExit: async () => true, collected: { stdout: { readFrom: () => ({ text, lossy: false }) }, stderr: { readFrom: () => ({ text: '', lossy: false }) } } }
    },
  }
  const host = createPreviewHost({ fs, sessions, workingDirectory: { get: () => cwd.value }, subprocess }, { vtxExecutable: '/opt/trusted/vtx' })
  const control = data => host.control(new Request('http://unit/api/framivra/control', { method: 'POST', body: JSON.stringify({ sessionId: 'one', ...data }) }))
  const opened = await control({ op: 'open', timeline: 'timeline.json' }); assert.equal(opened.status, 200, await opened.clone().text())
  const { grant } = await opened.json()
  const authorized = await control({ op: 'authorize-write', grant })
  assert.equal(authorized.status, 200, await authorized.clone().text())
  const { writeGrant } = await authorized.json()
  const plan = async () => {
    const response = await control({ op: 'plan', grant, writeGrant, base_revision: version, operations: [{ op: 'project.resize', duration_frames: 130 }] })
    return { status: response.status, body: await response.json() }
  }
  const crashToUnknown = async () => {
    const planned = await plan(); assert.equal(planned.status, 200)
    const crashed = await control({ op: 'commit', grant, writeGrant, planToken: planned.body.planToken })
    assert.equal(crashed.status, 200, await crashed.clone().text())
    const body = await crashed.json()
    assert.equal(body.ok, false)
    assert.equal(body.code, 'outcome_unknown')
    return body
  }
  return { host, calls, grant, writeGrant, control, plan, crashToUnknown, statusLeft: () => statusResults.length }
}

test('status pending:true is observational: the unknown grant stays locked even after inspect', async t => {
  const b = await bench({ crashCommit: true, statusResults: [storeStatus({ pending: true })] })
  t.after(() => b.host.dispose())
  const unknown = await b.crashToUnknown()
  assert.ok(unknown.op_id)
  // Locked before any status.
  const lockedPlan = await b.plan()
  assert.equal(lockedPlan.status, 409)
  assert.match(lockedPlan.body.message, /OUTCOME_UNKNOWN|status\/recover/)
  const observed = await b.control({ op: 'status', grant: b.grant, writeGrant: b.writeGrant })
  assert.equal(observed.status, 200, await observed.clone().text())
  assert.equal((await observed.json()).pending, true)
  const inspected = await b.control({ op: 'inspect', grant: b.grant })
  assert.equal(inspected.status, 200, await inspected.clone().text())
  const still = await b.plan()
  assert.equal(still.status, 409, 'pending:true must not unlock the grant')
  assert.match(still.body.message, /OUTCOME_UNKNOWN|status\/recover/)
  assert.equal(b.statusLeft(), 0)
})

test('settled status then pending:true revokes the unconsumed evidence; re-settled status plus inspect unlocks', async t => {
  const b = await bench({ crashCommit: true, statusResults: [
    storeStatus({ pending: false }),
    storeStatus({ pending: true }),
    storeStatus({ pending: false }),
  ] })
  t.after(() => b.host.dispose())
  await b.crashToUnknown()
  const settled = await b.control({ op: 'status', grant: b.grant, writeGrant: b.writeGrant })
  assert.equal((await settled.json()).pending, false)
  // Evidence not yet consumed by an inspect: a pending:true status revokes it.
  const revoked = await b.control({ op: 'status', grant: b.grant, writeGrant: b.writeGrant })
  assert.equal((await revoked.json()).pending, true)
  const inspectAfterRevoke = await b.control({ op: 'inspect', grant: b.grant })
  assert.equal(inspectAfterRevoke.status, 200)
  const stillLocked = await b.plan()
  assert.equal(stillLocked.status, 409, 'revoked evidence must not unlock via the later inspect')
  // A fresh settled status plus a fresh inspect finally unlocks.
  const ressettled = await b.control({ op: 'status', grant: b.grant, writeGrant: b.writeGrant })
  assert.equal((await ressettled.json()).pending, false)
  const finalInspect = await b.control({ op: 'inspect', grant: b.grant })
  assert.equal(finalInspect.status, 200)
  const unlocked = await b.plan()
  assert.equal(unlocked.status, 200, JSON.stringify(unlocked.body))
  assert.equal(b.statusLeft(), 0)
})
