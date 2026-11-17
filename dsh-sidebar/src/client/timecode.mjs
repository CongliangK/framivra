import { fpsMatchesReference } from './frame.mjs'

const integers = [24, 25, 30, 48, 50, 60, 100, 120]
const maximum = BigInt(Number.MAX_SAFE_INTEGER)
const safeFrame = frame => Number.isSafeInteger(frame) && frame >= 0

/** Saved core FPS identity only; unknown clocks must not silently become 30 fps. */
export function projectClock(fps) {
  for (const nominal of integers) if (fpsMatchesReference(nominal, 1, fps)) return { num: nominal, den: 1, nominal }
  for (const nominal of [24, 30, 48, 60, 120]) if (fpsMatchesReference(nominal * 1000, 1001, fps)) return { num: nominal * 1000, den: 1001, nominal }
  return null
}

/** mm:ss:frame, unlimited minutes. NTSC is explicitly nominal non-drop, not wall time. */
export function formatTimecode(frame, fps) {
  const clock = projectClock(fps)
  if (!clock || !safeFrame(frame)) return null
  const value = BigInt(frame), nominal = BigInt(clock.nominal), seconds = value / nominal
  return `${String(seconds / 60n).padStart(2, '0')}:${String(seconds % 60n).padStart(2, '0')}:${String(value % nominal).padStart(String(clock.nominal - 1).length, '0')}`
}

/** Accept exactly the formatter's reversible canonical spelling, within safe integer frames. */
export function parseTimecode(text, fps) {
  const clock = projectClock(fps)
  if (!clock || typeof text !== 'string' || text.length > 32 || !/^[0-9]{2,}:[0-9]{2}:[0-9]{2,3}$/.test(text)) return null
  const [minutes, seconds, frames] = text.split(':').map(BigInt)
  if (seconds >= 60n || frames >= BigInt(clock.nominal)) return null
  const value = (minutes * 60n + seconds) * BigInt(clock.nominal) + frames
  if (value > maximum) return null
  const frame = Number(value)
  return formatTimecode(frame, fps) === text ? frame : null
}

/** Actual rational project seconds (unlike nominal non-drop timecode labels). */
export function frameToSeconds(frame, fps) {
  const clock = projectClock(fps)
  return clock && safeFrame(frame) ? Number(BigInt(frame) * BigInt(clock.den)) / clock.num : null
}

/** Containing frame, not nearest frame. Snap only floating-point frame-boundary noise. */
export function frameFromSeconds(seconds, fps) {
  const clock = projectClock(fps)
  if (!clock || typeof seconds !== 'number' || !Number.isFinite(seconds) || seconds < 0) return null
  const scaled = seconds * clock.num / clock.den, nearest = Math.round(scaled)
  // The cap prevents large magnitudes from admitting meaningful sub-frame advances.
  const tolerance = Math.min(1e-7, 2 * Number.EPSILON * Math.max(1, Math.abs(scaled)))
  const frame = Math.floor(Math.abs(scaled - nearest) <= tolerance ? nearest : scaled)
  return safeFrame(frame) ? frame : null
}
