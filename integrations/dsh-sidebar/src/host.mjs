/** Host-only orchestration through public DSH filesystem/process capabilities. */
import { createHash, randomUUID } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { dirname, isAbsolute, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { PreviewError, TimelineReader, fail, HOST_IDENTITY, createRequestLogger, BAKED_BUILD_STAMP, BUILD_STAMP_MARKER, HOST_LIB_MODULES, CLIENT_MODULES, composeBuildStamp } from './protocol.mjs'
import { mediaResponse } from './media.mjs'
import { boundedText, CONTROL_REQUEST_LIMIT, FRAME_REQUEST_LIMIT, FRAME_WIRE_LIMIT } from './bounded.mjs'
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
  let semanticAdmission = 0
  const clock = config.clock ?? Date.now
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
    grant.frame?.abort()
    grant.write?.abort()
    grant.write = undefined
    grant.plan = undefined
  }
  function expire() {
    for (const [id, grant] of grants) if (clock() >= grant.expiresAt) {
      revoke(grant)
      grants.delete(id)
      // The id is about to become unaddressable; a last best-effort scoped reap
      // prevents an expired grant from wedging the global semantic gate.
      reader.reap(id).catch(() => {})
    }
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
  async function validateTimeline(grant, signal, allowStaleConfig = false) {
    if (!allowStaleConfig && !configCurrent(grant)) fail('CONFIG_CHANGED', 'Host plugin configuration changed; reopen this preview and re-authorize', 409)
    const root = await localTarget(grant.cwd, undefined, signal)
    if (root.targetKey !== grant.root.targetKey) fail('PATH_CHANGED', 'Workspace identity changed; reopen preview', 409)
    const result = await contained(grant.relative, grant.cwd, root, 'file', signal)
    if (result.target.targetKey !== grant.timeline.targetKey) fail('PATH_CHANGED', 'Timeline identity changed; reopen preview', 409)
    return result.target
  }
  function lease(request, deadline = true) {
    lifecycle.signal.throwIfAborted()
    const controller = new AbortController()
    const signal = AbortSignal.any([lifecycle.signal, request.signal, controller.signal])
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
    return { signal, release }
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
      const capabilities = await reader.read(executable, placeholder, 'capabilities', undefined, hold.signal, config, 'diagnostics')
      let storeCapabilities
      try { storeCapabilities = await reader.store(executable, placeholder, 'capabilities', undefined, hold.signal, config, 'diagnostics') }
      catch (error) {
        if (error instanceof PreviewError && error.code === 'CLI_UNSUPPORTED') {
          storeCapabilities = { ok: false, error: 'CLI_UNSUPPORTED', message: 'Installed vtx does not advertise the semantic store; reads remain usable' }
        } else throw error
      }
      hold.signal.throwIfAborted()
      if (snapshotConfig().fingerprint !== config.fingerprint) fail('CONFIG_CHANGED', 'Host plugin configuration changed during the check; run the check again', 409)
      return json({ ok: true, schema: 'framivra.diagnostics/v1',
        config: { vtxExecutable: config.vtxExecutable, trustedPath: config.trustedPath },
        executable: { path: ctx.fs.processPath(executableTarget), regularFile: true },
        capabilities, storeCapabilities })
    } finally { hold.release() }
  }
  async function control(request, note = {}) {
    const hold = lease(request)
    try {
      let raw
      try { raw = await boundedText(request, CONTROL_REQUEST_LIMIT, hold.signal) }
      catch (error) { if (error.message?.startsWith('BODY_LIMIT:')) fail('REQUEST_TOO_LARGE', error.message, 413); throw error }
      hold.signal.throwIfAborted() // An aborted body read must surface as CANCELLED, never as a JSON parse error.
      const data = parseJSON(raw)
      if (!data || typeof data !== 'object' || Array.isArray(data)) fail('BAD_REQUEST', 'Expected a JSON object')
      if (typeof data.op === 'string') note.op = data.op
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
        const capabilities = await reader.read(executable, ctx.fs.processPath(timeline), 'capabilities', undefined, hold.signal, config, grantId)
        const snapshot = await reader.read(executable, ctx.fs.processPath(timeline), 'inspect', undefined, hold.signal, config, grantId)
        const media = new Map()
        for (const path of MEDIA) {
          const item = await contained(path, projectPath, project, 'file', hold.signal, true)
          if (item) media.set(randomUUID(), { path, ...item })
        }
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
        grants.set(grantId, { id: grantId, session, cwd, root, relative, timeline, project, projectPath, executable, executableTarget, executableVersion, capabilities, media, contentVersion: snapshot.content_version, expiresAt: clock() + ttlMs, configFingerprint: config.fingerprint, configSnapshot: config })
        return json({ grant: grantId, snapshot, capabilities, expires_in_ms: ttlMs, host: hostIdentity(),
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
      const cleanup = ['close'].includes(data.op) || ['revoke-write', 'invalidate-plan', 'cancel-write'].includes(data.op)
      const grant = grantFor(data.sessionId, data.grant, cleanup)
      if (data.op === 'close') {
        revoke(grant)
        await Promise.all([grant.runningDone, grant.frameDone])
        // Scoped reap BEFORE untracking: an unconfirmed teardown keeps the grant
        // alive so an explicit retry can finish it; never half-deleted.
        await reader.reap(grant.id)
        grants.delete(data.grant)
        return json({ ok: true })
      }
      if (['proposal-list', 'proposal-reject', 'proposal-confirm'].includes(data.op)) {
        if (data.op === 'proposal-confirm' && frameOwners.size) fail('FRAME_BUSY', 'Cancel the frame and wait for actual cleanup before confirming', 409)
        semanticAdmission++
        try { return json(await agent.handle(data, grant, hold.signal)) }
        finally { semanticAdmission-- }
      }
      if (['authorize-write', 'revoke-write', 'invalidate-plan', 'cancel-write', 'plan', 'commit', 'status', 'recover', 'adopt', 'undo'].includes(data.op)) {
        if (['plan', 'commit', 'adopt', 'undo'].includes(data.op) && agent.hasLive(grant)) fail('PROPOSAL_PENDING', 'Resolve the pending agent proposal before starting a different semantic operation', 409)
        if (!['revoke-write', 'invalidate-plan'].includes(data.op) && frameOwners.size) fail('FRAME_BUSY', 'Cancel the frame and confirm actual process reaping before semantic operations', 409)
        semanticAdmission++
        try { return await editControl(data, grant, hold.signal, false, cleanup) }
        finally { semanticAdmission-- }
      }
      if (!['inspect', 'query', 'reference'].includes(data.op)) fail('BAD_OPERATION', 'Unknown operation')
      if (data.op === 'reference' && data.query?.expected_content_version !== grant.contentVersion) fail('STALE_REFERENCE', 'Reference version must match this opened preview; refresh first', 409)
      const timeline = await validateTimeline(grant, hold.signal)
      const result = await reader.read(grant.executable, ctx.fs.processPath(timeline), data.op, data.query, hold.signal, grant.configSnapshot, grant.id)
      grantFor(data.sessionId, data.grant)
      if (data.op === 'inspect') {
        if (grant.contentVersion !== result.content_version) { grant.plan = undefined; grant.planEpoch = (grant.planEpoch ?? 0) + 1 }
        grant.contentVersion = result.content_version
        if (grant.recoveryObserved) { grant.outcomeUnknown = false; grant.recoveryObserved = false }
        // Every successful inspect reports the observed version; agent.observed()
        // itself still requires row.outcome_unknown && row.recovered to reconcile.
        agent.observed(grant, result)
      }
      return json(result)
    } finally { hold.release() }
  }
  async function writeCapabilities(grant, signal) {
    if (process.platform !== 'linux') fail('WRITE_UNSUPPORTED', 'Semantic writes are verified only on the local Linux backend', 422)
    const capabilities = await reader.store(grant.executable, ctx.fs.processPath(grant.timeline), 'capabilities', undefined, signal, grant.configSnapshot, grant.id)
    if (!capabilities.ok || !capabilities.operations?.includes('edit') || !capabilities.edit?.operations?.length) fail('WRITE_UNSUPPORTED', 'Installed vtx does not advertise semantic edit/plan', 422)
    const transport = capabilities.request_transport?.stdin
    if (transport?.flag !== '--request-stdin' || transport.max_bytes !== 8388608) fail('WRITE_UNSUPPORTED', 'Installed vtx lacks the official bounded --request-stdin transport; read/copy remain available. Reopening /dev/stdin is not a supported fallback.', 422)
    return capabilities
  }
  // readOnlyPlan is private host authority, never a browser/model input field.
  async function editControl(data, grant, signal, readOnlyPlan = false, cleanup = false) {
    if (readOnlyPlan && data.op !== 'plan') fail('WRITE_DENIED', 'Model authority is restricted to zero-write planning', 403)
    await validateTimeline(grant, signal, cleanup)
    grantFor(data.sessionId, data.grant, cleanup)
    if (data.op === 'revoke-write') { revoke(grant); await Promise.all([grant.runningDone, grant.frameDone]); return json({ ok: true }) }
    // Revoking a frozen plan is host bookkeeping, not permission to spawn or write.
    // cancel-write IS the explicit store-child cancel: it must never be gated
    // by a stuck semanticBusy (a failed cleanup), or the retry path wedges.
    if (!['invalidate-plan', 'cancel-write'].includes(data.op) && (grant.frame || (reader.semanticBusy && !grant.running))) fail('FRAME_BUSY', 'Explicitly cancel the frame and wait for process reaping before semantic operations', 409)
    if (data.op === 'authorize-write') {
      revoke(grant)
      await grant.runningDone
      const capabilities = await writeCapabilities(grant, signal)
      grantFor(data.sessionId, data.grant)
      await validateTimeline(grant, signal)
      revoke(grant)
      grant.write = new AbortController()
      grant.writeKey = randomUUID()
      return json({ ok: true, writeGrant: grant.writeKey, capabilities })
    }
    if (!readOnlyPlan && (!grant.write || grant.write.signal.aborted || data.writeGrant !== grant.writeKey)) fail('WRITE_DENIED', 'Read access is not write permission; explicitly authorize this Session/project first', 403)
    if (readOnlyPlan) { await writeCapabilities(grant, signal); grantFor(data.sessionId, data.grant) }
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
    if (grant.outcomeUnknown && !['status', 'recover'].includes(data.op)) fail('OUTCOME_UNKNOWN', 'Use explicit core status/recover and refresh before any new plan or transaction', 409)
    const controller = new AbortController()
    grant.running = controller
    let settled
    grant.runningDone = new Promise(resolve => { settled = resolve })
    const combined = AbortSignal.any([signal, ...(readOnlyPlan ? [] : [grant.write.signal]), controller.signal])
    // Observe changes during a child too; get() is the public Session/cwd contract.
    const monitor = setInterval(() => {
      // A throwing provider inside a timer would kill the whole Host process;
      // treat any probe failure as "world unverifiable" and abort fail-closed.
      try {
        if (ctx.sessions.get(data.sessionId) !== grant.session || ctx.workingDirectory.get(grant.session) !== grant.cwd || !configCurrent(grant)) controller.abort()
      } catch { controller.abort() }
    }, 50)
    monitor.unref?.()
    const epoch = grant.planEpoch ?? 0
    let mutating = false, request
    try {
      const path = ctx.fs.processPath(await validateTimeline(grant, combined))
      if (data.op === 'status' || data.op === 'recover') {
        mutating = true // Even status may initialize lock/manifest; write grant required.
        const result = await reader.store(grant.executable, path, data.op, undefined, combined, grant.configSnapshot, grant.id)
        grant.plan = undefined
        // A successful status can still report unresolved intents. Only an explicit
        // settled core result plus a later authoritative read may release unknown state.
        grant.recoveryObserved = result.ok === true && result.pending === false
        agent.recovered(grant, result)
        return json(result)
      }
      if (data.op === 'commit') {
        const plan = grant.plan
        if (!plan || plan.token !== data.planToken) fail('PLAN_INVALID', 'Plan expired or changed; request a new plan', 409)
        request = plan.request
        // No body replacement, fresh ID, auto-rebase or automatically repeated commit.
        grant.plan = undefined
        mutating = true
        const result = await reader.store(grant.executable, path, 'commit', request, combined, grant.configSnapshot, grant.id)
        return json({ ...result, submitted_request: request })
      }
      if (data.base_revision !== grant.contentVersion) fail('STALE_BASE', 'Refresh the authoritative timeline before planning', 409)
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
        return json({ ...await reader.store(grant.executable, path, 'commit', request, combined, grant.configSnapshot, grant.id), submitted_request: request })
      }
      grant.plan = undefined
      const result = await reader.store(grant.executable, path, 'plan', request, combined, grant.configSnapshot, grant.id)
      grantFor(data.sessionId, data.grant)
      await validateTimeline(grant, combined)
      if (!result.ok) return json({ ...result, submitted_request: request })
      combined.throwIfAborted()
      if ((grant.planEpoch ?? 0) !== epoch) fail('PLAN_INVALID', 'Selection or intent changed while planning', 409)
      const token = randomUUID()
      grant.plan = { token, request }
      return json({ ...result, planToken: token, submitted_request: request })
    } catch (error) {
      if (mutating) { grant.outcomeUnknown = true; return json({ ok: false, schema: 'framivra.timeline.store/v1', code: 'outcome_unknown', op_id: request?.op_id ?? null,
        submitted_request: request, message: 'Command interrupted or result unavailable. It may have committed; use explicit core status/recover. Never generate a replacement operation to retry.',
        diagnostics: { error: error.code ?? error.name, message: error.message } }) }
      throw error
    } finally { clearInterval(monitor); grant.running = undefined; settled() }
  }
  async function frame(request, note = {}) {
    if (request.method !== 'POST') fail('METHOD_NOT_ALLOWED', 'Frame is POST-only', 405)
    const hold = lease(request)
    let grant, controller, settled, monitor, expiry
    try {
      let text
      try { text = await boundedText(request, FRAME_REQUEST_LIMIT, hold.signal) }
      catch (error) { if (error.message?.startsWith('BODY_LIMIT:')) fail('REQUEST_TOO_LARGE', error.message, 413); throw error }
      const data = parseJSON(text)
      if (!data || typeof data !== 'object' || Array.isArray(data) || Object.keys(data).some(key => !['op', 'sessionId', 'grant', 'frame', 'contentVersion', 'renderConsent', 'operationId'].includes(key))) fail('BAD_FRAME', 'Only the bounded frame operation fields are accepted')
      if (typeof data.op === 'string') note.op = data.op
      grant = grantFor(data.sessionId, data.grant, data.op === 'cancel')
      if (data.op === 'cancel') {
        grant.frame?.abort()
        await grant.frameDone
        // Scoped to THIS grant only: a cancel must never terminate children
        // spawned for another grant or session (or a diagnostics probe) that
        // happen to be in flight. Failed cleanups stay owned for a retry.
        await reader.reap(grant.id)
        grant.frame = undefined
        frameOwners.delete(grant)
        return json({ ok: true, reaped: true })
      }
      if (data.op !== 'render' || data.renderConsent !== 'cache/preview-only' || !/^[a-f0-9-]{36}$/.test(data.operationId)) fail('RENDER_DENIED', 'A fresh explicit preview-cache write consent is required', 403)
      if (!Number.isSafeInteger(data.frame) || data.frame < 0 || data.contentVersion !== grant.contentVersion || !/^sha256:[a-f0-9]{64}$/.test(data.contentVersion)) fail('STALE_FRAME', 'Use an exact saved version and pinned integer frame', 409)
      if (!inlineCapable(grant.capabilities)) fail('FRAME_UNSUPPORTED', 'Installed core must actually advertise --include-png; no path fallback', 422)
      if (semanticAdmission || grant.running || grant.outcomeUnknown || grant.plan || agent.hasLive(grant)) fail('WRITE_BUSY', 'Resolve the active/unknown write or pending plan and refresh before a frame', 409)
      if (frameOwners.size || grant.frame || reader.active.size) fail('FRAME_BUSY', 'Previous process ownership has not been reaped', 409)
      grant.frameNonces ??= new Set()
      if (grant.frameNonces.has(data.operationId)) fail('FRAME_REPLAY', 'A render click is single-use; no automatic retry', 409)
      if (grant.frameNonces.size >= 128) fail('FRAME_LIMIT', 'Reopen this preview before more captures', 429)
      grant.frameNonces.add(data.operationId)
      controller = new AbortController()
      grant.frame = controller // Synchronous admission: held until actual done + waitForExit.
      frameOwners.add(grant)
      grant.frameDone = new Promise(resolve => { settled = resolve })
      const signal = AbortSignal.any([hold.signal, controller.signal])
      monitor = setInterval(() => {
        // Same fail-closed rule as the edit monitor: a throwing provider must
        // abort this frame, never escape as an uncaught timer exception.
        try {
          if (clock() >= grant.expiresAt || ctx.sessions.get(data.sessionId) !== grant.session || ctx.workingDirectory.get(grant.session) !== grant.cwd || !configCurrent(grant)) controller.abort()
        } catch { controller.abort() }
      }, 50)
      monitor.unref?.()
      expiry = setTimeout(() => controller.abort(), Math.max(0, grant.expiresAt - clock()))
      expiry.unref?.()
      const validate = async () => {
        signal.throwIfAborted()
        grantFor(data.sessionId, data.grant)
        const timeline = await validateTimeline(grant, signal)
        const executable = await localTarget(grant.executable, undefined, signal)
        const info = await ctx.fs.stat(executable, signal)
        if (executable.targetKey !== grant.executableTarget.targetKey || info?.type !== 'file' || info.version !== grant.executableVersion || ctx.fs.contains(grant.root, executable)) fail('EXECUTABLE_CHANGED', 'Installed executable identity changed; reopen', 409)
        if (data.contentVersion !== grant.contentVersion) fail('STALE_FRAME', 'Displayed version changed', 409)
        grantFor(data.sessionId, data.grant)
        signal.throwIfAborted()
        return ctx.fs.processPath(timeline)
      }
      const path = await validate()
      const result = await reader.frame(grant.executable, path, data.frame, data.contentVersion, signal, grant.configSnapshot, grant.id)
      await validate()
      signal.throwIfAborted()
      // Exact whole response bound INCLUDING final newline; image.path is never resolved/read.
      const wire = stringifyJSON(result) + '\n'
      if (Buffer.byteLength(wire, 'utf8') > FRAME_WIRE_LIMIT) fail('FRAME_WIRE_LIMIT', 'Frame response exceeds 8 MiB including envelope/newline', 502)
      return new Response(wire, { headers: { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff' } })
    } finally {
      clearInterval(monitor); clearTimeout(expiry)
      if (controller) {
        // Failed cleanup keeps the owner; explicit cancel retries reaping. Never unlock on HTTP abort alone.
        // Ownership release checks only THIS grant's 'frame'-class children (the controller's own kind,
        // including any retained after a failed cleanup of the same kind): an overlapping read — even this
        // grant's own plain query — must not wedge the frame owner. Global admission (active.size,
        // semanticBusy) and reap(owner) semantics are unchanged elsewhere.
        if (!reader.activeFor(grant.id, 'frame') && grant.frame === controller) { grant.frame = undefined; frameOwners.delete(grant) }
        settled()
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
        const note = { op: handler.name === 'control' || handler.name === 'frame' ? undefined : handler.name }
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
    if (data.op === 'close') {
      // Private cleanup authority is the exact Session object that opened this grant.
      // It must still release its own resources after the Session/cwd has disappeared.
      const grant = grants.get(data.grant)
      if (!grant) return { ok: true }
      if (grant.session !== session) fail('BAD_GRANT', 'Cannot close another Session preview', 403)
      revoke(grant); grants.delete(data.grant)
      await Promise.all([grant.runningDone, grant.frameDone])
      return { ok: true }
    }
    return decodeControl(await control(new Request('http://owner/api/framivra/control', { method: 'POST', signal,
      body: stringifyJSON({ ...data, sessionId: session.id }) })))
  }
  async function readEditCapabilities(grant, signal) {
    const hold = lease({ signal })
    try {
      await validateTimeline(grant, hold.signal)
      return await reader.store(grant.executable, ctx.fs.processPath(grant.timeline), 'capabilities', undefined, hold.signal, grant.configSnapshot, grant.id)
    } catch (error) {
      // Only admission/protocol mismatches degrade to an advisory: reads stay usable.
      // Cleanup, cancellation, timeout and truncation failures must propagate.
      if (error instanceof PreviewError && (error.code === 'CLI_UNSUPPORTED' || error.code === 'PROTOCOL_MISMATCH')) {
        return { ok: false, error: error.code, message: 'Installed vtx does not advertise the semantic store; reads remain usable' }
      }
      throw error
    } finally { hold.release() }
  }
  async function decodeControl(response) {
    const value = parseJSON(await response.text())
    if (!response.ok) throw new PreviewError(value.error ?? 'PREVIEW_FAILED', value.message ?? 'Timeline operation failed', response.status)
    return value
  }
  async function planProposal(grant, data, signal) {
    const hold = lease({ signal })
    if (frameOwners.size) { hold.release(); fail('FRAME_BUSY', 'Cancel the frame and wait for actual cleanup before planning', 409) }
    semanticAdmission++
    try {
      const result = await decodeControl(await editControl(data, grant, hold.signal, true))
      return { result, frozen: grant.plan }
    } finally { semanticAdmission--; hold.release() }
  }
  async function commitProposal(grant, data, frozen, signal) {
    grant.plan = frozen
    return decodeControl(await editControl({ ...data, op: 'commit', planToken: frozen.token }, grant, signal))
  }
  const agent = createTimelineAgent({ ctx, clock, ttlMs, call: internalControl, grantFor,
    plan: planProposal, commit: commitProposal, validate: validateTimeline, editCapabilities: readEditCapabilities })
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
    control: safe(control), frame: safe(frame), media: safe(media), diagnostics: safe(diagnostics),
    agent: { read: trackTool(agent.read), propose: trackTool(agent.propose), status: trackTool(agent.status) },
    async dispose() {
      agent.dispose()
      lifecycle.abort()
      for (const grant of grants.values()) revoke(grant)
      grants.clear()
      for (const release of streams) release()
      try { await reader.dispose() }
      finally { await Promise.allSettled([...operations]) }
    },
  }
}
