import React, { useLayoutEffect, useEffect, useRef, useState } from 'react'
import { boundedText } from '../bounded.mjs'
import { FrameTools } from './frame.mjs'
import { terminalGrantFailure } from './grant-lifecycle.mjs'
import { frameToSeconds, frameFromSeconds, projectClock } from './timecode.mjs'
import { ToolButton, ToolSummary } from './icon-controls.mjs'

const h = React.createElement

/** One project clock. A requested seek owns the cursor until the browser settles;
 * decoded-frame callbacks are observations, never a second independent playhead. */
export function RenderedPlayer({ opened, sessionId, frame, fps, duration, onFrame, disclosureGroup }) {
  const [mediaId, setMediaId] = useState(opened.media[0]?.mediaId ?? '')
  const [status, setStatus] = useState('正在读取成片…')
  const video = useRef(), pending = useRef(), reported = useRef(), live = useRef()
  const media = opened.media.find(item => item.mediaId === mediaId)
  const clock = projectClock(fps)
  const seconds = frameToSeconds(frame, fps)
  const usable = clock && Number.isSafeInteger(duration) && duration > 0 && seconds !== null && frame < duration
  live.current = { frame, fps, clock, duration, onFrame, usable }

  function publish(time, decoded = false) {
    const current = live.current
    if (!current.usable || pending.current) return
    // Decoded mediaTime is a PTS rounded to browser microseconds, not a play
    // position inside a frame. Match its nearest project PTS; currentTime uses
    // the containing frame instead. Neither clock may round a seek backwards.
    const value = frameFromSeconds(time + (decoded ? current.clock.den / (2 * current.clock.num) : 0), current.fps)
    if (value === null) return
    const next = Math.min(current.duration - 1, value)
    if (next !== current.frame) { reported.current = next; current.onFrame(String(next)) }
    if (value >= current.duration) seek(next)
  }
  function seek(targetFrame = live.current.frame) {
    const node = video.current, current = live.current
    if (!node || !current.usable || node.readyState < 1) return
    // Seek inside the requested frame: Chromium quantizes currentTime to µs,
    // so a rational start such as 29/30 can otherwise land in frame 28.
    const target = frameToSeconds(targetFrame, current.fps) + current.clock.den / (2 * current.clock.num)
    if (!Number.isFinite(node.duration) || target >= node.duration) {
      node.pause(); pending.current = undefined
      setStatus('该位置超出成片时长；保留工程播放头，不夹到成片末尾。')
      return
    }
    node.pause()
    pending.current = { frame: targetFrame, time: target }
    setStatus('正在对齐成片与工程播放头…')
    // Use currentTime, not fastSeek: fastSeek is allowed to land on a keyframe.
    node.currentTime = target
    if (!node.seeking && Math.abs(node.currentTime - target) < 1e-7) {
      pending.current = undefined; setStatus('')
    }
  }
  useLayoutEffect(() => {
    if (!usable) { video.current?.pause(); pending.current = undefined; reported.current = undefined; return }
    if (reported.current === frame) { reported.current = undefined; return }
    reported.current = undefined
    seek()
  }, [frame, fps, duration, mediaId, opened.grant, usable])
  useEffect(() => {
    const node = video.current
    if (!node) return
    let callback, disposed = false
    const decoded = (_now, metadata) => {
      if (disposed) return
      if (!node.paused && !node.seeking) publish(metadata.mediaTime, true)
      callback = node.requestVideoFrameCallback(decoded)
    }
    if (typeof node.requestVideoFrameCallback === 'function') callback = node.requestVideoFrameCallback(decoded)
    return () => {
      disposed = true
      if (callback !== undefined) node.cancelVideoFrameCallback?.(callback)
      node.pause()
      pending.current = undefined
    }
  }, [mediaId, opened.grant])
  const outside = usable && Number.isFinite(video.current?.duration) && seconds >= video.current.duration
  return h('section', { className: 'fv-rendered', 'aria-label': '成片渲染预览' },
    h('div', { className: 'fv-program-stage', 'data-mode': 'rendered' },
      media ? h('video', { ref: video, controls: !!usable && !outside, preload: 'metadata', playsInline: true, style: !usable || outside ? { visibility: 'hidden' } : undefined,
        'aria-label': `${media.label}（来源对应版本未验证）`,
        src: `/api/framivra/media?${new URLSearchParams({ sessionId, grant: opened.grant, mediaId })}`,
        onLoadedMetadata: () => seek(),
        onSeeking: () => { if (!pending.current) setStatus('正在定位成片…') },
        onSeeked: () => {
          const node = video.current
          if (!node) return
          const requested = pending.current
          pending.current = undefined
          // A user may interrupt a programmatic seek using native video controls.
          if (!requested || Math.abs(node.currentTime - requested.time) > 1e-7) publish(node.currentTime)
          setStatus('')
        },
        onTimeUpdate: () => { const node = video.current; if (node && !node.seeking && (node.paused || typeof node.requestVideoFrameCallback !== 'function')) publish(node.currentTime) },
        onPlay: () => { if (!live.current.usable || outside) video.current?.pause() },
        onEnded: () => { const node = video.current, current = live.current; if (node && current.usable) publish(Math.max(0, node.currentTime - current.clock.den / (2 * current.clock.num))) },
        onError: () => { pending.current = undefined; setStatus('成片无法解码或读取；请检查文件并重新打开时间线。') },
      }) : null,
      !media || !usable || outside ? h('p', { className: 'fv-program-empty' }, !media ? '暂无成片；不会自动启动整片渲染。' : !usable ? '请先固定有效时间码；无法识别帧率时不猜测播放位置。' : '该工程位置没有对应的成片画面。') : null),
    media ? h('div', { className: 'fv-monitor-footer' },
      h('details', { className: 'fv-monitor-disclosure fv-monitor-disclosure-up', name: disclosureGroup },
        h(ToolSummary, { icon: 'file', label: '成片文件与来源', tooltip: `${media.label} · 来源未验证，不作为当前合成或 QC 证据` }),
        h('div', { className: 'fv-monitor-popover' },
          opened.media.length > 1 ? h('label', null, '成片文件 ', h('select', { value: mediaId, onChange: event => { video.current?.pause(); pending.current = undefined; reported.current = undefined; setMediaId(event.target.value); setStatus('正在读取成片…') } }, opened.media.map(item => h('option', { key: item.mediaId, value: item.mediaId }, item.label)))) : h('p', null, media.label),
          h('p', null, '来源 UNKNOWN（未验证是否对应保存版本）。按工程时钟联动，不作为当前合成或 QC 证据。'),
          status && usable ? h('p', null, status) : null)),
      h('span', { className: 'fv-output-provenance' }, '来源未验证'),
      status && usable ? h('span', { className: 'fv-monitor-status', role: 'status', title: status }, status) : null) : null)
}

const WINDOW_LIMIT = 5 * 1024 * 1024
const WINDOW_SCOPE = '只监看当前窗口；不连续渲染，不代表全片已就绪。'
const integer = /^(0|[1-9][0-9]*)$/
const versionToken = /^sha256:[a-f0-9]{64}$/
const rawDigest = /^[a-f0-9]{64}$/
class WindowFailure extends Error {}

function windowRequest(operation, op, signal) {
  const { sessionId, grant, operationId, frame, contentVersion } = operation
  const args = op === 'render' ? { frame, contentVersion, renderConsent: 'cache/preview-only', prepareInputs: true } : {}
  return fetch('/api/framivra/window', { method: 'POST', credentials: 'same-origin', cache: 'no-store', redirect: 'error',
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ sessionId, grant, op, operationId, ...args }), signal })
}

async function windowBytes(response, operation) {
  const signal = operation.controller.signal
  if (!response.ok) {
    const value = JSON.parse(await boundedText(response, 16_384, signal))
    const code = typeof value?.error === 'string' && /^[A-Z][A-Z0-9_]{0,63}$/.test(value.error) ? value.error : 'WINDOW_REFUSED'
    throw new WindowFailure(`${code}: 宿主或内核未返回完整音画窗口；没有跳过轨道或回退到其他预览。`)
  }
  const { headers } = response
  const sha = headers.get('X-Framivra-SHA256'), length = headers.get('Content-Length')
  const reader = response.body?.getReader()
  const abort = () => { void reader?.cancel(signal.reason).catch(() => {}) }
  signal.addEventListener('abort', abort, { once: true })
  let size = 0, complete = false
  const chunks = []
  try {
    signal.throwIfAborted()
    if (response.status !== 200 || headers.get('Content-Type') !== 'video/mp4' || headers.get('X-Framivra-Version') !== operation.contentVersion ||
      headers.get('X-Framivra-Start') !== String(operation.frame) || headers.get('X-Framivra-End') !== String(operation.end) ||
      !rawDigest.test(sha) || (length !== null && (!integer.test(length) || Number(length) < 1 || Number(length) > WINDOW_LIMIT)) || !reader) {
      throw new WindowFailure('窗口回执、区间或媒体尺寸上限不符。')
    }
    for (;;) {
      const { done, value } = await reader.read()
      signal.throwIfAborted()
      if (done) { complete = true; break }
      if (!ArrayBuffer.isView(value) || value.BYTES_PER_ELEMENT !== 1 || value.length !== value.byteLength || (size += value.byteLength) > WINDOW_LIMIT) throw new WindowFailure('窗口超过 5 MiB 或并非有效字节流。')
      if (value.byteLength) chunks.push(value)
    }
  } finally {
    signal.removeEventListener('abort', abort)
    if (!complete) abort()
    reader?.releaseLock()
  }
  if (!size || (length !== null && Number(length) !== size)) throw new WindowFailure('窗口字节长度不符。')
  const bytes = new Uint8Array(size)
  let at = 0
  for (const chunk of chunks) { bytes.set(chunk, at); at += chunk.byteLength }
  const digest = await crypto.subtle.digest('SHA-256', bytes)
  signal.throwIfAborted()
  if (Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('') !== sha) throw new WindowFailure('窗口 SHA-256 不符。')
  return new Blob([bytes], { type: 'video/mp4' })
}

// This slot lives on the workbench owner, not the video or its grant-keyed React
// subtree. Unmount or replacement cannot turn an unreaped request into a new slot.
class WindowMonitor {
  state = {}
  listeners = new Set()
  subscribe = listener => { this.listeners.add(listener); return () => this.listeners.delete(listener) }
  snapshot = () => this.state
  update(state) { this.state = state; for (const listener of this.listeners) listener() }
  owns(operation) { return !!operation && this.state.operation === operation && !operation.controller.signal.aborted }
  interrupt(origin) { if (this.owns(this.state.operation) && this.state.operation !== origin) this.cancel('旧窗口已失效。') }

  async start(binding) {
    if (this.state.operation || binding.tabSignal.aborted) return
    const { num, den } = binding.clock
    const operation = { ...binding, operationId: crypto.randomUUID(), controller: new AbortController(), cursor: binding.frame,
      end: binding.frame + Math.min(binding.duration - binding.frame, Math.ceil(num / den)) }
    this.update({ operation, message: '正在准备并生成当前音画窗口…' })
    try {
      const blob = await windowBytes(await windowRequest(operation, 'render', operation.controller.signal), operation)
      if (!this.owns(operation)) return
      operation.url = URL.createObjectURL(blob)
      operation.decodeTimer = setTimeout(() => { if (this.owns(operation)) this.cancel('浏览器未及时解码窗口。') }, 15_000)
      this.update({ operation, url: operation.url, message: '正在解码已验证的音画窗口…' })
    } catch (error) {
      if (!this.owns(operation)) return
      // Only feature-owned text may cross into the UI. JSON/transport errors
      // can include raw response bytes or environment details in .message.
      const detail = error instanceof WindowFailure ? error.message : '读取或校验窗口失败。'
      this.cancel(`窗口不可用：${detail}`)
      const dead = terminalGrantFailure(detail)
      if (dead) operation.onGrantDead?.(dead, operation.grant)
    }
  }

  cancel(message = this.state.reason ?? '窗口已取消。') {
    const { operation } = this.state
    if (!operation || operation.reaping) return operation?.reaping
    const reaping = Promise.resolve().then(async () => {
      try {
        // No signal from the obsolete tab/grant/reader: cleanup must outlive all three.
        const signal = AbortSignal.timeout(45_000)
        const response = await windowRequest(operation, 'cancel', signal)
        const value = JSON.parse(await boundedText(response, 16_384, signal))
        if (!response.ok || value?.ok !== true || value.reaped !== true) throw new Error('Host did not confirm reaping')
        operation.reaping = undefined
        this.update({ message: `${message} 宿主已确认回收；不会自动生成。` })
      } catch {
        operation.reaping = undefined
        this.update({ operation, failed: true, reason: message, message: `${message} 宿主回收未确认；请重试回收。` })
      }
    })
    operation.reaping = reaping
    operation.controller.abort()
    clearTimeout(operation.decodeTimer)
    const node = operation.node
    if (node) {
      node.muted = true
      node.style.visibility = 'hidden'
      node.pause()
      if (operation.callback !== undefined) node.cancelVideoFrameCallback?.(operation.callback)
      node.removeAttribute('src')
      node.load()
      operation.node = undefined
    }
    if (operation.url) { URL.revokeObjectURL(operation.url); operation.url = undefined }
    this.update({ operation, message: `${message} 正在等待宿主确认回收…` })
    return reaping
  }
}

function WindowPlayer({ monitor, state, owner, binding, frame, blocked, onFrame, disclosureGroup }) {
  const [consent, setConsent] = useState(false), [prepare, setPrepare] = useState(false)
  const { sessionId, grant, contentVersion, duration, fps, tabSignal } = binding
  const { operation, message } = state
  // A replacement grant must not briefly mount the old URL. Deliberately do
  // not compare the mutable native cursor to React's last committed frame:
  // an unrelated render can commit before a pending native frame observation.
  const current = monitor.owns(operation) && !blocked && operation.sessionId === sessionId && operation.grant === grant &&
    operation.contentVersion === contentVersion && operation.fps === fps && operation.duration === duration && operation.tabSignal === tabSignal
  const url = current ? state.url : undefined, ready = current && state.ready
  useLayoutEffect(() => {
    const stop = () => monitor.interrupt()
    tabSignal.addEventListener('abort', stop, { once: true })
    return () => { tabSignal.removeEventListener('abort', stop); stop() }
  }, [monitor, sessionId, grant, contentVersion, duration, fps, tabSignal])
  useLayoutEffect(() => {
    // External pins invalidate FrameEpoch synchronously. Native observations
    // use the operation token and may commit out of phase with media events.
    if (blocked) monitor.interrupt()
  }, [monitor, blocked])

  function generate() {
    if (blocked || !consent || !prepare || owner.epoch !== epoch || monitor.state.operation || tabSignal.aborted) return
    if (!globalThis.crypto?.subtle || typeof crypto.randomUUID !== 'function' || typeof URL.createObjectURL !== 'function') {
      monitor.update({ message: '当前浏览器不能验证并播放临时媒体。' }); return
    }
    void monitor.start({ ...binding, frame })
  }
  function loaded(node) {
    if (!monitor.owns(operation) || monitor.state.ready) return
    const step = operation.clock.den / operation.clock.num
    const expected = (operation.end - operation.frame) * step
    // MP4 movie duration may include an audio sample/codec tail. Never accept a
    // substantially longer container than the validated frame interval.
    if (node.videoWidth !== 1920 || node.videoHeight !== 1080 || !Number.isFinite(node.duration) || Math.abs(node.duration - expected) > step / 2) {
      monitor.cancel('解码后的窗口尺寸或时长不符。'); return
    }
    clearTimeout(operation.decodeTimer)
    monitor.update({ operation, url, ready: true })
  }
  function publish(time, decoded = false) {
    if (!monitor.owns(operation) || !monitor.state.ready || tabSignal.aborted) return
    const adjustment = decoded ? operation.clock.den / (2 * operation.clock.num) : 0
    const offset = frameFromSeconds(time + adjustment, fps)
    if (offset === null) return
    const next = operation.frame + Math.min(operation.end - operation.frame - 1, offset)
    if (next !== operation.cursor) { operation.cursor = next; onFrame(String(next), operation) }
  }
  function observe(node) {
    if (!monitor.owns(operation) || !monitor.state.ready) { node.pause(); return }
    if (operation.callback !== undefined || typeof node.requestVideoFrameCallback !== 'function') return
    const decoded = (_now, metadata) => {
      operation.callback = undefined
      if (!monitor.owns(operation) || node.paused) return
      if (!node.seeking) publish(metadata.mediaTime, true)
      if (monitor.owns(operation)) operation.callback = node.requestVideoFrameCallback(decoded)
    }
    operation.callback = node.requestVideoFrameCallback(decoded)
  }
  const epoch = owner.epoch
  return h('section', { className: 'fv-rendered', 'aria-label': '单窗口音画监看' },
    h('div', { className: 'fv-program-stage', 'data-mode': 'window' },
      url ? h('video', { ref: node => { if (node) operation.node = node }, src: url, controls: !!ready,
        preload: 'auto', playsInline: true, disablePictureInPicture: true, disableRemotePlayback: true, 'aria-label': '仅当前窗口的原生音画',
        style: !ready ? { visibility: 'hidden' } : undefined,
        onLoadedData: event => loaded(event.currentTarget),
        onError: () => { if (monitor.owns(operation)) monitor.cancel('浏览器无法解码窗口。') },
        onPlay: event => observe(event.currentTarget),
        onSeeked: event => publish(event.currentTarget.currentTime),
        onTimeUpdate: event => { const node = event.currentTarget; if (!node.seeking && (node.paused || typeof node.requestVideoFrameCallback !== 'function')) publish(node.currentTime) },
        onEnded: () => publish((operation.end - operation.frame - 1) * operation.clock.den / operation.clock.num, true),
      }) : null,
      !ready ? h('p', { className: 'fv-program-empty' }, '尚无可播窗口。先勾选两项许可，再手动生成。') : null),
    h('div', { className: 'fv-monitor-footer' },
      h(ToolButton, { icon: 'camera', variant: 'primary', label: '生成当前音画窗口', disabled: blocked || !consent || !prepare || !!operation, onClick: generate }),
      h('details', { className: 'fv-monitor-disclosure fv-monitor-disclosure-up', name: disclosureGroup },
        h(ToolSummary, { icon: 'settings', label: '窗口预览授权' }),
        h('div', { className: 'fv-monitor-popover' },
          h('label', null, h('input', { type: 'checkbox', checked: consent, onChange: event => { monitor.interrupt(); setConsent(event.target.checked) } }), '允许写入 cache/preview（不修改时间线）'),
          h('label', null, h('input', { type: 'checkbox', checked: prepare, onChange: event => { monitor.interrupt(); setPrepare(event.target.checked) } }), '允许按需冷准备输入（不授权执行 HTML）'),
          h('p', null, '如无法生成完整音画窗口，直接拒绝，不丢轨。'))),
      operation ? h(ToolButton, { icon: 'cancel', label: state.failed ? '重试回收音画窗口' : '取消并回收音画窗口', disabled: !!operation.reaping, onClick: () => { void monitor.cancel() } }) : null,
      h('span', { className: 'fv-monitor-status', role: 'status', title: message ? `${message} ${WINDOW_SCOPE}` : WINDOW_SCOPE }, message ?? (ready ? `仅当前窗口 [${operation.frame}, ${operation.end}) · 不自动续窗` : WINDOW_SCOPE))))
}

function useWindowMonitor(owner) {
  const monitor = owner.previewWindow ??= new WindowMonitor()
  return [monitor, React.useSyncExternalStore(monitor.subscribe, monitor.snapshot, monitor.snapshot)]
}

function CleanupStatus({ monitor, state }) {
  if (!state.operation || !state.operation.controller.signal.aborted) return null
  return h('div', { className: 'fv-monitor-footer', role: 'status' }, state.message,
    state.failed ? h(ToolButton, { icon: 'cancel', label: '重试回收音画窗口', onClick: () => { void monitor.cancel() } }) : null)
}

// The workbench may hide grant-bound tools before their cleanup finishes.
export function WindowRecovery({ owner }) {
  const [monitor, state] = useWindowMonitor(owner)
  return h(CleanupStatus, { monitor, state })
}

export function ProgramMonitor({ owner, opened, timeline, selectionDisplay, sessionId, frame, blank = false, fps, duration, onFrame, selection, tabSignal, getDraftServices, call, transport, disclosureGroup, suspended = false, onGrantDead }) {
  const canRender = opened.capabilities?.capabilities?.frame?.inline_png?.flag === '--include-png'
  const windowCapability = opened.capabilities?.capabilities?.preview_window
  const canWindow = windowCapability?.schema === 'framivra.timeline.preview-window/v1' && windowCapability.inline_media === true && windowCapability.prepare_inputs === true &&
    Array.isArray(opened.host?.ops) && opened.host.ops.includes('preview-window')
  const [monitor, windowState] = useWindowMonitor(owner)
  const [chosen, setMode] = useState()
  const mode = chosen === 'window' && canWindow ? 'window' : chosen === 'rendered' || !canRender ? 'rendered' : 'live'
  const clock = projectClock(fps), contentVersion = opened.snapshot.content_version
  const blocked = suspended || !!(owner.ghost || owner.edit.dirty || owner.edit.busy || owner.edit.unknown) || !clock ||
    !Number.isSafeInteger(frame) || frame < 0 || !Number.isSafeInteger(duration) || frame >= duration || !versionToken.test(contentVersion)
  function change(next) {
    if (next === mode) return
    owner.invalidate()
    setMode(next)
    if (blank) onFrame('0')
  }
  return h('div', { className: 'fv-program-monitor' },
    h('div', { className: 'fv-monitor-toolbar' }, transport,
      h('div', { className: 'fv-preview-modes', role: 'group', 'aria-label': '预览模式' },
        canRender ? h(ToolButton, { icon: 'diagnostic', label: '固定帧预览', tooltip: '当前完整合成帧 · PNG，无声，不是视频播放', 'aria-pressed': mode === 'live', onClick: () => change('live') }) : null,
        canWindow ? h(ToolButton, { icon: 'camera', label: '音画窗口', tooltip: WINDOW_SCOPE, 'aria-pressed': mode === 'window', onClick: () => change('window') }) : null,
        h(ToolButton, { icon: 'film', label: '成片渲染', tooltip: '成片渲染 · 播放已有成片，不启动渲染', 'aria-pressed': mode === 'rendered', onClick: () => change('rendered') }))),
    canRender ? h(FrameTools, { key: contentVersion, owner, opened: timeline ? { ...opened, timeline } : opened, selectionDisplay, sessionId, frame, selection, tabSignal, getDraftServices, call, disclosureGroup, active: mode === 'live', suspended: suspended || !!windowState.operation }) : null,
    mode === 'window' ? h(WindowPlayer, { key: `${sessionId}:${opened.grant}:${contentVersion}`, monitor, state: windowState, owner, binding: { sessionId, grant: opened.grant, contentVersion, duration, fps, clock, tabSignal, onGrantDead }, frame, blocked, onFrame, disclosureGroup }) : null,
    !canRender && !canWindow ? h('p', { className: 'fv-monitor-status' }, '当前宿主或内核不支持生成监看预览；仍可查看时间线和已有成片。') : null,
    mode === 'rendered' ? h(RenderedPlayer, { opened, sessionId, frame, fps, duration, onFrame, disclosureGroup }) : null,
    mode !== 'window' ? h(CleanupStatus, { monitor, state: windowState }) : null)
}
