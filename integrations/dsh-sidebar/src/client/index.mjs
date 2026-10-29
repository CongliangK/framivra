import React, { useEffect, useRef, useState } from 'react'
import { parseJSON, stringifyJSON } from '../json.mjs'
import { captureCurrentDraft, performReferenceAction } from './reference.mjs'

const h = React.createElement
const ID = 'framivra-dsh-sidebar'
export const inject = ['slots', 'sidebarRightTabs']
const pretty = value => stringifyJSON(value, 2)
const details = (title, data, key) => h('details', { key }, h('summary', null, title), h('pre', { style: { whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' } }, pretty(data)))

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
  const data = decodeControlResponse(await response.text())
  if (!response.ok) throw new Error(`${data.error}: ${data.message}${data.diagnostics ? `\n${pretty(data.diagnostics)}` : ''}`)
  return data
}

/** Pure presentation: all addresses, ranges, selection and raw fields come from vtx. */
export function TimelineView({ snapshot, result, onSelect }) {
  if (snapshot.content_version !== result.content_version) return h('p', { role: 'alert' }, 'Timeline changed between reads. Reopen preview; mixed-version selection is not displayed.')
  return h(React.Fragment, null,
    h('p', null, `Read-only · ${snapshot.content_version}`),
    h('p', null, 'Stored timeline blocks only; not render validation. Addresses are transient within this content version.'),
    ...[snapshot, result].filter(item => !item.coverage.complete).map((item, index) => h('div', { key: index, role: 'alert' },
      h('strong', null, 'Incomplete coverage: external subtitles/assets were not expanded.'), details('Unexpanded inputs', item.coverage))),
    details('Diagnostics', { inspect: snapshot.diagnostics, query: result.diagnostics }),
    ...result.tracks.map(track => h('section', { key: `${track.family}:${track.track_index}`, style: { borderTop: '1px solid currentColor', padding: '8px 0' } },
      h('h3', null, `${track.family} / track ${track.track_index}`),
      onSelect ? h('button', { type: 'button', onClick: () => onSelect({ kind: 'track', address: track.address }) }, `选择轨道 ${track.address}`) : null,
      details(`Track ${track.address} — all vtx-emitted fields`, track.data),
      ...result.blocks.filter(block => block.family === track.family && block.track_index === track.track_index).map(block =>
        h('div', { key: block.address },
          onSelect ? h('button', { type: 'button', onClick: () => onSelect({ kind: 'block', address: block.address }) }, `选择块 ${block.address}`) : null,
          details(`${block.address} · frames [${block.range[0]}, ${block.range[1]})`, block.data, block.address))))),
    result.tracks.length === 0 ? h('p', null, 'No tracks matched this query.') : null,
    h('p', null, 'All fields of the vtx-emitted read document are shown; wire numeric tokens are preserved. Original source numeric/lexical precision is not guaranteed by the current upstream protocol.'),
    details('Complete vtx-emitted timeline document (all emitted fields)', snapshot.document),
    details('Complete query envelope', result))
}

export function ReferenceTools({ selection, sessionId, opened, frame, tabSignal, getDraftServices, onStale }) {
  const [state, setState] = useState({})
  const task = useRef()
  useEffect(() => () => task.current?.abort(), [selection, sessionId, opened.grant, tabSignal])
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
      signal.throwIfAborted()
      setState(previous => ({ ...previous, busy: false, message: result.status === 'applied' ? '已追加到点击时会话的草稿；未发送。' : result.status === 'copied' ? '已复制。' : result.status === 'ready' ? '引用就绪。' : `草稿未更改：${result.status}/${result.reason}。保留引用供复制；请确认当前会话后重试。` }))
    } catch (error) {
      if (signal.aborted) return
      const stale = /STALE_REFERENCE|reference\.stale/.test(error.message)
      if (stale) onStale()
      setState(previous => ({ ...previous, busy: false, ...(stale ? { envelope: undefined, item: undefined } : {}), message: error.message }))
    }
  }
  useEffect(() => { setState({}); if (selection) void run('read') }, [selection, sessionId, opened.grant])
  return h('section', { 'aria-label': '时间线引用', style: { border: '1px solid currentColor', padding: 8 } },
    h('h3', null, '时间线引用 · 仅存储语义'),
    h('p', null, '选择轨道或块后可复制。raw JSON 是 vtx 提取的原文，不是上方 read/v1 的重编码。整数帧由 vtx 解释；播放是非精确采样、未与选择联动；本片不生成 PNG/附件。'),
    h('p', null, '加入草稿需要宿主公开 conversation.captureDraftTarget / stageDraft；缺少 API 或空白未创建会话时仅复制可用。不会自动创建会话、不会发送。'),
    selection ? h('p', null, `当前选择：${selection.kind} ${selection.address} · ${opened.snapshot.content_version}`) : h('p', null, '尚未选择轨道或块。'),
    ...[['id', '复制 ID / 临时定位'], ['raw', '复制 raw JSON'], ['diagnostic', '复制诊断引用 JSON'], ['draft', '加入当前会话草稿（不发送）']].map(([action, label]) => h('button', { key: action, type: 'button', disabled: !selection || state.busy, onClick: () => { void run(action) } }, label)),
    state.busy ? h('button', { type: 'button', onClick: () => { task.current?.abort(); setState(previous => ({ ...previous, busy: false, message: '已取消；未重试/补投草稿，选择与已读取原文保留。' })) } }, '取消引用操作') : null,
    state.message ? h('p', { role: 'status' }, state.message) : null,
    state.item ? h(React.Fragment, null,
      h('p', null, state.item.stable_id === null ? `legacy / 无协议稳定 ID：临时地址 ${state.item.address}（仅此 content_version 有效）` : `稳定 ID：${state.item.stable_id}`),
      h('pre', { 'aria-label': '选中对象 raw JSON', style: { whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' } }, state.item.raw ?? '无存储原文（虚拟轨道）'),
      details('vtx reference/v1 诊断与选择（完整）', state.envelope)) : null)
}

export function PreviewBody({ useTabInfo, sessionId, getDraftServices }) {
  const { tab } = useTabInfo()
  const [timeline, setTimeline] = useState('timeline.json')
  const [frame, setFrame] = useState('')
  const [state, setState] = useState({ phase: 'idle' })
  const [selection, setSelection] = useState()
  const task = useRef()
  const grant = useRef()
  const close = () => {
    const current = grant.current
    grant.current = undefined
    if (current) void control(sessionId, { op: 'close', grant: current }).catch(() => {})
  }
  useEffect(() => () => { task.current?.abort(); close() }, [sessionId])
  useEffect(() => {
    const abort = () => { task.current?.abort(); close() }
    tab.signal.addEventListener('abort', abort, { once: true })
    return () => { tab.signal.removeEventListener('abort', abort) }
  }, [tab.signal, sessionId])

  async function load(queryOnly = false) {
    let query
    try { query = queryOnly ? frameQuery(frame) : {} } catch (error) {
      // Keep the preview/grant and entered token so the user can correct it.
      // Invalid input must not query, reopen or even close over the network.
      setState(previous => ({ ...previous, message: error.message }))
      return
    }
    task.current?.abort()
    const controller = new AbortController()
    task.current = controller
    const signal = AbortSignal.any([controller.signal, tab.signal])
    const previous = state
    setState({ phase: 'loading' })
    try {
      let opened
      if (!queryOnly || previous.phase !== 'ready') {
        close()
        opened = await control(sessionId, { op: 'open', timeline }, signal)
        grant.current = opened.grant
      } else opened = previous.opened
      const result = await control(sessionId, { op: 'query', grant: opened.grant, query }, signal)
      signal.throwIfAborted()
      setSelection(undefined)
      setState({ phase: 'ready', opened, result, queryFrame: query.frame })
    } catch (error) {
      if (!signal.aborted) { close(); setState({ phase: 'failed', message: error.message }) }
    }
  }
  return h('article', { style: { padding: 12, overflow: 'auto', height: '100%', boxSizing: 'border-box' }, 'aria-label': 'Framivra read-only preview' },
    h('h2', null, 'Framivra · read-only'),
    h('p', null, 'Choose a relative timeline file inside this Session’s current workspace. External projects, remote filesystems, editing, rendering and attachments are not supported. This does not change the workspace.'),
    h('form', { onSubmit: event => { event.preventDefault(); void load() } },
      h('label', null, 'Timeline path ', h('input', { value: timeline, onChange: event => setTimeline(event.target.value), required: true })),
      h('button', { type: 'submit' }, 'Open / refresh')),
    state.phase === 'loading' ? h('p', { role: 'status' }, 'Reading through vtx…') : null,
    state.message ? h('pre', { role: 'alert', style: { whiteSpace: 'pre-wrap' } }, state.message) : null,
    state.phase === 'ready' ? h(React.Fragment, null,
      h('form', { onSubmit: event => { event.preventDefault(); void load(true) } },
        h('label', null, 'Frame (blank = all) ', h('input', { type: 'text', inputMode: 'numeric', value: frame, onChange: event => setFrame(event.target.value) })),
        h('button', { type: 'submit' }, 'Query stored blocks')),
      state.stale ? h('p', { role: 'alert' }, '时间线版本已变化；旧块已隐藏，请 Open / refresh。') : h(TimelineView, { snapshot: state.opened.snapshot, result: state.result, onSelect: setSelection }),
      state.opened.snapshot.content_version === state.result.content_version ? h(ReferenceTools, { key: `${state.opened.grant}:${selection?.kind}:${selection?.address}`, selection, sessionId, opened: state.opened, frame: state.queryFrame, tabSignal: tab.signal, getDraftServices,
        onStale: () => setState(previous => ({ ...previous, stale: true })),
      }) : null,
      h('h3', null, 'Existing output preview — provenance UNKNOWN'),
      h('p', null, 'These files are not verified to correspond to this timeline. Playback time is not linked to frame selection. Grants expire after 30 minutes; reopen on expiry.'),
      state.opened.media.length === 0 ? h('p', null, 'No existing final.mp4 / draft_final.mp4 output found. No render was started.') : null,
      ...state.opened.media.map(media => h('section', { key: media.mediaId }, h('p', null, media.label, ' — ', media.warning),
        h('video', { controls: true, preload: 'metadata', style: { width: '100%' }, src: `/api/framivra/media?${new URLSearchParams({ sessionId, grant: state.opened.grant, mediaId: media.mediaId })}` })))) : null)
}
function Guide({ useTabInfo }) {
  const { tab } = useTabInfo()
  return h('button', { onClick: () => tab.actions.openTab('framivra', { replaceTab: true }) }, 'Framivra · read-only timeline preview')
}
export function apply(ctx) {
  ctx.effect(() => ctx.sidebarRightTabs.register({
    id: ID, kind: 'framivra', priority: 'extension', title: () => 'Framivra',
    guide: [{ id: 'open', order: 40, title: () => 'Framivra preview', description: () => 'Read-only timeline and existing outputs' }],
  }))
  ctx.effect(() => ctx.slots.inject('sidebar.right.pane.tab', () => ctx.slots.register({
    name: 'sidebar.right.pane.tab', key: ID, inject: sessionId => ({ sessionId, getDraftServices: () => ({ conversation: ctx.get?.('conversation'), sessions: ctx.get?.('sessions') }) }),
  }, PreviewBody)))
  ctx.effect(() => ctx.slots.inject('sidebar.right.tab.guide.entry', () => ctx.slots.register({ name: 'sidebar.right.tab.guide.entry', key: ID }, Guide)))
}
