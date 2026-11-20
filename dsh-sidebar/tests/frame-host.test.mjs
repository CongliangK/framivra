// Adversarial Host boundary unit fixtures, NOT real provider/carrier evidence.
import test from 'node:test'
import assert from 'node:assert/strict'
import { PassThrough, Readable } from 'node:stream'
import { createHash, randomUUID } from 'node:crypto'
import { crc32, deflateSync } from 'node:zlib'
import { createPreviewHost } from '../src/host.mjs'
import { MemoryFs } from './helpers.mjs'
import { FRAME_WIRE_LIMIT, FRAME_REQUEST_LIMIT } from '../src/bounded.mjs'
const version = `sha256:${'a'.repeat(64)}`
const nextVersion = `sha256:${'b'.repeat(64)}`
const bareWindowCapability = { schema: 'framivra.timeline.preview-window/v1', inline_media: true, prepare_inputs: true }
const qualification = { launcher: 'framivra.preview-window.fixed-wheel/v1', wheel_sha256: 'c'.repeat(64), runtime_projection_sha256: 'd'.repeat(64) }
const windowCapability = { ...bareWindowCapability, ...qualification }
const windowLauncher = '/opt/private-window/preview_window_wheel.py'
const windowBytes = Buffer.from('000000186674797069736f6d0000020069736f6d69736f32', 'hex') // synthetic fixture, not playable media
const sha256 = createHash('sha256').update(windowBytes).digest('hex')
async function bench(options = {}) {
  const fs = new MemoryFs(), sessions = new Map([['one', { id: 'one' }]]), cwd = { value: '/workspace' }, calls = []
  sessions.list = () => [...sessions.values()]
  fs.files.set(windowLauncher, { type: 'file', version: 'wheel-launcher' })
  options.setup?.(fs, sessions)
  let now = 0
  const subprocess = { resolveExecutable: async command => command, spawn(spec) {
    calls.push(spec)
    const optionalProbe = ['--first-frame-capabilities', '--formal-archive-capabilities'].includes(spec.argv[1])
    const operation = optionalProbe ? spec.argv[1] : spec.argv[3] === 'timeline' ? spec.argv[4] : spec.argv[3]
    let value = operation === 'frame' ? { schema: 'framivra.timeline.frame/v1', operation, ok: true, diagnostics: [], content_version: version, image: { path: '/never/read.png' } }
      : { schema: 'framivra.timeline.read/v1', operation, ok: true, diagnostics: [], content_version: options.saved?.() ?? version, document: { project: { duration_frames: 36, fps: 23.976 } }, coverage: { complete: true, scope: 'unit fixture', unexpanded: [] }, capabilities: { operations: ['inspect', 'query'], preview_window: spec.argv[0] === windowLauncher ? options.windowCapability : options.ordinaryWindowCapability, frame: options.unsupported ? {} : { schema: 'framivra.timeline.frame/v1', explicit_render_required: true, ...(options.prepareCapability ? { prepare_gfx: options.prepareCapability } : {}), inline_png: { flag: '--include-png', encoding: 'base64', wire_limit_bytes: FRAME_WIRE_LIMIT } } } }
    if (operation === 'inspect' && options.videoTracks) value.document.video_tracks = options.videoTracks
    if (operation === 'capabilities' && options.storageCapability) value.capabilities.storage = options.storageCapability
    if (operation === '--first-frame-capabilities') value = options.firstFrameCapability ?? { ok: false }
    if (operation === '--formal-archive-capabilities') value = options.archiveCapability ?? { ok: false }
    if (operation === 'preview-formal-archive') {
      const start = Number(spec.argv[6])
      value = { schema: 'framivra.preview-formal-archive/v1', ...qualification, ok: true, nonce: spec.argv.at(-1).slice('--nonce='.length),
        content_version: version, start, end: 36, fps: { num: 24000, den: 1001 }, width: 1920, height: 1080, audio_sample_rate: 48000,
        audio_channels: 2, mime: 'video/mp4', sha256, byte_length: windowBytes.length, base64: windowBytes.toString('base64'), manifest_sha256: 'e'.repeat(64) }
      if (options.archiveResult) value = options.archiveResult(value)
    }
    if (['preview-window', 'preview-window-first-frame'].includes(operation)) {
      const start = Number(spec.argv[6])
      value = { schema: windowCapability.schema, ...qualification, ok: true, content_version: version, start, end: Math.min(36, start + 24), fps: { num: 24000, den: 1001 }, width: 1920, height: 1080,
        mime: 'video/mp4', audio_sample_rate: 48000, audio_channels: 2, byte_length: windowBytes.length, sha256, base64: windowBytes.toString('base64'), path: '/never/read.mp4' }
      value = options.windowResult ? options.windowResult(value, spec) : value
    }
    const text = JSON.stringify(value) + '\n'
    if (operation === 'frame' && options.handle) return options.handle(spec, text)
    if (operation === 'preview-window' && options.windowHandle) return options.windowHandle(spec, text)
    if (operation === 'preview-window-first-frame') return options.firstFrameHandle(spec, value)
    if (operation === 'preview-formal-archive' && options.archiveHandle) return options.archiveHandle(spec, text)
    if (operation === 'inspect' && options.inspectHandle) {
      const handle = options.inspectHandle(spec, text)
      if (handle) return handle
    }
    return { stdout: Readable.from([Buffer.from(text)]), done: Promise.resolve({ exitCode: optionalProbe || value.ok ? 0 : 1, signal: null }), terminate() {}, waitForExit: async () => true, collected: { stdout: { readFrom: () => ({ text, lossy: false }) }, stderr: { readFrom: () => ({ text: '', lossy: false }) } } }
  } }
  const config = { vtxExecutable: '/opt/trusted/vtx', previewWindowExecutable: options.windowExecutable ?? (options.windowCapability ? windowLauncher : ''), clock: () => now }
  if (options.ordinaryConfig) {
    config.vtxConfigFile = '/opt/settings/config.json'
    fs.files.set(config.vtxConfigFile, { type: 'file', version: 'ordinary-config' })
    fs.lstat = async path => fs.files.get(path) ?? { type: 'directory' }
  }
  const getCwd = session => session.cwd ?? cwd.value
  const host = createPreviewHost({ fs, sessions, workingDirectory: { get: getCwd, ensure: async agent => getCwd(agent.session) }, subprocess }, config)
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
test('whole-timeline Host lease is separate, canonical, and freshness-fenced on cache reads', linuxWindow, async t => {
  const b = await bench({ windowCapability: { ...windowCapability, input_epoch: true },
    storageCapability: { schema: 'framivra.storage/v1', request_stdin: true, operations: ['inspect', 'plan', 'clean'] },
    windowResult: value => ({ ...value, input_epoch: nextVersion }) })
  t.after(() => b.host.dispose())
  const ownerId = b.opening.previewSession.ownerId
  assert.deepEqual(b.opening.previewSession.programRun, { version: 1 })
  const scope = { op: 'authorize-preview-session', grant: b.grant, scope: 'whole-timeline', frame: 28, contentVersion: version,
    authorizationId: randomUUID(), renderConsent: 'cache/preview-only', prepareInputs: true, storageConsent: 'temporary-browser-cache' }
  const stopAttempt = { op: 'cancel-program-authorization', grant: b.grant, ownerId, authorizationId: scope.authorizationId }
  assert.equal((await b.control(stopAttempt)).status, 200)
  assert.equal((await b.control(scope)).status, 409, 'cancel can overtake its authorization POST')
  scope.authorizationId = randomUUID()
  const auth = await b.control(scope); assert.equal(auth.status, 200, await auth.clone().text())
  const { grant } = await auth.json()
  assert.equal(grant.maxWindows, 1024)
  assert.deepEqual(grant.programRun, { version: 1, contentVersion: version, frame: 28, duration: 36, span: 24, maxBytes: 512 * 1024 * 1024 })
  assert.equal((await b.window()).status, 409, 'manual window must not compete with the whole-timeline queue')
  assert.equal((await b.control({ op: 'authorize-write', grant: b.grant })).status, 409)
  const clean = await b.control({ op: 'storage-clean', grant: b.grant, confirm: 'stopped-writers-and-cleanup', planToken: 'irrelevant' })
  assert.equal((await clean.json()).error, 'PROGRAM_RUN_BUSY')
  const render = (frame, op = 'program-render') => b.window({ op, frame, ownerId, previewGrant: grant.id, renderConsent: undefined, prepareInputs: undefined })
  assert.equal((await render(0, 'session-render')).status, 403)
  assert.equal((await render(0)).status, 409, 'first window is the one containing the explicit playhead')
  const first = await render(24); assert.equal(first.status, 200, await first.clone().text())
  assert.equal(first.headers.get('X-Framivra-End'), '36')
  assert.equal(first.headers.get('X-Framivra-Media-Kind'), 'program-preview-window/v1')
  assert.equal((await render(24)).status, 409, 'a completed window cannot be rerendered on the same lease')
  const check = { op: 'program-check', grant: b.grant, previewGrant: grant.id, ownerId, frame: 24, sha256 }
  assert.equal((await b.control(check)).status, 200)
  b.fs.files.get('/workspace/timeline.json').version = 'changed'
  assert.equal((await b.control(check)).status, 409, 'retained bytes are not authorized after the file changes')
  assert.equal((await render(0)).status, 403)
  assert.equal((await b.control({ ...stopAttempt, authorizationId: scope.authorizationId })).status, 200)

  scope.authorizationId = randomUUID()
  const replacement = await b.control(scope)
  assert.equal(replacement.status, 200)
  b.fs.files.set('/elsewhere', { type: 'directory', version: 'other-root' })
  b.fs.files.set('/elsewhere/timeline.json', { type: 'file', version: 'other-timeline' })
  b.cwd.value = '/elsewhere'
  const moved = await b.control({ op: 'open', timeline: 'timeline.json' })
  assert.equal(moved.status, 200, await moved.clone().text())
  assert.equal((await b.window({ grant: (await moved.json()).grant })).status, 200, 'abandoned workspace cannot block the new preview')
  assert.equal((await b.control({ ...stopAttempt, authorizationId: scope.authorizationId })).status, 200, 'exact stale-scope cleanup still works')
})
test('another open preview taking ownership during authorization prevents whole-timeline mint', { ...linuxWindow, timeout: 2500 }, async t => {
  const beforeFinal = Promise.withResolvers(), finishAuthorization = Promise.withResolvers()
  const started = Promise.withResolvers(), finishWindow = Promise.withResolvers()
  let expectInspect = false, inspected = false
  const b = await bench({ windowCapability: { ...windowCapability, input_epoch: true },
    windowResult: value => ({ ...value, input_epoch: nextVersion }),
    inspectHandle() { if (expectInspect) inspected = true },
    windowHandle(spec, text) {
      started.resolve()
      return { stdout: Readable.from([Buffer.from(text)]), done: finishWindow.promise, terminate() {}, waitForExit: async () => true }
    } })
  t.after(async () => { finishAuthorization.resolve(); finishWindow.resolve({ exitCode: 0 }); await b.host.dispose() })
  const other = await (await b.control({ op: 'open', timeline: 'timeline.json' })).json()
  const stat = b.fs.stat.bind(b.fs)
  b.fs.stat = async (target, signal) => {
    // Fire after the authoritative inspect has returned and its child has
    // been reaped; an ordinary read still holding ownership would mask the race.
    if (expectInspect && inspected && target.targetKey === '/workspace/timeline.json') {
      expectInspect = false
      beforeFinal.resolve()
      await finishAuthorization.promise
    }
    return stat(target, signal)
  }
  expectInspect = true
  const authorizing = b.control({ op: 'authorize-preview-session', grant: b.grant, scope: 'whole-timeline', frame: 28, contentVersion: version,
    authorizationId: randomUUID(), renderConsent: 'cache/preview-only', prepareInputs: true, storageConsent: 'temporary-browser-cache' })
  const earlyAuthorization = await Promise.race([beforeFinal.promise, authorizing])
  if (earlyAuthorization) assert.fail(`Authorization skipped the final checkpoint: ${await earlyAuthorization.text()}`)
  const competing = b.window({ grant: other.grant })
  const earlyCompeting = await Promise.race([started.promise, competing])
  if (earlyCompeting) assert.fail(`Competing worker did not start: ${await earlyCompeting.text()}`)
  finishAuthorization.resolve()
  const refused = await authorizing
  assert.equal(refused.status, 409)
  assert.equal((await refused.json()).error, 'PROGRAM_RUN_BUSY')
  finishWindow.resolve({ exitCode: 0 })
  assert.equal((await competing).status, 200)
})

test('window uses separate owner POST, actual capability and two consents; only verified bytes leave Host', linuxWindow, async t => {
  for (const [options, probeCount] of [[{ ordinaryWindowCapability: windowCapability }, 0], [{ windowCapability: bareWindowCapability }, 1],
    [{ windowCapability: { ...windowCapability, prepare_inputs: false } }, 1]]) {
    const absent = await bench(options)
    t.after(() => absent.host.dispose())
    assert(absent.opening.host.ops.includes('preview-window'))
    assert.equal(absent.opening.capabilities.capabilities.preview_window, undefined)
    assert.equal((await absent.window()).status, 422)
    assert.equal(absent.calls.length, 2 + probeCount)
  }

  const b = await bench({ windowCapability, ordinaryConfig: true }); t.after(() => b.host.dispose())
  assert.deepEqual(b.opening.capabilities.capabilities.preview_window, windowCapability)
  for (const [payload, status] of [[{ prepareInputs: undefined }, 403], [{ prepareInputs: 'true' }, 403], [{ renderConsent: '' }, 403],
    [{ contentVersion: nextVersion }, 409], [{ frame: 1.5 }, 409], [{ path: '/other.mp4' }, 400], [{ prepareGfx: true }, 400], [{ htmlTrust: 'trusted-local-html' }, 400]]) {
    assert.equal((await b.window(payload)).status, status, JSON.stringify(payload))
  }
  assert.equal((await b.host.window(new Request('http://unit/api/framivra/window'))).status, 405)
  assert.equal((await b.control({ op: 'preview-window', grant: b.grant })).status, 400)
  assert.deepEqual(Object.keys(b.host.agent), ['read'])
  await assert.rejects(b.host.agent.read(undefined, { view: 'preview-window' }), { code: 'BAD_OPERATION' })
  assert.deepEqual(b.calls.map(call => [call.argv[0], call.argv[3] ?? call.argv[1]]), [
    ['/opt/trusted/vtx', 'capabilities'], ['/opt/trusted/vtx', 'inspect'], [windowLauncher, 'capabilities'], [windowLauncher, '--formal-archive-capabilities'],
  ])

  const operationId = randomUUID()
  const response = await b.window({ frame: 33, operationId })
  assert.equal(response.status, 200, await response.clone().text())
  assert.equal(response.headers.get('content-type'), 'video/mp4')
  assert.equal(response.headers.get('cache-control'), 'no-store')
  assert.deepEqual(['version', 'start', 'end', 'sha256'].map(key => response.headers.get(`x-framivra-${key}`)), [version, '33', '36', sha256])
  assert.deepEqual(Buffer.from(await response.arrayBuffer()), windowBytes)
  assert.deepEqual(b.calls.slice(4).map(call => call.argv), [
    [windowLauncher, '-t', '/workspace/timeline.json', 'timeline', 'preview-window', '--frame', '33', `--expected-content-version=${version}`, '--render', '--include-media', '--prepare-inputs'],
    ['/opt/trusted/vtx', '-t', '/workspace/timeline.json', 'inspect'],
  ])
  assert.deepEqual(b.calls.filter(call => call.argv[0] === windowLauncher).map(call => [call.cwd, call.env.VTX_CONFIG]), Array(3).fill(['/opt/private-window', undefined]))
  assert(b.calls.filter(call => call.argv[0] === '/opt/trusted/vtx').every(call => call.env.VTX_CONFIG === '/opt/settings/config.json'))
  assert.equal((await b.window({ operationId })).status, 409)
  assert.equal(b.fs.reads.length, 0)
  assert.equal((await b.frame({ operationId })).status, 200, 'fixed frame remains a separate operation')
  const beforeModelRead = b.calls.length
  const modelRead = await b.host.agent.read({ agent: { session: b.sessions.get('one') }, signal: new AbortController().signal }, { timeline: 'timeline.json', view: 'inspect' })
  assert.equal(modelRead.capabilities.capabilities.preview_window, undefined)
  assert(b.calls.slice(beforeModelRead).every(call => call.argv[0] === '/opt/trusted/vtx'), 'model read cannot spawn the window launcher')
})

test('automatic windows require their own owner grant and tombstone out-of-order cancellation', linuxWindow, async t => {
  const b = await bench({ windowCapability }); t.after(() => b.host.dispose())
  const consent = { op: 'authorize-preview-session', scope: 'current-project', grant: b.grant, renderConsent: 'cache/preview-only', prepareInputs: true }
  assert.equal((await b.control({ ...consent, prepareInputs: false })).status, 403)
  const authorized = await b.control(consent)
  assert.equal(authorized.status, 200)
  const { grant } = await authorized.json(), { ownerId } = b.opening.previewSession
  assert.notEqual(grant.id, b.grant); assert.equal(grant.ownerId, ownerId)
  const origin = { sessionId: 'one', grant: b.grant, ownerId, previewGrant: grant.id }
  const send = (op, id, extra = {}) => b.host.window(new Request('http://unit/api/framivra/window', { method: 'POST', body: JSON.stringify({ ...origin, op, operationId: id, ...(op === 'session-render' && { frame: 1, contentVersion: version }), ...extra }) }))
  const first = randomUUID(), cancelled = randomUUID()
  assert.equal((await send('session-render', first, { previewGrant: b.grant })).status, 403)
  assert.equal((await send('session-render', first, { renderConsent: 'cache/preview-only' })).status, 400)
  assert.equal((await send('session-render', first)).status, 200)
  assert.equal((await send('session-cancel', cancelled, { ownerId: randomUUID() })).status, 403)
  assert.deepEqual(await (await send('session-cancel', cancelled)).json(), { ok: true, reaped: true, operationId: cancelled, ownerId, openGrant: b.grant, grantId: grant.id })
  assert.equal((await send('session-render', cancelled)).status, 409)
  assert.equal((await b.control({ op: 'revoke-preview-session', ...origin })).status, 200)
  assert.equal((await send('session-render', randomUUID())).status, 403)
  // Simulate a lost cleanup acknowledgement, long after the bounded registry rotated.
  for (let i = 0; i < 64; i++) {
    const newer = (await (await b.control(consent)).json()).grant
    assert.equal((await b.control({ op: 'revoke-preview-session', ...origin, previewGrant: newer.id })).status, 200)
  }
  assert.equal((await send('session-render', randomUUID())).status, 403)
  assert.equal((await send('session-cancel', first, { ownerId: randomUUID() })).status, 403)
  assert.equal((await send('session-cancel', first)).status, 200, 'a retired authentic grant can confirm cleanup only')
  assert.equal(b.calls.filter(call => call.argv.includes('preview-window')).length, 1)
})

const warmEpoch = `sha256:${'f'.repeat(64)}`
const warmWindowCapability = { ...windowCapability, input_epoch: true, warm_svg_media: true }
const svgTracks = [{ kind: 'gfx', name: 'Titles', clips: [{ format: 'svg-sequence', src: 'gfx/sequence.json', dst_in: 24, dur: 12 }] }]
const sessionConsent = grant => ({ op: 'authorize-preview-session', scope: 'current-project', grant, renderConsent: 'cache/preview-only', prepareInputs: true })
const sessionWindow = (b, grant, data = {}) => b.host.window(new Request('http://unit/api/framivra/window', { method: 'POST', body: JSON.stringify({
  op: 'session-render', sessionId: 'one', grant: b.grant, previewGrant: grant.id, ownerId: grant.ownerId,
  operationId: randomUUID(), frame: 1, contentVersion: version, ...data,
}) }))

test('only a trusted fixed-wheel capability and a separately opted-in owner grant select a single intersecting SVG window', linuxWindow, async t => {
  const legacy = await bench({ windowCapability, ordinaryWindowCapability: warmWindowCapability }); t.after(() => legacy.host.dispose())
  assert.equal(legacy.opening.capabilities.capabilities.preview_window.warm_svg_media, undefined)
  assert.equal((await legacy.control({ ...sessionConsent(legacy.grant), readWarmSvg: true })).status, 403)

  const b = await bench({ windowCapability: warmWindowCapability, videoTracks: svgTracks, windowResult: (value, spec) =>
    spec.argv.at(-1) === '--read-warm-svg' ? { ...value, warm: true, input_epoch: warmEpoch } :
      { schema: value.schema, ok: false, diagnostics: [{ code: 'gfx.unsupported' }] },
  }); t.after(() => b.host.dispose())
  assert.equal(b.opening.capabilities.capabilities.preview_window.warm_svg_media, true)
  const consent = sessionConsent(b.grant), renders = () => b.calls.filter(call => call.argv[4] === 'preview-window')
  for (const extra of [{ prepareInputs: false }, { renderConsent: '' }, { readWarmSvg: 'true' }]) {
    assert.equal((await b.control({ ...consent, readWarmSvg: true, ...extra })).status, 403)
  }
  assert.equal((await b.window({ readWarmSvg: true })).status, 400)
  assert.equal(renders().length, 0)
  const normal = (await (await b.control(consent)).json()).grant
  assert.equal(normal.readWarmSvg, undefined)
  assert.equal((await sessionWindow(b, normal)).status, 422)
  assert.deepEqual(renders().at(-1).argv.slice(8), ['--render', '--include-media', '--prepare-inputs'])
  assert.equal((await b.control({ op: 'revoke-preview-session', grant: b.grant, ownerId: normal.ownerId, previewGrant: normal.id })).status, 200)

  const allowed = (await (await b.control({ ...consent, readWarmSvg: true })).json()).grant
  assert.equal(allowed.readWarmSvg, true)
  assert.equal((await sessionWindow(b, allowed, { ownerId: randomUUID() })).status, 403)
  assert.equal((await sessionWindow(b, allowed, { readWarmSvg: true })).status, 400)
  assert.equal(renders().length, 1)
  assert.equal((await sessionWindow(b, allowed, { frame: 0 })).status, 422, 'SVG starts exactly at the excluded NTSC window end; legacy path refuses off-window GFX')
  assert.deepEqual(renders().at(-1).argv.slice(8), ['--render', '--include-media', '--prepare-inputs'])
  const before = b.calls.length, response = await sessionWindow(b, allowed)
  assert.equal(response.status, 200, await response.clone().text())
  assert.equal(response.headers.get('X-Framivra-Input-Epoch'), warmEpoch)
  assert.deepEqual(Buffer.from(await response.arrayBuffer()), windowBytes)
  assert.deepEqual(b.calls.slice(before).map(call => call.argv), [
    ['/opt/trusted/vtx', '-t', '/workspace/timeline.json', 'inspect'],
    [windowLauncher, '-t', '/workspace/timeline.json', 'timeline', 'preview-window', '--frame', '1', `--expected-content-version=${version}`, '--render', '--include-media', '--prepare-inputs', '--read-warm-svg'],
    ['/opt/trusted/vtx', '-t', '/workspace/timeline.json', 'inspect'],
  ])
})

test('warm media uses the real saved version, never accepts the digest-only ABI or an unproven warm receipt', linuxWindow, async t => {
  let saved = version, patch
  const b = await bench({ windowCapability: warmWindowCapability, videoTracks: svgTracks, saved: () => saved,
    windowResult: value => ({ ...value, warm: true, input_epoch: warmEpoch, ...patch }),
  }); t.after(() => b.host.dispose())
  const { grant } = await (await b.control({ ...sessionConsent(b.grant), readWarmSvg: true })).json()
  saved = nextVersion
  assert.equal((await (await sessionWindow(b, grant)).json()).error, 'STALE_WINDOW')
  assert(!b.calls.some(call => call.argv[4] === 'preview-window'), 'preflight mismatch avoids invoking the wheel')
  saved = version
  for (patch of [{ warm: undefined }, { warm: 'true' }, { content_version: nextVersion }, { input_epoch: undefined }, { schema: 'framivra.preview-window.warm-svg/v1' }]) {
    const response = await sessionWindow(b, grant)
    assert.equal(response.status, 502)
    assert.equal((await response.json()).error, 'PROTOCOL_MISMATCH')
  }
  patch = { wheel_sha256: 'e'.repeat(64) }
  assert.equal((await (await sessionWindow(b, grant)).json()).error, 'WINDOW_PROFILE_CHANGED')
})

test('program audio requires direct fixed-wheel capability, separate owner grant and matching render intent', linuxWindow, async t => {
  const legacy = await bench({ windowCapability: { ...windowCapability, input_epoch: true, program_audio: 'true' },
    ordinaryWindowCapability: { ...windowCapability, program_audio: true } }); t.after(() => legacy.host.dispose())
  assert.equal(legacy.opening.capabilities.capabilities.preview_window.program_audio, undefined)
  assert.deepEqual(legacy.opening.previewSession.continuation, { version: 1 })
  assert.equal(legacy.opening.previewSession.programAudioContinuation, undefined)
  assert.equal((await legacy.control({ ...sessionConsent(legacy.grant), continuation: true, useProgramAudio: true })).status, 403)
  const programOnly = await bench({ windowCapability: { ...windowCapability, program_audio: true },
    ordinaryWindowCapability: { ...windowCapability, input_epoch: true } }); t.after(() => programOnly.host.dispose())
  assert.equal(programOnly.opening.previewSession.programAudioContinuation, undefined)
  assert.equal((await programOnly.control({ ...sessionConsent(programOnly.grant), continuation: true, useProgramAudio: true })).status, 403)

  const b = await bench({ windowCapability: { ...windowCapability, program_audio: true, input_epoch: true },
    windowResult: (value, spec) => ({ ...value, input_epoch: warmEpoch,
      ...(spec.argv.at(-1) === '--use-program-audio' && { program_audio: true }) }),
  }); t.after(() => b.host.dispose())
  assert.equal(b.opening.capabilities.capabilities.preview_window.program_audio, true)
  const consent = sessionConsent(b.grant), optedIn = { ...consent, useProgramAudio: true }
  const model = { agent: { session: b.sessions.get('one') }, signal: new AbortController().signal }
  await assert.rejects(b.host.agent.read(model, { timeline: 'timeline.json', view: 'authorize-preview-session', query_json: JSON.stringify(optedIn) }),
    { code: 'BAD_OPERATION' })
  const inspected = await b.host.agent.read(model, { timeline: 'timeline.json', view: 'inspect' })
  assert.equal(inspected.capabilities.capabilities.preview_window, undefined, 'model open never receives the direct launcher capability')
  const renders = () => b.calls.filter(call => call.argv[4] === 'preview-window')
  const revoke = grant => b.control({ op: 'revoke-preview-session', grant: b.grant, ownerId: grant.ownerId, previewGrant: grant.id })
  for (const extra of [{ prepareInputs: false }, { renderConsent: '' }, { useProgramAudio: false }, { useProgramAudio: 'true' },
    { firstFrame: true }, { continuation: false }, { continuation: 'true' }]) {
    assert.equal((await b.control({ ...optedIn, ...extra })).status, 403, JSON.stringify(extra))
  }
  assert.equal((await b.window({ useProgramAudio: true })).status, 400)
  assert.equal((await b.window({ op: 'formal-archive-read', useProgramAudio: true })).status, 400)
  const { grant: normal } = await (await b.control(consent)).json()
  assert.equal(Object.hasOwn(normal, 'useProgramAudio'), false)
  assert.equal((await sessionWindow(b, normal, { useProgramAudio: true })).status, 403)
  assert.equal(renders().length, 0)
  const ordinary = await sessionWindow(b, normal)
  assert.equal(ordinary.status, 200)
  assert.equal(ordinary.headers.get('X-Framivra-Program-Audio'), null)
  assert.deepEqual(renders().at(-1).argv.slice(8), ['--render', '--include-media', '--prepare-inputs'])
  assert.equal((await revoke(normal)).status, 200)

  const { grant } = await (await b.control(optedIn)).json()
  assert.equal(grant.useProgramAudio, true)
  for (const intent of [{}, { useProgramAudio: false }, { useProgramAudio: true, ownerId: randomUUID() },
    { useProgramAudio: true, firstFrame: true }]) assert.equal((await sessionWindow(b, grant, intent)).status, 403)
  assert.equal(renders().length, 1)
  const response = await sessionWindow(b, grant, { useProgramAudio: true })
  assert.equal(response.status, 200, await response.clone().text())
  assert.equal(response.headers.get('X-Framivra-Program-Audio'), '1')
  assert.equal(response.headers.get('X-Framivra-Input-Epoch'), warmEpoch)
  assert.deepEqual(Buffer.from(await response.arrayBuffer()), windowBytes)
  assert.deepEqual(renders().at(-1).argv.slice(8), ['--render', '--include-media', '--prepare-inputs', '--use-program-audio'])
  assert.equal((await revoke(grant)).status, 200)
  assert.equal((await sessionWindow(b, grant, { useProgramAudio: true })).status, 403)
  assert.equal(renders().length, 2)
  assert.deepEqual(b.fs.reads, [])
})

test('program audio checks the fixed-wheel media receipt even with an independently authorized warm SVG hit', linuxWindow, async t => {
  let patch
  const b = await bench({ windowCapability: { ...warmWindowCapability, program_audio: true }, videoTracks: svgTracks,
    windowResult: value => ({ ...value, input_epoch: warmEpoch, warm: true, program_audio: true, ...patch }),
  }); t.after(() => b.host.dispose())
  const consent = { ...sessionConsent(b.grant), readWarmSvg: true }
  const { grant: warm } = await (await b.control(consent)).json()
  for (patch of [{}, { program_audio: false }]) {
    const normal = await sessionWindow(b, warm)
    assert.equal(normal.status, 502)
    assert.equal((await normal.json()).error, 'PROTOCOL_MISMATCH')
  }
  assert.equal((await b.control({ op: 'revoke-preview-session', grant: b.grant, previewGrant: warm.id, ownerId: warm.ownerId })).status, 200)
  const { grant } = await (await b.control({ ...consent, useProgramAudio: true, continuation: true })).json()
  assert.equal(grant.readWarmSvg, true)
  assert.equal(grant.continuation, true)
  for (patch of [{ program_audio: undefined }, { program_audio: 'true' }, { input_epoch: undefined }, { warm: false }]) {
    const response = await sessionWindow(b, grant, { useProgramAudio: true })
    assert.equal(response.status, 502)
    assert.equal((await response.json()).error, 'PROTOCOL_MISMATCH')
  }
  patch = undefined
  const operationId = randomUUID()
  const response = await sessionWindow(b, grant, { operationId, useProgramAudio: true })
  assert.equal(response.status, 200, await response.clone().text())
  assert.equal(response.headers.get('X-Framivra-Program-Audio'), '1')
  assert.equal(response.headers.get('X-Framivra-Input-Epoch'), warmEpoch)
  assert.equal((await sessionWindow(b, grant, { op: 'session-cancel', operationId, frame: undefined, contentVersion: undefined })).status, 200)
  const tail = await sessionWindow(b, grant, { op: 'session-continue', previousOperationId: operationId, inputEpoch: warmEpoch,
    frame: 25, useProgramAudio: true })
  assert.equal(tail.status, 200, await tail.clone().text())
  assert.equal(tail.headers.get('X-Framivra-Program-Audio'), '1')
  assert.equal(tail.headers.get('X-Framivra-Continuation-From'), operationId)
  assert.deepEqual(b.calls.filter(call => call.argv[4] === 'preview-window').slice(-2).map(call => call.argv.slice(8)),
    Array.from({ length: 2 }, () => ['--render', '--include-media', '--prepare-inputs', '--read-warm-svg', '--use-program-audio']))
})

test('program audio continuation keeps the exact mode, predecessor, reap and single-use nonce', linuxWindow, async t => {
  const programEpoch = `sha256:${'e'.repeat(64)}`
  let patch
  const b = await bench({ windowCapability: { ...windowCapability, input_epoch: true, program_audio: true },
    windowResult: value => ({ ...value, input_epoch: programEpoch, program_audio: true, ...patch }),
  }); t.after(() => b.host.dispose())
  assert.deepEqual(b.opening.previewSession.programAudioContinuation, { version: 1 })
  const { grant } = await (await b.control({ ...sessionConsent(b.grant), useProgramAudio: true, continuation: true })).json()
  assert.equal(grant.useProgramAudio, true)
  assert.equal(grant.continuation, true)
  const send = (op, operationId, data) => sessionWindow(b, grant, { op, operationId, ...data })
  const cancel = operationId => send('session-cancel', operationId, { frame: undefined, contentVersion: undefined })
  const renders = () => b.calls.filter(call => call.argv[4] === 'preview-window')

  const start = randomUUID(), next = randomUUID(), earlyCancel = randomUUID()
  const first = await send('session-render', start, { frame: 0, useProgramAudio: true })
  assert.equal(first.status, 200)
  assert.equal(first.headers.get('X-Framivra-Continuation-From'), null)
  const tail = { previousOperationId: start, inputEpoch: programEpoch, frame: 24, useProgramAudio: true }
  assert.equal((await send('session-continue', next, tail)).status, 409, 'terminal media alone does not admit a successor')
  assert.equal((await cancel(start)).status, 200)
  for (const mode of [undefined, false, 'true']) {
    assert.equal((await send('session-continue', next, { ...tail, useProgramAudio: mode })).status, 403)
  }
  for (const fields of [{ ownerId: randomUUID() }, { previewGrant: randomUUID() }, { frame: 23 },
    { previousOperationId: randomUUID() }, { inputEpoch: warmEpoch }, { contentVersion: nextVersion }]) {
    const response = await send('session-continue', next, { ...tail, ...fields })
    assert.equal(response.status, fields.ownerId || fields.previewGrant ? 403 : 409)
  }
  assert.equal((await cancel(earlyCancel)).status, 200)
  assert.equal((await send('session-continue', earlyCancel, tail)).status, 409, 'early cancel seals the successor nonce')
  assert.equal(renders().length, 1)
  const playable = await send('session-continue', next, tail)
  assert.equal(playable.status, 200)
  assert.equal(playable.headers.get('X-Framivra-Program-Audio'), '1')
  assert.equal(playable.headers.get('X-Framivra-Input-Epoch'), programEpoch)
  assert.equal(playable.headers.get('X-Framivra-Continuation-From'), start)
  assert.equal(renders().at(-1).argv.at(-1), '--use-program-audio')
  assert.equal((await cancel(start)).status, 200, 'late reap acknowledgement cannot change the new predecessor')
  assert.equal((await send('session-continue', randomUUID(), tail)).status, 409)

  for (const [badReceipt, code] of [[{ program_audio: undefined }, 'PROTOCOL_MISMATCH'], [{ input_epoch: warmEpoch }, 'WINDOW_INPUTS_CHANGED']]) {
    const operationId = randomUUID()
    assert.equal((await send('session-render', operationId, { frame: 0, useProgramAudio: true })).status, 200)
    assert.equal((await cancel(operationId)).status, 200)
    patch = badReceipt
    const refused = await send('session-continue', randomUUID(), { ...tail, previousOperationId: operationId })
    patch = undefined
    assert.equal((await refused.json()).error, code)
    assert.equal((await send('session-continue', randomUUID(), { ...tail, previousOperationId: operationId })).status, 409, 'a refused successor spends its predecessor')
  }
})

test('one separately permitted adjoining window uses a reaped predecessor and freshly confirmed input epoch', linuxWindow, async t => {
  let epoch = `sha256:${'e'.repeat(64)}`
  const b = await bench({ windowCapability: { ...windowCapability, input_epoch: true }, windowResult: value => ({ ...value, input_epoch: epoch }) }); t.after(() => b.host.dispose())
  assert.deepEqual(b.opening.previewSession.continuation, { version: 1 })
  const consent = { op: 'authorize-preview-session', scope: 'current-project', grant: b.grant, renderConsent: 'cache/preview-only', prepareInputs: true }
  const auth = (async extra => (await (await b.control({ ...consent, ...extra })).json()).grant)
  const send = (g, op, operationId, extra = {}) => b.host.window(new Request('http://unit/api/framivra/window', { method: 'POST',
    body: JSON.stringify({ op, sessionId: 'one', grant: b.grant, previewGrant: g.id, ownerId: g.ownerId, operationId, ...extra }) }))
  const old = await auth({})
  assert.equal((await send(old, 'session-continue', randomUUID(), { frame: 24, contentVersion: version, previousOperationId: randomUUID(), inputEpoch: epoch })).status, 403)
  await b.control({ op: 'revoke-preview-session', grant: b.grant, previewGrant: old.id, ownerId: old.ownerId })
  const g = await auth({ continuation: true }); assert.equal(g.continuation, true)
  for (const changed of [false, true]) {
    const start = randomUUID(), next = randomUUID()
    const first = await send(g, 'session-render', start, { frame: 0, contentVersion: version }); assert.equal(first.status, 200)
    assert.equal(first.headers.get('x-framivra-input-epoch'), epoch)
    const tail = { previousOperationId: start, inputEpoch: epoch, frame: 24, contentVersion: version }
    assert.equal((await send(g, 'session-continue', next, tail)).status, 409, 'receipt alone is not a release')
    assert.equal((await send(g, 'session-cancel', start)).status, 200)
    assert.equal((await send(g, 'session-continue', next, { ...tail, useProgramAudio: true })).status, 403)
    assert.equal((await send(g, 'session-continue', next, { ...tail, frame: 23 })).status, 409)
    if (changed) epoch = `sha256:${'f'.repeat(64)}`
    const response = await send(g, 'session-continue', next, tail)
    assert.equal(response.status, changed ? 409 : 200)
    if (changed) assert.equal((await response.json()).error, 'WINDOW_INPUTS_CHANGED')
    else { assert.equal(response.headers.get('x-framivra-continuation-from'), start); assert.equal(response.headers.get('x-framivra-end'), '36') }
  }
})

const firstFrameSchema = 'framivra.preview-window.first-frame/v1'
const firstFrameCapability = { schema: firstFrameSchema, available: true, ...qualification, record_payload_limit_exclusive: FRAME_WIRE_LIMIT }
function rgbPng() {
  const chunk = (name, bytes) => {
    const out = Buffer.alloc(12 + bytes.length)
    out.writeUInt32BE(bytes.length); out.write(name, 4); bytes.copy(out, 8)
    out.writeUInt32BE(crc32(out.subarray(4, -4)), out.length - 4)
    return out
  }
  return Buffer.concat([Buffer.from('89504e470d0a1a0a', 'hex'), chunk('IHDR', Buffer.from('00000780000004380802000000', 'hex')),
    chunk('IDAT', deflateSync(Buffer.alloc(1080 * (1 + 1920 * 3)))), chunk('IEND', Buffer.alloc(0))])
}
const turn = () => new Promise(resolve => setImmediate(resolve))
function pack(kind, nonce, result) {
  const body = Buffer.from(JSON.stringify({ nonce, result })), header = Buffer.alloc(5)
  header.write(kind); header.writeUInt32BE(body.length, 1)
  return Buffer.concat([header, body])
}
function firstFrameChild(bytes) {
  let start, finish, reaped, settling
  const started = new Promise(resolve => { start = resolve }), done = new Promise(resolve => { finish = resolve })
  const range = new Promise(resolve => { reaped = resolve }), settled = new Promise(resolve => { settling = resolve })
  const stdout = new PassThrough()
  return { stdout, started, settled, finish, reaped, handle(spec, value) {
    const nonce = spec.argv.at(-1).slice('--nonce='.length)
    const first = { schema: firstFrameSchema, ...qualification, provisional: true, mime: 'image/png', frame: value.start, window: [value.start, value.end],
      segment: [value.start, value.end], fps: value.fps, width: 1920, height: 1080, content_version: version,
      byte_length: bytes.length, sha256: createHash('sha256').update(bytes).digest('hex'), base64: bytes.toString('base64') }
    const { path, ...window } = value
    start({ first, final: { ...window, input_epoch: warmEpoch }, nonce })
    return { stdout, done, terminate() {}, waitForExit() { settling(); return range }, collected: {
      stderr: { readFrom: () => { throw new Error('never read provisional stderr') } },
    } }
  } }
}
const poll = (b, grant, operationId, fields = {}) => sessionWindow(b, grant, { op: 'session-first-frame', operationId, project: grant.project, end: 25, ...fields })
const firstFrameRenders = b => b.calls.filter(call => call.argv[4] === 'preview-window-first-frame')

test('provisional visual qualifies only by the exact separate probe and mutually exclusive owner consent', linuxWindow, async t => {
  const legacy = await bench({ windowCapability: { ...warmWindowCapability, first_frame: { version: 1 } },
    firstFrameCapability: { ...firstFrameCapability, wheel_sha256: 'f'.repeat(64) } }); t.after(() => legacy.host.dispose())
  assert.equal(legacy.opening.capabilities.capabilities.preview_window.first_frame, undefined)
  assert.equal((await legacy.control({ ...sessionConsent(legacy.grant), firstFrame: true })).status, 403)
  const b = await bench({ windowCapability: warmWindowCapability, firstFrameCapability }); t.after(() => b.host.dispose())
  assert.deepEqual(b.calls.slice(-2).map(call => call.argv), [[windowLauncher, '--first-frame-capabilities'], [windowLauncher, '--formal-archive-capabilities']])
  assert.deepEqual(b.opening.capabilities.capabilities.preview_window.first_frame, { version: 1 })
  assert.deepEqual(b.opening.previewSession.firstFrame, { version: 1 })
  const consent = sessionConsent(b.grant)
  for (const extra of [{ continuation: true }, { readWarmSvg: true }, { firstFrame: false }]) {
    assert.equal((await b.control({ ...consent, firstFrame: true, ...extra })).status, 403)
  }
  const { grant } = await (await b.control(consent)).json()
  assert.equal(grant.firstFrame, undefined)
  assert.equal((await sessionWindow(b, grant, { firstFrame: true })).status, 403)
  assert.equal((await poll(b, grant, randomUUID())).status, 403)
  assert.equal(firstFrameRenders(b).length, 0)
})

test('first frame is read-only, exact, and temporary while the original operation settles to MP4', linuxWindow, async t => {
  const bytes = rgbPng(), child = firstFrameChild(bytes)
  const b = await bench({ windowCapability: warmWindowCapability, firstFrameCapability, firstFrameHandle: child.handle })
  t.after(async () => { child.stdout.destroy(); child.finish({ exitCode: 1 }); child.reaped(true); await b.host.dispose() })
  const { grant } = await (await b.control({ ...sessionConsent(b.grant), firstFrame: true })).json()
  assert.equal(grant.firstFrame, true)
  const operationId = randomUUID()
  assert.equal((await poll(b, grant, operationId)).status, 204, 'poll before admission reserves nothing')
  const terminal = sessionWindow(b, grant, { firstFrame: true, operationId })
  const { first, final, nonce } = await child.started
  assert.match(nonce, /^[a-f0-9]{32}$/)
  assert.equal((await poll(b, grant, operationId)).status, 204)
  const f = pack('F', nonce, first)
  child.stdout.write(f.subarray(0, 3)); child.stdout.write(f.subarray(3, 20)); child.stdout.write(f.subarray(20))
  await turn()
  const before = b.calls.length
  assert.equal((await poll(b, grant, operationId, { end: 24 })).status, 204)
  assert.equal((await poll(b, grant, operationId, { frame: 2 })).status, 204)
  assert.equal((await poll(b, grant, operationId, { contentVersion: nextVersion })).status, 409)
  assert.equal((await poll(b, grant, operationId, { project: 'elsewhere/timeline.json' })).status, 403)
  assert.equal((await poll(b, grant, operationId, { ownerId: randomUUID() })).status, 403)
  const visual = await poll(b, grant, operationId)
  assert.equal(visual.status, 200)
  assert.equal(visual.headers.get('content-type'), 'image/png'); assert.equal(visual.headers.get('cache-control'), 'no-store')
  assert.equal(visual.headers.get('x-framivra-sha256'), first.sha256)
  assert.deepEqual(Buffer.from(await visual.arrayBuffer()), bytes)
  assert.equal(b.calls.length, before, 'no poll spawns even an inspect')
  const stat = b.fs.stat
  b.fs.stat = async function (target) {
    if (target.targetKey === '/workspace/timeline.json') { this.stat = stat; throw new Error('transient stat failure') }
    return stat.call(this, target)
  }
  assert.equal((await poll(b, grant, operationId)).status, 503, 'an unreadable poll takes the visual out of service only')
  assert.equal((await poll(b, grant, operationId)).status, 204)
  child.stdout.end(pack('W', nonce, final)); child.finish({ exitCode: 0, signal: null }); await child.settled
  assert.equal((await sessionWindow(b, grant, { firstFrame: true })).status, 409, 'valid W, EOF and process close do not release the range')
  child.reaped(true)
  const response = await terminal
  assert.equal(response.status, 200)
  assert.equal(response.headers.get('content-type'), 'video/mp4'); assert.equal(response.headers.get('x-framivra-input-epoch'), warmEpoch)
  assert.deepEqual(Buffer.from(await response.arrayBuffer()), windowBytes)
  assert.equal((await poll(b, grant, operationId)).status, 204)
  assert.equal(firstFrameRenders(b).length, 1)
})

test('wire, observed stale binding and revoke withdraw provisional bytes before managed reaping', linuxWindow, async t => {
  const bytes = rgbPng(), nonces = new Set()
  for (const mode of ['bad-png', 'wrong-nonce', 'wrong-window', 'trailing', 'exit', 'stale', 'revoke']) {
    const child = firstFrameChild(bytes)
    const b = await bench({ windowCapability: warmWindowCapability, firstFrameCapability, firstFrameHandle: child.handle })
    t.after(async () => { child.stdout.destroy(); child.finish({ exitCode: 1 }); child.reaped(true); await b.host.dispose() })
    const { grant } = await (await b.control({ ...sessionConsent(b.grant), firstFrame: true })).json()
    const operationId = randomUUID(), terminal = sessionWindow(b, grant, { firstFrame: true, operationId })
    const { first, final, nonce } = await child.started
    assert(!nonces.has(nonce)); nonces.add(nonce)
    let revoked
    if (mode === 'bad-png' || mode === 'wrong-nonce') {
      const changed = Buffer.from(bytes); changed[29] ^= 1 // Corrupt an IHDR CRC byte despite matching digest.
      child.stdout.end(pack('F', mode === 'wrong-nonce' ? '0'.repeat(32) : nonce, mode === 'bad-png' ? {
        ...first, sha256: createHash('sha256').update(changed).digest('hex'), base64: changed.toString('base64'),
      } : first))
    } else {
      child.stdout.write(pack('F', nonce, first)); await turn()
      assert.equal((await poll(b, grant, operationId)).status, 200)
      if (mode === 'wrong-window') child.stdout.end(pack('W', nonce, { ...final, end: 24 }))
      if (mode === 'trailing') child.stdout.end(Buffer.concat([pack('W', nonce, final), Buffer.from('!')]))
      if (mode === 'exit') child.stdout.end(pack('W', nonce, final))
      if (mode === 'stale') {
        b.fs.files.get('/workspace/timeline.json').version = 'saved-new'
        assert.equal((await poll(b, grant, operationId)).status, 409)
      }
      if (mode === 'revoke') revoked = b.control({ op: 'revoke-preview-session', previewGrant: grant.id, grant: b.grant, ownerId: grant.ownerId })
    }
    child.finish({ exitCode: mode === 'exit' ? 1 : 0, signal: null }); await child.settled
    assert.notEqual((await poll(b, grant, operationId)).status, 200, mode)
    assert.notEqual((await sessionWindow(b, grant, { firstFrame: true })).status, 200, 'unconfirmed owner cannot start a producer')
    child.reaped(true)
    assert.notEqual((await terminal).status, 200, mode)
    if (revoked) assert.equal((await revoked).status, 200)
  }
})

test('window launcher profile must be physically separate from ordinary vtx and every Session workspace', linuxWindow, async t => {
  for (const [executable, setup] of [
    ['/opt/trusted/vtx', undefined],
    ['/workspace/sub/window', fs => { fs.files.set('/workspace/sub/window', { type: 'file', version: 'in-project' }) }],
    ['/opt/window-link', fs => { fs.aliases.set('/opt/window-link', windowLauncher) }],
    [windowLauncher, (_fs, sessions) => { sessions.set('two', { id: 'two', cwd: '/opt/private-window/child' }) }],
  ]) {
    const b = await bench({ windowCapability, windowExecutable: executable, setup }); t.after(() => b.host.dispose())
    assert.equal(b.opening.capabilities.capabilities.preview_window, undefined)
    assert.equal((await b.window()).status, 422)
    assert.equal(b.calls.length, 2)
    assert.equal((await b.frame()).status, 200)
  }
  const b = await bench({ windowCapability }); t.after(() => b.host.dispose())
  assert.deepEqual(b.calls.at(-1).argv, [windowLauncher, '--formal-archive-capabilities'])
  const beforeReplacement = b.calls.length
  b.fs.files.get(windowLauncher).version = 'replaced'
  const refused = await b.window()
  assert.equal(refused.status, 409)
  assert.equal((await refused.json()).error, 'EXECUTABLE_CHANGED')
  assert.deepEqual(b.calls.slice(beforeReplacement), [], 'replacement cannot spawn any child')
  assert.equal((await b.frame()).status, 200, 'window replacement does not move a fixed-frame child')
})

test('window refuses a changed profile proof, malformed, oversized, or stale bytes and reports bounded kernel rejection', linuxWindow, async t => {
  let saved = version, patch
  const b = await bench({ windowCapability, saved: () => saved, windowResult: value => patch(value) })
  t.after(() => b.host.dispose())
  for (const changed of [{ launcher: undefined }, { wheel_sha256: 'e'.repeat(64) }, { runtime_projection_sha256: 'e'.repeat(64) }]) {
    patch = value => ({ ...value, ...changed })
    const response = await b.window()
    assert.equal(response.status, 409)
    assert.equal((await response.json()).error, 'WINDOW_PROFILE_CHANGED')
  }
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
  for (const boundary of ['expiry', 'cwd', 'Session replacement', 'Session deletion', 'launcher config change']) {
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
      if (boundary === 'launcher config change') b.config.previewWindowExecutable = ''
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
  assert.equal(b.calls.length, 4)
})

const archiveCapability = { schema: 'framivra.preview-formal-archive.capabilities/v1', available: true, ...qualification }
const archives = b => b.calls.filter(call => call.argv[4] === 'preview-formal-archive')
function archiveRequest(b, overrides = {}) {
  const { op = 'formal-archive-read', ...fields } = overrides
  return b.host.window(new Request('http://unit/api/framivra/window', { method: 'POST', body: JSON.stringify({ op, sessionId: 'one',
    grant: b.grant, operationId: randomUUID(), ownerId: b.opening.previewSession.ownerId,
    ...(op === 'formal-archive-read' ? { frame: 33, contentVersion: version } : {}), ...fields }) }))
}

test('formal archive is a distinct owner-only read, with exact fixed-wheel arguments, two saved reads and no live receipt', linuxWindow, async t => {
  const unqualified = await bench({ windowCapability: { ...windowCapability, formal_archive: { version: 1 } },
    archiveCapability: { ...archiveCapability, wheel_sha256: 'f'.repeat(64) } })
  t.after(() => unqualified.host.dispose())
  assert.equal(unqualified.opening.previewSession.formalArchive, undefined)
  assert.equal((await archiveRequest(unqualified)).status, 403)
  assert.equal(archives(unqualified).length, 0)

  const b = await bench({ windowCapability, archiveCapability }); t.after(() => b.host.dispose())
  assert.deepEqual(b.opening.previewSession.formalArchive, { version: 1 })
  assert.deepEqual(b.opening.capabilities.capabilities.preview_window.formal_archive, { version: 1 })
  assert.deepEqual(b.calls.at(-1).argv, [windowLauncher, '--formal-archive-capabilities'])
  for (const field of [{ ownerId: 'wrong' }, { path: '/tmp/media.mp4' }, { prepareInputs: true }, { renderConsent: 'cache/preview-only' }]) {
    assert(!(await archiveRequest(b, field)).ok)
  }
  const { grant: automatic } = await (await b.control(sessionConsent(b.grant))).json()
  assert.equal((await archiveRequest(b)).status, 409)
  assert.equal(archives(b).length, 0)
  assert.equal((await b.control({ op: 'revoke-preview-session', grant: b.grant, previewGrant: automatic.id, ownerId: automatic.ownerId })).status, 200)
  const operationId = randomUUID(), before = b.calls.length
  const response = await archiveRequest(b, { operationId })
  assert.equal(response.status, 200, await response.clone().text())
  const args = archives(b)[0].argv
  assert.match(args.at(-1), /^--nonce=[a-f0-9]{32}$/)
  assert.deepEqual(b.calls.slice(before).map(call => call.argv), [
    ['/opt/trusted/vtx', '-t', '/workspace/timeline.json', 'inspect'],
    [windowLauncher, '-t', '/workspace/timeline.json', 'timeline', 'preview-formal-archive', '--frame', '33', `--expected-content-version=${version}`, '--read-approved-archive', args.at(-1)],
    ['/opt/trusted/vtx', '-t', '/workspace/timeline.json', 'inspect'],
  ])
  assert.deepEqual(['media-kind', 'operation-id', 'version', 'start', 'end', 'fps', 'sha256', 'input-epoch'].map(name => response.headers.get(`X-Framivra-${name}`)),
    ['approved-formal-archive/v1', operationId, version, '33', '36', '24000/1001', sha256, null])
  assert.deepEqual(Buffer.from(await response.arrayBuffer()), windowBytes)
  assert.equal((await archiveRequest(b, { operationId })).status, 409)
  assert.equal(b.fs.reads.length, 0)
})

test('formal archive rejects altered receipts and refuses bytes if the saved version changes during read', linuxWindow, async t => {
  for (const change of [{ nonce: '0'.repeat(32) }, { end: 37 }, { fps: { num: 23999, den: 1001 } },
    { sha256: '0'.repeat(64) }, { byte_length: 5 * 1024 * 1024 + 1 }, { media_path: '/tmp/other.mp4' }]) {
    const b = await bench({ windowCapability, archiveCapability, archiveResult: value => ({ ...value, ...change }) })
    t.after(() => b.host.dispose())
    const denied = await archiveRequest(b)
    assert.equal(denied.status, 502, JSON.stringify(change))
    assert.notEqual(denied.headers.get('content-type'), 'video/mp4')
    assert.equal(archives(b).length, 1)
    assert.equal(b.fs.reads.length, 0)
  }
  let saved = version
  const stale = await bench({ windowCapability, archiveCapability, saved: () => saved,
    archiveResult(value) { saved = nextVersion; return value } })
  t.after(() => stale.host.dispose())
  assert.equal((await archiveRequest(stale)).status, 409)
})

test('formal archive accepts only the settled fixed-wheel rejection for the current request', linuxWindow, async t => {
  let saved = version, variant = {}, reaped = true
  const b = await bench({ windowCapability, archiveCapability, saved: () => saved, archiveHandle(spec, success) {
    const { code = 'archive.selector_mismatch', exitCode = 1, signal = null, alter = wire => wire } = variant
    const wire = JSON.stringify({ schema: 'framivra.preview-formal-archive/v1', ok: false, nonce: spec.argv.at(-1).slice('--nonce='.length), code }) + '\n'
    return { stdout: Readable.from([Buffer.from(alter(wire, success))]), done: Promise.resolve({ exitCode, signal }),
      terminate() {}, waitForExit: async () => reaped, collected: { stderr: { readFrom: () => ({ text: 'private stderr', lossy: false }) } } }
  } })
  t.after(async () => { reaped = true; await b.host.dispose() })
  // The user has refreshed to the new saved version; the archive still pins the old version.
  saved = nextVersion
  assert.equal((await b.control({ op: 'inspect', grant: b.grant })).status, 200)
  for (const [options, status, code] of [
    [{}, 409, 'ARCHIVE_SELECTOR_MISMATCH'],
    [{ code: 'archive.inputs_mismatch' }, 409, 'ARCHIVE_INPUTS_MISMATCH'],
    [{ code: 'archive.unavailable' }, 409, 'ARCHIVE_UNAVAILABLE'],
    [{ code: 'archive.unknown' }, 502, 'ARCHIVE_PROTOCOL'],
    [{ alter: wire => wire.replace(/("nonce":")([a-f0-9])/, (_, prefix, first) => prefix + (first === '0' ? '1' : '0')) }, 502, 'ARCHIVE_PROTOCOL'],
    [{ alter: wire => '{"base64":"private media",' + wire.slice(1) }, 502, 'ARCHIVE_PROTOCOL'],
    [{ alter: wire => '{"ok":false,' + wire.slice(1) }, 502, 'ARCHIVE_PROTOCOL'],
    [{ alter: wire => wire.slice(0, -1) }, 502, 'ARCHIVE_PROTOCOL'],
    [{ alter: wire => '\uFEFF' + wire }, 502, 'ARCHIVE_PROTOCOL'],
    [{ alter: (_wire, success) => success }, 502, 'ARCHIVE_PROTOCOL'],
    [{ exitCode: 0 }, 502, 'ARCHIVE_PROTOCOL'],
    [{ exitCode: 2 }, 502, 'CLI_UNSUPPORTED'],
    [{ signal: 'SIGKILL' }, 502, 'CLI_FAILED'],
    [{ reaped: false }, 502, 'PROCESS_CLEANUP_FAILED'],
  ]) {
    variant = options
    reaped = options.reaped ?? true
    const operationId = randomUUID()
    const response = await archiveRequest(b, { operationId, contentVersion: saved })
    assert.equal(response.status, status, code)
    assert.notEqual(response.headers.get('content-type'), 'video/mp4')
    assert.equal(response.headers.get('x-framivra-media-kind'), null)
    const body = await response.json()
    assert.deepEqual(Object.keys(body).sort(), ['error', 'message', 'ok'])
    assert.equal(body.ok, false)
    assert.equal(body.error, code)
    const nonce = archives(b).at(-1).argv.at(-1).slice('--nonce='.length)
    assert.doesNotMatch(body.message, new RegExp(`private stderr|private media|${nonce}`))
    assert.equal(archives(b).at(-1).argv[7], `--expected-content-version=${saved}`)
    if (!reaped) {
      const busy = await b.frame({ contentVersion: saved })
      assert.equal(busy.status, 409, 'failed reaping retains the owner')
      assert.equal((await busy.json()).error, 'ARCHIVE_BUSY')
      reaped = true
      assert.equal((await archiveRequest(b, { op: 'formal-archive-cancel', operationId })).status, 200)
    }
  }
})

test('archive cleanup uses the exact original owner and waits for managed-range settlement', linuxWindow, async t => {
  let start, release
  const started = new Promise(resolve => { start = resolve }), range = new Promise(resolve => { release = resolve })
  const b = await bench({ windowCapability, archiveCapability, archiveHandle(_spec, text) {
    start()
    return { stdout: Readable.from([Buffer.from(text)]), done: Promise.resolve({ exitCode: 0, signal: null }),
      terminate() {}, waitForExit: () => range, collected: {} }
  } })
  t.after(async () => { release(true); await b.host.dispose() })
  const operationId = randomUUID(), reading = archiveRequest(b, { operationId })
  await started
  assert.equal((await b.frame()).status, 409)
  assert.equal((await b.control(sessionConsent(b.grant))).status, 409)
  const cancel = { op: 'formal-archive-cancel', operationId }
  assert.equal((await archiveRequest(b, { ...cancel, ownerId: 'wrong' })).status, 403)
  let settled = false
  const cancelling = archiveRequest(b, cancel).then(value => { settled = true; return value })
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(settled, false)
  release(true)
  assert.deepEqual(await (await cancelling).json(), { ok: true, reaped: true, operationId, ownerId: b.opening.previewSession.ownerId, openGrant: b.grant })
  assert(!(await reading).ok)
  assert.equal((await archiveRequest(b, { operationId })).status, 409)
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
