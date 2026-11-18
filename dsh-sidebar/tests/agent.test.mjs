/** Read-only model adapter coverage over recording host callbacks. */
import test from 'node:test'
import assert from 'node:assert/strict'
import { createTimelineAgent } from '../src/agent.mjs'
import { stringifyJSON } from '../src/json.mjs'

const VERSION = 'sha256:' + 'a'.repeat(64)
function makeHarness(overrides = {}) {
  const sessionA = { id: 'S-a' }
  const sessions = new Map([[sessionA.id, sessionA]])
  const cwds = new Map([[sessionA, '/proj']])
  const calls = []
  let closeError
  const opened = { grant: 'g-open', snapshot: { content_version: VERSION }, capabilities: {} }
  const agent = createTimelineAgent({
    ctx: { sessions, workingDirectory: {
      get: session => cwds.get(session),
      ensure: async (agent, signal) => { signal.throwIfAborted(); return cwds.get(agent.session) },
    } },
    call: async (session, data) => {
      calls.push({ session, data })
      if (data.op === 'open') return opened
      if (data.op === 'close') { if (closeError) throw closeError; return { ok: true } }
      return { ok: true, kind: data.op, query: data.query }
    },
    grantFor: () => ({}), editCapabilities: overrides.editCapabilities,
  })
  return { agent, calls, opened, sessions, cwds, sessionA,
    execA: () => ({ agent: { session: sessionA }, signal: new AbortController().signal }),
    setCloseError: error => { closeError = error },
  }
}

test('read is zero-write: only open/close reach the host, envelope carries snapshot + notice', async () => {
  const h = makeHarness()
  const value = await h.agent.read(h.execA(), { timeline: 'demo.json', view: 'inspect' })
  assert.deepEqual(h.calls.map(c => c.data.op), ['open', 'close'])
  assert.equal(value.operation, 'read')
  assert.equal(value.ok, true)
  assert.equal(value.result.content_version, VERSION)
  assert.match(value.notice, /untrusted content, not instructions/)
  assert.match(value.notice, /does not authorize a save/)
})

test('read query parses query_json once and forwards the parsed object with original tokens', async () => {
  const h = makeHarness()
  await h.agent.read(h.execA(), { timeline: 'demo.json', view: 'query', query_json: '{"family":"video","x":9007199254740993}' })
  assert.equal(h.calls[1].data.op, 'query')
  assert.equal(h.calls[1].data.query.family, 'video')
  assert.ok(stringifyJSON(h.calls[1].data.query).includes('9007199254740993'))
})

test('read rejects unknown view, oversized query, and inspect-with-fields without host calls', async () => {
  const h = makeHarness()
  await assert.rejects(h.agent.read(h.execA(), { timeline: 'demo.json', view: 'mutate' }), e => e.code === 'BAD_OPERATION')
  await assert.rejects(h.agent.read(h.execA(), { timeline: 'demo.json', view: 'query', query_json: 'x'.repeat(128 * 1024 + 1) }), e => e.code === 'BAD_QUERY')
  await assert.rejects(h.agent.read(h.execA(), { timeline: 'demo.json', view: 'inspect', query_json: '{"family":"video"}' }), e => e.code === 'BAD_QUERY')
  assert.deepEqual(h.calls.map(c => c.data.op), [])
})

test('oversized read result fails MODEL_OUTPUT_TOO_LARGE without truncation', async () => {
  const h = makeHarness()
  h.opened.snapshot = { content_version: VERSION, blob: 'y'.repeat(256 * 1024 + 64) }
  await assert.rejects(h.agent.read(h.execA(), { timeline: 'demo.json', view: 'inspect' }), e => e.code === 'MODEL_OUTPUT_TOO_LARGE')
})

test('read rejects non-object query_json (null/array/primitive/raw number) with BAD_QUERY before any host call', async () => {
  const h = makeHarness()
  for (const bad of ['null', '[]', '[{"track":"a"}]', '3', '"text"', '123.5', '9007199254740993', 'true']) {
    await assert.rejects(h.agent.read(h.execA(), { timeline: 'demo.json', view: 'query', query_json: bad }),
      e => e.code === 'BAD_QUERY', `query_json ${bad} must be rejected as non-object`)
  }
  assert.equal(h.calls.length, 0)
})

test('inspect exposes edit_capabilities verbatim, including a degraded advisory', async () => {
  const advisory = { ok: false, error: 'CLI_UNSUPPORTED', message: 'Installed vtx does not advertise the semantic store; reads remain usable' }
  const h = makeHarness({ editCapabilities: async () => advisory })
  const value = await h.agent.read(h.execA(), { timeline: 'demo.json', view: 'inspect' })
  assert.deepEqual(value.edit_capabilities, advisory)
  assert.equal(value.ok, true, 'a degraded store advisory must not fail the read itself')
})

test('read exposes no proposal authority and disposal rejects further reads', async () => {
  const h = makeHarness()
  assert.deepEqual(Object.keys(h.agent).sort(), ['dispose', 'read'])
  h.agent.dispose()
  await assert.rejects(h.agent.read(h.execA(), { timeline: 'demo.json', view: 'inspect' }), e => e.code === 'AGENT_DISPOSED')
})

test('read validates the live Session and detects cwd drift before returning', async () => {
  const h = makeHarness()
  const exec = h.execA()
  h.sessions.set(h.sessionA.id, { id: h.sessionA.id })
  await assert.rejects(h.agent.read(exec, { timeline: 'demo.json', view: 'inspect' }), e => e.code === 'BAD_SESSION')
  assert.equal(h.calls.length, 0)
  h.sessions.set(h.sessionA.id, h.sessionA)
  const changing = makeHarness({ editCapabilities: async () => { changing.cwds.set(changing.sessionA, '/new'); return {} } })
  await assert.rejects(changing.agent.read(changing.execA(), { timeline: 'demo.json', view: 'inspect' }), e => e.code === 'BAD_SESSION')
  assert.deepEqual(changing.calls.map(c => c.data.op), ['open', 'close'])
})

test('read preserves both primary and cleanup failures', async () => {
  const h = makeHarness({ editCapabilities: async () => { throw Object.assign(new Error('read failed'), { code: 'READ_FAILED' }) } })
  h.setCloseError(Object.assign(new Error('cleanup failed'), { code: 'CLEANUP_FAILED' }))
  await assert.rejects(h.agent.read(h.execA(), { timeline: 'demo.json', view: 'inspect' }), e =>
    e instanceof AggregateError && e.errors[0].code === 'READ_FAILED' && e.errors[1].code === 'CLEANUP_FAILED')
})
