import test from 'node:test'
import assert from 'node:assert/strict'
import { setup } from './helpers.mjs'

// Saved-configuration diagnostics: the authenticated route reads ONLY the
// Host's saved trusted configuration (empty body), resolves the real trusted
// executable, probes the static capabilities of the installed vtx through the
// same managed subprocess path as previews, and re-checks that the
// configuration snapshot still holds before reporting any outcome.

const request = (host, body = '{}', options = {}) =>
  host.diagnostics(new Request('http://owner/api/framivra/diagnostics', { method: 'POST', body, ...options }))
const spawns = fixture => fixture.subprocess.calls.filter(call => call.argv)

test('saved-config diagnostic: two static capability probes, no Session required', async t => {
  const f = setup(); t.after(() => f.host.dispose())
  // An entirely empty body is the canonical request.
  const response = await request(f.host, '')
  assert.equal(response.status, 200)
  const value = await response.json()
  assert.equal(value.ok, true)
  assert.equal(value.schema, 'framivra.diagnostics/v1')
  assert.deepEqual(value.config, { vtxExecutable: '/opt/trusted/vtx', trustedPath: '/usr/local/bin:/usr/bin:/bin' })
  assert.equal(value.executable.path, '/opt/trusted/vtx')
  assert.equal(value.executable.regularFile, true)
  assert.equal(value.capabilities.schema, 'framivra.timeline.read/v1')
  assert.equal(value.capabilities.operation, 'capabilities')
  // The fixture vtx exits 2 on `store` — the probe degrades to an advisory,
  // it must not fail the whole check.
  assert.equal(value.storeCapabilities.ok, false)
  assert.equal(value.storeCapabilities.error, 'CLI_UNSUPPORTED')
  assert.equal(spawns(f).length, 2)
  for (const spec of spawns(f)) {
    assert.equal(spec.argv[0], '/opt/trusted/vtx')
    assert.equal(spec.argv[2], 'framivra-diagnostics-no-timeline.json')
    assert.equal(spec.env.PYTHONDONTWRITEBYTECODE, '1')
  }
  assert.deepEqual(spawns(f).map(spec => spec.argv.slice(3)), [['capabilities'], ['store', 'capabilities']])
})

test('diagnostics accepts no input: any field is rejected before spawning', async t => {
  const f = setup(); t.after(() => f.host.dispose())
  for (const body of [
    '{"vtxExecutable":"/evil/vtx"}',
    '{"vtxExecutable":"vtx"}',
    '{"trustedPath":"/evil"}',
    '{"sessionId":"one"}',
    '{"path":"x"}',
    '{"op":"open"}',
    '[]',
    'null',
    '"x"',
  ]) {
    const response = await request(f.host, body)
    assert.equal(response.status, 400, body)
    assert.equal((await response.json()).error, 'BAD_REQUEST')
  }
  assert.equal(spawns(f).length, 0, 'a rejected check must never spawn anything')
})

test('executable resolution failure is actionable and spawns nothing', async t => {
  const f = setup(); t.after(() => f.host.dispose())
  f.ctx.subprocess.resolveExecutable = async () => {
    const error = new Error('vtx not found')
    error.name = 'SubprocessExecutableNotFoundError'
    throw error
  }
  const response = await request(f.host)
  assert.equal(response.status, 424)
  const value = await response.json()
  assert.equal(value.error, 'EXECUTABLE_NOT_FOUND')
  assert.match(value.message, /受信 PATH/)
  assert.equal(spawns(f).length, 0)
})

test('resolved executable must be a regular file', async t => {
  const f = setup(); t.after(() => f.host.dispose())
  f.fs.files.get('/opt/trusted/vtx').type = 'directory'
  const response = await request(f.host)
  assert.equal(response.status, 422)
  const value = await response.json()
  assert.equal(value.error, 'EXECUTABLE_NOT_REGULAR')
  assert.match(value.message, /常规文件/)
  assert.equal(spawns(f).length, 0)
})

test('executable inside a session workspace is refused (no trust by placement)', async t => {
  const f = setup(); t.after(() => f.host.dispose())
  f.fs.files.set('/workspace/bin/vtx', { type: 'file', version: 'workspace-vtx' })
  f.ctx.subprocess.resolveExecutable = async () => '/workspace/bin/vtx'
  const response = await request(f.host)
  assert.equal(response.status, 403)
  assert.equal((await response.json()).error, 'UNTRUSTED_EXECUTABLE')
  assert.equal(spawns(f).length, 0)
})

test('workspace trust sweep uses ONLY the public SessionStore surface (get/list, no values())', async t => {
  const f = setup(); t.after(() => f.host.dispose())
  // Production SessionStore exposes get()/list() only; values() must not be relied on.
  const backing = new Map([['one', { id: 'one' }], ['two', { id: 'two' }]])
  f.ctx.sessions = { get: id => backing.get(id), list: () => [...backing.values()] }
  f.fs.files.set('/workspace/bin/vtx', { type: 'file', version: 'workspace-vtx' })
  f.ctx.subprocess.resolveExecutable = async () => '/workspace/bin/vtx'
  const response = await request(f.host)
  assert.equal(response.status, 403)
  assert.equal((await response.json()).error, 'UNTRUSTED_EXECUTABLE')
  assert.equal(spawns(f).length, 0)
  // Missing public enumeration cannot silently skip the trust check.
  f.ctx.subprocess.resolveExecutable = async () => '/opt/trusted/vtx'
  f.ctx.sessions = { get: id => backing.get(id) }
  const degraded = await request(f.host)
  assert.equal(degraded.status, 503)
  assert.equal((await degraded.json()).error, 'SESSION_STORE_UNAVAILABLE')
  assert.equal(spawns(f).length, 0)
})

test('configuration change during the check voids the outcome', async t => {
  const current = { exe: '/opt/trusted/vtx', path: '/usr/local/bin:/usr/bin:/bin' }
  const f = setup({ vtxExecutable: { get: () => current.exe }, trustedPath: { get: () => current.path } })
  t.after(() => f.host.dispose())
  const original = f.subprocess.spawn.bind(f.subprocess)
  let count = 0
  f.subprocess.spawn = spec => { const handle = original(spec); if (++count === 1) current.exe = '/other/vtx'; return handle }
  const response = await request(f.host)
  assert.equal(response.status, 409)
  assert.equal((await response.json()).error, 'CONFIG_CHANGED')
})

test('aborted request surfaces as CANCELLED', async t => {
  const f = setup(); t.after(() => f.host.dispose())
  const controller = new AbortController()
  controller.abort()
  const response = await request(f.host, '{}', { signal: controller.signal })
  assert.equal(response.status, 499)
  assert.equal((await response.json()).error, 'CANCELLED')
})
