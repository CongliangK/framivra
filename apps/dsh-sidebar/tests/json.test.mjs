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

test('every Host query selector rejects noncanonical/unsafe raw tokens before spawning', async t => {
  const f = setup(); t.after(() => f.host.dispose())
  const opened = await (await f.control({ op: 'open', timeline: 'timeline.json' })).json()
  const request = query => f.host.control(new Request('http://owner/api/framivra/control', {
    method: 'POST', body: `{"sessionId":"one","op":"query","grant":"${opened.grant}","query":${query}}`,
  }))
  const selectors = token => [
    `{"frame":${token}}`, `{"family":"video","track_index":${token}}`,
    `{"family":"video","track_index":0,"block_index":${token}}`,
    `{"interval":[${token},9007199254740991]}`, `{"interval":[0,${token}]}`,
  ]
  const before = f.subprocess.calls.length
  for (const token of ['1.0000000000000001', '9007199254740991.1', '-0', '-1', '1.0', '1e0', '1e309', '1e-400', '9007199254740992', '9007199254740993', '9'.repeat(4000), '"1"', 'null', 'true']) {
    for (const query of selectors(token)) {
      const response = await request(query)
      assert.equal(response.status, 400, query)
      assert.equal((await response.json()).error, 'BAD_QUERY', query)
    }
  }
  assert.equal(f.subprocess.calls.length, before)
  for (const token of ['0', '29', '30', '9007199254740991']) {
    const response = await request(`{"frame":${token}}`)
    assert.equal(response.status, 200, await response.text())
    assert.deepEqual(f.subprocess.calls.at(-1).argv.slice(-2), ['--frame', token])
  }
  for (const query of ['{"family":"video","track_index":0,"block_index":0}', '{"interval":[0,9007199254740991]}']) {
    const response = await request(query)
    assert.equal(response.status, 200, await response.text())
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
