import test from 'node:test'
import assert from 'node:assert/strict'
import React, { act } from 'react'
import { JSDOM } from 'jsdom'
import { AgentProposals } from '../src/client/proposals.mjs'
const h = React.createElement
// Real AgentProposals component under jsdom; `call` is an in-test transport
// double only — every state transition, gating and payload below is the
// component's own behavior. No kernel claims.
const version = `sha256:${'a'.repeat(64)}`
const opId = '11111111-2222-4333-8444-555555555555'
const pendingRow = { proposal_id: 'p1', state: 'pending', timeline: 'timeline.json', base_revision: version,
  plan: { submitted_request: { op_id: opId, base_revision: version, operations: [{ op: 'project.resize', duration_frames: 130 }] } },
  confirmation: null, outcome: null }
const openedFor = content_version => ({ grant: 'read', snapshot: { content_version, document: { project: {} } } })

async function mount(t) {
  const dom = new JSDOM('<div id="root"></div>', { url: 'http://owner/' })
  globalThis.window = dom.window; globalThis.document = dom.window.document; globalThis.IS_REACT_ACT_ENVIRONMENT = true
  const { createRoot } = await import('react-dom/client')
  const root = createRoot(document.getElementById('root'))
  t.after(async () => { await act(async () => root.unmount()); dom.window.close(); delete globalThis.window; delete globalThis.document })
  return root
}
const button = text => [...document.querySelectorAll('button')].find(n => n.textContent === text)
const text = () => document.body.textContent

test('first poll is immediate: empty list and read failure are both visible', { timeout: 10000 }, async t => {
  const root = await mount(t)
  const calls = []
  const call = async (_session, payload, signal) => {
    calls.push({ op: payload.op, signal })
    if (payload.op === 'proposal-list') return { proposals: [] }
    throw new Error('unexpected op')
  }
  await act(async () => root.render(h(AgentProposals, { sessionId: 'one', opened: openedFor(version), tabSignal: new AbortController().signal, call, writeGrant: undefined, manualBlocked: false, onCommitted: () => {}, onEditState: () => {} })))
  assert.equal(calls.length, 1, 'exactly one immediate poll, no interval wait needed')
  assert(text().includes('暂无提案'))
  assert.equal(button('确认提交此智能体冻结提案'), undefined)

  const failing = []
  const failCall = async () => { failing.push(1); throw new Error('连接已断开') }
  await act(async () => root.render(h(AgentProposals, { sessionId: 'one', opened: openedFor(version), tabSignal: new AbortController().signal, call: failCall, writeGrant: 'explicit', manualBlocked: false, onCommitted: () => {}, onEditState: () => {} })))
  assert.match(text(), /无法读取智能体提案：连接已断开。请检查连接；不会自动提交。/)
  assert.equal(failing.length, 1)
})

test('malformed proposal response is a visible protocol failure, never an invented empty success', { timeout: 10000 }, async t => {
  const root = await mount(t)
  for (const response of [undefined, { ok: true }, { proposals: [null] }, { proposals: [{ proposal_id: 'p1' }] }]) {
    await act(async () => root.render(h(AgentProposals, { sessionId: 'one', opened: openedFor(version), tabSignal: new AbortController().signal,
      call: async () => response, writeGrant: 'explicit', manualBlocked: false, onCommitted: () => {}, onEditState: () => {} })))
    assert.match(text(), /无法读取智能体提案：提案响应格式不完整/)
    assert.equal(text().includes('暂无提案'), false)
    assert.equal(button('确认提交此智能体冻结提案'), undefined)
  }
})

test('same-version pending row shows the exact raw plan; confirm gated by writeGrant, manualBlocked and stale base', { timeout: 10000 }, async t => {
  const root = await mount(t)
  const calls = []
  const call = async (_session, payload) => { calls.push(payload.op); return { proposals: [{ ...pendingRow }] } }
  const tabSignal = new AbortController().signal
  const states = []
  const render = props => root.render(h(AgentProposals, { sessionId: 'one', opened: openedFor(version), tabSignal, call, onCommitted: () => {}, onEditState: s => states.push(s), ...props }))
  await act(async () => render({ writeGrant: undefined, manualBlocked: false }))
  assert.equal(calls.length, 1, 'rerenders must not restart the poll')
  assert(text().includes('等待您确认 · 尚未保存'))
  assert(text().includes(`冻结计划 / 回执 · p1`))
  const raw = document.querySelector('article pre').textContent
  assert(raw.includes(`"op_id": "${opId}"`) && raw.includes('"duration_frames": 130') && raw.includes(`"base_revision": "${version}"`),
    'details show the exact frozen plan bytes')
  assert.equal(button('确认提交此智能体冻结提案').disabled, true, 'no write permit → disabled')
  assert.equal(button('拒绝此提案（不保存）').disabled, false)
  assert(text().includes('确认前，先在上方明确授予本会话 / 工程写许可'))
  assert(states.some(s => s.dirty === true), 'pending row reports dirty edit state')

  await act(async () => render({ writeGrant: 'explicit', manualBlocked: true }))
  assert.equal(button('确认提交此智能体冻结提案').disabled, true, 'manual unsaved batch blocks the confirm')
  assert(text().includes('请先清空手动未保存批次 / 草稿并等待当前命令结束，或拒绝此提案。'))

  await act(async () => render({ writeGrant: 'explicit', manualBlocked: false, opened: openedFor(`sha256:${'b'.repeat(64)}`) }))
  assert.equal(button('确认提交此智能体冻结提案').disabled, true, 'stale base can never be rebased by the UI')
  assert(text().includes('基线已变化：此提案不能自动重定基。请拒绝后让智能体重新读取。'))

  await act(async () => render({ writeGrant: 'explicit', manualBlocked: false }))
  assert.equal(button('确认提交此智能体冻结提案').disabled, false, 'same version + permit + no manual batch → enabled')
  assert.equal(calls.filter(op => op === 'proposal-list').length, 1)
})

test('confirm sends grant/writeGrant/proposalId exactly once; applied awaits onCommitted and clears dirty/busy', { timeout: 10000 }, async t => {
  const root = await mount(t)
  const calls = [], commits = [], states = []
  let resolveConfirm
  const call = async (_session, payload) => {
    calls.push(payload)
    if (payload.op === 'proposal-list') return { proposals: [{ ...pendingRow }] }
    if (payload.op === 'proposal-confirm') return await new Promise(r => { resolveConfirm = r })
    throw new Error(`unexpected op ${payload.op}`)
  }
  const tabSignal = new AbortController().signal
  await act(async () => root.render(h(AgentProposals, { sessionId: 'one', opened: openedFor(version), tabSignal, call, writeGrant: 'explicit', manualBlocked: false, onCommitted: async () => { commits.push(1) }, onEditState: s => states.push(s) })))
  await act(async () => button('确认提交此智能体冻结提案').click())
  assert(text().includes('正在等待宿主与内核回执…'))
  assert.equal(button('确认提交此智能体冻结提案').disabled, true, 'busy blocks a second submit')
  assert.equal(button('确认提交此智能体冻结提案').disabled, true, 'busy blocks a second submit')
  await act(async () => { const confirm = button('确认提交此智能体冻结提案'); if (confirm && !confirm.disabled) confirm.click() })
  assert.equal(calls.filter(c => c.op === 'proposal-confirm').length, 1)
  assert.deepEqual(calls.find(c => c.op === 'proposal-confirm'),
    { op: 'proposal-confirm', grant: 'read', proposalId: 'p1', writeGrant: 'explicit' },
    'exact payload: host grant, UI-only write permit, targeted proposal id')
  assert(states.some(s => s.busy === true), 'in-flight confirm reports busy edit state')
  await act(async () => resolveConfirm({ proposal: { ...pendingRow, state: 'applied', outcome: { op_id: opId } } }))
  assert(text().includes('内核已保存'))
  assert.equal(commits.length, 1, 'applied awaits onCommitted exactly once')
  assert.match(text(), /已保存并重新读取权威时间线。智能体可用 proposal_status 读取此回执；不会自动发送消息。/)
  assert.equal(calls.filter(c => !['proposal-list', 'proposal-confirm'].includes(c.op)).length, 0, 'no auto messages or extra ops')
  const last = states.at(-1)
  assert.deepEqual({ dirty: last.dirty, busy: last.busy, unknown: last.unknown }, { dirty: false, busy: false, unknown: false }, 'applied proposal clears dirty/busy/unknown')
})

test('reject needs no write permit, sends no writeGrant, and never commits', { timeout: 10000 }, async t => {
  const root = await mount(t)
  const calls = [], commits = []
  const call = async (_session, payload) => {
    calls.push(payload)
    if (payload.op === 'proposal-list') return { proposals: [{ ...pendingRow }] }
    if (payload.op === 'proposal-reject') return { proposal: { ...pendingRow, state: 'rejected' } }
    throw new Error(`unexpected op ${payload.op}`)
  }
  await act(async () => root.render(h(AgentProposals, { sessionId: 'one', opened: openedFor(version), tabSignal: new AbortController().signal, call, writeGrant: undefined, manualBlocked: false, onCommitted: async () => { commits.push(1) }, onEditState: () => {} })))
  assert.equal(button('拒绝此提案（不保存）').disabled, false)
  await act(async () => button('拒绝此提案（不保存）').click())
  assert.deepEqual(calls.find(c => c.op === 'proposal-reject'), { op: 'proposal-reject', grant: 'read', proposalId: 'p1' })
  assert.equal('writeGrant' in calls.find(c => c.op === 'proposal-reject'), false)
  assert.equal(calls.filter(c => c.op === 'proposal-confirm').length, 0)
  assert.equal(commits.length, 0)
  assert(text().includes('您已拒绝 · 未提交'))
  assert(text().includes('已拒绝此提案，没有提交。'))
})

test('unknown confirm outcome surfaces the original op_id and fail-closes; no automatic status/recover/retry', { timeout: 10000 }, async t => {
  const root = await mount(t)
  const calls = []
  const call = async (_session, payload) => {
    calls.push(payload.op)
    if (payload.op === 'proposal-list') return { proposals: [{ ...pendingRow }] }
    if (payload.op === 'proposal-confirm') throw new Error('提交回执未取得')
    throw new Error(`unexpected op ${payload.op}`)
  }
  const states = []
  await act(async () => root.render(h(AgentProposals, { sessionId: 'one', opened: openedFor(version), tabSignal: new AbortController().signal, call, writeGrant: 'explicit', manualBlocked: false, onCommitted: () => {}, onEditState: s => states.push(s) })))
  await act(async () => button('确认提交此智能体冻结提案').click())
  assert.match(text(), /提交回执未取得。提交回执未取得不代表失败；保留原 op_id 11111111-2222-4333-8444-555555555555，读取提案回执及 core status \/ recover，不自动重试。/)
  assert.equal(button('确认提交此智能体冻结提案').disabled, true, 'unknown outcome fail-closes every later confirm')
  assert.equal(calls.filter(op => op !== 'proposal-list').length, 1, 'exactly one confirm; the component never calls status/recover by itself')
  assert(states.some(s => s.unknown === true), 'unknown outcome reports unknown edit state')
})

test('unmount aborts the in-flight poll signal and clears the reschedule timer', { timeout: 10000 }, async t => {
  const root = await mount(t)
  const originalSet = globalThis.setTimeout, originalClear = globalThis.clearTimeout
  const scheduled = new Map(), cleared = new Set()
  globalThis.setTimeout = (fn, ms, ...rest) => { const id = originalSet(fn, ms, ...rest); scheduled.set(id, ms); return id }
  globalThis.clearTimeout = id => { cleared.add(id); return originalClear(id) }
  t.after(() => { globalThis.setTimeout = originalSet; globalThis.clearTimeout = originalClear })
  const calls = []
  let resolvePoll
  const call = async (_session, payload, signal) => {
    calls.push({ op: payload.op, signal })
    if (payload.op === 'proposal-list') return await new Promise(r => { resolvePoll = r })
    throw new Error('unexpected op')
  }
  await act(async () => root.render(h(AgentProposals, { sessionId: 'one', opened: openedFor(version), tabSignal: new AbortController().signal, call, writeGrant: 'explicit', manualBlocked: false, onCommitted: () => {}, onEditState: () => {} })))
  assert.equal(calls.length, 1)
  const pollSignal = calls[0].signal
  await act(async () => root.unmount())
  assert.equal(pollSignal.aborted, true, 'unmount aborts the owner controller so the standing poll stops')
  await act(async () => resolvePoll({ proposals: [] }))
  await new Promise(r => originalSet(r, 50))
  assert.equal(calls.length, 1, 'no second poll after unmount')
  const liveTimers = [...scheduled.keys()].filter(id => !cleared.has(id) && scheduled.get(id) === 1500)
  assert.equal(liveTimers.length, 0, 'the 1.5s reschedule timer does not outlive the component')
})

test('scope change resyncs the mirror: a still-pending list on the new grant must not be suppressed by a stale report ref',{timeout:10000},async t=>{
 const root=await mount(t),published=[]
 let answer={proposals:[pendingRow]}
 const call=async(_session,payload)=>{if(payload.op==='proposal-list')return answer;throw new Error('unexpected op')}
 const props=grant=>h(AgentProposals,{sessionId:'one',opened:{...openedFor(version),grant},tabSignal:new AbortController().signal,call,writeGrant:undefined,manualBlocked:false,onCommitted:()=>{},onEditState:value=>published.push(value)})
 await act(async()=>root.render(props('read')))
 assert.equal(published.at(-1)?.dirty,true,'pending row publishes dirty=true')
 // Same component, new opened.grant: cleanup publishes false, the new scope
 // immediately polls and is STILL pending — the mirror must end up true again.
 answer={proposals:[{...pendingRow,proposal_id:'p2'}]}
 await act(async()=>root.render(props('read2')))
 assert(text().includes('等待您确认'),'new grant renders its own pending proposal')
 // Deterministic sequence observed: pending→true, cleanup→false, the new
 // scope's setRows([]) commit republishes false (syncing the reported ref),
 // then the still-pending answer republishes true. No suppression window.
 assert.deepEqual(published,[{dirty:true,busy:false,unknown:false},{dirty:false,busy:false,unknown:false},{dirty:false,busy:false,unknown:false},{dirty:true,busy:false,unknown:false}])
 assert.equal(published.at(-1)?.dirty,true,'still-pending list must re-publish dirty=true after the scope change')
})
