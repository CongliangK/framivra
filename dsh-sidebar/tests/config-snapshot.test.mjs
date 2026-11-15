// One-snapshot configuration contract: an admitted child never mixes an old
// executable with a newer PATH, a config change admits zero new spawns, an
// in-flight mutation keeps its request under outcome_unknown, open never mints
// an already-stale grant, and a missing vtx is an explicit EXECUTABLE_NOT_FOUND.
import test from 'node:test'
import assert from 'node:assert/strict'
import { Readable } from 'node:stream'
import { randomUUID } from 'node:crypto'
import { createPreviewHost } from '../src/host.mjs'
import { TimelineReader, PreviewError } from '../src/protocol.mjs'
import { MemoryFs } from './helpers.mjs'

const version = `sha256:${'a'.repeat(64)}`
const A = { exe: '/opt/a/vtx', path: '/opt/a' }
const B = { exe: '/opt/b/vtx', path: '/opt/b' }

function notFoundError() {
  return Object.assign(new Error('subprocess-local: command "vtx" was not found on PATH'), { name: 'SubprocessExecutableNotFoundError' })
}

async function bench(options = {}) {
  const fs = new MemoryFs()
  fs.files.set(A.exe, { type: 'file', version: 'vtx-a' })
  fs.files.set(B.exe, { type: 'file', version: 'vtx-b' })
  const sessions = new Map([['one', { id: 'one' }]])
  const current = { exe: options.exe ?? A.exe, path: options.path ?? A.path }
  const source = { vtxExecutable: { get: () => current.exe }, trustedPath: { get: () => current.path } }
  const spawns = []
  const held = {}
  held.done = new Promise(resolve => { held.doneResolve = resolve })
  held.range = new Promise(resolve => { held.rangeResolve = resolve })
  let heldUsed = false
  const subprocess = {
    resolveExecutable: async (command, env) => {
      const known = { [A.exe]: A.path, [B.exe]: B.path }
      if (known[command] === env.PATH) return command
      throw options.resolveError ? options.resolveError() : notFoundError()
    },
    spawn(spec) {
      spawns.push(spec)
      const store = spec.argv[3] === 'store'
      const operation = store ? (spec.argv[4] === 'plan' ? 'plan' : spec.argv[4]) : spec.argv[3] === 'timeline' ? spec.argv[4] : spec.argv[3]
      const value = store
        ? operation === 'capabilities'
          ? { schema: 'framivra.timeline.store/v1', operation, ok: true, diagnostics: [], operations: ['edit', 'commit', 'status', 'recover'], edit: { operations: ['project.resize'] }, request_transport: { stdin: { flag: '--request-stdin', max_bytes: 8388608 } } }
          : { schema: 'framivra.timeline.store/v1', operation, ok: true, diagnostics: [], op_id: randomUUID() }
        : operation === 'frame'
          ? { schema: 'framivra.timeline.frame/v1', operation, ok: true, diagnostics: [], content_version: version, image: { path: '/never/read.png' } }
          : operation === 'reference'
            ? { schema: 'framivra.timeline.reference/v1', operation, ok: true, diagnostics: [], content_version: version, document: { raw: '{}', content_version: version }, coverage: { complete: true, scope: 'unit fixture', unexpanded: [] }, image: { state: 'not-generated' }, selection: { tracks: [], blocks: [] } }
            : { schema: 'framivra.timeline.read/v1', operation, ok: true, diagnostics: [], content_version: version, document: {}, coverage: { complete: true, scope: 'unit fixture', unexpanded: [] }, capabilities: { operations: ['inspect', 'query'], frame: { schema: 'framivra.timeline.frame/v1', explicit_render_required: true } } }
      const text = JSON.stringify(value) + '\n'
      const isCommit = store && spec.argv[4] === '--request-stdin'
      if (options.hold === 'commit' && isCommit && !heldUsed) {
        heldUsed = true
        return {
          stdout: Readable.from([Buffer.from(text)]), done: held.done,
          terminate() {}, waitForExit: () => held.range,
          collected: { stdout: { readFrom: () => ({ text, lossy: false }) }, stderr: { readFrom: () => ({ text: '', lossy: false }) } },
        }
      }
      return { stdout: Readable.from([Buffer.from(text)]), done: Promise.resolve({ exitCode: 0 }), terminate() {}, waitForExit: async () => true, collected: { stdout: { readFrom: () => ({ text, lossy: false }) }, stderr: { readFrom: () => ({ text: '', lossy: false }) } } }
    },
  }
  const host = createPreviewHost({ fs, sessions, workingDirectory: { get: () => '/workspace' }, subprocess }, source)
  const control = data => host.control(new Request('http://unit/api/framivra/control', { method: 'POST', body: JSON.stringify({ sessionId: 'one', ...data }) }))
  return { host, control, current, spawns, held, fs }
}

const errorOf = async response => ({ status: response.status, ...(await response.json()) })

test('snapshot A resolved, then config flips to B: next read admits zero spawns with CONFIG_CHANGED', async () => {
  const { control, current, spawns } = await bench()
  const opened = await control({ op: 'open', timeline: 'timeline.json' })
  assert.equal(opened.status, 200, await opened.clone().text())
  const { grant } = await opened.json()
  current.exe = B.exe; current.path = B.path
  const before = spawns.length
  const denied = await control({ op: 'inspect', grant })
  const body = await denied.json()
  assert.equal(denied.status, 409)
  assert.equal(body.error, 'CONFIG_CHANGED')
  assert.equal(spawns.length, before) // zero new spawns after the flip
})

test('reader-level snapshot compare rejects before spawning even when the caller bypasses grant gates', async () => {
  const subprocess = {
    resolveExecutable: async (command, env) => {
      if (env.PATH === A.path && command === A.exe) return command
      throw notFoundError()
    },
    spawn(spec) {
      throw assert.fail(`spawn must not happen: ${spec.argv.join(' ')}`)
    },
  }
  const current = { exe: A.exe, path: A.path }
  const reader = new TimelineReader(subprocess, { vtxExecutable: { get: () => current.exe }, trustedPath: { get: () => current.path } })
  const snapshot = reader.snapshotConfig()
  current.exe = B.exe; current.path = B.path
  await assert.rejects(reader.read(A.exe, { targetKey: '/workspace/timeline.json' }, 'inspect', undefined, new AbortController().signal, snapshot),
    error => error instanceof PreviewError && error.code === 'CONFIG_CHANGED')
})

test('admitted commit child keeps env from snapshot A; a config flip aborts to outcome_unknown with the request retained', async () => {
  const { host, control, held, current, spawns } = await bench({ hold: 'commit' })
  const opened = await control({ op: 'open', timeline: 'timeline.json' })
  const { grant } = await opened.json()
  const authorized = await control({ op: 'authorize-write', grant })
  const { writeGrant } = await authorized.json()
  const planned = await control({ op: 'plan', grant, writeGrant, base_revision: version, operations: [{ op: 'project.resize', duration_frames: 130 }] })
  const { planToken } = await planned.json()
  const committed = control({ op: 'commit', grant, writeGrant, planToken })
  const isCommitSpawn = spec => spec.argv[3] === 'store' && spec.argv[4] === '--request-stdin'
  for (let i = 0; i < 100 && !spawns.some(isCommitSpawn); i++) await new Promise(resolve => setImmediate(resolve))
  const commitSpawn = spawns.find(isCommitSpawn)
  assert.ok(commitSpawn, 'commit child admitted')
  assert.equal(commitSpawn.env.PATH, A.path) // env strictly from the admitted snapshot
  const request = JSON.parse(commitSpawn.stdio.stdin.data)
  current.exe = B.exe; current.path = B.path // flip while the child is held
  await new Promise(resolve => setTimeout(resolve, 120)) // 50ms monitor fires
  const response = await committed
  const body = await response.json()
  assert.equal(response.status, 200)
  assert.equal(body.ok, false)
  assert.equal(body.code, 'outcome_unknown')
  assert.equal(body.submitted_request.op_id, request.op_id) // original request retained, never replaced
  held.doneResolve({ exitCode: 1 }); held.rangeResolve(true)
  await host.dispose()
})

test('open: a config flip during the executable stat window never mints a grant', async () => {
  const { control, current, fs } = await bench()
  const realStat = fs.stat.bind(fs)
  let releaseStat
  const statGate = new Promise(resolve => { releaseStat = resolve })
  fs.stat = async (target, signal) => {
    if (target.targetKey === A.exe) { await statGate }
    return realStat(target, signal)
  }
  const opening = control({ op: 'open', timeline: 'timeline.json' })
  await new Promise(resolve => setImmediate(resolve)); await new Promise(resolve => setImmediate(resolve))
  current.exe = B.exe; current.path = B.path
  releaseStat()
  const response = await opening
  const body = await response.json()
  assert.equal(response.status, 409)
  assert.equal(body.error, 'CONFIG_CHANGED')
  const reopened = await control({ op: 'inspect', grant: 'whatever' })
  assert.equal((await reopened.json()).error, 'BAD_GRANT') // nothing was minted
})

test('missing vtx on the trusted PATH is an explicit 424 EXECUTABLE_NOT_FOUND; other resolver errors keep their shape', async () => {
  const missing = await bench()
  missing.current.exe = '/opt/absent/vtx'
  const response = await missing.control({ op: 'open', timeline: 'timeline.json' })
  const body = await response.json()
  assert.equal(response.status, 424)
  assert.equal(body.error, 'EXECUTABLE_NOT_FOUND')
  assert.match(body.message, /绝对受信/)
  const generic = await bench({ resolveError: () => new Error('resolver exploded') })
  generic.current.exe = '/opt/absent/vtx'
  const other = await generic.control({ op: 'open', timeline: 'timeline.json' })
  const otherBody = await other.json()
  assert.equal(other.status, 500)
  assert.equal(otherBody.error, 'PREVIEW_FAILED') // non-matching errors unchanged
})

test('cleanup operations keep working on a config-stale grant', async () => {
  const { control, current } = await bench()
  const opened = await control({ op: 'open', timeline: 'timeline.json' })
  const { grant } = await opened.json()
  current.exe = B.exe; current.path = B.path
  const closed = await control({ op: 'close', grant })
  assert.equal(closed.status, 200, await closed.clone().text())
  assert.equal((await closed.json()).ok, true)
})
