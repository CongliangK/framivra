/** Adapter only: timeline interpretation and selection belong exclusively to vtx. */
import { createHash, randomBytes } from 'node:crypto'
import { dirname, isAbsolute } from 'node:path'
import { addAbortSignal, Readable } from 'node:stream'
import { crc32, inflateSync } from 'node:zlib'
import { boundedText, FRAME_WIRE_LIMIT } from './bounded.mjs'
import { parseJSON, stringifyJSON, LosslessJSONError } from './json.mjs'
import { readConfiguration, isVolatileConfig, ConfigurationError, PREVIEW_SUPPLIER_ABI } from './configuration.mjs'

export class PreviewError extends Error {
  constructor(code, message, status = 400) {
    super(message)
    this.code = code
    this.status = status
  }
}
export const fail = (code, message, status) => { throw new PreviewError(code, message, status) }

/**
 * Host protocol/build identity used for verifiable client↔host negotiation.
 * `ops` lists supported control ops and the independent preview-window route. A NEWER client on an
 * OLDER running host gets BAD_GRANT for ops the old host never knew (its
 * grant gate runs before BAD_OPERATION); with this identity the client can
 * distinguish "host too old, refresh/restart" from a real authorization
 * failure. Keep `plugin` in sync with package.json version.
 */
export const HOST_PROTOCOL_OPS = [
  'hello', 'open', 'browse', 'close',
  'authorize-write', 'revoke-write', 'invalidate-plan', 'cancel-write',
  'plan', 'commit', 'status', 'recover', 'adopt', 'undo', 'edit-view',
  'inspect', 'query', 'reference',
  'storage-inspect', 'storage-plan', 'storage-clean', 'storage-cancel', 'preview-window',
]
export const HOST_IDENTITY = { plugin: '0.3.1', protocol: 1, ops: HOST_PROTOCOL_OPS }

/**
 * Deterministic build fingerprint inputs — the single home for WHICH source
 * files define a build and the packaged-stamp marker format. The Host half
 * (src/host.mjs resolveBuildStamp) and the client build (scripts/build.mjs)
 * both hash this exact file list with the same recipe, so a client bundle and
 * the host it was shipped with carry the same stamp; scripts/package.mjs
 * replaces the BAKED_BUILD_STAMP literal below with the packaged value.
 * Any content change to any listed module changes the next build's stamp —
 * identity no longer depends on a hand-maintained version string.
 */
export const BUILD_STAMP_MARKER = '__FRAMIVRA_BUILD_STAMP__'
// packaged builds replace this literal (keep it identical to BUILD_STAMP_MARKER)
export const BAKED_BUILD_STAMP = '__FRAMIVRA_BUILD_STAMP__'
/** Host-side modules that participate in the build stamp, in fixed order. */
export const HOST_LIB_MODULES = ['configuration', 'kernel-config', 'kernel-management', 'protocol', 'bounded', 'json', 'media', 'agent', 'tools', 'host', 'index']
/** Client-side modules that participate in the build stamp, in fixed order. */
export const CLIENT_MODULES = [
  'icons', 'icon-controls', 'editor-controls', 'viewport', 'timeline', 'manual',
  'selection', 'reference', 'draft-summary', 'frame', 'edit-review', 'settings', 'kernel-settings', 'draft-services',
  'grant-lifecycle', 'official-draft', 'timecode', 'monitor', 'preview-session', 'preview-session-access', 'preview-program-run', 'preview-program-playback', 'workbench', 'storage', 'index',
]
/** Stamp recipe: prefix + first 16 hex of sha256 over the per-file sha256
 *  digests of the modules above (joined with '\n'), in the listed order.
 *  Implemented with node:crypto on both the Host and the build script. */
export function composeBuildStamp(prefix, combinedDigestHex) {
  return `${prefix}${String(combinedDigestHex).slice(0, 16)}`
}
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value) && !JSON.isRawJSON(value)
const integer = value => Number.isSafeInteger(value) && value >= 0

export const prepareGfxCapable = value => {
  const prepare = value?.capabilities?.frame?.prepare_gfx
  return prepare?.flag === '--prepare-gfx' && prepare.trust_flag === '--trust-html' && prepare.kind === 'current-composite' && prepare.preview_only === true
}
export function prepareGfxArgs(options) {
  if (options === undefined) return []
  if (!object(options) || Object.keys(options).some(key => !['prepareGfx', 'htmlTrust'].includes(key)) || (Object.hasOwn(options, 'prepareGfx') && typeof options.prepareGfx !== 'boolean')) fail('BAD_FRAME', 'prepareGfx must be an optional boolean; unknown frame options are forbidden')
  if (Object.hasOwn(options, 'htmlTrust') && (options.htmlTrust !== 'trusted-local-html' || options.prepareGfx !== true)) fail('HTML_TRUST_REQUIRED', 'Exact trusted-local-html consent requires prepareGfx=true', 403)
  return options.prepareGfx === true ? ['--prepare-gfx', ...(options.htmlTrust === 'trusted-local-html' ? ['--trust-html'] : [])] : []
}

const WINDOW_SCHEMA = 'framivra.timeline.preview-window/v1'
const WINDOW_MEDIA_LIMIT = 5 * 1024 * 1024
const WINDOW_DIAGNOSTICS = new Map([
  ['plugins.unsupported', 'Audio/video window preview does not support active or declared plugins.'],
  ['gfx.unsupported', 'Audio/video window preview cannot include or verify graphics.'],
  ['subtitles.unsupported', 'Audio/video window preview requires inline subtitles.'],
  ['snapshot.changed', 'The saved timeline changed; refresh and try again.'],
  ['inputs.changed', 'Preview inputs or tools changed during rendering.'],
  ['platform.unsupported', 'The installed core cannot render a window on this platform.'],
  ['environment.unsupported', 'The installed render environment is not supported for window preview.'],
  ['render.unsettled', 'The core could not confirm cleanup of its preview workspace.'],
  ['render.process', 'An audio/video render or verification tool failed.'],
  ['output.limit', 'The generated window exceeds the inline response limit.'],
])
const WINDOW_LAUNCHER = PREVIEW_SUPPLIER_ABI
const MANAGED_WINDOW_IDENTITY = 'framivra.preview-window.managed-pins/v1'
export const ORDINARY_PROGRAM_CAPABILITY = 'framivra.preview-window.ordinary-long-run/v1'
const windowDigest = value => typeof value === 'string' && value.length === 64 && /^[a-f0-9]{64}$/.test(value)

function decodePreviewWindowCapabilities(text, managedSelection) {
  const capability = decodeEnvelope(text, 'capabilities').capabilities.preview_window
  if (capability?.schema !== WINDOW_SCHEMA || capability.inline_media !== true || capability.prepare_inputs !== true ||
      capability.launcher !== WINDOW_LAUNCHER || !windowDigest(capability.wheel_sha256) || !windowDigest(capability.runtime_projection_sha256)) {
    fail('WINDOW_UNSUPPORTED', 'The independent window launcher must advertise the fixed-wheel profile', 422)
  }
  if (managedSelection) {
    const [, , , wheel, projection] = managedSelection.split(':')
    if (capability.managed_identity !== MANAGED_WINDOW_IDENTITY || capability.wheel_sha256 !== wheel ||
        capability.runtime_projection_sha256 !== projection) {
      fail('WINDOW_UNSUPPORTED', 'Live fixed-wheel capability did not confirm the owner-selected managed supplier and pre-import pins', 422)
    }
  }
  // Bind only the known capability and pin fields, never arbitrary launcher output.
  return Object.freeze({ schema: WINDOW_SCHEMA, inline_media: true, prepare_inputs: true, launcher: WINDOW_LAUNCHER,
    wheel_sha256: capability.wheel_sha256, runtime_projection_sha256: capability.runtime_projection_sha256,
    ...(capability.input_epoch === true ? { input_epoch: true } : {}),
    ...(capability.ordinary_long_run === ORDINARY_PROGRAM_CAPABILITY ? { ordinary_long_run: ORDINARY_PROGRAM_CAPABILITY } : {}),
    ...(capability.warm_svg_media === true ? { warm_svg_media: true } : {}),
    ...(capability.program_audio === true ? { program_audio: true } : {}) })
}

function decodePreviewWindow(text, start, version, capability, readWarmSvg, useProgramAudio) {
  let value
  try { value = parseJSON(text) } catch (error) {
    if (error instanceof LosslessJSONError) throw error
    fail('INVALID_JSON', 'vtx did not return complete preview-window JSON', 502)
  }
  return decodePreviewWindowValue(value, start, version, capability, readWarmSvg, useProgramAudio)
}

function decodePreviewWindowValue(value, start, version, capability, readWarmSvg, useProgramAudio = false) {
  const mismatch = () => fail('PROTOCOL_MISMATCH', 'Invalid vtx preview-window/v1 receipt', 502)
  if (!object(value) || value.schema !== WINDOW_SCHEMA || typeof value.ok !== 'boolean') mismatch()
  if (!value.ok) {
    if (!Array.isArray(value.diagnostics)) mismatch()
    return { ok: false, diagnostics: value.diagnostics.slice(0, 8).map(item => {
      const message = WINDOW_DIAGNOSTICS.get(item?.code)
      return message ? { code: item.code, message } : { code: 'window.rejected', message: 'The core refused this audio/video window.' }
    }) }
  }
  if (value.launcher !== capability.launcher || value.wheel_sha256 !== capability.wheel_sha256 || value.runtime_projection_sha256 !== capability.runtime_projection_sha256) {
    fail('WINDOW_PROFILE_CHANGED', 'Window receipt did not match the approved fixed-wheel profile; reopen preview', 409)
  }
  if (readWarmSvg ? value.warm !== true : value.warm !== undefined && value.warm !== false) mismatch()
  if (useProgramAudio ? value.program_audio !== true : Object.hasOwn(value, 'program_audio')) mismatch()
  const requiresEpoch = capability.input_epoch === true || readWarmSvg || useProgramAudio
  if (requiresEpoch && !(typeof value.input_epoch === 'string' && value.input_epoch.length === 71 && /^sha256:[0-9a-f]{64}$/.test(value.input_epoch))) mismatch()
  if (value.content_version !== version || value.start !== start || !integer(value.end) || value.end <= start ||
      !object(value.fps) || !integer(value.fps.num) || !value.fps.num || !integer(value.fps.den) || !value.fps.den ||
      value.width !== 1920 || value.height !== 1080 || value.mime !== 'video/mp4' ||
      value.audio_sample_rate !== 48000 || value.audio_channels !== 2 || !integer(value.byte_length) || value.byte_length === 0 ||
      typeof value.sha256 !== 'string' || value.sha256.length !== 64 || !/^[a-f0-9]{64}$/.test(value.sha256)) mismatch()
  if (value.byte_length > WINDOW_MEDIA_LIMIT) fail('WINDOW_MEDIA_LIMIT', 'Window media exceeds 5 MiB', 502)
  if (typeof value.base64 !== 'string' || value.base64.length !== Math.ceil(value.byte_length / 3) * 4) mismatch()
  const bytes = Buffer.from(value.base64, 'base64')
  if (bytes.length !== value.byte_length || bytes.toString('base64') !== value.base64 || createHash('sha256').update(bytes).digest('hex') !== value.sha256) mismatch()
  // The exact inline bytes are the only payload authority; do not retain or resolve any path.
  return { ok: true, start, end: value.end, ...(requiresEpoch && { inputEpoch: value.input_epoch }),
    ...(useProgramAudio && { programAudio: true }), fps: { num: value.fps.num, den: value.fps.den }, sha256: value.sha256, bytes }
}

const FIRST_FRAME_SCHEMA = 'framivra.preview-window.first-frame/v1'
const PNG_SIGNATURE = Buffer.from('89504e470d0a1a0a', 'hex')
const PNG_FORMAT = Buffer.from([8, 2, 0, 0, 0]) // RGB8, noninterlaced
const PNG_SCANLINE = 1 + 1920 * 3, PNG_RAW_SIZE = 1080 * PNG_SCANLINE
const firstFrameMismatch = () => fail('FIRST_FRAME_PROTOCOL', 'Invalid fixed-wheel provisional first-frame stream', 502)
const sameFields = (value, fields) => object(value) && Object.keys(value).length === fields.length && fields.every(key => Object.hasOwn(value, key))
const sameWindowProfile = (value, cap) => value.launcher === cap.launcher && value.wheel_sha256 === cap.wheel_sha256 && value.runtime_projection_sha256 === cap.runtime_projection_sha256

const ARCHIVE_CAP_SCHEMA = 'framivra.preview-formal-archive.capabilities/v1'
const ARCHIVE_SCHEMA = 'framivra.preview-formal-archive/v1'
const ARCHIVE_PROFILE_FIELDS = ['launcher', 'wheel_sha256', 'runtime_projection_sha256']
const ARCHIVE_REJECTIONS = new Map([
  ['archive.selector_mismatch', ['ARCHIVE_SELECTOR_MISMATCH', 'The project, start frame, or saved version does not match the archive approval.']],
  ['archive.inputs_mismatch', ['ARCHIVE_INPUTS_MISMATCH', 'The declared project inputs could not be verified against the archive approval.']],
  ['archive.unavailable', ['ARCHIVE_UNAVAILABLE', 'The archive approval, media, or fixed-wheel deployment could not be verified.']],
])

function decodeFormalArchiveRejection(text, nonce) {
  const invalid = () => fail('ARCHIVE_PROTOCOL', 'Invalid approved formal archive rejection', 502)
  let value
  try { value = JSON.parse(text) } catch { invalid() }
  if (!sameFields(value, ['schema', 'ok', 'nonce', 'code']) || value.schema !== ARCHIVE_SCHEMA || value.ok !== false || value.nonce !== nonce) invalid()
  const rejection = ARCHIVE_REJECTIONS.get(value.code)
  // The fixed wheel emits compact ASCII JSON and one LF. Round-trip to reject
  // duplicate fields and extra records, including anything carrying media.
  if (!rejection || `${JSON.stringify(value)}\n` !== text) invalid()
  return { rejection }
}

function decodeFormalArchiveCapability(text, cap) {
  const value = parseJSON(text)
  if (!sameFields(value, ['schema', 'available', ...ARCHIVE_PROFILE_FIELDS]) || value.schema !== ARCHIVE_CAP_SCHEMA ||
      value.available !== true || !sameWindowProfile(value, cap)) {
    fail('WINDOW_UNSUPPORTED', 'The fixed wheel did not separately qualify formal archive reads', 422)
  }
  return Object.freeze({ ...cap, formal_archive: Object.freeze({ version: 1 }) })
}

function decodeFormalArchive(text, frame, version, nonce, capability) {
  const mismatch = () => fail('ARCHIVE_PROTOCOL', 'Invalid approved formal archive receipt', 502)
  let value
  try { value = parseJSON(text) } catch { mismatch() }
  if (!sameFields(value, ['schema', 'ok', 'nonce', 'content_version', 'start', 'end', 'fps', 'width', 'height',
    'audio_sample_rate', 'audio_channels', 'mime', 'sha256', 'byte_length', 'base64', 'manifest_sha256', ...ARCHIVE_PROFILE_FIELDS]) ||
    value.schema !== ARCHIVE_SCHEMA || value.ok !== true || value.nonce !== nonce || value.content_version !== version ||
    value.start !== frame || !integer(value.end) || value.end <= frame || !sameFields(value.fps, ['num', 'den']) ||
    !integer(value.fps.num) || !value.fps.num || !integer(value.fps.den) || !value.fps.den ||
    value.width !== 1920 || value.height !== 1080 || value.audio_sample_rate !== 48000 || value.audio_channels !== 2 || value.mime !== 'video/mp4' ||
    !windowDigest(value.sha256) || !windowDigest(value.manifest_sha256) || !integer(value.byte_length) || !value.byte_length ||
    !sameWindowProfile(value, capability)) mismatch()
  if (value.byte_length > WINDOW_MEDIA_LIMIT) fail('ARCHIVE_MEDIA_LIMIT', 'Archive media exceeds 5 MiB', 502)
  if (typeof value.base64 !== 'string' || value.base64.length !== Math.ceil(value.byte_length / 3) * 4) mismatch()
  const bytes = Buffer.from(value.base64, 'base64')
  if (bytes.length !== value.byte_length || bytes.toString('base64') !== value.base64 || createHash('sha256').update(bytes).digest('hex') !== value.sha256) mismatch()
  return { start: frame, end: value.end, fps: value.fps, sha256: value.sha256, bytes }
}

function decodeFirstFrameCapability(text, cap) {
  const value = parseJSON(text)
  if (!object(value) || value.schema !== FIRST_FRAME_SCHEMA || value.available !== true || !sameWindowProfile(value, cap) ||
      value.record_payload_limit_exclusive !== FRAME_WIRE_LIMIT) fail('WINDOW_UNSUPPORTED', 'The fixed wheel did not separately qualify the first-frame wire', 422)
  return Object.freeze({ ...cap, first_frame: Object.freeze({ version: 1 }) })
}

function verifyFirstFramePng(bytes) {
  if (!bytes.subarray(0, 8).equals(PNG_SIGNATURE)) firstFrameMismatch()
  // The fixed wheel emits only IHDR, consecutive IDAT and IEND. Bound the
  // inflater's output independently of the compressed bytes and check every CRC.
  const compressed = Buffer.allocUnsafe(bytes.length)
  let compressedLength = 0
  for (let offset = 8; offset + 12 <= bytes.length;) {
    const length = bytes.readUInt32BE(offset), data = offset + 8, end = data + length
    if (end + 4 > bytes.length || crc32(bytes.subarray(offset + 4, end)) !== bytes.readUInt32BE(end)) firstFrameMismatch()
    const kind = bytes.toString('latin1', offset + 4, data)
    if (offset === 8) {
      if (kind !== 'IHDR' || length !== 13 || bytes.readUInt32BE(data) !== 1920 || bytes.readUInt32BE(data + 4) !== 1080 ||
          !bytes.subarray(data + 8, end).equals(PNG_FORMAT)) firstFrameMismatch()
    } else if (kind === 'IDAT') {
      bytes.copy(compressed, compressedLength, data, end)
      compressedLength += length
    } else if (kind === 'IEND' && length === 0 && end + 4 === bytes.length && compressedLength) {
      const { buffer, engine } = inflateSync(compressed.subarray(0, compressedLength), { maxOutputLength: PNG_RAW_SIZE, info: true })
      if (buffer.length !== PNG_RAW_SIZE || engine.bytesWritten !== compressedLength) firstFrameMismatch()
      for (let row = 0; row < PNG_RAW_SIZE; row += PNG_SCANLINE) if (buffer[row] > 4) firstFrameMismatch()
      return
    } else firstFrameMismatch()
    offset = end + 4
  }
  firstFrameMismatch()
}

function decodeFirstFrame(value, start, version, capability) {
  if (!sameFields(value, ['schema', 'provisional', 'mime', 'frame', 'window', 'segment', 'fps', 'width', 'height', 'content_version',
    'byte_length', 'sha256', 'base64', 'launcher', 'wheel_sha256', 'runtime_projection_sha256']) ||
    value.schema !== FIRST_FRAME_SCHEMA || value.provisional !== true || value.mime !== 'image/png' || !sameWindowProfile(value, capability) ||
    value.frame !== start || value.content_version !== version || value.width !== 1920 || value.height !== 1080) firstFrameMismatch()
  const { window, segment, fps } = value
  // Exact cut placement is signed by the fixed wheel under its effective
  // segment configuration; the Host checks the interval relationship here.
  if (!Array.isArray(window) || window.length !== 2 || window[0] !== start || !integer(window[1]) || window[1] <= start ||
      !Array.isArray(segment) || segment.length !== 2 || segment[0] !== start || !integer(segment[1]) || segment[1] <= start || segment[1] > window[1] ||
      !sameFields(fps, ['num', 'den']) || !integer(fps.num) || !fps.num || !integer(fps.den) || !fps.den ||
      !integer(value.byte_length) || !value.byte_length || !windowDigest(value.sha256) ||
      typeof value.base64 !== 'string' || value.base64.length !== Math.ceil(value.byte_length / 3) * 4) firstFrameMismatch()
  const bytes = Buffer.from(value.base64, 'base64')
  if (bytes.length !== value.byte_length || bytes.toString('base64') !== value.base64 || createHash('sha256').update(bytes).digest('hex') !== value.sha256) firstFrameMismatch()
  verifyFirstFramePng(bytes)
  return { start, end: window[1], fps, bytes, sha256: value.sha256 }
}

async function readFirstFrameStream(stdout, signal, { nonce, capability, frame, version, accept, clear }) {
  let used = 0, buffer = Buffer.alloc(5), kind, first, result
  try {
    for await (const chunk of addAbortSignal(signal, stdout)) {
      if (!Buffer.isBuffer(chunk)) firstFrameMismatch()
      for (let offset = 0; offset < chunk.length;) {
        if (result) firstFrameMismatch()
        const length = Math.min(buffer.length - used, chunk.length - offset)
        chunk.copy(buffer, used, offset, offset + length)
        used += length; offset += length
        if (used !== buffer.length) continue
        used = 0
        if (!kind) {
          kind = buffer[0]
          const size = buffer.readUInt32BE(1)
          if ((kind !== 70 && kind !== 87) || !size || size >= FRAME_WIRE_LIMIT || (kind === 70 && first)) firstFrameMismatch()
          buffer = Buffer.allocUnsafe(size)
          continue
        }
        const text = buffer.toString('ascii'), wire = parseJSON(text)
        // The launcher uses compact ASCII JSON (ensure_ascii=True). A native
        // round-trip also rejects duplicate keys and high-bit byte aliasing.
        const canonical = stringifyJSON(wire).replace(/[\u007f-\uffff]/g, char => `\\u${char.charCodeAt(0).toString(16).padStart(4, '0')}`)
        if (!buffer.equals(Buffer.from(canonical, 'ascii')) || !sameFields(wire, ['nonce', 'result']) || wire.nonce !== nonce || !object(wire.result)) firstFrameMismatch()
        if (kind === 70) {
          const image = decodeFirstFrame(wire.result, frame, version, capability)
          first = { end: image.end, fps: image.fps }
          accept(image)
        } else {
          result = decodePreviewWindowValue(wire.result, frame, version, capability, false)
          if (!result.ok) clear()
          else if (!first || result.end !== first.end || result.fps.num !== first.fps.num || result.fps.den !== first.fps.den) firstFrameMismatch()
        }
        buffer = Buffer.alloc(5); kind = undefined
      }
    }
    if (stdout.readableEnded !== true || kind || used || !result) firstFrameMismatch()
    return result
  } catch (error) {
    clear()
    signal.throwIfAborted()
    if (error instanceof PreviewError || error instanceof LosslessJSONError) throw error
    firstFrameMismatch() // Do not expose bytes, parser errors, zlib diagnostics, or process errors.
  }
}

/** A routing predicate over the pinned inspect/v1 document, not a render
 *  validator or a cache selector. Only the fixed wheel can select a pack. */
export function hitsWarmSvgWindow(document, start) {
  const { project, video_tracks: tracks } = document
  if (!Array.isArray(tracks) || !integer(project?.duration_frames) || start >= project.duration_frames) return false
  const fps = JSON.isRawJSON(project.fps) ? Number(project.fps.rawJSON) : project.fps
  // All inspect-validated decimal FPS aliases and their exact NTSC rates
  // have the same ceiling; fractional string labels need the denominator.
  const [num, den = 1] = typeof fps === 'string' ? fps.split('/').map(Number) : [fps]
  const frames = Math.ceil(num / den)
  if (!Number.isSafeInteger(frames) || frames <= 0) return false
  const end = start + Math.min(project.duration_frames - start, frames)
  let hits = 0
  for (const track of tracks) {
    if (typeof track?.kind !== 'string' || !Array.isArray(track.clips)) return false
    if (track.kind !== 'gfx') continue
    for (const clip of track.clips) {
      if (clip?.format !== 'svg-sequence' || !integer(clip.dst_in) || !integer(clip.dur) || clip.dur === 0 ||
          clip.dst_in > project.duration_frames - clip.dur) return false
      if (clip.dst_in < end && start < clip.dst_in + clip.dur && ++hits > 1) return false
    }
  }
  return hits === 1
}

function matchesSavedRate(fps, num, den) {
  const stored = JSON.isRawJSON(fps) ? Number(fps.rawJSON) : fps
  // inspect/v1 already validates allowed FPS labels. Map only their wire aliases (NTSC is exact N/1001).
  return typeof stored === 'string' ? stored === `${num}/${den}`
    : Number.isInteger(stored) ? den === 1 && stored === num : den === 1001 && stored === Math.round(num / den * 1000) / 1000
}

export function validatePreviewWindowRange(receipt, project) {
  const { start, end, fps: { num, den } } = receipt
  const { duration_frames: duration, fps } = project ?? {}
  if (!matchesSavedRate(fps, num, den) || !integer(duration) || start >= duration || end !== start + Math.min(duration - start, Math.ceil(num / den))) {
    fail('PROTOCOL_MISMATCH', 'vtx receipt does not cover the exact saved preview window', 502)
  }
}

export function validateFormalArchiveRange({ start, end, fps: { num, den } }, project) {
  const { duration_frames: duration, fps } = project ?? {}
  // Canonical NTSC (rather than another fraction that rounds to the same label).
  const canonicalRate = den === 1 || (den === 1001 && [24000, 30000, 48000, 60000, 120000].includes(num))
  if (!canonicalRate || !matchesSavedRate(fps, num, den) || !integer(duration) || end > duration ||
      BigInt(end - start) * BigInt(den) > 4n * BigInt(num)) {
    fail('ARCHIVE_PROTOCOL', 'Archive interval or rate does not match the current saved project', 502)
  }
}

export function queryArgs(query = {}) {
  if (!object(query)) fail('BAD_QUERY', 'Query must be an object')
  const allowed = ['family', 'track_index', 'block_index', 'frame', 'interval']
  if (Object.keys(query).some(key => !allowed.includes(key))) fail('BAD_QUERY', 'Unknown query field')
  if (query.track_index !== undefined && query.family === undefined) fail('BAD_QUERY', 'track_index requires family')
  if (query.block_index !== undefined && (query.family === undefined || query.track_index === undefined)) fail('BAD_QUERY', 'block_index requires family and track_index')
  const args = []
  if (query.family !== undefined) {
    if (!['video', 'audio', 'subtitles'].includes(query.family)) fail('BAD_QUERY', 'Unknown family')
    args.push('--family', query.family)
  }
  for (const key of ['track_index', 'block_index', 'frame']) {
    if (query[key] === undefined) continue
    if (!integer(query[key])) fail('BAD_QUERY', `${key} must be a nonnegative integer frame/index`)
    args.push(`--${key.replace('_', '-')}`, String(query[key]))
  }
  if (query.interval !== undefined) {
    const value = query.interval
    if (query.frame !== undefined || !Array.isArray(value) || value.length !== 2 || !value.every(integer) || value[0] >= value[1]) {
      fail('BAD_QUERY', 'Use frame OR a nonempty half-open integer interval [A, B)')
    }
    args.push('--interval', ...value.map(String))
  }
  return args
}

export function referenceArgs(query) {
  if (!object(query) || !/^sha256:[a-f0-9]{64}$/.test(query.expected_content_version)) fail('BAD_REFERENCE', 'Reference requires the displayed content version')
  const flags = { track_ids: '--track-id', block_ids: '--block-id', track_addresses: '--track-address', block_addresses: '--block-address' }
  if (Object.keys(query).some(key => ![...Object.keys(flags), 'expected_content_version', 'frame'].includes(key))) fail('BAD_REFERENCE', 'Unknown reference field')
  const args = [`--expected-content-version=${query.expected_content_version}`]
  let count = 0
  for (const [key, flag] of Object.entries(flags)) {
    if (query[key] === undefined) continue
    if (!Array.isArray(query[key]) || query[key].length > 64 || query[key].some(value => typeof value !== 'string' || !value || value.length > 4096 || value.includes('\0'))) fail('BAD_REFERENCE', 'Invalid or oversized reference selector')
    for (const value of query[key]) { args.push(`${flag}=${value}`); count++ }
  }
  if (count !== 1 && !(count === 0 && query.frame !== undefined)) fail('BAD_REFERENCE', 'Select exactly one stored track/block or an explicit position-only frame')
  if (query.frame !== undefined) {
    if (!integer(query.frame)) fail('BAD_REFERENCE', 'Reference frame must be a nonnegative safe integer')
    args.push('--frame', String(query.frame))
  }
  return args
}

function decodeReference(value) {
  const mismatch = () => fail('PROTOCOL_MISMATCH', 'Unsupported vtx reference/v1 envelope; upgrade installed vtx', 502)
  if (!object(value) || value.schema !== 'framivra.timeline.reference/v1' || value.operation !== 'reference' || value.ok !== true ||
      !/^sha256:[a-f0-9]{64}$/.test(value.content_version) || !Array.isArray(value.diagnostics) ||
      !object(value.document) || typeof value.document.raw !== 'string' || value.document.content_version !== value.content_version ||
      !object(value.selection) || !object(value.coverage) || typeof value.coverage.complete !== 'boolean' ||
      !object(value.image) || value.image.state !== 'not-generated') mismatch()
  for (const key of ['tracks', 'blocks']) {
    if (!Array.isArray(value.selection[key]) || value.selection[key].some(item => !object(item) ||
        typeof item.address !== 'string' || item.content_version !== value.content_version ||
        !(item.stable_id === null || typeof item.stable_id === 'string') || !(item.raw === null || typeof item.raw === 'string'))) mismatch()
  }
  return value
}

export function decodeEnvelope(text, operation) {
  let value
  try { value = parseJSON(text) } catch (error) {
    if (error instanceof LosslessJSONError) fail('LOSSLESS_JSON_UNSUPPORTED', error.message, 502)
    fail('INVALID_JSON', 'vtx did not return complete JSON', 502)
  }
  return validateEnvelope(value, operation)
}

/** Validate already parsed nested reads without reserializing lossless numbers. */
export function validateEnvelope(value, operation) {
  if (operation === 'reference') return decodeReference(value)
  if (operation === 'capabilities') {
    if (!object(value) || value.schema !== 'framivra.timeline.read/v1' || value.operation !== operation || value.ok !== true ||
        !object(value.capabilities) || !Array.isArray(value.capabilities.operations) ||
        !['inspect', 'query'].every(op => value.capabilities.operations.includes(op))) fail('PROTOCOL_MISMATCH', 'Installed vtx does not advertise read/v1 inspect and query', 502)
    return value
  }
  if (!object(value) || value.schema !== 'framivra.timeline.read/v1' || value.operation !== operation || value.ok !== true ||
      !/^sha256:[a-f0-9]{64}$/.test(value.content_version) || !Array.isArray(value.diagnostics) ||
      !object(value.coverage) || typeof value.coverage.complete !== 'boolean' || typeof value.coverage.scope !== 'string' || !Array.isArray(value.coverage.unexpanded)) {
    fail('PROTOCOL_MISMATCH', 'Unsupported vtx read envelope; upgrade the installed vtx', 502)
  }
  if (operation === 'inspect' && !object(value.document)) fail('PROTOCOL_MISMATCH', 'Missing complete timeline document', 502)
  if (operation === 'query') {
    for (const key of ['tracks', 'blocks']) {
      if (!Array.isArray(value[key]) || value[key].some(item => !object(item) || !['video', 'audio', 'subtitles'].includes(item.family) ||
        !integer(item.track_index) || typeof item.address !== 'string' || !Object.hasOwn(item, 'data') ||
        (key === 'blocks' && (!integer(item.block_index) || !Array.isArray(item.range) || item.range.length !== 2 || !item.range.every(integer))))) {
        fail('PROTOCOL_MISMATCH', `Invalid ${key} in vtx response`, 502)
      }
    }
  }
  return value // Preserve ALL tracks, unknown document fields, diagnostics and coverage verbatim.
}

export class TimelineReader {
  constructor(subprocess, config = {}, validateConfigSelection, validatePreviewSelection) {
    this.subprocess = subprocess
    this.configSource = config
    this.validateConfigSelection = validateConfigSelection
    this.validatePreviewSelection = validatePreviewSelection
    // Plain (test/debug) sources keep fail-fast constructor validation; live
    // Volatile sources are validated on EVERY snapshot instead, because their
    // values can change at any time without a remount.
    if (!isVolatileConfig(config)) {
      const snapshot = readConfiguration(config)
      this.config = { vtxExecutable: snapshot.vtxExecutable, trustedPath: snapshot.trustedPath }
    } else this.config = undefined
    this.active = new Map() // Ownership survives direct-command settlement and failed cleanup.
    this.spawnKinds = new WeakMap() // Child -> admission class (read, store-capabilities, store, frame, storage-read, storage-clean).
    this.spawnOwners = new WeakMap() // Spawned child -> owner token (grant/session) so cancel reaping stays scoped.
    this.cleanupFailed = new WeakSet() // Children whose managed range was not confirmed reaped; retained until a retry succeeds.
    this.lifecycle = new AbortController()
    this.idleEditView = undefined // At most one globally owned EOF-waiting child (including pending/failed cleanup).
  }
  /**
   * One consistent validated snapshot of the live configuration (defaults,
   * plain strings, or cordis Volatile cells). Every executable resolution and
   * every spawned command must take its own snapshot and use only that one.
   * @returns {{ vtxExecutable: string, trustedPath: string, fingerprint: string }}
   */
  snapshotConfig() {
    const snapshot = readConfiguration(this.configSource)
    this.config = { vtxExecutable: snapshot.vtxExecutable, trustedPath: snapshot.trustedPath }
    return snapshot
  }
  /**
   * True while semantic admission must wait: an actual store transaction or a
   * frame/storage-clean child is in flight, or any child — including a read — has a
   * failed, unreaped cleanup. Ordinary reads and the pure-static store
   * capabilities probe never block semantic operations on their own.
   * @returns {boolean}
   */
  get semanticBusy() {
    for (const handle of this.active.keys()) {
      if (this.cleanupFailed.has(handle)) return true
      const kind = this.spawnKinds.get(handle)
      if (kind === 'store' || kind === 'frame' || kind === 'storage-clean') return true
    }
    return false
  }
  /** Only a live, untouched EOF waiter is harmless to frame admission. */
  get frameBusy() {
    for (const handle of this.active.keys()) {
      const slot = this.idleEditView
      if (slot?.handle === handle && slot.state === 'idle' && !slot.signal.aborted && !this.active.get(handle) && !this.cleanupFailed.has(handle)) continue
      return true
    }
    return false
  }
  /** Best-effort, one-shot preload: NEVER send bytes until an actual store call takes it. */
  async primeEditView(executable, timeline, signal, snapshot, owner, writeGrant, beforeSpawn) {
    if (this.idleEditView || this.semanticBusy) return
    const controller = new AbortController()
    signal = AbortSignal.any([signal, this.lifecycle.signal, controller.signal])
    if (signal.aborted) return
    const slot = { executable, timeline, owner, writeGrant, fingerprint: snapshot.fingerprint, signal, controller, state: 'priming' }
    this.idleEditView = slot // Reserve synchronously, including async config validation.
    slot.abort = () => { void this.reapIdle(owner).catch(() => {}) }
    signal.addEventListener('abort', slot.abort, { once: true })
    slot.timer = setTimeout(() => controller.abort(), 4000)
    slot.timer.unref?.()
    try {
      await this.command(executable, timeline, ['store', 'edit-view', '--request-stdin'], signal, undefined, undefined, true, false, snapshot, owner, { warm: { prime: slot, beforeSpawn } })
    } finally {
      if (!slot.handle) {
        this.detachIdle(slot)
        if (this.idleEditView === slot) this.idleEditView = undefined
      }
    }
  }
  detachIdle(slot) {
    clearTimeout(slot.timer)
    slot.signal.removeEventListener('abort', slot.abort)
  }
  async reapIdle(owner) {
    const slot = this.idleEditView
    if (!slot || (owner !== undefined && slot.owner !== owner)) return
    slot.state = 'retiring'
    this.detachIdle(slot)
    slot.controller.abort()
    if (slot.handle) await this.cleanup(slot.handle)
    else if (this.idleEditView === slot) this.idleEditView = undefined
  }
  async executable(signal, snapshot = this.snapshotConfig()) {
    let path
    try {
      path = await this.subprocess.resolveExecutable(snapshot.vtxExecutable, { PATH: snapshot.trustedPath }, signal)
    } catch (error) {
      // The one stable public miss class; every other error keeps its original shape.
      if (error && error.name === 'SubprocessExecutableNotFoundError') {
        fail('EXECUTABLE_NOT_FOUND', '未在受信 PATH 找到 vtx；请在 Framivra 插件详情中将 vtxExecutable 配置为绝对受信路径（或将 vtx 安装到受信 PATH）', 424)
      }
      throw error
    }
    if (!isAbsolute(path)) fail('BAD_EXECUTABLE', 'Resolver returned a nonabsolute executable', 502)
    return path
  }
  async managedWindow(snapshot, signal) {
    if (typeof this.validatePreviewSelection !== 'function') fail('WINDOW_UNSUPPORTED', 'Managed preview requires Host enrollment validation', 422)
    try { return await this.validatePreviewSelection(snapshot, signal) }
    catch (error) {
      signal.throwIfAborted()
      const mismatch = error instanceof SyntaxError || ['PREVIEW_SUPPLIER_UNAPPROVED', 'PREVIEW_SUPPLIER_CHANGED',
        'BAD_PREVIEW_SUPPLIER', 'BAD_INSTALLATION', 'INSTALLATION_CHANGED', 'NOT_FOUND', 'FS_NOT_FOUND',
        'UNSAFE_PATH', 'WORKSPACE_PATH', 'NOT_REGULAR', 'FILE_TOO_LARGE'].includes(error?.code)
      if (mismatch) fail('EXECUTABLE_CHANGED', 'Managed preview enrollment changed; scan and save it again', 409)
      fail('WINDOW_UNSUPPORTED', 'Managed preview enrollment could not be verified', 422)
    }
  }
  async windowExecutable(signal, snapshot) {
    const selected = snapshot.managedPreviewSupplier ? (await this.managedWindow(snapshot, signal)).executable : snapshot.previewWindowExecutable
    if (!selected) fail('WINDOW_UNSUPPORTED', 'No independent window launcher is configured', 422)
    try {
      const executable = await this.subprocess.resolveExecutable(selected, { PATH: snapshot.trustedPath }, signal)
      if (executable !== selected || !isAbsolute(executable)) fail('WINDOW_UNSUPPORTED', 'The window launcher must resolve to the configured absolute path', 422)
      return executable
    } catch (error) {
      if (error?.name === 'SubprocessExecutableNotFoundError') fail('WINDOW_UNSUPPORTED', 'The configured window launcher is not executable', 422)
      throw error
    }
  }
  async read(executable, timeline, operation, query, signal, snapshot, owner) {
    if (!['inspect', 'query', 'reference', 'capabilities'].includes(operation)) fail('BAD_OPERATION', 'Only read operations are supported')
    const args = operation === 'query' ? queryArgs(query) : operation === 'reference' ? referenceArgs(query) : []
    return this.command(executable, timeline, [operation, ...args], signal, text => decodeEnvelope(text, operation), undefined, false, false, snapshot, owner)
  }
  async frame(executable, timeline, frame, version, signal, snapshot, owner, options) {
    if (!integer(frame) || !/^sha256:[a-f0-9]{64}$/.test(version)) fail('BAD_FRAME', 'Exact saved version and integer pin required')
    const prepareArgs = prepareGfxArgs(options)
    return this.command(executable, timeline, ['timeline', 'frame', '--frame', String(frame), `--expected-content-version=${version}`, '--render', '--include-png', '--max-png-bytes=8388608', ...prepareArgs], signal, text => {
      const value = parseJSON(text)
      if (!object(value) || value.schema !== 'framivra.timeline.frame/v1' || value.operation !== 'frame' || typeof value.ok !== 'boolean' || !Array.isArray(value.diagnostics)) fail('PROTOCOL_MISMATCH', 'Official frame/v1 envelope required', 502)
      return value
    }, undefined, true, true, snapshot, owner)
  }
  async windowCapabilities(executable, timeline, signal, snapshot, owner) {
    return this.command(executable, timeline, ['capabilities'], signal,
      text => decodePreviewWindowCapabilities(text, snapshot.managedPreviewSupplier),
      undefined, false, false, snapshot, owner, { isolatedWindow: true })
  }
  async firstFrameCapabilities(executable, capability, signal, snapshot, owner) {
    // The ordinary capabilities receipt cannot claim this transport. Probe
    // exactly the standalone launcher command without a project argument.
    return this.command(executable, undefined, ['--first-frame-capabilities'], signal,
      text => decodeFirstFrameCapability(text, capability), undefined, false, true, snapshot, owner, { isolatedWindow: true })
  }
  async formalArchiveCapabilities(executable, capability, signal, snapshot, owner) {
    return this.command(executable, undefined, ['--formal-archive-capabilities'], signal,
      text => decodeFormalArchiveCapability(text, capability), undefined, false, true, snapshot, owner, { isolatedWindow: true })
  }
  async previewFormalArchive({ executable, capability }, timeline, frame, version, signal, snapshot, owner) {
    if (!integer(frame) || !/^sha256:[a-f0-9]{64}$/.test(version) || capability.formal_archive?.version !== 1) {
      fail('ARCHIVE_DENIED', 'A qualified fixed wheel and exact saved version are required', 403)
    }
    const nonce = randomBytes(16).toString('hex')
    try {
      const result = await this.command(executable, timeline, ['timeline', 'preview-formal-archive', '--frame', String(frame),
        `--expected-content-version=${version}`, '--read-approved-archive', `--nonce=${nonce}`], signal,
        (text, exitCode) => exitCode === 1 ? decodeFormalArchiveRejection(text, nonce) : decodeFormalArchive(text, frame, version, nonce, capability),
        undefined, false, true, snapshot, owner, { isolatedWindow: true })
      // command only returns after the managed range is confirmed reaped.
      if (result.rejection) fail(...result.rejection, 409)
      return result
    } catch (error) {
      if (error?.message?.startsWith('BODY_LIMIT:')) fail('ARCHIVE_WIRE_LIMIT', 'Archive response exceeds 8 MiB including envelope/newline', 502)
      throw error
    }
  }
  async previewWindowFirstFrame({ executable, capability }, timeline, frame, version, signal, snapshot, owner, { accept, clear }) {
    if (!integer(frame) || !/^sha256:[a-f0-9]{64}$/.test(version) || capability.first_frame?.version !== 1 || capability.input_epoch !== true) {
      fail('FIRST_FRAME_DENIED', 'A qualified fixed wheel and exact saved version are required', 403)
    }
    const nonce = randomBytes(16).toString('hex')
    return this.command(executable, timeline, ['timeline', 'preview-window-first-frame', '--frame', String(frame), `--expected-content-version=${version}`,
      '--render', '--include-media', '--prepare-inputs', `--nonce=${nonce}`], signal, undefined, undefined, true,
      { nonce, capability, frame, version, accept, clear }, snapshot, owner, { isolatedWindow: true })
  }
  async previewWindow({ executable, capability }, timeline, frame, version, signal, snapshot, owner, readWarmSvg = false, useProgramAudio = false) {
    if (!integer(frame) || !/^sha256:[a-f0-9]{64}$/.test(version)) fail('BAD_WINDOW', 'Exact saved version and integer pin required')
    if (readWarmSvg && capability.warm_svg_media !== true) fail('WARM_SVG_DENIED', 'The qualified fixed wheel did not advertise warm SVG media', 403)
    if (useProgramAudio && capability.program_audio !== true) fail('PROGRAM_AUDIO_DENIED', 'The qualified fixed wheel did not advertise program audio', 403)
    try {
      return await this.command(executable, timeline, ['timeline', 'preview-window', '--frame', String(frame), `--expected-content-version=${version}`,
        '--render', '--include-media', '--prepare-inputs', ...(readWarmSvg ? ['--read-warm-svg'] : []), ...(useProgramAudio ? ['--use-program-audio'] : [])], signal,
        text => decodePreviewWindow(text, frame, version, capability, readWarmSvg, useProgramAudio), undefined, true, true, snapshot, owner, { isolatedWindow: true })
    } catch (error) {
      if (error?.message?.startsWith('BODY_LIMIT:')) fail('WINDOW_WIRE_LIMIT', 'Window response exceeds 8 MiB including envelope/newline', 502)
      throw error
    }
  }
  /**
   * Reap children, optionally scoped to one owner. Cancellation passes the
   * affected grant/session so a cancel can never terminate children spawned
   * for a DIFFERENT grant or session that happen to be in flight. Failed
   * cleanups stay owned (active) and a targeted retry re-attempts only them.
   * @param {object} [owner] Owner token passed at spawn time; omit to reap all (dispose).
   */
  async reap(owner) {
    await Promise.all([this.reapIdle(owner), ...[...this.active.keys()].filter(handle => owner === undefined || this.spawnOwners.get(handle) === owner).map(handle => this.cleanup(handle))])
  }
  /**
   * True while any child spawned for this owner token is still tracked —
   * in flight OR retained after a failed cleanup. Scoped exactly like reap,
   * so callers can distinguish "my children are done" from "some unrelated
   * grant/session still has a child in flight".
   * @param {object} owner Owner token passed at spawn time
   * @param {string} [kind] Optional spawn admission class ('read' |
   *   'store-capabilities' | 'store' | 'frame'); when given, only children of
   *   that class count. Admission globals (active.size, semanticBusy) and
   *   reap(owner) semantics are unchanged — this only narrows THIS query.
   * @returns {boolean}
   */
  activeFor(owner, kind) {
    for (const handle of this.active.keys()) {
      if (this.spawnOwners.get(handle) !== owner) continue
      if (kind === undefined || this.spawnKinds.get(handle) === kind) return true
    }
    return false
  }
  async storage(executable, timeline, operation, request, signal, snapshot, owner) {
    if (!['inspect', 'plan', 'clean'].includes(operation)) fail('BAD_OPERATION', 'Unknown storage command')
    const input = request === undefined ? undefined : stringifyJSON(request)
    if (input !== undefined && Buffer.byteLength(input, 'utf8') > 4096) fail('REQUEST_TOO_LARGE', 'Storage request exceeds 4 KiB', 413)
    return this.command(executable, timeline, ['storage', operation, ...(input === undefined ? [] : ['--request-stdin'])], signal, text => {
      const value = parseJSON(text)
      const mismatch = () => fail('PROTOCOL_MISMATCH', 'Official storage/v1 JSON envelope required', 502)
      if (!object(value) || value.schema !== 'framivra.storage/v1' || value.operation !== operation || typeof value.ok !== 'boolean' ||
          !(/^sha256:[a-f0-9]{64}$/.test(value.content_version) || (value.ok === false && value.content_version === null)) || !Array.isArray(value.diagnostics)) mismatch()
      if (value.ok && operation === 'inspect' && (!object(value.usage) || !['project_bytes', 'cache_bytes', 'out_bytes', 'other_bytes', 'file_count', 'symlinks_skipped'].every(key => integer(value.usage[key])) || !['off', 'core', 'aggressive'].includes(value.cache_mode))) mismatch()
      if (value.ok && operation === 'plan' && (!object(value.plan) || !/^sha256:[a-f0-9]{64}$/.test(value.plan.id) || !integer(value.plan.bytes) || !integer(value.plan.count) || !Array.isArray(value.plan.entries) || value.plan.count !== value.plan.entries.length || value.plan.entries.some(entry => !object(entry) || typeof entry.path !== 'string' || !integer(entry.bytes) || typeof entry.category !== 'string' || typeof entry.reason !== 'string'))) mismatch()
      if (operation === 'clean' && value.ok && (!object(value.removed) || !object(value.remaining) || !['bytes', 'count'].every(key => integer(value.removed[key]) && integer(value.remaining[key])))) mismatch()
      return value
    }, input, true, false, snapshot, owner)
  }
  async store(executable, timeline, operation, request, signal, snapshot, owner, writeGrant, beforeSpawn) {
    if (!['capabilities', 'plan', 'commit', 'status', 'recover', 'edit-view'].includes(operation)) fail('BAD_OPERATION', 'Unknown store command')
    // The official flag reads fd0 itself: public providers may expose a socket,
    // for which reopening /dev/stdin fails with ENXIO. No temp files/bridge.
    const args = request === undefined ? ['store', operation] : ['store', ...(['plan', 'edit-view'].includes(operation) ? [operation] : []), '--request-stdin']
    const input = request === undefined ? undefined : stringifyJSON(request)
    if (input !== undefined && Buffer.byteLength(input, 'utf8') > 8388608) fail('REQUEST_TOO_LARGE', 'Semantic request exceeds the official 8 MiB UTF-8 byte limit', 413)
    return this.command(executable, timeline, args, signal, text => {
      const result = parseJSON(text)
      if (result?.schema !== 'framivra.timeline.store/v1' || typeof result.ok !== 'boolean') fail('PROTOCOL_MISMATCH', 'Installed vtx store/v1 required', 502)
      return result
    }, input, true, undefined, snapshot, owner, { beforeSpawn, ...(operation === 'edit-view' && { warm: { writeGrant } }) })
  }
  async command(executable, timeline, args, signal, decode, input, business = false, streaming = false, snapshot, owner, { warm, beforeSpawn = warm?.beforeSpawn, isolatedWindow = false } = {}) {
    signal = AbortSignal.any([signal, this.lifecycle.signal])
    signal.throwIfAborted()
    // One snapshot per command: a caller-supplied snapshot is compared against
    // the live configuration synchronously BEFORE spawning, so an admitted
    // child never runs a stale executable with a newer PATH. Direct callers
    // without a snapshot take the current configuration themselves.
    let config
    if (snapshot !== undefined) {
      try { config = this.snapshotConfig() }
      catch (error) {
        if (error instanceof ConfigurationError) fail('CONFIG_INVALID', error.message, 502)
        throw error
      }
      if (config.fingerprint !== snapshot.fingerprint) fail('CONFIG_CHANGED', 'Host plugin configuration changed; reopen this preview and re-authorize', 409)
      config = snapshot
    } else config = this.snapshotConfig()
    let managedArg = []
    if (isolatedWindow) {
      const supplier = config.managedPreviewSupplier ? await this.managedWindow(config, signal) : undefined
      const selected = supplier ? supplier.executable : config.previewWindowExecutable
      if (executable !== selected || this.snapshotConfig().fingerprint !== config.fingerprint) {
        fail('CONFIG_CHANGED', 'The configured independent window launcher changed; reopen preview', 409)
      }
      if (config.managedPreviewSupplier) {
        const [, , , wheel, projection] = config.managedPreviewSupplier.split(':')
        if (!supplier || supplier.selection !== config.managedPreviewSupplier || supplier.wheelSha256 !== wheel ||
            supplier.runtimeProjectionSha256 !== projection) {
          fail('WINDOW_UNSUPPORTED', 'Managed preview requires the confirmed release and the owner-selected pins', 422)
        }
        managedArg = [`--managed-preview-pins-v1=${wheel}:${projection}`]
      }
    }
    if (!isolatedWindow && config.vtxConfigFile) {
      if (typeof this.validateConfigSelection !== 'function') fail('CONFIG_TRUST_REQUIRED', 'Explicit VTX_CONFIG requires Host path trust validation', 403)
      try { await this.validateConfigSelection(config, signal) }
      catch (error) {
        if (signal.aborted) throw signal.reason
        if (error instanceof PreviewError) throw error
        fail(error.code ?? 'CONFIG_UNTRUSTED', error.message ?? 'VTX_CONFIG path is not trusted', error.status ?? 403)
      }
      // Never spawn against a newer selection than the async path check used.
      if (this.snapshotConfig().fingerprint !== config.fingerprint) fail('CONFIG_CHANGED', 'Host configuration changed during VTX_CONFIG validation', 409)
    }
    if (beforeSpawn) {
      const admit = await beforeSpawn(signal)
      if (this.snapshotConfig().fingerprint !== config.fingerprint) fail('CONFIG_CHANGED', 'Host configuration changed during identity validation', 409)
      signal.throwIfAborted()
      if (typeof admit === 'function') admit()
    }
    signal.throwIfAborted()
    const framed = typeof streaming === 'object' ? streaming : undefined
    const archiveRead = args[0] === 'timeline' && args[1] === 'preview-formal-archive'
    const prime = warm?.prime
    if (prime && this.idleEditView !== prime) return
    const slot = this.idleEditView
    const take = !prime && warm?.writeGrant && slot?.state === 'idle' && !slot.signal.aborted &&
      slot.executable === executable && slot.timeline === timeline && slot.owner === owner &&
      slot.writeGrant === warm.writeGrant && slot.fingerprint === config.fingerprint
    // No await between matching, consuming and dispatch. Once taken, even a
    // throwing end() is ambiguous: use the SAME done/cleanup path, never spawn a fallback.
    if (take) { this.idleEditView = undefined; slot.state = 'taken'; this.detachIdle(slot) }
    const handle = take ? slot.handle : this.subprocess.spawn({
      argv: [executable, ...managedArg, ...(timeline === undefined ? [] : ['-t', timeline]), ...args],
      cwd: dirname(executable), // Never execute Python/imports from an untrusted project cwd.
      stdio: { stdin: prime ? 'pipe' : input === undefined ? 'ignore' : { data: input }, stdout: streaming ? 'pipe' : { maxBytes: 8 * 1024 * 1024 }, stderr: { maxBytes: 16 * 1024 } },
      graceMs: 1000, signal,
      env: { PATH: config.trustedPath, VTX_CONFIG: isolatedWindow ? undefined : config.vtxConfigFile || undefined, PYTHONDONTWRITEBYTECODE: '1', PYTHONSAFEPATH: '1', PYTHONPATH: undefined, PYTHONHOME: undefined },
    })
    this.active.set(handle, undefined)
    this.spawnKinds.set(handle, prime ? 'store-idle' : spawnKind(args))
    this.spawnOwners.set(handle, owner)
    if (prime) {
      prime.handle = handle
      try {
        if (this.idleEditView !== prime || prime.signal.aborted) { await this.cleanup(handle); return }
        if (!handle.stdin || typeof handle.stdin.end !== 'function') fail('STDIN_PIPE_UNSUPPORTED', 'Public provider did not expose the requested owner stdin pipe', 502)
        prime.state = 'idle'
        prime.stdinFailure = new Promise((_, reject) => {
          handle.stdin.once('error', error => {
            reject(error)
            if (this.idleEditView === prime && prime.state === 'idle') void this.reapIdle(owner).catch(() => {})
          })
        })
        void prime.stdinFailure.catch(() => {})
        // A pre-EOF exit is not a reusable worker; retain ownership until reap.
        void Promise.resolve(handle.done).then(() => {
          if (this.idleEditView === prime) return this.reapIdle(owner)
        }, () => {
          if (this.idleEditView === prime) return this.reapIdle(owner)
        }).catch(() => {})
        if (prime.signal.aborted) await this.cleanup(handle)
        return
      } catch (error) { await this.cleanup(handle); throw error }
    }
    try {
      if (take) handle.stdin.end(input)
      if (streaming && !handle.stdout) fail('FRAME_STREAM_UNSUPPORTED', 'Public provider did not expose the requested owner stdout pipe', 502)
      const [outcome, streamed] = await untilAborted(Promise.all([
        framed ? handle.done.then(outcome => {
          if (outcome.exitCode !== 0 || outcome.signal !== null) framed.clear()
          if (outcome.signal !== null || ![0, 1].includes(outcome.exitCode)) fail('CLI_FAILED', 'The fixed wheel did not exit normally', 502)
          return outcome
        }) : take ? Promise.race([handle.done, slot.stdinFailure]) : handle.done,
        framed ? readFirstFrameStream(handle.stdout, signal, framed)
          : streaming ? boundedText({ headers: new Headers(), body: Readable.toWeb(handle.stdout) }, FRAME_WIRE_LIMIT, signal, { preserveBOM: archiveRead }) : undefined,
      ]), signal)
      signal.throwIfAborted()
      if (framed) {
        if ((outcome.exitCode === 0) !== streamed.ok) firstFrameMismatch()
        return streamed
      }
      const archive = args[0] === '--formal-archive-capabilities' || archiveRead
      if ((archive && outcome.signal !== null) || (outcome.exitCode !== 0 && !((business || archiveRead) && outcome.exitCode === 1))) {
        const error = new PreviewError(outcome.exitCode === 2 ? 'CLI_UNSUPPORTED' : 'CLI_FAILED',
          outcome.exitCode === 2 ? 'Installed vtx does not support this read command' : 'vtx rejected this timeline or failed', 502)
        if (!isolatedWindow) {
          const diagnostic = handle.collected.stderr?.readFrom(0)
          error.details = { exitCode: outcome.exitCode, stderr: diagnostic?.text ?? '', stderr_lossy: diagnostic?.lossy ?? false }
          const output = handle.collected.stdout?.readFrom(0)
          if (output && !output.lossy) {
            try { error.details.diagnostics = parseJSON(output.text).diagnostics } catch { /* Retain stderr when error JSON is absent. */ }
          }
        }
        throw error
      }
      const output = streaming ? { text: streamed, lossy: false } : handle.collected.stdout?.readFrom(0)
      if (!output || output.lossy) fail('TRUNCATED_JSON', 'vtx JSON exceeded the 8 MiB read limit; refusing truncated preview', 502)
      const result = archiveRead ? decode(output.text, outcome.exitCode) : decode(output.text)
      if (business && (outcome.exitCode === 0) !== result.ok) fail('PROTOCOL_MISMATCH', 'Store exit/envelope mismatch', 502)
      return result
    } catch (error) {
      framed?.clear()
      throw error
    } finally {
      try { await this.cleanup(handle) }
      catch (error) { framed?.clear(); throw error }
      signal.throwIfAborted()
    }
  }
  cleanup(handle) {
    if (!this.active.has(handle)) return Promise.resolve()
    const pending = this.active.get(handle)
    if (pending) return pending
    const slot = this.idleEditView?.handle === handle ? this.idleEditView : undefined
    if (slot) { slot.state = 'retiring'; this.detachIdle(slot); slot.controller.abort() }
    const task = (async () => {
      // A successful read owns no background work. Reclaim helpers even if they
      // closed stdio and the direct command already exited. Never use the aborted
      // request signal for cleanup: it must observe actual range quiescence.
      const controller = new AbortController()
      const timer = setTimeout(() => controller.abort(), 5000)
      try {
        handle.terminate()
        const [, empty] = await untilAborted(Promise.all([
          handle.done.catch(() => {}), handle.waitForExit(controller.signal),
        ]), controller.signal)
        if (empty !== true) throw new Error('Managed range did not exit')
        this.active.delete(handle)
        this.cleanupFailed.delete(handle)
        if (slot && this.idleEditView === slot) this.idleEditView = undefined
      } catch {
        this.cleanupFailed.add(handle)
        fail('PROCESS_CLEANUP_FAILED', 'vtx managed process range could not be confirmed stopped within 5 seconds; ownership retained for unload retry', 502)
      } finally { clearTimeout(timer) }
    })()
    this.active.set(handle, task)
    void task.catch(() => {
      if (this.active.get(handle) === task) this.active.set(handle, undefined)
    })
    return task
  }
  async dispose() {
    this.lifecycle.abort()
    const results = await Promise.allSettled([...this.active.keys()].map(handle => this.cleanup(handle)))
    const failures = results.filter(result => result.status === 'rejected').map(result => result.reason)
    if (failures.length) throw new AggregateError(failures, 'vtx process cleanup failed; managed ranges remain owned')
  }
}

/** Bound command/result waits even if a provider fails to settle done on abort. */

/** Classify a spawned child for semantic admission. Store capabilities are a
 * pure static probe; every other store command is a transaction that may touch
 * the lock/manifest, and frame children own render state. */
function spawnKind(args) {
  if (args[0] === 'storage') return args[1] === 'clean' ? 'storage-clean' : 'storage-read'
  if (args[0] === 'store') return args[1] === 'capabilities' ? 'store-capabilities' : 'store'
  if (args[0] === 'timeline' && ['frame', 'preview-window', 'preview-window-first-frame', 'preview-formal-archive'].includes(args[1])) return 'frame'
  return 'read'
}

function untilAborted(promise, signal) {
  return new Promise((resolve, reject) => {
    const abort = () => reject(signal.reason)
    signal.addEventListener('abort', abort, { once: true })
    Promise.resolve(promise).then(resolve, reject).finally(() => signal.removeEventListener('abort', abort))
    if (signal.aborted) { signal.removeEventListener('abort', abort); abort() }
  })
}

/**
 * Bounded, sanitized request/rejection diagnostics for the Host operator.
 * Wire shape is fixed: `[framivra-dsh-sidebar] <route> op=<op> code=<code> status=<n> ms=<n>`.
 * NEVER log request bodies, timelines, grants/tokens, session ids, paths or
 * media identity — only whitelisted route/op/code tokens and bounded numbers.
 * A strict per-minute rate cap (plus one cap notice and a rollover summary)
 * keeps a chatty failure source from flooding the host log. The sink is
 * injectable so tests verify content and rate limits without the console.
 * The cap may be supplied as a zero-arg provider, re-read per line, so the
 * host's volatile configuration applies live without resetting the window.
 */
export function createRequestLogger(options = {}) {
  const sink = typeof options.sink === 'function' ? options.sink : null
  // The cap is VOLATILE: a plain number pins it for tests, a zero-arg provider
  // is re-read on every failure() so live configuration changes take effect
  // immediately. Reading must never break the caller's business failure.
  const capProvider = typeof options.maxPerMinute === 'function'
    ? options.maxPerMinute
    : () => (Number.isSafeInteger(options.maxPerMinute) && options.maxPerMinute >= 0 ? options.maxPerMinute : 120)
  let windowStart = -Infinity
  let counted = 0
  let suppressed = 0
  let capNoticeSent = false
  const token = value => (typeof value === 'string' && /^[A-Za-z0-9_.-]{1,64}$/.test(value) ? value : undefined)
  const emit = (kind, line) => {
    try {
      if (sink) sink(kind, line)
      // The bounded stack record (console.error) is emitted separately by the
      // host for unexpected failures; these one-liners stay on console.warn so
      // operators can filter them and stack-logging behavior stays stable.
      else console.warn(line)
    } catch { /* a failed sink must never replace the failure */ }
  }
  return {
    /**
     * One sanitized failure line. kind: 'rejected' (business refusal, incl.
     * PreviewError codes) or 'unexpected' (non-PreviewError). Cancelled
     * requests are not failures and are never logged by the caller.
     */
    failure({ route, op, code, status, durationMs, kind = 'rejected' }) {
      // Per-log volatile cap read. Invalid/throwing reads skip the line
      // silently (fail closed); the window counters are untouched so a later
      // valid read resumes the SAME minute's rate limiting — no reset.
      let maxPerMinute
      try {
        const read = capProvider()
        maxPerMinute = Number.isSafeInteger(read) && read >= 0 ? read : undefined
      } catch { maxPerMinute = undefined }
      if (maxPerMinute === undefined) return
      const now = Date.now()
      if (!(now - windowStart < 60_000)) {
        // Rollover summary only for windows that emitted lines: a fully
        // suppressed window (e.g. cap=0, or reads failing throughout) must
        // stay completely silent — no summary, no notice.
        if (suppressed > 0 && counted > 0) emit('suppressed', `[framivra-dsh-sidebar] ${suppressed} further failure line(s) suppressed in the last minute`)
        windowStart = now
        counted = 0
        suppressed = 0
        capNoticeSent = false
      }
      if (counted >= maxPerMinute) {
        // cap=0 (and any fully-capped window) stays completely silent — no
        // notice line, no per-failure output; suppressed still counts for the
        // rollover summary.
        if (maxPerMinute > 0 && !capNoticeSent) {
          capNoticeSent = true
          emit('suppressed', `[framivra-dsh-sidebar] failure lines capped at ${maxPerMinute}/min; further lines in this window are suppressed`)
        }
        suppressed++
        return
      }
      counted++
      const parts = [
        `[framivra-dsh-sidebar] ${token(route) ?? 'route'}`,
        `op=${token(op) ?? '?'}`,
        `code=${token(code) ?? (kind === 'unexpected' ? 'PREVIEW_FAILED' : 'REJECTED')}`,
        `status=${Number.isSafeInteger(status) ? status : 0}`,
        `ms=${Number.isSafeInteger(durationMs) && durationMs >= 0 ? Math.min(durationMs, 3_600_000) : 0}`,
      ]
      emit(kind === 'unexpected' ? 'unexpected' : 'rejected', parts.join(' '))
    },
  }
}
