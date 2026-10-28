import React, { useEffect, useRef, useState } from 'react'
import { parseJSON, stringifyJSON } from '../json.mjs'

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
export function TimelineView({ snapshot, result }) {
  if (snapshot.content_version !== result.content_version) return h('p', { role: 'alert' }, 'Timeline changed between reads. Reopen preview; mixed-version selection is not displayed.')
  return h(React.Fragment, null,
    h('p', null, `Read-only · ${snapshot.content_version}`),
    h('p', null, 'Stored timeline blocks only; not render validation. Addresses are transient within this content version.'),
    ...[snapshot, result].filter(item => !item.coverage.complete).map((item, index) => h('div', { key: index, role: 'alert' },
      h('strong', null, 'Incomplete coverage: external subtitles/assets were not expanded.'), details('Unexpanded inputs', item.coverage))),
    details('Diagnostics', { inspect: snapshot.diagnostics, query: result.diagnostics }),
    ...result.tracks.map(track => h('section', { key: `${track.family}:${track.track_index}`, style: { borderTop: '1px solid currentColor', padding: '8px 0' } },
      h('h3', null, `${track.family} / track ${track.track_index}`),
      details(`Track ${track.address} — all vtx-emitted fields`, track.data),
      ...result.blocks.filter(block => block.family === track.family && block.track_index === track.track_index).map(block =>
        details(`${block.address} · frames [${block.range[0]}, ${block.range[1]})`, block.data, block.address)))),
    result.tracks.length === 0 ? h('p', null, 'No tracks matched this query.') : null,
    h('p', null, 'All fields of the vtx-emitted read document are shown; wire numeric tokens are preserved. Original source numeric/lexical precision is not guaranteed by the current upstream protocol.'),
    details('Complete vtx-emitted timeline document (all emitted fields)', snapshot.document),
    details('Complete query envelope', result))
}

export function PreviewBody({ useTabInfo, sessionId }) {
  const { tab } = useTabInfo()
  const [timeline, setTimeline] = useState('timeline.json')
  const [frame, setFrame] = useState('')
  const [state, setState] = useState({ phase: 'idle' })
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
      setState({ phase: 'ready', opened, result })
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
      h(TimelineView, { snapshot: state.opened.snapshot, result: state.result }),
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
    name: 'sidebar.right.pane.tab', key: ID, inject: sessionId => ({ sessionId }),
  }, PreviewBody)))
  ctx.effect(() => ctx.slots.inject('sidebar.right.tab.guide.entry', () => ctx.slots.register({ name: 'sidebar.right.tab.guide.entry', key: ID }, Guide)))
}
