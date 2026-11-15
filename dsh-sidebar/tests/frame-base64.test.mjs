import test from 'node:test'
import assert from 'node:assert/strict'
import { createHash, webcrypto } from 'node:crypto'
import { deflateSync } from 'node:zlib'
import { verifyFrame, frameRequest, FrameEpoch } from '../src/client/frame.mjs'
import { FRAME_WIRE_LIMIT } from '../src/bounded.mjs'

const hash = bytes => createHash('sha256').update(bytes).digest('hex')
const raw = '{"unknown":9007199254740993,"decimal":1.0000000000000001}'
const version = `sha256:${hash(raw)}`
function chunk(type, data) {
  const body = Buffer.concat([Buffer.from(type), data]), result = Buffer.alloc(data.length + 12)
  result.writeUInt32BE(data.length); body.copy(result, 4)
  let crc = 0xffffffff
  for (const byte of body) { crc ^= byte; for (let bit = 0; bit < 8; bit++) crc = (crc >>> 1) ^ (crc & 1 ? 0xedb88320 : 0) }
  result.writeUInt32BE((crc ^ 0xffffffff) >>> 0, result.length - 4)
  return result
}
// Static deterministic RGB PNG encoding, not rendering or native browser decode evidence.
function png(width, height, noiseBytes = width * height * 3) {
  const stride = width * 3, scan = Buffer.alloc((stride + 1) * height)
  let state = 123456789, left = noiseBytes
  for (let y = 0; y < height; y++) for (let x = 0; x < stride && left > 0; x++, left--) {
    state ^= state << 13; state ^= state >>> 17; state ^= state << 5
    scan[y * (stride + 1) + x + 1] = state & 255
  }
  const ihdr = Buffer.alloc(13); ihdr.writeUInt32BE(width); ihdr.writeUInt32BE(height, 4); ihdr[8] = 8; ihdr[9] = 2
  return Buffer.concat([Buffer.from([137,80,78,71,13,10,26,10]), chunk('IHDR', ihdr), chunk('IDAT', deflateSync(scan)), chunk('IEND', Buffer.alloc(0))])
}
function envelope(bytes, width, height) {
  return { schema: 'framivra.timeline.frame/v1', operation: 'frame', ok: true, content_version: version, diagnostics: [],
    image: { state: 'generated', kind: 'current-composite', approximate: false, mime_type: 'image/png', width, height, frame: 6, fps: { num: 30, den: 1 }, content_version: version, input_fingerprint: 'a'.repeat(64), identity_semantics: 'Fast media stat identity; not live atomic', render_key: 'b'.repeat(64), cache_hit: false, bytes: bytes.length, sha256: hash(bytes), inline: { encoding: 'base64', data: bytes.toString('base64') } },
    reference: { schema: 'framivra.timeline.reference/v1', operation: 'reference', ok: true, content_version: version, diagnostics: [], document: { raw, content_version: version }, selection: { global_frame: 6, fps: 30, tracks: [], blocks: [] }, coverage: { complete: true } } }
}
const options = extra => ({ frame: 6, contentVersion: version, signal: new AbortController().signal, crypto: webcrypto, ...extra })

test('canonical large PNG and near whole-wire limit preserve exact File, SHA, raw and certificate', async () => {
  for (const [width, height, noise] of [[1280,1080,1500000], [1280,1080], [2094,1000]]) {
    const bytes = png(width, height, noise), value = envelope(bytes, width, height)
    const wire = JSON.stringify(value) + '\n', wireBytes = Buffer.byteLength(wire)
    assert(wireBytes <= FRAME_WIRE_LIMIT)
    if (width === 2094) assert(wireBytes > FRAME_WIRE_LIMIT - 16384, `near boundary: ${wireBytes}`)
    const read = await frameRequest('one', {}, new AbortController().signal, async () => new Response(wire))
    let decodes = 0
    const result = await verifyFrame(read, options({ decode: async (file, w, h, signal) => {
      decodes++; signal.throwIfAborted(); assert.deepEqual([w,h], [width,height]); assert.deepEqual(Buffer.from(await file.arrayBuffer()), bytes)
    } }))
    assert.equal(decodes, 1); assert.deepEqual(Buffer.from(await result.file.arrayBuffer()), bytes)
    assert.equal(hash(Buffer.from(await result.file.arrayBuffer())), value.image.sha256)
    assert.equal(result.file.type, 'image/png'); assert.equal(result.file.lastModified, 0)
    assert.equal(result.file.name, `framivra-frame-6-${value.image.sha256.slice(0,16)}.png`)
    assert.deepEqual(result.reference, value.reference); assert.equal(result.reference.document.raw, raw)
    const { inline, ...image } = value.image
    assert.deepEqual(result.certificate, image); assert(!('inline' in result.certificate))
  }
})

test('noncanonical alphabet, padding, bits, whitespace, encoding and lengths reject before File/decode', async () => {
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/'
  // Real PNGs with zero, one and two padding characters exercise the strict roundtrip.
  const seed = png(1,1)
  const bases = [0,1,2].map(n => Buffer.concat([seed.subarray(0,-12), chunk('tEXt', Buffer.from(`x\0${'a'.repeat(n)}`)), seed.subarray(-12)]))
  assert.equal(new Set(bases.map(bytes => bytes.length % 3)).size, 3)
  for (const bytes of bases) {
    const original = envelope(bytes, bytes.readUInt32BE(16), 1)
    const data = original.image.inline.data, mid = Math.floor(data.length / 2)
    const mutations = [
      v => { v.image.inline.data = data.slice(0,mid) + '!' + data.slice(mid+1) },
      ...[' ', '\n', '\r', '\t', '\u00a0', '\u2028', 'é', '-', '_', '='].map(char => v => { v.image.inline.data = data.slice(0,mid) + char + data.slice(mid+1) }),
      v => { v.image.inline.data = data.slice(0,-1) + '\n' },
      v => { v.image.inline.data += '=' }, v => { v.image.inline.data = data.slice(0,-1) },
      v => { v.image.inline.data = data.slice(0,-2) + '==' },
      v => { v.image.inline.encoding = 'base64url' }, v => { v.image.inline.data = new String(data) },
      v => { v.image.bytes = FRAME_WIRE_LIMIT + 1 },
    ]
    const pad = data.endsWith('==') ? 2 : data.endsWith('=') ? 1 : 0
    if (pad) mutations.push(v => {
      const at = data.length-pad-1, index = alphabet.indexOf(data[at])
      v.image.inline.data = data.slice(0,at) + alphabet[index | 1] + data.slice(at+1)
    })
    for (const mutate of mutations) {
      const value = structuredClone(original); mutate(value)
      // A redundant '==' can be canonical for some fixtures; only test actual mutations.
      if (value.image.inline.data === data && value.image.bytes === bytes.length && value.image.inline.encoding === 'base64') continue
      let files = 0, decodes = 0
      class CountFile extends File { constructor(...args) { super(...args); files++ } }
      await assert.rejects(verifyFrame(value, options({ File: CountFile, decode: async () => { decodes++ } })), /FRAME_INVALID/)
      assert.equal(files, 0); assert.equal(decodes, 0)
    }
  }
})

test('large invalid middle character, policy and whole-wire overflow fail closed', async () => {
  const bytes = png(1280,1080), value = envelope(bytes,1280,1080), at = 2000000
  value.image.inline.data = value.image.inline.data.slice(0,at) + '!' + value.image.inline.data.slice(at+1)
  let decodes = 0
  await assert.rejects(verifyFrame(value, options({ decode: async () => { decodes++ } })), /FRAME_INVALID/)
  await assert.rejects(verifyFrame(envelope(bytes,1280,1080), options({ limits: { mediaTypes:['image/png'],maxImageBytes:bytes.length-1,maxMessageImageBytes:bytes.length,maxImagesPerMessage:1 }, decode: async () => { decodes++ } })), /FRAME_INVALID/)
  assert.equal(decodes,0)
  await assert.rejects(frameRequest('one',{},new AbortController().signal,async()=>new Response(' '.repeat(FRAME_WIRE_LIMIT+1))), /BODY_LIMIT/)
})

test('synchronous cancellation and digest-time invalidation retain no partial File; legal retry works', async () => {
  const bytes = png(1,1), value = envelope(bytes,1,1), owner = new FrameEpoch(), tab = new AbortController()
  let files = 0, decodes = 0
  class CountFile extends File { constructor(...args) { super(...args); files++ } }
  const first = owner.begin(tab.signal); owner.invalidate()
  await assert.rejects(verifyFrame(value, options({signal:first.signal,File:CountFile,decode:async()=>{decodes++}})), /Abort/)
  const second = owner.begin(tab.signal)
  const crypto = {subtle:{async digest(...args){owner.invalidate();return webcrypto.subtle.digest(...args)}}}
  await assert.rejects(verifyFrame(value, options({signal:second.signal,crypto,File:CountFile,decode:async()=>{decodes++}})), /Abort/)
  assert.equal(files,0); assert.equal(decodes,0); assert.equal(owner.generated,undefined)
  const third = owner.begin(tab.signal)
  const result = await verifyFrame(value, options({signal:third.signal,File:CountFile,decode:async()=>{decodes++}}))
  owner.check(third); owner.generated = result
  assert.equal(files,1); assert.equal(decodes,1); assert.equal(owner.generated,result)
  owner.invalidate(); assert.equal(owner.generated,undefined)
})
