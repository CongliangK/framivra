import React, { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { displayExtent, windowFor, zoomWindow, panWindow, framePercent, clipGeometry, pointerFrame, frameTicks, displayLanes, safeFrame } from './viewport.mjs'
import { Icon } from './icons.mjs'
import { ToolButton, iconControlCSS } from './icon-controls.mjs'
import { formatClockTimecode, projectClock } from './timecode.mjs'
import { familyName } from './editor-controls.mjs'
import { closeGapIntent, internalGaps, snapDelta } from './direct-edit.mjs'

import { emptySelection, transitionSelection } from './selection.mjs'

const h = React.createElement
// Kept in the JS factory: no stylesheet asset or private DSH theme dependency.
const css = `
.fv-timeline{position:relative;min-width:0;max-width:100%;font:inherit;color:inherit;overflow-wrap:anywhere;container:timeline/inline-size}
.fv-timeline *{box-sizing:border-box}.fv-timeline button,.fv-timeline input{font:inherit;color:inherit;max-width:100%}
.fv-timeline button{border:1px solid color-mix(in srgb,currentColor 45%,transparent);border-radius:5px;background:color-mix(in srgb,currentColor 6%,transparent);padding:3px 8px;font-size:12px;cursor:pointer;transition:background-color .12s ease,border-color .12s ease;display:inline-flex;align-items:center;justify-content:center;gap:4px;min-height:24px}
.fv-timeline button:hover:not(:disabled){background:color-mix(in srgb,currentColor 15%,transparent)}
.fv-timeline button:active:not(:disabled){transform:scale(.97)}
.fv-timeline button:disabled{opacity:.45;cursor:default}
.fv-timeline :focus-visible{outline:2px solid var(--fv-tl-accent,#579deb);outline-offset:2px}
.fv-timeline .fv-head{display:flex;flex-wrap:nowrap;gap:4px;align-items:center;margin:0 0 4px;overflow-x:auto;min-height:32px;scrollbar-width:thin}
.fv-timeline .fv-head h3{margin:0;font-size:12px;font-weight:600;min-width:3em;flex:0 1 auto;max-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.fv-timeline .fv-badge{font-size:11px;font-weight:500;white-space:nowrap;color:color-mix(in srgb,currentColor 72%,transparent);border:1px solid color-mix(in srgb,currentColor 32%,transparent);border-radius:99px;padding:1px 8px}
.fv-timeline .fv-tools{display:flex;flex:none;flex-wrap:nowrap;gap:4px;align-items:center}
.fv-timeline .fv-tools svg{flex:none;opacity:.85}
.fv-timeline .fv-status{display:flex;flex-wrap:wrap;gap:2px 12px;margin:3px 0;font-size:11.5px;font-variant-numeric:tabular-nums;color:color-mix(in srgb,currentColor 80%,transparent)}
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
.fv-timeline .fv-row{position:relative;height:var(--fv-lane-height,46px);margin:2px 0;background:color-mix(in srgb,currentColor 6%,transparent);border-radius:4px;background-image:repeating-linear-gradient(90deg,transparent 0,transparent calc(25% - 1px),color-mix(in srgb,currentColor 9%,transparent) 25%)}
.fv-timeline .fv-clip{position:absolute;top:var(--fv-clip-top,3px);height:var(--fv-clip-height,40px);min-height:0;min-width:3px;padding:2px 6px!important;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;text-align:left;font-size:12px;align-items:center;border:1px solid color-mix(in srgb,var(--fv-tl-video,#6f8fad) 60%,transparent);background:color-mix(in srgb,var(--fv-tl-video,#6f8fad) 26%,transparent)}
.fv-timeline .fv-clip{user-select:none}
.fv-timeline .fv-clip[data-movable=true]{cursor:grab;touch-action:none}
.fv-timeline[data-dragging=true] .fv-clip[data-movable=true]{cursor:grabbing}
.fv-timeline .fv-clip[data-pending=true]{opacity:.55}
.fv-timeline .fv-placement{position:absolute;top:var(--fv-clip-top);height:var(--fv-clip-height);border:2px dashed var(--fv-tl-hit,#e1ae50);border-radius:4px;pointer-events:none;z-index:4;min-width:3px}
.fv-timeline .fv-placement[data-kind=drag]{border-style:solid;background:color-mix(in srgb,var(--fv-tl-accent) 30%,transparent);border-color:var(--fv-tl-accent)}
.fv-timeline .fv-placement[data-kind=blocked]{border-color:var(--fv-tl-playhead)}
.fv-timeline .fv-drag-feedback{position:absolute;bottom:6px;right:8px;z-index:5;max-width:calc(100% - 16px);background:var(--fv-tl-surface);border:1px solid var(--fv-line,currentColor);border-radius:5px;padding:3px 8px;display:flex;align-items:center;gap:6px;font-size:12px;font-variant-numeric:tabular-nums;pointer-events:none}
.fv-timeline .fv-drag-feedback button{pointer-events:auto}
.fv-timeline .fv-snap-line{border-color:var(--fv-tl-accent);z-index:5}
.fv-timeline .fv-snap-line::before{display:none}
.fv-timeline .fv-gap{position:absolute;top:var(--fv-clip-top);height:var(--fv-clip-height);min-height:0;padding:0!important;overflow:hidden;color:var(--fv-muted);border-color:transparent;background:transparent;opacity:.7;z-index:1}
.fv-timeline .fv-gap:hover,.fv-timeline .fv-gap:focus-visible{opacity:1;border-color:var(--fv-accent);color:var(--fv-text)}
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
.fv-timeline.fv-compact .fv-overview-row .fv-overview{height:22px}
.fv-timeline.fv-compact .fv-overview-row :is(.fv-ov,.fv-ov-window){transform:translateY(-2px)}
.fv-timeline.fv-compact .fv-ruler{height:24px}
.fv-timeline.fv-compact .fv-row{margin:0}
@container timeline (max-width:520px){.fv-timeline .fv-ruler-row,.fv-timeline .fv-track,.fv-timeline .fv-overview-row{grid-template-columns:var(--fv-tl-head-w-narrow,84px) minmax(0,1fr)}.fv-timeline .fv-clip{font-size:11px}.fv-timeline .fv-clip-len{display:none}.fv-timeline .fv-track-title{font-size:11px;padding:2px 4px}}
`
const laneCSS = `
.fv-timeline .fv-live-status{position:absolute;width:1px;height:1px;overflow:hidden;clip-path:inset(50%);white-space:nowrap}
@media(pointer:coarse){.fv-timeline .fv-disclosure summary{min-width:44px;min-height:44px}}
.fv-timeline .fv-head>*{flex-shrink:0}.fv-timeline .fv-head h3{flex-shrink:1}
.fv-timeline .fv-disclosure summary{cursor:pointer;list-style:none;display:flex;align-items:center;justify-content:center;width:30px;height:30px;border:1px solid color-mix(in srgb,currentColor 32%,transparent);border-radius:5px}
.fv-timeline .fv-disclosure summary::-webkit-details-marker{display:none}
.fv-timeline .fv-disclosure[open] .fv-popover{position:absolute;z-index:12;right:8px;margin-top:4px;padding:10px;border:1px solid color-mix(in srgb,currentColor 35%,transparent);border-radius:6px;background:var(--fv-tl-surface,var(--fv-surface,#202125));box-shadow:0 4px 16px #0005;max-width:min(440px,90vw)}
.fv-timeline .fv-lane-controls{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.fv-timeline .fv-lane-controls input[type=range]{width:100px;min-height:0;padding:0;accent-color:var(--fv-tl-accent,#579deb)}
.fv-timeline .fv-lane-controls output{font-variant-numeric:tabular-nums}
.fv-timeline[data-sized-lanes=true] .fv-track-title{min-height:0;height:var(--fv-lane-height);padding:0 4px}
.fv-timeline[data-sized-lanes=true] .fv-empty-note{display:none}
.fv-timeline[data-small-lanes=true] .fv-track-title{font-size:10px}
.fv-timeline[data-small-lanes=true] .fv-track-title svg{width:10px;height:10px}
.fv-timeline[data-small-lanes=true] .fv-clip{font-size:10px;padding:0 3px!important}
.fv-timeline[data-small-lanes=true] .fv-clip-len{display:none}
.fv-timeline .fv-trim{min-height:0}
`
const label = (data, fallback) => typeof data?.name === 'string' ? data.name : typeof data?.text === 'string' ? data.text : typeof data?.src === 'string' ? data.src : fallback
const trackIcon = kind => kind === 'audio' ? 'audio' : kind === 'subtitles' ? 'text' : kind === 'gfx' ? 'clip' : 'film'

const kindOfTrack = track => track.family === 'video' && track.data?.kind === 'gfx' ? 'gfx' : track.family
const trackKey = item => `${item.family}:${item.track_index}`
const trackId = track => track.family === 'subtitles' ? 'subtitles' : track.data?.id
const trackLabel = track => label(track.data, `${familyName(kindOfTrack(track))} ${track.track_index + 1}`)

export function VisualTimeline({ snapshot, result, hits, frame, onFrame, onSelect, activeSelection, onSelectionChange, selectionIntent, onGesture, onGestureState, gestureUnavailable, onOpenEdits, onOpenHistory, editActionLabel = '启用编辑', onUndo, compact = false, selectionTools, focusTool }) {
  const extent = React.useMemo(() => displayExtent(snapshot, result), [snapshot, result])
  const fps = snapshot.document?.project?.fps
  const clock = React.useMemo(() => projectClock(fps), [fps])
  const time = React.useCallback(value => formatClockTimecode(value, clock) ?? '时间码不可显示', [clock])
  const [window, setWindow] = useState()
  const [hover, setHover] = useState()
  const [snap, setSnap] = useState(true)
  const minLaneHeight = globalThis.window?.matchMedia?.('(pointer:coarse)').matches ? 24 : 14
  const [requestedLaneHeight, setLaneHeight] = useState(46)
  const laneHeight = Math.max(minLaneHeight, requestedLaneHeight)
  const [fitLanes, setFitLanes] = useState(false)
  const scrollport = useRef()
  const disclosureGroup = React.useId()
  // Measure the actual visual rows, including overlap lanes and empty tracks.
  // Border is excluded by clientHeight; padding, ruler and overview are not.
  useLayoutEffect(() => {
    if (!fitLanes || !scrollport.current) return
    const node = scrollport.current
    const measure = () => {
      const style = node.ownerDocument.defaultView.getComputedStyle(node)
      const fixed = [...node.children].filter(child => child.matches('.fv-overview-row,.fv-ruler-row')).reduce((n, child) => n + child.getBoundingClientRect().height, 0)
      let rows = 0, overhead = 0
      for (const track of node.querySelectorAll('.fv-track')) {
        const ts = node.ownerDocument.defaultView.getComputedStyle(track)
        overhead += (parseFloat(ts.paddingTop) || 0) + (parseFloat(ts.paddingBottom) || 0) + (parseFloat(ts.borderBottomWidth) || 0)
        const lanes = track.querySelectorAll('.fv-row')
        rows += Math.max(1, lanes.length)
        for (const lane of lanes) {
          const ls = node.ownerDocument.defaultView.getComputedStyle(lane)
          overhead += (parseFloat(ls.marginTop) || 0) + (parseFloat(ls.marginBottom) || 0)
        }
      }
      const available = node.clientHeight - fixed - (parseFloat(style.paddingTop) || 0) - (parseFloat(style.paddingBottom) || 0) - overhead
      if (node.clientHeight && rows) setLaneHeight(Math.max(minLaneHeight, Math.min(46, Math.floor(available / rows))))
    }
    measure()
    const Observer = node.ownerDocument.defaultView.ResizeObserver ?? globalThis.ResizeObserver
    const observer = Observer ? new Observer(measure) : undefined
    observer?.observe(node)
    return () => observer?.disconnect()
  }, [fitLanes, result, compact, minLaneHeight])
  // Standalone presentation consumers can remain uncontrolled. PreviewBody always
  // supplies its single intent; this fallback is never a second product selection.
  const [localIntent, setLocalIntent] = useState(emptySelection())
  const intent = selectionIntent ?? localIntent
  const selected = React.useMemo(() => new Set(intent.items.map(item => `${item.kind}:${item.address}`)), [intent])
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
  const [feedback, setFeedback] = useState()
  const [saving, setSaving] = useState()
  const directOwner = useRef()
  useLayoutEffect(() => () => { directOwner.current = undefined }, [])
  function releaseGesture(g) {
    try { if (g.node.hasPointerCapture?.(g.pointer)) g.node.releasePointerCapture(g.pointer) } catch { /* detached/lost capture */ }
  }
  function cancelGesture(event) {
    const g = gesture.current
    if (!g || (event && ((event.pointerId !== undefined && g.pointer !== event.pointerId) || g.node !== event.currentTarget))) return
    gesture.current = undefined
    pointerTails.current.add(g.pointer)
    setGhost(undefined)
    if (g.moved) setFeedback({ text: '拖动已取消。' })
    g.publish?.(g.token, false)
    releaseGesture(g)
  }
  useLayoutEffect(() => () => cancelGesture(), [snapshot.content_version, onGesture, onGestureState, gestureUnavailable, intent, frame, window, snap])
  useEffect(() => { setHover(undefined); setLocalIntent(emptySelection()); drag.current = undefined; overviewPointer.current = undefined }, [snapshot.content_version])
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
  const view = React.useMemo(() => window && !extent.diagnostic ? windowFor(extent.end, window.start, window.end - window.start) : extent, [window, extent])
  const pinned = safeFrame(frame) ? frame : undefined
  const { groups, tracksByKey, displayTracks } = React.useMemo(() => {
    const groups = new Map()
    const tracksByKey = new Map(result.tracks.map(track => [trackKey(track), track]))
    // Lift subtitle lanes without changing kernel order for selection or editing.
    const displayTracks = [
      ...result.tracks.filter(track => track.family === 'subtitles'),
      ...result.tracks.filter(track => track.family !== 'subtitles'),
    ]
    for (const block of result.blocks) {
      const key = trackKey(block)
      if (!groups.has(key)) groups.set(key, [])
      groups.get(key).push(block)
    }
    return { groups, tracksByKey, displayTracks }
  }, [result])
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
  const selectedBlocks = React.useMemo(() => result.blocks.filter(block => selected.has(`block:${block.address}`)), [result, selected])
  const selectionZoom = !extent.diagnostic && selectedBlocks.length ? windowFor(extent.end, Math.min(...selectedBlocks.map(block => block.range[0])), Math.max(1, Math.max(...selectedBlocks.map(block => block.range[1])) - Math.min(...selectedBlocks.map(block => block.range[0])))) : undefined
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
    if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey) return
    if (mode !== 'move' && event.pointerType === 'touch') {
      setFeedback({ problem: true, text: '触控指针不支持拖动裁剪，请在属性面板输入帧号。' })
      return
    }
    if (directOwner.current) return
    const origin = pointerFrame(event.clientX, ruler.current.getBoundingClientRect(), view)
    if (origin === undefined) return
    const moving = mode === 'move' && selected.has(`block:${block.address}`) ? selectedBlocks : [block]
    const allowed = onGesture && moving.every(item => typeof item.data?.id === 'string')
    const next = { pointer: event.pointerId, node: event.currentTarget, origin, x: event.clientX, y: event.clientY, block, track, mode, moving, token: {},
      reason: !onGesture ? gestureUnavailable ?? '请先启用编辑。' : !allowed ? '此片段不可编辑。' : undefined,
      publish: allowed ? onGestureState : undefined }
    const previous = gesture.current
    gesture.current = next
    try { event.currentTarget.setPointerCapture?.(event.pointerId) }
    catch { gesture.current = previous; setFeedback({ problem: true, text: '未能开始拖动，请重试。' }); return }
    next.publish?.(next.token, true)
    if (previous && previous.pointer !== next.pointer) {
      pointerTails.current.add(previous.pointer)
      previous.publish?.(previous.token, false)
      releaseGesture(previous)
    }
    setGhost(undefined)
    setFeedback(undefined)
  }
  function updateGesture(event) {
    const g = gesture.current
    if (!g || g.pointer !== event.pointerId || g.node !== event.currentTarget) return
    const rect = ruler.current.getBoundingClientRect()
    const at = pointerFrame(event.clientX, rect, view)
    if (at === undefined) return
    if (!g.moved && Math.max(Math.abs(event.clientX - g.x), Math.abs(event.clientY - g.y)) < 3) return g
    g.moved = true
    let target = g.track
    if (g.mode === 'move') {
      target = undefined
      for (const [key, node] of trackNodes.current) {
        const box = node.getBoundingClientRect()
        if (event.clientY >= box.top && event.clientY < box.bottom) { target = tracksByKey.get(key); break }
      }
    }
    let placed = { delta: at - g.origin }
    const within = delta => g.mode === 'move'
      ? g.moving.every(({ range }) => safeFrame(range[0] + delta) && safeFrame(range[1] + delta) && range[1] + delta <= extent.end)
      : g.mode === 'left' ? delta >= 0 && delta < g.block.range[1] - g.block.range[0] : delta <= 0 && -delta < g.block.range[1] - g.block.range[0]
    if (snap && !event.altKey && !g.reason && target && (placed.delta !== 0 || target !== g.track)) {
      placed = snapDelta({ delta: placed.delta, range: g.block.range, mode: g.mode, playhead: pinned, extent: extent.end, span, width: rect.width,
        neighbors: (groups.get(trackKey(target)) ?? []).filter(block => !g.moving.includes(block)), accepts: within })
    }
    const { delta, frame: snapFrame } = placed
    let problem = g.reason
    let placements = [], operations = []
    if (!problem && g.mode === 'move') {
      if (!target) problem = '请把片段放到轨道内。'
      else {
        placements = g.moving.map(block => {
          const track = trackKey(block) === trackKey(g.track) ? target : tracksByKey.get(trackKey(block))
          return { block, track, range: block.range.map(value => value + delta) }
        })
        if (kindOfTrack(target) !== kindOfTrack(g.track) || !trackId(target)) problem = '只能移到同类型的可编辑轨道。'
        else if (placements.some(({ range }) => !range.every(safeFrame) || range[1] > extent.end)) problem = '片段不能超出工程范围。'
        else if (delta || target !== g.track) operations = placements.map(({ block, track, range }) => ({ op: 'block.move', id: block.data.id, track_id: trackId(track), at: range[0] }))
      }
    } else if (!problem) {
      const [start, end] = g.block.range
      const range = [start + (g.mode === 'left' ? delta : 0), end + (g.mode === 'right' ? delta : 0)]
      if (range[0] < start || range[1] > end || range[0] >= range[1]) problem = '只能向内裁剪，至少保留 1 帧。'
      else {
        placements = [{ block: g.block, track: g.track, range }]
        if (delta) operations = [{ op: 'block.trim', id: g.block.data.id, start: range[0], end: range[1], fade_policy: 'preserve-content' }]
      }
    }
    const primary = placements.find(entry => entry.block === g.block)
    const text = problem || (primary ? `${g.mode === 'move' ? trackLabel(primary.track) : '裁剪'} · ${time(primary.range[0])}–${time(primary.range[1])}` : '')
    const next = { ...g, placements, operations, problem, text, snapFrame }
    setGhost(next)
    return next
  }
  function endGesture(event) {
    const g = updateGesture(event)
    if (!g) return
    gesture.current = undefined
    setGhost(undefined)
    try {
      if (!g.moved) return
      pointerTails.current.add(g.pointer)
      if (g.problem) { setFeedback({ problem: true, text: g.problem, open: Boolean(g.reason) }); return }
      if (!g.operations.length) { setFeedback({ text: '位置未变化，可放大后逐帧调整。' }); return }
      applyDirect(g.operations, g.placements, g.block.data.id)
    } catch (error) { setFeedback({ problem: true, text: error.message, open: true }) }
    finally {
      g.publish?.(g.token, false)
      releaseGesture(g)
    }
  }
  function applyDirect(operations, placements = [], focusId, label = '已保存。') {
    if (directOwner.current) return
    if (!onGesture) { setFeedback({ problem: true, text: gestureUnavailable || '请先启用编辑。', open: true }); return }
    const token = {}
    directOwner.current = token
    setSaving({ placements })
    setFeedback(undefined)
    let submitted
    try { submitted = onGesture(operations, { focusId }) }
    catch (error) { finish(error); return }
    // The callback takes ownership synchronously before we release the pointer
    // and preview exclusion; the promise covers plan, write, and fresh readback.
    void Promise.resolve(submitted).then(() => finish(), finish)
    function finish(error) {
      if (directOwner.current !== token) return
      directOwner.current = undefined
      setSaving(undefined)
      setFeedback(error ? { problem: !error.applied, text: error.message, history: error.outcomeUnknown || error.recoveryRequired } : { text: label })
    }
  }
  function gapControl(track, range) {
    const geometry = clipGeometry(range, view)
    if (!geometry || geometry.width <= 0 || !trackId(track)) return null
    const duration = range[1] - range[0]
    const name = `闭合空隙 · ${trackLabel(track)} · ${duration} 帧`
    return h('button', { key: `gap:${range[0]}`, type: 'button', className: 'fv-gap', disabled: Boolean(saving),
      style: { left: `${geometry.left}%`, width: `${geometry.width}%` }, 'aria-label': name,
      title: `${name}；仅将当前轨道后续片段向左移动`, onClick: () => applyDirect([closeGapIntent(track, range)], [], undefined, `已闭合 ${duration} 帧空隙。`) },
      h('span', { 'aria-hidden': true }, '↔'))
  }
  function placement(entry, kind) {
    const { range, block } = entry
    const geometry = range.every(safeFrame) && range[0] < range[1] ? clipGeometry(range, view) : undefined
    return geometry ? h('span', { key: `${kind}:${block.address}`, className: 'fv-placement', 'data-kind': kind, 'aria-hidden': true,
      style: { left: `${geometry.left}%`, width: `${geometry.width}%` } }) : null
  }
  const lines = () => [
    pinned !== undefined && pinned >= view.start && pinned < view.end ? h('span', { key: 'pin', className: 'fv-line', style: { left: `${framePercent(pinned, view)}%` }, 'aria-hidden': true }) : null,
    ghost && !ghost.problem && safeFrame(ghost.snapFrame) && ghost.snapFrame >= view.start && ghost.snapFrame <= view.end ? h('span', { key: 'snap', className: 'fv-line fv-snap-line', style: { left: `${framePercent(ghost.snapFrame, view)}%` }, 'aria-hidden': true }) : null,
    hover !== undefined && hover >= view.start && hover < view.end ? h('span', { key: 'hover', className: 'fv-line fv-hover', style: { left: `${framePercent(hover, view)}%` }, 'aria-hidden': true }) : null,
  ]
  // Pointer feedback never changes saved clips. Reuse their nodes while
  // dispatching events through the latest committed view and authorization.
  const actions = useRef()
  useLayoutEffect(() => { actions.current = { beginGesture, updateGesture, endGesture, cancelGesture, pick } })
  const editable = Boolean(onGesture)
  const rowsByTrack = React.useMemo(() => {
    if (extent.diagnostic) return new Map()
    const hitSet = hits ? new Set(hits.blocks.map(block => block.address)) : undefined
    const pending = new Set(saving?.placements.map(item => item.block.address))
    return new Map(displayTracks.map(track => {
      const key = trackKey(track), kind = kindOfTrack(track)
      const blocks = groups.get(key) ?? []
      const rows = blocks.length ? displayLanes(blocks) : [[]]
      return [key, rows.map(row => row.map(block => {
        const geometry = clipGeometry(block.range, view)
        if (!geometry) return null
        const text = label(block.data, block.address)
        const duration = block.range[1] - block.range[0]
        const events = mode => ({ onPointerDown: e => actions.current.beginGesture(e, block, track, mode), onPointerMove: e => actions.current.updateGesture(e), onPointerUp: e => actions.current.endGesture(e), onPointerCancel: e => actions.current.cancelGesture(e), onLostPointerCapture: e => actions.current.cancelGesture(e) })
        const button = h('button', { key: 'body', type: 'button', className: `fv-clip fv-${kind}`, style: { left: `${geometry.left}%`, width: `${geometry.width}%`, maxWidth: `${100 - geometry.left}%` }, 'data-movable': Boolean(editable && block.data?.id), 'data-pending': pending.has(block.address),
          title: `${text}\n${block.address} · [${time(block.range[0])}, ${time(block.range[1])}) · 长度 ${time(duration)}`, 'aria-label': `选择块 ${block.address} · ${text} · 帧 [${block.range[0]}, ${block.range[1]})`,
          'aria-pressed': selected.has(`block:${block.address}`), 'data-hit': hitSet?.has(block.address) ?? false,
          onDragStart: event => event.preventDefault(), onClick: event => actions.current.pick({ kind: 'block', address: block.address }, event), ...events('move'),
        }, h('span', { className: 'fv-clip-name' }, text), Number.isSafeInteger(duration) && duration >= 0 ? h('span', { className: 'fv-clip-len' }, time(duration)) : null)
        const handles = editable && block.data?.id && kind !== 'gfx' ? ['left','right'].filter(mode => mode === 'left' ? block.range[0] >= view.start : block.range[1] <= view.end).map(mode => h('button',{ key:mode,type:'button',className:'fv-trim','aria-label':`${mode === 'left' ? '左' : '右'} trim 手柄 ${block.address}`,title:'向内拖动以裁剪',style:{position:'absolute',top:'var(--fv-clip-top)',left:`${mode === 'left' ? geometry.left : geometry.left+geometry.width}%`,transform:mode==='right'?'translateX(-100%)':undefined,width:`min(7px, ${geometry.width / 4}%)`,minWidth:0,height:'var(--fv-clip-height)',padding:0,zIndex:3,touchAction:'none',cursor:'ew-resize'},...events(mode)})) : []
        return h(React.Fragment,{key:block.address},button,...handles)
      }))]
    }))
  }, [extent, displayTracks, groups, view, time, editable, saving, selected, hits])
  const overviewClips = React.useMemo(() => {
    if (extent.diagnostic) return null
    return result.blocks.map(block => {
      const geometry = clipGeometry(block.range, extent)
      if (!geometry) return null
      return h('span', { key: block.address, className: `fv-ov fv-ov-${kindOfTrack(tracksByKey.get(trackKey(block)) ?? block)}`, style: { left: `${geometry.left}%`, width: `${Math.max(geometry.width, 0.4)}%` }, title: `${label(block.data, block.address)} · [${time(block.range[0])}, ${time(block.range[1])})`, 'aria-hidden': true })
    })
  }, [result, extent, fps, tracksByKey])
  if (extent.diagnostic) return h('p', { role: 'alert' }, extent.diagnostic)
  return h('section', { className: compact ? 'fv-timeline fv-compact' : 'fv-timeline', 'data-small-lanes': laneHeight < 30, 'data-sized-lanes': fitLanes || laneHeight < 46, style: { '--fv-lane-height': `${laneHeight}px`, '--fv-clip-top': `${Math.min(3, Math.floor(laneHeight / 10))}px`, '--fv-clip-height': `${laneHeight - 2 * Math.min(3, Math.floor(laneHeight / 10))}px` }, 'aria-label': '完整只读可视时间轴', 'data-dragging': Boolean(ghost && !ghost.problem),
    onPointerDownCapture: event => pointerTails.current.delete(event.pointerId),
    onClickCapture: event => {
      if (event.detail === 0 || !pointerTails.current.delete(event.nativeEvent.pointerId)) return
      event.preventDefault(); event.stopPropagation()
    },
  },
    h('style', null, css + laneCSS + iconControlCSS),
    h('div', { className: 'fv-head' },
      h('h3', { title: label(snapshot.document?.project, '时间线') }, label(snapshot.document?.project, '时间线')),
      focusTool,
      compact ? h('div', { className: 'fv-tools', role: 'group', 'aria-label': '直接剪辑' },
        h(ToolButton, { icon: 'magnet', label: '吸附', tooltip: '吸附到同轨片段边缘或播放头 · 按住 Alt 临时关闭', 'aria-pressed': snap, onClick: () => setSnap(value => !value) }),
        h(ToolButton, { icon: 'undo', label: '撤销上次编辑', disabled: !onUndo || Boolean(saving), tooltip: '单步撤销最近一次保存', onClick: () => { setFeedback(undefined); void onUndo?.().then(answer => setFeedback(answer?.ok ? { text: '已撤销。' } : { problem: true, text: '未能确认撤销，请查看历史与恢复。', history: true }), error => setFeedback({ problem: true, text: error.message, history: error.outcomeUnknown || error.recoveryRequired })) } })) : null,
      h('details', { className: 'fv-disclosure', name: disclosureGroup },
        h('summary', { 'aria-label': '轨道高度', title: '轨道高度与适配全部轨道' }, h(Icon, { name: 'settings', size: 15 })),
        h('div', { className: 'fv-popover fv-lane-controls' },
          h('label', null, '轨道高度 ', h('input', { type: 'range', min: minLaneHeight, max: 46, value: laneHeight, 'aria-label': '轨道高度', onChange: event => { setFitLanes(false); setLaneHeight(Number(event.target.value)) } })),
          h('output', null, `${laneHeight}px`),
          h('button', { type: 'button', 'aria-pressed': fitLanes, onClick: () => setFitLanes(true) }, '适配全部轨道'))),
      selectionTools,
      h('div', { className: 'fv-tools', role: 'group', 'aria-label': '显示范围' },
        h(ToolButton, { icon: 'fit', label: '适配全片', tooltip: '窗口回到全片', onClick: () => setWindow(undefined) }),
        h(ToolButton, { icon: 'zoomIn', label: '放大', tooltip: '窗口放大一倍精度', disabled: view.end - view.start <= 1, onClick: () => setWindow(zoomWindow(view, extent.end, true)) }),
        h(ToolButton, { icon: 'zoomOut', label: '缩小', tooltip: '窗口缩小一半', disabled: view.end - view.start >= extent.end, onClick: () => setWindow(zoomWindow(view, extent.end, false)) }),
        h(ToolButton, { icon: 'back', label: '向前平移', tooltip: '窗口向前平移半屏', disabled: view.start === 0, onClick: () => setWindow(panWindow(view, extent.end, -1)) }),
        h(ToolButton, { icon: 'next', label: '向后平移', tooltip: '窗口向后平移半屏', disabled: view.end === extent.end, onClick: () => setWindow(panWindow(view, extent.end, 1)) }),
        h(ToolButton, { icon: 'clip', label: '缩放到选区', tooltip: '窗口缩放到已选块的存储帧范围（只改显示）', disabled: !selectionZoom, onClick: () => selectionZoom && setWindow(selectionZoom) })),
      h('div', { className: 'fv-tools', role: 'group', 'aria-label': '播放头步进' },
        h(ToolButton, { icon: 'previous', label: '前一帧', tooltip: '播放头后退一帧', disabled: !onFrame || pinned === undefined, onClick: () => step(-1) }),
        h(ToolButton, { icon: 'next', label: '后一帧', tooltip: '播放头前进一帧', disabled: !onFrame || pinned === undefined, onClick: () => step(1) })),
      h('details', { className: 'fv-disclosure', name: disclosureGroup },
        h('summary', { 'aria-label': '时间线状态', title: '时间线状态与诊断' }, h(Icon, { name: 'info', size: 15 })),
        h('div', { className: 'fv-popover' },
          h('span', { className: 'fv-badge' }, onGesture ? '可编辑' : '只读'),
    h('div', { className: 'fv-status', role: 'status' },
      h('span', { 'aria-label': '可见帧范围' }, `时间码窗口 [${time(view.start)}, ${time(view.end)}) / 全片 [${time(0)}, ${time(extent.end)})`),
      h('span', null, `播放头 ${pinned === undefined ? '未固定' : time(pinned)}`),
      h('span', null, `悬停时间码：${hover === undefined ? '—' : time(hover)}`),
      clock?.den === 1001 ? h('span', null, `NTSC non-drop · 名义 ${clock.nominal} fps（不是墙钟）`) : null,
      h('span', null, `已选 ${selected.size}`),
      h('span', { 'aria-label': '活动引用' }, `活动引用：${activeSelection ? activeSelection.address : '无'}`))))),
    h('span', { className: 'fv-live-status', role: 'status', 'aria-atomic': true }, `播放头 ${pinned === undefined ? '未固定' : time(pinned)} · 已选 ${selected.size}`),
    ghost || saving || feedback ? h('div', { className: 'fv-drag-feedback', 'aria-label': ghost ? '未保存拖动 ghost' : undefined },
      h('span', { role: ghost?.problem || (!ghost && feedback?.problem) ? 'alert' : 'status' }, ghost ? `${ghost.text}${safeFrame(ghost.snapFrame) && !ghost.problem ? ' · 已吸附' : ''}` : saving ? '正在保存…' : feedback.text),
      !ghost && !saving && feedback?.history && onOpenHistory ? h('button', { type: 'button', onClick: onOpenHistory }, '历史与恢复') : !ghost && !saving && feedback?.open && onOpenEdits ? h('button', { type: 'button', onClick: () => { setFeedback(undefined); onOpenEdits() } }, editActionLabel) : null) : null,
    h('div', { className: 'fv-scroll', ref: scrollport, tabIndex: 0, role: 'region', 'aria-label': '轨道垂直滚动区' },
      h('div', { className: 'fv-overview-row' },
        h('div', { className: 'fv-corner', 'aria-hidden': true }, '全片'),
        h('div', { className: 'fv-overview', ref: overview, role: 'slider', tabIndex: 0, 'aria-label': '全片导航条（只平移/缩放显示窗口）', 'aria-valuemin': 0, 'aria-valuemax': Math.max(0, extent.end - span), 'aria-valuenow': view.start, 'aria-valuetext': `窗口 [${time(view.start)}, ${time(view.end)}) / 全片 [${time(0)}, ${time(extent.end)})`,
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
          overviewClips,
          h('span', { className: 'fv-ov-window', style: { left: `${framePercent(view.start, extent)}%`, width: `${Math.max(framePercent(view.end, extent) - framePercent(view.start, extent), 0.6)}%` }, 'aria-hidden': true }),
          pinned !== undefined ? h('span', { className: 'fv-ov-pin', style: { left: `${framePercent(pinned, extent)}%` }, 'aria-hidden': true }) : null)),
      h('div', { className: 'fv-ruler-row' },
        h('div', { className: 'fv-corner', 'aria-hidden': true }, '时间码'),
        h('div', { className: 'fv-ruler', ref: ruler, role: 'slider', tabIndex: 0, 'aria-label': '固定播放头帧标尺', 'aria-valuemin': 0, 'aria-valuemax': extent.end - 1, ...(pinned === undefined ? { 'aria-valuetext': '未设置，按 Home 设置为首帧' } : { 'aria-valuenow': pinned, 'aria-valuetext': time(pinned) }),
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
        }, frameTicks(view).map((tick, index, all) => h('span', { key: tick, className: 'fv-tick', style: { left: `${framePercent(tick, view)}%`, transform: index === all.length - 1 ? 'translateX(-100%)' : undefined }, 'aria-hidden': true }, time(tick))), lines())),
      displayTracks.map(track => {
        const key = `${track.family}:${track.track_index}`
        const blocks = groups.get(key) ?? []
        const kind = kindOfTrack(track)
        const name = trackLabel(track)
        const trackSelection = { kind: 'track', address: track.address }
        return h('section', { key, ref: node => { if (node) trackNodes.current.set(key, node); else trackNodes.current.delete(key) }, className: 'fv-track', 'aria-label': name },
          h('div', { className: 'fv-track-head' },
            h('button', { className: 'fv-track-title', type: 'button', title: name, 'aria-label': `选择轨道 ${track.address}`, 'aria-pressed': selected.has(`track:${track.address}`), onClick: event => pick(trackSelection, event) }, h(Icon, { name: trackIcon(kind), size: 13 }), h('span', { className: 'fv-track-name' }, name)),
            blocks.length === 0 ? h('span', { className: 'fv-empty-note', title: '空轨道（kernel 未返回存储块）' }, '空轨道') : null),
          h('div', { className: 'fv-lanes' },
            rowsByTrack.get(key).map((row, index) => h('div', { className: 'fv-row', key: index, 'aria-label': `显示子行 ${index + 1}（不是新轨道）` },
              row, index === 0 ? saving?.placements.filter(entry => trackKey(entry.track) === key).map(entry => placement(entry, 'pending')) : null,
              index === 0 ? ghost?.placements?.filter(entry => trackKey(entry.track) === key).map(entry => placement(entry, ghost.problem ? 'blocked' : 'drag')) : null,
              index === 0 && compact && !ghost && !saving ? internalGaps(blocks).map(range => gapControl(track, range)) : null, lines()))))
      }), result.blocks.length === 0 ? h('p', { style: { margin: '6px' } }, '空项目：暂无片段。') : null))
}
