import React, { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { parseJSON, stringifyJSON } from '../json.mjs'
import { OperationList, PlanReview, StatusReview, operationNames, editError, receiptMessage } from './edit-review.mjs'
import { AgentProposals } from './proposals.mjs'
import { TimelineSettings } from './timeline-settings.mjs'
const h = React.createElement
const id = () => crypto.randomUUID()
export const EDIT_OPERATIONS = ['source.add','source.remove','source.update','track.add','track.remove','track.copy','track.reorder','track.rename','track.references','block.insert','block.remove','block.copy','block.move','block.relink','block.trim','block.split','parameters.update','parameters.unset','project.resize','range.ripple']
export function integerToken(text, signed = false) {
  if (typeof text !== 'string' || !(signed ? /^-?(0|[1-9][0-9]*)$/ : /^(0|[1-9][0-9]*)$/).test(text) || text === '-0' || !Number.isSafeInteger(Number(text))) throw new Error('不可编辑：需要精确安全整数帧/位置，不接受舍入、空白、小数或指数')
  return Number(text)
}
const knownProperties = ['transform','scale','x','y','opacity','fade_in_frames','fade_out_frames','cache_dependencies','volume','pan','loop','duck','duck_params','text','gfx_dependency_policy']
/** Only assembles explicit intents. Source windows, phase, fades and references are kernel decisions. */
export function compileIntent(op, v, { result, blocks, track, frame, makeId = id }) {
  const n = key => integerToken(v[key] ?? '')
  const targetTrack = () => v.track_id || track?.data?.id || (track?.family === 'subtitles' ? 'subtitles' : '')
  const one = () => { if (blocks.length !== 1 || typeof blocks[0].data?.id !== 'string') throw new Error('请选择一个受管块'); return blocks[0].data.id }
  const t = () => { if (!track?.data?.id) throw new Error('请选择一个真实受管轨道（字幕虚拟轨不适用）'); return track.data.id }
  const jsonValue = key => parseJSON(v[key])
  const firstData = () => {
    const data = v.family === 'audio' ? { id: makeId(), src: v.src, in: n('start'), out: n('end'), src_in: n('src_in'), volume: jsonValue('volume') } : v.family === 'subtitles' ? { id: makeId(), in: n('start'), out: n('end'), text: v.text } : { id: makeId(), src: v.src, dst_in: n('start'), dur: n('duration'), ...(v.kind === 'gfx' ? {} : { src_in: n('src_in'), src_out: n('src_out') }) }
    return data
  }
  if (!EDIT_OPERATIONS.includes(op)) throw new Error('Unsupported operation')
  switch (op) {
    case 'source.add': return [{ op, data: { id: makeId(), path: v.path } }]
    case 'source.update': return [{ op, id: v.source_id, set: v.source_field === 'transform' ? { transform: jsonValue('value') } : { path: v.path } }]
    case 'source.remove': return [{ op, id: v.source_id, ...(v.cascade ? { cascade: blocks.map(b => b.data.id) } : {}) }]
    case 'track.add': { if (v.family === 'subtitles') throw new Error('不支持额外字幕轨：仅内核虚拟 subtitles'); const data = { id: makeId(), name: v.name, ...(v.family === 'video' ? { kind: v.kind, clips: [firstData()] } : { events: [firstData()] }) }; return [{ op, family: v.family, index: n('index'), data }] }
    case 'track.remove': return [{ op, id: t() }]
    case 'track.copy': { const old = [t(), ...result.blocks.filter(b => b.family === track.family && b.track_index === track.track_index).map(b => b.data.id)]; return [{ op, id: t(), ids: Object.fromEntries(old.map(key => [key, makeId()])), name: v.name, index: n('index') }] }
    case 'track.rename': return [{ op, id: t(), name: v.name }]
    case 'track.reorder': return [{ op, family: v.family, ids: v.order }]
    case 'track.references': return [{ op, id: t(), set: v.unset ? {} : { [v.reference_field]: jsonValue('value') }, ...(v.unset ? { unset: [v.reference_field] } : {}) }]
    case 'block.insert': return [{ op, track_id: targetTrack(), data: firstData() }]
    case 'block.remove': return blocks.map(b => ({ op, id: b.data.id }))
    case 'block.copy': case 'block.move': {
      if (!blocks.length) throw new Error('请选择受管块')
      const at = n('at'), anchor = blocks[0].range[0]
      return blocks.map(b => { const start = at + (b.range[0] - anchor); if (!Number.isSafeInteger(start) || start < 0) throw new Error('批量位置超出安全整数'); return { op, id: b.data.id, track_id: v.track_id || (b.family === 'subtitles' ? 'subtitles' : result.tracks.find(t => t.family === b.family && t.track_index === b.track_index)?.data.id), at: start, ...(op === 'block.copy' ? { new_id: makeId() } : {}) } })
    }
    case 'block.relink': return [{ op, id: one(), src: v.src }]
    case 'block.trim': return [{ op, id: one(), start: n('start'), end: n('end'), fade_policy: v.fade_policy }]
    case 'block.split': {
      if (!Number.isSafeInteger(frame)) throw new Error('请先设置精确整数播放头')
      return [{ op, id: one(), at: frame, new_id: makeId(), fade_policy: v.fade_policy, ...(blocks[0].family === 'subtitles' ? { text_policy: v.text_policy, ...(v.text_policy === 'explicit' ? { texts: [v.text, v.right_text] } : {}) } : {}) }]
    }
    case 'parameters.update': case 'parameters.unset': {
      if (!knownProperties.includes(v.property)) throw new Error('UI 仅支持合同属性白名单；未知扩展保留、不可直接编辑')
      const target = v.property_target === 'project' ? { target: 'project' } : v.property_target === 'source' ? { id: v.source_id } : v.property_target === 'track' ? { id: t() } : { id: one() }
      const value = op === 'parameters.update' ? v.property === 'text' ? v.value : jsonValue('value') : undefined
      if (/frames$/.test(v.property) && op === 'parameters.update') integerToken(v.value)
      return [{ op, ...target, path: [v.property], ...(op === 'parameters.update' ? { value } : {}) }]
    }
    case 'project.resize': return [{ op, duration_frames: n('duration') }]
    case 'range.ripple': return [{ op, scope: { track_ids: v.scope, subtitles: Boolean(v.subtitles) }, mode: v.mode, crossing: v.crossing, resize: Boolean(v.resize), ...(v.mode === 'shift' ? { at: n('at'), delta: integerToken(v.delta, true) } : { start: n('start'), end: n('end'), ...(v.crossing === 'cut' ? { split_ids: Object.fromEntries((v.split_ids ?? []).map(key => [key, makeId()])), text_policy: 'duplicate-text', fade_policy: v.fade_policy } : {}) }) }]
  }
}

export function ManualEditor({ sessionId, opened, result, selection, selected, frame, tabSignal, call, onCommitted, onGestureReady, onQueueChange, onEditState, onQuickReady, onUndoReady, onGrantDead }) {
  const [write, setWrite] = useState()
  const [op, setOp] = useState('block.move')
  const [v, setV] = useState({ family: 'video', kind: 'aroll', source_field: 'path', property: 'opacity', property_target: 'block', reference_field: 'duck_by', fade_policy: 'preserve-content', text_policy: 'duplicate-text', mode: 'shift', crossing: 'reject', scope: [], split_ids: [], order: [], start: '0', end: '10', duration: '10', src_in: '0', src_out: '10', volume: '1', at: '0', delta: '1', index: '0', value: '1' })
  const [queue, setQueue] = useState([]), [plan, setPlan] = useState(), [status, setStatus] = useState(), [busy, setBusy] = useState(false), [message, setMessage] = useState('')
  const [settingsEpoch, setSettingsEpoch] = useState(0), [, refreshDraft] = useState(0)
  const request = useRef(), generation = useRef(0), live = useRef(true), invalidations = useRef(Promise.resolve()), directTransaction = useRef(false)
  const frameSafety = useRef({ dirty: false, busy: false, unknown: false })
  const proposalSafety = useRef({ dirty: false, busy: false, unknown: false })
  const [proposalBlocked, setProposalBlocked] = useState(false)
  // True when a kernel ok receipt was already answered but the authoritative
  // view refresh failed afterwards (commit/adopt/undo ok = confirmed write;
  // status/recover ok = NOT write proof). The stale view must stay fail-closed
  // (no further write/frame export from it), yet a confirmed write must
  // NOT be relabeled outcome_unknown and must never be auto-retried.
  const [staleLock, setStaleLock] = useState(false)
  // Dirty ownership: the stale-view lock owns its dirty ONLY while the
  // authoritative refresh has not succeeded. A later successful refresh may
  // clear stale-owned dirty, but NEVER the user's unsaved draft/queue dirty
  // (form edits, quick edits, gestures, queued intents stay fail-closed).
  const staleOwnedDirty = useRef(false), draftDirty = useRef(false)
  const safety = change => {
    frameSafety.current = { ...frameSafety.current, ...change }
    onEditState?.(Object.fromEntries(['dirty','busy','unknown'].map(key => [key, frameSafety.current[key] || proposalSafety.current[key]])))
  }
  const proposalState = change => { proposalSafety.current = change; setProposalBlocked(change.dirty || change.busy || change.unknown); safety({}) }
  const managed = opened.snapshot.document?._vtx?.protocol === 1
  const allSelected = selected ?? (selection ? [selection] : [])
  const blocks = result.blocks.filter(b => allSelected.some(s => s.kind === 'block' && s.address === b.address))
  const primaryBlock = blocks.find(b => selection?.kind === 'block' && b.address === selection.address)
  const track = selection?.kind === 'track' ? result.tracks.find(t => t.address === selection.address) : primaryBlock ? result.tracks.find(t => t.family === primaryBlock.family && t.track_index === primaryBlock.track_index) : undefined
  const base = opened.snapshot.content_version
  const payload = (action, extra = {}) => ({ op: action, grant: opened.grant, ...(write ? { writeGrant: write } : {}), ...extra })
  function invalidate() {
    generation.current++; setPlan(undefined)
    // A delayed invalidation must not overtake the next plan. Keep every
    // acknowledgement, fail closed on transport loss, and await this barrier.
    if (write && !directTransaction.current) invalidations.current = Promise.all([invalidations.current, call(sessionId, payload('invalidate-plan'), tabSignal).then(() => undefined, error => error)]).then(outcomes => outcomes.find(Boolean))
  }
  useLayoutEffect(() => { invalidate() }, [selection, selected, frame, base])
  useEffect(() => {
    // These checkboxes are selection-derived draft input, not queued intent.
    // Never submit a now-hidden former selection as the next ripple split target.
    setV(previous => {
      const split_ids = previous.split_ids.filter(key => blocks.some(b => b.data.id === key))
      return split_ids.length === previous.split_ids.length ? previous : { ...previous, split_ids }
    })
  }, [selection, selected, base])
  useEffect(() => { live.current = true; return () => { live.current = false; request.current?.abort() } }, [])
  const queueObserver = useRef(onQueueChange), publishedQueue = useRef(queue)
  queueObserver.current = onQueueChange
  useLayoutEffect(() => {
    if (publishedQueue.current !== queue) { publishedQueue.current = queue; queueObserver.current?.(queue) }
  }, [queue])
  const gestureOwner = useRef()
  const gestureReason = staleLock ? '请刷新工程' : frameSafety.current.unknown || proposalSafety.current.unknown ? '先读取状态 / 恢复' : busy ? '正在处理中' : proposalBlocked ? '先处理智能体提案' : !write ? '请先启用编辑' : !managed ? '请先接管旧工程' : frameSafety.current.dirty || queue.length || plan ? '先清空待保存操作' : ''
  useLayoutEffect(() => {
    const token = {}; gestureOwner.current = token
    const gesture = async (operations, options = {}) => {
      if (gestureOwner.current !== token || !live.current || tabSignal.aborted || gestureReason || frameSafety.current.dirty || frameSafety.current.busy || frameSafety.current.unknown || proposalSafety.current.dirty || proposalSafety.current.busy || proposalSafety.current.unknown) throw new Error('编辑状态已变化，请重新操作。')
      if (!Array.isArray(operations) || operations.some(operation => !operation || !['block.move', 'block.trim', 'range.ripple'].includes(operation.op) || operation.op === 'range.ripple' && (operation.mode !== 'delete' || operation.crossing !== 'reject' || operation.resize !== false))) throw new Error('此入口仅支持移动、向内裁剪或闭合空隙。')
      if (!operations.length) return { ok: true, noop: true }
      return saveGesture(parseJSON(stringifyJSON(operations)), options)
    }
    onGestureReady?.(gestureReason ? undefined : gesture, gestureReason)
    return () => { if (gestureOwner.current === token) gestureOwner.current = undefined; onGestureReady?.(undefined, '编辑已关闭') }
  }, [write, managed, busy, base, selected, selection, frame, result, sessionId, opened.grant, proposalBlocked, staleLock, gestureReason])
  async function saveGesture(operations, { focusId } = {}) {
    const epoch = generation.current
    const controller = new AbortController(); request.current = controller
    const signal = AbortSignal.any([tabSignal, controller.signal])
    directTransaction.current = true; safety({ busy: true }); setBusy(true); setMessage('正在保存…')
    let committing = false, applied = false, operationId
    try {
      const failure = await invalidations.current
      signal.throwIfAborted()
      if (failure) throw new Error('PLAN_INVALIDATION_FAILED: 旧计划撤销未确认')
      if (epoch !== generation.current) throw new Error('编辑状态已变化，请重新操作。')
      const prepared = await call(sessionId, payload('plan', { base_revision: base, operations }), signal)
      signal.throwIfAborted()
      if (!prepared.ok) throw Object.assign(new Error(prepared.message ?? editError(prepared)), { code: prepared.code })
      operationId = prepared.submitted_request?.op_id
      // Selection/frame changes can cancel preparation, but never invalidate an
      // owned token after commit dispatch or interrupt its definitive receipt.
      if (epoch !== generation.current || proposalSafety.current.dirty || proposalSafety.current.busy || proposalSafety.current.unknown || !live.current) {
        await call(sessionId, payload('invalidate-plan'), signal)
        throw new Error('编辑状态已变化，请重新操作。')
      }
      committing = true
      const answer = await call(sessionId, payload('commit', { planToken: prepared.planToken }), signal)
      applied = answer.ok === true
      if (!applied) {
        committing = answer.code === 'outcome_unknown' || answer.outcome === 'unknown'
        throw Object.assign(new Error(answer.message ?? editError(answer)), { code: answer.code, op_id: answer.op_id ?? operationId })
      }
      await onCommitted({ focusId })
      safety({ dirty: false, unknown: false }); setStaleLock(false); staleOwnedDirty.current = false; draftDirty.current = false
      if (live.current) setMessage('已保存。')
      return { ok: true, op_id: operationId, answer }
    } catch (cause) {
      const error = Object.assign(new Error(editError(cause)), { code: cause.code, op_id: cause.op_id ?? operationId })
      if (applied) {
        setStaleLock(true); staleOwnedDirty.current = true; draftDirty.current = false; safety({ dirty: true, unknown: false })
        error.applied = true; error.message = '内核已保存，但工程刷新失败。旧视图已锁定，请刷新或重开工程。'
      } else if (committing) {
        safety({ unknown: true }); error.outcomeUnknown = true
        error.message = `结果未知；原操作 ID：${operationId ?? '见内核历史'}。请读取状态 / 恢复，不要重试保存。`
      } else if (cause.code === 'recovery_required') { safety({ unknown: true }); error.recoveryRequired = true }
      if (live.current) setMessage(error.message)
      throw error
    } finally {
      directTransaction.current = false; safety({ busy: false }); if (live.current) setBusy(false)
    }
  }
  const undoOwner = useRef()
  const [undoneRevision, setUndoneRevision] = useState()
  useLayoutEffect(() => {
    const token = {}; undoOwner.current = token
    const available = !gestureReason && base !== undoneRevision && Boolean(opened.snapshot.document?._vtx?.head)
    onUndoReady?.(available ? () => {
      if (undoOwner.current !== token || !live.current || tabSignal.aborted || frameSafety.current.busy || frameSafety.current.dirty || frameSafety.current.unknown || proposalSafety.current.dirty || proposalSafety.current.busy || proposalSafety.current.unknown) return Promise.reject(new Error('编辑状态已变化，请重新操作。'))
      return run('undo', { base_revision: base, head: opened.snapshot.document._vtx.head })
    } : undefined)
    return () => { if (undoOwner.current === token) undoOwner.current = undefined; onUndoReady?.(undefined) }
  }, [gestureReason, opened.snapshot.document?._vtx?.head, base, write, sessionId, opened.grant, undoneRevision])
  const quickOwner = useRef()
  useLayoutEffect(() => {
    const token = {}
    quickOwner.current = token
    const available = write && managed && !busy && !proposalBlocked && !staleLock && !frameSafety.current.unknown
    const quick = operation => {
      // Retained handlers cannot enqueue against a newer target/frame/grant.
      if (quickOwner.current !== token || !live.current || tabSignal.aborted || !available || staleLock || frameSafety.current.busy || frameSafety.current.unknown || proposalSafety.current.dirty || proposalSafety.current.busy || proposalSafety.current.unknown) throw new Error('EDIT_STALE: 剪辑目标或写许可已变化，请重新选择。')
      if (!['block.split', 'block.remove'].includes(operation)) throw new Error('EDIT_UNSUPPORTED: 快捷入口只组装分割或移除 intent。')
      if (operation === 'block.split' && track?.data?.kind === 'gfx') throw new Error('EDIT_UNSUPPORTED: 图形轨不支持分割，请使用移动、复制或移除。')
      if (operation === 'block.split' && (blocks.length !== 1 || !Number.isSafeInteger(frame) || frame <= blocks[0].range[0] || frame >= blocks[0].range[1])) throw new Error('EDIT_BOUNDS: 请将播放头固定在选中片段的内部再分割。')
      const operations = compileIntent(operation, v, { result, blocks, track, frame })
      if (!operations.length) throw new Error('请选择可编辑片段。')
      draftDirty.current = true; safety({ dirty: true }); invalidate(); setQueue(previous => previous.concat(operations))
      setMessage('已加入待保存操作。')
    }
    onQuickReady?.(available ? quick : undefined)
    return () => { if (quickOwner.current === token) quickOwner.current = undefined; onQuickReady?.(undefined) }
  }, [write, managed, busy, base, selected, selection, frame, proposalBlocked, staleLock, v])
  const settingsDisabled = !write || !managed || busy || proposalBlocked || staleLock || frameSafety.current.unknown
  const settingsChanged = () => {
    if (settingsDisabled || tabSignal.aborted || !live.current) return false
    draftDirty.current = true; safety({ dirty: true }); invalidate(); refreshDraft(previous => previous + 1)
    return true
  }
  const settingsQueued = operations => {
    if (!settingsChanged()) return
    setQueue(previous => previous.concat(operations))
    setMessage('配置已加入待保存操作。')
  }
  const set = (key, value) => { draftDirty.current = true; safety({ dirty: true }); invalidate(); setV(previous => ({ ...previous, [key]: value })) }
  async function run(action, extra = {}) {
    if (frameSafety.current.busy || (['plan','commit','undo','adopt'].includes(action) && (staleLock || frameSafety.current.unknown || proposalSafety.current.dirty || proposalSafety.current.busy || proposalSafety.current.unknown))) return
    safety({ busy: true })
    request.current?.abort()
    const controller = new AbortController(); request.current = controller
    const signal = AbortSignal.any([tabSignal, controller.signal])
    const epoch = generation.current
    // Two distinct receipt classes, never conflated:
    // - confirmedWrite: commit/adopt/undo receipt ok — the write IS known applied.
    // - receiptOnly: status/recover receipt ok — proves nothing about a
    //   previously unknown write; the prior unknown verdict must survive.
    let confirmedWrite = false, receiptOnly = false
    const operationId = ['adopt','undo'].includes(action) ? id() : plan?.submitted_request?.op_id
    if (['adopt','undo'].includes(action)) extra = { ...extra, op_id: operationId }
    setBusy(true); setMessage(`正在执行内核命令…${operationId ? ` op_id ${operationId}` : ''}`)
    try {
      if (action === 'plan') {
        const failure = await invalidations.current
        signal.throwIfAborted()
        if (failure) throw new Error('PLAN_INVALIDATION_FAILED: 无法确认旧计划已撤销；请重新打开工程/授予许可')
        if (epoch !== generation.current) throw new Error('PLAN_INVALID: 等待撤销时选择或 intent 已变化')
      }
      const answer = await call(sessionId, payload(action, extra), signal)
      if (!live.current || signal.aborted) return
      if (action === 'authorize-write') { invalidations.current = Promise.resolve(); setWrite(answer.writeGrant) }
      else if (action === 'revoke-write') { setWrite(undefined); invalidate() }
      else if (action === 'plan' && answer.ok && epoch === generation.current) setPlan(answer)
      else if (action === 'status' || action === 'recover') { setStatus(answer); invalidate(); if (answer.ok) { receiptOnly = true; await onCommitted(); if (answer.pending === false) safety({ unknown: false }); setStaleLock(false); if (staleOwnedDirty.current) { staleOwnedDirty.current = false; if (!draftDirty.current) safety({ dirty: false }) } } }
      else if (['commit','undo','adopt'].includes(action)) { setPlan(undefined); if (answer.ok) { confirmedWrite = true; setQueue(previous => previous.length ? [] : previous); await onCommitted(); safety({ dirty: false, unknown: false }); setStaleLock(false); staleOwnedDirty.current = false; draftDirty.current = false } else if (answer.code === 'outcome_unknown' || answer.outcome === 'unknown') safety({ unknown: true }) }
      if (answer.code === 'recovery_required' || answer.code === 'outcome_unknown' || answer.outcome === 'unknown') safety({ unknown: true })
      if (action === 'undo' && answer.ok) setUndoneRevision(answer.revision)
      setMessage(receiptMessage(action, answer) + (answer.code === 'outcome_unknown' ? ` 原操作 ID：${answer.op_id ?? operationId ?? '见内核历史'}` : ''))
      return answer
    } catch (error) {
      if (confirmedWrite || receiptOnly) {
        // The kernel receipt itself settled ok; ONLY the authoritative-view
        // refresh failed. Lock the stale view against further writes and frame
        // export (dirty blocks via the frame owner) and never auto-retry.
        // A confirmed write is known applied (unknown cleared); a plain
        // status/recover receipt is NOT write proof — any prior unknown
        // verdict must survive untouched.
        setStaleLock(true)
        staleOwnedDirty.current = true
        if (confirmedWrite) { safety({ dirty: true, unknown: false }); draftDirty.current = false }
        else safety({ dirty: true })
        if (live.current) setMessage(confirmedWrite
          ? '内核已确认本次写入（ok/applied）；权威视图刷新失败。旧视图已锁定，请刷新或重开工程。'
          : '状态回执已取得，但权威视图刷新失败。旧视图已锁定，请刷新；原未知结果判定保持不变。')
      } else {
        if (['commit','adopt','undo','recover'].includes(action)) safety({ unknown: true })
        if (live.current) setMessage(`${editError(error)}${['commit','adopt','undo','recover'].includes(action) ? ` 结果未知；原操作 ID：${operationId ?? '见内核历史'}。请读取状态 / 恢复，不要重试保存。` : ''}`)
      }
    } finally { safety({ busy: false }); if (live.current) setBusy(false) }
  }
  function clear() {
    invalidate(); setQueue(previous => previous.length ? [] : previous); setSettingsEpoch(previous => previous + 1)
    if (!staleOwnedDirty.current) { draftDirty.current = false; safety({ dirty: false }) }
    setMessage('已清空待保存操作。')
  }
  function remove(index) {
    if (busy || staleLock) return
    if (queue.length === 1) { clear(); return }
    invalidate(); setQueue(previous => previous.filter((_, position) => position !== index))
  }
  function add() {
    if (settingsDisabled) return
    draftDirty.current = true
    safety({ dirty: true })
    try { const operations = compileIntent(op, v, { result, blocks, track, frame }); if (!operations.length) throw new Error('请选择操作对象'); invalidate(); setQueue(previous => previous.concat(operations)); setMessage('已加入待保存操作。') } catch (error) { setMessage(editError(error)) }
  }
  const field = (key, title, type = 'text') => h('label', { key, style: { display: 'block' } }, title, h('input', { type, value: v[key] ?? '', onChange: e => set(key, e.target.value), style: { display: 'block', width: '100%', boxSizing: 'border-box' } }))
  const select = (key, title, items) => h('label', { key, style: { display: 'block' } }, title, h('select', { value: v[key] ?? '', onChange: e => set(key, e.target.value), style: { width: '100%' } }, items.map(([value, label]) => h('option', { key: value, value }, label))))
  const check = (key, title) => h('label', { key, style: { display: 'block' } }, h('input', { type: 'checkbox', checked: Boolean(v[key]), onChange: e => set(key, e.target.checked) }), title)
  const sources = opened.snapshot.document.sources ?? []
  const sourceSelect = () => select('source_id', '素材 ID', [['','请选择素材'], ...sources.filter(s => typeof s.id === 'string').map(s => [s.id, `${s.id} · ${s.path}`])])
  const trackSelect = () => select('track_id', '目标轨（空=原轨）', [['','保留原轨'], ...result.tracks.filter(t => t.family === 'subtitles' || typeof t.data?.id === 'string').map(t => [t.family === 'subtitles' ? 'subtitles' : t.data.id, `${t.family} · ${t.data.name ?? t.address}`])])
  const fade = () => select('fade_policy', '淡入淡出策略（显式）', [['preserve-content','保留内容曲线（无法精确保留则拒绝）'], ['reapply-edge-fades','重新应用边缘 fade（行为变化）']])
  const family = () => select('family', '对象族', [['video','视频 / gfx'],['audio','音频'],['subtitles','内联字幕']])
  const newBlock = () => [family(), v.family === 'video' ? select('kind','视频轨类型', [['aroll','aroll'],['broll','broll'],['gfx','gfx']]) : null, field('src','源 ID / gfx 文件路径（明确引用，不探素材）'), field('start','全局起点帧'), v.family === 'video' ? field('duration','时长帧') : field('end','全局终点帧'), v.family !== 'subtitles' ? field('src_in','源起点帧（显式）') : field('text','字幕文本'), v.family === 'video' && v.kind !== 'gfx' ? field('src_out','源终点帧（显式）') : null, v.family === 'audio' ? field('volume','音量（JSON 数值）') : null]
  let form = []
  if (op.startsWith('source.')) form = [sourceSelect(), op !== 'source.remove' ? field('path','素材路径（不复制媒体）') : check('cascade','确认 cascade 恰为时间轴多选块 ID；内核核对完整用户集合'), op === 'source.update' ? select('source_field','更新字段', [['path','路径'],['transform','transform（单个 JSON 属性）']]) : null, op === 'source.update' && v.source_field === 'transform' ? field('value','transform JSON 属性') : null]
  if (op === 'track.add') form = [field('name','轨道名称'),field('index','数组插入位置'),...newBlock()]
  if (op === 'track.copy') form = [field('name','副本名称'),field('index','数组插入位置')]
  if (op === 'track.rename') form = [field('name','轨道新名称')]
  if (op === 'track.references') form = [select('reference_field','引用字段', [['duck_by','duck_by 轨名 JSON 数组'],['duck_strategy','duck_strategy JSON 属性']]), check('unset','移除该可选引用字段'),field('value','引用值（duck_by 为 JSON 字符串数组，如 ["voice"]）')]
  if (op === 'track.reorder') {
    const order = v.order.length ? v.order : result.tracks.filter(t => t.family === v.family).map(t => t.data.id)
    form = [family(), h('ol', { key: 'order' }, order.map((key,i) => h('li', { key }, key, ...[-1,1].map(d => h('button',{ type:'button',key:d,disabled:i+d<0||i+d>=order.length,onClick:()=>{ const next=[...order]; [next[i],next[i+d]]=[next[i+d],next[i]];set('order',next) }},d<0?'上移':'下移'))))),h('button',{type:'button',key:'useorder',onClick:()=>set('order',order)},'采用此完整轨序')]
  }
  if (op === 'block.insert') form = [trackSelect(), ...newBlock()]
  if (['block.copy','block.move'].includes(op)) form = [trackSelect(),field('at','首选块新起点帧（多选保持相对位置）')]
  if (op === 'block.relink') form = [field('src','新 source ID / gfx 文件路径')]
  if (op === 'block.trim') form = [field('start','新的全局起点帧（仅 inward）'),field('end','新的全局终点帧（仅 inward）'),fade()]
  if (op === 'block.split') form = [h('p',{key:'head'},`分割点 = 精确播放头 ${frame ?? '未设置'}`),fade(),select('text_policy','字幕文本策略', [['duplicate-text','明确复制文本到两半'],['explicit','分别输入两段文本']]),field('text','左段文本'),field('right_text','右段文本')]
  if (op.startsWith('parameters.')) form = [select('property_target','属性目标', [['block','选中块'],['track','选中轨'],['source','素材'],['project','工程']]),sourceSelect(), select('property','白名单属性（不适用对象由内核精确拒绝）',knownProperties.map(p=>[p,p])),op === 'parameters.update' ? field('value','属性值（text 为文本，其余单个 JSON 值）') : null]
  if (op === 'project.resize') form = [field('duration','工程新时长帧（不改 FPS/分辨率）')]
  if (op === 'range.ripple') form = [select('mode','ripple 模式',[['shift','平移'],['delete','删除区间']]),select('crossing','跨界策略',[['reject','跨界拒绝'],['cut','明确剪切（仅 delete）']]),check('resize','同时调整画布时长'),check('subtitles','明确包含内联字幕'),h('fieldset',{key:'scope'},h('legend',null,'显式轨集合（不隐式联动 AV）'), result.tracks.filter(t=>t.data?.id).map(t=>h('label',{key:t.data.id,style:{display:'block'}},h('input',{type:'checkbox',checked:v.scope.includes(t.data.id),onChange:e=>set('scope', e.target.checked?v.scope.concat(t.data.id):v.scope.filter(k=>k!==t.data.id))}),`${t.family} ${t.data.name} ${t.data.id}`))),field('at','shift 起点帧'),field('delta','shift 有符号整数偏移'),field('start','delete 起点帧'),field('end','delete 终点帧'),fade(), h('fieldset',{key:'splits'},h('legend',null,'确认为跨两端块提供右侧新 ID（内核要求恰好枚举）'),blocks.map(b=>h('label',{key:b.data.id,style:{display:'block'}},h('input',{type:'checkbox',checked:v.split_ids.includes(b.data.id),onChange:e=>set('split_ids',e.target.checked?v.split_ids.concat(b.data.id):v.split_ids.filter(k=>k!==b.data.id))}),b.data.id)))]
  const context = { tracks: result.tracks, blocks: result.blocks, sources }
  const locked = staleLock || frameSafety.current.unknown || proposalBlocked
  return h('section', { 'aria-label': '手动语义编辑', className: 'fv-edit-panel' },
    h('div', { className: 'fv-edit-header' }, h('h3', null, '待保存'), h('span', { className: 'fv-edit-meta' }, `${queue.length} 项`),
      h('button', { type: 'button', disabled: busy, title: '明确允许本会话向此工程及内核历史写入；拖动松手即预检并保存，高级批次仍需单独确认。', onClick: () => void run(write ? 'revoke-write' : 'authorize-write') }, write ? '撤销许可' : '启用编辑')),
    h('p', { className: 'fv-edit-meta' }, write ? '已允许本会话写入工程及历史 · 拖动松手即保存' : '启用后拖动松手即保存；允许本会话写入工程及历史'),
    staleLock ? h('p', { role: 'alert', className: 'fv-edit-status' }, '权威视图刷新失败；旧视图已锁定，请刷新或重开工程。') : null,
    frameSafety.current.unknown ? h('p', { role: 'alert', className: 'fv-edit-status' }, '结果未知或历史待恢复：保留原操作 ID，先读取状态 / 恢复。') : null,
    !managed ? h('div', { className: 'fv-edit-status' }, h('p', null, '旧工程只读。接管将分配稳定 ID 并建立持久历史。'), h('button', { type: 'button', disabled: !write || busy || locked, onClick: () => void run('adopt', { base_revision: base, confirmation: 'stable-ids-and-history' }) }, '确认接管旧工程')) : null,
    queue.length ? h(OperationList, { operations: queue, context, onRemove: remove, disabled: busy || staleLock }) : h('p', { className: 'fv-edit-meta' }, '高级表单与选区动作的修改会列在这里。'),
    h('div', { className: 'fv-edit-actions' },
      h('button', { type: 'button', disabled: !write || busy || locked || !queue.length, title: '内核零写预检', onClick: () => void run('plan', { base_revision: base, operations: queue }) }, '预检'),
      h('button', { type: 'button', disabled: busy || staleLock || (!queue.length && !draftDirty.current), onClick: clear }, '清空')),
    plan ? h('div', { className: 'fv-edit-plan' }, h(PlanReview, { plan, context, showOperations: false }), h('button', { type: 'button', disabled: !write || busy || locked, title: '明确提交此冻结计划，一次版本事务', onClick: () => void run('commit', { planToken: plan.planToken }) }, '保存更改')) : null,
    busy ? h('button', { type: 'button', title: '取消并等待子进程回收，写入结果可能未知', onClick: () => { request.current?.abort(); void call(sessionId, payload('cancel-write'), tabSignal).catch(() => {}) } }, '取消') : null,
    message ? h('p', { role: 'status', 'aria-live': 'polite', className: 'fv-edit-status' }, message) : null,
    h('details', { className: 'fv-edit-advanced' }, h('summary', null, '高级编辑'),
      h('fieldset', { disabled: settingsDisabled, className: 'fv-edit-form' }, h('legend', null, '操作表单'), selectOperation(), ...form, h('button', { type: 'button', onClick: add }, '加入批次')),
      h(TimelineSettings, { key: `${opened.grant}:${base}:${settingsEpoch}`, document: opened.snapshot.document, tracks: result.tracks, disabled: settingsDisabled, onChange: settingsChanged, onQueue: settingsQueued })),
    h('details', { className: 'fv-edit-advanced', open: frameSafety.current.unknown || staleLock || undefined }, h('summary', null, '历史与恢复'),
      h('div', { className: 'fv-edit-actions' },
        h('button', { type: 'button', disabled: !write || busy, title: '读取内核状态 / 历史，可能初始化历史', onClick: () => void run('status') }, '读取状态'),
        h('button', { type: 'button', disabled: !write || busy, title: '显式内核恢复，不另建操作', onClick: () => void run('recover') }, '恢复'),
        h('button', { type: 'button', disabled: !write || busy || locked || !opened.snapshot.document?._vtx?.head, title: '撤销当前内核版本，创建新的事务', onClick: () => void run('undo', { base_revision: base, head: opened.snapshot.document._vtx.head }) }, '撤销当前版本')),
      status ? h(StatusReview, { status }) : null),
    h(AgentProposals, { sessionId, opened, result, tabSignal, call, writeGrant: write, manualBlocked: frameSafety.current.dirty || frameSafety.current.unknown || busy || staleLock || Boolean(plan) || queue.length > 0, onCommitted, onEditState: proposalState, onGrantDead }))
  function selectOperation() { return h('label', { key: 'operation' }, '编辑操作', h('select', { value: op, onChange: e => { draftDirty.current = true; safety({ dirty: true }); invalidate(); setOp(e.target.value) } }, EDIT_OPERATIONS.map(value => h('option', { key: value, value }, operationNames[value])))) }
}
