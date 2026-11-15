/** Display geometry only. Inputs are kernel-emitted frame ranges, never source clips. */
export const safeFrame = value => Number.isSafeInteger(value) && value >= 0
const big = BigInt
const clamp = (n, lo, hi) => n < lo ? lo : n > hi ? hi : n

export function displayExtent(snapshot, result) {
  const duration = snapshot.document?.project?.duration_frames
  if (!safeFrame(duration) || duration === 0) return { diagnostic: 'DISPLAY_PRECISION: 缺少可安全显示的 duration_frames（需正安全整数）；不舍入/截断。完整 raw/诊断仍在下方。' }
  for (const block of result.blocks) {
    if (!Array.isArray(block.range) || block.range.length !== 2 || !block.range.every(safeFrame) || block.range[0] >= block.range[1] || block.range[1] > duration) {
      return { diagnostic: 'DISPLAY_PRECISION: kernel range 超出安全显示边界；不推算、不舍入/截断。完整 raw/诊断仍在下方。' }
    }
  }
  return { start: 0, end: duration }
}

export function windowFor(extent, start, span) {
  if (![extent, start, span].every(safeFrame) || extent < 1 || span < 1) throw new Error('DISPLAY_PRECISION: unsafe viewport')
  const size = Math.min(extent, span)
  const left = Math.min(start, extent - size)
  return { start: left, end: left + size }
}

export function zoomWindow(view, extent, zoomIn) {
  const span = big(view.end) - big(view.start)
  const size = zoomIn ? (span + 1n) / 2n : span * 2n
  const bounded = clamp(size, 1n, big(extent))
  const center = big(view.start) + span / 2n
  return windowFor(extent, Number(clamp(center - bounded / 2n, 0n, big(extent) - bounded)), Number(bounded))
}

export function panWindow(view, extent, direction) {
  const span = big(view.end) - big(view.start)
  const shift = span / 2n || 1n
  return windowFor(extent, Number(clamp(big(view.start) + big(direction) * shift, 0n, big(extent) - span)), Number(span))
}

// Subtract before conversion: high absolute frame positions never go through a float product.
export function framePercent(frame, view) {
  const offset = clamp(big(frame), big(view.start), big(view.end)) - big(view.start)
  return Number(offset * 1000000n / (big(view.end) - big(view.start))) / 10000
}

export function clipGeometry(range, view) {
  if (range[1] <= view.start || range[0] >= view.end) return null
  const left = framePercent(range[0], view)
  return { left, width: framePercent(range[1], view) - left }
}

export function pointerFrame(clientX, rect, view) {
  if (!Number.isFinite(clientX) || !Number.isFinite(rect.left) || !Number.isFinite(rect.width) || rect.width <= 0) return undefined
  // Pointer coordinates are display samples, quantized to 1/1024 CSS pixel, not render evidence.
  const width = Math.round(Math.min(rect.width, 1000000) * 1024)
  if (width < 1) return undefined
  const x = Math.round(clamp(clientX - rect.left, 0, rect.width) / rect.width * width)
  const span = big(view.end) - big(view.start)
  return Number(big(view.start) + clamp(big(x) * span / big(width), 0n, span - 1n))
}

export function frameTicks(view, divisions = 4) {
  const ticks = []
  const span = big(view.end) - big(view.start)
  let previous
  for (let i = 0; i <= divisions; i++) {
    const frame = Number(big(view.start) + span * big(i) / big(divisions))
    if (frame !== previous) ticks.push(frame)
    previous = frame
  }
  return ticks
}

/** Greedy display-only subrows. Never changes track identity, order, range or query membership. */
export function displayLanes(blocks) {
  const rows = [], ends = []
  const ordered = blocks.slice().sort((a, b) => a.range[0] - b.range[0] || a.block_index - b.block_index)
  for (const block of ordered) {
    let lane = 0
    while (lane < ends.length && ends[lane] > block.range[0]) lane++
    if (!rows[lane]) rows[lane] = []
    rows[lane].push(block)
    ends[lane] = block.range[1]
  }
  return rows
}
