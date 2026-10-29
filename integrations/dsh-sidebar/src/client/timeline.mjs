import React, { useEffect, useRef, useState } from 'react'
import { displayExtent, windowFor, zoomWindow, panWindow, framePercent, clipGeometry, pointerFrame, frameTicks, displayLanes, safeFrame } from './viewport.mjs'

const h = React.createElement
// Kept in the JS factory: no stylesheet asset or private DSH theme dependency.
const css = `
.fv-timeline{min-width:0;max-width:100%;font:inherit;color:inherit;overflow-wrap:anywhere}
.fv-timeline *{box-sizing:border-box}.fv-timeline button,.fv-timeline input{font:inherit;color:inherit;max-width:100%}
.fv-timeline button{border:1px solid currentColor;border-radius:4px;background:transparent;padding:4px 7px;cursor:pointer}
.fv-timeline button:disabled{opacity:.45;cursor:default}.fv-timeline :focus-visible{outline:2px solid #579deb;outline-offset:2px}
.fv-timeline .fv-tools{display:flex;flex-wrap:wrap;gap:5px;align-items:center;margin:6px 0}
.fv-timeline .fv-scroll{max-height:440px;overflow:auto;border:1px solid currentColor;border-radius:5px;padding:4px;min-width:0}
.fv-timeline .fv-ruler{height:54px;position:relative;touch-action:none;cursor:crosshair;border-bottom:1px solid currentColor;margin:0 8px;overflow:hidden}
.fv-timeline .fv-tick{position:absolute;font-size:11px;top:1px;white-space:nowrap;border-left:1px solid currentColor;padding-left:3px}
.fv-timeline .fv-track{margin:8px 0;border-bottom:1px solid color-mix(in srgb,currentColor 30%,transparent);padding-bottom:8px}
.fv-timeline .fv-track-title{display:block;width:100%;text-align:left;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.fv-timeline .fv-row{position:relative;height:36px;margin:4px 8px;background:color-mix(in srgb,currentColor 7%,transparent)}
.fv-timeline .fv-clip{position:absolute;top:3px;height:30px;min-width:3px;padding:2px!important;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;text-align:left;background:color-mix(in srgb,#579deb 24%,transparent)}
.fv-timeline .fv-audio{background:color-mix(in srgb,#46bb89 24%,transparent)}.fv-timeline .fv-subtitles{background:color-mix(in srgb,#e1ae50 24%,transparent)}
.fv-timeline .fv-gfx{background:color-mix(in srgb,#bb81e8 24%,transparent)}
.fv-timeline [aria-pressed=true]{outline:2px solid #579deb;outline-offset:0}
.fv-timeline [data-hit=true]{box-shadow:inset 0 -4px #e1ae50}
.fv-timeline .fv-line{position:absolute;top:0;bottom:0;border-left:2px solid #ee7272;pointer-events:none;z-index:2}
.fv-timeline .fv-hover{border-left:1px dashed currentColor}
.fv-timeline .fv-status{font-variant-numeric:tabular-nums;margin:5px 0}
`
const label = (data, fallback) => typeof data?.name === 'string' ? data.name : typeof data?.text === 'string' ? data.text : typeof data?.src === 'string' ? data.src : fallback

export function VisualTimeline({ snapshot, result, hits, frame, onFrame, onSelect, activeSelection }) {
  const extent = displayExtent(snapshot, result)
  const [window, setWindow] = useState()
  const [hover, setHover] = useState()
  const [selected, setSelected] = useState([])
  const drag = useRef()
  const ruler = useRef()
  useEffect(() => { setWindow(undefined); setHover(undefined); setSelected([]); drag.current = undefined }, [snapshot.content_version])
  useEffect(() => { if (!activeSelection) setSelected([]) }, [activeSelection])
  if (extent.diagnostic) return h('p', { role: 'alert' }, extent.diagnostic)
  const view = window ? windowFor(extent.end, window.start, window.end - window.start) : extent
  const pinned = safeFrame(frame) ? frame : undefined
  const hitSet = hits ? new Set(hits.blocks.map(block => block.address)) : undefined
  const groups = new Map()
  for (const block of result.blocks) {
    const key = `${block.family}:${block.track_index}`
    if (!groups.has(key)) groups.set(key, [])
    groups.get(key).push(block)
  }
  function pick(item, event) {
    const id = `${item.kind}:${item.address}`
    setSelected(previous => event.ctrlKey || event.metaKey ? previous.includes(id) ? previous.filter(value => value !== id) : previous.concat(id) : [id])
    onSelect?.(item)
  }
  function move(event, pin = false) {
    const value = pointerFrame(event.clientX, ruler.current.getBoundingClientRect(), view)
    if (value === undefined) return
    setHover(value)
    if (pin || drag.current === event.pointerId) onFrame?.(value)
  }
  function step(direction) {
    const start = pinned ?? view.start
    const next = direction < 0 ? Math.max(0, start - 1) : start >= extent.end - 1 ? extent.end - 1 : start + 1
    onFrame?.(next)
  }
  const lines = () => [
    pinned !== undefined && pinned >= view.start && pinned < view.end ? h('span', { key: 'pin', className: 'fv-line', style: { left: `${framePercent(pinned, view)}%` }, 'aria-hidden': true }) : null,
    hover !== undefined && hover >= view.start && hover < view.end ? h('span', { key: 'hover', className: 'fv-line fv-hover', style: { left: `${framePercent(hover, view)}%` }, 'aria-hidden': true }) : null,
  ]
  return h('section', { className: 'fv-timeline', 'aria-label': '完整只读可视时间轴' },
    h('style', null, css),
    h('h3', null, label(snapshot.document?.project, '时间线'), ' · 只读'),
    h('p', null, `全部 ${result.tracks.length} 轨 / ${result.blocks.length} 块 · video / gfx / audio / subtitles；仅映射 kernel range，不执行编辑或渲染。`),
    h('div', { className: 'fv-tools', role: 'group', 'aria-label': '显示范围' },
      h('button', { type: 'button', onClick: () => setWindow(undefined) }, '适配全片'),
      h('button', { type: 'button', disabled: view.end - view.start <= 1, onClick: () => setWindow(zoomWindow(view, extent.end, true)) }, '放大'),
      h('button', { type: 'button', disabled: view.end - view.start >= extent.end, onClick: () => setWindow(zoomWindow(view, extent.end, false)) }, '缩小'),
      h('button', { type: 'button', disabled: view.start === 0, onClick: () => setWindow(panWindow(view, extent.end, -1)) }, '向前平移'),
      h('button', { type: 'button', disabled: view.end === extent.end, onClick: () => setWindow(panWindow(view, extent.end, 1)) }, '向后平移')),
    h('p', { className: 'fv-status', 'aria-label': '可见帧范围' }, `帧窗口 [${view.start}, ${view.end}) / 全片 [0, ${extent.end})`),
    h('p', { className: 'fv-status', role: 'status' }, `固定播放头：${pinned ?? '未设置（空白或无效输入）'}；悬停帧：${hover ?? '—'}（不查询、不渲染）。`),
    h('div', { className: 'fv-tools' },
      h('button', { type: 'button', disabled: !onFrame || pinned === undefined, onClick: () => step(-1) }, '前一帧'),
      h('button', { type: 'button', disabled: !onFrame || pinned === undefined, onClick: () => step(1) }, '后一帧')),
    h('p', null, '帧标尺：点击/拖动固定播放头；聚焦标尺用 ←/→、Home/End。像素仅概览（1/1024 CSS px 采样），高帧/细块请放大或用整数输入精确定位。Ctrl/⌘ 点击可保留浏览多选；引用始终只有最后激活的一项。'),
    h('p', { 'aria-label': '活动引用' }, `浏览多选 ${selected.length} 项；活动引用：${activeSelection ? `${activeSelection.kind} ${activeSelection.address}` : '无'}（不支持批量写入/引用）`),
    h('div', { className: 'fv-scroll', tabIndex: 0, role: 'region', 'aria-label': '轨道垂直滚动区' },
      h('div', { className: 'fv-ruler', ref: ruler, role: 'slider', tabIndex: 0, 'aria-label': '固定播放头帧标尺', 'aria-valuemin': 0, 'aria-valuemax': extent.end - 1, ...(pinned === undefined ? { 'aria-valuetext': '未设置，按 Home 设置为首帧' } : { 'aria-valuenow': pinned }),
        onKeyDown: event => {
          if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
          event.preventDefault()
          if (event.key === 'Home') onFrame?.(0)
          else if (event.key === 'End') onFrame?.(extent.end - 1)
          else step(event.key === 'ArrowLeft' ? -1 : 1)
        },
        onPointerDown: event => { if (event.button !== 0) return; drag.current = event.pointerId; event.currentTarget.setPointerCapture?.(event.pointerId); move(event, true) },
        onPointerMove: event => move(event),
        onPointerUp: event => { if (drag.current === event.pointerId) { move(event, true); drag.current = undefined; event.currentTarget.releasePointerCapture?.(event.pointerId) } },
        onPointerCancel: () => { drag.current = undefined }, onLostPointerCapture: () => { drag.current = undefined },
        onPointerLeave: () => { if (drag.current === undefined) setHover(undefined) },
      }, frameTicks(view).map((tick, index, all) => h('span', { key: tick, className: 'fv-tick', style: { left: `${framePercent(tick, view)}%`, transform: index === all.length - 1 ? 'translateX(-100%)' : undefined }, 'aria-hidden': true }, tick)), lines()),
      result.tracks.map(track => {
        const key = `${track.family}:${track.track_index}`
        const blocks = groups.get(key) ?? []
        const kind = track.family === 'video' && track.data?.kind === 'gfx' ? 'gfx' : track.family
        const name = `${kind} / track ${track.track_index} · ${label(track.data, track.address)}`
        const trackSelection = { kind: 'track', address: track.address }
        return h('section', { key, className: 'fv-track', 'aria-label': name },
          h('button', { className: 'fv-track-title', type: 'button', title: name, 'aria-label': `选择轨道 ${track.address}`, 'aria-pressed': selected.includes(`track:${track.address}`), onClick: event => pick(trackSelection, event) }, name),
          blocks.length === 0 ? h('p', null, '空轨道（kernel 未返回存储块）') : null,
          displayLanes(blocks).map((row, index) => h('div', { className: 'fv-row', key: index, 'aria-label': `显示子行 ${index + 1}（不是新轨道）` },
            row.map(block => {
              const geometry = clipGeometry(block.range, view)
              if (!geometry) return null
              const text = label(block.data, block.address)
              return h('button', { key: block.address, type: 'button', className: `fv-clip fv-${kind}`, style: { left: `${geometry.left}%`, width: `${geometry.width}%`, maxWidth: `${100 - geometry.left}%` },
                title: `${text}\n${block.address} · [${block.range[0]}, ${block.range[1]})`, 'aria-label': `选择块 ${block.address} · ${text} · 帧 [${block.range[0]}, ${block.range[1]})`,
                'aria-pressed': selected.includes(`block:${block.address}`), 'data-hit': hitSet?.has(block.address) ?? false,
                onClick: event => pick({ kind: 'block', address: block.address }, event),
              }, text)
            }), lines())))
      }), result.blocks.length === 0 ? h('p', null, '空项目：没有存储块；外部字幕是否展开见 coverage。') : null))
}
