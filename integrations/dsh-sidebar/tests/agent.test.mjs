/**
 * createTimelineAgent unit tests over stub host callbacks. No real Loader,
 * ToolRuntime, or vtx process: `call`/`plan`/`commit`/`validate` are recording
 * stubs owned by this file. F1/F2 cases encode the agreed fix semantics
 * (AggregateError with primary+cleanup; no residual row on oversize); they are
 * expected to fail until that fix lands and are the regression pin for it.
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { createTimelineAgent } from '../src/agent.mjs'
import { stringifyJSON } from '../src/json.mjs'
import { PreviewError } from '../src/protocol.mjs'

const VERSION = 'sha256:' + 'a'.repeat(64)
const OPS = '[{"op":"block.trim","amount":1},{"op":"track.rename","name":"b"}]'

/**
 * Fully controlled harness: sessions/cwd maps, a recording `call`, and
 * overridable plan/commit/validate/close behaviors.
 */
function makeHarness(overrides = {}) {
  let now = 1_000_000
  const clock = () => now
  const sessionA = { id: 'S-a' }, sessionB = { id: 'S-b' }
  const sessions = new Map([[sessionA.id, sessionA], [sessionB.id, sessionB]])
  const cwds = new Map([[sessionA, '/proj'], [sessionB, '/other']])
  const calls = [], plans = [], commits = [], validates = []
  let closeError = null, planGate
  const opened = {
    grant: 'g-open',
    snapshot: { content_version: overrides.snapshotVersion ?? VERSION },
    capabilities: { edit: { operations: ['block.trim', 'track.rename'] } },
  }
  const grantA = { session: sessionA, cwd: '/proj', root: { targetKey: 'root#1' }, timeline: { targetKey: 'tl#1' },
    relative: 'demo.json', contentVersion: VERSION, write: null, writeKey: null, running: null, plan: null }
  const grantB = { session: sessionB, cwd: '/other', root: { targetKey: 'root#2' }, timeline: { targetKey: 'tl#2' },
    relative: 'demo.json', contentVersion: VERSION, write: null, writeKey: null, running: null, plan: null }
  const ctx = {
    sessions: { get: id => sessions.get(id) },
    workingDirectory: {
      get: session => cwds.get(session),
      ensure: async (agent, signal) => { signal?.throwIfAborted(); return cwds.get(agent.session) },
    },
  }
  const agent = createTimelineAgent({
    ctx, clock, ttlMs: 30_000,
    call: async (session, data, signal) => {
      calls.push({ session, data })
      if (data.op === 'open') return opened
      if (data.op === 'close') { if (closeError) throw closeError; return { ok: true } }
      if (data.op === 'query' || data.op === 'reference') return { ok: true, kind: data.op, query: data.query }
      throw new Error('unexpected call op ' + data.op)
    },
    grantFor: (sessionId, grant) => (sessionId === sessionA.id ? grantA : grantB),
    plan: async (grant, data, signal) => {
      plans.push({ grant, data })
      if (planGate) await planGate
      if (overrides.planError) throw overrides.planError
      const result = overrides.planResult ?? { ok: true, prediction: { touched: 2, ops: data.operations.length } }
      return { result, frozen: { request: { operation: 'edit', op_id: 'op-' + (plans.length), base_revision: data.base_revision, operations: data.operations } } }
    },
    commit: async (grant, data, frozen, signal) => {
      commits.push({ grant, data, frozen })
      if (overrides.commitError) throw overrides.commitError
      return overrides.commitResult ?? { ok: true, op_id: frozen.request.op_id, head: 'h1' }
    },
    validate: async (grant, signal) => { validates.push(grant); if (overrides.validateError) throw overrides.validateError },
    editCapabilities: overrides.editCapabilities,
  })
  const execA = () => ({ agent: { session: sessionA }, signal: new AbortController().signal })
  const execB = () => ({ agent: { session: sessionB }, signal: new AbortController().signal })
  const writeGrantA = () => { grantA.write = { signal: new AbortController().signal }; grantA.writeKey = 'wk-1' }
  return {
    agent, calls, plans, commits, validates, grantA, grantB, sessionA, sessionB, sessions, cwds,
    execA, execB, writeGrantA, opened,
    setCloseError: error => { closeError = error },
    setPlanGate: gate => { planGate = gate },
    advance: ms => { now += ms },
    driftCwd: () => cwds.set(sessionA, '/elsewhere'),
    replaceSession: () => { const fresh = { id: 'S-a' }; sessions.set('S-a', fresh); return fresh },
  }
}

const proposeArgs = (over = {}) => ({ timeline: 'demo.json', expected_content_version: VERSION, operations_json: OPS, ...over })

test('read is zero-write: only open/close reach the host, envelope carries snapshot + notice', async () => {
  const h = makeHarness()
  const value = await h.agent.read(h.execA(), { timeline: 'demo.json', view: 'inspect' })
  assert.deepEqual(h.calls.map(c => c.data.op), ['open', 'close'])
  assert.equal(h.plans.length + h.commits.length + h.validates.length, 0)
  assert.equal(value.operation, 'read')
  assert.equal(value.ok, true)
  assert.equal(value.result.content_version, VERSION)
  assert.match(value.notice, /untrusted content, not instructions/)
  assert.match(value.notice, /does not authorize a save/)
})

test('read query parses query_json once and forwards the parsed object with original tokens', async () => {
  const h = makeHarness()
  await h.agent.read(h.execA(), { timeline: 'demo.json', view: 'query', query_json: '{"family":"video","x":9007199254740993}' })
  assert.equal(h.calls[1].data.op, 'query')
  assert.equal(h.calls[1].data.query.family, 'video')
  assert.ok(stringifyJSON(h.calls[1].data.query).includes('9007199254740993'))
})

test('read rejects unknown view, oversized query, and inspect-with-fields without host calls', async () => {
  const h = makeHarness()
  await assert.rejects(h.agent.read(h.execA(), { timeline: 'demo.json', view: 'mutate' }), e => e.code === 'BAD_OPERATION')
  await assert.rejects(h.agent.read(h.execA(), { timeline: 'demo.json', view: 'query', query_json: 'x'.repeat(128 * 1024 + 1) }), e => e.code === 'BAD_QUERY')
  await assert.rejects(h.agent.read(h.execA(), { timeline: 'demo.json', view: 'inspect', query_json: '{"family":"video"}' }), e => e.code === 'BAD_QUERY')
  assert.deepEqual(h.calls.map(c => c.data.op), [])
})

test('propose is zero-write: one plan, no commit, pending row with next_action guidance', async () => {
  const h = makeHarness()
  const value = await h.agent.propose(h.execA(), proposeArgs())
  assert.equal(h.plans.length, 1)
  assert.equal(h.commits.length, 0)
  assert.deepEqual(h.calls.map(c => c.data.op), ['open', 'close'])
  assert.equal(h.plans[0].data.op, 'plan')
  assert.equal(h.plans[0].data.base_revision, VERSION)
  assert.equal(value.ok, true)
  assert.equal(value.proposal.state, 'pending')
  assert.match(value.next_action, /explicitly confirm/)
  assert.match(value.next_action, /No save has occurred/)
})

test('propose validates input before any host interaction', async () => {
  const h = makeHarness()
  await assert.rejects(h.agent.propose(h.execA(), proposeArgs({ operations_json: 'x'.repeat(128 * 1024 + 1) })), e => e.code === 'BAD_INTENT')
  await assert.rejects(h.agent.propose(h.execA(), proposeArgs({ expected_content_version: 'v1' })), e => e.code === 'STALE_BASE')
  await assert.rejects(h.agent.propose(h.execA(), proposeArgs({ operations_json: '[]' })), e => e.code === 'BAD_INTENT')
  await assert.rejects(h.agent.propose(h.execA(), proposeArgs({ operations_json: '{"op":1}' })), e => e.code === 'BAD_INTENT')
  await assert.rejects(h.agent.propose(h.execA(), proposeArgs({ operations_json: `[${Array(257).fill('{"op":"block.trim"}').join(',')}]` })), e => e.code === 'BAD_INTENT')
  assert.deepEqual([h.calls.length, h.plans.length, h.commits.length], [0, 0, 0])
})

test('propose rejects a stale base against the fresh snapshot', async () => {
  const h = makeHarness({ snapshotVersion: 'sha256:' + 'b'.repeat(64) })
  await assert.rejects(h.agent.propose(h.execA(), proposeArgs()), e => e.code === 'STALE_BASE')
  assert.equal(h.plans.length, 0)
  assert.deepEqual(h.calls.map(c => c.data.op), ['open', 'close'])
})

test('same fingerprint propose is idempotent: duplicate=true, same id, plan runs once', async () => {
  const h = makeHarness()
  const first = await h.agent.propose(h.execA(), proposeArgs())
  const second = await h.agent.propose(h.execA(), proposeArgs())
  assert.equal(second.duplicate, true)
  assert.equal(second.proposal.proposal_id, first.proposal.proposal_id)
  assert.equal(h.plans.length, 1)
})

test('different pending fingerprint is refused without a second plan', async () => {
  const h = makeHarness()
  await h.agent.propose(h.execA(), proposeArgs())
  await assert.rejects(
    h.agent.propose(h.execA(), proposeArgs({ operations_json: '[{"op":"track.rename","name":"c"}]' })),
    e => e.code === 'PROPOSAL_PENDING' && /Do not replace or retry/.test(e.message))
  assert.equal(h.plans.length, 1)
})

test('concurrent propose in one Session is serialized by AGENT_BUSY', async () => {
  const h = makeHarness()
  let release
  h.setPlanGate(new Promise(resolve => { release = resolve }))
  const first = h.agent.propose(h.execA(), proposeArgs())
  await new Promise(resolve => setImmediate(resolve))
  await assert.rejects(h.agent.propose(h.execA(), proposeArgs()), e => e.code === 'AGENT_BUSY')
  release()
  await first
  assert.equal(h.plans.length, 1)
})

test('status is zero-write and scoped: no calls, cross-Session invisible, unknown id is null', async () => {
  const h = makeHarness()
  const { proposal } = (await h.agent.propose(h.execA(), proposeArgs())).valueOf()
  h.calls.length = 0
  const own = await h.agent.status(h.execA(), { timeline: 'demo.json' })
  assert.equal(own.operation, 'proposal_status')
  assert.equal(own.proposal.proposal_id, proposal.proposal_id)
  assert.match(own.notice, /Never infer that a save failed or automatically retry/)
  const other = await h.agent.status(h.execB(), { timeline: 'demo.json' })
  assert.equal(other.proposal, null)
  const missing = await h.agent.status(h.execA(), { timeline: 'demo.json', proposal_id: 'nope' })
  assert.equal(missing.proposal, null)
  assert.equal(h.calls.length, 0)
  assert.equal(h.plans.length, 1, 'no additional plan may run during status')
  assert.equal(h.commits.length, 0)
})

test('UI confirm requires an explicit write grant and single-consumes the proposal', async () => {
  const h = makeHarness()
  const { proposal_id } = (await h.agent.propose(h.execA(), proposeArgs())).proposal
  await assert.rejects(
    h.agent.handle({ op: 'proposal-confirm', proposalId: proposal_id, writeGrant: 'wrong' }, h.grantA),
    e => e.code === 'WRITE_DENIED')
  h.writeGrantA()
  const confirmed = await h.agent.handle({ op: 'proposal-confirm', proposalId: proposal_id, writeGrant: 'wk-1' }, h.grantA)
  assert.equal(confirmed.ok, true)
  assert.equal(confirmed.proposal.state, 'applied')
  assert.equal(h.commits.length, 1)
  assert.equal(h.commits[0].frozen.request.op_id, confirmed.proposal.confirmation.op_id)
  await assert.rejects(
    h.agent.handle({ op: 'proposal-confirm', proposalId: proposal_id, writeGrant: 'wk-1' }, h.grantA),
    e => e.code === 'PROPOSAL_CONSUMED')
  assert.equal(h.commits.length, 1)
})

test('confirm refuses busy manual batch, stale base, and unknown proposals', async () => {
  const h = makeHarness()
  const { proposal_id } = (await h.agent.propose(h.execA(), proposeArgs())).proposal
  h.writeGrantA()
  h.grantA.plan = { token: 't' }
  await assert.rejects(h.agent.handle({ op: 'proposal-confirm', proposalId: proposal_id, writeGrant: 'wk-1' }, h.grantA), e => e.code === 'WRITE_BUSY')
  h.grantA.plan = null
  h.grantA.contentVersion = 'sha256:' + 'c'.repeat(64)
  await assert.rejects(h.agent.handle({ op: 'proposal-confirm', proposalId: proposal_id, writeGrant: 'wk-1' }, h.grantA), e => e.code === 'STALE_BASE')
  h.grantA.contentVersion = VERSION
  await assert.rejects(h.agent.handle({ op: 'proposal-confirm', proposalId: 'zzz', writeGrant: 'wk-1' }, h.grantA), e => e.code === 'BAD_PROPOSAL')
  const list = await h.agent.handle({ op: 'proposal-list' }, h.grantA)
  assert.equal(list.proposals.length, 1)
  assert.equal(h.commits.length, 0)
})

test('reject consumes the proposal without any commit', async () => {
  const h = makeHarness()
  const { proposal_id } = (await h.agent.propose(h.execA(), proposeArgs())).proposal
  const rejected = await h.agent.handle({ op: 'proposal-reject', proposalId: proposal_id }, h.grantA)
  assert.equal(rejected.proposal.state, 'rejected')
  assert.equal(h.commits.length, 0)
  await assert.rejects(h.agent.handle({ op: 'proposal-confirm', proposalId: proposal_id }, h.grantA), e => e.code === 'PROPOSAL_CONSUMED')
})

test('commit crash keeps op_id and frozen request under outcome_unknown; recovery never infers success', async () => {
  const h = makeHarness({ commitError: new Error('child vanished') })
  const proposed = await h.agent.propose(h.execA(), proposeArgs())
  const { proposal_id } = proposed.proposal
  h.writeGrantA()
  await assert.rejects(h.agent.handle({ op: 'proposal-confirm', proposalId: proposal_id, writeGrant: 'wk-1' }, h.grantA))
  const after = await h.agent.status(h.execA(), { timeline: 'demo.json' })
  assert.equal(after.proposal.state, 'outcome_unknown')
  assert.equal(after.proposal.outcome.op_id, h.commits[0].frozen.request.op_id)
  assert.deepEqual(after.proposal.outcome.submitted_request, h.commits[0].frozen.request)
  assert.match(after.proposal.outcome.message, /never replace or automatically retry/)
  // Real core recover shape (vtx/timeline_store.py status/recover): ok:true
  // with pending:false is the only settled-intent evidence.
  h.agent.recovered(h.grantA, { schema: 'framivra.timeline.store/v1', operation: 'recover', ok: true, code: 'ok', pending: false,
    current_revision: 'sha256:' + 'd'.repeat(64), head: null, evidence_path: '/gone', operations: [{ op_id: h.commits[0].frozen.request.op_id, state: 'applied' }] })
  const recoveredRow = (await h.agent.status(h.execA(), { timeline: 'demo.json' })).proposal
  assert.equal(recoveredRow.recovery.code, 'ok')
  assert.equal(recoveredRow.state, 'outcome_unknown')
  h.agent.observed(h.grantA, { content_version: 'sha256:' + 'd'.repeat(64) })
  const reconciled = (await h.agent.status(h.execA(), { timeline: 'demo.json' })).proposal
  assert.equal(reconciled.state, 'reconciled')
  assert.equal(reconciled.observed_content_version, 'sha256:' + 'd'.repeat(64))
  assert.equal(reconciled.outcome.ok, false, 'reconciliation must not flip the outcome to success')
  h.opened.snapshot = { content_version: 'sha256:' + 'd'.repeat(64) }
  const fresh = await h.agent.propose(h.execA(), proposeArgs({ expected_content_version: 'sha256:' + 'd'.repeat(64) }))
  assert.equal(fresh.ok, true, 'reconciled frees the timeline for new proposals')
})

test('commit returning code outcome_unknown lands in outcome_unknown, not not_applied', async () => {
  const h = makeHarness({ commitResult: { ok: false, code: 'outcome_unknown', op_id: 'op-1' } })
  const { proposal_id } = (await h.agent.propose(h.execA(), proposeArgs())).proposal
  h.writeGrantA()
  const confirmed = await h.agent.handle({ op: 'proposal-confirm', proposalId: proposal_id, writeGrant: 'wk-1' }, h.grantA)
  assert.equal(confirmed.ok, false)
  assert.equal(confirmed.proposal.state, 'outcome_unknown')
})

test('deterministic host rejection (PreviewError) is not_applied and consumed', async () => {
  const h = makeHarness({ commitError: new PreviewError('PLAN_INVALID', 'plan expired', 409) })
  const { proposal_id } = (await h.agent.propose(h.execA(), proposeArgs())).proposal
  h.writeGrantA()
  await assert.rejects(h.agent.handle({ op: 'proposal-confirm', proposalId: proposal_id, writeGrant: 'wk-1' }, h.grantA), e => e.code === 'PLAN_INVALID')
  const row = (await h.agent.status(h.execA(), { timeline: 'demo.json' })).proposal
  assert.equal(row.state, 'not_applied')
})

test('cwd drift hides pending rows (cwd-scoped visibility); session replacement is rejected at identity', async () => {
  const h = makeHarness()
  await h.agent.propose(h.execA(), proposeArgs())
  h.driftCwd()
  const drifted = await h.agent.status(h.execA(), { timeline: 'demo.json' })
  assert.equal(drifted.proposal, null, 'a pending row bound to the old cwd must not be visible or confirmable after drift')
  h.writeGrantA()
  await assert.rejects(h.agent.handle({ op: 'proposal-list' }, h.grantA), e => e.code === 'BAD_SESSION',
    'the stale grant itself is pinned to the pre-drift cwd and must be re-established')
  h.replaceSession()
  await assert.rejects(h.agent.status(h.execA(), { timeline: 'demo.json' }), e => e.code === 'BAD_SESSION')
})

test('TTL: pending expires, cannot be confirmed, receipts are deleted after the retention window', async () => {
  const h = makeHarness()
  const { proposal_id } = (await h.agent.propose(h.execA(), proposeArgs())).proposal
  h.advance(30_001)
  const expired = (await h.agent.status(h.execA(), { timeline: 'demo.json' })).proposal
  assert.equal(expired.state, 'expired')
  h.writeGrantA()
  await assert.rejects(h.agent.handle({ op: 'proposal-confirm', proposalId: proposal_id, writeGrant: 'wk-1' }, h.grantA), e => e.code === 'PROPOSAL_CONSUMED')
  assert.equal(h.commits.length, 0)
  h.advance(30_001)
  const gone = await h.agent.status(h.execA(), { timeline: 'demo.json' })
  assert.equal(gone.proposal, null)
})

test('oversized read result fails MODEL_OUTPUT_TOO_LARGE without truncation', async () => {
  const h = makeHarness()
  h.opened.snapshot = { content_version: VERSION, blob: 'y'.repeat(256 * 1024 + 64) }
  await assert.rejects(h.agent.read(h.execA(), { timeline: 'demo.json', view: 'inspect' }), e => e.code === 'MODEL_OUTPUT_TOO_LARGE')
})

test('F2 (fix pin): an oversize propose envelope leaves no residual pending row', async () => {
  const planResult = { ok: true, prediction: { blob: 'z'.repeat(256 * 1024 - 128) } }
  assert.ok(Buffer.byteLength(stringifyJSON(planResult), 'utf8') < 256 * 1024, 'the core result fits; only the complete proposal envelope exceeds the bound')
  const h = makeHarness({ planResult })
  await assert.rejects(h.agent.propose(h.execA(), proposeArgs()), e => e.code === 'MODEL_OUTPUT_TOO_LARGE')
  const after = await h.agent.status(h.execA(), { timeline: 'demo.json' })
  assert.equal(after.proposal, null, 'no row may be stored when its envelope cannot reach the model')
  planResult.prediction.blob = 'small'
  const retry = await h.agent.propose(h.execA(), proposeArgs({ operations_json: '[{"op":"track.rename","name":"c"}]' }))
  assert.equal(retry.duplicate, undefined, 'the next smaller proposal must not be treated as a duplicate')
})

test('F1 (fix pin): a cleanup failure must not mask the primary error — AggregateError keeps both', async () => {
  const h = makeHarness()
  h.setCloseError(Object.assign(new Error('session gone'), { code: 'BAD_SESSION' }))
  const error = await h.agent.propose(h.execA(), proposeArgs({ expected_content_version: 'sha256:' + 'e'.repeat(64) })).then(
    () => { throw new Error('expected rejection') },
    error => error)
  assert.ok(error instanceof AggregateError, `expected AggregateError with primary+cleanup, got ${error.constructor.name}: ${error.message}`)
  const primary = error.errors.find(e => e.code === 'STALE_BASE')
  const cleanup = error.errors.find(e => e.code === 'BAD_SESSION')
  assert.ok(primary, 'primary STALE_BASE must be preserved')
  assert.ok(cleanup, 'cleanup failure must be attached, not swallowed')
})

test('plan numbers survive to status with original tokens', async () => {
  const h = makeHarness({ planResult: { ok: true, prediction: { frame: JSON.rawJSON('9007199254740993'), drift: JSON.rawJSON('0.30000000000000004') } } })
  const proposed = await h.agent.propose(h.execA(), proposeArgs())
  const text = stringifyJSON(proposed)
  for (const token of ['9007199254740993', '0.30000000000000004']) assert.ok(text.includes(token), `lost ${token}`)
  const row = (await h.agent.status(h.execA(), { timeline: 'demo.json' })).proposal
  assert.ok(stringifyJSON(row).includes('9007199254740993'))
})

test('dispose(): every entry point reports AGENT_DISPOSED', async () => {
  const h = makeHarness()
  await h.agent.propose(h.execA(), proposeArgs())
  h.agent.dispose()
  for (const promise of [
    h.agent.read(h.execA(), { timeline: 'demo.json', view: 'inspect' }),
    h.agent.propose(h.execA(), proposeArgs()),
    h.agent.status(h.execA(), { timeline: 'demo.json' }),
  ]) {
    await assert.rejects(promise, e => e.code === 'AGENT_DISPOSED' && e.status === 503)
  }
})

test('plan rejection is surfaced as zero-save guidance, no row stored', async () => {
  const h = makeHarness({ planResult: { ok: false, code: 'recovery_required', message: 'unresolved intent' } })
  const value = await h.agent.propose(h.execA(), proposeArgs())
  assert.equal(value.ok, false)
  assert.equal(value.state, 'plan_rejected')
  assert.match(value.next_action, /Nothing was saved/)
  assert.equal((await h.agent.status(h.execA(), { timeline: 'demo.json' })).proposal, null)
})

test('read rejects non-object query_json (null/array/primitive/raw number) with BAD_QUERY before any host call', async () => {
  const h = makeHarness()
  for (const bad of ['null', '[]', '[{"track":"a"}]', '3', '"text"', '123.5', '9007199254740993', 'true']) {
    await assert.rejects(h.agent.read(h.execA(), { timeline: 'demo.json', view: 'query', query_json: bad }),
      e => e.code === 'BAD_QUERY', `query_json ${bad} must be rejected as non-object`)
  }
  assert.equal(h.calls.length, 0)
})

test('inspect exposes edit_capabilities verbatim, including a degraded advisory', async () => {
  const advisory = { ok: false, error: 'CLI_UNSUPPORTED', message: 'Installed vtx does not advertise the semantic store; reads remain usable' }
  const h = makeHarness({ editCapabilities: async () => advisory })
  const value = await h.agent.read(h.execA(), { timeline: 'demo.json', view: 'inspect' })
  assert.deepEqual(value.edit_capabilities, advisory)
  assert.equal(value.ok, true, 'a degraded store advisory must not fail the read itself')
})

test('observed without recovered never reconciles an unknown row', async () => {
  const h = makeHarness({ commitError: new Error('child died') })
  await h.agent.propose(h.execA(), proposeArgs())
  h.writeGrantA()
  await assert.rejects(h.agent.handle({ op: 'proposal-confirm', proposalId: (await h.agent.status(h.execA(), { timeline: 'demo.json' })).proposal.proposal_id, writeGrant: 'wk-1' }, h.grantA))
  h.agent.observed(h.grantA, { content_version: 'sha256:' + 'e'.repeat(64) })
  const row = (await h.agent.status(h.execA(), { timeline: 'demo.json' })).proposal
  assert.equal(row.state, 'outcome_unknown', 'a fresh version alone is not evidence the op settled')
  assert.ok(h.agent.hasLive(h.grantA))
})

test('outcome_unknown rows are never deleted by TTL sweeps', async () => {
  const h = makeHarness({ commitError: new Error('child died') })
  await h.agent.propose(h.execA(), proposeArgs())
  h.writeGrantA()
  await assert.rejects(h.agent.handle({ op: 'proposal-confirm', proposalId: (await h.agent.status(h.execA(), { timeline: 'demo.json' })).proposal.proposal_id, writeGrant: 'wk-1' }, h.grantA))
  h.advance(24 * 60 * 60 * 1000)
  const row = (await h.agent.status(h.execA(), { timeline: 'demo.json' })).proposal
  assert.ok(row, 'the unknown receipt must survive sweeping so status/recover stays authoritative')
  assert.equal(row.outcome.op_id, 'op-1')
  assert.ok(h.agent.hasLive(h.grantA))
})

test('cross-grant recovered (grant B) then observed (grant A) reconciles the shared unknown row', async () => {
  const h = makeHarness({ commitError: new Error('child died') })
  await h.agent.propose(h.execA(), proposeArgs())
  h.writeGrantA()
  await assert.rejects(h.agent.handle({ op: 'proposal-confirm', proposalId: (await h.agent.status(h.execA(), { timeline: 'demo.json' })).proposal.proposal_id, writeGrant: 'wk-1' }, h.grantA))
  // grantB shares the same timeline identity: recovery observed from any grant of the project counts.
  h.grantB.root = h.grantA.root; h.grantB.timeline = h.grantA.timeline; h.grantB.cwd = '/proj'; h.grantB.session = h.sessionA
  h.agent.recovered(h.grantB, { schema: 'framivra.timeline.store/v1', operation: 'status', ok: true, code: 'ok', pending: false,
    current_revision: 'sha256:' + 'f'.repeat(64), head: null, evidence_path: '/gone', operations: [] })
  h.agent.observed(h.grantA, { content_version: 'sha256:' + 'f'.repeat(64) })
  const row = (await h.agent.status(h.execA(), { timeline: 'demo.json' })).proposal
  assert.equal(row.state, 'reconciled')
  assert.equal(row.outcome.ok, false)
  assert.ok(!h.agent.hasLive(h.grantA), 'reconciliation releases the timeline for fresh proposals')
})

const coreStatus = over => ({ schema: 'framivra.timeline.store/v1', operation: 'status', ok: true, code: 'ok', pending: false,
  current_revision: VERSION, head: null, evidence_path: '/gone', operations: [], ...over })
const freshVersion = { content_version: 'sha256:' + '9'.repeat(64) }

test('recovery evidence is fail-closed: pending:true, missing pending, and ok:false never reconcile', async () => {
  const h = makeHarness({ commitError: new Error('child died') })
  const { proposal_id } = (await h.agent.propose(h.execA(), proposeArgs())).proposal
  h.writeGrantA()
  await assert.rejects(h.agent.handle({ op: 'proposal-confirm', proposalId: proposal_id, writeGrant: 'wk-1' }, h.grantA))
  const originalOpId = h.commits[0].frozen.request.op_id
  for (const result of [coreStatus({ pending: true }), coreStatus({ pending: undefined }), coreStatus({ ok: false, pending: false })]) {
    h.agent.recovered(h.grantA, result)
    h.agent.observed(h.grantA, freshVersion)
    const row = (await h.agent.status(h.execA(), { timeline: 'demo.json' })).proposal
    assert.equal(row.state, 'outcome_unknown', `${JSON.stringify(result)} must not count as settled recovery`)
    assert.ok(!row.recovery, 'no evidence may be recorded for non-settled status')
    assert.equal(row.outcome.op_id, originalOpId, 'the original op_id is preserved verbatim')
  }
  assert.ok(h.agent.hasLive(h.grantA), 'the unknown row still holds the timeline')
  // A settled status plus a fresh observed version reconciles.
  h.agent.recovered(h.grantA, coreStatus())
  h.agent.observed(h.grantA, freshVersion)
  const reconciled = (await h.agent.status(h.execA(), { timeline: 'demo.json' })).proposal
  assert.equal(reconciled.state, 'reconciled')
  assert.equal(reconciled.outcome.op_id, originalOpId)
})

test('a later pending:true status revokes unconsumed settled recovery evidence', async () => {
  const h = makeHarness({ commitError: new Error('child died') })
  const { proposal_id } = (await h.agent.propose(h.execA(), proposeArgs())).proposal
  h.writeGrantA()
  await assert.rejects(h.agent.handle({ op: 'proposal-confirm', proposalId: proposal_id, writeGrant: 'wk-1' }, h.grantA))
  h.agent.recovered(h.grantA, coreStatus())
  // Not yet consumed by an observed version: a pending:true status resets it.
  h.agent.recovered(h.grantA, coreStatus({ pending: true }))
  h.agent.observed(h.grantA, freshVersion)
  const revoked = (await h.agent.status(h.execA(), { timeline: 'demo.json' })).proposal
  assert.equal(revoked.state, 'outcome_unknown', 'revoked evidence must not reconcile on the later observed version')
  assert.ok(!revoked.recovery)
  assert.ok(h.agent.hasLive(h.grantA))
  // A fresh settled status plus a fresh observed version reconciles.
  h.agent.recovered(h.grantA, coreStatus())
  h.agent.observed(h.grantA, freshVersion)
  const row = (await h.agent.status(h.execA(), { timeline: 'demo.json' })).proposal
  assert.equal(row.state, 'reconciled')
  assert.ok(!h.agent.hasLive(h.grantA))
})
