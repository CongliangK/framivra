// Host boundary fixtures, not real provider or filesystem deletion evidence.
import test from 'node:test'
import assert from 'node:assert/strict'
import { createPreviewHost } from '../src/host.mjs'
import { TimelineReader } from '../src/protocol.mjs'
import { MemoryFs } from './helpers.mjs'
import { FRAME_WIRE_LIMIT } from '../src/bounded.mjs'
import { randomUUID } from 'node:crypto'
const version = `sha256:${'a'.repeat(64)}`, planId = `sha256:${'b'.repeat(64)}`
async function bench(options = {}) {
  const fs = new MemoryFs(), sessions = new Map([['one', { id: 'one' }], ['two', { id: 'two' }]]), calls = []
  let now = 0, current = version
  const config = { vtxExecutable: '/opt/trusted/vtx', clock: () => now, logger() {} }
  const subprocess = { resolveExecutable: async () => '/opt/trusted/vtx', spawn(spec) {
    calls.push(spec)
    const storage = spec.argv[3] === 'storage', operation = storage ? spec.argv[4] : spec.argv[3]
    const value = storage ? { schema: 'framivra.storage/v1', operation, ok: true, content_version: current, diagnostics: [],
      ...(operation === 'inspect' ? { usage: { project_bytes: 20, cache_bytes: 10, out_bytes: 0, other_bytes: 10, file_count: 2, symlinks_skipped: 0 }, cache_mode: 'core' } : {}),
      ...(operation === 'plan' ? { plan: { id: planId, bytes: 10, count: 1, entries: [{ path: 'cache/old', bytes: 10, category: 'cache', reason: 'stale' }] } } : {}),
      ...(operation === 'clean' ? { removed: { bytes: 10, count: 1 }, remaining: { bytes: 0, count: 0 } } : {}) }
      : { schema: 'framivra.timeline.read/v1', operation, ok: true, content_version: current, diagnostics: [], document: {}, coverage: { complete: true, scope: 'fixture', unexpanded: [] }, capabilities: { operations: ['inspect', 'query'], frame: { schema: 'framivra.timeline.frame/v1', explicit_render_required: true, inline_png: { flag: '--include-png', encoding: 'base64', wire_limit_bytes: FRAME_WIRE_LIMIT } }, ...(options.unsupported ? {} : { storage: { schema: 'framivra.storage/v1', operations: ['inspect', 'plan', 'clean'], request_stdin: true } }) } }
    const text = JSON.stringify(value)
    if (storage && options.handle) { const handle = options.handle(spec, text); if (handle) return handle }
    return { done: Promise.resolve({ exitCode: 0 }), terminate() {}, waitForExit: async () => true,
      collected: { stdout: { readFrom: () => ({ text, lossy: false }) }, stderr: { readFrom: () => ({ text: '', lossy: false }) } } }
  } }
  const host = createPreviewHost({ fs, sessions, workingDirectory: { get: () => '/workspace' }, subprocess }, config)
  const control = (data, signal) => host.control(new Request('http://unit/control', { method: 'POST', body: JSON.stringify({ sessionId: 'one', ...data }), signal }))
  const { grant } = await (await control({ op: 'open', timeline: 'timeline.json' })).json()
  const call = (op, fields = {}, signal) => control({ op, grant, ...fields }, signal)
  return { host, fs, calls, grant, control, call, config, advance: () => { now += 60_001 }, change: () => { current = `sha256:${'c'.repeat(64)}` } }
}
test('storage is separately advertised; older cores retain ordinary reads', async () => {
  const b = await bench({ unsupported: true })
  try { assert.equal((await b.call('storage-inspect')).status, 422); assert.equal((await b.call('inspect')).status, 200); assert.equal(b.calls.filter(s => s.argv[3] === 'storage').length, 0) }
  finally { await b.host.dispose() }
})
test('storage cleanup is one-use, frozen stdin and independent explicit confirmation', async () => {
  const b = await bench()
  try {
    const plan = await (await b.call('storage-plan')).json()
    assert.equal((await b.call('storage-clean', { planToken: plan.planToken, writeGrant: 'x', confirm: 'stopped-writers-and-cleanup' })).status, 400)
    assert.equal((await b.call('storage-clean', { planToken: plan.planToken, confirm: 'cache/preview-only' })).status, 403)
    assert.equal((await b.call('storage-clean', { planToken: plan.planToken, request: { paths: ['/etc/passwd'] }, confirm: 'stopped-writers-and-cleanup' })).status, 400)
    const clean = { planToken: plan.planToken, confirm: 'stopped-writers-and-cleanup' }
    assert.equal((await b.call('storage-clean', clean)).status, 200)
    assert.equal((await b.call('storage-clean', clean)).status, 409)
    const spec = b.calls.find(s => s.argv[4] === 'clean')
    assert.deepEqual(spec.argv.slice(3), ['storage', 'clean', '--request-stdin'])
    assert.deepEqual(JSON.parse(spec.stdio.stdin.data), { plan_id: planId, content_version: version, confirm: clean.confirm })
    assert.equal(b.fs.reads.length, 0)
  } finally { await b.host.dispose() }
})
test('storage plan binds session, grant, expiry, saved version, reopen and executable', async () => {
  for (const kind of ['session', 'grant', 'expiry', 'version', 'reopen', 'executable', 'config']) {
    const b = await bench()
    try {
      const plan = await (await b.call('storage-plan')).json()
      const fields = { planToken: plan.planToken, confirm: 'stopped-writers-and-cleanup' }
      if (kind === 'session') fields.sessionId = 'two'
      if (kind === 'grant') { const opened = await (await b.control({ op: 'open', timeline: 'timeline.json' })).json(); fields.grant = opened.grant }
      if (kind === 'expiry') b.advance()
      if (kind === 'version') b.change()
      if (kind === 'reopen') await b.control({ op: 'open', timeline: 'timeline.json' })
      if (kind === 'executable') b.fs.files.get('/opt/trusted/vtx').version = 'changed'
      if (kind === 'config') b.config.vtxExecutable = '/opt/other/vtx'
      const response = await b.call('storage-clean', fields)
      assert([403, 409].includes(response.status), `${kind}: ${await response.text()}`)
      assert.equal(b.calls.filter(s => s.argv[4] === 'clean').length, 0)
    } finally { await b.host.dispose() }
  }
})
test('storage cancellation retains admission until managed range exits and surfaces unknown clean', async () => {
  let startedResolve, doneResolve, rangeResolve, terminated = 0
  const started = new Promise(r => { startedResolve = r }), done = new Promise(r => { doneResolve = r }), range = new Promise(r => { rangeResolve = r })
  const b = await bench({ handle(spec, text) {
    if (spec.argv[4] !== 'clean') return
    startedResolve()
    return { done, terminate() { terminated++ }, waitForExit: () => range, collected: { stdout: { readFrom: () => ({ text, lossy: false }) } } }
  } })
  try {
    const plan = await (await b.call('storage-plan')).json()
    const fields = { planToken: plan.planToken, confirm: 'stopped-writers-and-cleanup' }
    const cleaning = b.call('storage-clean', fields); await started
    assert.equal((await b.call('storage-inspect')).status, 409)
    assert.equal((await b.call('authorize-write')).status, 409)
    const frame = await b.host.frame(new Request('http://unit/frame', { method: 'POST', body: JSON.stringify({ op: 'render', sessionId: 'one', grant: b.grant, frame: 0, contentVersion: version, renderConsent: 'cache/preview-only', operationId: randomUUID() }) }))
    assert.equal(frame.status, 409)
    assert.equal(b.calls.filter(s => s.argv[4] === 'frame').length, 0)
    let settled = false
    const cancelling = b.call('storage-cancel').then(r => { settled = true; return r })
    await new Promise(r => setImmediate(r)); assert(terminated); assert.equal(settled, false)
    doneResolve({ exitCode: 0 }); rangeResolve(true)
    assert.equal((await cancelling).status, 200)
    assert.equal((await (await cleaning).json()).outcome_unknown, true)
    assert.equal((await b.call('storage-clean', fields)).status, 409)
    assert.equal((await b.call('storage-inspect')).status, 200)
  } finally { doneResolve({ exitCode: 1 }); rangeResolve(true); await b.host.dispose() }
})
test('storage output loss during clean is unknown, consumes token and never repeats child', async () => {
  const b = await bench({ handle(spec) {
    if (spec.argv[4] !== 'clean') return
    return { done: Promise.resolve({ exitCode: 0 }), terminate() {}, waitForExit: async () => true, collected: { stdout: { readFrom: () => ({ text: '{}', lossy: true }) } } }
  } })
  try {
    const plan = await (await b.call('storage-plan')).json()
    const fields = { planToken: plan.planToken, confirm: 'stopped-writers-and-cleanup' }
    const result = await (await b.call('storage-clean', fields)).json()
    assert.equal(result.outcome_unknown, true)
    assert.equal((await b.call('storage-clean', fields)).status, 409)
    assert.equal(b.calls.filter(s => s.argv[4] === 'clean').length, 1)
  } finally { await b.host.dispose() }
})
test('overlapping scans are refused; reopening during planning cannot mint a stale token', async () => {
  let finish, startedResolve
  const done = new Promise(r => { finish = r }), started = new Promise(r => { startedResolve = r })
  const b = await bench({ handle(spec, text) {
    if (spec.argv[4] !== 'plan') return
    startedResolve()
    return { done, terminate() {}, waitForExit: async () => true, collected: { stdout: { readFrom: () => ({ text, lossy: false }) } } }
  } })
  try {
    const planning = b.call('storage-plan'); await started
    assert.equal((await b.call('storage-inspect')).status, 409)
    assert.equal((await b.control({ op: 'open', timeline: 'timeline.json' })).status, 200)
    finish({ exitCode: 0 })
    assert.equal((await planning).status, 409)
  } finally { finish({ exitCode: 0 }); await b.host.dispose() }
})
test('storage-clean spawn is semanticBusy and all storage outputs are bounded', async () => {
  let finish
  const done = new Promise(r => { finish = r })
  const reader = new TimelineReader({ spawn() { return { done, terminate() {}, waitForExit: async () => true, collected: { stdout: { readFrom: () => ({ text: '', lossy: true }) } } } } }, { vtxExecutable: '/opt/trusted/vtx' })
  const pending = reader.storage('/opt/trusted/vtx', '/workspace/timeline.json', 'clean', { plan_id: planId }, new AbortController().signal)
  assert.equal(reader.semanticBusy, true)
  finish({ exitCode: 0 })
  await assert.rejects(pending, error => error.code === 'TRUNCATED_JSON')
  assert.equal(reader.semanticBusy, false)
  await reader.dispose()
})
function resultHandle(value, exitCode = value.ok ? 0 : 1) {
  const text = JSON.stringify(value)
  return { done: Promise.resolve({ exitCode }), terminate() {}, waitForExit: async () => true, collected: { stdout: { readFrom: () => ({ text, lossy: false }) } } }
}
test('storage decoder allows null version only on business failures and preserves early diagnostics', async () => {
  for (const [ok, content_version, valid] of [[false, null, true], [false, version, true], [false, 'bad', false], [true, null, false], [false, undefined, false]]) {
    const value = { schema: 'framivra.storage/v1', operation: 'inspect', ok, content_version, diagnostics: [{ code: 'early_error', message: 'fixture' }] }
    const reader = new TimelineReader({ spawn: () => resultHandle(value) }, { vtxExecutable: '/opt/trusted/vtx' })
    try {
      const pending = reader.storage('/opt/trusted/vtx', '/workspace/timeline.json', 'inspect', undefined, new AbortController().signal)
      if (valid) assert.deepEqual(await pending, value)
      else await assert.rejects(pending, error => error.code === 'PROTOCOL_MISMATCH')
    } finally { await reader.dispose() }
  }
})
test('host passes early core failures and core unknown cleanup through without inventing statistics', async () => {
  const b = await bench({ handle(spec) {
    const operation = spec.argv[4]
    if (operation === 'plan') return
    return resultHandle({ schema: 'framivra.storage/v1', operation, ok: false, content_version: null, ...(operation === 'clean' ? { outcome_unknown: true } : {}), diagnostics: [{ code: 'accounting_failed', message: 'fixture' }] })
  } })
  try {
    const inspect = await (await b.call('storage-inspect')).json()
    assert.equal(inspect.content_version, null); assert.equal(inspect.diagnostics[0].code, 'accounting_failed')
    const plan = await (await b.call('storage-plan')).json()
    const result = await (await b.call('storage-clean', { planToken: plan.planToken, confirm: 'stopped-writers-and-cleanup' })).json()
    assert.equal(result.outcome_unknown, true); assert.equal(result.content_version, null)
    assert.equal(result.diagnostics[0].code, 'accounting_failed'); assert.equal(Object.hasOwn(result, 'removed'), false)
  } finally { await b.host.dispose() }
})
test('storage failed cleanup retains admission until explicit scoped cancellation reaps it', async () => {
  let attempts = 0
  const b = await bench({ handle(spec, text) {
    if (spec.argv[4] !== 'plan') return
    return { ...resultHandle(JSON.parse(text)), waitForExit: async () => ++attempts > 1 }
  } })
  try {
    assert.equal((await b.call('storage-plan')).status, 502)
    assert.equal((await b.call('storage-inspect')).status, 409)
    assert.equal((await b.call('storage-cancel')).status, 200)
    assert.equal((await b.call('storage-inspect')).status, 200)
    assert.equal(attempts, 2)
  } finally { await b.host.dispose() }
})
