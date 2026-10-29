import test from 'node:test'
import assert from 'node:assert/strict'
import { referenceArgs, decodeEnvelope } from '../src/protocol.mjs'
import { captureCurrentDraft, performReferenceAction, diagnosticText } from '../src/client/reference.mjs'
import { setup } from './helpers.mjs'

const version = `sha256:${'a'.repeat(64)}`
const selection = { kind: 'block', address: '/subtitles/0' }
const raw = '{ "in":0, "out":30, "text":"中文", "huge":9007199254740993, "x":1e400, "a":1.0000000000000001 }'
const envelope = (stable_id = null) => ({ schema: 'framivra.timeline.reference/v1', operation: 'reference', ok: true, content_version: version, document: { raw: `\n[${raw}]\n`, content_version: version }, diagnostics: [], coverage: { complete: true, scope: 'stored', unexpanded: [] }, image: { state: 'not-generated', kind: null }, selection: { fps: 30, global_frame: 0, tracks: [], blocks: [{ ...selection, stable_id, raw, content_version: version }] } })
function options(extra = {}) { return { selection, contentVersion: version, frame: 0, signal: new AbortController().signal, read: async () => envelope(), ...extra } }

test('reference argv is a bounded adapter; no semantic guessing or option injection', () => {
  assert.deepEqual(referenceArgs({ expected_content_version: version, block_addresses: ['/subtitles/0'], frame: 0 }), [`--expected-content-version=${version}`, '--block-address=/subtitles/0', '--frame', '0'])
  assert.deepEqual(referenceArgs({ expected_content_version: version, block_ids: ['--help'] }), [`--expected-content-version=${version}`, '--block-id=--help'])
  for (const query of [{}, { expected_content_version: version }, { expected_content_version: version, block_ids: ['x'], frame: JSON.rawJSON('1.0') }, { expected_content_version: version, block_ids: ['x', 'y'] }, { expected_content_version: version, block_ids: ['x'], path: '/outside' }]) assert.throws(() => referenceArgs(query), { code: 'BAD_REFERENCE' })
  assert.equal(decodeEnvelope(JSON.stringify(envelope()), 'reference').selection.blocks[0].raw, raw)
  assert.throws(() => decodeEnvelope(JSON.stringify({ ...envelope(), image: {} }), 'reference'), { code: 'PROTOCOL_MISMATCH' })
})

test('copy receives exact raw substring; managed ID vs legacy locator are not conflated', async () => {
  let copied
  const clipboard = { writeText: async text => { copied = text } }
  await performReferenceAction(options({ action: 'raw', clipboard }))
  assert.equal(copied, raw)
  await performReferenceAction(options({ action: 'id', clipboard }))
  assert.equal(copied, `transient:/subtitles/0\ncontent_version:${version}`)
  await performReferenceAction(options({ action: 'id', clipboard, read: async () => envelope('block-real-id') }))
  assert.equal(copied, 'block-real-id')
  await performReferenceAction(options({ action: 'diagnostic', clipboard }))
  assert.equal(copied, diagnosticText(envelope()))
  assert.equal(JSON.parse(copied).selection.blocks[0].raw, raw)
})

test('click captures once before async IO; stages only one append with the same opaque token', async () => {
  const events = [], target = Object.freeze({}), binding = {}
  const conversation = { captureDraftTarget: b => { assert.equal(b, binding); events.push('capture'); return target }, stageDraft: async request => { events.push('stage'); assert.equal(request.target, target); assert.ok(request.text.includes('未生成截图')); return { status: 'applied', draftRev: 1 } } }
  const captured = captureCurrentDraft(() => ({ conversation, sessions: { binding: id => { assert.equal(id, 'current'); return binding } } }), 'current')
  const result = await performReferenceAction(options({ action: 'draft', captured, read: async () => { events.push('read'); return envelope() } }))
  assert.equal(result.status, 'applied')
  assert.deepEqual(events, ['capture', 'read', 'stage'])
})

test('missing APIs/blank session, stale version, cancellation, raw absence and limits fail explicitly', async () => {
  assert.throws(() => captureCurrentDraft(() => ({}), undefined), /DRAFT_UNAVAILABLE/)
  const conversation = { captureDraftTarget() { throw Error('must not capture') }, stageDraft() {} }
  assert.throws(() => captureCurrentDraft(() => ({ conversation, sessions: { binding: () => undefined } })), /尚无已保留绑定/)
  const copied = [], staged = []
  const captured = { target: {}, conversation: { stageDraft: request => staged.push(request) } }
  await assert.rejects(performReferenceAction(options({ action: 'draft', captured, read: async () => ({ ...envelope(), content_version: `sha256:${'b'.repeat(64)}` }) })), /STALE_REFERENCE/)
  const controller = new AbortController()
  await assert.rejects(performReferenceAction(options({ action: 'draft', captured, signal: controller.signal, read: async () => { controller.abort(); return envelope() } })), { name: 'AbortError' })
  const noRaw = envelope(); noRaw.selection.blocks[0].raw = null
  await assert.rejects(performReferenceAction(options({ action: 'raw', clipboard: { writeText: text => copied.push(text) }, read: async () => noRaw })), /RAW_UNAVAILABLE/)
  assert.throws(() => diagnosticText({ raw: 'x'.repeat(8 * 1024 * 1024) }), /REFERENCE_LIMIT/)
  assert.deepEqual(staged, []); assert.deepEqual(copied, [])
})

test('reference reuses grant/current-cwd and Host-pinned version, not browser path claims', async t => {
  const f = setup(); t.after(() => f.host.dispose())
  const opened = await (await f.control({ op: 'open', timeline: 'timeline.json' })).json()
  const query = { expected_content_version: version, block_addresses: ['/subtitles/0'] }
  const before = f.subprocess.calls.length
  assert.equal((await f.control({ op: 'reference', grant: opened.grant, query: { ...query, expected_content_version: `sha256:${'b'.repeat(64)}` } })).status, 409)
  assert.equal((await f.control({ op: 'reference', sessionId: 'two', grant: opened.grant, query })).status, 403)
  f.cwd.value = '/other'
  assert.equal((await f.control({ op: 'reference', grant: opened.grant, query })).status, 403)
  assert.equal(f.subprocess.calls.length, before)
})
