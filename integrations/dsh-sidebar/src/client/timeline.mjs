import React, { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { displayExtent, windowFor, zoomWindow, panWindow, framePercent, clipGeometry, pointerFrame, frameTicks, displayLanes, safeFrame } from './viewport.mjs'

import { emptySelection, transitionSelection } from './selection.mjs'

const h = React.createElement
// Kept in the JS factory: no stylesheet asset or private DSH theme dependency.
const css = `
.fv-timeline{min-width:0;max-width:100%;font:inherit;color:inherit;overflow-wrap:anywhere;container:timeline/inline-size}
.fv-timeline *{box-sizing:border-box}.fv-timeline button,.fv-timeline input{font:inherit;color:inherit;max-width:100%}
.fv-timeline button{border:1px solid color-mix(in srgb,currentColor 45%,transparent);border-radius:5px;background:color-mix(in srgb,currentColor 6%,transparent);padding:4px 9px;font-size:12px;cursor:pointer;transition:background-color .12s ease,border-color .12s ease,transform .1s ease}
.fv-timeline button:hover:not(:disabled){background:color-mix(in srgb,currentColor 15%,transparent)}
.fv-timeline button:active:not(:disabled){transform:scale(.97)}
.fv-timeline button:disabled{opacity:.45;cursor:default}
.fv-timeline :focus-visible{outline:2px solid var(--fv-tl-accent,#579deb);outline-offset:2px}
.fv-timeline .fv-head{display:flex;flex-wrap:wrap;gap:6px 8px;align-items:center;margin:6px 0}
.fv-timeline .fv-head h3{margin:0;font-size:13px;font-weight:600;min-width:0;flex:1 1 110px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.fv-timeline .fv-badge{font-size:11px;font-weight:500;white-space:nowrap;color:color-mix(in srgb,currentColor 72%,transparent);border:1px solid color-mix(in srgb,currentColor 32%,transparent);border-radius:99px;padding:1px 8px}
.fv-timeline .fv-tools{display:flex;flex-wrap:wrap;gap:4px;align-items:center}
.fv-timeline .fv-status{display:flex;flex-wrap:wrap;gap:2px 12px;margin:4px 0;font-size:12px;font-variant-numeric:tabular-nums;color:color-mix(in srgb,currentColor 88%,transparent)}
.fv-timeline .fv-help{margin:4px 0;font-size:12px}
.fv-timeline .fv-help summary{cursor:pointer;color:color-mix(in srgb,currentColor 68%,transparent)}
.fv-timeline .fv-help p{margin:6px 0}
.fv-timeline .fv-scroll{max-height:460px;overflow:auto;border:1px solid color-mix(in srgb,currentColor 35%,transparent);border-radius:6px;padding:4px 0;min-width:0}
/* Ruler row and every track row share one grid: fixed header column + aligned frame area. */
.fv-timeline .fv-ruler-row,.fv-timeline .fv-track{display:grid;grid-template-columns:var(--fv-tl-head-w,112px) minmax(0,1fr);column-gap:8px;align-items:stretch}
.fv-timeline .fv-ruler-row{padding:0 6px;border-bottom:1px solid color-mix(in srgb,currentColor 45%,transparent);position:sticky;top:0;z-index:3;background:var(--fv-tl-surface,var(--fv-surface,#202125));color:var(--fv-tl-ruler-ink,#dfe3e8)}
.fv-timeline .fv-corner{display:flex;align-items:center;font-size:10px;color:color-mix(in srgb,currentColor 55%,transparent)}
.fv-timeline .fv-ruler{height:30px;position:relative;touch-action:none;cursor:crosshair;overflow:hidden}
.fv-timeline .fv-tick{position:absolute;font-size:10px;top:1px;white-space:nowrap;font-variant-numeric:tabular-nums;border-left:1px solid color-mix(in srgb,currentColor 55%,transparent);padding-left:3px;color:color-mix(in srgb,currentColor 72%,transparent)}
.fv-timeline .fv-track{padding:3px 6px 5px;border-bottom:1px solid color-mix(in srgb,currentColor 18%,transparent)}
.fv-timeline .fv-track-head{min-width:0;display:flex;flex-direction:column;justify-content:center;gap:2px}
.fv-timeline .fv-track-title{display:block;width:100%;text-align:left;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:11.5px;padding:3px 6px;border-radius:4px}
.fv-timeline .fv-empty-note{font-size:10px;padding-left:6px;color:color-mix(in srgb,currentColor 55%,transparent);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.fv-timeline .fv-row{position:relative;height:46px;margin:2px 0;background:color-mix(in srgb,currentColor 6%,transparent);border-radius:4px}
.fv-timeline .fv-clip{position:absolute;top:3px;height:40px;min-width:3px;padding:2px 6px!important;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;text-align:left;font-size:12px;border:1px solid color-mix(in srgb,var(--fv-tl-video,#6f8fad) 60%,transparent);background:color-mix(in srgb,var(--fv-tl-video,#6f8fad) 26%,transparent)}
.fv-timeline .fv-clip:hover{filter:brightness(1.12)}
.fv-timeline .fv-gfx{border-color:color-mix(in srgb,var(--fv-tl-gfx,#a678d9) 60%,transparent);background:color-mix(in srgb,var(--fv-tl-gfx,#a678d9) 26%,transparent)}
.fv-timeline .fv-audio{border-color:color-mix(in srgb,var(--fv-tl-audio,#4fae87) 60%,transparent);background:color-mix(in srgb,var(--fv-tl-audio,#4fae87) 26%,transparent)}
.fv-timeline .fv-subtitles{border-color:color-mix(in srgb,var(--fv-tl-subs,#d9a441) 60%,transparent);background:color-mix(in srgb,var(--fv-tl-subs,#d9a441) 26%,transparent)}
.fv-timeline [aria-pressed=true]{outline:2px solid var(--fv-tl-accent,#579deb);outline-offset:0}
.fv-timeline .fv-clip[aria-pressed=true]{border-color:var(--fv-tl-accent,#579deb)}
.fv-timeline [data-hit=true]{box-shadow:inset 0 -4px var(--fv-tl-hit,#e1ae50)}
.fv-timeline .fv-line{position:absolute;top:0;bottom:0;border-left:2px solid var(--fv-tl-playhead,#ee7272);pointer-events:none;z-index:2}
.fv-timeline .fv-hover{border-left:1px dashed currentColor}
@container timeline (max-width:520px){.fv-timeline .fv-ruler-row,.fv-timeline .fv-track{grid-template-columns:var(--fv-tl-head-w-narrow,84px) minmax(0,1fr)}.fv-timeline .fv-clip{font-size:11px}.fv-timeline .fv-track-title{font-size:11px;padding:2px 4px}}
`
const label = (data, fallback) => typeof data?.name === 'string' ? data.name : typeof data?.text === 'string' ? data.text : typeof data?.src === 'string' ? data.src : fallback

export function VisualTimeline({ snapshot, result, hits, frame, onFrame, onSelect, activeSelection, onSelectionChange, selectionIntent, onGesture, onGestureState }) {
  const extent = displayExtent(snapshot, result)
  const [window, setWindow] = useState()
  const [hover, setHover] = useState()
  // Standalone presentation consumers can remain uncontrolled. PreviewBody always
  // supplies its single intent; this fallback is never a second product selection.
  const [localIntent, setLocalIntent] = useState(emptySelection)
  const intent = selectionIntent ?? localIntent
  const selected = intent.items.map(item => `${item.kind}:${item.address}`)
  const drag = useRef()
  const ruler = useRef()
  const trackNodes = useRef(new Map())
  const gesture = useRef()
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
  useEffect(() => { setWindow(undefined); setHover(undefined); setLocalIntent(emptySelection()); drag.current = undefined }, [snapshot.content_version])
  useEffect(() => { if (!activeSelection) setLocalIntent(emptySelection()) }, [activeSelection])
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
  return h('section', { className: 'fv-timeline', 'aria-label': '完整只读可视时间轴',
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
        h('button', { type: 'button', onClick: () => setWindow(undefined) }, '适配全片'),
        h('button', { type: 'button', disabled: view.end - view.start <= 1, onClick: () => setWindow(zoomWindow(view, extent.end, true)) }, '放大'),
        h('button', { type: 'button', disabled: view.end - view.start >= extent.end, onClick: () => setWindow(zoomWindow(view, extent.end, false)) }, '缩小'),
        h('button', { type: 'button', disabled: view.start === 0, onClick: () => setWindow(panWindow(view, extent.end, -1)) }, '向前平移'),
        h('button', { type: 'button', disabled: view.end === extent.end, onClick: () => setWindow(panWindow(view, extent.end, 1)) }, '向后平移')),
      h('div', { className: 'fv-tools' },
        h('button', { type: 'button', disabled: !onFrame || pinned === undefined, onClick: () => step(-1) }, '前一帧'),
        h('button', { type: 'button', disabled: !onFrame || pinned === undefined, onClick: () => step(1) }, '后一帧'))),
    h('div', { className: 'fv-status', role: 'status' },
      h('span', { 'aria-label': '可见帧范围' }, `帧窗口 [${view.start}, ${view.end}) / 全片 [0, ${extent.end})`),
      h('span', null, `播放头 ${pinned ?? '未固定'}`),
      h('span', null, `悬停帧：${hover ?? '—'}`),
      h('span', null, `已选 ${selected.length}`),
      h('span', { 'aria-label': '活动引用' }, `活动引用：${activeSelection ? `${activeSelection.kind} ${activeSelection.address}` : '无'}`)),
    ghost ? h('p', { role: 'status', 'aria-label': '未保存拖动 ghost', style: { margin: '4px 0', fontSize: '12px' } }, `未保存 ghost · ${ghost.mode} 偏移 ${ghost.delta} 帧 → ${ghost.target.data?.name ?? ghost.target.address}（未改文件/引用）`) : null,
    gestureError ? h('p', { role: 'status', style: { margin: '4px 0', fontSize: '12px' } }, gestureError) : null,
    h('details', { className: 'fv-help' },
      h('summary', null, '操作说明（选择 / 播放头 / 拖拽 / 精度）'),
      h('p', null, `全部 ${result.tracks.length} 轨 / ${result.blocks.length} 块 · video / gfx / audio / subtitles；显示仅映射已保存 kernel range，不查询、不渲染。${onGesture ? '拖动块 / 跨轨 / 两侧 trim 手柄仅生成 ghost + 未保存 batch，预检与提交在下方；键盘使用专用表单。' : '未授写许可，不执行编辑或渲染。'}`),
      h('p', null, '帧标尺：点击/拖动固定播放头；聚焦标尺用 ←/→、Home/End。像素仅概览（1/1024 CSS px 采样），高帧/细块请放大或用整数输入精确定位。Ctrl/⌘ 点击切换多选，Shift 点击按 kernel 查询顺序选择同类区间；引用是组内最后激活的一项，引用不批量；写授权后可用多选批次。')),
    h('div', { className: 'fv-scroll', tabIndex: 0, role: 'region', 'aria-label': '轨道垂直滚动区' },
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
          onPointerDown: event => { if (event.button !== 0) return; drag.current = event.pointerId; event.currentTarget.setPointerCapture?.(event.pointerId); move(event, true) },
          onPointerMove: event => move(event),
          onPointerUp: event => { if (drag.current === event.pointerId) { move(event, true); drag.current = undefined; event.currentTarget.releasePointerCapture?.(event.pointerId) } },
          onPointerCancel: () => { drag.current = undefined }, onLostPointerCapture: () => { drag.current = undefined },
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
            h('button', { className: 'fv-track-title', type: 'button', title: name, 'aria-label': `选择轨道 ${track.address}`, 'aria-pressed': selected.includes(`track:${track.address}`), onClick: event => pick(trackSelection, event) }, name),
            blocks.length === 0 ? h('span', { className: 'fv-empty-note', title: '空轨道（kernel 未返回存储块）' }, '空轨道') : null),
          h('div', { className: 'fv-lanes' },
            blocks.length === 0 ? null : displayLanes(blocks).map((row, index) => h('div', { className: 'fv-row', key: index, 'aria-label': `显示子行 ${index + 1}（不是新轨道）` },
              row.map(block => {
                const geometry = clipGeometry(block.range, view)
                if (!geometry) return null
                const text = label(block.data, block.address)
                const events = mode => ({ onPointerDown: e => beginGesture(e, block, track, mode), onPointerMove: updateGesture, onPointerUp: endGesture, onPointerCancel: e => { cancelGesture(e); if (!gesture.current) setGhost(undefined) }, onLostPointerCapture: e => { cancelGesture(e); if (!gesture.current) setGhost(undefined) } })
                const button = h('button', { key: 'body', type: 'button', className: `fv-clip fv-${kind}`, style: { left: `${geometry.left}%`, width: `${geometry.width}%`, maxWidth: `${100 - geometry.left}%`, touchAction: onGesture ? 'none' : undefined },
                  title: `${text}\n${block.address} · [${block.range[0]}, ${block.range[1]})`, 'aria-label': `选择块 ${block.address} · ${text} · 帧 [${block.range[0]}, ${block.range[1]})`,
                  'aria-pressed': selected.includes(`block:${block.address}`), 'data-hit': hitSet?.has(block.address) ?? false,
                  onClick: event => pick({ kind: 'block', address: block.address }, event), ...events('move'),
                }, text)
                const handles = onGesture && block.data?.id ? ['left','right'].map(mode => h('button',{ key:mode,type:'button','aria-label':`${mode === 'left' ? '左' : '右'} trim 手柄 ${block.address}`,title:'仅 inward trim；拖动后内核预检。键盘请用下方精确帧表单。',style:{position:'absolute',top:3,left:`${mode === 'left' ? geometry.left : geometry.left+geometry.width}%`,transform:mode==='right'?'translateX(-100%)':undefined,width:7,height:40,padding:0,zIndex:3,touchAction:'none',cursor:'ew-resize'},...events(mode)},'⋮')) : []
                return h(React.Fragment,{key:block.address},button,...handles)
              }), ghost && index === 0 && ghost.target.address === track.address ? (() => { const range = ghost.mode === 'move' ? ghost.block.range.map(f => f + ghost.delta) : [ghost.block.range[0] + (ghost.mode === 'left' ? ghost.delta : 0), ghost.block.range[1] + (ghost.mode === 'right' ? ghost.delta : 0)]; const geometry = clipGeometry(range, view); return geometry ? h('span', { 'aria-hidden':true, style:{position:'absolute',left:`${geometry.left}%`,width:`${geometry.width}%`,height:40,top:3,border:'2px dashed var(--fv-tl-hit,#e1ae50)',borderRadius:4,pointerEvents:'none',zIndex:4,opacity:.8} }) : null })() : null, lines()))))
      }), result.blocks.length === 0 ? h('p', { style: { margin: '6px' } }, '空项目：没有存储块；外部字幕是否展开见 coverage。') : null))
}
