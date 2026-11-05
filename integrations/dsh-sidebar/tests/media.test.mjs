import test from 'node:test'
import assert from 'node:assert/strict'
import { byteRange } from '../src/media.mjs'
import { setup } from './helpers.mjs'

test('single range covers suffix, open end, clamping, zero length and integer overflow', () => {
  assert.deepEqual(byteRange(null, 0), { start: 0, end: -1, status: 200 })
  assert.deepEqual(byteRange('bytes=-200', 100), { start: 0, end: 99, status: 206 })
  assert.deepEqual(byteRange('bytes=20-', 100), { start: 20, end: 99, status: 206 })
  assert.deepEqual(byteRange('bytes=20-200', 100), { start: 20, end: 99, status: 206 })
  assert.equal(byteRange('bytes=0-0', 0), undefined)
  assert.equal(byteRange('bytes=9007199254740992-', 100), undefined)
  assert.equal(byteRange('bytes=0-9007199254740992', 100), undefined)
  assert.throws(() => byteRange(null, -1), { code: 'BAD_MEDIA_SIZE' })
})

test('revoke grant between pulls immediately prevents further reads', async t => {
  const f = setup(); t.after(() => f.host.dispose())
  const opened = await (await f.control({ op: 'open', timeline: 'timeline.json' })).json()
  const response = await f.media(opened.grant, opened.media[0].mediaId)
  const stream = response.body.getReader()
  await stream.read()
  await f.control({ op: 'close', grant: opened.grant })
  await assert.rejects(stream.read(), { code: 'BAD_GRANT' })
  assert.equal(f.fs.reads.length, 1)
})

test('request abort interrupts media pull without reading bytes', async t => {
  const f = setup(); t.after(() => f.host.dispose())
  const opened = await (await f.control({ op: 'open', timeline: 'timeline.json' })).json()
  const controller = new AbortController()
  const response = await f.media(opened.grant, opened.media[0].mediaId, { signal: controller.signal })
  controller.abort()
  await assert.rejects(response.body.getReader().read(), { name: 'AbortError' })
  assert.equal(f.fs.reads.length, 0)
})
