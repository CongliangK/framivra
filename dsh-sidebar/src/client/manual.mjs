import React, { useLayoutEffect, useRef, useState } from 'react'
import { StatusReview, editError, receiptMessage } from './edit-review.mjs'
import { terminalGrantFailure } from './grant-lifecycle.mjs'
import { ToolSummary } from './icon-controls.mjs'

const h = React.createElement
const staleError = () => new Error('EDIT_STALE: 编辑状态已变化，请重新操作。')
const validGesture = operation => operation && (['block.move', 'block.trim'].includes(operation.op) ||
  operation.op === 'range.ripple' && operation.mode === 'delete' && operation.crossing === 'reject' && operation.resize === false)
const unknownResult = answer => answer?.code === 'outcome_unknown' || answer?.outcome === 'unknown'

export function ManualEditor({ sessionId, opened, result, selection, selected, frame, tabSignal, call,
  onCommitted, onGestureReady, onEditState, onQuickReady, onUndoReady, onEnableReady, onGrantDead, suspended = false, stale = false, historyRef }) {
  const base = opened.snapshot.content_version, head = opened.snapshot.document?._vtx?.head
  const managed = opened.snapshot.document?._vtx?.protocol === 1
  const [write, setWrite] = useState()
  const [message, setMessage] = useState(), [status, setStatus] = useState(), [undoneRevision, setUndoneRevision] = useState()
  const flags = useRef({ dirty: false, busy: false, unknown: false })
  const [{ dirty, busy, unknown }, setFlags] = useState(flags.current)
  const active = useRef(), epoch = useRef(0), request = useRef()
  const openHistory = () => { if (historyRef?.current) historyRef.current.open = true }
  useLayoutEffect(() => () => request.current?.abort(), [])
  useLayoutEffect(() => { epoch.current++ }, [selection, selected, frame, base, opened.grant, suspended])
  const reason = suspended ? '请等待空间操作结束。' : busy ? '正在处理中' : unknown ? '请打开历史与恢复' : dirty || stale ? '请刷新工程' : !write ? '请先启用编辑' : !managed ? '请先接管旧工程' : ''
  const report = (patch) => {
    flags.current = { ...flags.current, ...patch }
    setFlags(flags.current)
    onEditState?.(flags.current)
  }
  const payload = (op, extra) => ({ op, grant: opened.grant, ...(write ? { writeGrant: write } : {}), ...extra })
  function check(token) {
    if (active.current !== token || flags.current.busy || suspended || tabSignal.aborted) throw staleError()
  }
  function answerError(answer) {
    return Object.assign(new Error(answer?.message ?? editError(answer)), { code: answer?.code, op_id: answer?.op_id })
  }
  async function transact(token, action, { operations, focusId } = {}) {
    check(token)
    const safety = flags.current
    const direct = action === 'edit', writeAction = direct || action === 'undo' || action === 'adopt'
    if (writeAction && (!write || stale || safety.dirty || safety.unknown || (!managed && action !== 'adopt'))) throw staleError()
    if ((['status', 'recover', 'revoke-write'].includes(action) && !write) || (action === 'authorize-write' && stale && !safety.unknown)) throw staleError()
    const start = epoch.current, controller = new AbortController()
    const signal = AbortSignal.any([tabSignal, controller.signal])
    request.current = controller
    const send = (verb, extra) => call(sessionId, payload(verb, extra), signal)
    let operationId, dispatched = false, applied = false, statusObserved = false
    report({ busy: true }); setMessage({ text: direct ? '正在保存…' : '处理中…' })
    try {
      let answer
      if (direct) {
        const prepared = await send('plan', { base_revision: base, operations })
        signal.throwIfAborted()
        if (!prepared.ok) throw answerError(prepared)
        const planToken = prepared.planToken
        if (typeof planToken !== 'string' || !planToken) throw new Error('预检未返回保存凭证，未保存。')
        operationId = prepared.submitted_request?.op_id
        if (start !== epoch.current) {
          await send('invalidate-plan')
          throw staleError()
        }
        dispatched = true
        answer = await send('commit', { planToken })
      } else {
        let extra
        if (action === 'undo' || action === 'adopt') {
          operationId = crypto.randomUUID()
          extra = { op_id: operationId, base_revision: base, ...(action === 'undo' ? { head } : { confirmation: 'stable-ids-and-history' }) }
        }
        dispatched = writeAction || action === 'recover'
        answer = await send(action, extra)
      }
      if (!answer.ok) {
        dispatched = unknownResult(answer)
        throw answerError(answer)
      }
      applied = writeAction
      statusObserved = action === 'status' || action === 'recover'
      signal.throwIfAborted()
      if (action === 'authorize-write') {
        if (!answer.writeGrant) throw new Error('未获得编辑许可。')
        setWrite(answer.writeGrant)
      } else if (action === 'revoke-write') setWrite(undefined)
      else if (applied || statusObserved) {
        if (statusObserved) setStatus(answer)
        await onCommitted?.({ focusId, expectedRevision: applied ? answer.revision : answer.current_revision })
        signal.throwIfAborted()
        report({ dirty: false, unknown: applied || answer.pending === false ? false : flags.current.unknown || answer.pending === true })
        if (action === 'undo') setUndoneRevision(answer.revision)
      }
      setMessage({ text: receiptMessage(direct ? 'commit' : action, answer) })
      return direct ? { ok: true, op_id: operationId, answer } : answer
    } catch (cause) {
      const dead = terminalGrantFailure(cause.message)
      if (dead) onGrantDead?.(dead, opened.grant)
      const error = Object.assign(new Error(editError(cause)), { code: cause.code, op_id: cause.op_id ?? operationId })
      if (applied || statusObserved) {
        report(applied ? { dirty: true, unknown: false } : { dirty: true })
        error.applied = applied
        error.message = applied ? '内核已保存，但工程刷新失败。请刷新保存版本。' : '已读取状态，但工程刷新失败；原写入结果未重新判定。'
      } else if (dispatched && (!cause.code || cause.code === 'outcome_unknown' || cause.code === 'OUTCOME_UNKNOWN' || cause.code === 'PROCESS_CLEANUP_FAILED') || cause.code === 'recovery_required') {
        report({ unknown: true })
        error.recoveryRequired = cause.code === 'recovery_required'
        error.outcomeUnknown = !error.recoveryRequired
        if (error.outcomeUnknown) error.message = `保存结果未知${error.op_id ? `（${error.op_id}）` : ''}，请先读取状态或恢复。`
        openHistory()
      }
      if (!controller.signal.aborted) {
        if (!direct) openHistory()
        setMessage({ text: error.message, error: !error.applied })
      }
      throw error
    } finally {
      if (request.current === controller) { request.current = undefined; report({ busy: false }) }
    }
  }
  useLayoutEffect(() => {
    const token = {}; active.current = token
    const ready = !reason
    onEnableReady?.(!write && !suspended && !busy && !dirty && !unknown && !stale ? () => transact(token, 'authorize-write') : undefined)
    onGestureReady?.(ready ? (operations, options) => {
      if (!Array.isArray(operations) || !operations.every(validGesture)) return Promise.reject(new Error('此入口仅支持移动、向内裁剪或闭合空隙。'))
      return operations.length ? transact(token, 'edit', { ...options, operations }) : Promise.resolve({ ok: true, noop: true })
    } : undefined, reason)
    onUndoReady?.(ready && head && base !== undoneRevision ? () => transact(token, 'undo') : undefined)
    onQuickReady?.(ready ? async operation => {
      check(token)
      if (!['block.split', 'block.remove'].includes(operation)) throw new Error('EDIT_UNSUPPORTED: 不支持此快捷操作。')
      const items = selected ?? (selection ? [selection] : [])
      const blocks = result.blocks.filter(block => items.some(item => item.kind === 'block' && item.address === block.address))
      if (!blocks.length || !blocks.every(block => typeof block.data?.id === 'string')) throw new Error('请选择可编辑片段。')
      if (operation === 'block.remove') return transact(token, 'edit', { operations: blocks.map(block => ({ op: operation, id: block.data.id })) })
      const [block] = blocks
      const track = result.tracks.find(item => item.family === block.family && item.track_index === block.track_index)
      if (track?.data?.kind === 'gfx') throw new Error('EDIT_UNSUPPORTED: 图形片段不能分割。')
      if (blocks.length !== 1 || !Number.isSafeInteger(frame) || frame <= block.range[0] || frame >= block.range[1]) throw new Error('EDIT_BOUNDS: 请将播放头放在一个选中片段内。')
      return transact(token, 'edit', { focusId: block.data.id, operations: [{ op: operation, id: block.data.id, at: frame, new_id: crypto.randomUUID(), ...(block.family === 'subtitles' ? { text_policy: 'duplicate-text' } : { fade_policy: 'preserve-content' }) }] })
    } : undefined)
    return () => { active.current = undefined; onEnableReady?.(undefined); onGestureReady?.(undefined, '编辑已关闭'); onUndoReady?.(undefined); onQuickReady?.(undefined) }
  }, [reason, write, busy, dirty, unknown, managed, suspended, stale, base, head, undoneRevision, selected, selection, frame, result, sessionId, opened.grant, tabSignal])

  const invoke = action => { void transact(active.current, action).catch(() => {}) }
  const locked = suspended || busy
  const notice = message ? h('p', { className: 'fv-edit-status', role: message.error ? 'alert' : 'status' }, message.text) : null
  return h('div', { className: 'fv-edit-top', 'aria-label': '时间线编辑', 'aria-busy': busy },
    h('button', { type: 'button', className: 'fv-edit-toggle', 'aria-pressed': Boolean(write), disabled: locked || (!write && (dirty || stale) && !unknown),
      title: write ? '撤销本会话的编辑许可' : unknown ? '重新授权以读取状态或恢复；不会提交' : '允许本会话编辑工程；松手后校验并保存', onClick: () => invoke(write ? 'revoke-write' : 'authorize-write') }, write ? '退出编辑' : '启用编辑'),
    h('details', { className: 'fv-history fv-disclosure', ref: historyRef },
      h(ToolSummary, { icon: 'undo', label: '历史与恢复' }),
      h('div', { className: 'fv-history-menu', 'aria-label': '版本操作' },
        h('strong', null, '历史与恢复'),
        !managed ? h('p', null, '接管旧工程将建立版本历史。') : null,
        h('div', { className: 'fv-history-actions' },
          !managed ? h('button', { type: 'button', disabled: !write || locked || dirty || stale || unknown, onClick: () => invoke('adopt') }, '确认接管旧工程') : null,
          h('button', { type: 'button', disabled: !write || locked || dirty || stale || unknown || !head || base === undoneRevision, onClick: () => invoke('undo') }, '撤销当前版本'),
          h('button', { type: 'button', disabled: !write || locked, onClick: () => invoke('status') }, '读取状态'),
          h('button', { type: 'button', disabled: !write || locked, onClick: () => invoke('recover') }, '恢复')),
        !write ? h('p', null, unknown ? '先重新启用编辑以读取状态或恢复，新修改仍被禁止。' : '先启用编辑。') : null,
        notice,
        status ? h(StatusReview, { status }) : null)),
    notice && !historyRef ? notice : null)
}
