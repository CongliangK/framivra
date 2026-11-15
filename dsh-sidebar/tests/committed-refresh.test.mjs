// R3 regression (F2): a kernel receipt that already confirmed a WRITE
// (commit/adopt/undo ok) must never be relabeled outcome_unknown when only
// the display refresh (onCommitted) fails — but a status/recover receipt is
// NOT write proof: it must never clear a prior unknown verdict. In both
// cases the stale view stays fail-closed (writes, quick edits, gestures,
// frame export, further proposal confirms disabled) with no automatic retry.
import test from 'node:test'
import assert from 'node:assert/strict'
import React, { act } from 'react'
import { JSDOM } from 'jsdom'
import { ManualEditor } from '../src/client/manual.mjs'
import { AgentProposals } from '../src/client/proposals.mjs'

const h = React.createElement
const version = `sha256:${'b'.repeat(64)}`
const opened = { grant: 'g1', snapshot: { content_version: version, document: { project: { fps: 30, width: 1920, height: 1080, duration_frames: 120 }, _vtx: { protocol: 1, head: 'h1' }, sources: [] } }, media: [] }
const result = { tracks: [{ family: 'video', track_index: 0, address: '/t/0', data: { name: 'MAIN', id: 'trk' } }], blocks: [] }

async function mount(t) {
  const dom = new JSDOM('<div id="root"></div>', { url: 'http://owner/' })
  globalThis.window = dom.window
  globalThis.document = dom.window.document
  globalThis.IS_REACT_ACT_ENVIRONMENT = true
  const { createRoot } = await import('react-dom/client')
  const root = createRoot(document.getElementById('root'))
  t.after(async () => { await act(async () => root.unmount()); dom.window.close(); delete globalThis.window; delete globalThis.document })
  const button = text => [...document.querySelectorAll('button')].find(b => b.textContent.includes(text))
  const settle = async predicate => { for (let i = 0; i < 400; i++) { if (predicate()) return; await act(async () => { await new Promise(resolve => setTimeout(resolve, 1)) }) } assert.fail(`DOM did not settle: ${document.body.textContent.slice(0, 300)}`) }
  return { root, dom, button, settle }
}
const lastSettled = reports => reports.filter(r => !r.busy).at(-1)

test('manual: real plan→commit ok with failed onCommitted reports a KNOWN applied write, not outcome_unknown; stale view fail-closed, no auto retry', async t => {
  const requests = [], reports = [], gestures = []
  let commits = 0
  const call = async (_sessionId, payload) => {
    requests.push(payload.op)
    if (payload.op === 'authorize-write') return { ok: true, writeGrant: 'wg' }
    if (payload.op === 'plan') return { ok: true, submitted_request: { op_id: 'op-9' }, planToken: 'tok' }
    if (payload.op === 'commit') return { ok: true, content_version: `sha256:${'c'.repeat(64)}` }
    return { ok: true }
  }
  const onCommitted = async () => { commits++; throw new Error('TRANSPORT_GONE') }
  const { root, dom, button, settle } = await mount(t)
  await act(async () => root.render(h(ManualEditor, { sessionId: 'one', opened, result, selection: undefined, selected: [], frame: undefined, tabSignal: new AbortController().signal, call, onCommitted, onEditState: next => reports.push(next), onGestureReady: callback => gestures.push(callback), onQuickReady: () => {} })))
  // Grant the permit, enqueue a resize intent, plan, then commit whose
  // receipt is ok while the authoritative refresh fails.
  await act(async () => button('明确允许本会话向此工程及 core 历史写入').click())
  await settle(() => requests.includes('authorize-write'))
  const opSelect = [...document.querySelectorAll('label')].find(l => l.textContent.includes('编辑操作'))?.querySelector('select')
  const { Simulate } = await import('react-dom/test-utils')
  await act(async () => Simulate.change(opSelect, { target: { value: 'project.resize' } }))
  await act(async () => button('加入未保存批次').click())
  await settle(() => button('内核预检计划（零写）').disabled === false)
  await act(async () => button('内核预检计划（零写）').click())
  await settle(() => document.body.textContent.includes('未保存计划 · op_id op-9'), 'plan accepted')
  await act(async () => button('确认提交此冻结计划').click())
  await settle(() => document.querySelector('[role=alert]')?.textContent.includes('旧视图已锁定'))
  // 1. Known applied: last settled report keeps unknown:false and locks dirty.
  assert.equal(lastSettled(reports).unknown, false, `reports=${JSON.stringify(reports)}`)
  assert.equal(lastSettled(reports).dirty, true)
  assert.equal(requests.filter(op => op === 'commit').length, 1)
  assert.equal(commits, 1)
  // 2. Message claims the confirmed write, never "outcome unknown".
  assert(document.body.textContent.includes('内核已确认本次写入'), document.body.textContent.slice(0, 200))
  assert(!document.body.textContent.includes('结果可能未知'), 'must not claim the outcome is unknown')
  // 3. Stale view fail-closed; read-only recovery stays usable.
  assert(button('撤销当前 core head').disabled)
  assert(button('内核预检计划（零写）').disabled)
  assert(button('读取 core status / history').disabled === false)
  assert(gestures.at(-1) === undefined, 'gesture entry revoked on the locked view')
  // StaleLock locks the dedicated form too — the batch-clear button must not
  // be able to clear the stale-owned dirty (frame-export lock).
  assert(document.querySelector('fieldset[disabled]'), 'staleLock disables the dedicated form fieldset')
  assert(button('加入未保存批次').disabled)
  assert(button('清空未保存批次').disabled)
  // 4. No automatic retry of any write-bearing operation.
  const before = requests.length
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 60)) })
  assert.equal(requests.length, before, `no auto retry; requests=${requests.join(',')}`)
})

test('manual: status ok + failed onCommitted does NOT clear a prior outcome_unknown verdict', async t => {
  const reports = []
  const call = async (_sessionId, payload) => {
    if (payload.op === 'authorize-write') return { ok: true, writeGrant: 'wg' }
    if (payload.op === 'undo') return { ok: false, code: 'outcome_unknown' } // prior write verdict becomes unknown
    if (payload.op === 'status') return { ok: true, history: [] }            // receipt ok, refresh fails below
    return { ok: true }
  }
  const onCommitted = async () => { throw new Error('REFRESH_GONE') }
  const { root, button, settle } = await mount(t)
  await act(async () => root.render(h(ManualEditor, { sessionId: 'one', opened, result, selection: undefined, selected: [], frame: undefined, tabSignal: new AbortController().signal, call, onCommitted, onEditState: next => reports.push(next), onGestureReady: () => {}, onQuickReady: () => {} })))
  await act(async () => button('明确允许本会话向此工程及 core 历史写入').click())
  await act(async () => button('撤销当前 core head').click())
  await settle(() => lastSettled(reports)?.unknown === true, 'undo receipt sets unknown')
  await act(async () => button('读取 core status / history').click())
  await settle(() => document.querySelector('[role=alert]')?.textContent.includes('旧视图已锁定'))
  // The status receipt is NOT write proof: unknown survives, view locked.
  assert.equal(lastSettled(reports).unknown, true, `reports=${JSON.stringify(reports)}`)
  assert.equal(lastSettled(reports).dirty, true)
  assert(!document.body.textContent.includes('内核已确认本次写入'), 'status path must not claim a confirmed write')
  assert(document.body.textContent.includes('不证明此前未知写已应用'))
  assert(button('撤销当前 core head').disabled)
  assert(button('读取 core status / history').disabled === false, 'read-only recovery stays usable')
})

test('proposals: applied receipt + failed onCommitted keeps unknown false, locks further confirms, no auto retry', async t => {
  const requests = []
  const reports = []
  const row = id => ({ proposal_id: id, state: 'pending', timeline: 'timeline.json', base_revision: version, plan: { submitted_request: { op_id: `op-${id}` } } })
  const call = async (_sessionId, payload) => {
    requests.push(payload.op)
    if (payload.op === 'proposal-list') return { ok: true, proposals: [row('p1'), row('p2')] }
    if (payload.op === 'proposal-confirm') return { ok: true, proposal: { ...row(payload.proposalId), state: 'applied' } }
    return { ok: true }
  }
  const onCommitted = async () => { throw new Error('REFRESH_GONE') }
  const { root, button, settle } = await mount(t)
  await act(async () => root.render(h(AgentProposals, { sessionId: 'one', opened, tabSignal: new AbortController().signal, call, writeGrant: 'wg', manualBlocked: false, onCommitted, onEditState: next => reports.push(next) })))
  await settle(() => [...document.querySelectorAll('article')].length === 2)
  const confirm = () => button('确认提交此智能体冻结提案')
  assert(!confirm().disabled)
  await act(async () => confirm().click())
  await settle(() => document.body.textContent.includes('旧视图已锁定'))
  // The confirmed row keeps its applied receipt; unknown is never reported.
  assert(document.body.textContent.includes('内核已保存'), document.body.textContent.slice(0, 200))
  assert(!document.body.textContent.includes('结果未知'))
  const settled = reports.filter(r => !r.busy)
  assert.equal(settled[settled.length - 1].unknown, false, `reports=${JSON.stringify(reports)}`)
  assert.equal(settled[settled.length - 1].dirty, true, 'stale view reported dirty: fail-closed')
  // The other pending proposal can no longer be confirmed on the stale snapshot.
  assert(confirm().disabled, 'confirm disabled on locked stale view')
  // No automatic retry or resubmission.
  const before = requests.filter(op => op === 'proposal-confirm').length
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 60)) })
  assert.equal(requests.filter(op => op === 'proposal-confirm').length, before)
})

test('manual: stale-owned dirty is cleared by a later SUCCESSFUL refresh (positive ownership)', async t => {
  const reports = []
  let refreshes = 0
  const call = async (_sessionId, payload) => {
    if (payload.op === 'authorize-write') return { ok: true, writeGrant: 'wg' }
    if (payload.op === 'plan') return { ok: true, submitted_request: { op_id: 'op-9' }, planToken: 'tok' }
    if (payload.op === 'commit') return { ok: true, content_version: `sha256:${'c'.repeat(64)}` }
    if (payload.op === 'status') return { ok: true, history: [] }
    return { ok: true }
  }
  // First refresh (after commit receipt ok) fails → staleLock; the second
  // (status with good transport) must release the stale-owned dirty lock.
  const onCommitted = async () => { refreshes++; if (refreshes === 1) throw new Error('TRANSPORT_GONE') }
  const { root, button, settle } = await mount(t)
  await act(async () => root.render(h(ManualEditor, { sessionId: 'one', opened, result, selection: undefined, selected: [], frame: undefined, tabSignal: new AbortController().signal, call, onCommitted, onEditState: next => reports.push(next), onGestureReady: () => {}, onQuickReady: () => {} })))
  await act(async () => button('明确允许本会话向此工程及 core 历史写入').click())
  await settle(() => button('加入未保存批次').disabled === false)
  const opSelect = [...document.querySelectorAll('label')].find(l => l.textContent.includes('编辑操作'))?.querySelector('select')
  const { Simulate } = await import('react-dom/test-utils')
  await act(async () => Simulate.change(opSelect, { target: { value: 'project.resize' } }))
  await act(async () => button('加入未保存批次').click())
  await settle(() => button('内核预检计划（零写）').disabled === false)
  await act(async () => button('内核预检计划（零写）').click())
  await settle(() => document.body.textContent.includes('未保存计划 · op_id op-9'))
  await act(async () => button('确认提交此冻结计划').click())
  await settle(() => document.querySelector('[role=alert]')?.textContent.includes('旧视图已锁定'))
  assert.equal(lastSettled(reports).dirty, true, 'stale lock owns dirty')
  assert(button('清空未保存批次').disabled)
  // Now the authoritative refresh succeeds: the stale-owned dirty must be
  // released (no queue, no draft edits) and the view unlocked.
  await act(async () => button('读取 core status / history').click())
  await settle(() => document.querySelector('[role=alert]') === null)
  assert.equal(lastSettled(reports).dirty, false, `stale dirty released on refresh success; reports=${JSON.stringify(reports)}`)
  assert.equal(lastSettled(reports).unknown, false)
  assert.equal(document.querySelector('fieldset[disabled]'), null, 'form usable again')
  assert(button('清空未保存批次').disabled === false)
  assert.equal(refreshes, 2)
})

test('manual: user draft/queue dirty is NOT cleared by a successful status refresh (negative ownership)', async t => {
  const reports = []
  const call = async (_sessionId, payload) => {
    if (payload.op === 'authorize-write') return { ok: true, writeGrant: 'wg' }
    if (payload.op === 'status') return { ok: true, history: [] }
    return { ok: true }
  }
  const onCommitted = async () => {} // refresh succeeds
  const { root, button, settle } = await mount(t)
  await act(async () => root.render(h(ManualEditor, { sessionId: 'one', opened, result, selection: undefined, selected: [], frame: undefined, tabSignal: new AbortController().signal, call, onCommitted, onEditState: next => reports.push(next), onGestureReady: () => {}, onQuickReady: () => {} })))
  await act(async () => button('明确允许本会话向此工程及 core 历史写入').click())
  await settle(() => button('加入未保存批次').disabled === false)
  const opSelect = [...document.querySelectorAll('label')].find(l => l.textContent.includes('编辑操作'))?.querySelector('select')
  const { Simulate } = await import('react-dom/test-utils')
  await act(async () => Simulate.change(opSelect, { target: { value: 'project.resize' } }))
  await act(async () => button('加入未保存批次').click())
  await settle(() => lastSettled(reports)?.dirty === true && document.querySelector('[aria-label="未保存 typed intents"]').textContent.includes('project.resize'))
  // status ok + successful refresh: user queue dirty must survive.
  await act(async () => button('读取 core status / history').click())
  await settle(() => document.body.textContent.includes('最近 core status / recover'))
  assert.equal(lastSettled(reports).dirty, true, `queue dirty preserved; reports=${JSON.stringify(reports)}`)
  assert.equal(lastSettled(reports).unknown, false)
  assert(document.querySelector('[aria-label="未保存 typed intents"]').textContent.includes('project.resize'), 'queue intact')
  assert(button('内核预检计划（零写）').disabled === false, 'plan still available')
  assert(button('确认提交此冻结计划') === undefined)
})

test('manual: explicit clear resets draftDirty, so a later stale→recovered refresh releases dirty (clear ownership reset)', async t => {
  const reports = []
  let refreshes = 0
  const call = async (_sessionId, payload) => {
    if (payload.op === 'authorize-write') return { ok: true, writeGrant: 'wg' }
    if (payload.op === 'status') return { ok: true, history: [] }
    return { ok: true }
  }
  // Refresh 1 (after a status ok) fails → staleLock owns dirty; refresh 2
  // succeeds and must release it, because the user's earlier draft dirty was
  // explicitly cleared via 清空未保存批次 (draftDirty reset with it).
  const onCommitted = async () => { refreshes++; if (refreshes === 1) throw new Error('TRANSPORT_GONE') }
  const { root, button, settle } = await mount(t)
  await act(async () => root.render(h(ManualEditor, { sessionId: 'one', opened, result, selection: undefined, selected: [], frame: undefined, tabSignal: new AbortController().signal, call, onCommitted, onEditState: next => reports.push(next), onGestureReady: () => {}, onQuickReady: () => {} })))
  await act(async () => button('明确允许本会话向此工程及 core 历史写入').click())
  await settle(() => button('加入未保存批次').disabled === false)
  const opSelect = [...document.querySelectorAll('label')].find(l => l.textContent.includes('编辑操作'))?.querySelector('select')
  const { Simulate } = await import('react-dom/test-utils')
  await act(async () => Simulate.change(opSelect, { target: { value: 'project.resize' } }))
  await act(async () => button('加入未保存批次').click())
  await settle(() => lastSettled(reports)?.dirty === true)
  // Explicit clear: reported dirty false AND draft ownership released.
  await act(async () => button('清空未保存批次').click())
  await settle(() => lastSettled(reports).dirty === false && document.querySelector('[aria-label="未保存 typed intents"]').textContent === '[]')
  // status ok with failing refresh: staleLock owns dirty now.
  await act(async () => button('读取 core status / history').click())
  await settle(() => document.querySelector('[role=alert]')?.textContent.includes('旧视图已锁定'))
  assert.equal(lastSettled(reports).dirty, true, 'stale lock owns dirty')
  // Refresh succeeds: stale-owned dirty must be released (queue cleared earlier).
  await act(async () => button('读取 core status / history').click())
  await settle(() => document.querySelector('[role=alert]') === null)
  assert.equal(lastSettled(reports).dirty, false, `stale dirty released after recovery; reports=${JSON.stringify(reports)}`)
  assert.equal(lastSettled(reports).unknown, false)
  assert.equal(document.querySelector('fieldset[disabled]'), null)
  assert.equal(refreshes, 2)
})

test('proposals: same-grant new authoritative snapshot object releases stale; applied receipt kept, pending old base stays CAS-blocked, no resubmit', async t => {
  const requests = []
  const reports = []
  const row = id => ({ proposal_id: id, state: 'pending', timeline: 'timeline.json', base_revision: version, plan: { submitted_request: { op_id: `op-${id}` } } })
  let appliedId
  const call = async (_sessionId, payload) => {
    requests.push(payload.op)
    if (payload.op === 'proposal-list') return { ok: true, proposals: [row('p1'), row('p2')].map(r => r.proposal_id === appliedId ? { ...r, state: 'applied' } : r) }
    if (payload.op === 'proposal-confirm') { appliedId = payload.proposalId; return { ok: true, proposal: { ...row(payload.proposalId), state: 'applied' } } }
    return { ok: true }
  }
  const onCommitted = async () => { throw new Error('REFRESH_GONE') }
  const { root, button, settle } = await mount(t)
  const props = extra => ({ sessionId: 'one', opened, tabSignal: new AbortController().signal, call, writeGrant: 'wg', manualBlocked: false, onCommitted, onEditState: next => reports.push(next), ...extra })
  await act(async () => root.render(h(AgentProposals, props())))
  await settle(() => [...document.querySelectorAll('article')].length === 2)
  const confirm = () => button('确认提交此智能体冻结提案')
  await act(async () => confirm().click())
  await settle(() => document.body.textContent.includes('旧视图已锁定'))
  assert(document.body.textContent.includes('内核已保存'))
  // Simulate the parent's successful authoritative refresh: SAME grant, but a
  // NEW opened.snapshot OBJECT (with an advanced content_version — no remount,
  // no new grant). This must release the proposals' own stale lock only.
  const refreshed = `sha256:${'d'.repeat(64)}`
  const opened2 = { ...opened, snapshot: { ...opened.snapshot, content_version: refreshed } }
  await act(async () => root.render(h(AgentProposals, props({ opened: opened2 }))))
  await settle(() => document.querySelector('[role=alert]') === null, 'stale alert released by new snapshot object')
  // Own stale released: reported dirty now comes only from the still-pending
  // row (rows.some(active)); unknown stays false; no applied→unknown relabel.
  const settled = reports.filter(r => !r.busy)
  assert.equal(settled[settled.length - 1].unknown, false, `reports=${JSON.stringify(reports)}`)
  assert.equal(settled[settled.length - 1].dirty, true, 'pending row keeps blocking dirty')
  // The applied receipt is preserved; the pending row keeps its old base —
  // original CAS base mismatch blocks confirmation on the fresh snapshot.
  assert(document.body.textContent.includes('内核已保存'), 'applied receipt retained')
  assert(confirm().disabled, 'pending old-base proposal not confirmable (CAS)')
  assert(document.body.textContent.includes('基线已变化'), 'base mismatch surfaced')
  // No automatic resubmission of anything.
  const before = requests.filter(op => op === 'proposal-confirm').length
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 60)) })
  assert.equal(requests.filter(op => op === 'proposal-confirm').length, before)
})
