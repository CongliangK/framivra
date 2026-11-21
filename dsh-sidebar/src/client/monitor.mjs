import React, { useLayoutEffect, useEffect, useRef, useState } from 'react'
import { boundedText } from '../bounded.mjs'
import { FrameTools, decodePNG, matchesPngIhdr } from './frame.mjs'
import { terminalGrantFailure } from './grant-lifecycle.mjs'
import { frameToSeconds, frameFromSeconds, projectClock } from './timecode.mjs'
import { ToolButton, ToolSummary } from './icon-controls.mjs'
import { PreviewSessionController, supportsWindow, supportsWindowContinuation, supportsWindowFirstFrame, supportsWindowProgramAudio, supportsWindowProgramAudioContinuation } from './preview-session.mjs'
import { previewSessionLimits } from './preview-session-access.mjs'
import { usePreviewProgramRun } from './preview-program-run.mjs'
import { connectProgramPlayback } from './preview-program-playback.mjs'

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
const FIRST_FRAME_LIMIT = 6 * 1024 * 1024 // The launcher's <8 MiB base64 envelope.
const FIRST_FRAME_TIMEOUT = 30_000
const FIRST_FRAME_STATUS = '正在合成音画，当前仅显示已验首帧'
const HAVE_CURRENT_DATA = 2
const HAVE_FUTURE_DATA = 3
const WINDOW_SCOPE = '只监看当前窗口；不连续渲染，不代表全片已就绪。'
const integer = /^(0|[1-9][0-9]*)$/
const versionToken = /^sha256:[a-f0-9]{64}$/
const rawDigest = /^[a-f0-9]{64}$/
const canVerifyWindow = () => globalThis.crypto?.subtle && typeof crypto.randomUUID === 'function' && typeof URL.createObjectURL === 'function'
class WindowFailure extends Error {}
const ARCHIVE_CONFLICTS = new Map([
  ['ARCHIVE_SELECTOR_MISMATCH', '项目、起点或保存版本与归档批准不符。'],
  ['ARCHIVE_INPUTS_MISMATCH', '无法核对工程声明的输入。'],
  ['ARCHIVE_UNAVAILABLE', '无法复核归档批准、媒体或部署。'],
])

function windowRequest(operation, op, signal) {
  const { sessionId, grant, operationId, frame, contentVersion, formalArchiveOwner } = operation
  const args = op === 'render' ? { frame, contentVersion, ...(formalArchiveOwner ? {} : { renderConsent: 'cache/preview-only', prepareInputs: true }) } : {}
  const wireOp = formalArchiveOwner ? (op === 'render' ? 'formal-archive-read' : 'formal-archive-cancel') : op
  return fetch('/api/framivra/window', { method: 'POST', credentials: 'same-origin', cache: 'no-store', redirect: 'error',
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ sessionId, grant, op: wireOp, operationId, ...args,
      ...(formalArchiveOwner && { ownerId: formalArchiveOwner }) }), signal })
}

async function refuseMedia(response, signal, detail, conflicts) {
  const value = JSON.parse(await boundedText(response, 16_384, signal))
  const code = typeof value?.error === 'string' && /^[A-Z][A-Z0-9_]{0,63}$/.test(value.error) ? value.error : 'WINDOW_REFUSED'
  const conflict = response.status === 409 && value?.ok === false ? conflicts?.get(code) : undefined
  // Only fixed, local explanations cross this boundary, never kernel text or paths.
  if (response.status === 422 && code === 'WINDOW_REJECTED' && value.ok === false && Array.isArray(value.diagnostics)) {
    if (value.diagnostics.some(d => d?.code === 'gfx.unsupported')) detail = '当前预览内核不能完整核验这个工程的图形轨；未跳过动效，也未使用旧成片。'
    else if (value.diagnostics.some(d => d?.code === 'plugins.unsupported')) detail = '当前预览内核不支持这个工程声明的插件；未跳过插件。'
  }
  throw new WindowFailure(conflict ?? `${code}: ${detail}`)
}

async function formalArchiveBytes(response, job) {
  const { headers } = response, { signal } = job.controller, { num, den } = job.clock
  if (!response.ok) await refuseMedia(response, signal, '未取得该精确点的已核归档；不会生成其他预览。', ARCHIVE_CONFLICTS)
  const end = Number(headers.get('X-Framivra-End'))
  const bytes = await qualifiedMediaBytes(response, { ...job, end }, signal, 'video/mp4', WINDOW_LIMIT, '归档', () => {
    if (headers.get('X-Framivra-Media-Kind') !== 'approved-formal-archive/v1' || headers.get('X-Framivra-Operation-Id') !== job.operationId ||
        headers.get('X-Framivra-FPS') !== `${num}/${den}` || headers.get('X-Framivra-Input-Epoch') !== null ||
        headers.get('X-Framivra-Continuation-From') !== null || headers.get('X-Framivra-Program-Audio') !== null || headers.get('Content-Length') === null ||
        !Number.isSafeInteger(end) || end <= job.frame || end > job.duration || (end - job.frame) * den > 4 * num) {
      throw new WindowFailure('归档身份、区间或帧率不符。')
    }
  })
  return { end, blob: new Blob([bytes], { type: 'video/mp4' }) }
}

async function windowBytes(response, operation) {
  const signal = operation.controller.signal
  if (!response.ok) await refuseMedia(response, signal, '宿主或内核未返回完整音画窗口；没有跳过轨道或回退到其他预览。')
  const inputEpoch = response.headers.get('X-Framivra-Input-Epoch'), from = response.headers.get('X-Framivra-Continuation-From')
  const useProgramAudio = operation.automatic?.useProgramAudio === true
  const bytes = await qualifiedMediaBytes(response, operation, signal, 'video/mp4', WINDOW_LIMIT, '窗口', () => {
    if (response.headers.get('X-Framivra-Media-Kind') !== null) throw new WindowFailure('窗口媒体类别不符。')
    if (response.headers.get('X-Framivra-Program-Audio') !== (useProgramAudio ? '1' : null) ||
        (useProgramAudio && (!versionToken.test(inputEpoch) || (!operation.automatic.continuation && from !== null)))) {
      throw new WindowFailure('窗口音频模式或输入身份不符。')
    }
    if (operation.automatic?.continuation && (!versionToken.test(inputEpoch) ||
      (operation.previousOperationId ? from !== operation.previousOperationId || inputEpoch !== operation.inputEpoch : from !== null))) {
      throw new WindowFailure('窗口输入身份或续窗前驱不符。')
    }
  })
  return { blob: new Blob([bytes], { type: 'video/mp4' }), inputEpoch }
}

async function qualifiedMediaBytes(response, operation, signal, type, limit, label, check) {
  const { headers } = response
  const sha = headers.get('X-Framivra-SHA256'), length = headers.get('Content-Length')
  const reader = response.body?.getReader()
  const abort = () => { void reader?.cancel(signal.reason).catch(() => {}) }
  signal.addEventListener('abort', abort, { once: true })
  let size = 0, complete = false
  const chunks = []
  try {
    signal.throwIfAborted()
    if (response.status !== 200 || headers.get('Content-Type') !== type || headers.get('X-Framivra-Version') !== operation.contentVersion ||
      headers.get('X-Framivra-Start') !== String(operation.frame) || headers.get('X-Framivra-End') !== String(operation.end) ||
      !rawDigest.test(sha) || (length === null ? type === 'image/png' : !integer.test(length) || Number(length) < 1 || Number(length) > limit) || !reader) {
      throw new WindowFailure(`${label}回执、区间或媒体尺寸上限不符。`)
    }
    check?.()
    for (;;) {
      const { done, value } = await reader.read()
      signal.throwIfAborted()
      if (done) { complete = true; break }
      if (!ArrayBuffer.isView(value) || value.BYTES_PER_ELEMENT !== 1 || value.length !== value.byteLength || (size += value.byteLength) > limit) throw new WindowFailure(`${label}超过 ${limit / (1024 * 1024)} MiB 或并非有效字节流。`)
      if (value.byteLength) chunks.push(value)
    }
  } finally {
    signal.removeEventListener('abort', abort)
    if (!complete) abort()
    reader?.releaseLock()
  }
  if (!size || (length !== null && Number(length) !== size)) throw new WindowFailure(`${label}字节长度不符。`)
  const bytes = new Uint8Array(size)
  let at = 0
  for (const chunk of chunks) { bytes.set(chunk, at); at += chunk.byteLength }
  const digest = await crypto.subtle.digest('SHA-256', bytes)
  signal.throwIfAborted()
  if (Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('') !== sha) throw new WindowFailure(`${label} SHA-256 不符。`)
  return bytes
}

async function firstFrameBlob(response, job, signal) {
  const bytes = await qualifiedMediaBytes(response, job, signal, 'image/png', FIRST_FRAME_LIMIT, '首图', () => {
    if (response.headers.get('X-Framivra-Program-Audio') !== null) throw new WindowFailure('临时首图不能带有音频模式。')
  })
  if (!matchesPngIhdr(bytes, 1920, 1080)) throw new WindowFailure('首图 PNG 格式或尺寸不符。')
  const blob = new Blob([bytes], { type: 'image/png' })
  await decodePNG(blob, 1920, 1080, signal)
  return blob
}

function matchesWindowBinding(media, binding) {
  return media.project === binding.project && media.sessionId === binding.sessionId && media.grant === binding.grant &&
    media.contentVersion === binding.contentVersion && media.fps === binding.fps && media.duration === binding.duration && media.tabSignal === binding.tabSignal
}

function attachedWindowNode(media, node) {
  return node && node === media.node && node.isConnected && node.getAttribute('src') === media.url && node.currentSrc === media.url
}

function playableWindowNode(media) {
  const node = media.playableNode
  if (node === media.readyNode && attachedWindowNode(media, node) && !node.error &&
      (node.readyState >= HAVE_FUTURE_DATA || node.ended)) return node
}

function retireWindowCallback(media) {
  const callback = media.callback
  media.callback = undefined
  if (callback !== undefined) media.node?.cancelVideoFrameCallback?.(callback)
}

function disposeWindowMedia(media) {
  if (!media) return
  clearTimeout(media.decodeTimer)
  retireWindowCallback(media)
  const node = media.node
  if (node) {
    node.muted = true
    node.style.visibility = 'hidden'
    node.pause()
    node.removeAttribute('src')
    node.load()
    media.node = undefined
  }
  media.readyNode = media.playableNode = media.resumeAttempt = undefined
  URL.revokeObjectURL(media.url)
}

// The workbench owns the request/reap slot independently of the decoded media
// and of the grant-keyed video subtree. Neither disappearing can unlock a job.
export class WindowMonitor {
  state = {}
  listeners = new Set()
  automatic = new PreviewSessionController(this)
  subscribe = listener => { this.listeners.add(listener); return () => this.listeners.delete(listener) }
  snapshot = () => this.state
  update(state) { this.state = state; for (const listener of this.listeners) listener() }
  refresh() { this.update({ ...this.state }) }
  queuePlayback(operation) { this.playOperation = operation.operationId; this.refresh() }
  live(binding) {
    return !binding.tabSignal.aborted && (!binding.program || binding.program.live()) && (!binding.automatic || this.automatic.isLive(binding.automatic))
  }
  owns(media) { return !!media && this.state.active === media && this.live(media) }
  ownsNode(media, node) { return this.owns(media) && media.readyNode === node && attachedWindowNode(media, node) }
  ownsMedia(media) { return this.owns(media) || (!!media && this.state.spare === media && this.live(media)) }
  ownsJob(job) { return !!job && this.state.job === job && !job.controller.signal.aborted && this.live(job) }
  ownsFirstFrame(image) {
    return !!image && this.state.firstFrame === image && this.ownsJob(image.job) && Date.now() < image.job.firstFrameRead?.expiresAt
  }

  readFirstFrame(job) {
    const controller = new AbortController(), signal = controller.signal
    const expiresAt = Date.now() + FIRST_FRAME_TIMEOUT
    const read = job.firstFrameRead = { controller, expiresAt, deadline: setTimeout(() => this.stopFirstFrame(job), FIRST_FRAME_TIMEOUT) }
    let delay = 100
    const mayRead = () => !signal.aborted && this.ownsJob(job) && !job.received && Date.now() < expiresAt
    const poll = async () => {
      if (!mayRead()) return
      try {
        const response = await this.request(job, 'first-frame', signal)
        if (!mayRead()) { void response.body?.cancel().catch(() => {}); return }
        if (response.status === 204) {
          delay = Math.min(delay + 50, 200)
          read.retry = setTimeout(poll, delay)
          return
        }
        const blob = await firstFrameBlob(response, job, signal)
        if (!mayRead()) return
        // The blob is for this stage only: never the FrameEpoch/File/draft path.
        this.update({ ...this.state, firstFrame: { job, url: URL.createObjectURL(blob) } })
      } catch {
        // A denied or malformed read withdraws just the optional image. The
        // original render response still owns failure, budget and cancellation.
        this.stopFirstFrame(job)
      }
    }
    read.retry = setTimeout(poll, delay)
  }

  stopFirstFrame(job, retainShown = false) {
    const read = job?.firstFrameRead
    if (!read) return
    clearTimeout(read.retry)
    read.controller.abort()
    const image = this.state.firstFrame
    if (retainShown && image?.job === job) return
    clearTimeout(read.deadline)
    job.firstFrameRead = undefined
    if (image?.job !== job) return
    if (image.node) { image.node.hidden = true; image.node.removeAttribute('src'); image.node = undefined }
    URL.revokeObjectURL(image.url)
    this.update({ ...this.state, firstFrame: undefined })
  }

  // A live projection, not a ledger. Check the render's binding as well as
  // slot ownership: the previous subtree's layout cleanup may not have run.
  coverage(binding, { active, spare, job, failed }) {
    const matches = item => matchesWindowBinding(item, binding) && (!item.automatic || item.automatic.adapter === binding.previewSession)
    const ranges = []
    if (failed || binding.tabSignal.aborted) return ranges
    for (const media of [active, spare]) {
      if (!this.ownsMedia(media) || media.formalArchiveOwner || !matches(media) || !versionToken.test(media.inputEpoch)) continue
      if (media === spare ? !this.parkedSpare(active, spare) || !matches(active) : !playableWindowNode(media)) continue
      ranges.push({ status: 'ready', range: [media.frame, media.end] })
    }
    if (this.ownsJob(job) && !job.formalArchiveOwner && matches(job) && !job.received) ranges.push({ status: 'rendering', range: [job.frame, job.end] })
    return ranges
  }

  settledSuccessor(active, spare) {
    return !this.state.job && !this.state.failed && this.owns(active) && this.state.spare === spare && this.ownsMedia(spare) &&
      // Program reads have already checked their settled receipt, unlike the
      // automatic continuation producer which must separately prove reap.
      (active.program ? spare.program === active.program : active.reaped && spare.reaped && spare.automatic === active.automatic) &&
      matchesWindowBinding(spare, active) && spare.clock === active.clock && spare.from === active.operationId && spare.frame === active.end &&
      versionToken.test(spare.inputEpoch) && spare.inputEpoch === active.inputEpoch
  }

  parkedSpare(active, spare) {
    if (!this.settledSuccessor(active, spare)) return false
    const node = playableWindowNode(spare)
    return node?.paused && !node.ended && !node.seeking && node.currentTime === 0 && spare.pendingSeek === undefined && spare.cursor === spare.frame
  }

  retainPendingRender(scope, frame) {
    const { job, active, failed } = this.state
    if (active || failed || job?.pinFrame === undefined || job.automatic !== scope || job.received || job.reaping ||
        !this.ownsJob(job) || !matchesWindowBinding(job, scope.binding) || job.clock !== scope.binding.clock ||
        frame < job.frame || frame >= job.end) return false
    job.pinFrame = frame
    return true
  }

  promoteSpareForPin(scope, frame) {
    const { active, spare } = this.state
    const { binding } = scope
    if (!scope.continuation || active?.automatic !== scope || !spare || frame < spare.frame || frame >= spare.end ||
        !matchesWindowBinding(active, binding) || active.clock !== binding.clock || !this.parkedSpare(active, spare)) return false
    const previous = playableWindowNode(active)
    if (!previous?.paused || previous.seeking || active.pendingSeek !== undefined) return false

    // This is a position change, not the native boundary handoff. Commit the
    // cursor/seek before publishing the slot; a reused key/ref must not play
    // first or take its position from the previous React frame.
    spare.resume = spare.playIntent = false
    this.seek(spare, frame)
    this.update({ active: spare })
    disposeWindowMedia(active)
    return true
  }

  interrupt() {
    const { job, active } = this.state
    if (active || (job && !job.controller.signal.aborted)) this.cancel('旧窗口已失效。')
  }
  invalidate(origin, pin) {
    if (this.owns(origin)) return // The owning video is reporting playback, not requesting a seek.
    const hadPendingPlayback = this.playOperation !== undefined
    this.playOperation = undefined
    if (this.program) { this.program.invalidate(pin); return }
    if (pin && !origin && this.automatic.pin(pin.frame)) {
      if (hadPendingPlayback) this.refresh()
      return
    }
    this.automatic.discardIntent()
    this.interrupt()
  }
  reapBeforeEdit() {
    if (this.stopProgram) return this.stopProgram().then(ok => ok === true && (this.reapBeforeEdit() ?? true))
    const { job, active, failed } = this.state
    this.automatic.holdForEdit(job ?? active)
    if (!job && !active) return
    // A manual, automatic, or archive job can all occupy the Host. Share the
    // exact cancellation started by invalidation; only recovery retries a failure.
    if (failed) return Promise.resolve(false)
    return this.cancel('编辑前回收音画预览。').then(reaped => reaped && !this.state.job)
  }
  request(job, op, signal) {
    const { automatic, operationId, contentVersion, frame, end, previousOperationId, inputEpoch } = job
    return automatic ? automatic.request(op, { grant: automatic.grant, operationId, contentVersion, frame, end,
      ...(op === 'render' && job.wantsFirstFrame ? { firstFrame: true } : {}),
      ...(op === 'continue' ? { previousOperationId, inputEpoch } : {}) }, signal)
      : windowRequest(job, op, signal)
  }
  seek(media, frame) {
    const node = media.node
    if (!node) return
    retireWindowCallback(media)
    node.pause()
    if (frame !== media.cursor) {
      const { clock } = media
      const time = (frame - media.frame + 0.5) * clock.den / clock.num
      media.cursor = frame
      media.pendingSeek = time
      node.currentTime = time
      if (!node.seeking && Math.abs(node.currentTime - time) < 1e-6) media.pendingSeek = undefined
    }
    this.stopContinuation(media)
  }

  ready(media, node) {
    if (!this.ownsMedia(media) || !attachedWindowNode(media, node) || media.ready) return
    // A Blob URL may be selected and emit progress before its metadata or
    // first frame exists. This is not evidence of a bad size or duration.
    const pin = media.initialPin
    if (pin && (node.error || node.ended || node.readyState < HAVE_CURRENT_DATA)) return
    const step = media.clock.den / media.clock.num
    const expected = (media.end - media.frame) * step
    // The container may include an audio-sample/codec tail. Do not derive a
    // project-frame endpoint from that duration.
    if (node.videoWidth !== 1920 || node.videoHeight !== 1080 || !Number.isFinite(node.duration) || Math.abs(node.duration - expected) > step / 2) {
      this.rejectMedia(media, media.formalArchiveOwner ? '解码后的归档 A/V 尺寸或时长不符。' : '解码后的窗口尺寸或时长不符。'); return
    }
    if (media.awaitPlayable && node.readyState < HAVE_FUTURE_DATA) return
    if (pin) {
      const offset = pin.frame - media.frame
      if (!pin.started) {
        const time = (offset + 0.5) * step
        const seekable = node.seekable
        let reachable = false
        for (let i = 0; i < seekable.length; i++) {
          if (seekable.start(i) <= time && time < seekable.end(i)) { reachable = true; break }
        }
        if (!reachable || time >= node.duration) return
        pin.started = true
        node.pause()
        try { node.currentTime = time } catch { this.cancel('浏览器无法定位已验证的音画窗口。'); return }
      }
      // A clamped/ignored seek cannot certify the requested frame. Keep the
      // stage hidden and let the existing decode deadline retire the operation.
      if (!node.paused || node.seeking || node.readyState < HAVE_CURRENT_DATA || frameFromSeconds(node.currentTime, media.fps) !== offset) return
      media.cursor = pin.frame
      media.initialPin = undefined
    }
    clearTimeout(media.decodeTimer)
    media.ready = true
    media.readyNode = node
    const image = this.state.firstFrame
    if (image?.job.operationId === media.operationId) this.stopFirstFrame(image.job)
    this.update({ ...this.state, message: undefined })
    this.advance()
  }
  playable(media, node, ready) {
    if (!this.ownsMedia(media) || media.node !== node) return
    const proof = ready && node.readyState >= HAVE_FUTURE_DATA ? node : undefined
    if (media.playableNode !== proof) {
      media.playableNode = proof
      this.update({ ...this.state })
    }
  }
  stopContinuation(media) {
    if (!this.owns(media)) return
    media.playIntent = false
    media.resume = false
    media.resumeAttempt = undefined
    if (media.program) {
      if (this.playOperation === media.operationId) this.playOperation = undefined
      if (media.node?.paused) retireWindowCallback(media)
      media.program.pause(media)
    }
    if (media.waiting) {
      media.waiting = false
      this.refresh()
    }
  }
  played(media) {
    if (!this.owns(media)) return
    if (media.playDenied) {
      media.playDenied = false
      this.update({ ...this.state, message: undefined })
    } else this.refresh()
    if (media.program || media.automatic?.continuation) {
      media.playIntent = true
      media.atEnd = media.waiting = false
      this.advance()
    }
  }
  ended(media) {
    if (!this.owns(media) || !media.playIntent) return
    media.atEnd = true
    this.advance()
  }
  advance() {
    const { active, spare, job } = this.state
    if (!this.owns(active) || !active.ready || !active.playIntent || !(active.program || active.automatic?.continuation) || active.end >= active.duration ||
        (active.atEnd ? !active.node?.ended : active.node?.paused)) return
    if (active.atEnd && !job && spare?.ready) {
      const node = spare.node
      if (!this.settledSuccessor(active, spare) || node !== spare.readyNode || !attachedWindowNode(spare, node) || node.error || node.ended) {
        this.rejectMedia(spare, '下一窗的来源或播放状态已失效。'); return
      }
      // Program consumption requires a currently playable parked node, not
      // just a decoded still left behind after readiness regressed.
      if (node.readyState >= (active.program ? HAVE_FUTURE_DATA : HAVE_CURRENT_DATA)) {
        if (!node.paused || node.seeking || node.currentTime !== 0 || (active.program && !node.muted)) {
          this.rejectMedia(spare, '下一窗不在静止音画起点。'); return
        }
        if (!active.program) { this.promoteSuccessor(active, spare); return }
        if (this.parkedSpare(active, spare)) {
          if (!active.waiting) { active.waiting = true; this.refresh() }
          active.program.atBoundary(active, spare)
          return
        }
      }
    }
    if (active.program) {
      if (active.successorFailed) {
        if (active.atEnd) this.failProgramHandoff(active, '下一窗不可播放；音画已停止。')
        return
      }
      if (active.atEnd) {
        if (!active.waiting) { active.waiting = true; this.refresh() }
        active.program.atBoundary(active)
      }
      if (!spare) active.program.prefetch(active)
      return
    }
    const canPrepare = active.automatic.canDispatch?.() === true
    if (active.atEnd && !active.waiting && (job || spare || canPrepare)) {
      active.waiting = true
      this.update({ ...this.state })
    }
    if (!job && !spare && canPrepare) void this.start({ ...active.automatic.binding, frame: active.end, automatic: active.automatic }, active)
  }
  promoteSuccessor(previous, spare) {
    spare.resume = true
    this.update({ active: spare })
    disposeWindowMedia(previous)
  }

  promoteProgramSuccessor(previous, previousNode, spare, node) {
    if (!this.ownsNode(previous, previousNode) || !previous.playIntent || !previous.atEnd || !previousNode.ended ||
        spare.node !== node || !node?.muted || !this.parkedSpare(previous, spare)) return false
    this.promoteSuccessor(previous, spare)
    return true
  }

  failProgramHandoff(previous, message) {
    if (!this.owns(previous) || !previous.playIntent) return
    previous.successorFailed = true
    this.stopContinuation(previous)
    this.update({ ...this.state, message })
  }

  async start(binding, previous, play = false) {
    if (this.state.job || this.state.spare || this.state.active !== previous || !this.live(binding)) return
    const archive = !!binding.formalArchiveOwner
    if (archive && (previous || binding.automatic)) return
    if (previous && (!this.owns(previous) || !previous.ready || !previous.playIntent || !binding.automatic?.continuation ||
      previous.automatic !== binding.automatic || previous.contentVersion !== binding.contentVersion ||
      previous.end !== binding.frame || !versionToken.test(previous.inputEpoch))) return
    const { num, den } = binding.clock
    const range = { ...binding, operationId: crypto.randomUUID(),
      end: archive ? undefined : binding.frame + Math.min(binding.duration - binding.frame, Math.ceil(num / den)) }
    const job = { ...range, controller: new AbortController(), wantsFirstFrame: !previous && binding.automatic?.firstFrame === true }
    if (previous) Object.assign(job, { previousOperationId: previous.operationId, inputEpoch: previous.inputEpoch })
    if (play) this.playOperation = job.operationId
    this.update({ ...this.state, job, message: archive ? '正在读取已核归档 A/V…' : previous ? undefined :
      job.automatic ? '正在生成当前音画窗口…' : '正在准备并生成当前音画窗口…' })
    try {
      const terminal = this.request(job, previous ? 'continue' : 'render', job.controller.signal)
      const automatic = job.automatic
      // Only an already dispatched, ordinary auto render may carry a new
      // destination. Its identity and predicted receipt interval never change.
      if (!previous && automatic && !automatic.firstFrame && !automatic.continuation && !automatic.useProgramAudio &&
          automatic.grant.readWarmSvg !== true && automatic.grant.firstFrame !== true && automatic.grant.continuation !== true) job.pinFrame = job.frame
      if (job.wantsFirstFrame && this.ownsJob(job)) this.readFirstFrame(job)
      const response = await terminal
      if (this.ownsJob(job)) {
        // No new image can commit once the terminal response has arrived. An
        // already displayed image stays only through verified video handoff.
        job.received = true
        this.stopFirstFrame(job, response.status === 200)
        this.update({ ...this.state })
      }
      const verified = archive ? await formalArchiveBytes(response, job) : await windowBytes(response, job)
      if (!this.ownsJob(job)) return
      const media = { ...range, ...(archive ? { end: verified.end } : { inputEpoch: verified.inputEpoch, from: job.previousOperationId }),
        url: URL.createObjectURL(verified.blob), cursor: range.frame, awaitPlayable: archive || job.wantsFirstFrame,
        initialPin: job.pinFrame !== undefined && job.pinFrame !== job.frame ? { frame: job.pinFrame } : undefined }
      media.decodeTimer = setTimeout(() => {
        if (this.ownsMedia(media)) this.cancel(archive ? '浏览器未及时解码归档 A/V。' : '浏览器未及时解码窗口。')
      }, 15_000)
      this.update({ ...this.state, [previous ? 'spare' : 'active']: media,
        message: archive ? '正在解码已核归档 A/V…' : previous ? undefined : '正在解码已验证的音画窗口…' })
      // Legacy/manual windows retain the existing explicit-cancel lifecycle.
      // Continuation requires actual Host proof before any successor admission.
      if (job.automatic?.continuation) void this.reap(job, '已完成窗口。')
    } catch (error) {
      if (!this.ownsJob(job)) return
      // Only feature-owned text may cross into the UI. JSON/transport errors
      // can include raw response bytes or environment details in .message.
      const detail = error instanceof WindowFailure ? error.message : '读取或校验窗口失败。'
      this.cancel(`${archive ? '归档' : '窗口'}不可用：${detail}`)
      if (job.automatic) job.automatic.onFailure?.(error instanceof WindowFailure ? detail.split(':', 1)[0] : undefined)
      else {
        const dead = terminalGrantFailure(detail)
        if (dead) job.onGrantDead?.(dead, job.grant)
      }
    }
  }

  dropSpare(spare) {
    if (this.state.spare !== spare) return
    this.update({ ...this.state, spare: undefined })
    disposeWindowMedia(spare)
  }

  rejectMedia(media, message) {
    if (!this.ownsMedia(media)) return
    if (media.program && this.state.spare === media) {
      // Speculation may fail without interrupting the valid current A/V.
      const active = this.state.active
      if (this.owns(active) && active.program === media.program) active.successorFailed = true
      this.dropSpare(media)
      this.advance()
    } else void this.cancel(message)
  }

  dropMedia(state) {
    const { active, spare } = this.state
    active?.program?.abortPrefetch(active)
    this.playOperation = undefined
    this.update({ ...state, active: undefined, spare: undefined })
    disposeWindowMedia(active)
    disposeWindowMedia(spare)
  }

  cancel(message = this.state.reason ?? '窗口已取消。') {
    this.playOperation = undefined
    const { job, active, spare } = this.state
    this.stopFirstFrame(job)
    if (active || spare) this.dropMedia({ ...this.state, reason: job ? message : undefined, message })
    if (job) return this.reap(job, message)
    return Promise.resolve(true)
  }

  reap(job, message) {
    if (job.reaping) return job.reaping
    const reaping = Promise.resolve().then(async () => {
      const grant = job.automatic?.grant
      try {
        // No signal from the obsolete tab/grant/reader: cleanup must outlive all three.
        const signal = AbortSignal.timeout(45_000)
        const response = await this.request(job, 'cancel', signal)
        const value = JSON.parse(await boundedText(response, 16_384, signal))
        if (!response.ok || value?.ok !== true || value.reaped !== true ||
          (job.formalArchiveOwner && (value.operationId !== job.operationId || value.ownerId !== job.formalArchiveOwner || value.openGrant !== job.grant)) ||
          (grant && (value.operationId !== job.operationId || value.ownerId !== grant.ownerId ||
            value.openGrant !== grant.openGrant || value.grantId !== grant.id))) throw new Error('Host did not confirm reaping')
      } catch {
        job.reaping = undefined
        const reason = this.state.reason ?? message
        this.dropMedia({ ...this.state, failed: true, reason, message: `${reason} 宿主回收未确认；请重试回收。` })
        return false
      }
      job.reaping = undefined
      const media = job.previousOperationId ? this.state.spare : this.state.active
      if (grant && media?.operationId === job.operationId) media.reaped = true
      this.update({ ...this.state, job: undefined, failed: false, reason: undefined,
        message: this.state.active ? undefined : `${this.state.reason ?? message} 宿主已确认回收。${grant ? '' : '不会自动生成。'}` })
      this.automatic.drain()
      this.advance()
      return true
    })
    job.reaping = reaping
    job.controller.abort()
    this.update({ ...this.state, failed: false, message: this.state.active ? this.state.message : `${message} 正在等待宿主确认回收…` })
    return reaping
  }
}

function AutoPreviewPermission({ controls, blocked }) {
  const [render, setRender] = useState(false), [prepare, setPrepare] = useState(false), [continuation, setContinuation] = useState(false)
  const [readWarmSvg, setReadWarmSvg] = useState(false), [firstFrame, setFirstFrame] = useState(false)
  const [useProgramAudio, setUseProgramAudio] = useState(false)
  const { adapter, needsRevoke, pending, note, failed, used, supportsContinuation, supportsWarmSvg,
    supportsFirstFrame, supportsProgramAudio, supportsProgramAudioContinuation } = controls
  return h('fieldset', null,
    h('legend', null, '会话自动预览（默认关闭）'),
    h('p', null, `仅本会话的当前工程，默认只处理普通输入。最多 ${previewSessionLimits.minutes} 分钟、${previewSessionLimits.windows} 窗，不超过本次打开授权。不会续期，不授权 HTML、插件或写编辑。`),
    adapter ? h('p', { role: 'status' }, `已开启：截至 ${new Date(adapter.grant.expiresAt).toLocaleString()} · 已请求 ${used}/${adapter.grant.maxWindows} 窗${used >= adapter.grant.maxWindows ? ' · 额度已用完，不会续期' : ''}${adapter.grant.readWarmSvg === true ? ' · 允许读取已验证的暖 SVG 包' : ''}${adapter.qualification.continuation?.version === 1 ? ' · 允许连续播放时准备下一窗' : ''}${adapter.grant.firstFrame === true ? ' · 允许显示临时首图' : ''}${adapter.grant.useProgramAudio === true ? ' · 允许整片音频基准混音' : ''}`) :
      !needsRevoke ? h(React.Fragment, null,
        h('label', null, h('input', { type: 'checkbox', checked: render, disabled: pending, onChange: event => {
          setRender(event.target.checked); if (!event.target.checked) setUseProgramAudio(false)
        } }), '允许自动生成窗口（仅 cache/preview）'),
        h('label', null, h('input', { type: 'checkbox', checked: prepare, disabled: pending, onChange: event => {
          setPrepare(event.target.checked); if (!event.target.checked) setUseProgramAudio(false)
        } }), '允许自动预览按需冷准备普通输入'),
        supportsWarmSvg ? h(React.Fragment, null,
          h('label', null, h('input', { type: 'checkbox', checked: readWarmSvg, disabled: pending || firstFrame, onChange: event => setReadWarmSvg(event.target.checked) }), '允许读取已验证的暖 SVG 包（仅命中的窗口，不生成图形）'),
          h('p', null, '未命中 SVG 时沿用普通窗口规则；若工程另有图形，宿主不会跳过图形来生成窗口。')) : null,
        supportsContinuation ? h('label', null, h('input', { type: 'checkbox', checked: continuation, disabled: pending || firstFrame || (useProgramAudio && !supportsProgramAudioContinuation), onChange: event => setContinuation(event.target.checked) }), '允许连续播放时准备下一窗（未就绪时音画一起等待）') : null,
        supportsFirstFrame ? h('label', null, h('input', { type: 'checkbox', checked: firstFrame, disabled: pending || continuation || readWarmSvg || useProgramAudio,
          onChange: event => setFirstFrame(event.target.checked) }), '允许显示临时首图（仅普通输入，不可与续窗或暖 SVG 同选）') : null,
        supportsProgramAudio ? h(React.Fragment, null,
          h('label', null, h('input', { type: 'checkbox', checked: useProgramAudio, disabled: pending || !render || !prepare || firstFrame || (continuation && !supportsProgramAudioContinuation),
            onChange: event => setUseProgramAudio(event.target.checked) }), '允许整片音频基准混音（限短工程，仅在私有窗口工作区处理）'),
          h('p', null, supportsProgramAudioContinuation ? '整片音频基准可与暖 SVG、续窗分别授权，不可与临时首图同选。开启后，需点击窗口原生播放才会准备下一窗。' :
            '整片音频基准可与已授权的暖 SVG 同选，不可与临时首图同选。当前宿主或内核不支持与续窗合用。')) : null) : null,
    needsRevoke ? h('button', { type: 'button', disabled: pending, onClick: controls.revoke }, failed ? '重试撤销自动预览授权' : '停止并撤销自动预览') :
      h('button', { type: 'button', disabled: blocked || pending || !render || !prepare, onClick: () => {
        setRender(false); setPrepare(false); setContinuation(false); setReadWarmSvg(false); setFirstFrame(false); setUseProgramAudio(false)
        void controls.authorize({ continuation: supportsContinuation && continuation, readWarmSvg: supportsWarmSvg && readWarmSvg,
          firstFrame: supportsFirstFrame && firstFrame, useProgramAudio: supportsProgramAudio && useProgramAudio })
      } }, '开启当前工程自动预览'),
    pending ? h('p', { role: 'status' }, '等待宿主确认…') : null,
    failed ? h('p', { role: 'alert' }, '宿主尚未确认撤销。自动预览已停止；请重试撤销。') : !adapter && note ? h('p', { role: 'status' }, note) : null)
}

function WindowPlayer({ monitor, state, owner, binding, frame, blocked, onFrame, disclosureGroup, sessionControls, previewSession, formalArchiveOwner, windowTransport, programBusy }) {
  const permission = useRef()
  const [consent, setConsent] = useState(false), [prepare, setPrepare] = useState(false)
  const { project, sessionId, grant, contentVersion, duration, fps, tabSignal } = binding
  const { job, active: media, firstFrame: image, message } = state
  const occupied = job || media
  const archive = !!occupied?.formalArchiveOwner
  const autoOccupies = !!(sessionControls?.needsRevoke || sessionControls?.pending || sessionControls?.adapter)
  const canReadArchive = !!formalArchiveOwner && !autoOccupies && !blocked && !occupied && !!canVerifyWindow()
  // A replacement grant must not briefly mount the old URL. Deliberately do
  // not compare the mutable native cursor to React's last committed frame:
  // an unrelated render can commit before a pending native frame observation.
  const current = monitor.owns(media) && !blocked && media.project === project && media.sessionId === sessionId && media.grant === grant &&
    media.contentVersion === contentVersion && media.fps === fps && media.duration === duration && media.tabSignal === tabSignal &&
    (!media.formalArchiveOwner || (media.formalArchiveOwner === formalArchiveOwner && !autoOccupies))
  const showFirstFrame = !blocked && monitor.ownsFirstFrame(image) && image.job.frame === frame && image.job.project === project &&
    image.job.sessionId === sessionId && image.job.grant === grant && image.job.contentVersion === contentVersion &&
    image.job.fps === fps && image.job.duration === duration && image.job.tabSignal === tabSignal && image.job.automatic.adapter === sessionControls?.adapter
  const ready = current && media.ready, waiting = ready && media.waiting
  const scopeText = archive ? '已核归档 A/V；不代表当前渲染或缓存覆盖。' :
    programBusy ? '播放本次后台任务捕获的输入。最多预缓冲一个相邻窗口，切换前复核；未就绪时音画一起等待，不保证无缝。更换外部素材后须停止并重新开始。' :
    media?.automatic?.continuation ? '仅准备一个相邻后继；未就绪时音画一起等待。' : WINDOW_SCOPE
  const status = showFirstFrame ? image.job.received ? '正在核验音画窗口，当前仅显示已验首帧' : FIRST_FRAME_STATUS :
    message ?? (waiting ? '音画正在一起等待下一窗。' : ready ? archive ? `已核归档 A/V [${media.frame}, ${media.end})` :
      `当前窗口 [${media.frame}, ${media.end})${media.program ? ' · 预缓冲一个相邻窗口，不保证无缝' : media.automatic?.continuation ? '' : ' · 不自动续窗'}` : scopeText)
  useLayoutEffect(() => {
    const stop = () => monitor.interrupt()
    tabSignal.addEventListener('abort', stop, { once: true })
    return () => { tabSignal.removeEventListener('abort', stop); stop() }
  }, [monitor, project, sessionId, grant, contentVersion, duration, fps, tabSignal])
  useLayoutEffect(() => {
    // External pins invalidate FrameEpoch synchronously. Native observations
    // use the operation token and may commit out of phase with media events.
    if (blocked || (archive && (autoOccupies || occupied.formalArchiveOwner !== formalArchiveOwner))) monitor.interrupt()
  }, [monitor, blocked, archive, autoOccupies, occupied, formalArchiveOwner])

  function readArchive() {
    if (!canReadArchive || owner.epoch !== epoch || tabSignal.aborted) return
    void monitor.start({ ...binding, frame, formalArchiveOwner })
  }
  function generate() {
    if (blocked || !consent || !prepare || owner.epoch !== epoch || monitor.state.job || monitor.state.active || tabSignal.aborted) return
    if (!canVerifyWindow()) {
      monitor.update({ message: '当前浏览器不能验证并播放临时媒体。' }); return
    }
    void monitor.start({ ...binding, frame })
  }
  function toggle() {
    if (blocked || tabSignal.aborted) return
    const { active, job } = monitor.state
    const operation = active ?? job
    if (operation) {
      if (!matchesWindowBinding(operation, binding)) return
      const node = active?.node
      const pending = monitor.playOperation === operation.operationId
      if (pending || active?.resumeAttempt || active?.waiting || node?.paused === false) {
        monitor.playOperation = undefined
        if (active?.ready) {
          node?.pause()
          monitor.stopContinuation(active)
          monitor.refresh()
        } else {
          monitor.automatic.discardIntent()
          void monitor.cancel('已取消待播放的音画窗口。')
        }
      } else if (owner.epoch !== epoch) return
      else if (active?.ready && node && !node.seeking && active.pendingSeek === undefined && monitor.ownsNode(active, node)) {
        void playWindow(active, node)
      } else if (!operation.formalArchiveOwner && (monitor.owns(active) || monitor.ownsJob(job))) {
        monitor.queuePlayback(operation)
      }
      return
    }
    if (owner.epoch !== epoch) return
    if (!canVerifyWindow()) { monitor.update({ message: '当前浏览器不能验证并播放临时媒体。' }); return }
    if (previewSession) {
      if (!monitor.automatic.play(frame)) permission.current.open = true
    } else if (consent && prepare) {
      void monitor.start({ ...binding, frame }, undefined, true)
    } else permission.current.open = true
  }
  useLayoutEffect(() => {
    windowTransport.current = toggle
    return () => { windowTransport.current = undefined }
  })

  async function playWindow(item, node, successor = false) {
    if (!monitor.ownsNode(item, node)) return
    if (monitor.playOperation === item.operationId) monitor.playOperation = undefined
    item.resume = false
    const attempt = item.resumeAttempt = {}
    monitor.refresh()
    try {
      await node.play()
      if (item.resumeAttempt !== attempt || !monitor.ownsNode(item, node)) return
      item.resumeAttempt = undefined
      if (!node.paused) observe(item, node)
      else monitor.refresh()
    } catch {
      if (item.resumeAttempt !== attempt || !monitor.ownsNode(item, node)) return
      monitor.stopContinuation(item)
      item.playDenied = true
      monitor.update({ ...monitor.state, message: successor ? '浏览器未自动播放下一窗；请手动播放。' : '浏览器未开始播放；请手动播放。' })
    }
  }
  useLayoutEffect(() => {
    const node = media?.node
    if (!ready || !monitor.ownsNode(media, node) || node.seeking || media.pendingSeek !== undefined) return
    if (media.resume) {
      // A natural handoff starts at the exact beginning, not a midpoint seek.
      onFrame(String(media.frame), media)
      if (media.resume) void playWindow(media, node, true)
    } else if (monitor.playOperation === media.operationId) {
      if (media.program && media.cursor !== frame) onFrame(String(media.cursor), media)
      void playWindow(media, node)
    }
  }, [monitor, media, state, ready, onFrame])

  function publish(item, node, time, decoded = false) {
    if (!monitor.ownsNode(item, node) || !item.ready || item.pendingSeek !== undefined) return
    const adjustment = decoded ? item.clock.den / (2 * item.clock.num) : 0
    const offset = frameFromSeconds(time + adjustment, item.fps)
    if (offset === null) return
    const next = item.frame + Math.min(item.end - item.frame - 1, offset)
    if (next !== item.cursor) { item.cursor = next; onFrame(String(next), item) }
  }
  function observe(item, node) {
    if (!monitor.ownsNode(item, node) || !item.ready) { node.pause(); return }
    if (node.paused) return // A queued play event cannot revive canceled playback.
    if (!item.resumeAttempt) monitor.played(item)
    if (item.callback !== undefined || typeof node.requestVideoFrameCallback !== 'function') return
    const requestObservation = () => {
      const id = node.requestVideoFrameCallback((_now, metadata) => {
        // The browser may already have queued this callback before a pin or
        // removal canceled it. Never consume a newer registration on reuse.
        if (item.callback !== id) return
        item.callback = undefined
        if (!monitor.ownsNode(item, node) || node.paused) return
        if (!node.seeking) publish(item, node, metadata.mediaTime, true)
        if (monitor.ownsNode(item, node) && !node.paused && item.callback === undefined) requestObservation()
      })
      item.callback = id
    }
    requestObservation()
  }
  const epoch = owner.epoch
  return h('section', { className: 'fv-rendered', 'aria-label': '单窗口音画监看' },
    h('div', { className: 'fv-program-stage', 'data-mode': 'window' },
      current ? [media, state.spare].filter(Boolean).map(item => {
        const active = item === media, visible = active && ready && !showFirstFrame
        return h('video', { key: item.url, ref: node => { if (node) item.node = node }, src: item.url,
          controls: !!visible && !waiting, muted: !active || !!item.initialPin, preload: 'auto', playsInline: true, disablePictureInPicture: true, disableRemotePlayback: true,
          'aria-label': active ? (archive ? `已核归档 A/V [${item.frame}, ${item.end})` : '仅当前窗口的原生音画') : undefined, 'aria-hidden': !visible,
          style: !visible ? { visibility: 'hidden', ...(active ? {} : { position: 'absolute' }) } : undefined,
          onLoadedData: event => monitor.ready(item, event.currentTarget),
          onProgress: event => { if (item.initialPin) monitor.ready(item, event.currentTarget) },
          onCanPlay: event => {
            const node = event.currentTarget
            monitor.playable(item, node, true)
            if (item.awaitPlayable || item.initialPin) monitor.ready(item, node)
            if (monitor.ownsMedia(item)) monitor.advance()
          },
          onWaiting: event => { if (!event.currentTarget.ended) monitor.playable(item, event.currentTarget, false) },
          onEmptied: event => monitor.playable(item, event.currentTarget, false),
          onError: () => monitor.rejectMedia(item, archive ? '浏览器无法解码归档 A/V。' : '浏览器无法解码窗口。'),
          onPlay: event => observe(item, event.currentTarget),
          onPause: event => {
            const node = event.currentTarget
            if (node.paused && !node.ended && monitor.ownsNode(item, node)) { monitor.stopContinuation(item); monitor.refresh() }
          },
          onSeeking: event => { const node = event.currentTarget; if (node.seeking && monitor.ownsNode(item, node)) monitor.stopContinuation(item) },
          onSeeked: event => {
            const node = event.currentTarget
            if (item.initialPin) { monitor.ready(item, node); return }
            if (!monitor.ownsNode(item, node) || node.seeking) return
            const requested = item.pendingSeek
            item.pendingSeek = undefined
            if (requested === undefined || Math.abs(node.currentTime - requested) > 1e-6) publish(item, node, node.currentTime)
            // Native seeking cancels the old lookahead. A seek that did not
            // pause the element may keep playing without another `play` event.
            if (item.program && !node.paused && !node.ended) observe(item, node)
            if (monitor.playOperation === item.operationId) monitor.refresh()
          },
          onTimeUpdate: event => { const node = event.currentTarget; if (!node.seeking && (node.paused || typeof node.requestVideoFrameCallback !== 'function')) publish(item, node, node.currentTime) },
          onEnded: event => {
            const node = event.currentTarget
            if (!monitor.ownsNode(item, node) || !node.ended || node.seeking || item.pendingSeek !== undefined) return
            publish(item, node, (item.end - item.frame - 1) * item.clock.den / item.clock.num, true)
            monitor.ended(item)
            monitor.refresh()
          },
        })
      }) : null,
      showFirstFrame ? h('img', { key: image.url, className: 'fv-window-first-frame', draggable: false, alt: '已验证的临时首帧（不可播放）',
        ref: node => {
          if (!node || !monitor.ownsFirstFrame(image)) return
          image.node = node
          if (node.getAttribute('src') !== image.url) node.src = image.url
        },
      }) : !ready ? h('p', { className: 'fv-program-empty' }, message ?? (archive ? '尚无可播归档 A/V。' : '尚无可播窗口。授权后可按空格播放。')) : null),
    h('div', { className: 'fv-monitor-footer' },
      !programBusy ? h(ToolButton, { icon: 'camera', variant: 'primary', label: '生成当前音画窗口', disabled: blocked || !consent || !prepare || !!occupied, onClick: generate }) : null,
      !programBusy && formalArchiveOwner ? h(ToolButton, { icon: 'file', label: '读取一次已核归档 A/V',
        tooltip: '仅读取当前精确点的已核归档 A/V；不渲染、不写项目、不自动预取。', disabled: !canReadArchive, onClick: readArchive }) : null,
      !programBusy ? h('details', { ref: permission, className: 'fv-monitor-disclosure fv-monitor-disclosure-up', name: disclosureGroup },
        h(ToolSummary, { icon: 'settings', label: '窗口预览授权' }),
        h('div', { className: 'fv-monitor-popover' },
          h('label', null, h('input', { type: 'checkbox', checked: consent, onChange: event => { if (!occupied?.automatic) monitor.interrupt(); setConsent(event.target.checked) } }), '允许写入 cache/preview（不修改时间线）'),
          h('label', null, h('input', { type: 'checkbox', checked: prepare, onChange: event => { if (!occupied?.automatic) monitor.interrupt(); setPrepare(event.target.checked) } }), '允许按需冷准备输入（不授权执行 HTML）'),
          h('p', null, '如无法生成完整音画窗口，直接拒绝，不丢轨。默认只在窗口内归一，响度不保证与成片一致；技术可播（包括整片音频基准模式）不等于正式 QC 通过。'),
          sessionControls ? h(AutoPreviewPermission, { controls: sessionControls, blocked: blocked || !!occupied || !canVerifyWindow() }) : null)) : null,
      waiting ? h('button', { type: 'button', onClick: () => monitor.stopContinuation(media) }, '暂停连续播放') : null,
      !programBusy && occupied ? h(ToolButton, { icon: 'cancel', label: archive ? (state.failed ? '重试回收归档读取' : '撤销已核归档 A/V') :
        state.failed ? '重试回收音画窗口' : '取消并回收音画窗口', disabled: !!job?.reaping && !media, onClick: () => { void monitor.cancel() } }) : null,
      h('span', { className: 'fv-monitor-status', role: 'status', title: archive && ready ? `${status} · ${scopeText}` : showFirstFrame ? status : scopeText }, status)))
}

export function useWindowMonitor(owner) {
  const monitor = owner.previewWindow ??= new WindowMonitor()
  return [monitor, React.useSyncExternalStore(monitor.subscribe, monitor.snapshot, monitor.snapshot)]
}

function CleanupStatus({ monitor, state }) {
  if (monitor.stopProgram?.failed) return h('div', { className: 'fv-monitor-footer', role: 'status' }, '上一整片预览尚未确认回收。',
    h('button', { type: 'button', onClick: () => { void monitor.stopProgram?.() } }, '重试回收后台任务'))
  if (!state.job?.controller.signal.aborted) return null
  return h('div', { className: 'fv-monitor-footer', role: 'status' }, state.message,
    state.failed ? h(ToolButton, { icon: 'cancel', label: state.job.formalArchiveOwner ? '重试回收归档读取' : '重试回收音画窗口',
      onClick: () => { void monitor.cancel() } }) : null)
}

// The workbench may hide grant-bound tools before their cleanup finishes.
export function WindowRecovery({ owner }) {
  const [monitor, state] = useWindowMonitor(owner)
  return h(CleanupStatus, { monitor, state })
}

export function ProgramMonitor({ owner, opened, timeline, selectionDisplay, sessionId, frame, blank = false, fps, duration, onFrame, selection, tabSignal, getDraftServices, call, transport, disclosureGroup, suspended = false, onGrantDead, previewSession, sessionControls, togglePreview }) {
  const windowTransport = useRef(), pendingStart = useRef()
  const canRender = opened.capabilities?.capabilities?.frame?.inline_png?.flag === '--include-png'
  const canWindow = supportsWindow(opened)
  const continuationOwner = supportsWindowContinuation(opened) ? opened.previewSession.ownerId : undefined
  const firstFrameOwner = supportsWindowFirstFrame(opened) ? opened.previewSession.ownerId : undefined
  const programAudioOwner = supportsWindowProgramAudio(opened) ? opened.previewSession.ownerId : undefined
  const programAudioContinuationOwner = supportsWindowProgramAudioContinuation(opened) ? opened.previewSession.ownerId : undefined
  const qualification = opened.previewSession
  const formalArchiveOwner = canWindow && opened.capabilities.capabilities.preview_window.formal_archive?.version === 1 &&
    qualification?.capability === 'preview-session' && qualification.version === 1 && qualification.formalArchive?.version === 1 &&
    typeof qualification.ownerId === 'string' && qualification.ownerId ? qualification.ownerId : undefined
  const [monitor, windowState] = useWindowMonitor(owner)
  const [chosen, setMode] = useState()
  const mode = chosen === 'window' && canWindow ? 'window' : chosen === 'rendered' || !canRender ? 'rendered' : 'live'
  const clock = projectClock(fps), contentVersion = opened.snapshot.content_version, grant = opened.grant
  const unavailable = suspended || !!(owner.ghost || owner.edit.dirty || owner.edit.busy || owner.edit.unknown) || !clock ||
    !Number.isSafeInteger(duration) || !versionToken.test(contentVersion)
  const blocked = unavailable || !Number.isSafeInteger(frame) || frame < 0 || frame >= duration
  const programDisclosure = useRef(), localProgram = useRef()
  useLayoutEffect(() => () => { void localProgram.current?.() }, [grant, contentVersion, tabSignal])
  const program = usePreviewProgramRun({ opened, sessionId, project: timeline, tabSignal, call, suspended: unavailable })
  const running = !!program?.signal && !program.signal.aborted
  useLayoutEffect(() => {
    if (program?.signal?.aborted && !program.cleanupFailed && !program.cleanupPending) void localProgram.current?.()
  }, [program?.signal?.aborted, program?.cleanupFailed, program?.cleanupPending])
  const orphanProgram = monitor.stopProgram && monitor.stopProgram !== localProgram.current
  const programBusy = running || orphanProgram || program?.cleanupPending || program?.cleanupFailed
  const otherLease = !!(sessionControls?.needsRevoke || sessionControls?.pending || previewSession)
  useLayoutEffect(() => {
    if (!running || unavailable || mode !== 'window') return
    return connectProgramPlayback(monitor, owner, { project: timeline, sessionId, grant, contentVersion, duration, fps, clock, tabSignal }, program, blank ? 0 : frame)
  }, [monitor, owner, program?.signal, unavailable, mode, timeline, sessionId, grant, contentVersion, duration, fps, tabSignal])
  function startProgram() {
    if (!program || monitor.stopProgram || programBusy || otherLease || unavailable || (blocked && !blank) || windowState.job || windowState.active || tabSignal.aborted) return
    change('window'); programDisclosure.current.open = false
    // Return focus from the now-hidden consent button to the editing surface:
    // the very next Space should pause, not scroll the page or reopen settings.
    programDisclosure.current.closest('.fv-monitor')?.focus({ preventScroll: true })
    // Retain the cleanup fence even if invalidation unmounts the player before pointer-up.
    const stop = async () => {
      const ok = await program.cancel().catch(() => false)
      stop.failed = !ok
      if (ok && monitor.stopProgram === stop) monitor.stopProgram = undefined
      if (ok && localProgram.current === stop) localProgram.current = undefined
      monitor.refresh()
      return ok
    }
    monitor.stopProgram = localProgram.current = stop
    void program.start(blank ? 0 : frame)
  }
  // PreviewBody mounts this only for a verified saved version (including the
  // commit/undo readback). Subsequent cursor intent arrives via FrameEpoch.pin,
  // not a frame dependency: decoder observations update the same React frame.
  useLayoutEffect(() => {
    if (mode !== 'window' || unavailable || !previewSession || !canVerifyWindow()) return
    return monitor.automatic.connect({ project: timeline, sessionId, grant, contentVersion, duration, fps, clock, tabSignal,
      continuationOwner, firstFrameOwner, programAudioOwner, programAudioContinuationOwner }, previewSession, frame)
  }, [monitor, mode, unavailable, previewSession, timeline, sessionId, grant, contentVersion, duration, fps, tabSignal,
    continuationOwner, firstFrameOwner, programAudioOwner, programAudioContinuationOwner])
  function change(next) {
    if (next === mode) return
    pendingStart.current = undefined
    if (programBusy) void monitor.stopProgram?.()
    owner.invalidate()
    setMode(next)
    if (blank) onFrame('0')
  }
  function togglePlayback() {
    if (unavailable || tabSignal.aborted || (blocked && !blank)) return
    if (owner.programTransport?.(blank ? 0 : frame)) return
    if (!canWindow) return
    if (program?.cleanupPending || program?.cleanupFailed || (running && !monitor.state.active?.program)) return
    if (program && !otherLease && !running && mode !== 'window') {
      programDisclosure.current.open = true
      return
    }
    if (mode === 'window' && !blank) windowTransport.current?.()
    else {
      // Mount the verified-window stage (and connect any existing authorization)
      // before handing off one deliberate start. Never carry it past a new pin.
      if (mode !== 'window') change('window')
      else onFrame('0')
      pendingStart.current = { epoch: owner.epoch, frame: blank ? 0 : frame }
    }
  }
  useLayoutEffect(() => {
    if (togglePreview) togglePreview.current = canWindow ? togglePlayback : undefined
    const intent = pendingStart.current
    pendingStart.current = undefined
    if (intent && mode === 'window' && !blocked && !tabSignal.aborted && intent.frame === frame && intent.epoch === owner.epoch) {
      windowTransport.current?.()
    }
    return () => { if (togglePreview) togglePreview.current = undefined }
  })
  const active = windowState.active
  const pending = monitor.program?.wantsPlayback || monitor.playOperation !== undefined || active?.resumeAttempt
  const playing = active?.waiting || active?.node?.paused === false
  return h('div', { className: 'fv-program-monitor' },
    h('div', { className: 'fv-monitor-toolbar' }, transport,
      canWindow ? h('button', { type: 'button', onClick: togglePlayback, disabled: unavailable || (blocked && !blank),
        'aria-keyshortcuts': 'Space', title: '空格：播放或暂停音画预览' }, pending ? '取消待播放' : playing ? '暂停预览' : '播放预览') : null,
      program ? h('details', { ref: programDisclosure, name: disclosureGroup, className: 'fv-monitor-disclosure' },
        h('summary', null, '整片后台预览'), h('div', { className: 'fv-monitor-popover' },
          h('p', null, '从播放头开始，再补齐前段。允许本次会话按需冷准备输入、写预览工作区，并把捕获的音画保存在此 DSH 站点的浏览器缓存中（最多 512 MiB）。停止时删除；若浏览器崩溃，数据可能保留到下次开启。不会修改时间线或授权执行 HTML。暂停不停止后台任务；外部素材替换后请重新开始。'),
          programBusy ? h('button', { type: 'button', disabled: program.cleanupPending, onClick: () => { void monitor.stopProgram?.() } }, program.cleanupFailed ? '重试回收后台任务' : '停止整片预览') :
            h('button', { type: 'button', disabled: unavailable || (blocked && !blank) || otherLease || !!(windowState.job || windowState.active), onClick: startProgram }, '允许并开始整片预览'))) : null,
      h('div', { className: 'fv-preview-modes', role: 'group', 'aria-label': '预览模式' },
        canRender ? h(ToolButton, { icon: 'diagnostic', label: '固定帧预览', tooltip: '当前完整合成帧 · PNG，无声，不是视频播放', 'aria-pressed': mode === 'live', onClick: () => change('live') }) : null,
        canWindow ? h(ToolButton, { icon: 'camera', label: '音画窗口', tooltip: WINDOW_SCOPE, 'aria-pressed': mode === 'window', onClick: () => change('window') }) : null,
        h(ToolButton, { icon: 'film', label: '成片渲染', tooltip: '成片渲染 · 播放已有成片，不启动渲染', 'aria-pressed': mode === 'rendered', onClick: () => change('rendered') })),
      mode !== 'window' && sessionControls?.needsRevoke ? h(ToolButton, { icon: 'cancel', disabled: sessionControls.pending,
        label: sessionControls.failed ? '重试撤销自动预览授权' : '停止并撤销自动预览', onClick: sessionControls.revoke }) : null),
    program && program.status !== 'idle' ? h('p', { role: 'status', className: 'fv-monitor-status' },
      `整片后台预览：${({ authorizing: '正在申请授权', running: '渲染中', complete: '本版窗口已备齐', partial: '未完成', cancelled: '已停止', error: '无法继续' })[program.status] ?? '未就绪'}${program.pendingWindow ? ` · 正在准备第 ${program.pendingWindow.start}–${program.pendingWindow.end - 1} 帧` : ''} · 已缓存 ${program.completedFrames}/${program.totalFrames} 帧${program.code ? ` · ${program.code}` : ''}`) : null,
    canRender ? h(FrameTools, { key: contentVersion, owner, opened: timeline ? { ...opened, timeline } : opened, selectionDisplay, sessionId, frame, selection, tabSignal, getDraftServices, call, disclosureGroup, active: mode === 'live', suspended: suspended || !!(windowState.job || windowState.active) }) : null,
    mode === 'window' ? h(WindowPlayer, { key: `${sessionId}:${opened.grant}:${contentVersion}`, monitor, state: windowState, owner, binding: { project: timeline, sessionId, grant, contentVersion, duration, fps, clock, tabSignal, onGrantDead }, frame, blocked, onFrame, disclosureGroup, sessionControls, previewSession, formalArchiveOwner, windowTransport, programBusy }) : null,
    !canRender && !canWindow ? h('p', { className: 'fv-monitor-status' }, '当前宿主或内核不支持生成监看预览；仍可查看时间线和已有成片。') : null,
    mode === 'rendered' ? h(RenderedPlayer, { opened, sessionId, frame, fps, duration, onFrame, disclosureGroup }) : null,
    mode !== 'window' ? h(CleanupStatus, { monitor, state: windowState }) : null)
}
