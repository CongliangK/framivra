import React, { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { displayExtent, windowFor, zoomWindow, panWindow, framePercent, clipGeometry, pointerFrame, frameTicks, displayLanes, safeFrame } from './viewport.mjs'
import { Icon } from './workbench.mjs'

import { emptySelection, transitionSelection } from './selection.mjs'

const h = React.createElement
// Kept in the JS factory: no stylesheet asset or private DSH theme dependency.
const css = `
.fv-timeline{min-width:0;max-width:100%;font:inherit;color:inherit;overflow-wrap:anywhere;container:timeline/inline-size}
.fv-timeline *{box-sizing:border-box}.fv-timeline button,.fv-timeline input{font:inherit;color:inherit;max-width:100%}
.fv-timeline button{border:1px solid color-mix(in srgb,currentColor 45%,transparent);border-radius:5px;background:color-mix(in srgb,currentColor 6%,transparent);padding:3px 8px;font-size:12px;cursor:pointer;transition:background-color .12s ease,border-color .12s ease;display:inline-flex;align-items:center;justify-content:center;gap:4px;min-height:24px}
.fv-timeline button:hover:not(:disabled){background:color-mix(in srgb,currentColor 15%,transparent)}
.fv-timeline button:active:not(:disabled){transform:scale(.97)}
.fv-timeline button:disabled{opacity:.45;cursor:default}
.fv-timeline :focus-visible{outline:2px solid var(--fv-tl-accent,#579deb);outline-offset:2px}
.fv-timeline .fv-head{display:flex;flex-wrap:wrap;gap:6px 8px;align-items:center;margin:6px 0}
.fv-timeline .fv-head h3{margin:0;font-size:13px;font-weight:600;min-width:0;flex:1 1 110px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.fv-timeline .fv-badge{font-size:11px;font-weight:500;white-space:nowrap;color:color-mix(in srgb,currentColor 72%,transparent);border:1px solid color-mix(in srgb,currentColor 32%,transparent);border-radius:99px;padding:1px 8px}
.fv-timeline .fv-tools{display:flex;flex-wrap:wrap;gap:4px;align-items:center}
.fv-timeline .fv-tools svg{flex:none;opacity:.85}
.fv-timeline .fv-status{display:flex;flex-wrap:wrap;gap:2px 12px;margin:3px 0;font-size:11.5px;font-variant-numeric:tabular-nums;color:color-mix(in srgb,currentColor 80%,transparent)}
.fv-timeline .fv-help{margin:4px 0;font-size:12px}
.fv-timeline .fv-help summary{cursor:pointer;color:color-mix(in srgb,currentColor 68%,transparent)}
.fv-timeline .fv-help p{margin:6px 0}
.fv-timeline .fv-scroll{max-height:460px;overflow:auto;border:1px solid color-mix(in srgb,currentColor 35%,transparent);border-radius:6px;padding:4px 0;min-width:0}
/* Ruler row and every track row share one grid: fixed header column + aligned frame area. */
.fv-timeline .fv-ruler-row,.fv-timeline .fv-track{display:grid;grid-template-columns:var(--fv-tl-head-w,112px) minmax(0,1fr);column-gap:8px;align-items:stretch}
.fv-timeline .fv-ruler-row{padding:0 6px;border-bottom:1px solid color-mix(in srgb,currentColor 45%,transparent);position:sticky;top:0;z-index:3;background:var(--fv-tl-surface,var(--fv-surface,#202125));color:var(--fv-tl-ruler-ink,#dfe3e8)}
.fv-timeline .fv-overview-row{display:grid;grid-template-columns:var(--fv-tl-head-w,112px) minmax(0,1fr);column-gap:8px;align-items:center;padding:2px 6px;border-bottom:1px solid color-mix(in srgb,currentColor 25%,transparent)}
.fv-timeline .fv-corner{display:flex;align-items:center;font-size:10px;color:color-mix(in srgb,currentColor 55%,transparent)}
.fv-timeline .fv-overview{position:relative;height:26px;border-radius:4px;touch-action:none;cursor:crosshair;background:color-mix(in srgb,currentColor 7%,transparent);background-image:repeating-linear-gradient(90deg,transparent 0,transparent calc(25% - 1px),color-mix(in srgb,currentColor 14%,transparent) 25%)}
.fv-timeline .fv-ov{position:absolute;top:10px;height:6px;min-width:1px;border-radius:2px;opacity:.6;pointer-events:none}
.fv-timeline .fv-ov-video{background:var(--fv-tl-video,#6f8fad)}.fv-timeline .fv-ov-gfx{background:var(--fv-tl-gfx,#a678d9)}.fv-timeline .fv-ov-audio{background:var(--fv-tl-audio,#4fae87)}.fv-timeline .fv-ov-subtitles{background:var(--fv-tl-subs,#d9a441)}
.fv-timeline .fv-ov-window{position:absolute;top:5px;height:16px;border:1.5px solid var(--fv-tl-accent,#579deb);border-radius:3px;pointer-events:none}
.fv-timeline .fv-ov-pin{position:absolute;top:3px;bottom:3px;border-left:2px solid var(--fv-tl-playhead,#ee7272);pointer-events:none}
.fv-timeline .fv-ruler{height:30px;position:relative;touch-action:none;cursor:crosshair;overflow:hidden;background-image:repeating-linear-gradient(90deg,transparent 0,transparent calc(25% - 1px),color-mix(in srgb,currentColor 20%,transparent) 25%)}
.fv-timeline .fv-tick{position:absolute;font-size:10px;top:1px;white-space:nowrap;font-variant-numeric:tabular-nums;border-left:1px solid color-mix(in srgb,currentColor 55%,transparent);padding-left:3px;color:color-mix(in srgb,currentColor 72%,transparent)}
.fv-timeline .fv-track{padding:3px 6px 5px;border-bottom:1px solid color-mix(in srgb,currentColor 18%,transparent)}
.fv-timeline .fv-track-head{min-width:0;display:flex;flex-direction:column;justify-content:center;gap:2px}
.fv-timeline .fv-track-title{display:flex;width:100%;text-align:left;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:11.5px;padding:3px 6px;border-radius:4px;align-items:center;gap:5px}
.fv-timeline .fv-track-title svg{flex:none;opacity:.8}
.fv-timeline .fv-track-name{min-width:0;overflow:hidden;text-overflow:ellipsis}
.fv-timeline .fv-empty-note{font-size:10px;padding-left:6px;color:color-mix(in srgb,currentColor 55%,transparent);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.fv-timeline .fv-row{position:relative;height:46px;margin:2px 0;background:color-mix(in srgb,currentColor 6%,transparent);border-radius:4px;background-image:repeating-linear-gradient(90deg,transparent 0,transparent calc(25% - 1px),color-mix(in srgb,currentColor 9%,transparent) 25%)}
.fv-timeline .fv-clip{position:absolute;top:3px;height:40px;min-width:3px;padding:2px 6px!important;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;text-align:left;font-size:12px;align-items:center;border:1px solid color-mix(in srgb,var(--fv-tl-video,#6f8fad) 60%,transparent);background:color-mix(in srgb,var(--fv-tl-video,#6f8fad) 26%,transparent)}
.fv-timeline .fv-clip:hover{filter:brightness(1.12)}
.fv-timeline .fv-gfx{border-color:color-mix(in srgb,var(--fv-tl-gfx,#a678d9) 60%,transparent);background:color-mix(in srgb,var(--fv-tl-gfx,#a678d9) 26%,transparent)}
.fv-timeline .fv-audio{border-color:color-mix(in srgb,var(--fv-tl-audio,#4fae87) 60%,transparent);background:color-mix(in srgb,var(--fv-tl-audio,#4fae87) 26%,transparent)}
.fv-timeline .fv-subtitles{border-color:color-mix(in srgb,var(--fv-tl-subs,#d9a441) 60%,transparent);background:color-mix(in srgb,var(--fv-tl-subs,#d9a441) 26%,transparent)}
.fv-timeline [aria-pressed=true]{outline:2px solid var(--fv-tl-accent,#579deb);outline-offset:0}
.fv-timeline .fv-clip[aria-pressed=true]{border-color:var(--fv-tl-accent,#579deb);box-shadow:inset 0 0 0 1px var(--fv-tl-accent,#579deb);filter:brightness(1.1)}
.fv-timeline [data-hit=true]{box-shadow:inset 0 -4px var(--fv-tl-hit,#e1ae50)}
.fv-timeline .fv-clip[aria-pressed=true][data-hit=true]{box-shadow:inset 0 0 0 1px var(--fv-tl-accent,#579deb),inset 0 -4px var(--fv-tl-hit,#e1ae50)}
.fv-timeline .fv-clip-name{min-width:0;overflow:hidden;text-overflow:ellipsis}
.fv-timeline .fv-clip-len{flex:none;margin-left:5px;padding:0 4px;border-radius:3px;font-size:10px;font-variant-numeric:tabular-nums;color:color-mix(in srgb,currentColor 78%,transparent);background:color-mix(in srgb,currentColor 14%,transparent)}
.fv-timeline .fv-line{position:absolute;top:0;bottom:0;border-left:2px solid var(--fv-tl-playhead,#ee7272);pointer-events:none;z-index:2}
.fv-timeline .fv-line::before{content:'';position:absolute;top:0;left:-5px;border:4px solid transparent;border-top:6px solid var(--fv-tl-playhead,#ee7272)}
.fv-timeline .fv-hover{border-left:1px dashed currentColor}
.fv-timeline .fv-hover::before{display:none}
/* Coarse primary pointers enlarge only the compact controls — never clips or
   trim handles: a forced 44px minimum over a 7px handle would put an invisible
   overlay across every clip edge, turning touch selects into trims. Handles are
   hidden for coarse primary pointers; a touch pointer is also refused in JS
   (hybrid devices) with the precise integer form as the alternative. */
@media(pointer:coarse){.fv-timeline .fv-tools button{min-height:44px;min-width:44px}.fv-timeline .fv-track-title{min-height:44px}.fv-timeline .fv-trim{display:none}}
/* Compact embedding (monitor pane): only tighter track/row vertical rhythm.
   Lane height 46 / clip 40 / coarse title minimums stay untouched. */
.fv-timeline.fv-compact .fv-track{padding:0 6px}
.fv-timeline.fv-compact .fv-row{margin:0}
@container timeline (max-width:520px){.fv-timeline .fv-ruler-row,.fv-timeline .fv-track,.fv-timeline .fv-overview-row{grid-template-columns:var(--fv-tl-head-w-narrow,84px) minmax(0,1fr)}.fv-timeline .fv-clip{font-size:11px}.fv-timeline .fv-clip-len{display:none}.fv-timeline .fv-track-title{font-size:11px;padding:2px 4px}}
`
const label = (data, fallback) => typeof data?.name === 'string' ? data.name : typeof data?.text === 'string' ? data.text : typeof data?.src === 'string' ? data.src : fallback
const trackIcon = kind => kind === 'audio' ? 'audio' : kind === 'subtitles' ? 'text' : kind === 'gfx' ? 'clip' : 'film'

// The help/legend text, extracted verbatim from the inline details so the
// compact timeline can omit it while the inspector mounts the same content.
// Same labels and copy; standalone TimelineView keeps its inline default.
export function TimelineHelp({ result, onGesture }) {
  return h('details', { className: 'fv-help' },
    h('summary', null, '操作说明（选择 / 播放头 / 拖拽 / 精度）'),
    h('p', null, `全部 ${result.tracks.length} 轨 / ${result.blocks.length} 块 · video / gfx / audio / subtitles；显示仅映射已保存 kernel range，不查询、不渲染。${onGesture ? '拖动块 / 跨轨 / 两侧 trim 手柄仅生成 ghost + 未保存 batch，预检与提交在下方；键盘使用专用表单。' : '未授写许可，不执行编辑或渲染。'}`),
    h('p', null, '帧标尺：点击/拖动固定播放头；聚焦标尺用 ←/→、Home/End。全片导航条：点击/拖动或 ←/→/Home/End 只平移缩放显示窗口，不查询、不渲染、不改选择。像素仅概览（1/1024 CSS px 采样），高帧/细块请放大或用整数输入精确定位。Ctrl/⌘ 点击切换多选，Shift 点击按 kernel 查询顺序选择同类区间；引用是组内最后激活的一项，引用不批量；写授权后可用多选批次。'))
}

export function VisualTimeline({ snapshot, result, hits, frame, onFrame, onSelect, activeSelection, onSelectionChange, selectionIntent, onGesture, onGestureState, compact = false }) {
  const extent = displayExtent(snapshot, result)
  const [window, setWindow] = useState()
  const [hover, setHover] = useState()
  // Standalone presentation consumers can remain uncontrolled. PreviewBody always
  // supplies its single intent; this fallback is never a second product selection.
  const [localIntent, setLocalIntent] = useState(emptySelection())
  const intent = selectionIntent ?? localIntent
  const selected = intent.items.map(item => `${item.kind}:${item.address}`)
  const drag = useRef()
  const ruler = useRef()
  const trackNodes = useRef(new Map())
  const gesture = useRef()
  const overview = useRef()
  const overviewPointer = useRef()
  // A canceled gesture still owns its physical pointer's eventual click. React's
  // click facade is a MouseEvent; the native PointerEvent carries pointerId.
  // Retire ownership only on that click or a fresh down from the same pointer,
  // never on a keyboard activation or an unrelated pointer's end/capture loss.
  const pointerTails = useRef(new Set())
  const [ghost, setGhost] = useState()
  const [gestureError, setGestureError] = useState('')
  function releaseGesture(g) {
    try { if (g.node.hasPointerCapture?.(g.pointer)) g.node.releasePointerCapture(g.pointer) } catch { /* detached/lost capture */ }
  }
  function cancelGesture(event) {
    const g = gesture.current
    if (!g || (event && ((event.pointerId !== undefined && g.pointer !== event.pointerId) || g.node !== event.currentTarget))) return
    gesture.current = undefined
    pointerTails.current.add(g.pointer)
    setGhost(undefined)
    g.publish?.(g.token, false)
    releaseGesture(g)
  }
  useLayoutEffect(() => () => cancelGesture(), [snapshot.content_version, onGesture, onGestureState, intent, frame])
  useEffect(() => { setWindow(undefined); setHover(undefined); setLocalIntent(emptySelection()); drag.current = undefined; overviewPointer.current = undefined }, [snapshot.content_version])
  useEffect(() => {
    // The native dialog collapse never remounts this component, and window
    // blur / hidden tab can end a pointer capture WITHOUT delivering any
    // pointerup/pointercancel. Clear every pointer owner defensively — refs
    // first, then a release attempt for each actually captured pointer — so no
    // stranded owner can wedge all later input. Non-owner pointer cancellations
    // in the per-node handlers stay ignored; only this global fallback is
    // unconditional. A canceled gesture keeps its eventual click via pointerTails.
    const clear = () => {
      const rulerPointer = drag.current
      const overviewOwner = overviewPointer.current
      drag.current = undefined
      overviewPointer.current = undefined
      try { if (rulerPointer !== undefined && ruler.current?.hasPointerCapture?.(rulerPointer)) ruler.current.releasePointerCapture(rulerPointer) } catch { /* capture already gone */ }
      try { if (overviewOwner !== undefined && overview.current?.hasPointerCapture?.(overviewOwner)) overview.current.releasePointerCapture(overviewOwner) } catch { /* capture already gone */ }
      cancelGesture()
    }
    const onVisibility = () => { if (document.hidden) clear() }
    // The view-window state above shadows the global `window`; reach it via the
    // document's default view. Guard for non-DOM hosts.
    const hostWindow = typeof document === 'undefined' ? undefined : document.defaultView
    if (!hostWindow || typeof document.addEventListener !== 'function') return
    hostWindow.addEventListener('blur', clear)
    document.addEventListener('visibilitychange', onVisibility)
    return () => { hostWindow.removeEventListener('blur', clear); document.removeEventListener('visibilitychange', onVisibility) }
  }, [])
  useEffect(() => { if (!activeSelection) setLocalIntent(emptySelection()) }, [activeSelection])
  if (extent.diagnostic) return h('p', { role: 'alert' }, extent.diagnostic)
  const view = window ? windowFor(extent.end, window.start, window.end - window.start) : extent
  const pinned = safeFrame(frame) ? frame : undefined
  const hitSet = hits ? new Set(hits.blocks.map(block => block.address)) : undefined
  const groups = new Map()
  const kindOf = new Map(result.tracks.map(track => [`${track.family}:${track.track_index}`, track.family === 'video' && track.data?.kind === 'gfx' ? 'gfx' : track.family]))
  for (const block of result.blocks) {
    const key = `${block.family}:${block.track_index}`
    if (!groups.has(key)) groups.set(key, [])
    groups.get(key).push(block)
  }
  // Overview/navigation strip: display-only. Pointer and keyboard move the view
  // window; they never query, render, edit, pin the playhead, or change selection.
  const span = view.end - view.start
  function panBy(delta) {
    const start = Math.max(0, Math.min(view.start + delta, extent.end - span))
    setWindow(windowFor(extent.end, start, span))
  }
  function seekOverview(event) {
    const rect = overview.current?.getBoundingClientRect()
    const at = rect ? pointerFrame(event.clientX, rect, extent) : undefined
    if (at === undefined) return
    setWindow(windowFor(extent.end, Math.max(0, Math.min(at - Math.floor(span / 2), extent.end - span)), span))
  }
  const selectedBlocks = result.blocks.filter(block => selected.includes(`block:${block.address}`))
  const selectionZoom = selectedBlocks.length ? windowFor(extent.end, Math.min(...selectedBlocks.map(block => block.range[0])), Math.max(1, Math.max(...selectedBlocks.map(block => block.range[1])) - Math.min(...selectedBlocks.map(block => block.range[0])))) : undefined
  function pick(item, event) {
    if (selectionIntent) { onSelect?.(item, event); return }
    const next = transitionSelection(localIntent, item, event, result)
    setLocalIntent(next)
    onSelectionChange?.(next.items)
    onSelect?.(next.primary)
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
  function beginGesture(event, block, track, mode) {
    if (!onGesture || event.button !== 0 || !block.data?.id || event.ctrlKey || event.metaKey) return
    if ((mode === 'left' || mode === 'right') && event.pointerType === 'touch') {
      // Hybrid-device safety: a touch pointer never starts a trim even where the
      // handle is visible (fine primary pointer). Keep selection and move.
      setGestureError('触控指针不支持 trim 手柄：请用下方整数帧表单精确修改块起始/结束帧。')
      return
    }
    const origin = pointerFrame(event.clientX, ruler.current.getBoundingClientRect(), view)
    if (origin === undefined) return
    const moving = selected.includes(`block:${block.address}`) ? result.blocks.filter(b => selected.includes(`block:${b.address}`)) : [block]
    const next = { pointer: event.pointerId, node: event.currentTarget, origin, block, track, mode, moving, token: {}, publish: onGestureState }
    // Exclude frame work in this very DOM event, before capture/release can
    // dispatch callbacks. Retiring the old token cannot clear this new one.
    next.publish?.(next.token, true)
    cancelGesture()
    gesture.current = next
    event.currentTarget.setPointerCapture?.(event.pointerId)
  }
  function updateGesture(event) {
    const g = gesture.current
    if (!g || g.pointer !== event.pointerId || g.node !== event.currentTarget) return
    const at = pointerFrame(event.clientX, ruler.current.getBoundingClientRect(), view)
    if (at === undefined) return
    let target = g.track
    for (const [key, node] of trackNodes.current) {
      if (!node) continue
      const box = node.getBoundingClientRect()
      if (event.clientY >= box.top && event.clientY < box.bottom) { target = result.tracks.find(t => `${t.family}:${t.track_index}` === key); break }
    }
    const delta = at - g.origin
    setGhost({ ...g, delta, target })
    return { ...g, delta, target }
  }
  function endGesture(event) {
    const g = updateGesture(event)
    if (!g) return
    gesture.current = undefined; setGhost(undefined)
    if (!g.delta && (g.mode !== 'move' || g.target.address === g.track.address)) {
      g.publish?.(g.token, false); releaseGesture(g); return
    }
    pointerTails.current.add(g.pointer)
    try {
      if (g.mode === 'move') {
        const operations = g.moving.map(b => { const start = b.range[0] + g.delta; if (!Number.isSafeInteger(start) || start < 0) throw new Error('拖动结果不是可编辑安全整数帧')
          const original = result.tracks.find(t => t.family === b.family && t.track_index === b.track_index)
          const target = b.address === g.block.address || (b.family === g.block.family && b.track_index === g.block.track_index) ? g.target : original
          return { op: 'block.move', id: b.data.id, track_id: target.family === 'subtitles' ? 'subtitles' : target.data.id, at: start }
        })
        onGesture(operations)
      } else {
        const start = g.mode === 'left' ? g.block.range[0] + g.delta : g.block.range[0]
        const end = g.mode === 'right' ? g.block.range[1] + g.delta : g.block.range[1]
        if (![start,end].every(Number.isSafeInteger)) throw new Error('trim 帧不安全，改用整数表单')
        onGesture([{ op: 'block.trim', id: g.block.data.id, start, end, fade_policy: 'preserve-content' }])
      }
      setGestureError('拖动/trim 已加入未保存批次。边界、源窗、AV、gfx 和 fade 以内核计划为准；默认明确 preserve-content，可在表单选择另一策略。')
    } catch (error) { setGestureError(error.message) }
    finally {
      // onGesture publishes queued dirty synchronously before exclusion ends.
      g.publish?.(g.token, false)
      releaseGesture(g)
    }
  }
  const lines = () => [
    pinned !== undefined && pinned >= view.start && pinned < view.end ? h('span', { key: 'pin', className: 'fv-line', style: { left: `${framePercent(pinned, view)}%` }, 'aria-hidden': true }) : null,
    hover !== undefined && hover >= view.start && hover < view.end ? h('span', { key: 'hover', className: 'fv-line fv-hover', style: { left: `${framePercent(hover, view)}%` }, 'aria-hidden': true }) : null,
  ]
  return h('section', { className: compact ? 'fv-timeline fv-compact' : 'fv-timeline', 'aria-label': '完整只读可视时间轴',
    onPointerDownCapture: event => pointerTails.current.delete(event.pointerId),
    onClickCapture: event => {
      if (event.detail === 0 || !pointerTails.current.delete(event.nativeEvent.pointerId)) return
      event.preventDefault(); event.stopPropagation()
    },
  },
    h('style', null, css),
    h('div', { className: 'fv-head' },
      h('h3', { title: label(snapshot.document?.project, '时间线') }, label(snapshot.document?.project, '时间线')),
      h('span', { className: 'fv-badge' }, onGesture ? '可拖拽 · 未保存 intents' : '只读'),
      h('div', { className: 'fv-tools', role: 'group', 'aria-label': '显示范围' },
        h('button', { type: 'button', title: '窗口回到全片', onClick: () => setWindow(undefined) }, h(Icon, { name: 'fit', size: 13 }), '适配全片'),
        h('button', { type: 'button', title: '窗口放大一倍精度', disabled: view.end - view.start <= 1, onClick: () => setWindow(zoomWindow(view, extent.end, true)) }, h(Icon, { name: 'zoomIn', size: 13 }), '放大'),
        h('button', { type: 'button', title: '窗口缩小一半', disabled: view.end - view.start >= extent.end, onClick: () => setWindow(zoomWindow(view, extent.end, false)) }, h(Icon, { name: 'zoomOut', size: 13 }), '缩小'),
        h('button', { type: 'button', title: '窗口向前平移半屏', disabled: view.start === 0, onClick: () => setWindow(panWindow(view, extent.end, -1)) }, h(Icon, { name: 'back', size: 13 }), '向前平移'),
        h('button', { type: 'button', title: '窗口向后平移半屏', disabled: view.end === extent.end, onClick: () => setWindow(panWindow(view, extent.end, 1)) }, h(Icon, { name: 'next', size: 13 }), '向后平移'),
        h('button', { type: 'button', title: '窗口缩放到已选块的存储帧范围（只改显示）', disabled: !selectionZoom, onClick: () => selectionZoom && setWindow(selectionZoom) }, h(Icon, { name: 'clip', size: 13 }), '缩放到选区')),
      h('div', { className: 'fv-tools' },
        h('button', { type: 'button', disabled: !onFrame || pinned === undefined, onClick: () => step(-1) }, h(Icon, { name: 'previous', size: 13 }), '前一帧'),
        h('button', { type: 'button', disabled: !onFrame || pinned === undefined, onClick: () => step(1) }, h(Icon, { name: 'next', size: 13 }), '后一帧'))),
    h('div', { className: 'fv-status', role: 'status' },
      h('span', { 'aria-label': '可见帧范围' }, `帧窗口 [${view.start}, ${view.end}) / 全片 [0, ${extent.end})`),
      h('span', null, `播放头 ${pinned ?? '未固定'}`),
      h('span', null, `悬停帧：${hover ?? '—'}`),
      h('span', null, `已选 ${selected.length}`),
      h('span', { 'aria-label': '活动引用' }, `活动引用：${activeSelection ? `${activeSelection.kind} ${activeSelection.address}` : '无'}`)),
    ghost ? h('p', { role: 'status', 'aria-label': '未保存拖动 ghost', style: { margin: '4px 0', fontSize: '12px' } }, `未保存 ghost · ${ghost.mode} 偏移 ${ghost.delta} 帧 → ${ghost.target.data?.name ?? ghost.target.address}（未改文件/引用）`) : null,
    gestureError ? h('p', { role: 'status', style: { margin: '4px 0', fontSize: '12px' } }, gestureError) : null,
    // Compact mode (monitor-pane embedding) suppresses only the inline help; the
    // inspector mounts the identical TimelineHelp content separately.
    compact ? null : h(TimelineHelp, { result, onGesture }),
    h('div', { className: 'fv-scroll', tabIndex: 0, role: 'region', 'aria-label': '轨道垂直滚动区' },
      h('div', { className: 'fv-overview-row' },
        h('div', { className: 'fv-corner', 'aria-hidden': true }, '全片'),
        h('div', { className: 'fv-overview', ref: overview, role: 'slider', tabIndex: 0, 'aria-label': '全片导航条（只平移/缩放显示窗口）', 'aria-valuemin': 0, 'aria-valuemax': Math.max(0, extent.end - span), 'aria-valuenow': view.start, 'aria-valuetext': `窗口 [${view.start}, ${view.end}) / 全片 [0, ${extent.end})`,
          onKeyDown: event => {
            if (!['ArrowLeft', 'ArrowRight', 'Home', 'End', 'PageUp', 'PageDown'].includes(event.key)) return
            event.preventDefault()
            if (event.key === 'Home') setWindow(windowFor(extent.end, 0, span))
            else if (event.key === 'End') setWindow(windowFor(extent.end, extent.end - span, span))
            else if (event.key === 'PageUp') setWindow(panWindow(view, extent.end, -1))
            else if (event.key === 'PageDown') setWindow(panWindow(view, extent.end, 1))
            else panBy(Math.round(span / 4) * (event.key === 'ArrowLeft' ? -1 : 1))
          },
          onPointerDown: event => {
            if (event.button !== 0) return
            // First pointer owns the navigation strip until release; a second
            // simultaneous touch never hijacks the pan.
            if (overviewPointer.current !== undefined) return
            overviewPointer.current = event.pointerId
            event.currentTarget.setPointerCapture?.(event.pointerId)
            seekOverview(event)
          },
          onPointerMove: event => { if (overviewPointer.current === event.pointerId) seekOverview(event) },
          onPointerUp: event => { if (overviewPointer.current === event.pointerId) { overviewPointer.current = undefined; event.currentTarget.releasePointerCapture?.(event.pointerId) } },
          onPointerCancel: event => { if (overviewPointer.current === event.pointerId) overviewPointer.current = undefined },
          onLostPointerCapture: event => { if (overviewPointer.current === event.pointerId) overviewPointer.current = undefined },
        },
          result.blocks.map(block => { const geometry = clipGeometry(block.range, extent); if (!geometry) return null
            return h('span', { key: block.address, className: `fv-ov fv-ov-${kindOf.get(`${block.family}:${block.track_index}`) ?? block.family}`, style: { left: `${geometry.left}%`, width: `${Math.max(geometry.width, 0.4)}%` }, title: `${label(block.data, block.address)} · [${block.range[0]}, ${block.range[1]})`, 'aria-hidden': true }) }),
          h('span', { className: 'fv-ov-window', style: { left: `${framePercent(view.start, extent)}%`, width: `${Math.max(framePercent(view.end, extent) - framePercent(view.start, extent), 0.6)}%` }, 'aria-hidden': true }),
          pinned !== undefined ? h('span', { className: 'fv-ov-pin', style: { left: `${framePercent(pinned, extent)}%` }, 'aria-hidden': true }) : null)),
      h('div', { className: 'fv-ruler-row' },
        h('div', { className: 'fv-corner', 'aria-hidden': true }, '帧'),
        h('div', { className: 'fv-ruler', ref: ruler, role: 'slider', tabIndex: 0, 'aria-label': '固定播放头帧标尺', 'aria-valuemin': 0, 'aria-valuemax': extent.end - 1, ...(pinned === undefined ? { 'aria-valuetext': '未设置，按 Home 设置为首帧' } : { 'aria-valuenow': pinned }),
          onKeyDown: event => {
            if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
            event.preventDefault()
            if (event.key === 'Home') onFrame?.(0)
            else if (event.key === 'End') onFrame?.(extent.end - 1)
            else step(event.key === 'ArrowLeft' ? -1 : 1)
          },
          onPointerDown: event => {
            if (event.button !== 0) return
            // First pointer owns the scrub until release; a second simultaneous
            // touch cannot hijack the playhead, and capture loss of a non-owner
            // (implicit release after its own pointerup) cannot cancel the owner.
            if (drag.current !== undefined) return
            drag.current = event.pointerId
            event.currentTarget.setPointerCapture?.(event.pointerId)
            move(event, true)
          },
          onPointerMove: event => move(event),
          onPointerUp: event => { if (drag.current === event.pointerId) { move(event, true); drag.current = undefined; event.currentTarget.releasePointerCapture?.(event.pointerId) } },
          onPointerCancel: event => { if (drag.current === event.pointerId) drag.current = undefined },
          onLostPointerCapture: event => { if (drag.current === event.pointerId) drag.current = undefined },
          onPointerLeave: () => { if (drag.current === undefined) setHover(undefined) },
        }, frameTicks(view).map((tick, index, all) => h('span', { key: tick, className: 'fv-tick', style: { left: `${framePercent(tick, view)}%`, transform: index === all.length - 1 ? 'translateX(-100%)' : undefined }, 'aria-hidden': true }, tick)), lines())),
      result.tracks.map(track => {
        const key = `${track.family}:${track.track_index}`
        const blocks = groups.get(key) ?? []
        const kind = track.family === 'video' && track.data?.kind === 'gfx' ? 'gfx' : track.family
        const name = `${kind} / track ${track.track_index} · ${label(track.data, track.address)}`
        const trackSelection = { kind: 'track', address: track.address }
        return h('section', { key, ref: node => { if (node) trackNodes.current.set(key, node); else trackNodes.current.delete(key) }, className: 'fv-track', 'aria-label': name },
          h('div', { className: 'fv-track-head' },
            h('button', { className: 'fv-track-title', type: 'button', title: name, 'aria-label': `选择轨道 ${track.address}`, 'aria-pressed': selected.includes(`track:${track.address}`), onClick: event => pick(trackSelection, event) }, h(Icon, { name: trackIcon(kind), size: 13 }), h('span', { className: 'fv-track-name' }, name)),
            blocks.length === 0 ? h('span', { className: 'fv-empty-note', title: '空轨道（kernel 未返回存储块）' }, '空轨道') : null),
          h('div', { className: 'fv-lanes' },
            blocks.length === 0 ? null : displayLanes(blocks).map((row, index) => h('div', { className: 'fv-row', key: index, 'aria-label': `显示子行 ${index + 1}（不是新轨道）` },
              row.map(block => {
                const geometry = clipGeometry(block.range, view)
                if (!geometry) return null
                const text = label(block.data, block.address)
                const duration = block.range[1] - block.range[0]
                const events = mode => ({ onPointerDown: e => beginGesture(e, block, track, mode), onPointerMove: updateGesture, onPointerUp: endGesture, onPointerCancel: e => { cancelGesture(e); if (!gesture.current) setGhost(undefined) }, onLostPointerCapture: e => { cancelGesture(e); if (!gesture.current) setGhost(undefined) } })
                const button = h('button', { key: 'body', type: 'button', className: `fv-clip fv-${kind}`, style: { left: `${geometry.left}%`, width: `${geometry.width}%`, maxWidth: `${100 - geometry.left}%`, touchAction: onGesture ? 'none' : undefined },
                  title: `${text}\n${block.address} · [${block.range[0]}, ${block.range[1]}) · ${duration} 帧`, 'aria-label': `选择块 ${block.address} · ${text} · 帧 [${block.range[0]}, ${block.range[1]})`,
                  'aria-pressed': selected.includes(`block:${block.address}`), 'data-hit': hitSet?.has(block.address) ?? false,
                  onClick: event => pick({ kind: 'block', address: block.address }, event), ...events('move'),
                }, h('span', { className: 'fv-clip-name' }, text), Number.isSafeInteger(duration) && duration >= 0 ? h('span', { className: 'fv-clip-len' }, `${duration} 帧`) : null)
                const handles = onGesture && block.data?.id ? ['left','right'].map(mode => h('button',{ key:mode,type:'button',className:'fv-trim','aria-label':`${mode === 'left' ? '左' : '右'} trim 手柄 ${block.address}`,title:'仅 inward trim；拖动后内核预检。触控请用下方精确帧表单。',style:{position:'absolute',top:3,left:`${mode === 'left' ? geometry.left : geometry.left+geometry.width}%`,transform:mode==='right'?'translateX(-100%)':undefined,width:7,height:40,padding:0,zIndex:3,touchAction:'none',cursor:'ew-resize'},...events(mode)})) : []
                return h(React.Fragment,{key:block.address},button,...handles)
              }), ghost && index === 0 && ghost.target.address === track.address ? (() => { const range = ghost.mode === 'move' ? ghost.block.range.map(f => f + ghost.delta) : [ghost.block.range[0] + (ghost.mode === 'left' ? ghost.delta : 0), ghost.block.range[1] + (ghost.mode === 'right' ? ghost.delta : 0)]; const geometry = clipGeometry(range, view); return geometry ? h('span', { 'aria-hidden':true, style:{position:'absolute',left:`${geometry.left}%`,width:`${geometry.width}%`,height:40,top:3,border:'2px dashed var(--fv-tl-hit,#e1ae50)',borderRadius:4,pointerEvents:'none',zIndex:4,opacity:.8} }) : null })() : null, lines()))))
      }), result.blocks.length === 0 ? h('p', { style: { margin: '6px' } }, '空项目：没有存储块；外部字幕是否展开见 coverage。') : null))
}
