import test from 'node:test'
import assert from 'node:assert/strict'
import { setup } from './helpers.mjs'

// Diagnostic logs are for the operator, never the client envelope. These tests
// throw before spawning; no subprocess, signal delivery, or project writes.
test('unexpected Host error records a bounded stack without exposing it over HTTP', async t => {
  const messages = []
  t.mock.method(console, 'error', message => messages.push(message))
  const fixture = setup()
  t.after(() => fixture.host.dispose())
  fixture.fs.resolve = async () => { throw new TypeError('operator-only-test-marker ' + 'x'.repeat(9000)) }
  const response = await fixture.control({ op: 'open', timeline: 'timeline.json' })
  assert.equal(response.status, 500)
  assert.deepEqual(await response.json(), { ok: false, error: 'PREVIEW_FAILED', message: 'Preview read failed' })
  assert.equal(messages.length, 1)
  assert.match(messages[0], /^\[framivra-dsh-sidebar\] Unexpected control failure:/)
  assert.match(messages[0], /TypeError: operator-only-test-marker/)
  assert.match(messages[0], /\[stack truncated\]$/)
  assert.ok(messages[0].length < 8400)
  assert.equal(fixture.subprocess.calls.filter(call => call.argv).length, 0)
})

test('expected admission and cancellation failures do not log unexpected stacks', async t => {
  const messages = []
  t.mock.method(console, 'error', message => messages.push(message))
  const fixture = setup()
  t.after(() => fixture.host.dispose())
  const denied = await fixture.control({ op: 'open', timeline: '../outside.json' })
  assert.equal(denied.status, 400)
  assert.equal((await denied.json()).error, 'BAD_PATH')
  const controller = new AbortController()
  controller.abort()
  const cancelled = await fixture.host.control(new Request('http://owner/api/framivra/control', {
    method: 'POST', signal: controller.signal,
    body: JSON.stringify({ op: 'open', timeline: 'timeline.json', sessionId: 'one' }),
  }))
  assert.equal(cancelled.status, 499)
  assert.equal((await cancelled.json()).error, 'CANCELLED')
  assert.deepEqual(messages, [])
  assert.equal(fixture.subprocess.calls.filter(call => call.argv).length, 0)
})

test('a failed diagnostic sink cannot replace the original sanitized failure', async t => {
  t.mock.method(console, 'error', () => { throw new Error('diagnostic sink unavailable') })
  const fixture = setup()
  t.after(() => fixture.host.dispose())
  fixture.fs.resolve = async () => { throw new TypeError('original unexpected failure') }
  const response = await fixture.control({ op: 'open', timeline: 'timeline.json' })
  assert.equal(response.status, 500)
  assert.deepEqual(await response.json(), { ok: false, error: 'PREVIEW_FAILED', message: 'Preview read failed' })
})
