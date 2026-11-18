// Adversarial Host boundary unit fixtures, NOT real provider/carrier evidence.
import test from 'node:test'
import assert from 'node:assert/strict'
import { Readable } from 'node:stream'
import { createHash, randomUUID } from 'node:crypto'
import { createPreviewHost } from '../src/host.mjs'
import { MemoryFs } from './helpers.mjs'
import { FRAME_WIRE_LIMIT, FRAME_REQUEST_LIMIT } from '../src/bounded.mjs'
const version = `sha256:${'a'.repeat(64)}`
const nextVersion = `sha256:${'b'.repeat(64)}`
const windowCapability = { schema: 'framivra.timeline.preview-window/v1', inline_media: true, prepare_inputs: true }
const windowBytes = Buffer.from('000000186674797069736f6d0000020069736f6d69736f32', 'hex') // synthetic fixture, not playable media
const sha256 = createHash('sha256').update(windowBytes).digest('hex')
async function bench(options = {}) {
  const fs = new MemoryFs(), sessions = new Map([['one', { id: 'one' }]]), cwd = { value: '/workspace' }, calls = []
  let now = 0
  const subprocess = { resolveExecutable: async () => '/opt/trusted/vtx', spawn(spec) {
    calls.push(spec)
    const operation = spec.argv[3] === 'timeline' ? spec.argv[4] : spec.argv[3]
    let value = operation === 'frame' ? { schema: 'framivra.timeline.frame/v1', operation, ok: true, diagnostics: [], content_version: version, image: { path: '/never/read.png' } }
      : { schema: 'framivra.timeline.read/v1', operation, ok: true, diagnostics: [], content_version: options.saved?.() ?? version, document: { project: { duration_frames: 36, fps: 23.976 } }, coverage: { complete: true, scope: 'unit fixture', unexpanded: [] }, capabilities: { operations: ['inspect', 'query'], preview_window: options.windowCapability, frame: options.unsupported ? {} : { schema: 'framivra.timeline.frame/v1', explicit_render_required: true, ...(options.prepareCapability ? { prepare_gfx: options.prepareCapability } : {}), inline_png: { flag: '--include-png', encoding: 'base64', wire_limit_bytes: FRAME_WIRE_LIMIT } } } }
    if (operation === 'preview-window') {
      const start = Number(spec.argv[6])
      value = { schema: windowCapability.schema, ok: true, content_version: version, start, end: Math.min(36, start + 24), fps: { num: 24000, den: 1001 }, width: 1920, height: 1080,
        mime: 'video/mp4', audio_sample_rate: 48000, audio_channels: 2, byte_length: windowBytes.length, sha256, base64: windowBytes.toString('base64'), path: '/never/read.mp4' }
      value = options.windowResult ? options.windowResult(value) : value
    }
    const text = JSON.stringify(value) + '\n'
    if (operation === 'frame' && options.handle) return options.handle(spec, text)
    if (operation === 'preview-window' && options.windowHandle) return options.windowHandle(spec, text)
    if (operation === 'inspect' && options.inspectHandle) {
      const handle = options.inspectHandle(spec, text)
      if (handle) return handle
    }
    return { stdout: Readable.from([Buffer.from(text)]), done: Promise.resolve({ exitCode: value.ok ? 0 : 1 }), terminate() {}, waitForExit: async () => true, collected: { stdout: { readFrom: () => ({ text, lossy: false }) }, stderr: { readFrom: () => ({ text: '', lossy: false }) } } }
  } }
  const config = { vtxExecutable: '/opt/trusted/vtx', clock: () => now }
  const host = createPreviewHost({ fs, sessions, workingDirectory: { get: () => cwd.value }, subprocess }, config)
  const control = data => host.control(new Request('http://unit/api/framivra/control', { method: 'POST', body: JSON.stringify({ sessionId: 'one', ...data }) }))
  const opened = await control({ op: 'open', timeline: 'timeline.json' }); assert.equal(opened.status, 200, await opened.clone().text())
  const opening = await opened.json()
  const { grant } = opening
  const request = { op: 'render', sessionId: 'one', grant, frame: 0, contentVersion: version, renderConsent: 'cache/preview-only', operationId: randomUUID() }
  const frame = (data = {}, init = {}) => host.frame(new Request('http://unit/api/framivra/frame', { method: 'POST', body: JSON.stringify({ ...request, operationId: randomUUID(), ...data }), ...init }))
  const window = (data = {}) => host.window(new Request('http://unit/api/framivra/window', { method: 'POST', body: JSON.stringify(data.op === 'cancel'
    ? { sessionId: 'one', grant, ...data }
    : { ...request, prepareInputs: true, operationId: randomUUID(), ...data }) }))
  return { host, config, fs, cwd, sessions, calls, frame, window, control, grant, opening, request, expire: () => { now = 30 * 60 * 1000 } }
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

const linuxWindow = { skip: process.platform !== 'linux' }
test('window uses separate owner POST, actual capability and two consents; only verified bytes leave Host', linuxWindow, async t => {
  const absent = await bench({ windowCapability: { ...windowCapability, prepare_inputs: false } })
  t.after(() => absent.host.dispose())
  assert(absent.opening.host.ops.includes('preview-window'))
  assert.equal(absent.opening.capabilities.capabilities.preview_window, undefined)
  assert.equal((await absent.window()).status, 422)
  assert.equal(absent.calls.length, 2)

  const b = await bench({ windowCapability }); t.after(() => b.host.dispose())
  assert.deepEqual(b.opening.capabilities.capabilities.preview_window, windowCapability)
  for (const [payload, status] of [[{ prepareInputs: undefined }, 403], [{ prepareInputs: 'true' }, 403], [{ renderConsent: '' }, 403],
    [{ contentVersion: nextVersion }, 409], [{ frame: 1.5 }, 409], [{ path: '/other.mp4' }, 400], [{ prepareGfx: true }, 400], [{ htmlTrust: 'trusted-local-html' }, 400]]) {
    assert.equal((await b.window(payload)).status, status, JSON.stringify(payload))
  }
  assert.equal((await b.host.window(new Request('http://unit/api/framivra/window'))).status, 405)
  assert.equal((await b.control({ op: 'preview-window', grant: b.grant })).status, 400)
  assert.deepEqual(Object.keys(b.host.agent), ['read'])
  await assert.rejects(b.host.agent.read(undefined, { view: 'preview-window' }), { code: 'BAD_OPERATION' })
  assert.equal(b.calls.length, 2)

  const operationId = randomUUID()
  const response = await b.window({ frame: 33, operationId })
  assert.equal(response.status, 200, await response.clone().text())
  assert.equal(response.headers.get('content-type'), 'video/mp4')
  assert.equal(response.headers.get('cache-control'), 'no-store')
  assert.deepEqual(['version', 'start', 'end', 'sha256'].map(key => response.headers.get(`x-framivra-${key}`)), [version, '33', '36', sha256])
  assert.deepEqual(Buffer.from(await response.arrayBuffer()), windowBytes)
  assert.deepEqual(b.calls.slice(2).map(call => call.argv), [
    ['/opt/trusted/vtx', '-t', '/workspace/timeline.json', 'timeline', 'preview-window', '--frame', '33', `--expected-content-version=${version}`, '--render', '--include-media', '--prepare-inputs'],
    ['/opt/trusted/vtx', '-t', '/workspace/timeline.json', 'inspect'],
  ])
  assert.equal((await b.window({ operationId })).status, 409)
  assert.equal(b.fs.reads.length, 0)
  assert.equal((await b.frame({ operationId })).status, 200, 'fixed frame remains a separate operation')
})

test('window refuses malformed, oversized, or stale bytes and reports bounded kernel rejection', linuxWindow, async t => {
  let saved = version, patch
  const b = await bench({ windowCapability, saved: () => saved, windowResult: value => patch(value) })
  t.after(() => b.host.dispose())
  for (const malformed of [{ content_version: nextVersion }, { base64: undefined }, { base64: ' '.repeat(windowBytes.length * 4 / 3) },
    { sha256: 'f'.repeat(64) }, { fps: { num: 24, den: 1 } }, { end: 1 }, { byte_length: 5 * 1024 * 1024 + 1 }, { path: 'X'.repeat(FRAME_WIRE_LIMIT) }]) {
    patch = value => ({ ...value, ...malformed })
    const response = await b.window()
    assert.equal(response.status, 502)
    assert.equal(response.headers.get('x-framivra-version'), null)
  }
  const sentinel = '/workspace/private/ffmpeg-stderr-secret'
  patch = () => ({ schema: windowCapability.schema, ok: false, diagnostics: Array.from({ length: 10 }, (_, i) => ({ code: ['plugins.unsupported', 'gfx.unsupported', 'untrusted.secret'][i % 3], message: sentinel, address: sentinel })) })
  const refused = await b.window(); assert.equal(refused.status, 422)
  const raw = await refused.text()
  assert(!raw.includes(sentinel)); assert(!raw.includes('untrusted.secret'))
  const { diagnostics } = JSON.parse(raw)
  assert.equal(diagnostics.length, 8)
  assert.deepEqual(diagnostics.slice(0, 3).map(item => item.code), ['plugins.unsupported', 'gfx.unsupported', 'window.rejected'])
  assert.match(diagnostics[0].message, /plugins/); assert.match(diagnostics[1].message, /graphics/)
  patch = value => { saved = nextVersion; return value }
  const stale = await b.window(); assert.equal(stale.status, 409); assert.equal((await stale.json()).error, 'STALE_WINDOW')
  assert.equal((await b.control({ op: 'inspect', grant: b.grant })).status, 200, 'rejection does not wedge ordinary editing reads')
  assert.equal(b.fs.reads.length, 0)
})

test('window cancel is tombstoned, nonce/grant-scoped, and awaits ownership even during final inspect', linuxWindow, async t => {
  let start, finish, releaseRange, terminated = 0, inspections = 0
  const started = new Promise(resolve => { start = resolve }), done = new Promise(resolve => { finish = resolve }), range = new Promise(resolve => { releaseRange = resolve })
  const b = await bench({ windowCapability, inspectHandle() {
    if (++inspections !== 3) return
    start()
    return { done, terminate() { terminated++ }, waitForExit: () => range, collected: {} }
  } })
  t.after(async () => { finish({ exitCode: 1 }); releaseRange(true); await b.host.dispose() })
  const second = (await (await b.control({ op: 'open', timeline: 'timeline.json' })).json()).grant
  const operationId = randomUUID(), early = randomUUID()
  assert.deepEqual(await (await b.window({ op: 'cancel', operationId: early })).json(), { ok: true, reaped: true })
  assert.equal((await b.window({ operationId: early })).status, 409)
  assert.equal((await b.window({ op: 'cancel' })).status, 400)

  const rendering = b.window({ operationId }); await started
  assert.equal((await b.frame({ op: 'cancel' })).status, 409, 'legacy cancel cannot terminate a window')
  assert.equal((await b.window({ op: 'cancel', grant: second, operationId })).status, 200)
  assert.equal((await b.window({ op: 'cancel', operationId: randomUUID() })).status, 200)
  assert.equal(terminated, 0)
  let cancelled = false
  const cancelling = b.window({ op: 'cancel', operationId }).then(response => { cancelled = true; return response })
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(terminated, 1); assert.equal(cancelled, false)
  assert.equal((await b.control({ op: 'authorize-write', grant: second })).status, 409)
  finish({ exitCode: 1 }); await new Promise(resolve => setImmediate(resolve))
  assert.equal(cancelled, false, 'plain done does not prove the managed range exited')
  releaseRange(true)
  assert.deepEqual(await (await cancelling).json(), { ok: true, reaped: true })
  assert.equal((await rendering).status, 499)
  assert.equal((await b.frame()).status, 200)
})

test('failed window cleanup remains retryable by its original tuple after the read grant is stale', linuxWindow, async () => {
  for (const boundary of ['expiry', 'cwd', 'Session replacement', 'Session deletion']) {
    let reaped = false
    const b = await bench({ windowCapability, windowHandle(_spec, text) {
      return { stdout: Readable.from([Buffer.from(text)]), done: Promise.resolve({ exitCode: 0 }), terminate() {}, waitForExit: async () => reaped, collected: {} }
    } })
    try {
      const operationId = randomUUID()
      const rendering = await b.window({ operationId }); assert.equal(rendering.status, 502)
      assert.equal((await rendering.json()).error, 'PROCESS_CLEANUP_FAILED')
      if (boundary === 'expiry') b.expire()
      if (boundary === 'cwd') b.cwd.value = '/other'
      if (boundary === 'Session replacement') b.sessions.set('one', { id: 'one' })
      if (boundary === 'Session deletion') b.sessions.delete('one')
      assert(!(await b.control({ op: 'inspect', grant: b.grant })).ok, boundary)
      assert(!(await b.window({ operationId: randomUUID() })).ok, boundary)
      assert.equal((await b.window({ op: 'cancel', operationId })).status, 502, boundary)
      assert(!(await b.window({ op: 'cancel', sessionId: 'foreign', operationId })).ok, boundary)
      reaped = true
      for (let i = 0; i < 2; i++) assert.deepEqual(await (await b.window({ op: 'cancel', operationId })).json(), { ok: true, reaped: true }, boundary)
      b.cwd.value = '/workspace'; b.sessions.set('one', { id: 'one' })
      const newGrant = (await (await b.control({ op: 'open', timeline: 'timeline.json' })).json()).grant
      assert.equal((await b.frame({ grant: newGrant })).status, 200, boundary)
    } finally { reaped = true; await b.host.dispose() }
  }
})

test('close and exact cancel may arrive in either order; close does not await a window', linuxWindow, async () => {
  for (const cancelFirst of [false, true]) {
    let start, finish, releaseRange
    const started = new Promise(resolve => { start = resolve }), done = new Promise(resolve => { finish = resolve }), range = new Promise(resolve => { releaseRange = resolve })
    const b = await bench({ windowCapability, windowHandle(_spec, text) {
      start()
      return { stdout: Readable.from([Buffer.from(text)]), done, terminate() {}, waitForExit: () => range, collected: {} }
    } })
    try {
      const operationId = randomUUID(), rendering = b.window({ operationId }); await started
      const cancel = () => b.window({ op: 'cancel', operationId })
      const cancelling = cancelFirst ? cancel() : undefined
      if (cancelling) await new Promise(resolve => setImmediate(resolve))
      assert.equal((await b.control({ op: 'close', grant: b.grant })).status, 200)
      const confirmed = cancelling ?? cancel()
      const newGrant = (await (await b.control({ op: 'open', timeline: 'timeline.json' })).json()).grant
      assert.equal((await b.frame({ grant: newGrant })).status, 409, 'closed read grant must not release unreaped window ownership')
      const notAdmitted = randomUUID()
      assert.deepEqual(await (await b.window({ op: 'cancel', operationId: notAdmitted })).json(), { ok: true, reaped: true })
      assert(!(await b.window({ operationId: notAdmitted })).ok, 'close-before-render cannot later admit that operation')
      finish({ exitCode: 1 }); releaseRange(true)
      assert.deepEqual(await (await confirmed).json(), { ok: true, reaped: true })
      assert(!(await rendering).ok)
      assert.equal((await b.frame({ grant: newGrant })).status, 200)
    } finally { finish({ exitCode: 1 }); releaseRange(true); await b.host.dispose() }
  }
})

test('saturated window nonce ledger can certify a never-admitted nonce without growing', linuxWindow, async t => {
  const b = await bench({ windowCapability }); t.after(() => b.host.dispose())
  for (let i = 0; i < 128; i++) assert.equal((await b.window({ op: 'cancel', operationId: randomUUID() })).status, 200)
  const operationId = randomUUID()
  assert.equal((await b.window({ operationId })).status, 429)
  assert.deepEqual(await (await b.window({ op: 'cancel', operationId })).json(), { ok: true, reaped: true })
  assert.equal((await b.window({ operationId })).status, 429)
  assert(!(await b.window({ op: 'cancel', grant: randomUUID(), operationId })).ok)
  assert.equal(b.calls.length, 2)
})

test('a different grant observing a different version cannot cancel a window', linuxWindow, async t => {
  let start, finish, saved = version
  const started = new Promise(resolve => { start = resolve }), done = new Promise(resolve => { finish = resolve })
  const b = await bench({ windowCapability, saved: () => saved, windowHandle(_spec, text) {
    start()
    return { stdout: Readable.from([Buffer.from(text)]), done, terminate() {}, waitForExit: async () => true, collected: {} }
  } })
  t.after(async () => { finish({ exitCode: 1 }); await b.host.dispose() })
  const other = (await (await b.control({ op: 'open', timeline: 'timeline.json' })).json()).grant
  const rendering = b.window(); await started
  saved = nextVersion
  const observed = await b.control({ op: 'inspect', grant: other }); assert.equal((await observed.json()).content_version, nextVersion)
  saved = version
  finish({ exitCode: 0 })
  assert.equal((await rendering).status, 200)
})
