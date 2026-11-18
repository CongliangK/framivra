import React, { useEffect, useRef, useState } from 'react'
import { parseJSON, stringifyJSON } from '../json.mjs'
import { captureCurrentDraft, performReferenceAction } from './reference.mjs'
import { VisualTimeline } from './timeline.mjs'
import { ManualEditor } from './manual.mjs'
import { emptySelection, transitionSelection, preserveSelection } from './selection.mjs'
import { FrameEpoch } from './frame.mjs'
import { ProgramMonitor } from './monitor.mjs'
import { registerBundleConfig } from './settings.mjs'
import { createOfficialDraftServices, draftResultMessage } from './draft-services.mjs'
import { WorkbenchStyles, FileBrowser, buildMismatchMessage } from './workbench.mjs'
import { Icon } from './icons.mjs'
import { ToolButton, ToolSummary } from './icon-controls.mjs'
import { boundedText, CONTROL_REQUEST_LIMIT } from '../bounded.mjs'
import { LayoutDivider, SelectionSummary, Transport, editingTarget, stepFrame, clipName, familyName } from './editor-controls.mjs'
import { terminalGrantFailure, GRANT_DEAD_MESSAGE } from './grant-lifecycle.mjs'
import { StoragePanel } from './storage.mjs'
import { formatTimecode, projectClock } from './timecode.mjs'

const h = React.createElement
const ID = 'framivra-dsh-sidebar'
export const inject = ['slots', 'sidebarRightTabs', 'configForms', 'conversation', 'sessions', 'uiSession']
const pretty = value => stringifyJSON(value, 2)
function JSONDetails({ title, data }) {
  // Keep immutable envelopes cheap while scrubbing, without expanding the
  // shared-React loader's module-materialization surface.
  const cached = useRef()
  if (!cached.current || cached.current.data !== data) cached.current = { data, text: pretty(data) }
  return h('details', null, h('summary', null, title), h('pre', { style: { whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' } }, cached.current.text))
}
const details = (title, data, key) => h(JSONDetails, { title, data, key })

// Also used by wire/presentation regressions: response.json() would round again.
export const decodeControlResponse = text => parseJSON(text)

// Match the Host's source-token contract before any floating-point conversion.
// Decimal/exponent spellings (even 1.0/1e0), signs and whitespace are not canonical.
export function frameQuery(input) {
  if (input === '') return {}
  const max = '9007199254740991'
  if (typeof input !== 'string' || /[^0-9]/.test(input) || (input.length > 1 && input[0] === '0') ||
      input.length > max.length || (input.length === max.length && input > max)) {
    throw new Error('BAD_QUERY: Frame must be a canonical nonnegative integer from 0 to 9007199254740991 (digits only; no signs, leading zeros, decimals, exponents or whitespace). Blank selects all blocks.')
  }
  return { frame: Number(input) } // Exact and safe only after lexical and bound checks.
}

async function control(sessionId, payload, signal) {
  const response = await fetch('/api/framivra/control', {
    method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...payload, sessionId }), signal,
  })
  const data = decodeControlResponse(await boundedText(response, CONTROL_REQUEST_LIMIT, signal))
  signal?.throwIfAborted()
  if (!response.ok) throw Object.assign(new Error(`${data.error}: ${data.message}${data.diagnostics ? `\n${pretty(data.diagnostics)}` : ''}`), { code: data.error, op_id: data.op_id })
  return data
}

/** Pure presentation: all addresses, ranges, selection and raw fields come from vtx. */
export function TimelineView({ snapshot, result, hits, frame, onFrame, onSelect, activeSelection, onSelectionChange, selectionIntent, onGesture, onGestureState, gestureUnavailable, onOpenEdits, onOpenHistory, editActionLabel, onUndo, selectionTools, focusTool, compact = false }) {
  if (snapshot.content_version !== result.content_version) return h('p', { role: 'alert' }, 'Timeline changed between reads. Reopen preview; mixed-version selection is not displayed.')
  return h(React.Fragment, null,
    h(VisualTimeline, { snapshot, result, hits, frame, onFrame, onSelect, activeSelection, onSelectionChange, selectionIntent, onGesture, onGestureState, gestureUnavailable, onOpenEdits, onOpenHistory, editActionLabel, onUndo, selectionTools, focusTool, compact }),
    compact ? null : timelineData({ snapshot, result, hits, onSelect }))
}
function timelineData({ snapshot, result, hits, onSelect }) {
  return h('details', { className: 'fv-data' }, h('summary', null, '项目数据与诊断'),
    h('p', null, `Read-only · ${snapshot.content_version}`),
    h('p', null, 'Stored timeline blocks only; not render validation. Addresses are transient within this content version.'),
    ...[snapshot, result].filter(item => !item.coverage.complete).map((item, index) => h('div', { key: index, role: 'alert' },
      h('strong', null, 'Incomplete coverage: external subtitles/assets were not expanded.'), details('Unexpanded inputs', item.coverage))),
    details('Diagnostics', { inspect: snapshot.diagnostics, query: result.diagnostics, frameQuery: hits?.diagnostics }),
    hits ? h('section', { 'aria-label': '帧查询详情' }, h('p', null, `本次 kernel 查询命中 ${hits.blocks.length} 块；完整时间轴保留，黄色底边仅强调命中。`), details('帧查询 envelope（独立详情）', hits)) : null,
    h('details', null, h('summary', null, '全部轨道/块字段与选择（展开）'),
    result.tracks.map(track => h('section', { key: `${track.family}:${track.track_index}`, style: { borderTop: '1px solid currentColor', padding: '8px 0' } },
      h('h3', null, `${track.family} / track ${track.track_index}`),
      onSelect ? h('button', { type: 'button', onClick: () => onSelect({ kind: 'track', address: track.address }) }, `选择轨道 ${track.address}`) : null,
      details(`Track ${track.address} — all vtx-emitted fields`, track.data),
      result.blocks.filter(block => block.family === track.family && block.track_index === track.track_index).map(block =>
        h('div', { key: block.address },
          onSelect ? h('button', { type: 'button', onClick: () => onSelect({ kind: 'block', address: block.address }) }, `选择块 ${block.address}`) : null,
          details(`${block.address} · frames ${pretty(block.range)}`, block.data, block.address)))))),
    result.tracks.length === 0 ? h('p', null, 'No tracks matched this query.') : null,
    h('p', null, 'All fields of the vtx-emitted read document are shown; wire numeric tokens are preserved. Original source numeric/lexical precision is not guaranteed by the current upstream protocol.'),
    details('Complete vtx-emitted timeline document (all emitted fields)', snapshot.document),
    details('Complete query envelope', result))
}

export function ReferenceTools({ selection, sessionId, opened, tabSignal, getDraftServices, onStale, onGrantDead, timeline, selectionDisplay }) {
  const [state, setState] = useState({})
  const task = useRef()
  useEffect(() => () => task.current?.abort(), [selection, sessionId, opened.grant, opened.snapshot.content_version, tabSignal])
  async function run(action) {
    task.current?.abort()
    const controller = new AbortController()
    task.current = controller
    const signal = AbortSignal.any([controller.signal, tabSignal])
    try {
      const captured = action === 'draft' ? captureCurrentDraft(getDraftServices, sessionId) : undefined
      setState({ busy: true })
      // Stored references do not claim that the clip intersects the playhead.
      const result = await performReferenceAction({ action, selection, contentVersion: opened.snapshot.content_version, signal, captured, timeline, selectionDisplay,
        clipboard: globalThis.navigator?.clipboard,
        read: (query, signal) => control(sessionId, { op: 'reference', grant: opened.grant, query }, signal),
      })
      if (action !== 'draft') signal.throwIfAborted()
      setState({ message: result.status === 'copied' ? '已复制。' : result.status === 'ready' ? undefined : result.status === 'applied' ? '已加入会话草稿。' : draftResultMessage(result), ready: result.status === 'ready' })
    } catch (error) {
      if (signal.aborted) return
      const stale = /STALE_REFERENCE|reference\.stale/.test(error.message)
      const dead = terminalGrantFailure(error.message)
      if (stale) onStale()
      if (dead) onGrantDead?.(dead, opened.grant)
      const message = stale || dead ? '请刷新时间线后再试。' : /CLIPBOARD|NotAllowedError/i.test(`${error.name} ${error.message}`) ? '无法访问剪贴板，请检查浏览器权限。' : /DRAFT_UNAVAILABLE/.test(error.message) ? '请先打开一个已有会话，再加入草稿。' : /RAW_UNAVAILABLE/.test(error.message) ? '此轨道没有可复制的原文。' : '无法读取此选区，请刷新后重试。'
      setState({ error: true, message })
    }
  }
  useEffect(() => { setState({}); if (selection) void run('read') }, [selection, sessionId, opened.grant, opened.snapshot.content_version, tabSignal])
  if (!selection) return null
  const action = (key, icon, label) => h(ToolButton, { key, icon, label, disabled: state.busy, onClick: () => { void run(key) } })
  return h('div', { className: 'fv-reference', 'aria-label': '时间线引用' },
    h('div', { className: 'fv-reference-actions', role: 'group', 'aria-label': '引用与草稿工具' },
      action('id', 'id', '复制 ID / 临时定位'), action('draft', 'draft', '加入当前会话草稿（不发送）'),
      h('details', { className: 'fv-reference-more' }, h(ToolSummary, { icon: 'more', label: '更多引用操作' }),
        h('div', { className: 'fv-reference-extra' }, action('raw', 'code', '复制 raw JSON'), action('diagnostic', 'diagnostic', '复制诊断引用 JSON'))),
      state.busy ? h(ToolButton, { icon: 'cancel', label: '取消引用操作', onClick: () => { task.current?.abort(); setState({ message: '已取消。' }) } }) : null),
    state.ready ? h('span', { className: 'fv-sr-only', role: 'status' }, '引用就绪。') : null,
    state.message ? h('p', { role: state.error ? 'alert' : 'status' }, state.message) : null)
}

function SelectionCard({ selection, items, result, fps, onPin, children }) {
  const block = selection?.kind === 'block' ? result.blocks.find(item => item.address === selection.address) : undefined
  const track = result.tracks.find(item => block ? item.family === block.family && item.track_index === block.track_index : item.address === selection?.address)
  if (!track && !block) return h('div', { className: 'fv-inspector-empty' }, h(Icon, { name: 'clip', size: 19 }), h('p', null, '选择片段或轨道以查看属性。'))
  const family = track?.data?.kind === 'gfx' ? 'gfx' : (block ?? track).family
  const kind = familyName(family), trackName = clipName(track?.data, `${kind} ${(track?.track_index ?? 0) + 1}`)
  const title = block ? clipName(block.data) : trackName
  const time = value => formatTimecode(value, fps) ?? `${value} 帧`
  const metric = (name, value) => h('div', { key: name }, h('dt', null, name), h('dd', null, value))
  return h('section', { className: 'fv-selection-card', 'aria-label': '选区' },
    h('div', { className: 'fv-inspector-item' },
      h('span', { className: 'fv-selection-kind', 'data-family': family }, h(Icon, { name: family === 'audio' ? 'audio' : family === 'subtitles' ? 'text' : 'clip', size: 18 })),
      h('div', null, h('strong', { title }, title), h('p', null, `${kind}${block ? ` · ${trackName}` : '轨道'}${items.length > 1 ? ` · 已选 ${items.length} 项` : ''}`))),
    block ? h('dl', { className: 'fv-inspector-metrics' }, metric('起点', time(block.range[0])), metric('终点', time(block.range[1])), metric('时长', time(block.range[1] - block.range[0]))) : h('p', null, `${result.blocks.filter(item => item.family === track.family && item.track_index === track.track_index).length} 个片段`),
    h('div', { className: 'fv-inspector-tools' }, block ? h(ToolButton, { icon: 'pin', label: '定位片段起点', onClick: () => onPin(String(block.range[0])) }) : null, children))
}

function ProjectCard({ snapshot, result, hits, fps }) {
  const coverage = result.coverage.complete ? snapshot.coverage : result.coverage
  const diagnostics = [...(snapshot.diagnostics ?? []), ...(result.diagnostics ?? []), ...(hits?.diagnostics ?? [])]
  const project = snapshot.document?.project ?? {}, clock = projectClock(fps)
  return h('details', { className: 'fv-inspector-section' }, h('summary', null, '工程概览'),
    h('dl', { className: 'fv-inspector-overview' },
      h('dt', null, '规格'), h('dd', null, `${project.width ?? '—'} × ${project.height ?? '—'}${clock ? ` · ${clock.den === 1 ? clock.num : `${clock.num}/${clock.den}`} fps` : ''}`),
      h('dt', null, '时长'), h('dd', null, formatTimecode(project.duration_frames, fps) ?? '—'),
      h('dt', null, '轨道 / 片段'), h('dd', null, `${result.tracks.length} / ${result.blocks.length}`),
      hits ? [h('dt', { key: 'hits-label' }, '当前查询'), h('dd', { key: 'hits-count' }, `命中 ${hits.blocks.length} 个片段`)] : null),
    !coverage.complete ? h('p', null, '未展开外部引用', ...(coverage.unexpanded ?? []).map((entry, i) => h('span', { className: 'fv-external-ref', key: i }, entry.reference))) : null,
    diagnostics.length ? h('ul', { className: 'fv-inspector-diagnostics' }, diagnostics.map((entry, i) => h('li', { key: i }, entry.message ?? entry.code))) : null)
}

export function PreviewBody({ useTabInfo, sessionId, getDraftServices: getHostServices, inputActions, useAccess = () => false }) {
  const { tab } = useTabInfo()
  const promptAllowed = useAccess('session/prompt') === true
  const liveDraft = useRef()
  liveDraft.current = { getHostServices, inputActions, promptAllowed }
  const draftServices = React.useMemo(() => createOfficialDraftServices({
    sessionId,
    getServices: () => liveDraft.current.getHostServices?.(),
    getActions: () => liveDraft.current.inputActions,
    getAllowed: () => liveDraft.current.promptAllowed,
  }), [sessionId])
  useEffect(() => () => draftServices.dispose(), [draftServices])
  const getDraftServices = React.useCallback(() => draftServices.getServices(), [draftServices])
  const [timeline, setTimeline] = useState('timeline.json')
  const [frame, setFrame] = useState('')
  const [state, setState] = useState({ phase: 'idle' })
  const [selectionIntent, setSelectionIntent] = useState(emptySelection)
  const [filesVisible, setFilesVisible] = useState(false)
  const [browserEpoch, setBrowserEpoch] = useState(0)
  const [browserRecalibrate, setBrowserRecalibrate] = useState(0)
  const [inspectorVisible, setInspectorVisible] = useState(false)
  const [expanded, setExpanded] = useState(false)
  const [timelineShare, setTimelineShare] = useState(38)
  const [timelineFocus, setTimelineFocus] = useState(false)
  const [quickEdit, setQuickEdit] = useState(), [enableEdit, setEnableEdit] = useState()
  const history = useRef()
  const quickReady = React.useCallback(callback => setQuickEdit(() => callback), [])
  const enableReady = React.useCallback(callback => setEnableEdit(() => callback), [])
  const inspector = useRef()
  const workbench = useRef()
  const expandButton = useRef()
  const filesButton = useRef()
  const pathDisclosure = useRef()
  const pendingFileFocus = useRef(false)
  function browseWorkspace() {
    if (filesVisible && !timelineFocus) { workbench.current?.querySelector('.fv-file-search')?.focus(); return }
    setTimelineFocus(false)
    pendingFileFocus.current = true
    setFilesVisible(true)
  }
  useEffect(() => {
    if (filesVisible && pendingFileFocus.current) {
      pendingFileFocus.current = false
      workbench.current?.querySelector('.fv-file-search')?.focus()
    }
  }, [filesVisible, timelineFocus])
  function closeFiles() {
    pendingFileFocus.current = false
    setFilesVisible(false)
    filesButton.current?.focus()
  }
  function openPath() {
    if (!pathDisclosure.current) return
    pathDisclosure.current.open = true
    pathDisclosure.current.querySelector('input')?.focus()
  }
  function toggleExpanded() {
    const dialog = workbench.current
    if (dialog?.open) { dialog.close(); return }
    if (typeof dialog?.showModal === 'function') { dialog.showModal(); setExpanded(true) }
  }
  const frameOwner = useRef(new FrameEpoch()).current
  const ownerBinding = useRef()
  if (ownerBinding.current !== sessionId) { frameOwner.invalidate(); frameOwner.ghost = undefined; frameOwner.edit = { dirty: false, busy: false, unknown: false }; ownerBinding.current = sessionId }
  const gestureState = React.useCallback((token, active) => {
    if (active) { const video = workbench.current?.querySelector('video'); if (video && !video.paused) video.pause() }
    if (frameOwner.gestureChanged(token, active)) setState(previous => ({ ...previous }))
  }, [frameOwner])
  const { primary: selection, items: selected } = selectionIntent
  const pick = React.useCallback((item, event) => { frameOwner.invalidate(); setSelectionIntent(previous => transitionSelection(previous, item, event, state.result)) }, [frameOwner, state.result])
  const clearSelection = React.useCallback(() => { frameOwner.invalidate(); setSelectionIntent(emptySelection()) }, [frameOwner])
  const pin = React.useCallback(value => { frameOwner.invalidate(); setFrame(value) }, [frameOwner])
  const editState = next => { if (state.opened?.grant && grant.current === state.opened.grant && frameOwner.editChanged(next)) setState(previous => ({ ...previous })) }
  const [gesture, setGesture] = useState({})
  const [undoEdit, setUndoEdit] = useState()
  const gestureReady = React.useCallback((callback, reason) => setGesture(previous => previous?.callback === callback && previous?.reason === reason ? previous : { callback, reason }), [])
  const undoReady = React.useCallback(callback => setUndoEdit(() => callback), [])
  const task = useRef()
  const grant = useRef()
  const readSequence = useRef(0)
  // Terminal grant death, lifted from any grant-bound failure (or the Host's
  // own expires_in_ms deadline). While set, every grant-using tool is
  // unmounted: no frame render, draft export or manual write. Only a NEW
  // grant (explicit refresh / reopen) clears it.
  const [grantDead, setGrantDead] = useState()
  const [storageBusy, setStorageBusy] = useState(false)
  const grantDeadlines = useRef(new Map())
  const grantDied = (code, grantId) => { if (grantId === grant.current) setGrantDead(previous => previous ?? code) }
  let frameValue, frameError
  try { frameValue = frameQuery(frame).frame } catch (error) { frameError = error.message }
  const close = () => {
    readSequence.current++
    frameOwner.invalidate()
    const current = grant.current
    grant.current = undefined
    if (current) void control(sessionId, { op: 'close', grant: current }).catch(() => {})
  }
  useEffect(() => { clearSelection(); setGesture(undefined); setState({ phase: 'idle' }); setGrantDead(undefined); return () => { task.current?.abort(); close() } }, [sessionId])
  useEffect(() => {
    const abort = () => { task.current?.abort(); close() }
    tab.signal.addEventListener('abort', abort, { once: true })
    return () => { tab.signal.removeEventListener('abort', abort) }
  }, [tab.signal, sessionId])

  useEffect(() => {
    const services = getDraftServices?.(), list = services?.sessions?.list, navigation = services?.uiSession?.adapter.current
    let binding = services?.sessions?.binding?.(sessionId), cwd = list?.getSnapshot().byId?.[sessionId]?.cwd, key = navigation?.getSnapshot().key
    const observe = () => {
      const nextBinding = services?.sessions?.binding?.(sessionId), nextCwd = list?.getSnapshot().byId?.[sessionId]?.cwd, nextKey = navigation?.getSnapshot().key
      if (binding !== nextBinding || cwd !== nextCwd || key !== nextKey) {
        binding = nextBinding; cwd = nextCwd; key = nextKey
        task.current?.abort(); close(); clearSelection(); setFrame('')
        setBrowserEpoch(previous => previous + 1)
        setState({ phase: 'idle', message: '会话或工作区已切换，请重新选择时间线。' })
      }
    }
    const off = [list?.subscribe(observe), navigation?.subscribe(observe)]
    return () => { frameOwner.invalidate(); off.forEach(dispose => dispose?.()) }
  }, [sessionId, frameOwner])
  // Host grants carry a fixed absolute TTL (open answers expires_in_ms). The
  // Host stays fail-closed on its own clock regardless; this local advisory
  // deadline only makes the UI proactive: when it fires (or any grant-bound
  // call answers a terminal code via grantDied), the stale view is locked and
  // the user is told to refresh/reopen — instead of hammering 403s forever.
  // The deadline is anchored ONCE per grant id, so refreshes of the same
  // grant never extend it, and it never fires for a grant already replaced.
  useEffect(() => {
    const opened = state.opened
    if (state.phase !== 'ready' || !opened?.grant || !Number.isSafeInteger(opened.expires_in_ms) || opened.expires_in_ms <= 0) return
    const grantId = opened.grant
    let deadline = grantDeadlines.current.get(grantId)
    if (deadline === undefined) { deadline = Date.now() + opened.expires_in_ms; grantDeadlines.current.set(grantId, deadline) }
    const remaining = deadline - Date.now()
    if (remaining <= 0) { if (grant.current === grantId) setGrantDead(previous => previous ?? 'EXPIRED'); return }
    const timer = setTimeout(() => { if (grant.current === grantId) setGrantDead(previous => previous ?? 'EXPIRED') }, remaining)
    return () => clearTimeout(timer)
  }, [state.phase, state.opened?.grant, sessionId])
  async function load(queryOnly = false, chosenTimeline = timeline) {
    if (frameOwner.edit.busy || (frameOwner.edit.unknown && !grantDead)) {
      setState(previous => ({ ...previous, message: '编辑正在执行或写结果未知。请等待，或在顶栏读取状态 / 恢复。' }))
      if (frameOwner.edit.unknown) showHistory()
      return
    }
    // A position query cannot re-authorize a view whose saved revision was
    // never confirmed. Only a full refresh or a settled store readback can.
    if (queryOnly && (state.stale || frameOwner.edit.dirty)) {
      setState(previous => ({ ...previous, message: '请先刷新保存版本，再查询播放头。' }))
      return
    }
    if (!queryOnly && chosenTimeline !== timeline) { setTimeline(chosenTimeline); setFrame('') }
    frameOwner.invalidate()
    let query
    try { query = queryOnly ? frameQuery(frame) : {} } catch (error) {
      // Invalid text does not query, reopen, or close an existing grant.
      setState(previous => ({ ...previous, message: error.message }))
      return
    }
    task.current?.abort()
    const controller = new AbortController()
    task.current = controller
    const signal = AbortSignal.any([controller.signal, tab.signal])
    const previous = state
    const opening = !queryOnly || previous.phase !== 'ready'
    if (opening) { close(); frameOwner.editChanged({ dirty: false, busy: false, unknown: false }) }
    const sequence = ++readSequence.current
    const current = () => !signal.aborted && readSequence.current === sequence && task.current === controller
    setState(queryOnly && previous.phase === 'ready' ? { ...previous, loading: true, message: undefined } : { phase: 'loading' })
    try {
      let opened
      if (opening) {
        // Opening mints a Host resource. Let this bounded operation settle even
        // after UI cancellation, so its exact grant can be released below.
        // Queries remain bound to the UI signal; a dead transport still times out.
        const openingController = new AbortController()
        const openingTimeout = setTimeout(() => openingController.abort(new DOMException('Timeline open exceeded 45 seconds', 'TimeoutError')), 45_000)
        try { opened = await control(sessionId, { op: 'open', timeline: chosenTimeline }, openingController.signal) }
        finally { clearTimeout(openingTimeout) }
        if (!current()) {
          // Never publish a stale owner; release only the grant it minted.
          void control(sessionId, { op: 'close', grant: opened.grant }).catch(() => {})
          return
        }
        // Deterministic host/client build check (both stamps baked from the
        // same artifact hash): a mismatch means the RUNNING host is not the
        // build this client shipped with — e.g. the plugin was updated but
        // the host process was not restarted. Fail closed BEFORE committing
        // any state: the minted grant is released and editing/framing never
        // starts against a host whose feature set this client cannot know.
        // Unknown stamps (unbundled source runs) never block.
        const mismatch = buildMismatchMessage(opened?.host?.build)
        if (mismatch) {
          grant.current = undefined
          // Single best-effort close of the just-minted grant; no retry. If it
          // cannot be confirmed reclaimed, say so explicitly instead of letting
          // the failure banner imply the session is fully released.
          let closeConfirmed = false
          try { await control(sessionId, { op: 'close', grant: opened.grant }); closeConfirmed = true } catch { closeConfirmed = false }
          if (!current()) return
          const message = closeConfirmed ? mismatch : `${mismatch}（注意：未能确认回收刚打开的预览授权；请勿重开会话，或等待授权TTL过期后由宿主自动回收。）`
          setState(previous => ({ phase: 'failed', sessionId, message }))
          return
        }
        grant.current = opened.grant
        // A new grant is the ONLY thing that releases a terminal grant death.
        setGrantDead(undefined)
        // A successful open recalibrates the file browser: a stale failed
        // listing retries at its current folder; a healthy one only clears a
        // lingering banner. The browser itself decides; no forced remount.
        setBrowserRecalibrate(value => value + 1)
      } else opened = previous.opened
      // Always acquire a same-version full projection before a filtered query.
      const full = queryOnly && query.frame !== undefined && previous.phase === 'ready' ? previous.result : await control(sessionId, { op: 'query', grant: opened.grant, query: {} }, signal)
      if (!current()) return
      const hits = query.frame === undefined ? undefined : await control(sessionId, { op: 'query', grant: opened.grant, query }, signal)
      if (!current()) return
      const stale = opened.snapshot.content_version !== full.content_version || (hits && hits.content_version !== full.content_version)
      if (!queryOnly || stale) clearSelection()
      setState({ phase: 'ready', sessionId, timeline: queryOnly && previous.phase === 'ready' ? previous.timeline : chosenTimeline, opened, result: full, hits, stale: Boolean(stale) })
    } catch (error) {
      if (current()) {
        // A terminal grant code observed on any load path means this grant id
        // is dead at the Host; lock the view before the regular handling so
        // the shared banner + tool teardown appear even without the deadline.
        const code = terminalGrantFailure(error.message)
        if (code) setGrantDead(previous => previous ?? code)
        if (queryOnly && previous.phase === 'ready') setState({ ...previous, loading: false, message: error.message })
        else { close(); setState({ phase: 'failed', message: error.message }) }
      }
    }
  }
  async function refreshCommitted({ focusId, expectedRevision }) {
    frameOwner.invalidate()
    task.current?.abort()
    const controller = new AbortController()
    task.current = controller
    const sequence = ++readSequence.current
    const opened = state.opened
    const signal = AbortSignal.any([controller.signal, tab.signal])
    // Neither authoritative read depends on the other's result. If one fails,
    // cancel the sibling instead of blocking an already-applied edit on it.
    const pair = new AbortController()
    const readSignal = AbortSignal.any([signal, pair.signal])
    let failure
    const read = async (op, query) => {
      try { return await control(sessionId, { op, grant: opened.grant, query }, readSignal) }
      catch (error) {
        if (!pair.signal.aborted || terminalGrantFailure(error?.message)) failure = error
        pair.abort()
      }
    }
    const [snapshot, result] = await Promise.all([read('inspect'), read('query', {})])
    signal.throwIfAborted()
    if (pair.signal.aborted) throw failure
    if (grant.current !== opened.grant || readSequence.current !== sequence || task.current !== controller) throw new Error('显示已切换，未确认刷新。')
    // Two matching reads can both be from a later, unrelated save.
    const consistent = typeof expectedRevision === 'string' && /^sha256:[0-9a-f]{64}$/.test(expectedRevision) &&
      snapshot.content_version === expectedRevision && result.content_version === expectedRevision
    const focus = focusId ? result.blocks.find(block => block.data?.id === focusId) : undefined
    setSelectionIntent(previous => {
      if (!consistent) return emptySelection()
      const retained = preserveSelection(previous, state.result, result)
      return !focus || retained.items.some(item => item.address === focus.address && item.kind === 'block') ? retained : transitionSelection(retained, { kind: 'block', address: focus.address }, {}, result)
    })
    setState({ phase: 'ready', sessionId, timeline: state.timeline, opened: { ...opened, snapshot }, result, stale: !consistent })
    if (!consistent) throw new Error('时间线已变化，请刷新工程。')
  }
  const ready = state.phase === 'ready' && state.sessionId === sessionId
  const valid = ready && !state.stale && state.opened.snapshot.content_version === state.result.content_version
  const editUnsupported = ready && state.opened.storeCapabilities?.error === 'WRITE_UNSUPPORTED'
  const activePath = ready ? state.timeline : timeline
  const canEditHere = valid && !editUnsupported && !grantDead && !storageBusy && !state.loading
  const selectedBlock = valid && selection?.kind === 'block' ? state.result.blocks.find(block => block.address === selection.address) : undefined
  const selectedTrack = valid ? state.result.tracks.find(track => selectedBlock ? track.family === selectedBlock.family && track.track_index === selectedBlock.track_index : track.address === selection?.address) : undefined
  const selectionDisplay = selection && valid ? { content_version: state.opened.snapshot.content_version, address: selection.address,
    blockName: selectedBlock ? clipName(selectedBlock.data) : undefined, trackName: selectedTrack?.data?.name, source_id: selectedBlock?.data?.src } : undefined
  const showHistory = () => { const node = history.current; if (node) { node.open = true; node.querySelector('summary')?.focus() } }
  const filename = activePath.split('/').at(-1) || '时间线'
  const duration = state.opened?.snapshot.document?.project?.duration_frames
  const fps = state.opened?.snapshot.document?.project?.fps
  function showProperties() {
    setTimelineFocus(false)
    setInspectorVisible(true)
  }
  function reportEditFailure(error) { setState(previous => ({ ...previous, message: error.message })) }
  function runQuick(operation) {
    if (!canEditHere || !quickEdit) return
    if (operation === 'block.remove' && !globalThis.window.confirm('移除选中的片段并保存？')) return
    setState(previous => ({ ...previous, message: undefined }))
    void quickEdit(operation).catch(reportEditFailure)
  }
  const disclosureGroup = React.useId()
  const transport = ready ? h(Transport, { disclosureGroup, frameText: frame, frameError, duration, fps, onFrame: pin, onText: pin, onQuery: () => void load(true) }) : null
  function editorKey(event) {
    if (event.key === 'Escape' && !event.defaultPrevented) {
      const disclosure = event.target.closest?.('.fv-monitor-disclosure[open],.fv-disclosure[open]')
      if (disclosure) { event.preventDefault(); event.stopPropagation(); disclosure.open = false; disclosure.querySelector('summary')?.focus(); return }
    }
    if (!ready || event.defaultPrevented || event.repeat || event.ctrlKey || event.metaKey || event.altKey || editingTarget(event.target) || (!event.target.closest?.('.fv-monitor,.fv-timeline-pane') || event.target.closest?.('video'))) return
    if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) {
      if (event.target.closest?.('[role=slider],[role=separator]')) return
      let next = event.key === 'Home' ? 0 : event.key === 'End' && Number.isSafeInteger(duration) ? duration - 1 : stepFrame(frameValue, duration, event.key === 'ArrowLeft' ? -1 : 1, event.shiftKey ? 10 : 1)
      if (!Number.isSafeInteger(next) || next < 0) return
      event.preventDefault(); pin(String(next))
    } else if (canEditHere && quickEdit && ((event.key.toLowerCase() === 's' && selected.filter(item => item.kind === 'block').length === 1 && Number.isSafeInteger(frameValue)) || (['Delete', 'Backspace'].includes(event.key) && selected.some(item => item.kind === 'block')))) {
      event.preventDefault(); runQuick(event.key.toLowerCase() === 's' ? 'block.split' : 'block.remove')
    }
  }
  return h('article', { className: 'fv-shell', 'aria-label': 'Framivra read-only preview' },
    h(WorkbenchStyles),
    h('dialog', { className: 'fv-workbench', ref: workbench, 'data-view': ready ? 'editor' : 'start', 'data-files': filesVisible, 'data-timeline-focus': ready && valid && !state.stale && timelineFocus, role: expanded ? 'dialog' : 'region', 'aria-label': 'Framivra 工作台', 'aria-modal': expanded ? true : undefined,
      onClose: () => { setExpanded(false); expandButton.current?.focus() }, onKeyDown: editorKey,
      style: { '--fv-files-column': filesVisible ? '248px' : '0px', '--fv-inspector-column': ready && inspectorVisible ? '300px' : '0px', '--fv-timeline-share': `${timelineShare}%` } },
      h('header', { className: 'fv-topbar' },
        h('span', { className: 'fv-brand' }, h(Icon, { name: 'film', size: 22 })),
        h('div', { className: 'fv-title' }, h('h2', { title: ready ? activePath : 'Framivra' }, ready ? filename : 'Framivra'), h('span', { className: 'fv-subtitle', title: ready ? '已保存视图 · 精确帧定位' : '时间线工作台' }, ready ? '已保存视图' : '时间线工作台')),
        ready && !grantDead && !editUnsupported ? h(ManualEditor, { key: state.opened.grant, sessionId, opened: state.opened, result: state.result, selection, selected, frame: frameValue, tabSignal: tab.signal, call: control, onCommitted: refreshCommitted, onEditState: editState, onGrantDead: grantDied, onGestureReady: gestureReady, onUndoReady: undoReady, onQuickReady: quickReady, onEnableReady: enableReady, suspended: storageBusy || Boolean(state.loading), stale: !valid, historyRef: history }) : null,
        h(ToolButton, { icon: 'folder', label: '浏览工作区打开时间线', tooltip: '浏览工作区，打开时间线', 'aria-pressed': filesVisible, elementRef: filesButton, onClick: () => filesVisible && !timelineFocus ? closeFiles() : browseWorkspace() }),
        ready ? h(ToolButton, { icon: 'refresh', label: '刷新保存版本', onClick: () => void load(), disabled: state.loading || frameOwner.edit.busy }) : null,
        ready ? h(ToolButton, { icon: 'settings', label: '属性与编辑', 'aria-pressed': inspectorVisible, onClick: () => { setTimelineFocus(false); setInspectorVisible(value => !value) } }) : null,
        h('details', { className: 'fv-path', ref: pathDisclosure, onKeyDown: event => { if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); event.currentTarget.open = false; event.currentTarget.querySelector('summary')?.focus() } } },
          h(ToolSummary, { icon: 'path', label: '通过路径打开', tooltip: '通过工作区内的相对路径打开' }),
          h('form', { onSubmit: event => { event.preventDefault(); event.currentTarget.closest('details').open = false; void load() } },
            h('label', null, '时间线路径 ', h('input', { name: 'framivra-timeline', autoComplete: 'off', spellCheck: false, value: timeline, onChange: event => setTimeline(event.target.value), required: true })),
            h('button', { type: 'submit' }, '打开 / 刷新'))),
        h(ToolButton, { icon: expanded ? 'collapse' : 'expand', label: expanded ? '收起工作台' : '展开工作台', elementRef: expandButton, onClick: toggleExpanded })),
      state.phase === 'loading' || state.loading ? h('p', { className: 'fv-notice', role: 'status' }, '正在通过 vtx 读取时间线…') : null,
      state.message ? h('p', { className: 'fv-notice', role: 'alert' }, state.message.split('\n')[0]) : null,
      grantDead ? h('p', { className: 'fv-notice', role: 'alert', style: { border: '1px solid currentColor', padding: 8 } }, `${GRANT_DEAD_MESSAGE}（${grantDead}）`) : null,
      valid && (!state.result.coverage.complete || !state.opened.snapshot.coverage.complete) ? h('p', { className: 'fv-notice', role: 'alert' }, '存在未展开的外部素材；请在属性面板查看完整诊断。') : null,
      h('div', { className: 'fv-body' },
        h('div', { className: 'fv-upper' },
          filesVisible ? h(FileBrowser, { key: `${sessionId}:${browserEpoch}`, sessionId, tabSignal: tab.signal, call: control, selectedPath: ready ? activePath : undefined, onChoose: path => void load(false, path), onClose: closeFiles, recalibrate: browserRecalibrate, hostOps: state.opened?.host?.ops }) : null,
          h('main', { className: 'fv-monitor', 'aria-label': ready ? '画面预览' : '时间线启动区' },
            ready ? (valid && !grantDead ? h('fieldset', { disabled: storageBusy, style: { display: 'contents' } }, h(ProgramMonitor, { key: state.opened.grant, suspended: storageBusy, owner: frameOwner, opened: state.opened, timeline: activePath, selectionDisplay, sessionId, frame: frameValue, blank: frame === '', fps, duration, onFrame: pin, selection, tabSignal: tab.signal, getDraftServices, call: control, transport, disclosureGroup })) : h('div', { className: 'fv-monitor-toolbar' }, transport)) :
              h('div', { className: 'fv-empty' }, h(Icon, { name: 'film', size: 32 }), h('h3', null, '从一条时间线开始'), h('p', null, '选择当前工作区中的时间线 JSON 文件，开始预览与剪辑。'),
                h('div', { className: 'fv-start-actions' },
                  h('button', { className: 'fv-primary', type: 'button', onClick: browseWorkspace }, h(Icon, { name: 'folder', size: 17 }), '浏览工作区'),
                  h('button', { className: 'fv-path-action', type: 'button', onClick: openPath }, '通过路径打开')))),
          ready ? h('aside', { className: 'fv-inspector', ref: inspector, 'aria-label': '属性与剪辑', hidden: !inspectorVisible },
            h('div', { className: 'fv-panel-heading' }, h('h3', null, '属性与剪辑'), h(ToolButton, { icon: 'close', label: '收起属性与剪辑', onClick: () => setInspectorVisible(false) })),
            valid && !grantDead ? h(SelectionCard, { selection, items: selected, result: state.result, fps, onPin: pin },
              h(ReferenceTools, { key: `${state.opened.grant}:${state.opened.snapshot.content_version}:${selection?.kind}:${selection?.address}`, selection, sessionId, opened: state.opened, tabSignal: tab.signal, getDraftServices, onGrantDead: grantDied, timeline: activePath, selectionDisplay,
                onStale: () => { clearSelection(); setState(previous => ({ ...previous, stale: true })) } })) : null,
            valid && !grantDead ? h(React.Fragment, null,
              h(ProjectCard, { snapshot: state.opened.snapshot, result: state.result, hits: state.hits, fps }),
              h(StoragePanel, { key: `${state.opened.grant}:${state.opened.snapshot.content_version}`, sessionId, opened: state.opened, tabSignal: tab.signal, call: control, active: inspectorVisible, initiallyOpen: false, disabled: !!(frameOwner.edit.dirty || frameOwner.edit.busy || frameOwner.edit.unknown || frameOwner.ghost), onBusyChange: setStorageBusy, onGrantDead: grantDied })) : null) : null),
         ready ? h(LayoutDivider, { value: timelineShare, onChange: setTimelineShare }) : null,
        ready ? h('section', { className: 'fv-timeline-pane', 'aria-label': '工程时间线' },
          state.stale ? h('p', { role: 'alert' }, '时间线版本已变化；旧块已隐藏，请刷新保存版本。') : h(TimelineView, { key: state.opened.grant, compact: true, snapshot: state.opened.snapshot, result: state.result, hits: state.hits, frame: frameValue, onFrame: value => pin(String(value)), onSelect: pick, activeSelection: selection, selectionIntent, onGesture: canEditHere ? gesture?.callback : undefined, onGestureState: gestureState,
             gestureUnavailable: grantDead ? '请刷新时间线后再编辑。' : storageBusy ? '请等待空间操作结束。' : editUnsupported ? '当前环境仅支持只读时间线。' : gesture?.reason, onOpenEdits: canEditHere && (enableEdit || ['请先接管旧工程', '请打开历史与恢复'].includes(gesture?.reason)) ? (enableEdit ? () => { void enableEdit().catch(reportEditFailure) } : showHistory) : undefined, onOpenHistory: canEditHere ? showHistory : undefined, editActionLabel: enableEdit ? '启用编辑' : '历史与恢复',
             onUndo: canEditHere ? undoEdit : undefined,
             focusTool: h(ToolButton, { icon: timelineFocus ? 'collapse' : 'expand', label: '轨道专注视图', tooltip: timelineFocus ? '恢复画面和时间线' : '暂时隐藏画面和侧栏，为轨道腾出整个工作区', 'aria-pressed': timelineFocus, onClick: () => setTimelineFocus(value => !value) }),
             selectionTools: valid ? h(SelectionSummary, { selection, selected, result: state.result, frame: frameValue, fps, onFrame: pin, onClear: clearSelection, onProperties: () => showProperties(), quick: canEditHere && quickEdit ? runQuick : undefined }) : null })) : null)))
}
// DSH 0.2.2-rc.1: this is the tab-scoped action, not ISidebarRight.
// Its boolean replaceTab:true is translated to this occurrence's TabId by
// SidebarRightTabActions.place(); passing a raw TabId here would be dropped.
function Guide({ useTabInfo }) {
  const { tab } = useTabInfo()
  return h(React.Fragment, null, h(WorkbenchStyles),
    h('button', { className: 'fv-guide', type: 'button', onClick: () => tab.actions.openTab('framivra', { replaceTab: true }) }, h(Icon, { name: 'film', size: 21 }), h('strong', null, 'Framivra'), h('small', null, '时间线工作台')) )
}
export function apply(ctx) {
  const lifetime = { active: true }
  // Official Cordis creates a new traceable Proxy on EVERY service read.
  // Pin public injected faces for this apply lifetime, not for each click.
  // Dependency replacement disposes this lifetime and re-runs apply with fresh
  // faces; draft contents, binding, permissions and budgets are still read live.
  const services = { conversation: ctx.get?.('conversation'), sessions: ctx.get?.('sessions'), uiSession: ctx.get?.('uiSession') }
  ctx.effect(() => () => { lifetime.active = false })
  ctx.effect(() => ctx.sidebarRightTabs.register({
    id: ID, kind: 'framivra', priority: 'extension', title: () => 'Framivra',
    guide: [{ id: 'open', order: 40, title: () => 'Framivra preview', description: () => 'Read-only timeline and existing outputs' }],
  }))
  ctx.effect(() => ctx.slots.inject('sidebar.right.pane.tab', () => ctx.slots.register({
    name: 'sidebar.right.pane.tab', key: ID, inject: sessionId => ({ sessionId, getDraftServices: () => ({ ...services, active: lifetime.active }) }),
  }, PreviewBody)))
  ctx.effect(() => ctx.slots.inject('sidebar.right.tab.guide.entry', () => ctx.slots.register({ name: 'sidebar.right.tab.guide.entry', key: ID }, Guide)))
  registerBundleConfig(ctx)
}
