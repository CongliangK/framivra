import React, { useLayoutEffect, useEffect, useRef, useState } from 'react'
import { FrameTools } from './frame.mjs'
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

export function ProgramMonitor({ owner, opened, timeline, selectionDisplay, sessionId, frame, blank = false, fps, duration, onFrame, selection, tabSignal, getDraftServices, call, transport, disclosureGroup, suspended = false }) {
  const canRender = opened.capabilities?.capabilities?.frame?.inline_png?.flag === '--include-png'
  const [mode, setMode] = useState('live')
  function change(next) {
    if (next === mode) return
    owner.invalidate()
    setMode(next)
    if (blank) onFrame('0')
  }
  return h('div', { className: 'fv-program-monitor' },
    h('div', { className: 'fv-monitor-toolbar' }, transport,
      h('div', { className: 'fv-preview-modes', role: 'group', 'aria-label': '预览模式' },
        canRender ? h(ToolButton, { icon: 'diagnostic', label: '实时真预览', tooltip: '实时真预览 · 当前时间线的完整合成帧', 'aria-pressed': mode === 'live', onClick: () => change('live') }) : null,
        h(ToolButton, { icon: 'film', label: '成片渲染', tooltip: '成片渲染 · 播放已有成片，不启动渲染', 'aria-pressed': !canRender || mode === 'rendered', onClick: () => change('rendered') }))),
    canRender ? h(FrameTools, { key: opened.snapshot.content_version, owner, opened: timeline ? { ...opened, timeline } : opened, selectionDisplay, sessionId, frame, selection, tabSignal, getDraftServices, call, disclosureGroup, active: mode === 'live', suspended }) : h('p', { className: 'fv-monitor-status' }, '当前宿主或内核不支持生成完整合成帧；仍可查看时间线和已有成片。'),
    !canRender || mode === 'rendered' ? h(RenderedPlayer, { opened, sessionId, frame, fps, duration, onFrame, disclosureGroup }) : null)
}
