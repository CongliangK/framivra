import test from 'node:test'
import assert from 'node:assert/strict'
import { TimelineReader, queryArgs, decodeEnvelope } from '../src/protocol.mjs'
import { setup } from './helpers.mjs'

const envelope = { schema: 'framivra.timeline.read/v1', operation: 'inspect', ok: true, content_version: `sha256:${'b'.repeat(64)}`, diagnostics: [], document: { unknown: [1, 2] }, coverage: { complete: true, scope: 'stored timeline blocks only', unexpanded: [] } }
const fakeReader = ({ text = JSON.stringify(envelope), lossy = false, exitCode = 0 } = {}) => new TimelineReader({ spawn: () => ({ done: Promise.resolve({ exitCode }), collected: { stdout: { readFrom: () => ({ text, lossy }) }, stderr: { readFrom: () => ({ text: 'diagnostic', lossy: false }) } } }) })
const read = reader => reader.read('/opt/vtx', '/project/timeline.json', 'inspect', {}, new AbortController().signal)

test('query builder forwards only explicit validated integer selectors; no shell/string query', () => {
  assert.deepEqual(queryArgs({ family: 'video', track_index: 1, block_index: 2, interval: [4, 10] }), ['--family', 'video', '--track-index', '1', '--block-index', '2', '--interval', '4', '10'])
  assert.deepEqual(queryArgs({ frame: 0 }), ['--frame', '0'])
  for (const value of [{ family: 'x' }, { track_index: 0 }, { block_index: 1, family: 'video' }, { frame: 1.2 }, { frame: -1 }, { frame: '1;rm' }, { frame: 1, interval: [0, 2] }, { interval: [2, 2] }, { interval: [0, Infinity] }, { shell: true }, []]) {
    assert.throws(() => queryArgs(value), { code: 'BAD_QUERY' })
  }
})

test('reject lossy/truncated JSON, malformed envelope and CLI unsupported/failure distinctly', async () => {
  assert.deepEqual((await read(fakeReader())).document, { unknown: [1, 2] })
  for (const options of [{ lossy: true }, { text: '{' }, { text: JSON.stringify({ ...envelope, schema: 'v2' }) }, { exitCode: 2 }, { exitCode: 1 }]) {
    const code = options.lossy ? 'TRUNCATED_JSON' : options.text === '{' ? 'INVALID_JSON' : options.exitCode === 2 ? 'CLI_UNSUPPORTED' : options.exitCode === 1 ? 'CLI_FAILED' : 'PROTOCOL_MISMATCH'
    await assert.rejects(read(fakeReader(options)), { code })
  }
  const value = JSON.stringify({ ...envelope, operation: 'query', tracks: [], blocks: [{ family: 'video', track_index: 0, address: '/x', data: {}, range: [0, 1] }] })
  assert.throws(() => decodeEnvelope(value, 'query'), { code: 'PROTOCOL_MISMATCH' })
  await assert.rejects(read(fakeReader({ exitCode: 1, text: JSON.stringify({ diagnostics: [{ code: 'BAD_INPUT' }] }) })), error => error.details.diagnostics[0].code === 'BAD_INPUT')
})

test('request cancellation reaches subprocess and never yields partial JSON', async t => {
  const f = setup(); t.after(() => f.host.dispose())
  let began, finish
  const started = new Promise(resolve => { began = resolve })
  let signal
  f.ctx.subprocess.spawn = spec => {
    signal = spec.signal
    began()
    const done = new Promise(resolve => { finish = resolve })
    signal.addEventListener('abort', () => finish({ exitCode: null, signal: 'SIGTERM' }), { once: true })
    return { done, terminate: () => finish({ exitCode: null, signal: 'SIGTERM' }), collected: { stdout: { readFrom: () => ({ text: '{', lossy: false }) } } }
  }
  const controller = new AbortController()
  const result = f.host.control(new Request('http://owner/api/framivra/control', { method: 'POST', body: JSON.stringify({ sessionId: 'one', op: 'open', timeline: 'timeline.json' }), signal: controller.signal }))
  await started
  controller.abort()
  assert.equal((await result).status, 499)
  assert.equal(signal.aborted, true)
})

test('plugin dispose cancels active subprocesses, streams and grants', async () => {
  const f = setup()
  const opened = await (await f.control({ op: 'open', timeline: 'timeline.json' })).json()
  const response = await f.media(opened.grant, opened.media[0].mediaId)
  const stream = response.body.getReader()
  await stream.read()
  let began, terminated = false
  const started = new Promise(resolve => { began = resolve })
  f.ctx.subprocess.spawn = spec => {
    let finish
    const done = new Promise(resolve => { finish = resolve })
    const terminate = () => { terminated = true; finish({ exitCode: null }) }
    spec.signal.addEventListener('abort', terminate, { once: true })
    began()
    return { done, terminate, collected: {} }
  }
  const query = f.control({ op: 'query', grant: opened.grant })
  await started
  await f.host.dispose()
  assert.equal(terminated, true)
  assert.equal((await query).status, 499)
  assert.equal(f.fs.reads[0].signal.aborted, true)
  await assert.rejects(stream.read())
  assert.equal((await f.control({ op: 'inspect', grant: opened.grant })).status, 499)
})
