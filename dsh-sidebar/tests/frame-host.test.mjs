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
      : { schema: 'framivra.timeline.read/v1', operation, ok: true, diagnostics: [], content_version: version, document: {}, coverage: { complete: true, scope: 'unit fixture', unexpanded: [] }, capabilities: { operations: ['inspect', 'query'], frame: options.unsupported ? {} : { schema: 'framivra.timeline.frame/v1', explicit_render_required: true, ...(options.prepareCapability ? { prepare_gfx: options.prepareCapability } : {}), inline_png: { flag: '--include-png', encoding: 'base64', wire_limit_bytes: FRAME_WIRE_LIMIT } } } }
    const text = JSON.stringify(value) + '\n'
    if (operation === 'frame' && options.handle) return options.handle(spec, text)
    return { stdout: Readable.from([Buffer.from(text)]), done: Promise.resolve({ exitCode: 0 }), terminate() {}, waitForExit: async () => true, collected: { stdout: { readFrom: () => ({ text, lossy: false }) }, stderr: { readFrom: () => ({ text: '', lossy: false }) } } }
  } }
  const config = { vtxExecutable: '/opt/trusted/vtx', clock: () => now }
  const host = createPreviewHost({ fs, sessions, workingDirectory: { get: () => cwd.value }, subprocess }, config)
  const control = data => host.control(new Request('http://unit/api/framivra/control', { method: 'POST', body: JSON.stringify({ sessionId: 'one', ...data }) }))
  const opened = await control({ op: 'open', timeline: 'timeline.json' }); assert.equal(opened.status, 200, await opened.clone().text())
  const { grant } = await opened.json()
  const request = { op: 'render', sessionId: 'one', grant, frame: 0, contentVersion: version, renderConsent: 'cache/preview-only', operationId: randomUUID() }
  const frame = (data = {}, init = {}) => host.frame(new Request('http://unit/api/framivra/frame', { method: 'POST', body: JSON.stringify({ ...request, operationId: randomUUID(), ...data }), ...init }))
  return { host, config, fs, cwd, sessions, calls, frame, control, grant, request, expire: () => { now = 30 * 60 * 1000 } }
}
const prepareCapability = { flag: '--prepare-gfx', trust_flag: '--trust-html', kind: 'current-composite', preview_only: true }
test('Host prepare requires exact capability and consent, HTML trust remains independent and additive', async () => {
  const cases = [
    [undefined, { prepareGfx: true }, 422],
    ...Object.entries({ flag: '--other', trust_flag: '--other', kind: 'html-layer', preview_only: false }).map(([key, value]) => [{ ...prepareCapability, [key]: value }, { prepareGfx: true }, 422]),
    [prepareCapability, { prepareGfx: 'true' }, 400],
    [prepareCapability, { prepareGfx: null }, 400],
    [prepareCapability, { htmlTrust: 'trusted-local-html' }, 403],
    [prepareCapability, { prepareGfx: false, htmlTrust: 'trusted-local-html' }, 403],
    [prepareCapability, { prepareGfx: true, htmlTrust: true }, 403],
    [prepareCapability, { prepareGfx: true, htmlTrust: 'unknown' }, 403],
    [prepareCapability, { htmlBlockAddress: '/video_tracks/0/clips/0' }, 400],
    [prepareCapability, { prepareGfx: true, htmlTrust: 'trusted-local-html', renderConsent: '' }, 403],
  ]
  for (const [capability, payload, status] of cases) {
    const b = await bench({ prepareCapability: capability })
    try { assert.equal((await b.frame(payload)).status, status); assert.equal(b.calls.filter(s => s.argv[4] === 'frame').length, 0) }
    finally { await b.host.dispose() }
  }
  const b = await bench({ prepareCapability })
  try {
    for (const payload of [{ prepareGfx: true }, { prepareGfx: true, htmlTrust: 'trusted-local-html' }, {}, { prepareGfx: false }]) {
      assert.equal((await b.frame(payload)).status, 200)
      const flags = ['--prepare-gfx', '--trust-html'].filter(flag => b.calls.at(-1).argv.includes(flag))
      assert.deepEqual(flags, payload.prepareGfx ? ['--prepare-gfx', ...(payload.htmlTrust ? ['--trust-html'] : [])] : [])
      assert(!b.calls.at(-1).argv.includes('--html-layer')); assert(b.calls.at(-1).argv.includes('--include-png'))
    }
  } finally { await b.host.dispose() }
})
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
test('bounded FIFO queues distinct grants; queued cancel removes only its click and cleanup gates next spawn', async () => {
  let start, finish, releaseRange, terminated = 0
  const started = new Promise(resolve => { start = resolve }), done = new Promise(resolve => { finish = resolve }), range = new Promise(resolve => { releaseRange = resolve })
  let count = 0
  const order = []
  const b = await bench({ handle(spec, text) {
    order.push(Number(spec.argv[spec.argv.indexOf('--frame') + 1]))
    if (++count > 1) return { stdout: Readable.from([Buffer.from(text)]), done: Promise.resolve({ exitCode: 0 }), terminate() {}, waitForExit: async () => true, collected: {} }
    start()
    return { stdout: Readable.from([Buffer.from(text)]), done, terminate() { terminated++ }, waitForExit: () => range, collected: {} }
  } })
  const grants = []
  try {
    for (let i = 0; i < 6; i++) grants.push((await (await b.control({ op: 'open', timeline: 'timeline.json' })).json()).grant)
    const first = b.frame(); await started
    const queued = grants.slice(0, 4).map((grant, index) => b.frame({ grant, frame: index + 1 }))
    // One microtask turn admits the queued requests without sleeping/polling.
    await new Promise(resolve => setImmediate(resolve))
    assert.equal(count, 1)
    assert.equal((await b.frame({ grant: grants[4] })).status, 429)
    assert.equal((await b.control({ op: 'authorize-write', grant: grants[5] })).status, 409)
    assert.equal((await b.frame({ op: 'cancel', grant: grants[1] })).status, 200)
    assert.notEqual((await queued[1]).status, 200)
    assert.equal(count, 1); assert.equal(terminated, 0, 'queued cancel cannot terminate running owner')
    finish({ exitCode: 0 }); await new Promise(resolve => setImmediate(resolve))
    assert(terminated); assert.equal(count, 1, 'done without managed-range exit must not release FIFO')
    releaseRange(true)
    assert.equal((await first).status, 200)
    for (const index of [0, 2, 3]) assert.equal((await queued[index]).status, 200)
    assert.equal(count, 4)
    assert.deepEqual(order, [0, 1, 3, 4], 'non-cancelled clicks preserve exact FIFO order')
  } finally { finish({ exitCode: 1 }); releaseRange(true); await b.host.dispose() }
})
test('queued click is removed on expiry, config/workspace change and unload without spawning', async () => {
  for (const boundary of ['expiry', 'config', 'workspace', 'unload']) {
    let start, finish, releaseRange
    const started = new Promise(resolve => { start = resolve }), done = new Promise(resolve => { finish = resolve }), range = new Promise(resolve => { releaseRange = resolve })
    let count = 0
    const b = await bench({ handle(spec, text) { count++; start(); return { stdout: Readable.from([Buffer.from(text)]), done, terminate() {}, waitForExit: () => range, collected: {} } } })
    try {
      const grant = (await (await b.control({ op: 'open', timeline: 'timeline.json' })).json()).grant
      const first = b.frame(); await started
      const queued = b.frame({ grant }); await new Promise(resolve => setImmediate(resolve))
      let disposal
      if (boundary === 'expiry') { b.expire(); await b.control({ op: 'hello' }) }
      else if (boundary === 'config') { b.config.vtxExecutable = '/opt/changed/vtx'; await b.control({ op: 'query', grant }) }
      else if (boundary === 'workspace') { b.cwd.value = '/elsewhere'; await b.frame({ op: 'cancel', grant }) }
      else disposal = b.host.dispose()
      finish({ exitCode: 0 }); releaseRange(true)
      assert.notEqual((await queued).status, 200); await first; await disposal
      assert.equal(count, 1, boundary)
    } finally { finish({ exitCode: 1 }); releaseRange(true); await b.host.dispose() }
  }
})
test('failed predecessor cleanup refuses queued work once and retains exclusion until explicit scoped reap', async () => {
  let start, finish, reaped = false, count = 0
  const started = new Promise(resolve => { start = resolve }), done = new Promise(resolve => { finish = resolve })
  const b = await bench({ handle(spec, text) { count++; start(); return { stdout: Readable.from([Buffer.from(text)]), done, terminate() {}, waitForExit: async () => reaped, collected: {} } } })
  try {
    const grant = (await (await b.control({ op: 'open', timeline: 'timeline.json' })).json()).grant
    const first = b.frame(); await started
    const queued = b.frame({ grant }); await new Promise(resolve => setImmediate(resolve))
    finish({ exitCode: 0 })
    assert.equal((await first).status, 502)
    assert.equal((await queued).status, 409); assert.equal(count, 1)
    assert.equal((await b.frame({ grant })).status, 409)
    reaped = true
    assert.equal((await b.frame({ op: 'cancel' })).status, 200)
    assert.equal((await b.frame({ grant })).status, 200)
    assert.equal(count, 2)
  } finally { reaped = true; finish({ exitCode: 1 }); await b.host.dispose() }
})
test('queued wait consumes the original 30 second request deadline; no spawn or automatic retry', async t => {
  let start, finish, releaseRange, count = 0
  const started = new Promise(resolve => { start = resolve }), done = new Promise(resolve => { finish = resolve }), range = new Promise(resolve => { releaseRange = resolve })
  const b = await bench({ handle(spec, text) { count++; start(); return { stdout: Readable.from([Buffer.from(text)]), done, terminate() {}, waitForExit: () => range, collected: {} } } })
  try {
    const grant = (await (await b.control({ op: 'open', timeline: 'timeline.json' })).json()).grant
    t.mock.timers.enable({ apis: ['setTimeout'] })
    const first = b.frame(); await started
    const queued = b.frame({ grant }); await new Promise(resolve => setImmediate(resolve))
    t.mock.timers.tick(30_000)
    await new Promise(resolve => setImmediate(resolve))
    assert.notEqual((await queued).status, 200); assert.equal(count, 1)
    finish({ exitCode: 0 }); releaseRange(true); await first
    assert.equal(count, 1)
  } finally { finish({ exitCode: 1 }); releaseRange(true); await b.host.dispose(); t.mock.timers.reset() }
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
