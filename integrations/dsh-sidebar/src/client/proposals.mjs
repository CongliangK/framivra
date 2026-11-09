import React, { useEffect, useRef, useState } from 'react'
import { stringifyJSON } from '../json.mjs'
const h = React.createElement
const active = row => ['pending', 'committing', 'outcome_unknown'].includes(row.state)
const labels = { pending: '等待您确认 · 尚未保存', committing: '内核处理中 · 不可重复提交', applied: '内核已保存', not_applied: '内核未应用', outcome_unknown: '结果未知 · 先恢复，不重试', reconciled: '已读取恢复后的版本 · 原操作结果仍以 core 历史为准', rejected: '您已拒绝 · 未提交', expired: '提案已过期 · 不可提交', invalidated: '会话或目录已变化 · 不可提交' }
const pre = { whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', maxHeight: 320, overflow: 'auto' }

/** Proposals carry intent, never write authority; the existing manual permit stays UI-only. */
export function AgentProposals({ sessionId, opened, tabSignal, call, writeGrant, manualBlocked, onCommitted, onEditState }) {
  const [rows, setRows] = useState([]), [loaded, setLoaded] = useState(false)
  const [busy, setBusy] = useState(false), [message, setMessage] = useState(''), [unknown, setUnknown] = useState(false)
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
  const owner = useRef(), busyRef = useRef(false), latest = useRef({ onCommitted, onEditState })
  useEffect(() => { latest.current = { onCommitted, onEditState } }, [onCommitted, onEditState])
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
    setRows([]); setLoaded(false); setUnknown(false); setStale(false)
    async function poll() {
      try {
        if (!busyRef.current) {
          const answer = await call(sessionId, { op: 'proposal-list', grant: opened.grant }, signal)
          if (!signal.aborted) {
            if (!Array.isArray(answer?.proposals) || answer.proposals.some(row => !row || typeof row.proposal_id !== 'string' || typeof row.state !== 'string')) throw new Error('提案响应格式不完整；未执行任何提交')
            setRows(answer.proposals); setLoaded(true)
            if (answer.proposals.length && answer.proposals.every(row => !active(row))) setUnknown(false)
          }
        }
      } catch (error) { if (!signal.aborted) setMessage(`无法读取智能体提案：${error.message}。请检查连接；不会自动提交。`) }
      finally { if (!signal.aborted) timer = setTimeout(poll, 1500) }
    }
    void poll()
    return () => { clearTimeout(timer); controller.abort(); latest.current.onEditState?.({ dirty: false, busy: false, unknown: false }) }
  }, [sessionId, opened.grant, tabSignal, call])

  async function act(row, action) {
    if (busyRef.current || !owner.current || owner.current.signal.aborted) return
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
      setMessage(action === 'proposal-reject' ? '已拒绝此提案，没有提交。' : answer.proposal.state === 'applied' ? '已保存并重新读取权威时间线。智能体可用 proposal_status 读取此回执；不会自动发送消息。' : '请检查下方内核回执；未知结果须先显式 core status / recover，不要重复提交。')
    } catch (error) {
      if (!signal.aborted) {
        if (applied) {
          // Kernel receipt already confirmed applied; ONLY the authoritative
          // view refresh failed. Lock the stale view (reported dirty blocks
          // manual writes and frame export), keep unknown false, no retry.
          setStale(true); setUnknown(false)
          setMessage(`内核已确认保存（回执 applied）；仅权威视图刷新失败：${error.message}。旧视图已锁定（禁止再写与导帧）；请刷新或关闭后重开预览；不会自动重试，智能体可用 proposal_status 读取此回执。`)
        } else {
          if (action === 'proposal-confirm') setUnknown(true)
          setMessage(`${error.message}${action === 'proposal-confirm' ? `。提交回执未取得不代表失败；保留原 op_id ${row.plan?.submitted_request?.op_id ?? row.confirmation?.op_id ?? '见冻结计划'}，读取提案回执及 core status / recover，不自动重试。` : ''}`)
        }
      }
    } finally { busyRef.current = false; if (!signal.aborted) setBusy(false) }
  }
  async function copy(row) {
    try {
      if (!navigator.clipboard?.writeText) throw new Error('系统剪贴板不可用；可在展开的回执中手动选择文本')
      await navigator.clipboard.writeText(stringifyJSON(row, 2)); setMessage('已复制提案 / 确认回执；未发送消息。')
    } catch (error) { setMessage(error.message) }
  }
  return h('div', { 'aria-label': '智能体提案', style: { minWidth: 0, marginTop: 12, paddingTop: 12, borderTop: '1px solid currentColor' } },
    h('h3', null, '智能体提案 · 您确认后才保存'),
    h('p', null, '智能体只能读取或运行零写预检。请核对目标、版本和冻结操作；本区复用上方明确授予的写许可，不会把许可交给模型。'),
    !loaded ? h('p', { role: 'status' }, '正在读取本会话 / 工程的提案…') : !rows.length ? h('p', null, '暂无提案。可让智能体先读取时间线，再提出修改；读取本身不会保存。') : null,
    ...rows.map(row => h('article', { key: row.proposal_id, style: { marginTop: 12, minWidth: 0 } },
      h('h4', null, labels[row.state] ?? row.state),
      h('p', { style: { overflowWrap: 'anywhere' } }, `目标：${row.timeline}；基线：${row.base_revision}`),
      h('details', { open: row.state === 'pending' }, h('summary', null, `冻结计划 / 回执 · ${row.proposal_id}`), h('pre', { style: pre }, stringifyJSON(row, 2))),
      row.state === 'pending' ? h('div', { style: { display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 8 } },
        h('button', { type: 'button', disabled: !writeGrant || manualBlocked || busy || unknown || stale || opened.snapshot.content_version !== row.base_revision,
          onClick: () => void act(row, 'proposal-confirm') }, '确认提交此智能体冻结提案'),
        h('button', { type: 'button', disabled: busy, onClick: () => void act(row, 'proposal-reject') }, '拒绝此提案（不保存）')) : null,
      row.state === 'pending' && !writeGrant ? h('p', null, '确认前，先在上方明确授予本会话 / 工程写许可。') : null,
      row.state === 'pending' && manualBlocked ? h('p', null, '请先清空手动未保存批次 / 草稿并等待当前命令结束，或拒绝此提案。') : null,
      row.state === 'pending' && opened.snapshot.content_version !== row.base_revision ? h('p', null, '基线已变化：此提案不能自动重定基。请拒绝后让智能体重新读取。') : null,
      row.state === 'outcome_unknown' || row.state === 'reconciled' ? h('p', null, '保留原操作 ID。上方 core status / recover 与重新读取只能恢复权威状态，不自动证明原操作已应用或回滚。') : null,
      h('button', { type: 'button', onClick: () => void copy(row), style: { marginTop: 8 } }, '复制提案 / 确认回执'))),
    stale ? h('p', { role: 'alert' }, '内核已确认上一次提案写入（applied），但权威视图刷新失败：旧视图已锁定，禁止再确认提案、手动写入与导帧。请刷新或重开预览；不会自动重试。') : null,
    h('p', null, '提案和侧栏回执是有时限的内存状态，卸载或重启后消失；持久审计以 core 历史及原 op_id 为准。'),
    h('p', { role: 'status', 'aria-live': 'polite', style: { overflowWrap: 'anywhere' } }, message))
}
