// REAL Loader/agent-loop acceptance over the shipped headless profile:
// bootProductionProfile composes the actual profile bundles through the Loader,
// a deterministic fixture model (registered as provider "mock") drives real
// agent turns that call the plugin's three native tools, and the human side
// confirms the proposal through the same connection Fetch routes the sidebar
// registers. No external LLM API, no credentials, no real model bill.
//
// Boundaries (deliberate, documented): the plugin entry is mounted from its
// source path (src/index.mjs) through the real Loader — the extracted-ZIP
// install path is exercised separately in the Web UI flow; the `connection`
// carrier is a test-provided minimal service implementing exactly the
// fetch.register surface the plugin consumes (the sidebar-host Fetch
// transport verbatim, no authentication claim), because the headless profile
// ships no Web connection carrier — any other connection consumer fails
// loudly on the missing method instead of being no-op'd.
import test from 'node:test'
import assert from 'node:assert/strict'
import { execFile } from 'node:child_process'
import { mkdir, mkdtemp, writeFile, readFile, readdir } from 'node:fs/promises'
import { join } from 'node:path'
import { pathToFileURL, fileURLToPath } from 'node:url'
import { dshEntryShape, resolveDshEntry } from './dsh-public-sdk.mjs'

const root = process.env.SIDEBAR_DSH_ROOT
const vtx = process.env.SIDEBAR_REAL_VTX
const evidenceRoot = process.env.SIDEBAR_EVIDENCE_DIR
const skip = !root || !vtx || !evidenceRoot ? 'set SIDEBAR_DSH_ROOT / SIDEBAR_REAL_VTX / SIDEBAR_EVIDENCE_DIR' : false
const here = fileURLToPath(new URL('.', import.meta.url))
const sidebarDir = join(here, '..')

// Sidebar-local imports only; nothing here resolves through SIDEBAR_DSH_ROOT,
// so a plain `node --test` without the environment skips cleanly. The DSH
// worktree imports happen inside the test callback instead.
const { parseJSON, stringifyJSON } = await import(pathToFileURL(join(sidebarDir, 'src/json.mjs')))
const { fixture } = await import(pathToFileURL(join(sidebarDir, 'tests/manual-env.mjs')))

/** Legacy (unmanaged) adopt-compatible document, byte-exact number tokens. */
function legacyDocumentText() {
  const doc = fixture()
  for (const track of [...doc.video_tracks, ...doc.audio_tracks]) {
    delete track.id
    for (const block of track.clips ?? track.events) delete block.id
  }
  for (const sub of doc.subtitles) delete sub.id
  return stringifyJSON(doc)
}

const CONTROL = '/api/framivra/control'

/**
 * Deterministic fixture model body. Registered as provider "mock"; scripted by
 * the turn tag and the number of tool results already in the conversation.
 * Every model request is appended to MOCK_CONTEXT_PATH for context-persistence
 * assertions. Written as a generated file next to the overlay so the Loader
 * anchors it exactly like any test fixture plugin.
 */
function mockLlmSource(dshLlmUrl) {
  return [
    `import { LlmAdapter, ToolCallId } from ${JSON.stringify(dshLlmUrl)}`,
    `import { appendFileSync } from 'node:fs'`,
    ``,
    `class FramivraFixtureAdapter extends LlmAdapter {`,
    `  async * stream(options) {`,
    `    const firstUser = options.messages.findLast(m => m.role === 'user')?.content?.map(b => b.text ?? '').join('') ?? ''`,
    `    const turnTwo = firstUser.includes('TURN-TWO')`,
    `    const results = options.messages.filter(m => m.role === 'tool')`,
    `    const textOf = m => (m?.content ?? []).filter(b => b.type === 'text').map(b => b.text).join('')`,
    `    appendFileSync(process.env.MOCK_CONTEXT_PATH, JSON.stringify({`,
    `      turn: turnTwo ? 2 : 1,`,
    `      roles: options.messages.map(m => m.role),`,
    `      tools: (options.tools ?? []).map(tool => tool.name),`,
    `      loop: options.toolHistory !== undefined,`,
    `      toolResults: results.map(textOf),`,
    `    }) + '\\n')`,
    `    const call = (id, name, args) => {`,
    `      const payload = JSON.stringify(args)`,
    `      return [`,
    `        { type: 'block-start', index: 0, blockType: 'tool-call' },`,
    `        { type: 'tool-call-delta', index: 0, id: ToolCallId(id), name, argumentsDelta: payload },`,
    `        { type: 'block-end', index: 0, block: { type: 'tool-call', id: ToolCallId(id), name, arguments: payload } },`,
    `        { type: 'usage', usage: { inputTokens: 11, outputTokens: 3 } },`,
    `      ]`,
    `    }`,
    `    const finish = reply => [`,
    `      { type: 'block-start', index: 0, blockType: 'text' },`,
    `      { type: 'text-delta', index: 0, text: reply },`,
    `      { type: 'block-end', index: 0, block: { type: 'text', text: reply } },`,
    `      { type: 'usage', usage: { inputTokens: 7, outputTokens: 5 } },`,
    `      { type: 'finish', reason: { kind: 'stop' } },`,
    `    ]`,
    `    if (!turnTwo) {`,
    `      if (results.length === 0) {`,
    `        yield* call('profile-read', 'framivra_timeline_read', { timeline: 'timeline.json', view: 'inspect' })`,
    `        yield { type: 'finish', reason: { kind: 'tool-calls' } }`,
    `        return`,
    `      }`,
    `      if (results.length === 1) {`,
    `        const version = /"content_version"\\s*:\\s*"(sha256:[0-9a-f]{64})"/.exec(textOf(results[0]))?.[1]`,
    `        if (version === undefined) { yield* finish('READ_FAILED no content version'); return }`,
    `        yield* call('profile-propose', 'framivra_timeline_propose', {`,
    `          timeline: 'timeline.json', expected_content_version: version,`,
    `          operations_json: '[{"op":"project.resize","duration_frames":130}]',`,
    `        })`,
    `        yield { type: 'finish', reason: { kind: 'tool-calls' } }`,
    `        return`,
    `      }`,
    `      const proposed = textOf(results.at(-1))`,
    `      const proposalId = /"proposal_id"\\s*:\\s*"([^"]+)"/.exec(proposed)?.[1]`,
    `      const opId = /"op_id"\\s*:\\s*"([^"]+)"/.exec(proposed)?.[1]`,
    `      yield* finish(proposalId && opId ? \`PROPOSAL \${proposalId} \${opId}\` : 'PROPOSE_FAILED')`,
    `      return`,
    `    }`,
    `    if (results.length < 3) {`,
    `      yield* call('profile-status', 'framivra_proposal_status', { timeline: 'timeline.json' })`,
    `      yield { type: 'finish', reason: { kind: 'tool-calls' } }`,
    `      return`,
    `    }`,
    `    const status = textOf(results.at(-1))`,
    `    const state = /"state"\\s*:\\s*"([^"]+)"/.exec(status)?.[1]`,
    `    const opId = /"op_id"\\s*:\\s*"([^"]+)"/.exec(status)?.[1]`,
    `    yield* finish(state ? \`STATUS \${state} \${opId ?? 'none'}\` : 'STATUS_FAILED')`,
    `  }`,
    `}`,
    ``,
    `export const name = 'framivra-profile-mock-llm'`,
    `export const inject = ['llm']`,
    `export function apply(ctx) { ctx.llm.registerAdapter(['mock'], new FramivraFixtureAdapter()) }`,
    ``,
  ].join('\n')
}

test('real Loader composition: deterministic model turns drive the native timeline tools end to end', { skip, timeout: 420_000 }, async t => {
  // DSH-worktree imports resolve inside the test body so the module itself
  // loads (and skips) without SIDEBAR_DSH_ROOT.
  // DSH-worktree imports resolve inside the test body so the module itself
  // loads (and skips) without SIDEBAR_DSH_ROOT. A source checkout keeps the
  // original test-support fixture; the official npm distribution (which does
  // not ship test-support) boots the same composition through the thin
  // public-API harness with the npm package.json as install anchor. Same
  // profile, overlays, assertions, and turn semantics either way.
  let bootProductionProfile, runFixtureTurn, harnessAnchor = null
  if (dshEntryShape(root) === 'monorepo') {
    const dshLib = rel => pathToFileURL(join(root, rel)).href
    ;({ bootProductionProfile } = await import(dshLib('packages/test-support/loader-smoke/tests/fixtures/production-profile.ts')))
    ;({ runFixtureTurn } = await import(dshLib('packages/test-support/loader-smoke/src/agent-turn.ts')))
  } else {
    ;({ bootProductionProfile, runFixtureTurn } = await import('./dsh-profile-harness.mjs'))
    harnessAnchor = join(root, 'package.json')
  }

  const evidence = await mkdtemp(join(evidenceRoot, 'profile-agent-'))
  const workspace = join(evidence, 'workspace')
  const timelinePath = join(workspace, 'timeline.json')
  await mkdir(workspace, { recursive: true })
  await mkdir(join(evidence, 'dsh-home'), { recursive: true })
  await mkdir(join(evidence, 'agents-home'), { recursive: true })
  await writeFile(timelinePath, legacyDocumentText())
  await writeFile(join(evidence, 'mock-llm.mjs'), mockLlmSource(pathToFileURL(resolveDshEntry(root, 'llm')).href))
  const contextLog = join(evidence, 'model-context.jsonl')
  await writeFile(contextLog, '')

  process.env.DSH_HOME = join(evidence, 'dsh-home')
  process.env.DSH_AGENTS_HOME = join(evidence, 'agents-home')
  process.env.DSH_TELEMETRY_DISABLED = '1'
  process.env.MOCK_CONTEXT_PATH = contextLog

  // Minimal test-provided connection carrier: exactly the fetch.register
  // surface the plugin consumes. No other method exists — any other consumer
  // of the "connection" service fails loudly here instead of being no-op'd.
  const routes = {}
  const connection = {
    fetch: {
      register(route) {
        if (routes[route.path] !== undefined) throw new Error(`connection test carrier: duplicate route ${route.path}`)
        routes[route.path] = route.fetch
        return () => { delete routes[route.path] }
      },
    },
  }

  const overlay = join(evidence, 'overlay.patch.yml')
  await writeFile(overlay, [
    '# Test-only deterministic composition over the shipped headless profile.',
    '- id: headless-startup',
    '  disabled: true',
    '- id: headless-runner',
    '  disabled: true',
    '- id: agent-loop',
    '  config:',
    '    agents:',
    '      - id: main',
    '        provider: mock',
    '        model: framivra-fixture',
    `        cwd: ${JSON.stringify(workspace)}`,
    '- id: session-persistence-jsonl',
    '  config:',
    `    root: ${JSON.stringify(join(evidence, 'sessions'))}`,
    '    compression: none',
    '- id: fs-sandbox',
    '  config:',
    `    cwd: ${JSON.stringify(workspace)}`,
    '- insert:',
    '    - id: framivra-profile-mock-llm',
    "      name: './mock-llm.mjs'",
    '    - id: framivra-dsh-sidebar',
    `      name: ${JSON.stringify(pathToFileURL(join(sidebarDir, 'src/index.mjs')).href)}`,
    '      config:',
    `        vtxExecutable: ${JSON.stringify(vtx)}`,
    '',
  ].join('\n'))

  const ctx = await (harnessAnchor
    ? bootProductionProfile({
        binName: 'framivra-profile-agent',
        profile: 'headless',
        overlayPaths: [overlay],
        prepare(context) { context.provide('connection', connection) },
      }, harnessAnchor, root)
    : bootProductionProfile({
        binName: 'framivra-profile-agent',
        profile: 'headless',
        overlayPaths: [overlay],
        prepare(context) { context.provide('connection', connection) },
      }))
  // Single-dispose guard: the flow below disposes on success; t.after is the
  // failure-path net. Both share the flag, so a dispose error is never
  // swallowed as a harmless second failure.
  let disposed = false
  const dispose = async () => { if (!disposed) { disposed = true; await ctx.fiber.dispose() } }
  t.after(dispose)

  // The plugin mounted through the real Loader: its Fetch routes are live.
  const control = routes[CONTROL]
  assert.equal(typeof control, 'function', `plugin control route missing; routes: ${Object.keys(routes).join(', ')}`)
  const call = async payload => {
    const response = await control(new Request('http://owner' + CONTROL, { method: 'POST', body: JSON.stringify(payload) }))
    return { status: response.status, value: await response.json() }
  }
  const ok = async payload => {
    const { status, value } = await call(payload)
    assert.equal(status, 200, `${payload.op}: ${stringifyJSON(value)}`)
    return value
  }

  // Wait for the configured root agent exactly like the official driver.
  const registry = ctx.get('agents')
  if (registry.roots().length === 0) {
    await new Promise(resolve => {
      const dispose = ctx.on('agent/created', () => { dispose(); resolve() })
    })
  }
  const [agent] = registry.roots()
  assert.ok(agent, 'configured root agent must publish')
  await agent.whenIdle()
  const sessionId = agent.session.id

  // Human project setup before the first turn: adopt the legacy timeline
  // through the sidebar-host control API (the user's own flow).
  const opened = await ok({ sessionId, op: 'open', timeline: 'timeline.json' })
  const authorized = await ok({ sessionId, op: 'authorize-write', grant: opened.grant })
  const adopted = await ok({
    sessionId, op: 'adopt', grant: opened.grant, writeGrant: authorized.writeGrant,
    base_revision: opened.snapshot.content_version, confirmation: 'stable-ids-and-history',
  })
  assert.equal(adopted.ok, true)
  await ok({ sessionId, op: 'close', grant: opened.grant })
  const bytesBeforeTurn = await readFile(timelinePath, 'utf8')

  // Turn one: real agent turn; the model reads, then proposes (zero write).
  const events = []
  const remember = (_sid, event) => { events.push(event) }
  const first = await runFixtureTurn(ctx, { task: 'TURN-ONE inspect the timeline and propose the resize', onEvent: remember }, harnessAnchor ? root : undefined)
  t.diagnostic(`turn one output: ${first.output}`)
  const proposalMatch = /^PROPOSAL ([0-9a-f-]+) ([0-9a-f-]+)$/.exec(first.output ?? '')
  assert.ok(proposalMatch, `turn one must report the parked proposal, got: ${first.output}`)
  const [, proposalId, modelOpId] = proposalMatch
  assert.equal(await readFile(timelinePath, 'utf8'), bytesBeforeTurn, 'propose is zero-write: bytes unchanged')

  // Human confirmation through the sidebar-host control API.
  const reopened = await ok({ sessionId, op: 'open', timeline: 'timeline.json' })
  const reauthorized = await ok({ sessionId, op: 'authorize-write', grant: reopened.grant })
  const confirmed = await ok({
    sessionId, op: 'proposal-confirm', grant: reopened.grant, writeGrant: reauthorized.writeGrant, proposalId,
  })
  assert.equal(confirmed.ok !== false, true, `confirm failed: ${stringifyJSON(confirmed)}`)
  const confirmedOpId = confirmed.proposal?.confirmation?.op_id ?? confirmed.proposal?.outcome?.op_id
  assert.equal(confirmedOpId, modelOpId, `confirmed op id matches the frozen model plan; envelope: ${stringifyJSON(confirmed)}`)
  await ok({ sessionId, op: 'close', grant: reopened.grant })

  const disk = parseJSON(await readFile(timelinePath, 'utf8'))
  assert.equal(disk.project.duration_frames, 130, 'vtx really applied the resize')
  assert.equal(disk._vtx.head, modelOpId, 'kernel head is the confirmed op id')

  // Turn two: same session, real context; the model asks for status itself.
  const second = await runFixtureTurn(ctx, { task: 'TURN-TWO check what the user confirmed', onEvent: remember }, harnessAnchor ? root : undefined)
  t.diagnostic(`turn two output: ${second.output}`)
  assert.match(second.output ?? '', /^STATUS applied [0-9a-f-]+$/)
  const statusOpId = /^STATUS applied ([0-9a-f-]+)$/.exec(second.output)[1]
  assert.equal(statusOpId, modelOpId, 'status reports the same frozen op id')

  // Model-context persistence: turn two saw turn one's tool results verbatim.
  const requests = (await readFile(contextLog, 'utf8')).trim().split('\n').map(parseJSON)
  assert.equal(requests.length >= 3, true, 'at least read/propose/status model requests')
  // Tool projection: every agent-loop model request (marked by toolHistory;
  // side requests like the session titler legitimately carry no tools)
  // advertised exactly the plugin's three framivra tools — no
  // commit/adopt/authorize escape hatch. Other host tools (bash, fs, …) are
  // legal and not asserted here.
  const loopRequests = requests.filter(r => r.loop)
  assert.equal(loopRequests.length >= 3, true, 'at least read/propose/status agent-loop model requests')
  for (const request of loopRequests) {
    const framivraTools = request.tools.filter(name => name.startsWith('framivra_')).sort()
    assert.deepEqual(framivraTools, ['framivra_proposal_status', 'framivra_timeline_propose', 'framivra_timeline_read'],
      `agent-loop request advertised the plugin tools exactly; all tools: ${request.tools.join(', ')}`)
  }
  const turnTwoRequests = requests.filter(r => r.turn === 2)
  assert.equal(turnTwoRequests.length >= 2, true)
  assert.equal(turnTwoRequests[0].toolResults.some(text => text.includes('framivra') && text.includes(proposalId)), true,
    'turn two context carried the parked proposal result')
  assert.equal(requests.some(r => r.toolResults.some(text => text.includes('130'))), true)

  // Durable session log: tool calls and results are persisted projections.
  const sessionText = await readSessionLog(join(evidence, 'sessions'), first.sessionId)
  for (const token of ['framivra_timeline_read', 'framivra_timeline_propose', 'framivra_proposal_status', modelOpId, proposalId]) {
    assert.equal(sessionText.includes(token), true, `session log must persist ${token}`)
  }
  assert.equal(events.some(e => JSON.stringify(e).includes('framivra_timeline_propose')), true)

  // Teardown: the Loader disposal unregisters the routes and the plugin host
  // reaps every managed vtx child.
  await dispose()
  assert.equal(Object.keys(routes).length, 0, 'connection routes unregistered on dispose')
  const live = await new Promise(resolve => execFile('pgrep', ['-f', timelinePath], error => {
    resolve(error ? (error.code === 1 ? [] : ['pgrep-failed']) : ['live-children'])
  }))
  assert.deepEqual(live, [], 'no vtx child may outlive the composition')
})

/** Concatenate every persisted JSONL line for one session id. */
async function readSessionLog(root, sessionId) {
  const parts = []
  const visit = async dir => {
    for (const entry of await readdir(dir, { withFileTypes: true })) {
      const path = join(dir, entry.name)
      if (entry.isDirectory()) await visit(path)
      else if (entry.name.endsWith('.jsonl') && path.includes(sessionId)) parts.push(await readFile(path, 'utf8'))
    }
  }
  await visit(root)
  assert.equal(parts.length > 0, true, 'session persistence wrote at least one JSONL file')
  return parts.join('\n')
}
