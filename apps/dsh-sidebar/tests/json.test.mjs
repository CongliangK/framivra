import test from 'node:test'
import assert from 'node:assert/strict'
import { parseJSON, stringifyJSON, LosslessJSONError } from '../src/json.mjs'
import { decodeEnvelope, queryArgs } from '../src/protocol.mjs'
import { setup } from './helpers.mjs'

test('native JSON preserves every numeric token, not strings or lookalike raw objects', () => {
  const text = '{"id":9007199254740993,"huge":123456789012345678901234567890,"negative":-9007199254740993,"decimal":0.123456789012345678901,"exponent":1e+100,"zero":-0,"small":1e-999,"max":9007199254740991,"text":"9007199254740993","rawJSON":"0","nested":[{"rawJSON":"9007199254740993"}]}'
  const value = parseJSON(text)
  assert.equal(stringifyJSON(value), text)
  assert.equal(value.max, Number.MAX_SAFE_INTEGER)
  assert.equal(value.text, '9007199254740993')
  assert.deepEqual(value.nested[0], { rawJSON: '9007199254740993' })
  assert.throws(() => parseJSON('{"id":9007199254740993oops}'), SyntaxError)
  assert.throws(() => queryArgs({ frame: value.id }), { code: 'BAD_QUERY' })
})

test('unsafe or fractional protocol indices/ranges are explicitly refused', () => {
  const base = '{"schema":"framivra.timeline.read/v1","operation":"query","ok":true,"content_version":"sha256:' + 'b'.repeat(64) + '","diagnostics":[],"coverage":{"complete":true,"scope":"stored","unexpanded":[]},"tracks":[],"blocks":[{"family":"subtitles","track_index":TRACK,"block_index":0,"address":"/subtitles/0","range":[0,END],"data":{"asset_id":9007199254740993}}]}'
  for (const [track, end] of [['9007199254740993', '30'], ['0', '9007199254740993'], ['1.000000000000000000001', '30']]) {
    assert.throws(() => decodeEnvelope(base.replace('TRACK', track).replace('END', end), 'query'), { code: 'PROTOCOL_MISMATCH' })
  }
})

test('Host returns actionable unsupported-native-JSON diagnostics before spawning vtx', async t => {
  const f = setup(); t.after(() => f.host.dispose())
  const raw = JSON.rawJSON
  try {
    JSON.rawJSON = undefined
    const response = await f.control({ op: 'open', timeline: 'timeline.json' })
    const payload = await response.json()
    assert.equal(response.status, 502)
    assert.equal(payload.error, 'LOSSLESS_JSON_UNSUPPORTED')
    assert.match(payload.message, /Upgrade/)
    assert.equal(f.subprocess.calls.length, 0)
  } finally { JSON.rawJSON = raw }
})

test('missing native source/rawJSON support refuses with actionable precision diagnostic', () => {
  const raw = JSON.rawJSON
  try {
    JSON.rawJSON = undefined
    assert.throws(() => parseJSON('{"asset_id":9007199254740993}'), LosslessJSONError)
    assert.throws(() => decodeEnvelope('{}', 'inspect'), { code: 'LOSSLESS_JSON_UNSUPPORTED' })
  } finally { JSON.rawJSON = raw }
})
