import React, { useEffect, useRef, useState } from 'react'
import { stringifyJSON } from '../json.mjs'
import { terminalGrantFailure, GRANT_DEAD_MESSAGE } from './grant-lifecycle.mjs'
import { PlanReview, editError } from './edit-review.mjs'
const h = React.createElement
const active = row => ['pending', 'committing', 'outcome_unknown'].includes(row.state)
const labels = { pending: '等待您确认 · 尚未保存', committing: '内核处理中 · 不可重复提交', applied: '内核已保存', not_applied: '内核未应用', outcome_unknown: '结果未知 · 先恢复，不重试', reconciled: '已读取恢复后的版本 · 原操作结果仍以 core 历史为准', rejected: '您已拒绝 · 未提交', expired: '提案已过期 · 不可提交', invalidated: '会话或目录已变化 · 不可提交' }

/** Proposals carry intent, never write authority; the existing manual permit stays UI-only. */
export function AgentProposals({ sessionId, opened, result, tabSignal, call, writeGrant, manualBlocked, onCommitted, onEditState, onGrantDead }) {
  const [rows, setRows] = useState([]), [loaded, setLoaded] = useState(false)
  const [busy, setBusy] = useState(false), [message, setMessage] = useState(''), [unknown, setUnknown] = useState(false)
  // Terminal grant death (BAD_GRANT / CONFIG_CHANGED / PATH_CHANGED): the
  // Host can only repeat the same failure for this grant id, so polling
  // stops permanently and every action is invalidated until a NEW grant.
  const [dead, setDead] = useState()
  // True when the kernel receipt already said applied but the authoritative
  // view refresh failed: the stale view must stay fail-closed (reported dirty
  // blocks manual writes/frames), yet the KNOWN-applied outcome must never be
  // relabeled outcome_unknown and is never auto-retried.
  const [stale, setStale] = useState(false)
  // A successful authoritative refresh replaces the parent's opened.snapshot
  // OBJECT (same grant). That new object — not any frame/query or a content
  // version string — releases our own stale lock; rows.some(active) and
  // unknown keep blocking independently.
  const knownSnapshot = useRef(), knownGrant = useRef()
  useEffect(() => {
    if (knownSnapshot.current !== undefined && opened.snapshot !== knownSnapshot.current && knownGrant.current === opened.grant) setStale(false)
    knownSnapshot.current = opened.snapshot
    knownGrant.current = opened.grant
  }, [opened.grant, opened.snapshot])
  const owner = useRef(), busyRef = useRef(false), latest = useRef({ onCommitted, onEditState, onGrantDead })
  useEffect(() => { latest.current = { onCommitted, onEditState, onGrantDead } }, [onCommitted, onEditState, onGrantDead])
  // Notify the parent only when a derived safety flag actually transitions:
  // every poll replaces `rows` with a fresh array, so reporting on identity
  // would re-send an identical {dirty,busy,unknown} and (before the epoch
  // became idempotent) abort in-flight frame renders on a read-only poll.
  // The list itself still re-renders normally below; only the mirror report
  // is gated on the derived booleans.
  const reported = useRef({ dirty: false, busy: false, unknown: false })
  useEffect(() => {
    const next = { dirty: stale || rows.some(active), busy, unknown: unknown || rows.some(row => row.state === 'outcome_unknown') }
    const previous = reported.current
    if (previous.dirty !== next.dirty || previous.busy !== next.busy || previous.unknown !== next.unknown) {
      reported.current = next
      latest.current.onEditState?.(next)
    }
  }, [rows, busy, unknown, stale])
  useEffect(() => {
    const controller = new AbortController(), signal = AbortSignal.any([tabSignal, controller.signal])
    owner.current = controller
    let timer
    setRows([]); setLoaded(false); setUnknown(false); setStale(false); setDead(undefined)
    async function poll() {
      let stopped = false
      try {
        if (!busyRef.current) {
          const answer = await call(sessionId, { op: 'proposal-list', grant: opened.grant }, signal)
          if (!signal.aborted) {
            if (!Array.isArray(answer?.proposals) || answer.proposals.some(row => !row || typeof row.proposal_id !== 'string' || typeof row.state !== 'string')) throw new Error('提案响应格式不完整；未执行任何提交')
            setRows(answer.proposals); setLoaded(true)
            if (answer.proposals.length && answer.proposals.every(row => !active(row))) setUnknown(false)
          }
        }
      } catch (error) {
        if (!signal.aborted) {
          const code = terminalGrantFailure(error.message)
          if (code) {
            // Terminal: the Host TTL expired / config or workspace moved under
            // this grant. Re-polling could only hammer the same 403 forever;
            // stop the timer, lift the death to the parent and fail closed.
            stopped = true
            setDead(code)
            latest.current.onGrantDead?.(code, opened.grant)
            setMessage(`预览授权已失效（${code}）；请重开工程。`)
          } else setMessage(`无法读取智能体提案：${editError(error)}。请检查连接。`)
        }
      }
      finally { if (!signal.aborted && !stopped) timer = setTimeout(poll, 1500) }
    }
    void poll()
    return () => { clearTimeout(timer); controller.abort(); latest.current.onEditState?.({ dirty: false, busy: false, unknown: false }) }
  }, [sessionId, opened.grant, tabSignal, call])

  async function act(row, action) {
    if (dead || busyRef.current || !owner.current || owner.current.signal.aborted) return
    if (action === 'proposal-confirm' && (!writeGrant || manualBlocked || unknown || stale || row.state !== 'pending' || opened.snapshot.content_version !== row.base_revision)) return
    const signal = AbortSignal.any([tabSignal, owner.current.signal])
    busyRef.current = true; setBusy(true); setMessage('正在等待宿主与内核回执…')
    let applied = false
    try {
      const answer = await call(sessionId, { op: action, grant: opened.grant, proposalId: row.proposal_id,
        ...(action === 'proposal-confirm' ? { writeGrant } : {}) }, signal)
      if (signal.aborted) return
      setRows(previous => previous.map(item => item.proposal_id === row.proposal_id ? answer.proposal : item))
      setUnknown(answer.proposal.state === 'outcome_unknown')
      if (answer.proposal.state === 'applied') { applied = true; await latest.current.onCommitted(); setStale(false) }
      setMessage(action === 'proposal-reject' ? '已拒绝提案。' : answer.proposal.state === 'applied' ? '已保存并刷新工程。' : answer.proposal.outcome ? editError(answer.proposal.outcome) : '请先读取状态 / 恢复，不要重复提交。')
    } catch (error) {
      if (!signal.aborted) {
        if (applied) {
          // Kernel receipt already confirmed applied; ONLY the authoritative
          // view refresh failed. Lock the stale view (reported dirty blocks
          // manual writes and frame export), keep unknown false, no retry.
          setStale(true); setUnknown(false)
          setMessage('内核已确认保存（applied）；权威视图刷新失败，旧视图已锁定。请刷新或重开工程。')
        } else {
          if (action === 'proposal-confirm') setUnknown(true)
          setMessage(`${editError(error)}${action === 'proposal-confirm' ? `。结果未知；原操作 ID：${row.plan?.submitted_request?.op_id ?? row.confirmation?.op_id ?? '见冻结计划'}。请读取状态 / 恢复，不要重试保存。` : ''}`)
        }
      }
    } finally { busyRef.current = false; if (!signal.aborted) setBusy(false) }
  }
  async function copy(row) {
    try {
      if (!navigator.clipboard?.writeText) throw new Error('系统剪贴板不可用，请检查浏览器权限。')
      await navigator.clipboard.writeText(stringifyJSON(row, 2)); setMessage('已复制提案 / 确认回执；未发送消息。')
    } catch (error) { setMessage(error.message) }
  }
  const context = { tracks: result?.tracks, blocks: result?.blocks, sources: opened.snapshot.document.sources }
  return h('section', { 'aria-label': '智能体提案', className: 'fv-edit-proposals' },
    h('h3', null, '智能体提案'),
    dead ? h('p', { role: 'alert', className: 'fv-edit-status' }, `${GRANT_DEAD_MESSAGE}（${dead}）`) : null,
    !loaded ? h('p', { role: 'status', className: 'fv-edit-meta' }, '正在读取提案…') : !rows.length ? h('p', { className: 'fv-edit-meta' }, '暂无提案') : null,
    ...rows.map(row => h('article', { key: row.proposal_id, className: 'fv-edit-proposal' },
      h('h4', null, labels[row.state] ?? row.state),
      h('p', { className: 'fv-edit-meta' }, row.timeline),
      row.plan ? h('details', { open: row.state === 'pending' }, h('summary', null, '预检与冻结操作'), h(PlanReview, { plan: row.plan, context })) : null,
      row.outcome ? h('p', { className: 'fv-edit-status' }, row.state === 'applied' ? '已保存' : editError(row.outcome)) : null,
      row.state === 'pending' ? h('div', { className: 'fv-edit-actions' },
        h('button', { type: 'button', disabled: dead || !writeGrant || manualBlocked || busy || unknown || stale || opened.snapshot.content_version !== row.base_revision,
          title: '明确提交此智能体冻结提案', onClick: () => void act(row, 'proposal-confirm') }, '保存提案'),
        h('button', { type: 'button', disabled: dead || busy, onClick: () => void act(row, 'proposal-reject') }, '拒绝提案')) : null,
      row.state === 'pending' && !writeGrant ? h('p', { className: 'fv-edit-meta' }, '请先启用编辑。') : null,
      row.state === 'pending' && manualBlocked ? h('p', { className: 'fv-edit-meta' }, '先清空手动草稿并等待当前操作结束，或拒绝提案。') : null,
      row.state === 'pending' && opened.snapshot.content_version !== row.base_revision ? h('p', { role: 'alert', className: 'fv-edit-status' }, '基线已变化，请拒绝提案并重新读取。') : null,
      row.state === 'outcome_unknown' || row.state === 'reconciled' ? h('p', { className: 'fv-edit-status' }, `保留原操作 ID：${row.plan?.submitted_request?.op_id ?? row.confirmation?.op_id ?? '见内核历史'}。读取状态 / 恢复不证明原操作已应用或回滚。`) : null,
      h('button', { type: 'button', onClick: () => void copy(row) }, '复制回执'))),
    stale ? h('p', { role: 'alert', className: 'fv-edit-status' }, '内核已确认写入（applied），但权威视图刷新失败。旧视图已锁定，请刷新或重开工程。') : null,
    message ? h('p', { role: 'status', 'aria-live': 'polite', className: 'fv-edit-status' }, message) : null)
}
