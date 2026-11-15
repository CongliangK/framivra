// Adversarial Host boundary unit fixtures, NOT real provider/carrier evidence.
import test from 'node:test'
import assert from 'node:assert/strict'
import { Readable } from 'node:stream'
import { randomUUID } from 'node:crypto'
import { createPreviewHost } from '../src/host.mjs'
import { MemoryFs } from './helpers.mjs'
import { FRAME_WIRE_LIMIT, FRAME_REQUEST_LIMIT } from '../src/bounded.mjs'
const version = `sha256:${'a'.repeat(64)}`
async function bench(options = {}) {
  const fs = new MemoryFs(), sessions = new Map([['one', { id: 'one' }]]), cwd = { value: '/workspace' }, calls = []
  let now = 0
  const subprocess = { resolveExecutable: async () => '/opt/trusted/vtx', spawn(spec) {
    calls.push(spec)
    const operation = spec.argv[3] === 'timeline' ? spec.argv[4] : spec.argv[3]
    const value = operation === 'frame' ? { schema: 'framivra.timeline.frame/v1', operation, ok: true, diagnostics: [], content_version: version, image: { path: '/never/read.png' } }
      : { schema: 'framivra.timeline.read/v1', operation, ok: true, diagnostics: [], content_version: version, document: {}, coverage: { complete: true, scope: 'unit fixture', unexpanded: [] }, capabilities: { operations: ['inspect', 'query'], frame: options.unsupported ? {} : { schema: 'framivra.timeline.frame/v1', explicit_render_required: true, inline_png: { flag: '--include-png', encoding: 'base64', wire_limit_bytes: FRAME_WIRE_LIMIT } } } }
    const text = JSON.stringify(value) + '\n'
    if (operation === 'frame' && options.handle) return options.handle(spec, text)
    return { stdout: Readable.from([Buffer.from(text)]), done: Promise.resolve({ exitCode: 0 }), terminate() {}, waitForExit: async () => true, collected: { stdout: { readFrom: () => ({ text, lossy: false }) }, stderr: { readFrom: () => ({ text: '', lossy: false }) } } }
  } }
  const host = createPreviewHost({ fs, sessions, workingDirectory: { get: () => cwd.value }, subprocess }, { vtxExecutable: '/opt/trusted/vtx', clock: () => now })
  const control = data => host.control(new Request('http://unit/api/framivra/control', { method: 'POST', body: JSON.stringify({ sessionId: 'one', ...data }) }))
  const opened = await control({ op: 'open', timeline: 'timeline.json' }); assert.equal(opened.status, 200, await opened.clone().text())
  const { grant } = await opened.json()
  const request = { op: 'render', sessionId: 'one', grant, frame: 0, contentVersion: version, renderConsent: 'cache/preview-only', operationId: randomUUID() }
  const frame = (data = {}, init = {}) => host.frame(new Request('http://unit/api/framivra/frame', { method: 'POST', body: JSON.stringify({ ...request, operationId: randomUUID(), ...data }), ...init }))
  return { host, fs, cwd, sessions, calls, frame, control, grant, request, expire: () => { now = 30 * 60 * 1000 } }
}
test('frame Host refuses missing consent/capability, stale versions, replacement executable, path/session/cwd/TTL changes without a render', async () => {
  const cases = [
    ['consent', b => ({ renderConsent: '' }), 403],
    ['capability', () => ({}), 422],
    ['version', () => ({ contentVersion: `sha256:${'b'.repeat(64)}` }), 409],
    ['executable', b => { b.fs.files.get('/opt/trusted/vtx').version = 'replaced'; return {} }, 409],
    ['symlink', b => { b.fs.aliases.set('/workspace/timeline.json', '/outside/timeline.json'); return {} }, 403],
    ['cwd', b => { b.cwd.value = '/elsewhere'; return {} }, 403],
    ['session', b => { b.sessions.set('one', { id: 'one' }); return {} }, 403],
    ['ttl', b => { b.expire(); return {} }, 403],
    ['path field', () => ({ path: '/etc/passwd' }), 400],
  ]
  for (const [name, mutate, expected] of cases) {
    const b = await bench({ unsupported: name === 'capability' })
    try { const response = await b.frame(mutate(b)); assert.equal(response.status, expected, `${name}: ${await response.text()}`); assert.equal(b.calls.filter(s => s.argv[4] === 'frame').length, 0); assert.equal(b.fs.reads.length, 0) }
    finally { await b.host.dispose() }
  }
})
test('frame Host bounds request bytes, never reads emitted image.path, rejects replay, and separates render from semantic write consent', async () => {
  const b = await bench()
  try {
    const oversized = await b.host.frame(new Request('http://unit/api/framivra/frame', { method: 'POST', body: ' '.repeat(FRAME_REQUEST_LIMIT + 1) })); assert.equal(oversized.status, 413)
    const response = await b.frame({ operationId: b.request.operationId }); assert.equal(response.status, 200); assert.equal(response.headers.get('cache-control'), 'no-store')
    assert.equal((await b.frame({ operationId: b.request.operationId })).status, 409)
    const denied = await b.control({ op: 'plan', grant: b.grant, request: {} }); assert.equal(denied.status, 403)
    assert.equal(b.fs.reads.length, 0)
  } finally { await b.host.dispose() }
})
test('frame + semantic mutual exclusion lasts through HTTP cancel until actual done AND managed-range exit', async () => {
  let startedResolve, doneResolve, rangeResolve, terminated = 0
  const started = new Promise(r => { startedResolve = r }), done = new Promise(r => { doneResolve = r }), range = new Promise(r => { rangeResolve = r })
  const b = await bench({ handle: (spec, text) => {
    startedResolve()
    return {
      stdout: Readable.from([Buffer.from(text)]), done,
      terminate() { terminated++ }, waitForExit: () => range,
      collected: { stderr: { readFrom: () => ({ text: '', lossy: false }) } },
    }
  } })
  try {
    const rendering = b.frame(); await started
    assert.equal((await b.frame()).status, 409)
    assert.equal((await b.control({ op: 'authorize-write', grant: b.grant })).status, 409)
    let settled = false
    const cancelling = b.frame({ op: 'cancel' }).then(r => { settled = true; return r })
    await new Promise(r => setImmediate(r)); assert(terminated); assert.equal(settled, false)
    doneResolve({ exitCode: 0 }); await new Promise(r => setImmediate(r)); assert.equal(settled, false)
    assert.equal((await b.frame()).status, 409)
    rangeResolve(true)
    assert.equal((await cancelling).status, 200); assert.notEqual((await rendering).status, 200)
    assert.equal((await b.frame()).status, 200)
  } finally { doneResolve({ exitCode: 1 }); rangeResolve(true); await b.host.dispose() }
})
