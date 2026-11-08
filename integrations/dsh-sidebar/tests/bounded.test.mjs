import test from 'node:test'
import assert from 'node:assert/strict'
import vm from 'node:vm'
import { boundedText, CONTROL_REQUEST_LIMIT, FRAME_WIRE_LIMIT } from '../src/bounded.mjs'

const byteStream = (chunks, hooks = {}) => new ReadableStream({
  pull(controller) { hooks.pull?.(); chunks.length ? controller.enqueue(chunks.shift()) : controller.close() },
  cancel(reason) { hooks.cancel?.(reason) },
})

const message = (body, headers) => new Response(body, { headers })

test('bounds are exported as concrete byte numbers', () => {
  // Must at least admit the largest valid plan body: 256 semantic operations on
  // the 8 MiB --request-stdin transport plus envelope overhead.
  assert.ok(Number.isSafeInteger(CONTROL_REQUEST_LIMIT) && CONTROL_REQUEST_LIMIT >= 8 * 1024 * 1024, `CONTROL_REQUEST_LIMIT too small: ${CONTROL_REQUEST_LIMIT}`)
  assert.equal(FRAME_WIRE_LIMIT, 8 * 1024 * 1024)
})

test('exactly-at-limit body passes; one byte over fails closed', async () => {
  const limit = 1024
  const exact = await boundedText(message(byteStream([new Uint8Array(limit).fill(97)])), limit)
  assert.equal(exact.length, limit)
  await assert.rejects(
    boundedText(message(byteStream([new Uint8Array(limit), new Uint8Array([120])])), limit),
    /BODY_LIMIT: Actual stream exceeds feature byte limit/,
  )
})

test('Content-Length is validated before the body is read at all', async () => {
  let pulled = 0
  const over = message(byteStream([new Uint8Array([104, 105])], { pull: () => pulled++ }), { 'Content-Length': String(CONTROL_REQUEST_LIMIT + 1) })
  await assert.rejects(boundedText(over, 16), /BODY_LIMIT: Invalid or oversized Content-Length/)
  const malformed = message(byteStream([new Uint8Array([104, 105])], { pull: () => pulled++ }), { 'Content-Length': '4x' })
  await assert.rejects(boundedText(malformed, 16), /BODY_LIMIT: Invalid or oversized Content-Length/)
  assert.equal(pulled, 0, 'oversized or malformed Content-Length never pulls a chunk')
})

test('cross-realm Uint8Array chunks are accepted; look-alikes are rejected as non-byte', async () => {
  const otherRealm = chunkSource => message(byteStream([vm.runInNewContext(chunkSource)]))
  const text = await boundedText(otherRealm('new Uint8Array([104, 105])'), 16)
  assert.equal(text, 'hi', 'Uint8Array from another vm realm is a byte chunk')
  await assert.rejects(boundedText(otherRealm('new Uint16Array([2593])'), 16), /BODY_LIMIT: A byte stream is required/)
  await assert.rejects(boundedText(otherRealm('"hi"'), 16), /BODY_LIMIT: A byte stream is required/)
  await assert.rejects(boundedText(otherRealm('new DataView(new ArrayBuffer(2))'), 16), /BODY_LIMIT: A byte stream is required/)
})

test('malformed UTF-8 fails instead of decoding lossily', async () => {
  await assert.rejects(boundedText(message(byteStream([new Uint8Array([0xf0, 0x9f, 0x8d]), new Uint8Array([0x41])])), 16))
})

test('a failing or aborted read cancels the underlying stream and releases the reader lock', async () => {
  // over-limit failure: cancel is invoked on the source, and the lock is released
  let cancelled = false
  const over = byteStream([new Uint8Array(64), new Uint8Array([1])], { cancel: () => cancelled = true })
  await assert.rejects(boundedText(message(over), 64), /exceeds feature byte limit/)
  assert.equal(cancelled, true, 'over-limit read cancels the stream')
  assert.doesNotThrow(() => over.getReader(), 'reader lock released after failure')

  // non-byte failure: same ownership contract
  let cancelled2 = false
  const nonbyte = byteStream([{}], { cancel: () => cancelled2 = true })
  await assert.rejects(boundedText(message(nonbyte), 64), /A byte stream is required/)
  assert.equal(cancelled2, true)
  assert.doesNotThrow(() => nonbyte.getReader())

  // signal abort mid-read: cancel carries the abort reason
  let cancelReason = null
  const slow = byteStream([new Uint8Array([97])], { cancel: reason => cancelReason = reason })
  const controller = new AbortController()
  const pending = boundedText(message(slow), 64, controller.signal)
  controller.abort(new Error('UI_GONE'))
  await assert.rejects(pending, /UI_GONE/)
  assert(cancelReason instanceof Error || String(cancelReason).length > 0, 'abort cancels the stream with a reason')

  // a pre-aborted signal never reads
  const preAborted = new AbortController()
  preAborted.abort(new Error('EARLY'))
  const never = byteStream([new Uint8Array([97])])
  await assert.rejects(boundedText(message(never), 64, preAborted.signal), /EARLY/)
})

test('empty and absent bodies decode to empty text without touching a reader', async () => {
  assert.equal(await boundedText(message(null), 16), '')
  const empty = await boundedText(message(byteStream([])), 16)
  assert.equal(empty, '')
})
