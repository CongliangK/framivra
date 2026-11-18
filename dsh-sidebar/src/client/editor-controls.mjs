import React, { useEffect, useId, useRef, useState } from 'react'
import { Icon } from './icons.mjs'
import { ToolButton, ToolSummary } from './icon-controls.mjs'
import { safeFrame } from './viewport.mjs'
import { projectClock, formatTimecode, parseTimecode } from './timecode.mjs'

const h = React.createElement
export const clipName = (data, fallback = '片段') => typeof data?.name === 'string' ? data.name : typeof data?.text === 'string' ? data.text : typeof data?.src === 'string' ? data.src : fallback
export const familyName = family => ({ video: '视频', gfx: '图形', audio: '音频', subtitles: '字幕' })[family] ?? family

/** Navigation only: no query, rendering, source-time inference, or write action. */
export function stepFrame(frame, duration, direction, distance = 1) {
  if (!safeFrame(duration) || duration < 1 || !Number.isSafeInteger(distance) || distance < 1) return undefined
  const current = safeFrame(frame) && frame < duration ? frame : 0
  // Bound the addition before doing it; duration can be MAX_SAFE_INTEGER.
  return direction < 0 ? Math.max(0, current - distance) : current >= duration - distance ? duration - 1 : current + distance
}
export function editingTarget(target) {
  return !!target?.closest?.('input,textarea,select,[contenteditable]:not([contenteditable="false"]),[role="textbox"],[role="combobox"]')
}

// Inspect exact core tokens; never coerce a rawJSON object through Number().
const integerProjectFps = value => { const clock = projectClock(value); return clock?.den === 1 ? clock.num : undefined }

/** Integer clocks only: a fractional second has no fixed integer frame offset. */
export function secondsFrame(seconds, offset, fps = 30) {
  fps = integerProjectFps(fps)
  if (!Number.isSafeInteger(fps) || fps < 1) throw new Error('非整数帧率请直接使用全局整数帧定位。')
  const canonical = value => typeof value === 'string' && /^(0|[1-9][0-9]*)$/.test(value) && value.length <= 16
  if (!canonical(seconds) || !canonical(offset)) throw new Error(`请输入规范非负整数秒与秒内帧偏移（0–${fps - 1}）。`)
  const value = BigInt(seconds) * BigInt(fps) + BigInt(offset)
  if (BigInt(offset) >= BigInt(fps) || value > BigInt(Number.MAX_SAFE_INTEGER)) throw new Error(`秒内偏移须为 0–${fps - 1}，换算结果须在安全整数范围内。`)
  return Number(value)
}

export function Transport({ frameText, frameError, duration, fps, onFrame, onText, onQuery, disclosureGroup }) {
  const clock = projectClock(fps), integerFps = integerProjectFps(fps)
  // Invalid timecode lives in canonical text as an explicitly noncanonical token.
  // The parent's existing lexical gate synchronously suspends all frame references;
  // deriving the display from that same text preserves corrections across renders.
  const invalidTimecode = typeof frameText === 'string' && frameText.startsWith('timecode:')
  const canonicalFrame = typeof frameText === 'string' && /^(0|[1-9][0-9]*)$/.test(frameText) && frameText.length <= 16 && safeFrame(Number(frameText)) ? Number(frameText) : undefined
  const timecode = invalidTimecode ? frameText.slice(9) : frameText === '' ? '' : formatTimecode(canonicalFrame, fps) ?? ''
  const changeTimecode = text => {
    const value = parseTimecode(text, fps)
    onText(text === '' ? '' : value === null ? `timecode:${text}` : String(value))
  }
  const [seconds, setSeconds] = useState('0'), [offset, setOffset] = useState('0'), [timeError, setTimeError] = useState('')
  let converted
  try { if (integerFps !== undefined) converted = secondsFrame(seconds, offset, integerFps) } catch { /* render a correction hint after explicit conversion */ }
  const validDuration = safeFrame(duration) && duration > 0
  const errorId = useId()
  const error = invalidTimecode ? '时间码无效：请输入规范 mm:ss:frame；保留原文供纠错，引用已暂停。' : frameError ? `${frameError} 引用已暂停；不会暗用旧帧。` : undefined
  return h('form', { className: 'fv-frame-query fv-transport', 'aria-label': '帧定位控制', onSubmit: event => { event.preventDefault(); onQuery() } },
    h('label', { className: 'fv-frame-input' }, h('span', { className: 'fv-sr-only' }, '时间码（留空为全部） '), h('input', { type: 'text', name: 'framivra-timecode', autoComplete: 'off', spellCheck: false, value: timecode, title: '时间码（留空为全部） · mm:ss:frame', placeholder: clock ? 'mm:ss:frame' : '帧率未知，时间码不可显示', disabled: !clock, 'aria-invalid': Boolean(error), 'aria-describedby': error ? errorId : undefined, onChange: event => changeTimecode(event.target.value) })),
    error ? h('span', { className: 'fv-transport-error', title: error }, h('span', { 'aria-hidden': true }, '!'), h('span', { id: errorId, role: 'alert', className: 'fv-sr-only' }, error)) : null,
    h('span', { className: 'fv-duration', title: '工程存储帧范围的时间码，不表示已渲染时长' }, validDuration && formatTimecode(duration, fps) !== null ? `/ ${formatTimecode(duration, fps)}` : '时长不可显示'),
    h(ToolButton, { type: 'submit', icon: 'query', label: '查询命中', className: 'fv-query-button', tooltip: '查询该帧的内核命中集合，不生成画面' }),
    h('details', { className: 'fv-monitor-disclosure', name: disclosureGroup },
      h(ToolSummary, { icon: 'settings', label: '高级帧定位', tooltip: '全局整数帧与按秒 + 帧定位（不渲染）' }),
      h('div', { className: 'fv-monitor-popover' },
      h('label', null, '帧（留空为全部） ', h('input', { type: 'text', name: 'framivra-frame', autoComplete: 'off', spellCheck: false, inputMode: 'numeric', value: frameText, 'aria-invalid': Boolean(error), 'aria-describedby': error ? errorId : undefined, onChange: event => onText(event.target.value) })),
      clock?.den === 1001 ? h('p', null, `NTSC non-drop · 名义 ${clock.nominal} fps（不是墙钟）`) : null,
    Number.isSafeInteger(integerFps) ? h('div', { className: 'fv-seconds-helper' }, h('p', null, `按秒 + 帧定位（${integerFps} fps）`),
      h('label', null, '整数秒 ', h('input', { type: 'text', name: 'framivra-seconds', inputMode: 'numeric', value: seconds, onChange: event => { setSeconds(event.target.value); setTimeError('') } })),
      h('label', null, `秒内帧偏移（0–${integerFps - 1}，零基） `, h('input', { type: 'text', name: 'framivra-offset', inputMode: 'numeric', value: offset, onChange: event => { setOffset(event.target.value); setTimeError('') } })),
      h('span', null, converted === undefined ? '待修正输入' : ` → 全局帧 ${converted}`),
      h('button', { type: 'button', onClick: () => { try { const next = secondsFrame(seconds, offset, integerFps); onFrame(String(next)); setTimeError('') } catch (error) { setTimeError(error.message) } } }, '换算定位（不渲染）'),
      h('p', null, `偏移 0 为该秒首帧；10 秒 + 偏移 5 = 全局帧 ${10 * integerFps + 5}。范围由内核检查。`),
      timeError ? h('p', { role: 'alert' }, timeError) : null) : h('p', null, '非整数或未知帧率：秒内帧偏移换算不可用；可使用全局整数帧定位。'))))
}

/** Local layout only. One captured pointer owns resizing; blur/cancel always retire it. */
export function LayoutDivider({ value, onChange }) {
  const drag = useRef()
  function finish(event) {
    const current = drag.current
    if (!current || (event?.pointerId !== undefined && event.pointerId !== current.pointer)) return
    drag.current = undefined
    try { if (current.node.hasPointerCapture?.(current.pointer)) current.node.releasePointerCapture(current.pointer) } catch { /* detached/capture already lost */ }
  }
  useEffect(() => {
    const blur = () => finish()
    globalThis.window?.addEventListener('blur', blur)
    return () => { globalThis.window?.removeEventListener('blur', blur); finish() }
  }, [])
  function move(event) {
    const current = drag.current
    if (!current || event.pointerId !== current.pointer) return
    const percent = Math.round((current.bottom - event.clientY) * 100 / current.height)
    if (Number.isFinite(percent)) onChange(Math.max(26, Math.min(65, percent)))
  }
  return h('div', { className: 'fv-layout-divider', role: 'separator', tabIndex: 0, 'aria-orientation': 'horizontal', 'aria-label': '调整监视器与时间线高度', 'aria-valuemin': 26, 'aria-valuemax': 65, 'aria-valuenow': value, 'aria-valuetext': `时间线占 ${value}%`, title: '拖动调整布局 · ↑/↓ 调整 · Home 或双击复位',
    onDoubleClick: () => onChange(38),
    onKeyDown: event => {
      if (!['ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) return
      event.preventDefault(); event.stopPropagation()
      onChange(event.key === 'Home' ? 38 : event.key === 'End' ? 65 : Math.max(26, Math.min(65, value + (event.key === 'ArrowUp' ? 5 : -5))))
    },
    onPointerDown: event => {
      if (event.button !== 0 || drag.current) return
      const box = event.currentTarget.parentElement.getBoundingClientRect()
      if (!Number.isFinite(box.height) || box.height <= 0) return
      event.preventDefault()
      drag.current = { pointer: event.pointerId, node: event.currentTarget, bottom: box.bottom, height: box.height }
      event.currentTarget.setPointerCapture?.(event.pointerId)
    }, onPointerMove: move, onPointerUp: event => { move(event); finish(event) }, onPointerCancel: finish, onLostPointerCapture: finish,
  }, h(Icon, { name: 'grip', size: 14 }))
}

/** Selected values come exclusively from the saved kernel projection. */
export function SelectionSummary({ selection, selected = [], result, frame, fps, onFrame, onClear, onProperties, quick }) {
  const item = selection?.kind === 'block' ? result.blocks.find(block => block.address === selection.address) : selection?.kind === 'track' ? result.tracks.find(track => track.address === selection.address) : undefined
  const blocks = result.blocks.filter(block => selected.some(value => value.kind === 'block' && value.address === block.address))
  const family = item?.family === 'video' && (item.data?.kind === 'gfx' || result.tracks.find(track => track.family === item.family && track.track_index === item.track_index)?.data?.kind === 'gfx') ? 'gfx' : item?.family
  const range = selection?.kind === 'block' && item?.range?.length === 2 && item.range.every(safeFrame) && item.range[1] > item.range[0] ? item.range : undefined
  const time = value => formatTimecode(value, fps) ?? '时间码不可显示'
  const description = item ? `${familyName(family)}${selected.length > 1 ? ` · 已选 ${selected.length} 项` : ''}${range ? ` · ${time(range[0])}–${time(range[1])} · 长度 ${time(range[1] - range[0])}` : ' · 轨道'}` : '点击片段选择 · Ctrl/⌘ 多选 · 拖动标尺定位'
  return h('div', { className: 'fv-selection-bar', 'aria-label': '选区剪辑工具' },
    item ? h('div', { className: 'fv-selection-description', title: `${clipName(item.data, item.address)} · ${description}` },
      h('span', { className: 'fv-selection-kind', 'data-family': family }, h(Icon, { name: family === 'audio' ? 'audio' : family === 'subtitles' ? 'text' : 'clip', size: 14 })),
      h('strong', null, clipName(item.data, item.address)), h('span', { className: 'fv-sr-only' }, description)) : null,
    h('div', { className: 'fv-selection-actions', role: 'group', 'aria-label': '片段操作（仅加入待预检批次）' },
      h(ToolButton, { icon: 'pin', label: '定位选中片段起点', tooltip: '定位片段起点，不生成画面', disabled: !range, onClick: () => onFrame(String(range[0])) }),
      h(ToolButton, { icon: 'split', label: '在播放头分割选中片段', tooltip: quick ? '分割选中块，加入未保存批次 · S' : '请先在属性面板明确授予工程写许可；不自动接管 legacy', disabled: !quick || blocks.length !== 1 || !range || family === 'gfx' || !safeFrame(frame) || frame <= range[0] || frame >= range[1], onClick: () => quick('block.split') }),
      h(ToolButton, { icon: 'remove', label: '将移除选中片段加入批次', tooltip: quick ? '移除选中块，加入未保存批次 · Delete' : '请先在属性面板明确授予工程写许可', variant: 'danger', disabled: !quick || !blocks.length, onClick: () => quick('block.remove') }),
      h(ToolButton, { icon: 'settings', label: '查看选区属性与编辑', onClick: onProperties }),
      h(ToolButton, { icon: 'close', label: '清除时间线选区', tooltip: '清除选区，不改片段', disabled: !selected.length, onClick: onClear })))
}
