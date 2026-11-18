import { safeFrame } from './viewport.mjs'

// Only same-version stored ranges participate. Leading/trailing space and
// space occupied on another visual subrow of the same track are not gaps.
export function internalGaps(blocks) {
  const ordered = blocks.map(block => block.range).slice().sort((a, b) => a[0] - b[0])
  const gaps = []
  let end
  for (const [start, stop] of ordered) {
    if (end !== undefined && start > end) gaps.push([end, start])
    end = end === undefined ? stop : Math.max(stop, end)
  }
  return gaps
}

export function closeGapIntent(track, [start, end]) {
  return { op: 'range.ripple', mode: 'delete', start, end, crossing: 'reject', resize: false,
    scope: { track_ids: track.family === 'subtitles' ? [] : [track.data.id], subtitles: track.family === 'subtitles' } }
}

/** Best alignment within an 8 CSS-pixel radius. Move snaps facing edges on
 * the destination track; trims and the playhead can align to either edge.
 * `accepts` excludes positions outside bounds without silently clamping. */
export function snapDelta({ delta, range, mode, neighbors, playhead, extent, span, width, accepts }) {
  let best
  const radius = 8 * span / width
  function consider(edge, frame) {
    if (!safeFrame(frame)) return
    const next = frame - edge, distance = Math.abs(next - delta)
    if (distance > radius || (best && distance >= best.distance) || !accepts(next)) return
    best = { delta: next, frame, distance }
  }
  for (const edge of mode === 'left' ? [range[0]] : mode === 'right' ? [range[1]] : range) consider(edge, playhead)
  if (mode === 'move') {
    consider(range[0], 0); consider(range[1], extent)
    for (const block of neighbors) { consider(range[0], block.range[1]); consider(range[1], block.range[0]) }
  } else {
    const edge = range[mode === 'left' ? 0 : 1]
    for (const block of neighbors) { consider(edge, block.range[0]); consider(edge, block.range[1]) }
  }
  return best ?? { delta }
}
