/**
 * Real-DSH ToolRuntime acceptance for the read-only Framivra model tool.
 *
 * Unlike tools.test.mjs (direct tool.execute with a synthetic exec) and
 * manual-real.test.mjs (UI control ops only), this file mounts the actual
 * public DSH ToolRuntime service with its native executor (the same registry +
 * tool-executor-native pairing dsh-base ships) from SIDEBAR_DSH_ROOT (built
 * lib) with a real Cordis Context + LocalSubprocessRuntime, registers the tools
 * exactly as the plugin does, and drives them through ctx.tools.execute with a
 * live agent
 * Session — no fake agent callbacks, no mocked kernel responses. The vtx
 * processes are the real installed kernel from SIDEBAR_REAL_VTX.
 *
 * Scope guard: this covers the ToolRuntime/registry seam, not Loader/profile
 * composition or the GUI (the parent session exercises those live at 3180).
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdir, mkdtemp, writeFile, readFile } from 'node:fs/promises'
import { join } from 'node:path'
import { createPreviewHost } from '../src/host.mjs'
import { createTimelineTools } from '../src/tools.mjs'
import { parseJSON, stringifyJSON } from '../src/json.mjs'
import { fixture, localFs } from './manual-env.mjs'
import { importDsh } from './dsh-public-sdk.mjs'

const enabled = Boolean(process.env.SIDEBAR_DSH_ROOT && process.env.SIDEBAR_REAL_VTX && process.env.SIDEBAR_EVIDENCE_DIR)
const skip = enabled ? false : 'Set SIDEBAR_DSH_ROOT, SIDEBAR_REAL_VTX and SIDEBAR_EVIDENCE_DIR to run the real ToolRuntime acceptance'

/**
 * A legacy (unmanaged) timeline document written as literal JSON text so the
 * precision-critical number tokens survive on disk byte-exactly. ids are
 * stripped from tracks/clips/events/subtitles (the adopt-compatible shape);
 * source ids stay.
 */
function legacyDocumentText() {
  const doc = fixture()
  for (const track of [...doc.video_tracks, ...doc.audio_tracks]) {
    delete track.id
    for (const block of track.clips ?? track.events) delete block.id
  }
  for (const sub of doc.subtitles) delete sub.id
  // Exact tokens: 2^53+1 and -(2^53+1) are beyond JS safe integers (a JS
  // parse/stringify round-trip would corrupt them) while the kernel keeps
  // integer tokens exact on write. A sub-double-precision decimal is not used
  // here: the kernel itself refuses to write tokens that would lose precision,
  // so only integer tokens can legitimately survive a manage/commit cycle.
  return stringifyJSON(doc).replace('"unknown_root":',
    '"extension":{"asset_id":9007199254740993,"asset_id_neg":-9007199254740993},"unknown_root":')
}

/**
 * Mount the real public DSH services and the plugin's own host + tools.
 * Everything below ctx.tools.execute is production code; only ctx.sessions /
 * workingDirectory are test doubles standing in for the Host adapter seam the
 * plugin already consumes as opaque public services.
 */
async function runtime(t, label) {
  const root = process.env.SIDEBAR_DSH_ROOT
  const evidence = process.env.SIDEBAR_EVIDENCE_DIR
  await mkdir(evidence, { recursive: true })
  const dir = await mkdtemp(join(evidence, `agent-runtime-${label}-`))
  const path = join(dir, 'timeline.json')
  await writeFile(path, legacyDocumentText())
  const dirTwo = await mkdtemp(join(evidence, `agent-runtime-${label}-two-`))
  await writeFile(join(dirTwo, 'timeline.json'), legacyDocumentText())

  const dsh = name => importDsh(root, name)
  const { Context } = await dsh('cordis')
  const { LocalSubprocessRuntime } = await dsh('subprocessLocal')
  const SystemPrompt = (await dsh('systemPrompt')).default
  const ToolRuntime = (await dsh('tools')).default
  // dsh 0.2.2 moved batch execution out of the ToolRuntime registry into a
  // mounted executor plugin; ctx.tools.execute dispatches through whatever
  // executor the composition selects. dsh-base ships the registry and
  // @deepseek-ai/dsh-tool-executor-native side by side, so this mirrors that
  // public composition instead of bypassing the new dispatch contract.
  const nativeToolExecutor = await dsh('toolExecutorNative')
  const { ToolCallId } = await dsh('llm')

  const ctx = new Context()
  const fibers = []
  for (const plugin of [LocalSubprocessRuntime, SystemPrompt, ToolRuntime, nativeToolExecutor]) {
    const fiber = ctx.plugin(plugin)
    fibers.push(fiber)
    await fiber.await()
  }
  t.after(() => { for (const fiber of fibers.reverse()) fiber.dispose() })

  // Real subprocess runtime; spawn is wrapped only to collect child handles so
  // teardown can prove every actual vtx process exited. Children stay real.
  const real = ctx.get('subprocess')
  const handles = []
  const subprocess = {
    resolveExecutable: (...args) => real.resolveExecutable(...args),
    spawn(spec) { const child = real.spawn(spec); handles.push(child); return child },
  }

  const sessionOne = { id: 'one' }, sessionTwo = { id: 'two' }
  const sessions = new Map([['one', sessionOne], ['two', sessionTwo]])
  const cwds = { one: { value: dir }, two: { value: dirTwo } }
  const workingDirectory = {
    get: session => cwds[session.id].value,
    async ensure(agent, signal) {
      signal?.throwIfAborted()
      return cwds[agent.session.id].value
    },
  }

  const host = createPreviewHost({ subprocess, fs: localFs(), sessions, workingDirectory },
    { vtxExecutable: process.env.SIDEBAR_REAL_VTX })
  t.after(() => host.dispose())

  const tools = createTimelineTools(host.agent)
  const disposers = tools.map(tool => ctx.tools.register(tool))

  const transcript = []
  let sequence = 0
  async function call(name, args, session = sessionOne) {
    // dsh 0.2.2 batch contract: calls carry raw model JSON text (exactly what
    // an assistant emits) and settle per call; the single-call batch here keeps
    // the same one-call-per-execute shape the previous per-call API had.
    const callId = ToolCallId(`agent-runtime-${label}-${sequence++}`)
    const batch = await ctx.tools.execute({
      calls: [{ id: callId, name, arguments: stringifyJSON(args) }],
      signal: new AbortController().signal,
      agent: { session },
    })
    assert.equal(batch.results.length, 1, `expected exactly one settled call for ${name}`)
    assert.equal(batch.results[0].callId, callId, 'results must be keyed by the submitted call id')
    const result = batch.results[0].result
    const text = result.isError ? JSON.stringify(result.error) : result.value
    transcript.push({ kind: 'tool', name, args, isError: result.isError, result: text })
    return result
  }
  const ok = result => {
    assert.equal(result.isError, false, `tool failed: ${JSON.stringify(result.error)}`)
    return result.value
  }
  const fails = (result, pattern) => {
    assert.equal(result.isError, true, `expected failure, got: ${result.value}`)
    assert.match(JSON.stringify(result.error), pattern)
  }
  const readRaw = () => readFile(path, 'utf8')
  return {
    t, host, ctx, tools, disposers, handles, transcript, dir, path, dirTwo, cwds,
    call, ok, fails, readRaw, sessionOne, sessionTwo,
    async save() { await writeFile(join(dir, 'transcript.json'), stringifyJSON(transcript, 2)) },
  }
}

test('real ToolRuntime: lossless read, no proposal tools, teardown', { skip, timeout: 240_000 }, async t => {
  const env = await runtime(t, 'lifecycle')
  await t.test('legacy read: inspect/query/reference keep exact number tokens', async () => {
    const inspect = parseJSON(env.ok(await env.call('framivra_timeline_read', { timeline: 'timeline.json', view: 'inspect' })))
    assert.equal(inspect.ok, true)
    assert.match(inspect.result.content_version ?? '', /^sha256:[a-f0-9]{64}$/)
    assert.ok(inspect.result.document && stringifyJSON(inspect.result.document).includes('9007199254740993'),
      'inspect must keep the 2^53+1 asset_id token')
    const reread = env.ok(await env.call('framivra_timeline_read', { timeline: 'timeline.json', view: 'inspect' }))
    assert.ok(reread.includes('9007199254740993') && reread.includes('-9007199254740993'),
      'inspect must keep the JS-unsafe integer tokens')
    const query = parseJSON(env.ok(await env.call('framivra_timeline_read',
      { timeline: 'timeline.json', view: 'query', query_json: stringifyJSON({ family: 'video' }) })))
    assert.equal(query.ok, true)
    const reference = parseJSON(env.ok(await env.call('framivra_timeline_read', {
      timeline: 'timeline.json', view: 'reference',
      query_json: stringifyJSON({ block_addresses: ['/video_tracks/0/clips/0'], expected_content_version: inspect.result.content_version }),
    })))
    assert.ok((await env.readRaw()).includes(reference.result.selection.blocks[0].raw),
      'reference raw must be an exact on-disk substring')
  })

  await t.test('proposal tools are unregistered and cannot touch the timeline', async () => {
    const before = await env.readRaw()
    assert.deepEqual(env.tools.map(tool => tool.name), ['framivra_timeline_read'])
    for (const name of ['framivra_timeline_propose', 'framivra_proposal_status']) {
      env.fails(await env.call(name, { timeline: 'timeline.json' }), /UNKNOWN_TOOL|unknown tool/i)
    }
    assert.equal(await env.readRaw(), before)
  })

  await t.test('teardown: unregister hides the tools, disposal refuses service, children are reaped', async () => {
    for (const dispose of env.disposers) dispose()
    env.fails(await env.call('framivra_timeline_read', { timeline: 'timeline.json', view: 'inspect' }), /UNKNOWN_TOOL|unknown tool/i)
    const re = env.ctx.tools.register(env.tools[0])
    await env.host.dispose()
    env.fails(await env.call('framivra_timeline_read', { timeline: 'timeline.json', view: 'inspect' }),
      /Timeline tools are unavailable after plugin disposal/)
    re()
    await Promise.all(env.handles.map(handle => handle.waitForExit(AbortSignal.timeout(5000))))
    assert.ok(true, `all ${env.handles.length} real vtx children exited after disposal`)
  })
  await env.save()
})

test('real ToolRuntime: session-scoped reads follow the current workspace', { skip, timeout: 240_000 }, async t => {
  const env = await runtime(t, 'isolation')
  // Distinct document bytes make accidental cross-session reads observable.
  const otherPath = join(env.dirTwo, 'timeline.json')
  await writeFile(otherPath, legacyDocumentText().replace('9007199254740993', '9007199254740995'))
  const first = env.ok(await env.call('framivra_timeline_read', { timeline: 'timeline.json', view: 'inspect' }, env.sessionOne))
  const other = env.ok(await env.call('framivra_timeline_read', { timeline: 'timeline.json', view: 'inspect' }, env.sessionTwo))
  assert.ok(first.includes('9007199254740993'))
  assert.ok(other.includes('9007199254740995'))
  assert.notEqual(parseJSON(first).result.content_version, parseJSON(other).result.content_version)
  env.cwds.one.value = env.dirTwo
  const moved = env.ok(await env.call('framivra_timeline_read', { timeline: 'timeline.json', view: 'inspect' }, env.sessionOne))
  assert.equal(parseJSON(moved).result.content_version, parseJSON(other).result.content_version)
  await env.save()
})
