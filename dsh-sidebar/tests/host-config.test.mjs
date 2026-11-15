// Adversarial Host boundary unit fixtures, NOT real provider/carrier evidence.
// Configuration lives in a mutable Volatile-like source exactly like the Host
// settings service serves it; every case flips it mid-flight.
import test from 'node:test'
import assert from 'node:assert/strict'
import { Readable } from 'node:stream'
import { randomUUID } from 'node:crypto'
import { createPreviewHost } from '../src/host.mjs'
import { MemoryFs } from './helpers.mjs'
import { FRAME_WIRE_LIMIT } from '../src/bounded.mjs'

const version = `sha256:${'a'.repeat(64)}`

async function bench(options = {}) {
  const fs = new MemoryFs()
  fs.files.set('/other/vtx', { type: 'file', version: 'vtx2' })
  const sessions = new Map([['one', { id: 'one' }]]), cwd = { value: '/workspace' }, calls = []
  const resolved = []
  // The live configuration cells the Host settings service would serve.
  const live = { vtxExecutable: '/opt/trusted/vtx', trustedPath: '/opt/trusted' }
  const config = { vtxExecutable: { get: () => live.vtxExecutable }, trustedPath: { get: () => live.trustedPath } }
  const subprocess = {
    async resolveExecutable(command, env) { resolved.push({ command, env }); return command },
    spawn(spec) {
      calls.push(spec)
      const operation = spec.argv[3] === 'timeline' ? spec.argv[4] : spec.argv[3]
      const value = operation === 'frame' ? { schema: 'framivra.timeline.frame/v1', operation, ok: true, diagnostics: [], content_version: version, image: { path: '/never/read.png' } }
        : { schema: 'framivra.timeline.read/v1', operation, ok: true, diagnostics: [], content_version: version, document: {}, coverage: { complete: true, scope: 'unit fixture', unexpanded: [] }, capabilities: { operations: ['inspect', 'query'], frame: options.unsupported ? {} : { schema: 'framivra.timeline.frame/v1', explicit_render_required: true, inline_png: { flag: '--include-png', encoding: 'base64', wire_limit_bytes: FRAME_WIRE_LIMIT } } } }
      const text = JSON.stringify(value) + '\n'
      if (operation === 'frame' && options.handle) return options.handle(spec, text)
      return { stdout: Readable.from([Buffer.from(text)]), done: Promise.resolve({ exitCode: 0 }), terminate() {}, waitForExit: async () => true, collected: { stdout: { readFrom: () => ({ text, lossy: false }) }, stderr: { readFrom: () => ({ text: '', lossy: false }) } } }
    },
  }
  const host = createPreviewHost({ fs, sessions, workingDirectory: { get: () => cwd.value }, subprocess }, config)
  const control = data => host.control(new Request('http://unit/api/framivra/control', { method: 'POST', body: JSON.stringify({ sessionId: 'one', ...data }) }))
  const opened = await control({ op: 'open', timeline: 'timeline.json' }); assert.equal(opened.status, 200, await opened.clone().text())
  const { grant } = await opened.json()
  const request = { op: 'render', sessionId: 'one', grant, frame: 0, contentVersion: version, renderConsent: 'cache/preview-only', operationId: randomUUID() }
  const frame = (data = {}) => host.frame(new Request('http://unit/api/framivra/frame', { method: 'POST', body: JSON.stringify({ ...request, operationId: randomUUID(), ...data }) }))
  return { host, fs, cwd, sessions, calls, resolved, live, frame, control, grant, request }
}

test('configuration change fails old grants closed: reads 409 CONFIG_CHANGED, writes die with them, cleanup still works', async () => {
  const b = await bench()
  try {
    b.live.vtxExecutable = '/other/vtx'
    const query = await b.control({ op: 'query', grant: b.grant })
    assert.equal(query.status, 409); assert.equal((await query.json()).error, 'CONFIG_CHANGED')
    const plan = await b.control({ op: 'plan', grant: b.grant, request: {} })
    assert.equal(plan.status, 409); assert.equal((await plan.json()).error, 'CONFIG_CHANGED')
    const media = await b.host.media(new Request(`http://unit/api/framivra/media?sessionId=one&grant=${b.grant}&mediaId=x`))
    assert.equal(media.status, 409); assert.equal((await media.json()).error, 'CONFIG_CHANGED')
    const close = await b.control({ op: 'close', grant: b.grant })
    assert.equal(close.status, 200)
    assert.equal(b.fs.reads.length, 0)
    assert.equal(b.calls.filter(spec => ['query', 'plan'].includes(spec.argv[3] === 'timeline' ? spec.argv[4] : spec.argv[3])).length, 0)
  } finally { await b.host.dispose() }
})

test('a new open after the change binds and spawns the new trusted executable', async () => {
  const b = await bench()
  try {
    b.live.vtxExecutable = '/other/vtx'
    b.live.trustedPath = '/other'
    const opened = await b.control({ op: 'open', timeline: 'timeline.json' })
    assert.equal(opened.status, 200, await opened.text())
    assert.equal(b.resolved.at(-1).command, '/other/vtx')
    assert.equal(b.resolved.at(-1).env.PATH, '/other')
    const spawn = b.calls.at(-1)
    assert.equal(spawn.env.PATH, '/other')
  } finally { await b.host.dispose() }
})

test('in-flight frame aborts on configuration change without a read, and cancel still works after a change', async () => {
  let startedResolve, doneResolve, rangeResolve, terminated = 0
  const started = new Promise(r => { startedResolve = r }), done = new Promise(r => { doneResolve = r }), range = new Promise(r => { rangeResolve = r })
  const b = await bench({ handle: (_spec, text) => {
    startedResolve()
    return {
      stdout: Readable.from([Buffer.from(text)]), done,
      terminate() { terminated++ }, waitForExit: () => range,
      collected: { stderr: { readFrom: () => ({ text: '', lossy: false }) } },
    }
  } })
  try {
    const rendering = b.frame()
    await started
    b.live.trustedPath = '/usr/local/bin'
    for (let i = 0; i < 40 && terminated === 0; i++) await new Promise(r => setTimeout(r, 10))
    assert.equal(terminated >= 1, true)
    assert.equal(b.fs.reads.length, 0)
    doneResolve({ exitCode: 0 }); rangeResolve(true)
    const outcome = await rendering
    assert.notEqual(outcome.status, 200)
    assert.equal((await outcome.json()).error, 'CANCELLED')
    // Cancel is cleanup: still accepted after the configuration changed.
    const cancelled = await b.frame({ op: 'cancel' })
    assert.equal(cancelled.status, 200)
  } finally { await b.host.dispose() }
})
