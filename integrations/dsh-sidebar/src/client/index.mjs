import React, { useEffect, useRef, useState } from 'react'
import { parseJSON, stringifyJSON } from '../json.mjs'
import { captureCurrentDraft, performReferenceAction } from './reference.mjs'
import { TimelineHelp, VisualTimeline } from './timeline.mjs'
import { ManualEditor } from './manual.mjs'
import { emptySelection, transitionSelection, preserveSelection } from './selection.mjs'
import { FrameEpoch, FrameTools } from './frame.mjs'
import { registerBundleConfig } from './settings.mjs'
import { createOfficialDraftServices, draftResultMessage } from './draft-services.mjs'
import { Icon, WorkbenchStyles, FileBrowser } from './workbench.mjs'
import { boundedText, CONTROL_REQUEST_LIMIT } from '../bounded.mjs'
import { LayoutDivider, SelectionSummary, Transport, editingTarget, stepFrame } from './editor-controls.mjs'

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
  if (!response.ok) throw new Error(`${data.error}: ${data.message}${data.diagnostics ? `\n${pretty(data.diagnostics)}` : ''}`)
  return data
}

/** Pure presentation: all addresses, ranges, selection and raw fields come from vtx. */
export function TimelineView({ snapshot, result, hits, frame, onFrame, onSelect, activeSelection, onSelectionChange, selectionIntent, onGesture, onGestureState, compact = false }) {
  if (snapshot.content_version !== result.content_version) return h('p', { role: 'alert' }, 'Timeline changed between reads. Reopen preview; mixed-version selection is not displayed.')
  return h(React.Fragment, null,
    h(VisualTimeline, { key: snapshot.content_version, snapshot, result, hits, frame, onFrame, onSelect, activeSelection, onSelectionChange, selectionIntent, onGesture, onGestureState, compact }),
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

export function ReferenceTools({ selection, sessionId, opened, frame, tabSignal, getDraftServices, onStale }) {
  const [state, setState] = useState({})
  const task = useRef()
  useEffect(() => () => task.current?.abort(), [selection, sessionId, opened.grant, opened.snapshot.content_version, frame, tabSignal])
  async function run(action) {
    task.current?.abort()
    const controller = new AbortController()
    task.current = controller
    const signal = AbortSignal.any([controller.signal, tabSignal])
    let captured
    try {
      // Capture is deliberately before the first await and before starting reference IO.
      if (action === 'draft') captured = captureCurrentDraft(getDraftServices, sessionId)
      setState(previous => ({ ...previous, busy: true, message: '正在读取 vtx 原文…' }))
      const result = await performReferenceAction({ action, selection, contentVersion: opened.snapshot.content_version, frame, signal, captured,
        clipboard: globalThis.navigator?.clipboard,
        read: (query, signal) => control(sessionId, { op: 'reference', grant: opened.grant, query }, signal),
        onReference: (envelope, item) => setState(previous => ({ ...previous, envelope, item })),
      })
      // A completed draft mutation must not be relabelled "unchanged" if a
      // synchronous subscriber navigated/aborted after the public action ran.
      if (action !== 'draft') signal.throwIfAborted()
      setState(previous => ({ ...previous, busy: false, message: result.status === 'copied' ? '已复制。' : result.status === 'ready' ? '引用就绪。' : draftResultMessage(result) }))
    } catch (error) {
      if (signal.aborted) return
      const stale = /STALE_REFERENCE|reference\.stale/.test(error.message)
      if (stale) onStale()
      setState(previous => ({ ...previous, busy: false, ...(stale ? { envelope: undefined, item: undefined } : {}), message: error.message }))
    }
  }
  useEffect(() => { task.current?.abort(); setState({}) }, [frame])
  useEffect(() => { setState({}); if (selection) void run('read') }, [selection, sessionId, opened.grant, opened.snapshot.content_version])
  return h('section', { 'aria-label': '时间线引用', style: { border: '1px solid currentColor', padding: 8 } },
    h('h3', null, '时间线引用 · 仅存储语义'),
    h('details', null, h('summary', null, '引用与草稿说明'),
      h('p', null, '选择轨道或块后可复制。raw JSON 是 vtx 提取的原文，不是上方 read/v1 的重编码。整数帧由 vtx 解释；播放未与选择联动，不能代替精确帧。此处仅提取引用；生成 PNG 与加入图片草稿需在帧工具中单独授权、点击。'),
      h('p', null, '加入草稿通过官方会话 InputActions；在点击时选区起点插入，不替换已选文字或原有引用。文本和图片顺序加入，部分结果会明确提示；不会创建会话、自动补投或发送。')),
    selection ? h('p', null, `当前选择：${selection.kind} ${selection.address} · ${opened.snapshot.content_version}`) : h('p', null, '尚未选择轨道或块。'),
    ...[['id', '复制 ID / 临时定位'], ['raw', '复制 raw JSON'], ['diagnostic', '复制诊断引用 JSON'], ['draft', '加入当前会话草稿（不发送）']].map(([action, label]) => h('button', { key: action, type: 'button', disabled: !selection || state.busy, onClick: () => { void run(action) } }, label)),
    state.busy ? h('button', { type: 'button', onClick: () => { task.current?.abort(); setState(previous => ({ ...previous, busy: false, message: '已取消；未重试/补投草稿，选择与已读取原文保留。' })) } }, '取消引用操作') : null,
    state.message ? h('p', { role: 'status' }, state.message) : null,
    state.item ? h(React.Fragment, null,
      h('p', null, state.item.stable_id === null ? `legacy / 无协议稳定 ID：临时地址 ${state.item.address}（仅此 content_version 有效）` : `稳定 ID：${state.item.stable_id}`),
      h('details', null, h('summary', null, '选中对象原文'), h('pre', { 'aria-label': '选中对象 raw JSON', style: { whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' } }, state.item.raw ?? '无存储原文（虚拟轨道）')),
      details('vtx reference/v1 诊断与选择（完整）', state.envelope)) : null)
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
  const [inspectorVisible, setInspectorVisible] = useState(false)
  const [expanded, setExpanded] = useState(false)
  const [timelineShare, setTimelineShare] = useState(38)
  const [quickEdit, setQuickEdit] = useState()
  const inspector = useRef()
  const workbench = useRef()
  const expandButton = useRef()
  function toggleExpanded() {
    const dialog = workbench.current
    if (dialog?.open) { dialog.close(); return }
    if (typeof dialog?.showModal === 'function') { dialog.showModal(); setExpanded(true) }
  }
  const frameOwner = useRef(new FrameEpoch()).current
  const ownerBinding = useRef()
  if (ownerBinding.current !== sessionId) { frameOwner.invalidate(); frameOwner.ghost = undefined; frameOwner.edit = { dirty: false, busy: false, unknown: false }; ownerBinding.current = sessionId }
  const gestureState = React.useCallback((token, active) => {
    if (frameOwner.gestureChanged(token, active)) setState(previous => ({ ...previous }))
  }, [frameOwner])
  const { primary: selection, items: selected } = selectionIntent
  const pick = React.useCallback((item, event) => { frameOwner.invalidate(); setSelectionIntent(previous => transitionSelection(previous, item, event, state.result)) }, [frameOwner, state.result])
  const clearSelection = React.useCallback(() => { frameOwner.invalidate(); setSelectionIntent(emptySelection()) }, [frameOwner])
  const pin = React.useCallback(value => { frameOwner.invalidate(); setFrame(value) }, [frameOwner])
  const editState = next => { if (state.opened?.grant && grant.current === state.opened.grant && frameOwner.editChanged(next)) setState(previous => ({ ...previous })) }
  const [gesture, setGesture] = useState()
  const task = useRef()
  const grant = useRef()
  const readSequence = useRef(0)
  let frameValue, frameError
  try { frameValue = frameQuery(frame).frame } catch (error) { frameError = error.message }
  const close = () => {
    readSequence.current++
    frameOwner.invalidate()
    const current = grant.current
    grant.current = undefined
    if (current) void control(sessionId, { op: 'close', grant: current }).catch(() => {})
  }
  useEffect(() => { clearSelection(); setGesture(undefined); setState({ phase: 'idle' }); return () => { task.current?.abort(); close() } }, [sessionId])
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
  async function load(queryOnly = false, chosenTimeline = timeline) {
    if (frameOwner.edit.busy || frameOwner.edit.unknown) {
      setState(previous => ({ ...previous, message: '编辑正在执行或写结果未知。请先等待刷新完成，或在属性面板读取 status / recover。' }))
      setInspectorVisible(true)
      return
    }
    if (!queryOnly && frameOwner.edit.dirty && !globalThis.window?.confirm('存在未保存的编辑批次。切换或刷新会丢弃此批次，是否继续？')) return
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
    if (opening) close()
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
        grant.current = opened.grant
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
        if (queryOnly && previous.phase === 'ready') setState({ ...previous, loading: false, message: error.message })
        else { close(); setState({ phase: 'failed', message: error.message }) }
      }
    }
  }
  async function refreshCommitted() {
    frameOwner.invalidate()
    task.current?.abort()
    const controller = new AbortController()
    task.current = controller
    const sequence = ++readSequence.current
    const opened = state.opened
    const signal = AbortSignal.any([controller.signal, tab.signal])
    const snapshot = await control(sessionId, { op: 'inspect', grant: opened.grant }, signal)
    const result = await control(sessionId, { op: 'query', grant: opened.grant, query: {} }, signal)
    signal.throwIfAborted()
    if (grant.current !== opened.grant || readSequence.current !== sequence || task.current !== controller) return
    // A later read cannot be overwritten by an old commit-refresh completion.
    setSelectionIntent(previous => snapshot.content_version === result.content_version ? preserveSelection(previous, state.result, result) : emptySelection())
    setState({ phase: 'ready', sessionId, timeline: state.timeline, opened: { ...opened, snapshot }, result, stale: snapshot.content_version !== result.content_version })
  }
  const ready = state.phase === 'ready' && state.sessionId === sessionId
  const valid = ready && !state.stale && state.opened.snapshot.content_version === state.result.content_version
  const activePath = ready ? state.timeline : timeline
  const filename = activePath.split('/').at(-1) || '时间线'
  const duration = state.opened?.snapshot.document?.project?.duration_frames
  const manualPane = useRef()
  function showProperties(edit = false) {
    setInspectorVisible(true)
    if (edit && manualPane.current) manualPane.current.open = true
  }
  function runQuick(operation) {
    if (!quickEdit) return
    try { quickEdit(operation); showProperties(true) }
    catch (error) { setState(previous => ({ ...previous, message: error.message })) }
  }
  function editorKey(event) {
    if (!ready || event.defaultPrevented || event.repeat || event.ctrlKey || event.metaKey || event.altKey || editingTarget(event.target) || !event.target.closest?.('.fv-monitor,.fv-timeline-pane')) return
    if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) {
      if (event.target.closest?.('[role=slider],[role=separator]')) return
      let next = event.key === 'Home' ? 0 : event.key === 'End' && Number.isSafeInteger(duration) ? duration - 1 : stepFrame(frameValue, duration, event.key === 'ArrowLeft' ? -1 : 1, event.shiftKey ? 10 : 1)
      if (!Number.isSafeInteger(next) || next < 0) return
      event.preventDefault(); pin(String(next))
    } else if (quickEdit && ((event.key.toLowerCase() === 's' && selected.filter(item => item.kind === 'block').length === 1 && Number.isSafeInteger(frameValue)) || (['Delete', 'Backspace'].includes(event.key) && selected.some(item => item.kind === 'block')))) {
      event.preventDefault(); runQuick(event.key.toLowerCase() === 's' ? 'block.split' : 'block.remove')
    }
  }
  return h('article', { className: 'fv-shell', 'aria-label': 'Framivra read-only preview' },
    h(WorkbenchStyles),
    h('dialog', { className: 'fv-workbench', ref: workbench, role: expanded ? 'dialog' : 'region', 'aria-label': 'Framivra 工作台', 'aria-modal': expanded ? true : undefined,
      onClose: () => { setExpanded(false); expandButton.current?.focus() }, onKeyDown: editorKey,
      style: { '--fv-files-column': filesVisible ? '220px' : '0px', '--fv-inspector-column': ready && inspectorVisible ? '300px' : '0px', '--fv-timeline-share': `${timelineShare}%` } },
      h('header', { className: 'fv-topbar' },
        h('span', { className: 'fv-brand' }, h(Icon, { name: 'film', size: 22 })),
        h('div', { className: 'fv-title' }, h('h2', { title: ready ? activePath : 'Framivra' }, ready ? filename : 'Framivra'), h('span', { className: 'fv-subtitle' }, ready ? '已保存视图 · 精确帧定位' : '时间线工作台')),
        h('button', { type: 'button', title: '浏览工作区，打开时间线', 'aria-label': '浏览工作区打开时间线', 'aria-pressed': filesVisible, onClick: () => setFilesVisible(value => !value) }, h(Icon, { name: 'folder', size: 16 }), h('span', { className: 'fv-open-label' }, '打开')),
        ready ? h('button', { className: 'fv-icon-button', type: 'button', title: '刷新保存版本', 'aria-label': '刷新保存版本', onClick: () => void load(), disabled: state.loading }, h(Icon, { name: 'refresh', size: 16 })) : null,
        ready ? h('button', { className: 'fv-icon-button', type: 'button', title: '属性与编辑', 'aria-label': '属性与编辑', 'aria-pressed': inspectorVisible, onClick: () => setInspectorVisible(value => !value) }, h(Icon, { name: 'settings', size: 17 })) : null,
        h('details', { className: 'fv-path', onKeyDown: event => { if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); event.currentTarget.open = false; event.currentTarget.querySelector('summary')?.focus() } } },
          h('summary', { 'aria-label': '通过路径打开', title: '通过工作区内的相对路径打开' }, '路径'),
          h('form', { onSubmit: event => { event.preventDefault(); event.currentTarget.closest('details').open = false; void load() } },
            h('label', null, '时间线路径 ', h('input', { name: 'framivra-timeline', autoComplete: 'off', spellCheck: false, value: timeline, onChange: event => setTimeline(event.target.value), required: true })),
            h('button', { type: 'submit' }, '打开 / 刷新'))),
        h('button', { className: 'fv-icon-button', type: 'button', ref: expandButton, title: expanded ? '收起工作台' : '展开工作台', 'aria-label': expanded ? '收起工作台' : '展开工作台', onClick: toggleExpanded }, h(Icon, { name: expanded ? 'collapse' : 'expand', size: 17 }))),
      state.phase === 'loading' || state.loading ? h('p', { className: 'fv-notice', role: 'status' }, '正在通过 vtx 读取时间线…') : null,
      state.message ? h('p', { className: 'fv-notice', role: 'alert' }, state.message) : null,
      valid && (!state.result.coverage.complete || !state.opened.snapshot.coverage.complete) ? h('p', { className: 'fv-notice', role: 'alert' }, '存在未展开的外部素材；请在属性面板查看完整诊断。') : null,
      h('div', { className: 'fv-body' },
        h('div', { className: 'fv-upper' },
          filesVisible ? h(FileBrowser, { key: `${sessionId}:${browserEpoch}`, sessionId, tabSignal: tab.signal, call: control, selectedPath: ready ? activePath : undefined, onChoose: path => void load(false, path), onClose: () => setFilesVisible(false) }) : null,
          h('main', { className: 'fv-monitor', 'aria-label': '画面预览' },
            ready ? h(React.Fragment, null,
              h('div', { className: 'fv-monitor-title' }, h('span', null, '节目监视器'), h('span', null, frameValue === undefined ? '尚未定位' : `帧 ${frameValue}`)),
              h(Transport, { frame: frameValue, frameText: frame, frameError, duration, onFrame: pin, onText: pin, onQuery: () => void load(true) }),
              frameError ? h('p', { role: 'alert' }, `${frameError} 引用已暂停；不会暗用旧帧。`) : null,
              valid && !frameError ? h(FrameTools, { key: `${state.opened.grant}:${state.opened.snapshot.content_version}`, owner: frameOwner, opened: state.opened, sessionId, frame: frameValue, selection, tabSignal: tab.signal, getDraftServices, call: control }) : null,
              h('details', { className: 'fv-output', hidden: !state.opened.media.length },  h('summary', null, `已有成片${state.opened.media.length ? ` · ${state.opened.media.length}` : ' · 暂无'}`),
                h('h3', null, 'Existing output preview — provenance UNKNOWN'),
                h('p', null, 'These files are not verified to correspond to this timeline. Playback time is not linked to frame selection. Grants expire after 30 minutes; reopen on expiry.'),
                !state.opened.media.length ? h('p', null, 'No existing final.mp4 / draft_final.mp4 output found. No render was started.') : null,
                ...state.opened.media.map(media => h('section', { key: media.mediaId }, h('p', null, media.label, ' — ', media.warning),
                  h('video', { controls: true, preload: 'none', 'aria-label': `${media.label}（未验证对应版本）`, src: `/api/framivra/media?${new URLSearchParams({ sessionId, grant: state.opened.grant, mediaId: media.mediaId })}` }))))) :
              h('div', { className: 'fv-empty' }, h(Icon, { name: 'film', size: 38 }), h('h3', null, '从一条时间线开始'), h('p', null, '浏览当前工作区，选择工程的 JSON 文件。'),
                h('button', { className: 'fv-primary', type: 'button', onClick: () => setFilesVisible(true) }, h(Icon, { name: 'folder', size: 17 }), '打开时间线'))),
          ready ? h('aside', { className: 'fv-inspector', ref: inspector, 'aria-label': '属性与剪辑', hidden: !inspectorVisible },
            h('div', { className: 'fv-panel-heading' }, h('h3', null, '属性与剪辑'), h('button', { type: 'button', 'aria-label': '收起属性与剪辑', onClick: () => setInspectorVisible(false) }, h(Icon, { name: 'close', size: 15 }))),
            valid && !frameError ? h('details', { className: 'fv-inspector-section', open: true }, h('summary', null, '选区与引用'),
              h(ReferenceTools, { key: `${state.opened.grant}:${state.opened.snapshot.content_version}:${selection?.kind}:${selection?.address}`, selection, sessionId, opened: state.opened, frame: frameValue, tabSignal: tab.signal, getDraftServices,
                onStale: () => { clearSelection(); setState(previous => ({ ...previous, stale: true })) } })) : null,
            valid ? h('details', { className: 'fv-inspector-section', ref: manualPane }, h('summary', null, '剪辑编辑与提交'),
              h(ManualEditor, { key: state.opened.grant, sessionId, opened: state.opened, result: state.result, selection, selected, frame: frameValue, tabSignal: tab.signal, call: control, onCommitted: refreshCommitted, onEditState: editState, onGestureReady: callback => setGesture(() => callback), onQuickReady: callback => setQuickEdit(() => callback) })) : null,
             valid ? timelineData({ snapshot: state.opened.snapshot, result: state.result, hits: state.hits, onSelect: pick }) : null,
             valid ? h(TimelineHelp, { result: state.result, onGesture: gesture }) : null) : null),
        ready ? h(LayoutDivider, { value: timelineShare, onChange: setTimelineShare }) : null,
        ready ? h('section', { className: 'fv-timeline-pane', 'aria-label': '工程时间线' },
          valid ? h(SelectionSummary, { selection, selected, result: state.result, frame: frameValue, onFrame: pin, onClear: clearSelection, onProperties: () => showProperties(true), quick: quickEdit ? runQuick : undefined }) : null,
          state.stale ? h('p', { role: 'alert' }, '时间线版本已变化；旧块已隐藏，请刷新保存版本。') : h(TimelineView, { compact: true, snapshot: state.opened.snapshot, result: state.result, hits: state.hits, frame: frameValue, onFrame: value => pin(String(value)), onSelect: pick, activeSelection: selection, selectionIntent, onGesture: gesture, onGestureState: gestureState })) : null)))
}
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
