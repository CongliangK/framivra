import React, { useEffect, useRef } from 'react'
import { Icon } from './workbench.mjs'
import { safeFrame } from './viewport.mjs'

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

export function Transport({ frame, frameText, frameError, duration, onFrame, onText, onQuery }) {
  const validDuration = safeFrame(duration) && duration > 0
  const move = (direction, distance = 1) => { const next = stepFrame(frame, duration, direction, distance); if (next !== undefined) onFrame(String(next)) }
  return h('form', { className: 'fv-frame-query fv-transport', 'aria-label': '帧定位控制', onSubmit: event => { event.preventDefault(); onQuery() } },
    h('div', { className: 'fv-transport-buttons', role: 'group', 'aria-label': '播放头定位（不自动渲染）' },
      h('button', { type: 'button', title: '定位首帧 · Home', 'aria-label': '定位首帧', disabled: !validDuration, onClick: () => onFrame('0') }, h(Icon, { name: 'first', size: 16 })),
      h('button', { type: 'button', title: '前一帧 · ←', 'aria-label': '监视器前一帧', disabled: !validDuration || frame === 0, onClick: () => move(-1) }, h(Icon, { name: 'previous', size: 16 })),
      h('label', { className: 'fv-frame-input' }, h('span', null, '帧（留空为全部） '), h('input', { type: 'text', name: 'framivra-frame', autoComplete: 'off', spellCheck: false, inputMode: 'numeric', value: frameText, 'aria-invalid': Boolean(frameError), onChange: event => onText(event.target.value) })),
      h('button', { type: 'button', title: '后一帧 · →', 'aria-label': '监视器后一帧', disabled: !validDuration || frame === duration - 1, onClick: () => move(1) }, h(Icon, { name: 'next', size: 16 })),
      h('button', { type: 'button', title: '定位末帧 · End', 'aria-label': '定位末帧', disabled: !validDuration, onClick: () => onFrame(String(duration - 1)) }, h(Icon, { name: 'last', size: 16 }))),
    h('span', { className: 'fv-duration', title: '工程整数帧范围，不表示已渲染时长' }, validDuration ? `/ ${duration} 帧` : '时长不可显示'),
    h('button', { type: 'submit', className: 'fv-query-button', title: '查询该帧的内核命中集合，不生成画面' }, '查询命中'))
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
export function SelectionSummary({ selection, selected = [], result, frame, onFrame, onClear, onProperties, quick }) {
  const item = selection?.kind === 'block' ? result.blocks.find(block => block.address === selection.address) : selection?.kind === 'track' ? result.tracks.find(track => track.address === selection.address) : undefined
  const blocks = result.blocks.filter(block => selected.some(value => value.kind === 'block' && value.address === block.address))
  const family = item?.family === 'video' && (item.data?.kind === 'gfx' || result.tracks.find(track => track.family === item.family && track.track_index === item.track_index)?.data?.kind === 'gfx') ? 'gfx' : item?.family
  const range = selection?.kind === 'block' && item?.range?.length === 2 && item.range.every(safeFrame) && item.range[1] > item.range[0] ? item.range : undefined
  return h('div', { className: 'fv-selection-bar', 'aria-label': '选区剪辑工具' },
    h('div', { className: 'fv-selection-description' },
      h('span', { className: 'fv-selection-kind', 'data-family': family }, h(Icon, { name: family === 'audio' ? 'audio' : family === 'subtitles' ? 'text' : 'clip', size: 16 })),
      h('div', null, h('strong', { title: item ? clipName(item.data, item.address) : undefined }, item ? clipName(item.data, item.address) : '选择时间线中的片段'),
        h('span', null, item ? `${familyName(family)}${selected.length > 1 ? ` · 已选 ${selected.length} 项` : ''}${range ? ` · ${range[0]}–${range[1]} · ${range[1] - range[0]} 帧` : ' · 轨道'}` : '点击选择 · Ctrl/⌘ 多选 · 拖动标尺定位'))),
    h('div', { className: 'fv-selection-actions', role: 'group', 'aria-label': '片段操作（仅加入待预检批次）' },
      h('button', { type: 'button', title: '定位片段起点，不生成画面', 'aria-label': '定位选中片段起点', disabled: !range, onClick: () => onFrame(String(range[0])) }, h(Icon, { name: 'pin', size: 15 }), h('span', null, '定位')),
      h('button', { type: 'button', title: quick ? '分割选中块，加入未保存批次 · S' : '请先在属性面板明确授予工程写许可；不自动接管 legacy', 'aria-label': '在播放头分割选中片段', disabled: !quick || blocks.length !== 1 || !range || family === 'gfx' || !safeFrame(frame) || frame <= range[0] || frame >= range[1], onClick: () => quick('block.split') }, h(Icon, { name: 'split', size: 16 }), h('span', null, '分割')),
      h('button', { type: 'button', title: quick ? '移除选中块，加入未保存批次 · Delete' : '请先在属性面板明确授予工程写许可', 'aria-label': '将移除选中片段加入批次', disabled: !quick || !blocks.length, onClick: () => quick('block.remove') }, h(Icon, { name: 'remove', size: 16 }), h('span', null, '移除')),
      h('button', { type: 'button', 'aria-label': '查看选区属性与编辑', onClick: onProperties }, h(Icon, { name: 'settings', size: 16 }), h('span', null, '属性')),
      h('button', { type: 'button', title: '清除选区，不改片段', 'aria-label': '清除时间线选区', disabled: !selected.length, onClick: onClear }, h(Icon, { name: 'close', size: 15 }))))
}
