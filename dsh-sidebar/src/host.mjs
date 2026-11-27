/** Host-only orchestration through public DSH filesystem/process capabilities. */
import { createHash, createHmac, randomBytes, randomUUID, timingSafeEqual } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { dirname, isAbsolute, join, win32 } from 'node:path'
import { fileURLToPath } from 'node:url'
import { PreviewError, TimelineReader, validateEnvelope, prepareGfxArgs, prepareGfxCapable, hitsWarmSvgWindow, validatePreviewWindowRange, validateFormalArchiveRange, fail, HOST_IDENTITY, createRequestLogger, BAKED_BUILD_STAMP, BUILD_STAMP_MARKER, HOST_LIB_MODULES, CLIENT_MODULES, composeBuildStamp } from './protocol.mjs'
import { mediaResponse } from './media.mjs'
import { boundedText, CONTROL_REQUEST_LIMIT, FRAME_REQUEST_LIMIT, FRAME_WIRE_LIMIT, PROGRAM_RUN_MAX_WINDOWS, PROGRAM_RUN_MAX_BYTES, PROGRAM_RUN_WINDOW_BYTES, PROGRAM_RUN_TTL } from './bounded.mjs'
import { parseJSON, stringifyJSON, LosslessJSONError } from './json.mjs'
import { createTimelineAgent } from './agent.mjs'
import { ConfigurationError, readLogMaxPerMinute } from './configuration.mjs'
import { validateKernelConfigSelection } from './kernel-management.mjs'

/**
 * Deterministic build stamp over the module list in protocol.mjs: per-file
 * sha256 digests joined with '\n', re-hashed, first 16 hex, 'src:'-prefixed.
 * Shared verbatim with scripts/build.mjs (which injects the same value into
 * the client bundle through its define) — keep the two in lockstep.
 */
export function computeSourceStampFromDir(dir) {
  const digests = []
  for (const name of HOST_LIB_MODULES) digests.push(createHash('sha256').update(readFileSync(join(dir, `${name}.mjs`))).digest('hex'))
  for (const name of CLIENT_MODULES) digests.push(createHash('sha256').update(readFileSync(join(dir, 'client', `${name}.mjs`))).digest('hex'))
  return composeBuildStamp('src:', createHash('sha256').update(digests.join('\n')).digest('hex'))
}

/**
 * Build identity of THIS running host code, FROZEN at module load — never
 * recomputed, never re-read from disk. Packaged builds: package.mjs baked the
 * artifact hash into the BAKED_BUILD_STAMP literal (substituted before this
 * module ever ships, so the marker branch is dead there). Source/dev runs:
 * the source tree is hashed once, synchronously, while this module loads.
 * A host process therefore always reports the identity of the code it
 * actually loaded, even if the files on disk are replaced underneath it —
 * exactly the property that lets a newer client detect a stale running host
 * deterministically instead of guessing from version strings.
 */
const BUILD_STAMP = BAKED_BUILD_STAMP !== BUILD_STAMP_MARKER
  ? composeBuildStamp('pkg:', BAKED_BUILD_STAMP)
  : computeSourceStampFromDir(dirname(fileURLToPath(import.meta.url)))
export function resolveBuildStamp() { return BUILD_STAMP }

/** Identity reported in hello/open; build is the frozen per-process stamp. */
function hostIdentity() {
  return { ...HOST_IDENTITY, build: BUILD_STAMP }
}

const MEDIA = ['out/final.mp4', 'out/draft_final.mp4']
const PREVIEW_NONCE_LIMIT = 128
const SESSION_WINDOW_LIMIT = 120, SESSION_WINDOW_TTL = 10 * 60 * 1000
const continuationCapable = capability => capability?.input_epoch === true
// Both bits must come from the same verified direct launcher capability as ordinary continuation.
const programAudioContinuationCapable = capability => continuationCapable(capability) && capability.program_audio === true
const sessionQualification = (ownerId, capability) => ({ capability: 'preview-session', version: 1, ownerId,
  ...(continuationCapable(capability) && { continuation: { version: 1 }, programRun: { version: 1 } }),
  ...(programAudioContinuationCapable(capability) && { programAudioContinuation: { version: 1 } }),
  ...(capability.first_frame?.version === 1 && { firstFrame: { version: 1 } }),
  ...(capability.formal_archive?.version === 1 && { formalArchive: { version: 1 } }) })

class WindowSessions {
  #receiptKey = randomBytes(32)
  constructor(clock) { this.clock = clock; this.leases = new Map() }
  #tag(nonce, sessionId, openGrant, ownerId) {
    return createHmac('sha256', this.#receiptKey).update(JSON.stringify([nonce, sessionId, openGrant, ownerId])).digest('hex')
  }
  active(lease) { return !lease.revoked && this.clock() < lease.info.expiresAt }
  activeFor(session, programOnly = false) { return [...this.leases.values()].some(lease => lease.open.session === session && this.active(lease) && (!programOnly || lease.run)) }
  activeProgramForProject(project) { return [...this.leases.values()].some(lease => lease.run && lease.open.project.targetKey === project.targetKey && this.active(lease)) }

  authorize(open, data, programBinding) {
    if (!open.owner || !open.windowOwnerId || open.closing || !open.windowExecutable) fail('SESSION_UNSUPPORTED', 'Open a qualified owner preview first', 403)
    if ((programBinding ? data.scope !== 'whole-timeline' : data.scope !== 'current-project') || data.renderConsent !== 'cache/preview-only' || data.prepareInputs !== true) fail('SESSION_CONSENT', 'Separate render and cold-input permissions are required', 403)
    const { capability } = open.windowExecutable
    if (programBinding && (!continuationCapable(capability) || this.activeFor(open.session))) fail('PROGRAM_RUN_BUSY', 'Requires input identity support and no other preview session', 409)
    if (!programBinding && this.activeFor(open.session, true)) fail('SESSION_BUSY', 'Stop the whole-timeline preview before authorizing another scope', 409)
    if (Object.hasOwn(data, 'continuation') && (data.continuation !== true || !continuationCapable(capability))) fail('CONTINUATION_DENIED', 'Continuous preview requires separate consent and input identity support', 403)
    if (Object.hasOwn(data, 'readWarmSvg') && (data.readWarmSvg !== true || capability.warm_svg_media !== true)) fail('WARM_SVG_DENIED', 'Warm SVG reads require exact owner consent and a qualified fixed-wheel capability', 403)
    if (Object.hasOwn(data, 'useProgramAudio') && (data.useProgramAudio !== true || capability.program_audio !== true ||
        Object.hasOwn(data, 'firstFrame') || (data.continuation === true && !programAudioContinuationCapable(capability)))) {
      fail('PROGRAM_AUDIO_DENIED', 'Program audio requires separate fixed-wheel owner consent, no provisional visual, and specific qualification to combine with continuation', 403)
    }
    if (Object.hasOwn(data, 'firstFrame') && (data.firstFrame !== true || open.windowExecutable.capability.first_frame?.version !== 1 || data.continuation || data.readWarmSvg)) {
      fail('FIRST_FRAME_DENIED', 'Provisional visual requires independent consent and separate fixed-wheel qualification, without warm SVG or continuation', 403)
    }
    for (const lease of this.leases.values()) {
      if (lease.open === open && (this.active(lease) || open.windowOperation?.lease === lease)) fail('SESSION_BUSY', 'Revoke and settle the previous preview session first', 409)
    }
    if (this.leases.size >= 64) {
      for (const [id, lease] of this.leases) {
        if (!this.active(lease) && lease.open.windowOperation?.lease !== lease) { this.leases.delete(id); break }
      }
      if (this.leases.size >= 64) fail('SESSION_LIMIT', 'No preview session capacity', 429)
    }
    const nonce = randomUUID()
    const id = `${nonce}.${this.#tag(nonce, open.session.id, open.id, open.windowOwnerId)}`
    const programRun = programBinding && Object.freeze({ version: 1, contentVersion: data.contentVersion,
      frame: data.frame, duration: programBinding.duration, span: programBinding.span, maxBytes: PROGRAM_RUN_MAX_BYTES })
    const info = Object.freeze({ id, ownerId: open.windowOwnerId, openGrant: open.id,
      sessionId: open.session.id, project: open.relative, ...(data.continuation === true && { continuation: true }),
      ...(data.readWarmSvg === true && { readWarmSvg: true }), ...(data.firstFrame === true && { firstFrame: true }),
      ...(data.useProgramAudio === true && { useProgramAudio: true }), ...(programRun && { programRun }),
      expiresAt: Math.min(open.expiresAt, this.clock() + (programRun ? PROGRAM_RUN_TTL : SESSION_WINDOW_TTL)),
      maxWindows: programRun ? PROGRAM_RUN_MAX_WINDOWS : SESSION_WINDOW_LIMIT })
    const run = programRun && { timelineVersion: programBinding.timelineVersion, entries: new Map(), bytes: 0 }
    this.leases.set(info.id, { info, open, revoked: false, nonces: new Set(), run })
    return info
  }

  original(data, cleanup = false) {
    const lease = this.leases.get(data.previewGrant)
    if (!lease && cleanup && typeof data.previewGrant === 'string') {
      // Eviction above is possible only after retirement and actual settlement.
      // An authenticated old tuple can confirm that fact, never start new work.
      const match = /^([a-f0-9-]{36})\.([0-9a-f]{64})$/.exec(data.previewGrant)
      if (match && timingSafeEqual(Buffer.from(match[2], 'hex'), Buffer.from(this.#tag(match[1], data.sessionId, data.grant, data.ownerId), 'hex'))) {
        return { revoked: true, info: { id: data.previewGrant, ownerId: data.ownerId, openGrant: data.grant } }
      }
    }
    if (!lease || lease.info.openGrant !== data.grant || lease.info.ownerId !== data.ownerId || lease.info.sessionId !== data.sessionId) fail('BAD_SESSION_GRANT', 'Use the original owner, Session and both grants', 403)
    return lease
  }

  reserve(lease, open, id) {
    if (lease.run || lease.open !== open || open.closing || !this.active(lease)) fail('SESSION_EXPIRED', 'Automatic window authorization is no longer active', 403)
    if (lease.nonces.has(id)) fail('WINDOW_REPLAY', 'A window intent is single-use', 409)
    if (lease.nonces.size >= SESSION_WINDOW_LIMIT) fail('SESSION_LIMIT', 'Automatic window budget exhausted', 429)
    lease.nonces.add(id)
  }

  reserveProgram(lease, open, frame) {
    const { run, info } = lease
    if (!run || lease.open !== open || open.closing || !this.active(lease)) fail('PROGRAM_RUN_EXPIRED', 'Whole-timeline scope is no longer active', 403)
    const { frame: pin, duration, span } = info.programRun
    if (frame >= duration || frame % span || (!run.entries.size && frame !== pin - pin % span)) fail('BAD_PROGRAM_RUN', 'Use the canonical window grid, starting at the clicked playhead', 409)
    if (run.entries.has(frame)) fail('WINDOW_REPLAY', 'A whole-timeline window may be dispatched only once', 409)
    if (run.entries.size >= info.maxWindows || run.bytes >= info.programRun.maxBytes) fail('PROGRAM_RUN_LIMIT', 'Whole-timeline scope exhausted its window or byte budget', 429)
    run.entries.set(frame, undefined) // Failed or interrupted work is not counted as coverage and is never retried.
  }

  seal(lease, id) {
    // A cancel may overtake the HTTP render. A revoked, expired or full
    // lease can never admit an unknown nonce, without retaining extra IDs.
    if (this.active(lease) && lease.nonces.size < SESSION_WINDOW_LIMIT) lease.nonces.add(id)
  }

  closeOpen(open) { for (const lease of this.leases.values()) if (lease.open === open) lease.revoked = true }
  closePrograms(matches) {
    for (const lease of this.leases.values()) if (lease.run && !lease.revoked && matches(lease.open)) {
      lease.revoked = true
      if (lease.open.windowOperation?.lease === lease) lease.open.frame?.abort()
    }
  }
}

export function relativeTimeline(path) {
  if (typeof path !== 'string' || !path || path.includes('\0') || path.includes('\\') || path.includes(':') || isAbsolute(path) || path.split('/').some(part => !part || part === '.' || part === '..')) {
    fail('BAD_PATH', 'Choose a relative timeline file inside the current Session workspace; URLs and external paths are not supported')
  }
  return path
}
/** One-level browse relative path: '' (or absent/null) means the workspace
 *  root; every other value takes the same rejects as relativeTimeline. */
export function relativeBrowsePath(path) {
  if (path === '' || path === null || path === undefined) return ''
  if (typeof path !== 'string' || !path || path.includes('\0') || path.includes('\\') || path.includes(':') || isAbsolute(path) || path.split('/').some(part => !part || part === '.' || part === '..')) {
    fail('BAD_PATH', 'Choose a relative workspace directory inside the current Session workspace; URLs and external paths are not supported')
  }
  return path
}
const json = (value, status = 200) => new Response(stringifyJSON(value), { status, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff' } })
const inlineCapable = value => value?.capabilities?.frame?.schema === 'framivra.timeline.frame/v1' && value.capabilities.frame.explicit_render_required === true && value.capabilities.frame.inline_png?.flag === '--include-png' && value.capabilities.frame.inline_png.encoding === 'base64' && value.capabilities.frame.inline_png.wire_limit_bytes === FRAME_WIRE_LIMIT
const nativeWindows = () => process.platform === 'win32'
const platformAllowsWrites = () => ['linux', 'darwin', 'win32'].includes(process.platform)
const windowsStoreOperations = ['edit', 'adopt', 'undo', 'status', 'recover']
// The kernel validates its own macOS Python/FFmpeg architecture and closure;
// the architecture of the DSH Node process is not a proxy for either.
const platformAllowsFrame = () => ['linux', 'darwin'].includes(process.platform)
const platformAllowsWindow = () => process.platform === 'linux' // Experimental kernel producer is Linux-only.
const writePlatformMessage = '当前宿主不支持内核语义写入；仍可读取时间线、复制或引用。'
const framePlatformMessage = '当前宿主不支持生成完整合成帧；可继续查看时间线与播放已有成片。'
const writeUnavailable = message => ({ ok: false, error: 'WRITE_UNSUPPORTED', message })
const editViewCapable = value => value?.edit_view?.command === 'edit-view' && value.edit_view.read_schema === 'framivra.timeline.read/v1' && value.edit_view.wire_limit_bytes === 8388608
const revisionShape = value => typeof value === 'string' && /^sha256:[a-f0-9]{64}$/.test(value)
const uuidShape = value => typeof value === 'string' && /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i.test(value)

/** Receipt proof is separate from view proof: a bad view must not hide an applied edit. */
function appliedEditReceipt(result, request) {
  return result?.schema === 'framivra.timeline.store/v1' && result.ok === true && result.operation === 'edit' &&
    result.op_id === request.op_id && result.base_revision === request.base_revision && revisionShape(result.revision) &&
    revisionShape(result.current_revision) && typeof result.replayed === 'boolean'
}
function validateEditView(result, request) {
  if (!appliedEditReceipt(result, request) || result.replayed !== false || result.current_revision !== result.revision) fail('PROTOCOL_MISMATCH', 'Exact non-replayed edit receipt required', 502)
  const snapshot = validateEnvelope(result.view?.snapshot, 'inspect')
  const query = validateEnvelope(result.view?.result, 'query')
  if (snapshot.content_version !== result.revision || query.content_version !== result.revision || snapshot.document._vtx?.head !== request.op_id) fail('PROTOCOL_MISMATCH', 'Edit view must contain matching saved revision/head and full query', 502)
}

function previewCapabilities(value) {
  // Ordinary vtx may describe a wire format but never authorizes windows.
  // Only an owner open with a separately qualified launcher can add one back.
  const capabilities = { ...value.capabilities }
  if (!platformAllowsFrame() || !inlineCapable(value)) delete capabilities.frame
  delete capabilities.preview_window
  return { ...value, capabilities }
}

function writeCapabilityRefusal(capabilities) {
  if (process.platform === 'darwin' && capabilities.platform_support?.darwin !== true) return 'Installed vtx lacks macOS-safe timeline locking and path identity; update the core before authorizing writes.'
  if (!capabilities.ok || !Array.isArray(capabilities.operations) || !capabilities.operations.includes('edit') || !Array.isArray(capabilities.edit?.operations) || !capabilities.edit.operations.length) return 'Installed vtx does not advertise semantic edit/plan'
  if (nativeWindows() && (capabilities.platform_support?.win32 !== true || !windowsStoreOperations.every(op => capabilities.operations.includes(op)))) return 'Installed vtx lacks the native Windows NTFS edit and recovery store; update the core before authorizing writes.'
  const transport = capabilities.request_transport?.stdin
  if (transport?.flag !== '--request-stdin' || transport.max_bytes !== 8388608) return 'Installed vtx lacks the official bounded --request-stdin transport; read/copy remain available. Reopening /dev/stdin is not a supported fallback.'
}

export function createPreviewHost(ctx, config = {}) {
  const reader = new TimelineReader(ctx.subprocess, config, async (snapshot, signal) => {
    try { return await validateKernelConfigSelection(ctx, snapshot, signal) }
    catch (error) {
      if (typeof error?.code === 'string' && Number.isInteger(error?.status)) fail('CONFIG_INVALID', error.message, error.status)
      throw error
    }
  })
  const lifecycle = new AbortController()
  const grants = new Map()
  const operations = new Set()
  const streams = new Set()
  const frameOwners = new Set()
  const retiredWindowReceipts = new Map()
  const storageOwners = new Set()
  // Keep Windows store reservations and uncertain outcomes on the physical
  // timeline, not the tab. Closing or opening a second tab must not bypass it.
  const windowsStoreActive = new Set()
  const windowsRecovery = new Map()
  // Explicit FIFO: one running frame plus at most four waiting clicks. Each
  // entry owns its grant/controller/deadline from admission through cleanup.
  const frameQueue = []
  let semanticAdmission = 0
  const clock = config.clock ?? Date.now
  const automatic = new WindowSessions(clock)
  const ttlMs = 30 * 60 * 1000
  // Sanitized, rate-bounded diagnostics: config.logger(level, line) injectable
  // for tests; logMaxPerMinute comes from the validated configuration schema
  // (single home: configuration.mjs readLogMaxPerMinute — bounds enforced even
  // for directly constructed config objects that bypass the schema). The cap
  // is re-read per logged line so VOLATILE live changes take effect without a
  // host restart and without resetting the current minute's counters; a read
  // error skips the line, never the business failure. Never receives bodies.
  // Logging limits deliberately do NOT join the grant fingerprint: changing
  // them must not invalidate open previews.
  const requestLog = config.requestLogger ?? createRequestLogger({ sink: config.logger, maxPerMinute: () => readLogMaxPerMinute(config) })

  /** Current live configuration snapshot; CONFIG_INVALID is fail-closed (502). */
  function snapshotConfig() {
    try { return reader.snapshotConfig() }
    catch (error) {
      if (error instanceof ConfigurationError) fail('CONFIG_INVALID', error.message, 502)
      throw error
    }
  }
  /** Whether the live configuration still matches the one this grant was minted under. */
  function configCurrent(grant) {
    try { return snapshotConfig().fingerprint === grant.configFingerprint }
    catch { return false } // An invalid new configuration also invalidates every old grant.
  }

  function sessionFor(id) {
    if (typeof id !== 'string' || !id) fail('BAD_SESSION', 'A Session is required')
    const session = ctx.sessions.get(id)
    if (!session) fail('BAD_SESSION', 'Session is unavailable', 404)
    return session
  }
  async function localTarget(path, cwd, signal) {
    const target = await ctx.fs.resolve(path, { cwd, signal })
    const processPath = ctx.fs.processPath(target)
    if (!isAbsolute(processPath) || ctx.fs.processPathFromHostPath(processPath) !== processPath) {
      fail('REMOTE_UNSUPPORTED', 'This preview supports local shared-path filesystems only; remote mapping is unavailable', 422)
    }
    return target
  }
  async function windowExecutableIdentity(executable, root, project, ordinaryExecutable, signal) {
    const target = await localTarget(executable, undefined, signal)
    const profilePath = dirname(executable)
    const profile = await localTarget(profilePath, undefined, signal)
    const overlaps = other => ctx.fs.contains(other, profile) || ctx.fs.contains(profile, other)
    // Require the exact physical spelling for both the launcher and its cwd.
    // The launcher itself verifies ownership and the wheel files in this profile.
    if (ctx.fs.processPath(target) !== executable || ctx.fs.processPath(profile) !== profilePath ||
        target.targetKey === ordinaryExecutable.targetKey || overlaps(root) || overlaps(project)) {
      fail('WINDOW_EXECUTABLE_UNTRUSTED', 'The independent window launcher needs a separate physical profile outside project and Session workspaces', 403)
    }
    const sessions = ctx.sessions.list?.()
    if (!Array.isArray(sessions)) fail('WINDOW_EXECUTABLE_UNTRUSTED', 'The Host cannot verify the independent window profile against Session workspaces', 403)
    const checked = new Set([ctx.fs.processPath(root)])
    for (const session of sessions) {
      const cwd = ctx.workingDirectory.get(session)
      if (!cwd || checked.has(cwd)) continue
      if (overlaps(await localTarget(cwd, undefined, signal))) fail('WINDOW_EXECUTABLE_UNTRUSTED', 'The independent window profile overlaps a Session workspace', 403)
      checked.add(cwd)
    }
    const info = await ctx.fs.stat(target, signal)
    if (info?.type !== 'file' || typeof info.version !== 'string' || !info.version) fail('WINDOW_EXECUTABLE_UNTRUSTED', 'The independent window launcher has no regular-file identity', 403)
    return { executable, targetKey: target.targetKey, profileKey: profile.targetKey, version: info.version }
  }
  function sameWindowExecutable(a, b) {
    if (a.targetKey !== b.targetKey || a.profileKey !== b.profileKey || a.version !== b.version) fail('EXECUTABLE_CHANGED', 'Independent window launcher identity changed; reopen preview', 409)
  }
  async function probeWindow(config, root, project, timeline, ordinaryExecutable, signal, owner, deadlineAt) {
    if (!platformAllowsWindow() || !config.previewWindowExecutable) return
    try {
      const executable = await reader.windowExecutable(signal, config)
      const identify = () => windowExecutableIdentity(executable, root, project, ordinaryExecutable, signal)
      const binding = await identify()
      let capability = await reader.windowCapabilities(executable, ctx.fs.processPath(timeline), signal, config, owner)
      sameWindowExecutable(binding, await identify())
      // Do not spend an almost-expired legacy open on optional probes. Each
      // reserves 3 s, command cleanup (5 s), and a final identity check.
      const optionalProbe = async probe => {
        if (deadlineAt - performance.now() < 9_000) return
        const budget = AbortSignal.timeout(3_000)
        const probeSignal = AbortSignal.any([signal, budget])
        try { capability = await probe(executable, capability, probeSignal, config, owner) }
        catch (error) {
          signal.throwIfAborted()
          if (error?.code === 'PROCESS_CLEANUP_FAILED' || !(budget.aborted || error instanceof SyntaxError || error instanceof LosslessJSONError ||
              ['WINDOW_UNSUPPORTED', 'CLI_UNSUPPORTED', 'CLI_FAILED', 'FRAME_STREAM_UNSUPPORTED', 'TRUNCATED_JSON'].includes(error?.code) ||
              error?.message?.startsWith('BODY_LIMIT:'))) throw error
        }
        sameWindowExecutable(binding, await identify())
      }
      if (capability.input_epoch === true) await optionalProbe(reader.firstFrameCapabilities.bind(reader))
      await optionalProbe(reader.formalArchiveCapabilities.bind(reader))
      return Object.freeze({ ...binding, capability })
    } catch (error) {
      signal.throwIfAborted()
      // Failed qualification never takes over ordinary timeline opens. Actual
      // cancellation/config change or missing process reaping is not optional.
      if (error instanceof LosslessJSONError || ['WINDOW_UNSUPPORTED', 'WINDOW_EXECUTABLE_UNTRUSTED', 'EXECUTABLE_CHANGED', 'REMOTE_UNSUPPORTED',
        'CLI_UNSUPPORTED', 'CLI_FAILED', 'PROTOCOL_MISMATCH', 'INVALID_JSON', 'TRUNCATED_JSON', 'LOSSLESS_JSON_UNSUPPORTED'].includes(error?.code)) return
      throw error
    }
  }
  async function contained(path, cwd, parent, type, signal, optional = false) {
    const target = await localTarget(path, cwd, signal)
    if (!ctx.fs.contains(parent, target)) fail('PATH_ESCAPE', 'Resolved target escapes its authorized directory', 403)
    const info = await ctx.fs.stat(target, signal)
    // Optional entries (probe outputs) are skipped when absent OR of the wrong
    // type — a stale directory where a file is expected must not fail the whole
    // open. Containment was already enforced above; stat errors still propagate.
    if (optional && (!info || info.type !== type)) return undefined
    if (info?.type !== type) fail('NOT_REGULAR', `Expected an existing regular ${type}`, 400)
    return { target, info }
  }
  function revoke(grant) {
    automatic.closePrograms(open => open === grant)
    grant.authorization?.abort()
    grant.authorization = undefined
    grant.frame?.abort()
    grant.storage?.abort()
    grant.storagePlan = undefined
    grant.write?.abort()
    grant.write = undefined
    grant.writeCapabilities = undefined
    grant.plan = undefined
  }
  function rememberRetiredWindows(grant) {
    if (!grant.owner) return
    // Mark only that this exact Session/grant can never admit a window again.
    // An unsettled nonce stays reachable via frameOwners, not this bounded map.
    retiredWindowReceipts.delete(grant.id)
    retiredWindowReceipts.set(grant.id, { sessionId: grant.session.id, programOwnerId: grant.windowOwnerId,
      archiveOwnerId: grant.windowExecutable?.capability.formal_archive?.version === 1 ? grant.windowOwnerId : undefined })
    if (retiredWindowReceipts.size > 32) retiredWindowReceipts.delete(retiredWindowReceipts.keys().next().value)
  }
  function releaseWindowOwner(grant, operation) {
    if (grant.windowOperation !== operation || reader.activeFor(operation.owner)) return
    grant.windowOperation = undefined
    grant.frame = undefined
    frameOwners.delete(grant)
    if (!grants.has(grant.id) && !lifecycle.signal.aborted) rememberRetiredWindows(grant)
  }
  async function reapWindow(grant, operation = grant.windowOperation) {
    if (!operation) return
    await operation.done
    await reader.reap(operation.owner)
    releaseWindowOwner(grant, operation)
  }
  function expire() {
    for (const [id, grant] of grants) if (clock() >= grant.expiresAt) {
      revoke(grant)
      grants.delete(id)
      rememberRetiredWindows(grant)
      // Expiry cannot restore read/write authority. Keep window cancellation
      // addressable by its original tuple if this best-effort reap fails.
      reader.reap(id).then(() => storageOwners.delete(grant)).catch(() => {})
      void reapWindow(grant).catch(() => {})
    }
    // A program may be between jobs or already fully cached when cwd/config
    // changes. Retire that authority even if no request uses the old grant.
    automatic.closePrograms(open => {
      try { return ctx.sessions.get(open.session.id) !== open.session || ctx.workingDirectory.get(open.session) !== open.cwd || !configCurrent(open) }
      catch { return true }
    })
  }
  function grantFor(sessionId, id, allowStaleConfig = false) {
    expire()
    const session = sessionFor(sessionId)
    const grant = grants.get(id)
    if (!grant || grant.session !== session || grant.cwd !== ctx.workingDirectory.get(session)) {
      if (grant && (ctx.sessions.get(grant.session.id) === undefined || grant.cwd !== ctx.workingDirectory.get(grant.session))) revoke(grant)
      fail('BAD_GRANT', 'Preview grant expired, closed, belongs to another Session, or workspace changed', 403)
    }
    // Fail closed after a Host configuration change: old read grants AND old
    // write permissions die together, because the grant's bound executable and
    // trusted PATH are no longer the ones the administrator chose. Cleanup-only
    // operations (close, cancel, revoke-write, ...) still pass through so a
    // stale grant can always be torn down, never left half-alive.
    if (!allowStaleConfig && !configCurrent(grant)) {
      revoke(grant)
      fail('CONFIG_CHANGED', 'Host plugin configuration changed; reopen this preview and re-authorize', 409)
    }
    return grant
  }
  const archiveOwner = (grant, ownerId) => grant?.windowExecutable?.capability.formal_archive?.version === 1 &&
    typeof ownerId === 'string' && !!ownerId && grant.windowOwnerId === ownerId
  async function cancelWindow(data, signal, formalArchive = false) {
    const { sessionId, grant: id, operationId, ownerId } = data
    if (typeof sessionId !== 'string' || !sessionId || typeof id !== 'string' || !id) fail('BAD_GRANT', 'Cancel requires the original Session and grant', 403)
    expire()
    // This endpoint already passed owner Connection admission. Only the exact
    // old tuple may bypass a live Session/cwd check, and only for teardown.
    const matches = grant => grant?.owner && grant.session.id === sessionId && (!formalArchive || archiveOwner(grant, ownerId))
    const outstanding = [...frameOwners].find(grant => grant.id === id && matches(grant) && grant.windowOperation?.id === operationId &&
      grant.windowOperation.formalArchive === formalArchive && !grant.windowOperation.lease)
    if (outstanding) {
      outstanding.frame.abort()
      await reapWindow(outstanding)
    } else {
      const live = grants.get(id), retired = retiredWindowReceipts.get(id)
      const bound = matches(live), field = formalArchive ? 'archiveNonces' : 'windowNonces'
      const known = bound && live[field]?.has(operationId)
      // Detect a stale origin even when its operation had not reached admission.
      if (bound && !known && !live.closing && (ctx.sessions.get(sessionId) !== live.session || ctx.workingDirectory.get(live.session) !== live.cwd)) live.closing = true
      const retiredTuple = retired?.sessionId === sessionId && (!formalArchive ||
        (typeof ownerId === 'string' && !!ownerId && retired.archiveOwnerId === ownerId))
      const cannotAdmit = retiredTuple || (bound && (live.closing || live[field]?.size >= PREVIEW_NONCE_LIMIT))
      if (!cannotAdmit && !known) {
        const grant = grantFor(sessionId, id, true)
        if (!matches(grant)) fail('BAD_GRANT', 'Cancellation requires the original owner preview', 403)
        // A live grant with space can tombstone a cancel ahead of its operation.
        const nonces = grant[field] ??= new Set()
        nonces.add(operationId)
      }
    }
    signal.throwIfAborted()
    return json({ ok: true, reaped: true, ...(formalArchive && { operationId, ownerId, openGrant: id }) })
  }
  async function validateTimeline(grant, signal, allowStaleConfig = false) {
    if (!allowStaleConfig && !configCurrent(grant)) fail('CONFIG_CHANGED', 'Host plugin configuration changed; reopen this preview and re-authorize', 409)
    const root = await localTarget(grant.cwd, undefined, signal)
    if (root.targetKey !== grant.root.targetKey) fail('PATH_CHANGED', 'Workspace identity changed; reopen preview', 409)
    const result = await contained(grant.relative, grant.cwd, root, 'file', signal)
    if (result.target.targetKey !== grant.timeline.targetKey) fail('PATH_CHANGED', 'Timeline identity changed; reopen preview', 409)
    return result.target
  }
  async function programTimelineVersion(grant, signal) {
    const timeline = await validateTimeline(grant, signal)
    const project = await localTarget(grant.projectPath, undefined, signal)
    if (project.targetKey !== grant.project.targetKey) fail('PATH_CHANGED', 'Project identity changed; reopen preview', 409)
    const info = await ctx.fs.stat(timeline, signal)
    if (info?.type !== 'file' || typeof info.version !== 'string' || !info.version) fail('STALE_WINDOW', 'No current saved timeline identity', 409)
    signal.throwIfAborted()
    return info.version
  }

  async function validateProgram(lease, grant, signal) {
    const { run, info } = lease
    const live = () => run && lease.open === grant && !grant.closing && automatic.active(lease)
    if (!live()) fail('PROGRAM_RUN_EXPIRED', 'Whole-timeline scope is no longer active', 403)
    const current = await programTimelineVersion(grant, signal)
    grantFor(grant.session.id, grant.id)
    if (!live()) fail('PROGRAM_RUN_EXPIRED', 'Whole-timeline scope is no longer active', 403)
    if (current !== run.timelineVersion || info.programRun.contentVersion !== grant.contentVersion) {
      automatic.closePrograms(open => open === grant)
      fail('STALE_WINDOW', 'Timeline changed; discard all whole-timeline media', 409)
    }
    if (grant.running || grant.plan || grant.outcomeUnknown || semanticAdmission) fail('WRITE_BUSY', 'Resolve editing before using whole-timeline media', 409)
  }

  async function editViewTarget(grant, signal) {
    signal.throwIfAborted()
    grantFor(grant.session.id, grant.id)
    const timeline = await validateTimeline(grant, signal)
    const project = await localTarget(grant.projectPath, undefined, signal)
    if (project.targetKey !== grant.project.targetKey || (await ctx.fs.stat(project, signal))?.type !== 'directory') fail('PATH_CHANGED', 'Project identity changed; reopen', 409)
    const executable = await localTarget(grant.executable, undefined, signal)
    const info = await ctx.fs.stat(executable, signal)
    if (executable.targetKey !== grant.executableTarget.targetKey || info?.type !== 'file' || info.version !== grant.executableVersion || ctx.fs.contains(grant.root, executable)) fail('EXECUTABLE_CHANGED', 'Installed executable identity changed; reopen', 409)
    const path = ctx.fs.processPath(timeline)
    if (nativeWindows()) {
      // Do not discard a path alias while resolving the timeline, or use an
      // alias for argv[0] after checking the physical executable target.
      if (win32.normalize(path) !== win32.join(grant.cwd, grant.relative)) fail('PATH_CHANGED', 'Windows edits require the exact physical workspace and timeline path', 409)
      if (typeof info.version !== 'string' || !info.version || win32.normalize(grant.executable) !== win32.normalize(ctx.fs.processPath(executable))) fail('EXECUTABLE_CHANGED', 'Windows edits require a verifiable physical installed executable', 409)
      if (grant.closing) fail('BAD_GRANT', 'Preview is closing', 403)
    }
    grantFor(grant.session.id, grant.id)
    signal.throwIfAborted()
    return path
  }
  function primeEditView(grant) {
    if (!editViewCapable(grant.writeCapabilities) || grant.writeCapabilities.edit_view.preload_stdin !== true) return
    const write = grant.write
    // Detached from the HTTP lease: only the exact write grant and reader
    // lifecycle own this zero-byte EOF waiter. Priming never delays a response.
    queueMicrotask(() => {
      void (async () => {
        const path = ctx.fs.processPath(grant.timeline)
        const validate = async signal => {
          const current = () => grant.write === write && !write.signal.aborted && !grant.running && !grant.outcomeUnknown && !grant.editViewNeedsRefresh
          if (!current()) throw new DOMException('Write grant unavailable', 'AbortError')
          if (await editViewTarget(grant, signal) !== path || !current()) throw new DOMException('Write grant changed', 'AbortError')
        }
        await reader.primeEditView(grant.executable, path, write.signal, grant.configSnapshot, grant.id, write, validate)
      })().catch(() => {})
    })
  }
  function lease(request, deadline = true) {
    lifecycle.signal.throwIfAborted()
    const controller = new AbortController()
    const signal = AbortSignal.any([lifecycle.signal, request.signal, controller.signal])
    const deadlineAt = deadline ? performance.now() + 30_000 : undefined
    const timer = deadline ? setTimeout(() => controller.abort(new DOMException('Preview read deadline exceeded', 'TimeoutError')), 30_000) : undefined
    timer?.unref?.()
    let released = false
    const release = () => {
      if (released) return
      released = true
      clearTimeout(timer)
      controller.abort()
      streams.delete(release)
    }
    return { signal, release, deadlineAt }
  }
  /** Bound on one browse listing; beyond this the response is truncated. */
  const BROWSE_ENTRY_LIMIT = 500
  /** One-level workspace listing through public fs APIs only. The directory
   *  comes from the Host-owned Session cwd (never the browser); every entry's
   *  RESOLVED target must stay canonically inside that root — symlinks that
   *  escape are counted and omitted, never followed. This is a file picker,
   *  not a parser: selecting a timeline still goes through op 'open'/vtx. */
  async function browse(session, sessionId, relative, signal) {
    const cwd = ctx.workingDirectory.get(session) // Never accept cwd/root from the browser.
    const root = await localTarget(cwd, undefined, signal)
    if ((await ctx.fs.stat(root, signal))?.type !== 'directory') fail('BAD_WORKSPACE', 'Session workspace is unavailable')
    const target = relative === '' ? root : await localTarget(relative, cwd, signal)
    if (target !== root && !ctx.fs.contains(root, target)) fail('PATH_ESCAPE', 'Resolved target escapes its authorized directory', 403)
    if ((await ctx.fs.stat(target, signal))?.type !== 'directory') fail('NOT_A_DIRECTORY', 'Browse expects an existing workspace directory', 400)
    if (typeof ctx.fs.listDir !== 'function') fail('BROWSE_UNSUPPORTED', 'This filesystem provider does not support directory listing', 502)
    let listing
    try { listing = await ctx.fs.listDir(target, signal) }
    catch (error) {
      if (error instanceof PreviewError) throw error
      fail('BROWSE_UNSUPPORTED', 'Workspace directory could not be listed', 502)
    }
    if (!Array.isArray(listing)) fail('BROWSE_UNSUPPORTED', 'Filesystem provider returned an invalid listing', 502)
    const entries = []
    let truncated = false, escaped = 0
    for (const entry of listing) {
      if (!entry || typeof entry.name !== 'string' || !entry.name || entry.name.includes('/') || !['directory', 'file', 'other'].includes(entry.type)) {
        fail('BROWSE_UNSUPPORTED', 'Filesystem provider returned an invalid listing', 502)
      }
      if (!entry.target || typeof entry.target !== 'object') fail('BROWSE_UNSUPPORTED', 'Filesystem provider returned an unresolved entry', 502)
      // Canonical containment per entry against the RESOLVED target: a symlink
      // that leaves the workspace is reported (escaped) and omitted.
      if (!ctx.fs.contains(root, entry.target)) { escaped++; continue }
      // A name that could not itself be addressed by op 'browse'/'open' (dots,
      // separators, control bytes…) is not returnable — choosing it would only
      // produce a BAD_PATH later. Validate with the shared helper, skip on reject.
      try { relativeBrowsePath(entry.name) } catch { continue }
      if (entry.type !== 'directory' && !(entry.type === 'file' && entry.name.toLowerCase().endsWith('.json'))) continue
      if (entries.length >= BROWSE_ENTRY_LIMIT) { truncated = true; break }
      entries.push({ name: entry.name, path: relative === '' ? entry.name : `${relative}/${entry.name}`, type: entry.type })
    }
    entries.sort((a, b) => a.name < b.name ? -1 : a.name > b.name ? 1 : 0)
    signal.throwIfAborted()
    // The listing must still describe the same Session/cwd it started from.
    if (ctx.workingDirectory.get(session) !== cwd || ctx.sessions.get(sessionId) !== session) fail('PATH_CHANGED', 'Session workspace changed; browse again', 409)
    return { ok: true, schema: 'framivra.browse/v1', path: relative, entries, truncated, escaped, limit: BROWSE_ENTRY_LIMIT }
  }
  /** Read-only diagnostic of the SAVED trusted Host configuration. The request
   *  body must be empty: no executable, PATH or any other value is ever
   *  accepted from the browser. Both probes are static capabilities commands
   *  (they never open a real timeline or project, zero writes); the outcome is
   *  only reported after re-checking that the configuration snapshot still
   *  holds, so a stale result can never be presented as valid. No grant, no
   *  Session, no settings mutation. */
  async function diagnostics(request) {
    const hold = lease(request)
    try {
      let text
      try { text = await boundedText(request, FRAME_REQUEST_LIMIT, hold.signal) }
      catch (error) { if (error.message?.startsWith('BODY_LIMIT:')) fail('REQUEST_TOO_LARGE', error.message, 413); throw error }
      hold.signal.throwIfAborted() // An aborted body read must surface as CANCELLED, never as a JSON parse error.
      const data = text.trim() === '' ? {} : parseJSON(text)
      if (!data || typeof data !== 'object' || Array.isArray(data) || Object.keys(data).length) {
        fail('BAD_REQUEST', 'The check reads only the saved Host configuration; no request fields are accepted')
      }
      const config = snapshotConfig()
      const executable = await reader.executable(hold.signal, config)
      const executableTarget = await localTarget(executable, undefined, hold.signal)
      if ((await ctx.fs.stat(executableTarget, hold.signal))?.type !== 'file') {
        fail('EXECUTABLE_NOT_REGULAR', '已解析的 vtx 不是常规文件；请在插件配置中改为受信目录里的常规可执行文件', 422)
      }
      // Public SessionStore surface is get()/list() only — values() is not a
      // public API and must not be relied on.
      if (typeof ctx.sessions.list !== 'function') fail('SESSION_STORE_UNAVAILABLE', 'Host SessionStore cannot enumerate workspaces; the executable trust check is unavailable', 503)
      const liveSessions = ctx.sessions.list()
      for (const session of liveSessions) {
        let cwd
        try { cwd = ctx.workingDirectory.get(session) } catch { continue }
        if (typeof cwd !== 'string' || !cwd) continue
        try {
          const root = await localTarget(cwd, undefined, hold.signal)
          if (ctx.fs.contains(root, executableTarget)) fail('UNTRUSTED_EXECUTABLE', 'Do not execute vtx from a session workspace', 403)
        } catch (error) {
          // Only the trust verdict aborts the check; per-session resolution
          // problems in unrelated sessions must not mask the diagnostic.
          if (error instanceof PreviewError && error.code === 'UNTRUSTED_EXECUTABLE') throw error
        }
      }
      // Static probes: `capabilities` never opens the timeline or project.
      const placeholder = 'framivra-diagnostics-no-timeline.json'
      const capabilities = previewCapabilities(await reader.read(executable, placeholder, 'capabilities', undefined, hold.signal, config, 'diagnostics'))
      const storeCapabilities = await probeStore(executable, placeholder, hold.signal, config, 'diagnostics')
      hold.signal.throwIfAborted()
      if (snapshotConfig().fingerprint !== config.fingerprint) fail('CONFIG_CHANGED', 'Host plugin configuration changed during the check; run the check again', 409)
      return json({ ok: true, schema: 'framivra.diagnostics/v1',
        config: { vtxExecutable: config.vtxExecutable, trustedPath: config.trustedPath },
        executable: { path: ctx.fs.processPath(executableTarget), regularFile: true },
        capabilities, storeCapabilities })
    } finally { hold.release() }
  }
  async function control(request, note = {}, owner = true) {
    const hold = lease(request)
    try {
      let raw
      try { raw = await boundedText(request, CONTROL_REQUEST_LIMIT, hold.signal) }
      catch (error) { if (error.message?.startsWith('BODY_LIMIT:')) fail('REQUEST_TOO_LARGE', error.message, 413); throw error }
      hold.signal.throwIfAborted() // An aborted body read must surface as CANCELLED, never as a JSON parse error.
      const data = parseJSON(raw)
      if (!data || typeof data !== 'object' || Array.isArray(data)) fail('BAD_REQUEST', 'Expected a JSON object')
      if (typeof data.op === 'string') note.op = data.op
      if (data.op === 'cancel-program-authorization') {
        // A cancelled first POST may have minted a grant whose answer was lost.
        // Seal its exact attempt before acknowledging; do not revoke a newer run.
        if (!owner || !uuidShape(data.authorizationId) || Object.keys(data).some(key => !['op', 'sessionId', 'grant', 'ownerId', 'authorizationId'].includes(key))) {
          fail('BAD_PROGRAM_RUN', 'Use the exact original owner and authorization attempt', 403)
        }
        const open = grants.get(data.grant) ?? [...frameOwners].find(item => item.id === data.grant)
        const retired = retiredWindowReceipts.get(data.grant)
        if (open ? !open.windowOwnerId || open.windowOwnerId !== data.ownerId || open.session.id !== data.sessionId :
            !retired?.programOwnerId || retired.programOwnerId !== data.ownerId || retired.sessionId !== data.sessionId) {
          fail('BAD_PROGRAM_RUN', 'Use the exact original owner and authorization attempt', 403)
        }
        if (open) {
          const attempts = open.programAttempts ??= new Map()
          let attempt = attempts.get(data.authorizationId)
          if (!attempt) {
            if (attempts.size >= PREVIEW_NONCE_LIMIT) fail('PROGRAM_RUN_LIMIT', 'No more authorization attempts on this open grant', 429)
            attempts.set(data.authorizationId, attempt = {})
          }
          attempt.cancelled = true
          const lease = automatic.leases.get(attempt.grantId), operation = open.windowOperation
          if (lease) {
            lease.revoked = true
            if (operation?.lease === lease) { open.frame.abort(); await reapWindow(open, operation) }
          }
        }
        return json({ ok: true, revoked: true, authorizationId: data.authorizationId })
      }
      if (data.op === 'revoke-preview-session') {
        if (!owner) fail('BAD_SESSION_GRANT', 'Owner session required', 403)
        const lease = automatic.original(data, true)
        lease.revoked = true
        const operation = lease.open?.windowOperation
        if (operation?.lease === lease) { lease.open.frame.abort(); await reapWindow(lease.open, operation) }
        return json({ ok: true, revoked: true, grantId: lease.info.id })
      }
      const session = sessionFor(data.sessionId)
      if (data.op === 'hello') {
        // Grant-free protocol/build handshake. A NEWER client probes this first
        // so an OLD running host (no hello → BAD_GRANT at the grant gate) can be
        // reported as "host too old, refresh/restart" instead of a misleading
        // authorization failure. Dispatch order elsewhere is unchanged: unknown
        // ops without a grant still fail BAD_GRANT exactly as before.
        return json({ ok: true, schema: 'framivra.handshake/v1', host: hostIdentity() })
      }
      if (data.op === 'open') {
        // The grant id is minted BEFORE any probe so every open child is owned
        // by this id from birth; teardown (close/cancel) reaps by the same id.
        const grantId = randomUUID()
        try {
        const relative = relativeTimeline(data.timeline)
        const cwd = ctx.workingDirectory.get(session) // Never accept cwd/root from the browser.
        const root = await localTarget(cwd, undefined, hold.signal)
        if ((await ctx.fs.stat(root, hold.signal))?.type !== 'directory') fail('BAD_WORKSPACE', 'Session workspace is unavailable')
        const { target: timeline } = await contained(relative, cwd, root, 'file', hold.signal)
        const projectPath = dirname(ctx.fs.processPath(timeline))
        const project = await localTarget(projectPath, undefined, hold.signal)
        const config = snapshotConfig()
        const executable = await reader.executable(hold.signal, config)
        const executableTarget = await localTarget(executable, undefined, hold.signal)
        if (ctx.fs.contains(root, executableTarget)) fail('UNTRUSTED_EXECUTABLE', 'Do not execute vtx from the project workspace', 403)
        const capabilities = previewCapabilities(await reader.read(executable, ctx.fs.processPath(timeline), 'capabilities', undefined, hold.signal, config, grantId))
        const snapshot = await reader.read(executable, ctx.fs.processPath(timeline), 'inspect', undefined, hold.signal, config, grantId)
        const media = new Map()
        for (const path of MEDIA) {
          const item = await contained(path, projectPath, project, 'file', hold.signal, true)
          if (item) media.set(randomUUID(), { path, ...item })
        }
        const windowExecutable = owner ? await probeWindow(config, root, project, timeline, executableTarget, hold.signal, grantId, hold.deadlineAt) : undefined
        if (windowExecutable) capabilities.capabilities.preview_window = windowExecutable.capability
        // Resolving the executable version awaits BEFORE the final configuration
        // check; from that check to the mint there is no await, so a grant is
        // never minted already-stale.
        const executableVersion = (await ctx.fs.stat(executableTarget, hold.signal))?.version
        hold.signal.throwIfAborted()
        if (ctx.workingDirectory.get(session) !== cwd || ctx.sessions.get(data.sessionId) !== session) fail('PATH_CHANGED', 'Session workspace changed; reopen preview', 409)
        // The minted grant binds the SAME configuration snapshot the resolved
        // executable came from; a config change during the opening reads never
        // stamps a new fingerprint onto an old executable.
        if (snapshotConfig().fingerprint !== config.fingerprint) fail('CONFIG_CHANGED', 'Host plugin configuration changed while opening; retry', 409)
        expire()
        if (grants.size >= 32) fail('GRANT_LIMIT', 'Close an existing preview before opening another', 429)
        for (const previous of grants.values()) if (previous.session === session && previous.timeline.targetKey === timeline.targetKey) { previous.storagePlan = undefined; previous.storageEpoch = (previous.storageEpoch ?? 0) + 1 }
        const windowOwnerId = windowExecutable ? randomUUID() : undefined
        grants.set(grantId, { id: grantId, owner, windowOwnerId, session, cwd, root, relative, timeline, project, projectPath, executable, executableTarget, executableVersion, windowExecutable, capabilities, media, contentVersion: snapshot.content_version, savedProject: snapshot.document.project, expiresAt: clock() + ttlMs, configFingerprint: config.fingerprint, configSnapshot: config })
        return json({ grant: grantId, snapshot, capabilities, ...(windowOwnerId ? { previewSession: sessionQualification(windowOwnerId, windowExecutable.capability) } : {}), expires_in_ms: ttlMs, host: hostIdentity(),
          ...(!platformAllowsWrites() ? { storeCapabilities: writeUnavailable(writePlatformMessage) } : {}),
          media: [...media].map(([mediaId, item]) => ({ mediaId, label: item.path, provenance: 'unknown', warning: 'Existing output; correspondence to this timeline content_version is NOT verified' })) })
        } catch (error) {
          // A failed open must not strand probe children: one scoped reap retry
          // keeps the global semantic gate from wedging. A still-stuck child
          // stays owned by this unrevealed id until dispose.
          await reader.reap(grantId).catch(() => {})
          throw error
        }
      }
      if (data.op === 'browse') {
        const relative = relativeBrowsePath(data.path)
        return json(await browse(session, data.sessionId, relative, hold.signal))
      }
      // Cleanup-only operations keep working after a configuration change so a
      // stale grant or write permission can always be torn down explicitly.
      const cleanup = ['close', 'storage-cancel'].includes(data.op) || ['revoke-write', 'invalidate-plan', 'cancel-write'].includes(data.op)
      const grant = grantFor(data.sessionId, data.grant, cleanup)
      if (data.op === 'program-check') {
        if (!owner || Object.keys(data).some(key => !['op', 'sessionId', 'grant', 'previewGrant', 'ownerId', 'frame', 'sha256'].includes(key))) fail('BAD_PROGRAM_RUN', 'Use the original owner and exact window digest', 403)
        const lease = automatic.original(data)
        await validateProgram(lease, grant, hold.signal)
        if (!lease.run.entries.has(data.frame) || !lease.run.entries.get(data.frame) || lease.run.entries.get(data.frame) !== data.sha256) {
          fail('BAD_PROGRAM_RUN', 'The window must match an accepted receipt in this scope', 409)
        }
        return json({ ok: true, grantId: lease.info.id, frame: data.frame, sha256: data.sha256 })
      }
      if (data.op === 'authorize-preview-session') {
        const program = data.scope === 'whole-timeline'
        const scopeKeys = program ? ['frame', 'contentVersion', 'storageConsent', 'authorizationId'] : ['continuation', 'readWarmSvg', 'firstFrame', 'useProgramAudio']
        if (Object.keys(data).some(key => !['op', 'sessionId', 'grant', 'scope', 'renderConsent', 'prepareInputs', ...scopeKeys].includes(key))) fail('BAD_SESSION_GRANT', 'Unexpected automatic preview authorization field')
        if (!owner || !grant.owner) fail('BAD_SESSION_GRANT', 'Explicit owner authorization is required', 403)
        let attempt
        if (program) {
          const attempts = grant.programAttempts ??= new Map()
          if (!uuidShape(data.authorizationId) || attempts.has(data.authorizationId)) fail('BAD_PROGRAM_RUN', 'An authorization attempt is single-use', 409)
          if (attempts.size >= PREVIEW_NONCE_LIMIT) fail('PROGRAM_RUN_LIMIT', 'No more authorization attempts on this open grant', 429)
          attempts.set(data.authorizationId, attempt = {})
        }
        await validateTimeline(grant, hold.signal)
        grantFor(data.sessionId, data.grant)
        hold.signal.throwIfAborted()
        if ([...frameOwners].some(open => open.session === grant.session && open.windowOperation?.formalArchive)) {
          fail('ARCHIVE_BUSY', 'Settle the formal archive read before authorizing automatic preview', 409)
        }
        let binding
        if (program) {
          if (data.storageConsent !== 'temporary-browser-cache' || data.renderConsent !== 'cache/preview-only' || data.prepareInputs !== true ||
              !continuationCapable(grant.windowExecutable?.capability)) fail('SESSION_CONSENT', 'Whole-timeline preview requires qualified input identity and explicit temporary browser-cache consent', 403)
          const busy = () => frameOwners.size || reader.frameBusy || grant.plan || grant.running || grant.outcomeUnknown || semanticAdmission || storageOwners.size
          if (busy()) fail('PROGRAM_RUN_BUSY', 'Settle current preview, edits or storage work first', 409)
          const timelineVersion = await programTimelineVersion(grant, hold.signal)
          const path = await editViewTarget(grant, hold.signal)
          let current
          try { current = await reader.read(grant.executable, path, 'inspect', undefined, hold.signal, grant.configSnapshot, grant.id) }
          catch (error) { if (error instanceof PreviewError && error.details) delete error.details; throw error }
          const after = await programTimelineVersion(grant, hold.signal)
          if (current.document.video_tracks?.some(track => track.kind === 'gfx' && track.clips?.length)) {
            fail('PROGRAM_GFX_UNSUPPORTED', 'Whole-timeline preview only supports ordinary audio/video without graphics tracks', 422)
          }
          grantFor(data.sessionId, data.grant)
          if (busy()) fail('PROGRAM_RUN_BUSY', 'Settle current preview, edits or storage work first', 409)
          const { duration_frames: duration, fps } = current.document.project
          const [num, den = 1] = typeof fps === 'string' ? fps.split('/').map(Number) : [JSON.isRawJSON(fps) ? Number(fps.rawJSON) : fps]
          const span = Math.ceil(num / den)
          if (after !== timelineVersion || data.contentVersion !== grant.contentVersion || data.contentVersion !== current.content_version ||
              !Number.isSafeInteger(duration) || !Number.isSafeInteger(span) || span < 1 || !Number.isSafeInteger(data.frame) || data.frame < 0 || data.frame >= duration) {
            fail('STALE_WINDOW', 'Whole-timeline scope requires the current saved version and an in-range playhead', 409)
          }
          hold.signal.throwIfAborted()
          binding = { timelineVersion, duration, span }
        }
        grantFor(data.sessionId, data.grant)
        if (attempt?.cancelled) fail('PROGRAM_RUN_EXPIRED', 'Authorization attempt was cancelled', 403)
        const info = automatic.authorize(grant, data, binding)
        if (attempt) attempt.grantId = info.id
        return json({ ok: true, grant: info })
      }
      if (data.op === 'close') {
        automatic.closeOpen(grant)
        grant.closing = true // Close must never admit another window.
        const windowOperation = grant.windowOperation
        revoke(grant)
        // The window has its own exact cancel/confirmation path. Retain its
        // admission independently; closing a read grant need not wait for it.
        if (windowOperation) void reapWindow(grant, windowOperation).catch(() => {})
        await Promise.all([grant.runningDone, windowOperation ? undefined : grant.frameDone, grant.storageDone])
        // Original grant-scoped children still require proof before untracking.
        await reader.reap(grant.id)
        storageOwners.delete(grant)
        grants.delete(data.grant)
        rememberRetiredWindows(grant)
        return json({ ok: true })
      }
      if (['storage-inspect', 'storage-plan', 'storage-clean', 'storage-cancel'].includes(data.op)) return await storageControl(data, grant, hold.signal)
      if (['authorize-write', 'revoke-write', 'invalidate-plan', 'cancel-write', 'plan', 'commit', 'status', 'recover', 'adopt', 'undo', 'edit-view'].includes(data.op)) {
        if (!owner || !grant.owner) fail('WRITE_DENIED', 'Semantic operations require an owner-opened preview', 403)
        const bookkeeping = ['revoke-write', 'invalidate-plan'].includes(data.op)
        const recovery = ['status', 'recover', 'cancel-write'].includes(data.op)
        if (!bookkeeping && frameOwners.size) fail('FRAME_BUSY', 'Cancel the frame and confirm actual process reaping before semantic operations', 409)
        if (!bookkeeping && !recovery && (automatic.activeFor(grant.session, true) || automatic.activeProgramForProject(grant.project))) {
          fail('FRAME_BUSY', 'Stop whole-timeline preview before semantic operations', 409)
        }
        semanticAdmission++
        try { return await editControl(data, grant, hold.signal, cleanup) }
        finally { semanticAdmission-- }
      }
      if (!['inspect', 'query', 'reference'].includes(data.op)) fail('BAD_OPERATION', 'Unknown operation')
      if (data.op === 'reference' && data.query?.expected_content_version !== grant.contentVersion) fail('STALE_REFERENCE', 'Reference version must match this opened preview; refresh first', 409)
      if (data.op === 'inspect' && grant.editViewing) fail('WRITE_BUSY', 'Wait for the edit view before refreshing', 409)
      const readEpoch = grant.editViewEpoch ?? 0
      const recoveryObserved = nativeWindows() ? windowsRecovery.get(grant.timeline.targetKey) : grant.recoveryObserved
      // Only an inspect begun during this exact owner operation can observe a
      // version change for it. A delayed read (possibly model-only) of another
      // grant is not evidence about this worker's current saved document.
      const observedWindow = data.op === 'inspect' ? grant.windowOperation : undefined
      const timeline = await validateTimeline(grant, hold.signal)
      const result = await reader.read(grant.executable, ctx.fs.processPath(timeline), data.op, data.query, hold.signal, grant.configSnapshot, grant.id)
      grantFor(data.sessionId, data.grant)
      if (data.op === 'inspect') {
        if (grant.editViewing || (grant.editViewEpoch ?? 0) !== readEpoch) fail('STALE_READ', 'An edit started during this read; refresh explicitly', 409)
        grant.editViewNeedsRefresh = false
        if (observedWindow && grant.windowOperation === observedWindow && observedWindow.version !== result.content_version) {
          grant.frame.abort(new PreviewError('STALE_WINDOW', 'Saved timeline changed; refresh before rendering a window', 409))
        }
        if (grant.contentVersion !== result.content_version) {
          automatic.closePrograms(open => open === grant)
          grant.storagePlan = undefined; grant.storageEpoch = (grant.storageEpoch ?? 0) + 1; grant.plan = undefined; grant.planEpoch = (grant.planEpoch ?? 0) + 1
        }
        grant.contentVersion = result.content_version
        grant.savedProject = result.document.project
        if (nativeWindows()) {
          // A read already in flight at the time of status/recover is not
          // evidence of the settled bytes. Never unlock on a different revision.
          const key = grant.timeline.targetKey
          if (owner && grant.owner && recoveryObserved?.grant === grant.id && windowsRecovery.get(key) === recoveryObserved && !windowsStoreActive.has(key)) {
            if (result.content_version === recoveryObserved.revision) windowsRecovery.delete(key)
            else windowsRecovery.set(key, {})
          }
        } else if (grant.recoveryObserved) { grant.outcomeUnknown = false; grant.recoveryObserved = false }
      }
      return json(result)
    } finally { hold.release() }
  }
  /** Owner-only storage controls. No model/tool adapter calls this authority. */
  async function storageControl(data, grant, signal) {
    const allowed = ['op', 'sessionId', 'grant', ...(data.op === 'storage-clean' ? ['planToken', 'confirm'] : [])]
    if (Object.keys(data).some(key => !allowed.includes(key))) fail('BAD_STORAGE_REQUEST', 'Storage accepts only host-owned plan tokens, never paths or replacement requests')
    if (data.op === 'storage-cancel') {
      grant.storagePlan = undefined
      grant.storage?.abort()
      await grant.storageDone
      await reader.reap(grant.id)
      if (!reader.activeFor(grant.id, 'storage-read') && !reader.activeFor(grant.id, 'storage-clean')) storageOwners.delete(grant)
      return json({ ok: true, reaped: true, message: 'Cleanup may already have removed files; cancellation does not roll back or retry.' })
    }
    const capability = grant.capabilities?.capabilities?.storage
    if (capability?.schema !== 'framivra.storage/v1' || capability.request_stdin !== true || !['inspect', 'plan', 'clean'].every(op => capability.operations?.includes(op))) fail('STORAGE_UNSUPPORTED', 'Installed core lacks storage/v1; ordinary timeline reads remain available', 422)
    if (grant.storage || storageOwners.has(grant)) fail('STORAGE_BUSY', 'Wait for this project storage scan and managed cleanup', 409)
    if (semanticAdmission || frameOwners.size || reader.semanticBusy || grant.running || grant.plan || grant.outcomeUnknown || (nativeWindows() && windowsRecovery.has(grant.timeline.targetKey))) fail('WRITE_BUSY', 'Resolve local rendering, semantic writes before storage operations. External writers must be manually stopped.', 409)
    const operation = data.op.slice('storage-'.length)
    let frozen
    if (operation === 'clean') {
      if (automatic.activeProgramForProject(grant.project)) fail('PROGRAM_RUN_BUSY', 'Stop whole-timeline preview before cleaning this project', 409)
      if (data.confirm !== 'stopped-writers-and-cleanup') fail('STORAGE_CONFIRMATION', 'Independently confirm stopped external writers and cleanup', 403)
      frozen = grant.storagePlan
      if (!frozen || frozen.token !== data.planToken || clock() >= frozen.expiresAt || frozen.version !== grant.contentVersion) { grant.storagePlan = undefined; fail('STORAGE_PLAN_INVALID', 'Storage plan expired or changed; explicitly request a new plan', 409) }
      grant.storagePlan = undefined // Single use, including every validation/spawn failure.
    }
    if (operation === 'plan') grant.storagePlan = undefined
    const controller = new AbortController()
    grant.storage = controller
    storageOwners.add(grant) // Synchronous admission before any async validation.
    semanticAdmission++
    const epoch = grant.storageEpoch ?? 0
    let settled, mutating = false
    grant.storageDone = new Promise(resolve => { settled = resolve })
    const combined = AbortSignal.any([signal, controller.signal])
    const monitor = setInterval(() => {
      try {
        if (clock() >= grant.expiresAt || ctx.sessions.get(data.sessionId) !== grant.session || ctx.workingDirectory.get(grant.session) !== grant.cwd || !configCurrent(grant)) controller.abort()
      } catch { controller.abort() }
    }, 50)
    monitor.unref?.()
    const validate = async () => {
      combined.throwIfAborted()
      grantFor(data.sessionId, data.grant)
      const timeline = await validateTimeline(grant, combined)
      const project = await localTarget(grant.projectPath, undefined, combined)
      if (project.targetKey !== grant.project.targetKey || (await ctx.fs.stat(project, combined))?.type !== 'directory') fail('PATH_CHANGED', 'Project identity changed; reopen', 409)
      const executable = await localTarget(grant.executable, undefined, combined)
      const info = await ctx.fs.stat(executable, combined)
      if (executable.targetKey !== grant.executableTarget.targetKey || info?.type !== 'file' || info.version !== grant.executableVersion || ctx.fs.contains(grant.root, executable)) fail('EXECUTABLE_CHANGED', 'Installed executable identity changed; reopen', 409)
      grantFor(data.sessionId, data.grant)
      return ctx.fs.processPath(timeline)
    }
    try {
      const path = await validate()
      if (operation === 'clean') {
        const current = await reader.read(grant.executable, path, 'inspect', undefined, combined, grant.configSnapshot, grant.id)
        if (current.content_version !== frozen.version) { grant.contentVersion = current.content_version; fail('STORAGE_PLAN_INVALID', 'Saved timeline changed; refresh and request a new storage plan', 409) }
        await validate()
        if (clock() >= frozen.expiresAt || (grant.storageEpoch ?? 0) !== epoch) fail('STORAGE_PLAN_INVALID', 'Storage plan expired or preview reopened before cleanup; explicitly plan again', 409)
      }
      const request = operation === 'clean' ? { plan_id: frozen.id, content_version: frozen.version, confirm: 'stopped-writers-and-cleanup' } : undefined
      mutating = operation === 'clean'
      const result = await reader.storage(grant.executable, path, operation, request, combined, grant.configSnapshot, grant.id)
      await validate()
      combined.throwIfAborted()
      if (result.content_version !== grant.contentVersion) {
        grant.storagePlan = undefined
        if (result.ok) fail('STORAGE_PLAN_INVALID', 'Timeline changed; refresh before storage planning', 409)
      }
      if (operation === 'plan' && result.ok) {
        if ((grant.storageEpoch ?? 0) !== epoch) fail('STORAGE_PLAN_INVALID', 'Preview reopened or changed while planning; explicitly plan again', 409)
        const token = randomUUID()
        const plan = Object.freeze({ ...result.plan, entries: Object.freeze(result.plan.entries.map(entry => Object.freeze({ ...entry }))) })
        grant.storagePlan = Object.freeze({ token, id: plan.id, plan, version: result.content_version, expiresAt: clock() + 60_000 })
        return json({ ...result, planToken: token, expires_in_ms: 60_000 })
      }
      return json(result)
    } catch (error) {
      if (mutating) return json({ ok: false, schema: 'framivra.storage/v1', operation: 'clean', content_version: frozen.version, code: 'outcome_unknown', outcome_unknown: true,
        diagnostics: [{ code: error.code ?? error.name, message: 'Cleanup interrupted or result unavailable; files may have been removed. Never retry this token. Inspect and explicitly plan again after stopping external writers.' }] })
      throw error
    } finally {
      clearInterval(monitor)
      semanticAdmission--
      grant.storage = undefined
      if (!reader.activeFor(grant.id, 'storage-read') && !reader.activeFor(grant.id, 'storage-clean')) storageOwners.delete(grant)
      settled()
    }
  }
  async function probeStore(executable, timeline, signal, config, owner, beforeSpawn) {
    if (!platformAllowsWrites()) return writeUnavailable(writePlatformMessage)
    try {
      const capabilities = await reader.store(executable, timeline, 'capabilities', undefined, signal, config, owner, undefined, beforeSpawn)
      const refusal = writeCapabilityRefusal(capabilities)
      return refusal ? writeUnavailable(refusal) : capabilities
    } catch (error) {
      // Only unsupported protocol/CLI degrades to an advisory. Never hide
      // cancellation, truncation, timeout or failed cleanup behind read-only.
      if (error instanceof PreviewError && ['CLI_UNSUPPORTED', 'PROTOCOL_MISMATCH'].includes(error.code)) {
        return { ok: false, error: error.code, message: 'Installed vtx does not advertise the semantic store; reads remain usable' }
      }
      throw error
    }
  }
  async function writeCapabilities(grant, signal) {
    const path = ctx.fs.processPath(grant.timeline)
    const fence = nativeWindows() ? async signal => {
      if (await editViewTarget(grant, signal) !== path) fail('PATH_CHANGED', 'Timeline process path changed before authorization', 409)
    } : undefined
    const capabilities = await probeStore(grant.executable, path, signal, grant.configSnapshot, grant.id, fence)
    if (!capabilities.ok) fail('WRITE_UNSUPPORTED', capabilities.message, 422)
    if (fence) await fence(signal)
    return capabilities
  }
  async function editControl(data, grant, signal, cleanup = false) {
    if (data.op === 'edit-view') {
      if (Object.keys(data).some(key => !['op', 'sessionId', 'grant', 'writeGrant', 'op_id', 'base_revision', 'operations'].includes(key))) fail('BAD_INTENT', 'Only explicit edit-view request fields are accepted')
      if (!uuidShape(data.op_id)) fail('BAD_OP_ID', 'Direct UI operation ID must be an explicit UUID')
      if (!Array.isArray(data.operations) || !data.operations.length || data.operations.length > 256) fail('BAD_INTENT', 'Use 1..256 explicit semantic operations')
    }
    if (['plan', 'commit', 'adopt', 'undo', 'recover', 'edit-view'].includes(data.op)) grant.storagePlan = undefined
    if (storageOwners.size && !['revoke-write', 'invalidate-plan', 'cancel-write'].includes(data.op)) fail('STORAGE_BUSY', 'Wait for storage process cleanup before semantic operations', 409)
    const windows = nativeWindows()
    if (data.op === 'revoke-write') {
      revoke(grant) // End pending authorization synchronously as well as existing writes.
      await Promise.all([grant.runningDone, grant.frameDone, grant.storageDone]); await reader.reapIdle(grant.id)
      return json({ ok: true })
    }
    // Revoking a frozen plan is host bookkeeping, not permission to spawn or write.
    // cancel-write IS the explicit store-child cancel: it must never be gated
    // by a stuck semanticBusy (a failed cleanup), or the retry path wedges.
    if (!['invalidate-plan', 'cancel-write'].includes(data.op) && (grant.frame || (reader.semanticBusy && !grant.running))) fail('FRAME_BUSY', 'Explicitly cancel the frame and wait for process reaping before semantic operations', 409)
    if (data.op === 'authorize-write') {
      revoke(grant)
      const authorization = grant.authorization = new AbortController()
      const authorizedSignal = AbortSignal.any([signal, authorization.signal])
      try {
        await grant.runningDone
        await reader.reapIdle(grant.id)
        await validateTimeline(grant, authorizedSignal)
        const capabilities = await writeCapabilities(grant, authorizedSignal)
        await validateTimeline(grant, authorizedSignal)
        grantFor(data.sessionId, data.grant)
        authorizedSignal.throwIfAborted()
        if (grant.authorization !== authorization || grant.closing) fail('BAD_GRANT', 'Write authorization is no longer current', 403)
        grant.write = new AbortController()
        grant.writeKey = randomUUID()
        grant.writeCapabilities = capabilities
        // Windows uses the qualified one-shot stdin carrier, not an idle worker.
        const fast = !windows && editViewCapable(capabilities)
        if (fast) primeEditView(grant)
        return json({ ok: true, writeGrant: grant.writeKey, capabilities, ...(fast ? { editView: true } : {}) })
      } finally { if (grant.authorization === authorization) grant.authorization = undefined }
    }
    await validateTimeline(grant, signal, cleanup)
    grantFor(data.sessionId, data.grant, cleanup)
    if (!grant.write || grant.write.signal.aborted || data.writeGrant !== grant.writeKey) fail('WRITE_DENIED', 'Read access is not write permission; explicitly authorize this Session/project first', 403)
    if (data.op === 'cancel-write') {
      grant.running?.abort()
      await grant.runningDone
      // Scoped to THIS grant: other grants'/sessions' in-flight children are
      // untouched. A failed cleanup stays owned and a retry re-attempts it.
      await reader.reap(grant.id)
      return json({ ok: true, message: 'Process settled and reaped; original operation may have committed. Check status/recover.' })
    }
    if (data.op === 'invalidate-plan') { grant.plan = undefined; grant.planEpoch = (grant.planEpoch ?? 0) + 1; return json({ ok: true }) }
    if (grant.running) fail('WRITE_BUSY', 'Wait for managed process cleanup before another command', 409)
    const targetKey = grant.timeline.targetKey
    if ((windows ? windowsRecovery.has(targetKey) : grant.outcomeUnknown) && !['status', 'recover'].includes(data.op)) fail('OUTCOME_UNKNOWN', 'Use explicit core status/recover and refresh before any new plan or transaction', 409)
    if (grant.editViewNeedsRefresh && ['edit-view', 'plan', 'commit', 'adopt', 'undo'].includes(data.op)) fail('STALE_BASE', 'Explicitly refresh after an applied edit whose view was unavailable', 409)
    if (data.op === 'edit-view' && (windows || !editViewCapable(grant.writeCapabilities))) fail('EDIT_VIEW_UNSUPPORTED', 'Edit-view is not qualified for this write authorization', 422)
    if (windows && windowsStoreActive.has(targetKey)) fail('WRITE_BUSY', 'Wait for the store operation on this timeline to settle', 409)
    const controller = new AbortController()
    if (windows) windowsStoreActive.add(targetKey)
    grant.running = controller
    if (data.op === 'edit-view') { grant.editViewing = true; grant.editViewEpoch = (grant.editViewEpoch ?? 0) + 1 }
    let settled
    grant.runningDone = new Promise(resolve => { settled = resolve })
    const combined = AbortSignal.any([signal, grant.write.signal, controller.signal])
    // Observe changes during a child too; get() is the public Session/cwd contract.
    const monitor = setInterval(() => {
      // A throwing provider inside a timer would kill the whole Host process;
      // treat any probe failure as "world unverifiable" and abort fail-closed.
      try {
        if (((windows || data.op === 'edit-view') && clock() >= grant.expiresAt) || ctx.sessions.get(data.sessionId) !== grant.session || ctx.workingDirectory.get(grant.session) !== grant.cwd || !configCurrent(grant)) controller.abort()
      } catch { controller.abort() }
    }, 50)
    monitor.unref?.()
    const epoch = grant.planEpoch ?? 0
    let mutating = false, dispatchAttempted = false, request, appliedReceipt, plannedDigest
    const editViewFence = () => editViewTarget(grant, combined)
    function lockForRecovery() {
      if (windows) {
        windowsRecovery.set(targetKey, {})
        for (const open of grants.values()) if (open.timeline.targetKey === targetKey) {
          open.plan = undefined
          open.planEpoch = (open.planEpoch ?? 0) + 1
          open.storagePlan = undefined
          open.storageEpoch = (open.storageEpoch ?? 0) + 1
        }
      } else {
        grant.outcomeUnknown = true
        grant.recoveryObserved = undefined
        grant.plan = undefined
      }
    }
    try {
      const path = windows || data.op === 'edit-view' ? await editViewFence() : ctx.fs.processPath(await validateTimeline(grant, combined))
      const runStore = async (operation, request) => {
        const fence = async signal => {
          if (await editViewTarget(grant, signal) !== path) fail('PATH_CHANGED', 'Timeline process path changed before dispatch', 409)
        }
        const result = await reader.store(grant.executable, path, operation, request, combined, grant.configSnapshot, grant.id, undefined, windows ? async signal => {
          await fence(signal)
          return () => {
            grantFor(data.sessionId, data.grant)
            if (grant.closing || grant.running !== controller) fail('BAD_GRANT', 'Write is no longer current', 403)
            if (data.op === 'commit' && ((grant.planEpoch ?? 0) !== epoch || request.base_revision !== grant.contentVersion)) fail('PLAN_INVALID', 'Plan was invalidated before dispatch', 409)
            dispatchAttempted = true
          }
        } : undefined)
        if (windows) {
          await fence(combined)
          const unsettled = result.code === 'recovery_required' || result.code === 'outcome_unknown' || result.outcome_unknown === true
          if (unsettled) lockForRecovery()
          const expected = operation === 'commit' ? request.operation : operation
          if (result.ok && (unsettled || result.operation !== expected || (request && (
            result.op_id !== request.op_id || result.base_revision !== request.base_revision || result.replayed !== false ||
            !revisionShape(result.request_digest) || (plannedDigest && result.request_digest !== plannedDigest) ||
            (operation === 'commit' && (!revisionShape(result.revision) || result.current_revision !== result.revision)))))) {
            fail('PROTOCOL_MISMATCH', 'Exact Windows store operation receipt required', 502)
          }
          if (!result.ok) {
            if (result.operation !== expected || typeof result.code !== 'string' || !result.code.trim() ||
                (request && result.op_id != null && result.op_id !== request.op_id)) fail('PROTOCOL_MISMATCH', 'Exact Windows store failure receipt required', 502)
            // Native publication writes an intent before staging. Some errors
            // before replacement can therefore still leave pending history.
            if (operation === 'commit' && (result.code === 'input.error' || result.code.startsWith('path.'))) {
              fail(result.code, result.message ?? 'Windows commit left history unverified', 502)
            }
          }
        }
        return result
      }
      if (data.op === 'status' || data.op === 'recover') {
        // Recovery must remain available between preview windows. Retire any
        // captured media authority before a store command can change it.
        automatic.closePrograms(open => open.session === grant.session || open.project.targetKey === grant.project.targetKey)
        mutating = true // Even status may initialize lock/manifest; write grant required.
        if (windows && windowsRecovery.has(targetKey)) windowsRecovery.set(targetKey, {})
        const result = await runStore(data.op)
        grant.plan = undefined
        // A successful status can still report unresolved intents. Only an explicit
        // settled core result plus a later authoritative read may release unknown state.
        if (windows) {
          if (result.ok && (result.operation !== data.op || typeof result.pending !== 'boolean' || !revisionShape(result.current_revision))) fail('PROTOCOL_MISMATCH', 'Exact Windows recovery status and revision required', 502)
          if (result.pending === true) lockForRecovery()
          if (windowsRecovery.has(targetKey) && result.ok && result.pending === false) windowsRecovery.set(targetKey, { grant: grant.id, revision: result.current_revision })
        } else grant.recoveryObserved = result.ok === true && result.pending === false
        return json(result)
      }
      if (data.op === 'commit') {
        const plan = grant.plan
        if (!plan || plan.token !== data.planToken) fail('PLAN_INVALID', 'Plan expired or changed; request a new plan', 409)
        request = plan.request
        plannedDigest = plan.requestDigest
        // No body replacement, fresh ID, auto-rebase or automatically repeated commit.
        grant.plan = undefined
        mutating = true
        const result = await runStore('commit', request)
        return json({ ...result, submitted_request: request })
      }
      if (data.base_revision !== grant.contentVersion) fail('STALE_BASE', 'Refresh the authoritative timeline before planning', 409)
      if (data.op === 'edit-view') {
        request = { operation: 'edit', op_id: data.op_id, base_revision: data.base_revision, operations: data.operations }
        grant.plan = undefined
        mutating = true
        const result = await reader.store(grant.executable, path, 'edit-view', request, combined, grant.configSnapshot, grant.id,
          grant.writeCapabilities.edit_view.preload_stdin === true ? grant.write : undefined, async () => {
            if (await editViewFence() !== path) fail('PATH_CHANGED', 'Timeline process path changed before dispatch', 409)
          })
        if (result.outcome_unknown === true || result.code === 'outcome_unknown') {
          grant.outcomeUnknown = true
          return json({ ...result, op_id: request.op_id })
        }
        if (!result.ok) {
          if (typeof result.code !== 'string' || !result.code.trim() || (result.op_id !== undefined && result.op_id !== request.op_id)) fail('PROTOCOL_MISMATCH', 'Bounded edit failure requires a code and matching operation identity when present', 502)
          return json(result)
        }
        if (appliedEditReceipt(result, request)) appliedReceipt = result
        validateEditView(result, request)
        await editViewFence()
        grantFor(data.sessionId, data.grant)
        combined.throwIfAborted()
        grant.contentVersion = result.revision
        grant.savedProject = result.view.snapshot.document.project
        grant.storageEpoch = (grant.storageEpoch ?? 0) + 1
        grant.planEpoch = (grant.planEpoch ?? 0) + 1
        primeEditView(grant)
        return json(result)
      }
      if (data.op === 'plan') {
        if (!Array.isArray(data.operations) || !data.operations.length || data.operations.length > 256) fail('BAD_INTENT', 'Use 1..256 explicit semantic operations')
        request = { operation: 'edit', op_id: randomUUID(), base_revision: data.base_revision, operations: data.operations }
      } else if (data.op === 'adopt') {
        if (data.op_id !== undefined && !/^[a-f0-9-]{36}$/.test(data.op_id)) fail('BAD_OP_ID', 'Direct UI operation ID must be an explicit UUID')
        if (data.confirmation !== 'stable-ids-and-history') fail('ADOPT_CONFIRMATION', 'Explicitly acknowledge stable IDs and persistent history')
        request = { operation: 'adopt', op_id: data.op_id ?? randomUUID(), base_revision: data.base_revision }
      } else if (data.op === 'undo') {
        if (data.op_id !== undefined && !/^[a-f0-9-]{36}$/.test(data.op_id)) fail('BAD_OP_ID', 'Direct UI operation ID must be an explicit UUID')
        if (typeof data.head !== 'string') fail('BAD_HEAD', 'Explicit current core head required')
        request = { operation: 'undo', op_id: data.op_id ?? randomUUID(), base_revision: data.base_revision, head: data.head }
      }
      // adopt/undo have no core semantic plan. They are explicit direct transactions.
      if (data.op !== 'plan') {
        mutating = true
        grant.plan = undefined
        return json({ ...await runStore('commit', request), submitted_request: request })
      }
      grant.plan = undefined
      const result = await runStore('plan', request)
      grantFor(data.sessionId, data.grant)
      await validateTimeline(grant, combined)
      if (!result.ok) return json({ ...result, submitted_request: request })
      combined.throwIfAborted()
      if ((grant.planEpoch ?? 0) !== epoch) fail('PLAN_INVALID', 'Selection or intent changed while planning', 409)
      const token = randomUUID()
      grant.plan = { token, request, ...(windows && { requestDigest: result.request_digest }) }
      return json({ ...result, planToken: token, submitted_request: request })
    } catch (error) {
      if (appliedReceipt) {
        grant.editViewNeedsRefresh = true
        const { view: _view, ...receipt } = appliedReceipt
        return json({ ...receipt, ok: false, applied: true, code: 'applied_view_unavailable',
          ...(['BAD_GRANT', 'CONFIG_CHANGED', 'PATH_CHANGED', 'EXECUTABLE_CHANGED'].includes(error.code) ? { terminal_code: error.code } : {}),
          message: 'The edit was applied, but its view could not be published. Do not retry; refresh explicitly or reopen.',
          diagnostics: { error: error.code ?? error.name, message: error.message } })
      }
      if (mutating && (!windows || dispatchAttempted)) { lockForRecovery(); return json({ ok: false, schema: 'framivra.timeline.store/v1', code: 'outcome_unknown', op_id: request?.op_id ?? null,
        ...(data.op === 'edit-view' ? { outcome_unknown: true } : { submitted_request: request }), message: 'Command interrupted or result unavailable. It may have committed; use explicit core status/recover. Never generate a replacement operation to retry.',
        diagnostics: { error: error.code ?? error.name, message: error.message } }) }
      throw error
    } finally {
      clearInterval(monitor)
      if (windows) windowsStoreActive.delete(targetKey)
      grant.running = undefined
      if (data.op === 'edit-view') grant.editViewing = false
      settled()
    }
  }
  function clearWindowFirstFrame(operation) {
    const provisional = operation?.provisional
    if (provisional) { provisional.closed = true; provisional.image = undefined }
  }

  async function pollFirstFrame(grant, sessionLease, data, signal) {
    if (sessionLease.open !== grant || sessionLease.info.firstFrame !== true || !automatic.active(sessionLease) ||
        grant.closing || grant.windowExecutable?.capability.first_frame?.version !== 1 || data.project !== grant.relative) {
      fail('FIRST_FRAME_DENIED', 'Use the live owner grant and project that authorized the provisional visual', 403)
    }
    if (!Number.isSafeInteger(data.end) || data.end <= data.frame) fail('BAD_WINDOW', 'First-frame polling requires the exact exclusive window end')
    const pending = () => new Response(null, { status: 204, headers: { 'Cache-Control': 'no-store' } })
    const operation = grant.windowOperation
    const currentImage = () => !grant.closing && automatic.active(sessionLease) && grant.windowOperation === operation &&
      operation?.lease === sessionLease && operation.id === data.operationId && operation.version === data.contentVersion &&
      operation.start === data.frame && !operation.signal.aborted && grant.contentVersion === operation.version &&
      operation.provisional?.image?.end === data.end ? operation.provisional.image : undefined
    if (!currentImage()) return pending()
    const checkSignal = AbortSignal.any([signal, operation.signal])
    try {
      const project = await localTarget(grant.projectPath, undefined, checkSignal)
      if (project.targetKey !== grant.project.targetKey) fail('PATH_CHANGED', 'Project identity changed; reopen preview', 409)
      const binding = grant.windowExecutable
      sameWindowExecutable(binding, await windowExecutableIdentity(binding.executable, grant.root, project, grant.executableTarget, checkSignal))
      const timeline = await validateTimeline(grant, checkSignal)
      const info = await ctx.fs.stat(timeline, checkSignal)
      if (info?.type !== 'file' || info.version !== operation.provisional.timelineVersion) fail('STALE_WINDOW', 'Saved timeline changed; refresh before rendering a window', 409)
      grantFor(data.sessionId, data.grant)
    } catch (error) {
      signal.throwIfAborted()
      if (operation.signal.aborted || grant.windowOperation !== operation) return pending()
      clearWindowFirstFrame(operation)
      // A transient poll I/O failure cannot decide the original render's
      // outcome. Only a positively observed binding mismatch cancels it.
      if (error instanceof PreviewError && ['PATH_CHANGED', 'PATH_ESCAPE', 'EXECUTABLE_CHANGED', 'CONFIG_CHANGED', 'STALE_WINDOW'].includes(error.code)) {
        grant.frame.abort(error)
        throw error
      }
      fail('FIRST_FRAME_UNVERIFIED', 'Could not verify the active provisional visual', 503)
    }
    signal.throwIfAborted()
    // A poll can race W, abort, revoke, or resize/reopen through the await above.
    const image = currentImage()
    if (!image) return pending()
    return new Response(image.bytes, { headers: {
      'Content-Type': 'image/png', 'Content-Length': String(image.bytes.length), 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
      'X-Framivra-Version': operation.version, 'X-Framivra-Start': String(image.start), 'X-Framivra-End': String(image.end), 'X-Framivra-SHA256': image.sha256,
    } })
  }

  async function frame(request, note) { return renderPreview(request, note, false) }
  async function window(request, note) { return renderPreview(request, note, true) }
  async function renderPreview(request, note, isWindow) {
    const kind = isWindow ? 'Window' : 'Frame'
    const errorPrefix = kind.toUpperCase()
    if (request.method !== 'POST') fail('METHOD_NOT_ALLOWED', `${kind} is POST-only`, 405)
    const hold = lease(request)
    let grant, controller, settled, monitor, expiry, queued, windowOperation, clearFirstFrame
    try {
      let text
      try { text = await boundedText(request, FRAME_REQUEST_LIMIT, hold.signal) }
      catch (error) { if (error.message?.startsWith('BODY_LIMIT:')) fail('REQUEST_TOO_LARGE', error.message, 413); throw error }
      if (isWindow) hold.signal.throwIfAborted()
      let data
      try { data = parseJSON(text) }
      catch (error) { if (isWindow && error instanceof SyntaxError) fail('BAD_WINDOW', 'Expected complete window request JSON'); throw error }
      const continuation = isWindow && data?.op === 'session-continue'
      const programRender = isWindow && data?.op === 'program-render'
      const sessionRender = isWindow && data?.op === 'session-render'
      const firstFramePoll = isWindow && data?.op === 'session-first-frame'
      const formalArchive = isWindow && ['formal-archive-read', 'formal-archive-cancel'].includes(data?.op)
      const isAuto = programRender || continuation || sessionRender || firstFramePoll || (isWindow && data?.op === 'session-cancel')
      const allowed = formalArchive ? ['op', 'sessionId', 'grant', 'ownerId', 'operationId', ...(data.op === 'formal-archive-read' ? ['frame', 'contentVersion'] : [])]
        : isAuto ? ['op', 'sessionId', 'grant', 'previewGrant', 'ownerId', 'operationId',
        ...(data.op !== 'session-cancel' ? ['frame', 'contentVersion'] : []), ...(continuation ? ['inputEpoch', 'previousOperationId'] : []),
        ...(sessionRender ? ['firstFrame'] : []), ...(sessionRender || continuation ? ['useProgramAudio'] : []), ...(firstFramePoll ? ['project', 'end'] : [])]
        : ['op', 'sessionId', 'grant', 'operationId', ...(isWindow && data?.op === 'cancel' ? [] : ['frame', 'contentVersion', 'renderConsent', ...(isWindow ? ['prepareInputs'] : ['prepareGfx', 'htmlTrust'])])]
      if (!data || typeof data !== 'object' || Array.isArray(data) || Object.keys(data).some(key => !allowed.includes(key))) fail(`BAD_${errorPrefix}`, `Only the bounded ${kind.toLowerCase()} operation fields are accepted`)
      if (typeof data.op === 'string') note.op = data.op
      const validNonce = typeof data.operationId === 'string' && (formalArchive
        ? /^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/.test(data.operationId)
        : /^[a-f0-9-]{36}$/.test(data.operationId))
      const sessionLease = isAuto ? automatic.original(data, data.op === 'session-cancel') : undefined
      if (sessionLease && !!sessionLease.run !== programRender && data.op !== 'session-cancel') fail('BAD_PROGRAM_RUN', 'The original preview scope does not authorize this operation', 403)
      if (isAuto && data.op === 'session-cancel') {
        if (sessionLease.run) fail('BAD_PROGRAM_RUN', 'Stop the whole-timeline scope with its exact revocation receipt', 403)
        if (!validNonce) fail('BAD_WINDOW', 'Cancel requires the original window operationId')
        automatic.seal(sessionLease, data.operationId)
        const op = sessionLease.open?.windowOperation
        if (op?.lease === sessionLease && op.id === data.operationId) {
          sessionLease.open.frame.abort(); await reapWindow(sessionLease.open, op)
        }
        hold.signal.throwIfAborted()
        if (sessionLease.last?.operationId === data.operationId) sessionLease.last.reaped = true
        return json({ ok: true, reaped: true, operationId: data.operationId, ownerId: sessionLease.info.ownerId,
          openGrant: sessionLease.info.openGrant, grantId: sessionLease.info.id })
      }
      if (isWindow && (data.op === 'cancel' || data.op === 'formal-archive-cancel')) {
        if (!validNonce) fail('BAD_WINDOW', 'Cancel requires the exact window operationId')
        return await cancelWindow(data, hold.signal, formalArchive)
      }
      grant = grantFor(data.sessionId, data.grant, data.op === 'cancel')
      if (isWindow && !grant.owner) fail('BAD_GRANT', 'An owner-opened preview grant is required for a window', 403)
      if (data.op === 'cancel') {
        if (grant.windowOperation) fail('FRAME_BUSY', 'Use the window cancel route and its exact operationId', 409)
        grant.frame?.abort()
        await grant.frameDone
        // Legacy cancel remains scoped to THIS grant (not a window token).
        await reader.reap(grant.id)
        if (!grant.windowOperation) { grant.frame = undefined; frameOwners.delete(grant) }
        return json({ ok: true, reaped: true })
      }
      if (formalArchive) {
        if (!validNonce || !archiveOwner(grant, data.ownerId)) fail('ARCHIVE_DENIED', 'This read requires a fresh click and the qualified owner open grant', 403)
        if (automatic.activeFor(grant.session)) fail('SESSION_BUSY', 'Revoke automatic preview before reading an approved archive', 409)
      } else if (!validNonce || (!isAuto && (data.op !== 'render' || data.renderConsent !== 'cache/preview-only'))) {
        fail('RENDER_DENIED', 'A fresh explicit preview-cache write consent is required', 403)
      }
      if (!programRender && automatic.activeFor(grant.session, true)) fail('PROGRAM_RUN_BUSY', 'Stop whole-timeline preview before dispatching a separate preview worker', 409)
      if (!Number.isSafeInteger(data.frame) || data.frame < 0 || data.contentVersion !== grant.contentVersion || !/^sha256:[a-f0-9]{64}$/.test(data.contentVersion)) fail(`STALE_${errorPrefix}`, 'Use an exact saved version and pinned integer frame', 409)
      if (firstFramePoll) return await pollFirstFrame(grant, sessionLease, data, hold.signal)
      const firstFrame = Object.hasOwn(data, 'firstFrame')
      const useProgramAudio = (sessionRender || continuation) && data.useProgramAudio === true
      if ((sessionRender || continuation) && (data.useProgramAudio !== sessionLease.info.useProgramAudio ||
          (useProgramAudio && (sessionLease.open !== grant || firstFrame || sessionLease.info.firstFrame ||
            grant.windowExecutable?.capability.program_audio !== true ||
            (sessionLease.info.continuation && !programAudioContinuationCapable(grant.windowExecutable.capability)))))) {
        fail('PROGRAM_AUDIO_DENIED', 'Program audio must exactly match the original fixed-wheel session grant', 403)
      }
      if (firstFrame && (data.firstFrame !== true || sessionLease.open !== grant || !automatic.active(sessionLease) ||
          sessionLease.info.firstFrame !== true || sessionLease.info.continuation || sessionLease.info.readWarmSvg ||
          grant.windowExecutable?.capability.first_frame?.version !== 1)) {
        fail('FIRST_FRAME_DENIED', 'This original render lacks qualified owner consent for a provisional visual', 403)
      }
      let options
      if (isWindow) {
        if (!formalArchive && !isAuto && data.prepareInputs !== true) fail('PREPARE_INPUTS_DENIED', 'Explicit prepareInputs:true consent is required; no warm-cache fallback', 403)
        if (!grant.windowExecutable) fail('WINDOW_UNSUPPORTED', 'This Host has no qualified independent fixed-wheel window launcher for this preview', 422)
        if (grant.closing) fail('BAD_GRANT', 'Preview is closing; open a new grant', 403)
        if (storageOwners.size) fail('STORAGE_BUSY', 'Wait for storage process cleanup before previewing a window', 409)
      } else {
        options = Object.hasOwn(data, 'prepareGfx') || Object.hasOwn(data, 'htmlTrust') ? Object.fromEntries(['prepareGfx', 'htmlTrust'].filter(key => Object.hasOwn(data, key)).map(key => [key, data[key]])) : undefined
        prepareGfxArgs(options) // Validate exact types/combinations before admission or spawning.
        if (!platformAllowsFrame()) fail('FRAME_UNSUPPORTED', framePlatformMessage, 422)
        if (!inlineCapable(grant.capabilities)) fail('FRAME_UNSUPPORTED', 'Installed core must actually advertise --include-png; no path fallback', 422)
        if (options?.prepareGfx === true && !prepareGfxCapable(grant.capabilities)) fail('FRAME_UNSUPPORTED', 'Installed core must advertise current-composite preview-only graphics preparation', 422)
      }
      if (semanticAdmission || grant.running || grant.outcomeUnknown || grant.plan) fail('WRITE_BUSY', `Resolve the active/unknown write or pending plan and refresh before a ${kind.toLowerCase()}`, 409)
      if (formalArchive ? (frameQueue.length || frameOwners.size || reader.frameBusy) :
          [...frameOwners].some(open => open.windowOperation?.formalArchive)) {
        fail('ARCHIVE_BUSY', 'An approved archive read does not queue or overlap other preview operations', 409)
      }
      if (grant.frame || (!frameQueue.length && (frameOwners.size || reader.frameBusy))) fail(`${errorPrefix}_BUSY`, 'Previous process ownership has not been reaped; cancel it explicitly', 409)
      if (frameQueue.length >= 5) fail(`${errorPrefix}_QUEUE_FULL`, 'At most four explicit preview clicks may wait; no automatic retry', 429)
      if (programRender) {
        automatic.reserveProgram(sessionLease, grant, data.frame)
      } else if (isAuto) {
        if (continuation) {
          if (!sessionLease.info.continuation) fail('CONTINUATION_DENIED', 'Continuation was not authorized', 403)
          const last = sessionLease.last
          if (!last?.reaped || last.operationId !== data.previousOperationId || last.version !== data.contentVersion ||
              last.end !== data.frame || last.inputEpoch !== data.inputEpoch || last.programAudio !== sessionLease.info.useProgramAudio) {
            fail('STALE_PREDECESSOR', 'No confirmed adjoining window for this intent', 409)
          }
        }
        automatic.reserve(sessionLease, grant, data.operationId)
        sessionLease.last = undefined // One successor only; a new foreground intent also retires this predecessor.
      } else {
        const nonces = formalArchive ? (grant.archiveNonces ??= new Set()) : isWindow ? (grant.windowNonces ??= new Set()) : (grant.frameNonces ??= new Set())
        if (nonces.has(data.operationId)) fail(formalArchive ? 'ARCHIVE_REPLAY' : `${errorPrefix}_REPLAY`,
          formalArchive ? 'An archive read click is single-use; no automatic retry' : 'A render click is single-use; no automatic retry', 409)
        if (nonces.size >= PREVIEW_NONCE_LIMIT) fail(formalArchive ? 'ARCHIVE_LIMIT' : `${errorPrefix}_LIMIT`, 'Reopen this preview before more captures', 429)
        nonces.add(data.operationId)
      }
      if (!formalArchive) grant.storagePlan = undefined
      controller = new AbortController()
      grant.frame = controller // Synchronous admission: held until actual done + waitForExit.
      frameOwners.add(grant)
      grant.frameDone = new Promise(resolve => { settled = resolve })
      const signal = AbortSignal.any([hold.signal, controller.signal])
      if (isWindow) windowOperation = grant.windowOperation = { id: data.operationId, owner: Symbol('window'), lease: sessionLease, formalArchive,
        version: data.contentVersion, start: data.frame, signal, done: grant.frameDone,
        ...(firstFrame && { provisional: { project: grant.savedProject } }) }
      if (firstFrame) {
        clearFirstFrame = () => clearWindowFirstFrame(windowOperation)
        signal.addEventListener('abort', clearFirstFrame, { once: true })
      }
      let start
      const turn = new Promise(resolve => { start = resolve })
      queued = { start, grant }
      frameQueue.push(queued)
      if (frameQueue.length === 1) start()
      monitor = setInterval(() => {
        // Same fail-closed rule as the edit monitor: a throwing provider must
        // abort this frame, never escape as an uncaught timer exception.
        try {
          if (clock() >= grant.expiresAt || ctx.sessions.get(data.sessionId) !== grant.session || ctx.workingDirectory.get(grant.session) !== grant.cwd || !configCurrent(grant)) controller.abort()
        } catch { controller.abort() }
      }, 50)
      monitor.unref?.()
      expiry = setTimeout(() => controller.abort(), Math.max(0, (sessionLease?.info.expiresAt ?? grant.expiresAt) - clock()))
      expiry.unref?.()
      const validate = async () => {
        signal.throwIfAborted()
        grantFor(data.sessionId, data.grant)
        const timeline = await validateTimeline(grant, signal)
        const executable = await localTarget(grant.executable, undefined, signal)
        const info = await ctx.fs.stat(executable, signal)
        if (executable.targetKey !== grant.executableTarget.targetKey || info?.type !== 'file' || info.version !== grant.executableVersion || ctx.fs.contains(grant.root, executable)) fail('EXECUTABLE_CHANGED', 'Installed executable identity changed; reopen', 409)
        if (isWindow) {
          if (grant.closing) fail('BAD_GRANT', 'Preview is closing; open a new grant', 403)
          const project = await localTarget(grant.projectPath, undefined, signal)
          if (project.targetKey !== grant.project.targetKey) fail('PATH_CHANGED', 'Project identity changed; reopen preview', 409)
          const binding = grant.windowExecutable
          sameWindowExecutable(binding, await windowExecutableIdentity(binding.executable, grant.root, project, executable, signal))
        }
        if (programRender) await validateProgram(sessionLease, grant, signal)
        if (isAuto && !automatic.active(sessionLease)) fail('SESSION_EXPIRED', 'Automatic window authorization ended', 403)
        if (data.contentVersion !== grant.contentVersion) fail(`STALE_${errorPrefix}`, 'Displayed version changed', 409)
        grantFor(data.sessionId, data.grant)
        signal.throwIfAborted()
        return ctx.fs.processPath(timeline)
      }
      // Waiting consumes the SAME request lease; no polling, retries or
      // deadline extension. Abort removes only this entry in finally.
      await new Promise((resolve, reject) => {
        const abort = () => { signal.removeEventListener('abort', abort); reject(signal.reason) }
        signal.addEventListener('abort', abort, { once: true })
        turn.then(resolve, reject).finally(() => signal.removeEventListener('abort', abort))
        if (signal.aborted) abort()
      })
      signal.throwIfAborted()
      // Predecessor cleanup is complete, but failed cleanup or an unrelated
      // read may still own a child. Refuse once, never wait/retry behind it.
      if (reader.frameBusy || [...frameOwners].some(owner => owner !== grant && !frameQueue.some(entry => entry.grant === owner))) fail(`${errorPrefix}_BUSY`, 'Previous process ownership has not been reaped', 409)
      const path = await validate()
      if (windowOperation) {
        // Authoritative read, not SDK stat. It uses this operation's same
        // deadline and owner; unlike control/inspect it cannot mutate grants.
        const inspectWindow = async () => {
          let current
          try { current = await reader.read(grant.executable, path, 'inspect', undefined, signal, grant.configSnapshot, windowOperation.owner) }
          catch (error) { if (error instanceof PreviewError && error.details) delete error.details; throw error }
          await validate()
          signal.throwIfAborted()
          if (current.content_version !== data.contentVersion) fail('STALE_WINDOW', 'Saved timeline changed; refresh the preview', 409)
          return current.document
        }
        if (formalArchive) {
          const before = (await inspectWindow()).project
          if (!Number.isSafeInteger(before?.duration_frames) || data.frame >= before.duration_frames) {
            fail('ARCHIVE_DENIED', 'The approved point must be inside the current saved timeline', 422)
          }
          const archive = await reader.previewFormalArchive(grant.windowExecutable, path, data.frame, data.contentVersion,
            signal, grant.configSnapshot, windowOperation.owner)
          const after = (await inspectWindow()).project
          validateFormalArchiveRange(archive, before)
          validateFormalArchiveRange(archive, after)
          return new Response(archive.bytes, { headers: {
            'Content-Type': 'video/mp4', 'Content-Length': String(archive.bytes.length), 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
            'X-Framivra-Media-Kind': 'approved-formal-archive/v1', 'X-Framivra-Operation-Id': data.operationId,
            'X-Framivra-Version': data.contentVersion, 'X-Framivra-Start': String(archive.start), 'X-Framivra-End': String(archive.end),
            'X-Framivra-FPS': `${archive.fps.num}/${archive.fps.den}`, 'X-Framivra-SHA256': archive.sha256,
          } })
        }
        const readWarmSvg = sessionLease?.info.readWarmSvg === true && hitsWarmSvgWindow(await inspectWindow(), data.frame)
        const provisional = windowOperation.provisional
        if (provisional) {
          const info = await ctx.fs.stat(grant.timeline, signal)
          if (info?.type !== 'file' || typeof info.version !== 'string' || !info.version) fail('FIRST_FRAME_DENIED', 'No saved timeline identity for provisional visual', 403)
          provisional.timelineVersion = info.version
        }
        const result = provisional ? await reader.previewWindowFirstFrame(grant.windowExecutable, path, data.frame, data.contentVersion,
          signal, grant.configSnapshot, windowOperation.owner, {
            accept(image) {
              validatePreviewWindowRange(image, provisional.project)
              if (!provisional.closed && !signal.aborted && automatic.active(sessionLease) && grant.contentVersion === data.contentVersion) provisional.image = image
            }, clear: clearFirstFrame,
          }) : await reader.previewWindow(grant.windowExecutable, path, data.frame, data.contentVersion, signal, grant.configSnapshot, windowOperation.owner, readWarmSvg, useProgramAudio)
        await validate()
        if (!result.ok) {
          const error = new PreviewError('WINDOW_REJECTED', 'The installed core refused this audio/video window', 422)
          error.details = result.diagnostics
          throw error
        }
        validatePreviewWindowRange(result, (await inspectWindow()).project)
        if (programRender) {
          const { run, info } = sessionLease
          if (!result.inputEpoch || (run.inputEpoch && run.inputEpoch !== result.inputEpoch)) {
            sessionLease.revoked = true
            fail('WINDOW_INPUTS_CHANGED', 'Playback inputs changed; discard all whole-timeline media', 409)
          }
          if (result.bytes.length > PROGRAM_RUN_WINDOW_BYTES || result.bytes.length > info.programRun.maxBytes - run.bytes) {
            fail('PROGRAM_RUN_LIMIT', 'Whole-timeline byte budget exhausted', 429)
          }
          run.inputEpoch = result.inputEpoch
          run.bytes += result.bytes.length
          run.entries.set(data.frame, result.sha256)
        }
        if (continuation && result.inputEpoch !== data.inputEpoch) fail('WINDOW_INPUTS_CHANGED', 'Playback inputs changed across the window boundary', 409)
        if (sessionLease?.info.continuation && result.inputEpoch) sessionLease.last = { operationId: data.operationId,
          inputEpoch: result.inputEpoch, programAudio: result.programAudio, end: result.end, version: data.contentVersion, reaped: false }
        return new Response(result.bytes, { headers: {
          'Content-Type': 'video/mp4', 'Content-Length': String(result.bytes.length), 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
          ...(programRender && { 'X-Framivra-Media-Kind': 'program-preview-window/v1', 'X-Framivra-Operation-Id': data.operationId }),
          ...(result.inputEpoch && { 'X-Framivra-Input-Epoch': result.inputEpoch }),
          ...(result.programAudio === true && { 'X-Framivra-Program-Audio': '1' }),
          ...(continuation && { 'X-Framivra-Continuation-From': data.previousOperationId }),
          'X-Framivra-Version': data.contentVersion, 'X-Framivra-Start': String(result.start), 'X-Framivra-End': String(result.end), 'X-Framivra-SHA256': result.sha256,
        } })
      }
      const result = await reader.frame(grant.executable, path, data.frame, data.contentVersion, signal, grant.configSnapshot, grant.id, options)
      await validate()
      signal.throwIfAborted()
      // Exact whole response bound INCLUDING final newline; image.path is never resolved/read.
      const wire = stringifyJSON(result) + '\n'
      if (Buffer.byteLength(wire, 'utf8') > FRAME_WIRE_LIMIT) fail('FRAME_WIRE_LIMIT', 'Frame response exceeds 8 MiB including envelope/newline', 502)
      return new Response(wire, { headers: { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff' } })
    } finally {
      clearFirstFrame?.()
      clearInterval(monitor); clearTimeout(expiry)
      if (controller) {
        // Failed cleanup keeps the owner; explicit cancel retries reaping. Never unlock on HTTP abort alone.
        // Ownership release checks only THIS grant's 'frame'-class children (the controller's own kind,
        // including any retained after a failed cleanup of the same kind): an overlapping read — even this
        // grant's own plain query — must not wedge the frame owner. Global admission (active.size,
        // semanticBusy) and reap(owner) semantics are unchanged elsewhere.
        if (windowOperation) releaseWindowOwner(grant, windowOperation)
        else if (!reader.activeFor(grant.id, 'frame') && grant.frame === controller) { grant.frame = undefined; frameOwners.delete(grant) }
        settled()
        const index = frameQueue.indexOf(queued)
        if (index >= 0) {
          frameQueue.splice(index, 1)
          if (index === 0) frameQueue[0]?.start()
        }
      }
      hold.release()
    }
  }
  async function media(request) {
    const hold = lease(request, false)
    streams.add(hold.release)
    try {
      const params = new URL(request.url).searchParams
      // This endpoint never accepts a path. Opaque media IDs resolve only through a session grant.
      if ([...params.keys()].some(key => !['sessionId', 'grant', 'mediaId'].includes(key))) fail('BAD_REQUEST', 'Media accepts only sessionId, grant and mediaId')
      const validate = async (signal) => {
        const grant = grantFor(params.get('sessionId'), params.get('grant'))
        await validateTimeline(grant, signal)
        const item = grant.media.get(params.get('mediaId'))
        if (!item) fail('BAD_MEDIA', 'Media is not authorized by this preview grant', 403)
        const current = await contained(item.path, grant.projectPath, grant.project, 'file', signal)
        if (current.target.targetKey !== item.target.targetKey || current.info.version !== item.info.version) fail('MEDIA_CHANGED', 'Output identity changed; reopen preview', 409)
        return current
      }
      return await mediaResponse({ request, fs: ctx.fs, validate, ...hold })
    } catch (error) { hold.release(); throw error }
  }
  function safe(handler) {
    return request => {
      const operation = (async () => {
        // note.op is set by the handler after body parsing; the sanitized
        // failure line below then names the op without ever logging the body.
        const note = { op: ['control', 'frame', 'window'].includes(handler.name) ? undefined : handler.name }
        const startedAt = clock()
        try { return await handler(request, note) } catch (error) {
          if (error instanceof LosslessJSONError) return json({ ok: false, error: 'LOSSLESS_JSON_UNSUPPORTED', message: error.message }, 502)
          const aborted = request.signal.aborted || lifecycle.signal.aborted || error?.name === 'AbortError' || error?.name === 'TimeoutError'
          if (!(error instanceof PreviewError) && !aborted) {
            // Keep unexpected failures diagnosable for the operator without
            // exposing stacks, request bodies, raw documents or causes on wire.
            // Only the stack string is logged, bounded independently of JSON.
            try {
              const stack = typeof error?.stack === 'string' ? error.stack : `Non-Error rejection (${typeof error})`
              console.error(`[framivra-dsh-sidebar] Unexpected ${handler.name} failure:\n${stack.slice(0, 8192)}${stack.length > 8192 ? '\n[stack truncated]' : ''}`)
            } catch { /* A failed diagnostic sink must not replace this failure. */ }
          }
          if (!aborted) {
            // Bounded, redacted one-line record for EVERY failure — including
            // ordinary business rejections (PreviewError), which previously
            // left no trace. Tokens only; rate-capped; injectable sink.
            requestLog.failure({
              route: handler.name,
              op: note.op,
              code: error instanceof PreviewError ? error.code : 'PREVIEW_FAILED',
              status: error instanceof PreviewError ? error.status : 500,
              durationMs: Math.max(0, clock() - startedAt),
              kind: error instanceof PreviewError ? 'rejected' : 'unexpected',
            })
          }
          return json({ ok: false, error: error instanceof PreviewError ? error.code : aborted ? 'CANCELLED' : 'PREVIEW_FAILED',
            message: error instanceof PreviewError ? error.message : aborted ? 'Preview cancelled' : 'Preview read failed',
            ...(error instanceof PreviewError && error.details ? { diagnostics: error.details } : {}) }, error instanceof PreviewError ? error.status : aborted ? 499 : 500)
        }
      })()
      operations.add(operation)
      void operation.finally(() => operations.delete(operation))
      return operation
    }
  }
  async function internalControl(session, data, signal) {
    if (!['open', 'inspect', 'query', 'reference', 'close'].includes(data.op)) fail('WRITE_DENIED', 'Model timeline access is read-only', 403)
    if (data.op === 'close') {
      // Private cleanup authority is the exact Session object that opened this grant.
      // It must still release its own resources after the Session/cwd has disappeared.
      const grant = grants.get(data.grant)
      if (!grant) return { ok: true }
      if (grant.session !== session) fail('BAD_GRANT', 'Cannot close another Session preview', 403)
      revoke(grant); grants.delete(data.grant)
      await Promise.all([grant.runningDone, grant.frameDone, grant.storageDone])
      return { ok: true }
    }
    return decodeControl(await control(new Request('http://owner/api/framivra/control', { method: 'POST', signal,
      body: stringifyJSON({ ...data, sessionId: session.id }) }), {}, false))
  }
  async function readEditCapabilities(grant, signal) {
    if (nativeWindows()) return writeUnavailable('Windows semantic store is available only through explicit owner editing; model tools remain read-only.')
    const hold = lease({ signal })
    try {
      await validateTimeline(grant, hold.signal)
      const capabilities = await probeStore(grant.executable, ctx.fs.processPath(grant.timeline), hold.signal, grant.configSnapshot, grant.id)
      // Fast-write transport is an owner UI capability, not model authority.
      const { edit_view: _editView, ...modelCapabilities } = capabilities
      return modelCapabilities
    } finally { hold.release() }
  }
  async function decodeControl(response) {
    const value = parseJSON(await response.text())
    if (!response.ok) throw new PreviewError(value.error ?? 'PREVIEW_FAILED', value.message ?? 'Timeline operation failed', response.status)
    return value
  }
  const agent = createTimelineAgent({ ctx, call: internalControl, grantFor, editCapabilities: readEditCapabilities })
  function trackTool(method) {
    return (...args) => {
      const operation = method(...args)
      operations.add(operation)
      // Both continuations settle normally; no unobserved rejected .finally promise.
      void operation.then(() => operations.delete(operation), () => operations.delete(operation))
      return operation
    }
  }
  return {
    control: safe(control), frame: safe(frame), window: safe(window), media: safe(media), diagnostics: safe(diagnostics),
    agent: { read: trackTool(agent.read) },
    async dispose() {
      agent.dispose()
      lifecycle.abort()
      for (const grant of grants.values()) revoke(grant)
      grants.clear()
      retiredWindowReceipts.clear()
      for (const release of streams) release()
      try { await reader.dispose() }
      finally { await Promise.allSettled([...operations]) }
    },
  }
}
