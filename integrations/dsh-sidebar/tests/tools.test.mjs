/**
 * Model-facing timeline tools contract tests. Pure in-process: no signals, no
 * subprocess kills, no real vtx. The Host adapter (host.agent) is stubbed with
 * call-recording fakes; envelope semantics stay with the Host.
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { createTimelineTools, registerTimelineTools } from '../src/tools.mjs'
import { AGENT_INPUT_LIMIT, AGENT_WIRE_LIMIT } from '../src/bounded.mjs'
import snapshot from './tools-schema.snapshot.json' with { type: 'json' }

const EXEC = Object.freeze({})

/** Recording fake for the host.agent adapter. */
function fakeAgent(results = {}) {
  const calls = { read: [], propose: [], status: [] }
  const defaults = {
    read: () => ({ schema: 'framivra.timeline.agent/v1', ok: true, view: 'inspect', content_version: 'sha256:' + 'a'.repeat(64) }),
    propose: () => ({ schema: 'framivra.timeline.agent/v1', ok: true, proposal_id: 'p-1', zero_write: true }),
    status: () => ({ schema: 'framivra.timeline.agent/v1', ok: true, proposals: [] }),
  }
  const agent = {}
  for (const op of ['read', 'propose', 'status']) {
    agent[op] = async (exec, args) => { calls[op].push({ exec, args }); return (results[op] ?? defaults[op])() }
  }
  return { agent, calls }
}

function byName(tools, name) { return tools.find(t => t.name === name) }

test('snapshot pins names, descriptions, parameter schemas, output schema', () => {
  const tools = createTimelineTools(fakeAgent().agent)
  assert.equal(tools.length, snapshot.tools.length)
  for (const pinned of snapshot.tools) {
    const tool = byName(tools, pinned.name)
    assert.ok(tool, `missing tool ${pinned.name}`)
    assert.equal(tool.description, pinned.description)
    assert.deepEqual(tool.parameters, pinned.parameters)
    assert.deepEqual(tool.output.schema, pinned.output_schema)
    assert.equal(typeof tool.output.render, 'function')
    assert.equal(typeof tool.execute, 'function')
  }
})

test('no mutating tool names and no grant/session/cwd parameters exist', () => {
  const tools = createTimelineTools(fakeAgent().agent)
  assert.deepEqual(tools.map(t => t.name).sort(),
    ['framivra_proposal_status', 'framivra_timeline_propose', 'framivra_timeline_read'])
  for (const tool of tools) {
    assert.ok(!/commit|adopt|undo|authorize|write|grant/i.test(tool.name), `${tool.name} leaks a write verb`)
    const keys = Object.keys(tool.parameters.properties)
    for (const forbidden of ['grant', 'writeGrant', 'session', 'sessionId', 'cwd', 'root', 'write_key']) {
      assert.ok(!keys.includes(forbidden), `${tool.name} accepts "${forbidden}"`)
    }
  }
})

test('argument validation rejects unknown, missing, mistyped, bad enum, non-object', async () => {
  const { agent } = fakeAgent()
  const tools = createTimelineTools(agent)
  const cases = [
    [byName(tools, 'framivra_timeline_read'), { timeline: 't', view: 'inspect', extra: 1 }],
    [byName(tools, 'framivra_timeline_read'), { timeline: 't' }],
    [byName(tools, 'framivra_timeline_read'), { timeline: 7, view: 'inspect' }],
    [byName(tools, 'framivra_timeline_read'), { timeline: 't', view: 'mutations' }],
    [byName(tools, 'framivra_timeline_read'), null],
    [byName(tools, 'framivra_timeline_propose'), { timeline: 't', expected_content_version: 'v' }],
    [byName(tools, 'framivra_timeline_propose'), { timeline: 't', operations_json: '[]', expected_content_version: 'v', writeGrant: 'x' }],
    [byName(tools, 'framivra_proposal_status'), {}],
    [byName(tools, 'framivra_proposal_status'), { timeline: 't', proposal_id: 4 }],
  ]
  for (const [tool, args] of cases) {
    await assert.rejects(tool.execute(args, EXEC), error => error.code === 'BAD_ARGS')
  }
})

test('empty strings rejected: timeline and json payloads', async () => {
  const tools = createTimelineTools(fakeAgent().agent)
  await assert.rejects(
    byName(tools, 'framivra_timeline_read').execute({ timeline: '', view: 'inspect' }, EXEC),
    error => error.code === 'BAD_ARGS')
  await assert.rejects(
    byName(tools, 'framivra_timeline_propose').execute({ timeline: 't', expected_content_version: 'v', operations_json: '' }, EXEC),
    error => error.code === 'BAD_ARGS')
})

test('agent receives exactly the declared args, json payloads verbatim', async () => {
  const { agent, calls } = fakeAgent()
  const tools = createTimelineTools(agent)
  const query = '{ "family": "video", "frames": [1e999, 9007199254740993] ,  "spaced":  0.30000000000000004 }'
  await byName(tools, 'framivra_timeline_read').execute({ timeline: 'demo.json', view: 'query', query_json: query }, EXEC)
  assert.deepEqual(calls.read[0].args, { timeline: 'demo.json', view: 'query', query_json: query })
  const ops = '[ {"op":"block.trim","amount":1e999} ,  {"op":"track.add"} ]'
  await byName(tools, 'framivra_timeline_propose').execute({ timeline: 'demo.json', expected_content_version: 'sha256:b', operations_json: ops }, EXEC)
  assert.deepEqual(calls.propose[0].args, { timeline: 'demo.json', expected_content_version: 'sha256:b', operations_json: ops })
  await byName(tools, 'framivra_proposal_status').execute({ timeline: 'demo.json' }, EXEC)
  assert.deepEqual(calls.status[0].args, { timeline: 'demo.json', proposal_id: undefined })
})

test('envelope numbers keep original tokens; render never round-trips', async () => {
  const envelope = {
    schema: 'framivra.timeline.agent/v1', ok: true,
    unsafe: JSON.rawJSON('9007199254740993'),
    huge: JSON.rawJSON('1e999'),
    decimal: JSON.rawJSON('0.30000000000000004'),
    negative_zero: JSON.rawJSON('-0'),
    safe_count: 42,
  }
  const tools = createTimelineTools(fakeAgent({ read: () => envelope }).agent)
  const value = await byName(tools, 'framivra_timeline_read').execute({ timeline: 't', view: 'inspect' }, EXEC)
  assert.equal(typeof value, 'string')
  for (const token of ['9007199254740993', '1e999', '0.30000000000000004', '-0']) {
    assert.ok(value.includes(token), `lost original token ${token}`)
  }
  const blocks = byName(tools, 'framivra_timeline_read').output.render({ timeline: 't', view: 'inspect' }, value)
  assert.deepEqual(blocks, [{ type: 'text', text: value }])
})

test('oversized envelope fails MODEL_OUTPUT_TOO_LARGE instead of truncating', async () => {
  const tools = createTimelineTools(fakeAgent({
    read: () => ({ schema: 'framivra.timeline.agent/v1', ok: true, blob: 'x'.repeat(AGENT_WIRE_LIMIT) }),
  }).agent)
  await assert.rejects(
    byName(tools, 'framivra_timeline_read').execute({ timeline: 't', view: 'inspect' }, EXEC),
    error => error.code === 'MODEL_OUTPUT_TOO_LARGE' && !/x{100}/.test(error.message))
})

test('oversized json payloads fail MODEL_INPUT_TOO_LARGE before reaching the agent', async () => {
  const { agent, calls } = fakeAgent()
  const tools = createTimelineTools(agent)
  const big = '[' + '"a",'.repeat(AGENT_INPUT_LIMIT / 2 + 8) + '"a"]'
  await assert.rejects(
    byName(tools, 'framivra_timeline_propose').execute({ timeline: 't', expected_content_version: 'v', operations_json: big }, EXEC),
    error => error.code === 'MODEL_INPUT_TOO_LARGE')
  assert.equal(calls.propose.length, 0)
})

test('bounds count UTF-8 bytes, not characters: multibyte input is rejected by byte size', async () => {
  const { agent, calls } = fakeAgent()
  const tools = createTimelineTools(agent)
  const mbTimeline = '时'.repeat(3000) // 3000 chars, 9000 UTF-8 bytes > 4096-byte timeline bound
  assert.equal(mbTimeline.length, 3000)
  await assert.rejects(
    byName(tools, 'framivra_timeline_read').execute({ timeline: mbTimeline, view: 'inspect' }, EXEC),
    error => error.code === 'MODEL_INPUT_TOO_LARGE' && /4096 UTF-8 bytes/.test(error.message))
  assert.equal(calls.read.length, 0)
  const tooBigOps = '[{"op":"block.trim","备注":"' + '注'.repeat(50000) + '"}]' // 150000+ UTF-8 bytes > 131072
  assert.ok(Buffer.byteLength(tooBigOps, 'utf8') > AGENT_INPUT_LIMIT)
  await assert.rejects(
    byName(tools, 'framivra_timeline_propose').execute({ timeline: 't', expected_content_version: 'sha256:' + 'a'.repeat(64), operations_json: tooBigOps }, EXEC),
    error => error.code === 'MODEL_INPUT_TOO_LARGE' && /131072 UTF-8 bytes/.test(error.message))
  assert.equal(calls.propose.length, 0)
  // Multibyte payloads within the byte bound pass through verbatim (agent stub accepts any string ≤ bound).
  const okOps = JSON.stringify([{ op: 'track.rename', 备注: '中文备注保持原样' }])
  await byName(tools, 'framivra_timeline_propose').execute({ timeline: '演示/timeline.json', expected_content_version: 'sha256:' + 'a'.repeat(64), operations_json: okOps }, EXEC)
  assert.equal(calls.propose[0].args.operations_json, okOps)
  assert.equal(calls.propose[0].args.timeline, '演示/timeline.json')
})

test('oversized envelope counts UTF-8 bytes: multibyte output fails without truncation', async () => {
  const mb = '时'.repeat(Math.floor(AGENT_WIRE_LIMIT / 3) + 8) // chars < limit, bytes > limit
  assert.ok(mb.length < AGENT_WIRE_LIMIT && Buffer.byteLength(mb, 'utf8') > AGENT_WIRE_LIMIT)
  const tools = createTimelineTools(fakeAgent({
    read: () => ({ schema: 'framivra.timeline.agent/v1', ok: true, blob: mb }),
  }).agent)
  await assert.rejects(
    byName(tools, 'framivra_timeline_read').execute({ timeline: 't', view: 'inspect' }, EXEC),
    error => error.code === 'MODEL_OUTPUT_TOO_LARGE' && /UTF-8 bytes/.test(error.message))
})

test('createTimelineTools rejects an adapter without the three methods', () => {
  assert.throws(() => createTimelineTools({ read() {} }), TypeError)
})

test('registerTimelineTools registers via ctx.effect and disposal removes all three', () => {
  const registered = []
  const disposers = []
  const ctx = {
    effect(fn) { disposers.push(fn()) },
    tools: { register(definition) { registered.push(definition); return () => { registered.splice(registered.indexOf(definition), 1) } } },
  }
  registerTimelineTools(ctx, fakeAgent().agent)
  assert.equal(registered.length, 3)
  assert.equal(disposers.length, 3)
  for (const dispose of disposers) dispose()
  assert.equal(registered.length, 0)
})
