// Host hardening regressions: cancel/reap ownership scope, monitor provider
// failures, buffered request bounds, optional media skips. In-process Host
// boundary fixtures only — no web server, no installed vtx.
import test from 'node:test'
import assert from 'node:assert/strict'
import { Readable, Writable } from 'node:stream'
import { createPreviewHost } from '../src/host.mjs'
import { MemoryFs } from './helpers.mjs'
import * as bounded from '../src/bounded.mjs'

const version = `sha256:${'a'.repeat(64)}`
const wire = value => JSON.stringify(value) + '\n'
const readEnvelope = operation => ({
  schema: 'framivra.timeline.read/v1', operation, ok: true, content_version: version, diagnostics: [],
  document: {}, coverage: { complete: true, scope: 'unit fixture', unexpanded: [] },
  ...(operation === 'query' ? { tracks: [], blocks: [] } : {}),
  ...(operation === 'capabilities'
    ? { content_version: null, capabilities: { operations: ['inspect', 'query', 'capabilities'], frame: { schema: 'framivra.timeline.frame/v1', explicit_render_required: true, inline_png: { flag: '--include-png', encoding: 'base64', wire_limit_bytes: 8 * 1024 * 1024 } } } }
    : {}),
})
const frameEnvelope = () => ({ schema: 'framivra.timeline.frame/v1', operation: 'frame', ok: true, content_version: version, diagnostics: [], image: { path: '/never/read.png' } })
const storeEnvelope = operation => ({ schema: 'framivra.timeline.store/v1', operation: operation.split(':')[1], ok: true, ...(operation === 'store:capabilities' ? { platform_support: { darwin: true }, operations: ['edit'], edit: { operations: ['edit'] }, request_transport: { stdin: { flag: '--request-stdin', max_bytes: 8388608 } } } : {}) })

/** Deferred manual child handles so tests control exit/managed-range timing exactly.
 *  waitForExit consumes one outcome per cleanup attempt (default true), modeling
 *  a provider whose managed-range confirmation can fail and later recover. */
function makeBench({ storeCapabilities = storeEnvelope('store:capabilities'), reply = (_op, value) => value, config = { vtxExecutable: '/opt/trusted/vtx' } } = {}) {
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
    const responseText = inputSpec => wire(reply(op, op === 'store:capabilities' ? storeCapabilities : op === 'frame' ? frameEnvelope() : op.startsWith('store:') ? storeEnvelope(op) : readEnvelope(op), inputSpec))
    let text = spec.stdio.stdin === 'pipe' ? '' : responseText(spec)
    let doneResolve, terminated = 0
    const rangeOutcomes = []
    const done = new Promise(resolve => { doneResolve = resolve })
    const handle = {
      op, spec, terminated: () => terminated,
      stdout: Readable.from([Buffer.from(text)]),
      done,
      terminate() { terminated++; doneResolve({ exitCode: null, signal: 'SIGTERM' }) },
      async waitForExit() { await done; return rangeOutcomes.shift() ?? true },
      settle(exitCode = op === 'store:edit-view' && JSON.parse(text).ok === false ? 1 : 0) { doneResolve({ exitCode }) },
      failRange(times = 1) { for (let i = 0; i < times; i++) rangeOutcomes.push(false) },
      collected: { stdout: { readFrom: () => ({ text, lossy: false }) }, stderr: { readFrom: () => ({ text: '', lossy: false }) } },
    }
    if (spec.stdio.stdin === 'pipe') {
      const chunks = []
      handle.input = () => Buffer.concat(chunks).toString('utf8')
      handle.stdin = new Writable({
        write(chunk, _encoding, callback) { chunks.push(Buffer.from(chunk)); callback() },
        final(callback) {
          try {
            text = responseText({ ...spec, stdio: { ...spec.stdio, stdin: { data: handle.input() } } })
            if (!bench.manual.has(op)) handle.settle()
            callback()
          } catch (error) { callback(error) }
        },
      })
    }
    bench.spawned.push(handle)
    for (const waiter of bench.waiters.filter(w => w.op === op)) waiter.resolve(handle)
    if (!bench.manual.has(op) && spec.stdio.stdin !== 'pipe') handle.settle()
    return handle
  }
  const subprocess = { resolveExecutable: async () => '/opt/trusted/vtx', spawn }
  const host = createPreviewHost({ fs, sessions, workingDirectory, subprocess }, config)
  const control = (data, sessionId = 'one') => host.control(new Request('http://unit/api/framivra/control', { method: 'POST', body: typeof data === 'string' ? data : JSON.stringify({ sessionId, ...data }) }))
  const frame = (data, sessionId = 'one') => host.frame(new Request('http://unit/api/framivra/frame', { method: 'POST', body: JSON.stringify({ op: 'render', sessionId, frame: 0, contentVersion: version, renderConsent: 'cache/preview-only', operationId: crypto.randomUUID(), ...data }) }))
  const open = async sessionId => {
    const response = await control({ op: 'open', timeline: 'timeline.json' }, sessionId)
    assert.equal(response.status, 200, await response.clone().text())
    return (await response.json()).grant
  }
  return { host, fs, sessions, cwds, workingDirectory, config, ...bench, control, frame, open }
}

test('write platform capability: Darwin requires an explicit new-core certificate; old core stays read-only', async t => {
  const platformDescriptor = Object.getOwnPropertyDescriptor(process, 'platform')
  const supported = storeEnvelope('store:capabilities')
  const oldCore = { ...supported }; delete oldCore.platform_support
  const cases = [
    ['darwin', 'new core', supported, true],
    ['darwin', 'old core (no platform_support)', oldCore, false],
    ['darwin', 'empty platform_support', { ...supported, platform_support: {} }, false],
    ['darwin', 'explicit unsupported', { ...supported, platform_support: { darwin: false } }, false],
    ['darwin', 'truthy string is not a certificate', { ...supported, platform_support: { darwin: 'true' } }, false],
    ['darwin', 'new core still needs stdin transport', { ...supported, request_transport: undefined }, false],
    ['darwin', 'new core still needs semantic edit', { ...supported, edit: { operations: [] } }, false],
    ['linux', 'old core remains supported', oldCore, true],
    ['linux', 'stdin refusal remains enforced', { ...oldCore, request_transport: undefined }, false],
    ['win32', 'certificate cannot override unsupported host', supported, false],
  ]
  try {
    for (const [platform, label, storeCapabilities, allowed] of cases) await t.test(`${platform}: ${label}`, async () => {
      Object.defineProperty(process, 'platform', { ...platformDescriptor, value: platform })
      const b = makeBench({ storeCapabilities })
      try {
        const grant = await b.open('one')
        const authorized = await b.control({ op: 'authorize-write', grant })
        const result = await authorized.json()
        assert.equal(authorized.status, allowed ? 200 : 422, JSON.stringify(result))
        if (allowed) {
          assert.equal(typeof result.writeGrant, 'string')
          assert(result.writeGrant.length > 0)
          const status = await b.control({ op: 'status', grant, writeGrant: result.writeGrant })
          assert.equal(status.status, 200, await status.clone().text())
        } else {
          assert.equal(result.error, 'WRITE_UNSUPPORTED')
          assert.equal(result.writeGrant, undefined, 'refusal must never mint a write token')
          const denied = await b.control({ op: 'status', grant, writeGrant: 'not-minted' })
          assert.equal(denied.status, 403)
          assert.equal((await denied.json()).error, 'WRITE_DENIED')
          assert(b.spawned.filter(child => child.op.startsWith('store:')).every(child => child.op === 'store:capabilities'), 'refusal must not run store mutations')
        }
        assert.equal((await b.control({ op: 'inspect', grant })).status, 200, 'write support must not disable ordinary reads')
        assert.equal((await b.control({ op: 'query', grant })).status, 200)
      } finally { await b.host.dispose() }
    })
  } finally { Object.defineProperty(process, 'platform', platformDescriptor) }
})

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

const editedVersion = `sha256:${'b'.repeat(64)}`
const fastCapabilities = () => ({ ...storeEnvelope('store:capabilities'), edit_view: { command: 'edit-view', read_schema: 'framivra.timeline.read/v1', wire_limit_bytes: 8388608 } })
function fastReply(spec) {
  const request = JSON.parse(spec.stdio.stdin.data)
  return { schema: 'framivra.timeline.store/v1', operation: 'edit', ok: true, op_id: request.op_id,
    base_revision: request.base_revision, revision: editedVersion, current_revision: editedVersion, replayed: false,
    view: { snapshot: { ...readEnvelope('inspect'), content_version: editedVersion, document: { _vtx: { head: request.op_id } } },
      result: { ...readEnvelope('query'), content_version: editedVersion } } }
}
async function authorizeFast(b) {
  const grant = await b.open('one')
  const authorized = await (await b.control({ op: 'authorize-write', grant })).json()
  return { op: 'edit-view', grant, writeGrant: authorized.writeGrant, op_id: crypto.randomUUID(), base_revision: version, operations: [{ op: 'fixture.edit' }], authorized }
}
const fastRequest = ({ authorized: _authorized, ...request }) => request
const warmCapabilities = () => { const value = fastCapabilities(); value.edit_view.preload_stdin = true; return value }
const nextTurn = () => new Promise(resolve => setImmediate(resolve))
async function idleChild(b) {
  await nextTurn() // Allow detached identity checks, not an arbitrary timer sleep.
  const child = b.spawned.filter(child => child.op === 'store:edit-view' && child.spec.stdio.stdin === 'pipe').at(-1)
  assert(child, 'an advertised preload should spawn a bounded EOF waiter')
  assert.equal(child.input(), '', 'idle means zero request bytes, not a partial JSON prefix')
  assert.equal(child.stdin.writableEnded, false)
  return child
}

test('edit-view: exact capability/write grant, bounded official stdin, same validated receipt and advanced base', async () => {
  const b = makeBench({ storeCapabilities: fastCapabilities(), reply: (op, value, spec) => op === 'store:edit-view' ? fastReply(spec) : value })
  try {
    const setup = await authorizeFast(b)
    assert.equal(setup.authorized.editView, true)
    const request = fastRequest(setup)
    assert.equal(b.spawned.some(child => child.spec.stdio.stdin === 'pipe'), false, 'older fast advertisement must not preload')
    const denied = await b.control({ ...request, writeGrant: 'wrong' })
    assert.equal(denied.status, 403)
    for (const invalid of [{ op_id: '-'.repeat(36) }, { extra: true }, { operations: [] }]) assert.equal((await b.control({ ...request, ...invalid })).status, 400)
    const result = await (await b.control(request)).json()
    const child = b.spawned.find(child => child.op === 'store:edit-view')
    assert.deepEqual(child.spec.argv.slice(3), ['store', 'edit-view', '--request-stdin'])
    assert.deepEqual(JSON.parse(child.spec.stdio.stdin.data), { operation: 'edit', op_id: request.op_id, base_revision: version, operations: request.operations })
    assert.equal(child.spec.stdio.stdout.maxBytes, 8388608)
    assert.deepEqual(result, fastReply(child.spec))
    assert.equal(result.submitted_request, undefined)
    assert.equal((await b.control({ ...request, op_id: crypto.randomUUID() })).status, 409, 'old base must not be reusable')
    assert.equal((await b.control({ ...request, op_id: crypto.randomUUID(), base_revision: editedVersion })).status, 200)
    assert.equal(Object.hasOwn(b.host.agent, 'edit-view'), false)
  } finally { await b.host.dispose() }
})

test('edit-view: old or inexact capability keeps ordinary plan flow and never spawns fast edit', async t => {
  for (const edit_view of [undefined, { ...fastCapabilities().edit_view, wire_limit_bytes: 1 }, { ...fastCapabilities().edit_view, command: 'other' }]) await t.test(JSON.stringify(edit_view) ?? 'older core', async () => {
    const b = makeBench({ storeCapabilities: { ...fastCapabilities(), edit_view } })
    try {
      const setup = await authorizeFast(b)
      assert.equal(setup.authorized.editView, undefined)
      assert.equal((await b.control(fastRequest(setup))).status, 422)
      assert.equal(b.spawned.some(child => child.op === 'store:edit-view'), false)
      assert.equal((await b.control({ ...fastRequest(setup), op: 'plan' })).status, 200)
    } finally { await b.host.dispose() }
  })
})

test('edit-view: applied view/fence failures remain distinct from opaque or explicitly unknown outcomes', async t => {
  for (const scenario of ['view-revision', 'view-head', 'view-shape', 'replay', 'executable', 'config', 'receipt-id', 'explicit-unknown']) await t.test(scenario, async () => {
    let b
    b = makeBench({ storeCapabilities: fastCapabilities(), reply: (op, value, spec) => {
      if (op !== 'store:edit-view') return value
      const result = fastReply(spec)
      if (scenario === 'view-revision') result.view.result.content_version = version
      if (scenario === 'view-head') result.view.snapshot.document._vtx.head = crypto.randomUUID()
      if (scenario === 'view-shape') result.view.result.blocks = null
      if (scenario === 'replay') { result.replayed = true; delete result.view }
      if (scenario === 'executable') b.fs.files.get('/opt/trusted/vtx').version = 'replaced'
      if (scenario === 'config') b.config.trustedPath = '/changed/bin' // Post-receipt grant fence also aborts the write signal.
      if (scenario === 'receipt-id') result.op_id = crypto.randomUUID()
      if (scenario === 'explicit-unknown') return { schema: result.schema, ok: false, code: 'outcome_unknown', outcome_unknown: true }
      return result
    } })
    try {
      const request = fastRequest(await authorizeFast(b))
      const result = await (await b.control(request)).json()
      const unknown = ['receipt-id', 'explicit-unknown'].includes(scenario)
      assert.equal(result.code, unknown ? 'outcome_unknown' : 'applied_view_unavailable')
      assert.equal(result.op_id, request.op_id)
      assert.equal(result.view, undefined)
      assert.equal(result.submitted_request, undefined)
      if (!unknown) assert.equal(result.applied, true)
      if (scenario === 'executable') assert.equal(result.terminal_code, 'EXECUTABLE_CHANGED')
      if (scenario === 'config') assert.equal(result.terminal_code, 'CONFIG_CHANGED', 'known applied receipt survives a later aborting config fence')
      const refused = await b.control({ ...request, op_id: crypto.randomUUID() })
      assert.equal(refused.status, 409)
      assert.equal((await refused.json()).error, scenario === 'config' ? 'CONFIG_CHANGED' : unknown ? 'OUTCOME_UNKNOWN' : 'STALE_BASE')
      if (scenario === 'view-revision') for (const op of ['plan', 'commit', 'adopt', 'undo']) {
        const legacy = await b.control({ ...request, op })
        assert.equal((await legacy.json()).error, 'STALE_BASE', 'an applied stale view must not reopen editing through the older route')
      }
      assert.equal(b.spawned.filter(child => child.op === 'store:edit-view').length, 1, 'never retry')
    } finally { await b.host.dispose() }
  })
})

test('edit-view: malformed failure receipt is unknown; valid input refusal may omit op_id', async t => {
  for (const failure of [{}, { code: '' }, { code: 1 }, { code: 'invalid', op_id: crypto.randomUUID() }, { code: 'invalid' }]) await t.test(JSON.stringify(failure), async () => {
    const b = makeBench({ storeCapabilities: fastCapabilities(), reply: (op, value) => op === 'store:edit-view' ? { schema: 'framivra.timeline.store/v1', ok: false, ...failure } : value })
    try {
      const request = fastRequest(await authorizeFast(b))
      const result = await (await b.control(request)).json()
      assert.equal(result.code, failure.code === 'invalid' && failure.op_id === undefined ? 'invalid' : 'outcome_unknown')
      if (result.code === 'outcome_unknown') assert.equal(result.op_id, request.op_id)
    } finally { await b.host.dispose() }
  })
})

test('edit-view preload: one empty EOF waiter, frame coexistence, one-shot exact release and verified re-prime', async () => {
  const b = makeBench({ storeCapabilities: warmCapabilities(), reply: (op, value, spec) => op === 'store:edit-view' ? fastReply(spec) : value })
  try {
    const request = fastRequest(await authorizeFast(b))
    const idle = await idleChild(b)
    assert.equal(idle.spec.stdio.stdout.maxBytes, 8388608)
    assert.equal((await b.control({ ...request, writeGrant: 'wrong' })).status, 403)
    assert.equal((await b.control({ ...request, operations: [] })).status, 400)
    assert.equal(idle.input(), '')
    const rendering = b.frame({ grant: request.grant })
    const frame = await b.onSpawned('frame')
    frame.settle()
    assert.equal((await rendering).status, 200, 'a harmless EOF waiter must not block either frame active gate')
    assert.equal(idle.terminated(), 0)
    const result = await (await b.control(request)).json()
    assert.equal(result.ok, true)
    assert.deepEqual(JSON.parse(idle.input()), { operation: 'edit', op_id: request.op_id, base_revision: version, operations: request.operations })
    assert.equal(idle.stdin.writableEnded, true)
    assert(idle.terminated() > 0, 'taken child must complete existing range cleanup')
    const replacement = await idleChild(b)
    assert.notEqual(replacement, idle)
    assert.equal(b.spawned.filter(child => child.op === 'store:edit-view').length, 2, 'only taken child plus the next one-shot idle child')
    assert.equal((await b.control({ op: 'cancel-write', grant: request.grant, writeGrant: request.writeGrant })).status, 200)
    assert(replacement.terminated() > 0)
    assert.equal(replacement.input(), '')
  } finally { await b.host.dispose() }
})

test('edit-view preload: authorization does not await prime, whose own executable fence precedes spawn', async () => {
  const b = makeBench({ storeCapabilities: warmCapabilities() })
  let resume
  const paused = new Promise(resolve => { resume = resolve })
  let reached
  const atFence = new Promise(resolve => { reached = resolve })
  try {
    const grant = await b.open('one')
    const stat = b.fs.stat.bind(b.fs)
    b.fs.stat = async (target, signal) => {
      if (target.targetKey === '/opt/trusted/vtx') { reached(); await paused }
      return stat(target, signal)
    }
    const response = await b.control({ op: 'authorize-write', grant })
    assert.equal(response.status, 200, 'HTTP authorization must finish while prime identity validation is still pending')
    await atFence
    b.fs.files.get('/opt/trusted/vtx').version = 'changed-during-prime'
    resume()
    await nextTurn()
    assert.equal(b.spawned.some(child => child.spec.stdio.stdin === 'pipe'), false, 'a failed idle-spawn identity fence must not execute a child')
  } finally { resume(); await b.host.dispose() }
})

test('edit-view preload: full release fences reject drift without writing any idle bytes', async t => {
  for (const drift of ['executable', 'timeline', 'project', 'cwd', 'session', 'config']) await t.test(drift, async () => {
    const b = makeBench({ storeCapabilities: warmCapabilities(), reply: (op, value, spec) => op === 'store:edit-view' ? fastReply(spec) : value })
    try {
      const request = fastRequest(await authorizeFast(b))
      const idle = await idleChild(b)
      if (drift === 'executable') b.fs.files.get('/opt/trusted/vtx').version = 'replaced'
      if (drift === 'timeline') b.fs.aliases.set('/workspace/timeline.json', '/workspace/other.json')
      if (drift === 'project') b.fs.aliases.set('/workspace', '/other-workspace')
      if (drift === 'cwd') b.cwds.one = '/other-workspace'
      if (drift === 'session') b.sessions.set('one', { id: 'one' })
      if (drift === 'config') b.config.trustedPath = '/changed/bin'
      const response = await b.control(request)
      const result = await response.json()
      assert(response.status !== 200 || result.ok === false)
      assert.equal(idle.input(), '', 'all identity fences precede request-byte release')
      assert.equal(b.spawned.filter(child => child.op === 'store:edit-view').length, 1, 'no fallback/replacement transaction')
    } finally { await b.host.dispose() }
  })
})

test('edit-view preload: one global slot stays scoped to its original write owner', async () => {
  const b = makeBench({ storeCapabilities: warmCapabilities(), reply: (op, value, spec) => op === 'store:edit-view' ? fastReply(spec) : value })
  try {
    const request = fastRequest(await authorizeFast(b))
    const idle = await idleChild(b)
    const otherGrant = await b.open('two')
    const other = await (await b.control({ op: 'authorize-write', grant: otherGrant }, 'two')).json()
    await nextTurn()
    assert.equal(b.spawned.filter(child => child.spec.stdio.stdin === 'pipe').length, 1)
    const result = await (await b.control({ ...request, grant: otherGrant, writeGrant: other.writeGrant, op_id: crypto.randomUUID() }, 'two')).json()
    assert.equal(result.ok, true)
    assert.equal(idle.input(), '', 'another owner must use its normal cold request, never the idle slot')
    assert.equal((await b.control({ op: 'revoke-write', grant: otherGrant }, 'two')).status, 200)
    assert.equal(idle.terminated(), 0, 'unrelated revoke cannot reap the original idle owner')
    const renewed = await (await b.control({ op: 'authorize-write', grant: request.grant })).json()
    const replacement = await idleChild(b)
    assert.notEqual(replacement, idle, 'reauthorization must never reuse the previous write grant worker')
    assert.notEqual(renewed.writeGrant, request.writeGrant)
    assert(idle.terminated() > 0)
    assert.equal((await b.control(request)).status, 403)
    assert.equal(replacement.input(), '')
    assert.equal((await b.control({ op: 'revoke-write', grant: request.grant })).status, 200)
    assert(replacement.terminated() > 0)
  } finally { await b.host.dispose() }
})

test('edit-view preload: a taken worker becomes an active transaction before bytes are dispatched', async () => {
  const b = makeBench({ storeCapabilities: warmCapabilities(), reply: (op, value, spec) => op === 'store:edit-view' ? fastReply(spec) : value })
  try {
    const request = fastRequest(await authorizeFast(b))
    const idle = await idleChild(b)
    const otherGrant = await b.open('two')
    const other = await (await b.control({ op: 'authorize-write', grant: otherGrant }, 'two')).json()
    b.manual.add('store:edit-view')
    const editing = b.control(request)
    await nextTurn()
    assert(idle.input().length > 0, 'the original idle worker must have been taken')
    const refused = await b.control({ op: 'plan', grant: otherGrant, writeGrant: other.writeGrant, base_revision: version, operations: request.operations }, 'two')
    assert.equal(refused.status, 409)
    assert.equal((await refused.json()).error, 'FRAME_BUSY', 'another grant must observe the taken worker as semanticBusy, not harmless idle')
    assert.equal(b.spawned.some(child => child.op === 'store:plan'), false)
    idle.settle()
    assert.equal((await (await editing).json()).ok, true)
  } finally { await b.host.dispose() }
})

test('edit-view preload: idle cleanup failure blocks frame admission and remains retryable', async () => {
  const b = makeBench({ storeCapabilities: warmCapabilities() })
  try {
    const request = fastRequest(await authorizeFast(b))
    const idle = await idleChild(b)
    idle.failRange(10)
    assert.equal((await b.control({ op: 'revoke-write', grant: request.grant })).status, 502)
    assert.equal((await b.frame({ grant: request.grant })).status, 409, 'failed idle cleanup is NOT harmless idle exclusion')
    idle.waitForExit = async () => true
    assert.equal((await b.control({ op: 'close', grant: request.grant })).status, 200, 'retained idle owner can be explicitly reaped')
    assert.equal(idle.input(), '')
  } finally { await b.host.dispose() }
})

test('edit-view preload: four-second expiry and dispose reap zero-byte workers', async () => {
  const realSetTimeout = globalThis.setTimeout
  let expire
  globalThis.setTimeout = (fn, ms, ...args) => {
    if (ms === 4000) expire = fn
    return realSetTimeout(fn, ms, ...args)
  }
  const b = makeBench({ storeCapabilities: warmCapabilities() })
  try {
    await authorizeFast(b)
    const idle = await idleChild(b)
    assert.equal(typeof expire, 'function')
    expire()
    await nextTurn()
    assert(idle.terminated() > 0)
    assert.equal(idle.input(), '')
    const grant = await b.open('one')
    await b.control({ op: 'authorize-write', grant })
    const next = await idleChild(b)
    assert.notEqual(next, idle)
    await b.host.dispose()
    assert(next.terminated() > 0)
    assert.equal(next.input(), '')
  } finally { globalThis.setTimeout = realSetTimeout; await b.host.dispose() }
})

test('edit-view preload: dispatch failure is unknown and never falls back after taking', async t => {
  for (const asynchronous of [false, true]) await t.test(asynchronous ? 'Writable error' : 'throwing end', async () => {
    const b = makeBench({ storeCapabilities: warmCapabilities() })
    try {
      const request = fastRequest(await authorizeFast(b))
      const idle = await idleChild(b)
      let ends = 0
      idle.stdin.end = () => {
        ends++
        if (asynchronous) { queueMicrotask(() => idle.stdin.emit('error', new Error('pipe failed'))); return idle.stdin }
        throw new Error('ambiguous dispatch')
      }
      const result = await (await b.control(request)).json()
      assert.equal(result.code, 'outcome_unknown')
      assert.equal(result.op_id, request.op_id)
      assert.equal(ends, 1)
      assert(idle.terminated() > 0)
      assert.equal(b.spawned.filter(child => child.op === 'store:edit-view').length, 1)
    } finally { await b.host.dispose() }
  })
})

test('edit-view: interruption preserves op_id/unknown and late inspect cannot downgrade published grant', async t => {
  for (const interrupted of [false, true]) await t.test(interrupted ? 'cancel' : 'late inspect', async () => {
    const b = makeBench({ storeCapabilities: fastCapabilities(), reply: (op, value, spec) => op === 'store:edit-view' ? fastReply(spec) : value })
    try {
      const request = fastRequest(await authorizeFast(b))
      b.manual.add('inspect')
      const inspecting = b.control({ op: 'inspect', grant: request.grant })
      const oldRead = await b.onSpawned('inspect')
      b.manual.add('store:edit-view')
      const editing = b.control(request)
      const child = await b.onSpawned('store:edit-view')
      assert.equal((await b.control({ op: 'inspect', grant: request.grant })).status, 409)
      if (interrupted) await b.control({ op: 'cancel-write', grant: request.grant, writeGrant: request.writeGrant })
      else child.settle()
      const result = await (await editing).json()
      assert.equal(result.op_id, request.op_id)
      assert.equal(result.code, interrupted ? 'outcome_unknown' : undefined)
      oldRead.settle()
      assert.equal((await inspecting).status, interrupted ? 502 : 409)
      const next = await b.control({ ...request, op_id: crypto.randomUUID() })
      assert.equal(next.status, 409)
      assert.equal((await next.json()).error, interrupted ? 'OUTCOME_UNKNOWN' : 'STALE_BASE')
    } finally { await b.host.dispose() }
  })
})
