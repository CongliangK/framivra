/**
 * Real-DSH ToolRuntime acceptance for the three Framivra model tools.
 *
 * Unlike tools.test.mjs (direct tool.execute with a synthetic exec) and
 * manual-real.test.mjs (UI control ops only), this file mounts the actual
 * public DSH ToolRuntime service from SIDEBAR_DSH_ROOT (built lib) with a real
 * Cordis Context + LocalSubprocessRuntime, registers the tools exactly as the
 * plugin does, and drives them through ctx.tools.execute with a live agent
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
import { pathToFileURL } from 'node:url'
import { createPreviewHost } from '../src/host.mjs'
import { createTimelineTools } from '../src/tools.mjs'
import { parseJSON, stringifyJSON } from '../src/json.mjs'
import { fixture, localFs } from './manual-env.mjs'

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

  const dsh = rel => import(pathToFileURL(join(root, rel)))
  const { Context } = await dsh('vendor/cordis/lib/index.js')
  const { LocalSubprocessRuntime } = await dsh('packages/subprocess/subprocess-local/lib/index.js')
  const SystemPrompt = (await dsh('packages/core/system-prompt/lib/index.js')).default
  const ToolRuntime = (await dsh('packages/core/tools/lib/index.js')).default
  const { ToolCallId } = await dsh('packages/llm/llm/lib/index.js')

  const ctx = new Context()
  const fibers = []
  for (const plugin of [LocalSubprocessRuntime, SystemPrompt, ToolRuntime]) {
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
    const result = await ctx.tools.execute({
      name, arguments: args,
      callId: ToolCallId(`agent-runtime-${label}-${sequence++}`),
      signal: new AbortController().signal,
      agent: { session },
    })
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
  async function control(sessionId, data) {
    const response = await host.control(new Request('http://owner/api/framivra/control', {
      method: 'POST', body: stringifyJSON({ sessionId, ...data }), signal: new AbortController().signal,
    }))
    const answer = parseJSON(await response.text())
    transcript.push({ kind: 'control', sessionId, data, status: response.status, response: answer })
    return { status: response.status, answer }
  }
  const controlOk = async (sessionId, data) => {
    const { status, answer } = await control(sessionId, data)
    assert.equal(status, 200, `control ${data.op} failed: ${stringifyJSON(answer)}`)
    return answer
  }
  const readRaw = () => readFile(path, 'utf8')
  return {
    t, host, ctx, tools, disposers, handles, transcript, dir, path, dirTwo, cwds,
    call, ok, fails, control, controlOk, readRaw, sessionOne, sessionTwo,
    async save() { await writeFile(join(dir, 'transcript.json'), stringifyJSON(transcript, 2)) },
  }
}

// Test-side UI helpers mirroring the sidebar's own control sequence.
async function adopt(env, sessionId = 'one') {
  const opened = await env.controlOk(sessionId, { op: 'open', timeline: 'timeline.json' })
  const write = await env.controlOk(sessionId, { op: 'authorize-write', grant: opened.grant })
  const adopted = await env.controlOk(sessionId, {
    op: 'adopt', grant: opened.grant, writeGrant: write.writeGrant,
    base_revision: opened.snapshot.content_version, confirmation: 'stable-ids-and-history',
  })
  assert.equal(adopted.ok, true, `adopt failed: ${stringifyJSON(adopted)}`)
  await env.controlOk(sessionId, { op: 'close', grant: opened.grant })
  return adopted
}

test('real ToolRuntime: read lossless, legacy rejection, adopt→propose→UI confirm→applied, CAS/dedupe/reject/stale', { skip, timeout: 240_000 }, async t => {
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

  await t.test('legacy propose is rejected by the kernel, file untouched', async () => {
    const inspect = parseJSON(env.ok(await env.call('framivra_timeline_read', { timeline: 'timeline.json', view: 'inspect' })))
    const before = await env.readRaw()
    const rejected = parseJSON(env.ok(await env.call('framivra_timeline_propose', {
      timeline: 'timeline.json', expected_content_version: inspect.result.content_version,
      operations_json: stringifyJSON([{ op: 'project.resize', duration_frames: 130 }]),
    })))
    assert.equal(rejected.ok, false)
    assert.equal(rejected.state, 'plan_rejected')
    assert.equal(rejected.plan.code, 'input.structure')
    assert.match(rejected.plan.message, /explicit adopt required/)
    assert.equal(await env.readRaw(), before, 'a rejected plan must not touch the file')
  })

  await t.test('explicit test-side adopt converts the fixture, tokens survive', async () => {
    await adopt(env)
    const reread = env.ok(await env.call('framivra_timeline_read', { timeline: 'timeline.json', view: 'inspect' }))
    assert.ok(reread.includes('9007199254740993') && reread.includes('-9007199254740993'),
      'adopt must preserve exact number tokens')
  })

  let proposal, rawBefore
  await t.test('propose is zero-write and deduplicated', async () => {
    const inspect = parseJSON(env.ok(await env.call('framivra_timeline_read', { timeline: 'timeline.json', view: 'inspect' })))
    rawBefore = await env.readRaw()
    const args = {
      timeline: 'timeline.json', expected_content_version: inspect.result.content_version,
      operations_json: stringifyJSON([{ op: 'project.resize', duration_frames: 130 }]),
    }
    proposal = parseJSON(env.ok(await env.call('framivra_timeline_propose', args))).proposal
    assert.equal(proposal.state, 'pending')
    assert.match(proposal.plan.submitted_request.op_id, /^[a-f0-9-]{36}$/)
    assert.equal(await env.readRaw(), rawBefore, 'a plan is zero-write')
    const duplicate = parseJSON(env.ok(await env.call('framivra_timeline_propose', args)))
    assert.equal(duplicate.duplicate, true)
    assert.equal(duplicate.proposal.proposal_id, proposal.proposal_id)
    assert.equal(await env.readRaw(), rawBefore)
  })

  let opId
  await t.test('UI authorize-write + proposal-confirm commits the exact frozen request', async () => {
    const opened = await env.controlOk('one', { op: 'open', timeline: 'timeline.json' })
    const write = await env.controlOk('one', { op: 'authorize-write', grant: opened.grant })
    const confirmed = await env.controlOk('one', {
      op: 'proposal-confirm', grant: opened.grant, writeGrant: write.writeGrant, proposalId: proposal.proposal_id,
    })
    assert.equal(confirmed.ok, true)
    assert.equal(confirmed.proposal.state, 'applied')
    opId = confirmed.proposal.confirmation.op_id
    assert.equal(opId, proposal.plan.submitted_request.op_id, 'confirmed op_id must be the frozen plan op_id')
    assert.deepEqual(confirmed.proposal.outcome.submitted_request, proposal.plan.submitted_request,
      'no body replacement, no rebase, no fresh id')
    assert.equal(confirmed.proposal.outcome.op_id, opId)
    const raw = await env.readRaw()
    assert.notEqual(raw, rawBefore, 'the real file changed')
    const doc = parseJSON(raw)
    assert.equal(doc._vtx.head, opId, 'kernel head is the confirmed op_id')
    assert.equal(doc.project.duration_frames, 130)
    await env.controlOk('one', { op: 'close', grant: opened.grant })
  })

  await t.test('model sees applied; double-confirm and duplicate paths stay single-use', async () => {
    const status = parseJSON(env.ok(await env.call('framivra_proposal_status', { timeline: 'timeline.json' })))
    assert.equal(status.proposal.state, 'applied')
    assert.equal(status.proposal.outcome.op_id, opId)
    const opened = await env.controlOk('one', { op: 'open', timeline: 'timeline.json' })
    const write = await env.controlOk('one', { op: 'authorize-write', grant: opened.grant })
    const again = await env.control('one', {
      op: 'proposal-confirm', grant: opened.grant, writeGrant: write.writeGrant, proposalId: proposal.proposal_id,
    })
    assert.equal(again.status, 409)
    assert.equal(again.answer.error, 'PROPOSAL_CONSUMED')
    await env.controlOk('one', { op: 'close', grant: opened.grant })
  })

  await t.test('reject path: pending proposal refused without a write, then rejected by the user', async () => {
    const rawAfterCommit = await env.readRaw()
    const inspect = parseJSON(env.ok(await env.call('framivra_timeline_read', { timeline: 'timeline.json', view: 'inspect' })))
    const second = parseJSON(env.ok(await env.call('framivra_timeline_propose', {
      timeline: 'timeline.json', expected_content_version: inspect.result.content_version,
      // Address-free op: after adopt, track-addressed ops need the regenerated
      // stable track ids, and the reject path must not depend on them.
      operations_json: stringifyJSON([{ op: 'project.resize', duration_frames: 140 }]),
    }))).proposal
    assert.equal(second.state, 'pending')
    assert.equal(await env.readRaw(), rawAfterCommit, 'reject path is still zero-write until confirmed')
    const opened = await env.controlOk('one', { op: 'open', timeline: 'timeline.json' })
    const rejected = await env.controlOk('one', { op: 'proposal-reject', grant: opened.grant, proposalId: second.proposal_id })
    assert.equal(rejected.proposal.state, 'rejected')
    await env.controlOk('one', { op: 'close', grant: opened.grant })
    const status = parseJSON(env.ok(await env.call('framivra_proposal_status', { timeline: 'timeline.json', proposal_id: second.proposal_id })))
    assert.equal(status.proposal.state, 'rejected')
    assert.equal(await env.readRaw(), rawAfterCommit, 'a rejected proposal never writes')
  })

  await t.test('stale bases are refused, never rebased', async () => {
    const stale = await env.call('framivra_timeline_propose', {
      timeline: 'timeline.json', expected_content_version: proposal.base_revision,
      operations_json: stringifyJSON([{ op: 'project.resize', duration_frames: 140 }]),
    })
    env.fails(stale, /Timeline changed; read it again before proposing/)
    const malformed = await env.call('framivra_timeline_propose', {
      timeline: 'timeline.json', expected_content_version: 'not-a-version',
      operations_json: stringifyJSON([{ op: 'project.resize', duration_frames: 140 }]),
    })
    env.fails(malformed, /exact content version/)
  })

  await t.test('teardown: unregister hides the tools, disposal refuses service, children are reaped', async () => {
    for (const dispose of env.disposers) dispose()
    env.fails(await env.call('framivra_timeline_read', { timeline: 'timeline.json', view: 'inspect' }), /UNKNOWN_TOOL|unknown tool/i)
    const re = env.ctx.tools.register(env.tools[0])
    env.host.dispose()
    env.fails(await env.call('framivra_timeline_read', { timeline: 'timeline.json', view: 'inspect' }),
      /Timeline tools are unavailable after plugin disposal/)
    re()
    await Promise.all(env.handles.map(handle => handle.waitForExit(AbortSignal.timeout(5000))))
    assert.ok(true, `all ${env.handles.length} real vtx children exited after disposal`)
  })
  await env.save()
})

test('real ToolRuntime: session/cwd isolation and drift invalidation', { skip, timeout: 240_000 }, async t => {
  const env = await runtime(t, 'isolation')
  await adopt(env, 'one')
  await adopt(env, 'two')

  const inspect = parseJSON(env.ok(await env.call('framivra_timeline_read',
    { timeline: 'timeline.json', view: 'inspect' }, env.sessionOne)))
  const proposal = parseJSON(env.ok(await env.call('framivra_timeline_propose', {
    timeline: 'timeline.json', expected_content_version: inspect.result.content_version,
    operations_json: stringifyJSON([{ op: 'project.resize', duration_frames: 150 }]),
  }, env.sessionOne))).proposal
  assert.equal(proposal.state, 'pending')

  await t.test('another session reads only its own workspace copy', async () => {
    const other = parseJSON(env.ok(await env.call('framivra_timeline_read',
      { timeline: 'timeline.json', view: 'inspect' }, env.sessionTwo)))
    assert.equal(other.ok, true)
    assert.notEqual(other.result.content_version, proposal.base_revision,
      'session two sees its own adopted file, not session one’s timeline')
  })

  await t.test('another session cannot confirm or even see the proposal', async () => {
    const opened = await env.controlOk('two', { op: 'open', timeline: 'timeline.json' })
    const write = await env.controlOk('two', { op: 'authorize-write', grant: opened.grant })
    const cross = await env.control('two', {
      op: 'proposal-confirm', grant: opened.grant, writeGrant: write.writeGrant, proposalId: proposal.proposal_id,
    })
    assert.equal(cross.status, 404)
    assert.equal(cross.answer.error, 'BAD_PROPOSAL')
    await env.controlOk('two', { op: 'close', grant: opened.grant })
    const otherStatus = parseJSON(env.ok(await env.call('framivra_proposal_status',
      { timeline: 'timeline.json' }, env.sessionTwo)))
    assert.equal(otherStatus.proposal, null, 'receipts are session-scoped')
  })

  await t.test('cwd drift invalidates pending proposals for the drifting session', async () => {
    const before = parseJSON(env.ok(await env.call('framivra_proposal_status', { timeline: 'timeline.json' })))
    assert.equal(before.proposal.state, 'pending')
    env.cwds.one.value = env.dirTwo
    const drifted = parseJSON(env.ok(await env.call('framivra_proposal_status', { timeline: 'timeline.json' })))
    assert.equal(drifted.proposal, null, 'a pending receipt does not follow the session into another workspace')
    env.cwds.one.value = env.dir
    const after = parseJSON(env.ok(await env.call('framivra_proposal_status', { timeline: 'timeline.json' })))
    assert.equal(after.proposal.state, 'invalidated', 'the drift invalidated the pending proposal')
  })
  await env.save()
})
