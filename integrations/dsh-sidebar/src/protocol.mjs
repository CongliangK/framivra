/** Adapter only: timeline interpretation and selection belong exclusively to vtx. */
import { dirname, isAbsolute } from 'node:path'
import { Readable } from 'node:stream'
import { boundedText, FRAME_WIRE_LIMIT } from './bounded.mjs'
import { parseJSON, stringifyJSON, LosslessJSONError } from './json.mjs'
import { readConfiguration, isVolatileConfig, ConfigurationError } from './configuration.mjs'

export class PreviewError extends Error {
  constructor(code, message, status = 400) {
    super(message)
    this.code = code
    this.status = status
  }
}
export const fail = (code, message, status) => { throw new PreviewError(code, message, status) }
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value) && !JSON.isRawJSON(value)
const integer = value => Number.isSafeInteger(value) && value >= 0

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
  constructor(subprocess, config = {}) {
    this.subprocess = subprocess
    this.configSource = config
    // Plain (test/debug) sources keep fail-fast constructor validation; live
    // Volatile sources are validated on EVERY snapshot instead, because their
    // values can change at any time without a remount.
    if (!isVolatileConfig(config)) {
      const snapshot = readConfiguration(config)
      this.config = { vtxExecutable: snapshot.vtxExecutable, trustedPath: snapshot.trustedPath }
    } else this.config = undefined
    this.active = new Map() // Ownership survives direct-command settlement and failed cleanup.
    this.spawnKinds = new WeakMap() // Spawned child -> admission class ('read' | 'store-capabilities' | 'store' | 'frame').
    this.cleanupFailed = new WeakSet() // Children whose managed range was not confirmed reaped; retained until a retry succeeds.
    this.lifecycle = new AbortController()
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
   * frame child is in flight, or any child — including an ordinary read — has a
   * failed, unreaped cleanup. Ordinary reads and the pure-static store
   * capabilities probe never block semantic operations on their own.
   * @returns {boolean}
   */
  get semanticBusy() {
    for (const handle of this.active.keys()) {
      if (this.cleanupFailed.has(handle)) return true
      const kind = this.spawnKinds.get(handle)
      if (kind === 'store' || kind === 'frame') return true
    }
    return false
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
  async read(executable, timeline, operation, query, signal, snapshot) {
    if (!['inspect', 'query', 'reference', 'capabilities'].includes(operation)) fail('BAD_OPERATION', 'Only read operations are supported')
    const args = operation === 'query' ? queryArgs(query) : operation === 'reference' ? referenceArgs(query) : []
    return this.command(executable, timeline, [operation, ...args], signal, text => decodeEnvelope(text, operation), undefined, false, false, snapshot)
  }
  async frame(executable, timeline, frame, version, signal, snapshot) {
    if (!integer(frame) || !/^sha256:[a-f0-9]{64}$/.test(version)) fail('BAD_FRAME', 'Exact saved version and integer pin required')
    return this.command(executable, timeline, ['timeline', 'frame', '--frame', String(frame), `--expected-content-version=${version}`, '--render', '--include-png', '--max-png-bytes=8388608'], signal, text => {
      const value = parseJSON(text)
      if (!object(value) || value.schema !== 'framivra.timeline.frame/v1' || value.operation !== 'frame' || typeof value.ok !== 'boolean' || !Array.isArray(value.diagnostics)) fail('PROTOCOL_MISMATCH', 'Official frame/v1 envelope required', 502)
      return value
    }, undefined, true, true, snapshot)
  }
  async reap() {
    await Promise.all([...this.active.keys()].map(handle => this.cleanup(handle)))
  }
  async store(executable, timeline, operation, request, signal, snapshot) {
    if (!['capabilities', 'plan', 'commit', 'status', 'recover'].includes(operation)) fail('BAD_OPERATION', 'Unknown store command')
    // The official flag reads fd0 itself: public providers may expose a socket,
    // for which reopening /dev/stdin fails with ENXIO. No temp files/bridge.
    const args = request === undefined ? ['store', operation] : ['store', ...(operation === 'plan' ? ['plan'] : []), '--request-stdin']
    const input = request === undefined ? undefined : stringifyJSON(request)
    if (input !== undefined && Buffer.byteLength(input, 'utf8') > 8388608) fail('REQUEST_TOO_LARGE', 'Semantic request exceeds the official 8 MiB UTF-8 byte limit', 413)
    return this.command(executable, timeline, args, signal, text => {
      const result = parseJSON(text)
      if (result?.schema !== 'framivra.timeline.store/v1' || typeof result.ok !== 'boolean') fail('PROTOCOL_MISMATCH', 'Installed vtx store/v1 required', 502)
      return result
    }, input, true, undefined, snapshot)
  }
  async command(executable, timeline, args, signal, decode, input, business = false, streaming = false, snapshot) {
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
    const handle = this.subprocess.spawn({
      argv: [executable, '-t', timeline, ...args],
      cwd: dirname(executable), // Never execute Python/imports from an untrusted project cwd.
      stdio: { stdin: input === undefined ? 'ignore' : { data: input }, stdout: streaming ? 'pipe' : { maxBytes: 8 * 1024 * 1024 }, stderr: { maxBytes: 16 * 1024 } },
      graceMs: 1000, signal,
      env: { PATH: config.trustedPath, PYTHONDONTWRITEBYTECODE: '1', PYTHONSAFEPATH: '1', PYTHONPATH: undefined, PYTHONHOME: undefined },
    })
    this.active.set(handle, undefined)
    this.spawnKinds.set(handle, spawnKind(args))
    try {
      if (streaming && !handle.stdout) fail('FRAME_STREAM_UNSUPPORTED', 'Public provider did not expose the requested owner stdout pipe', 502)
      const [outcome, streamed] = await untilAborted(Promise.all([
        handle.done,
        streaming ? boundedText({ headers: new Headers(), body: Readable.toWeb(handle.stdout) }, FRAME_WIRE_LIMIT, signal) : undefined,
      ]), signal)
      signal.throwIfAborted()
      if (outcome.exitCode !== 0 && !(business && outcome.exitCode === 1)) {
        const error = new PreviewError(outcome.exitCode === 2 ? 'CLI_UNSUPPORTED' : 'CLI_FAILED',
          outcome.exitCode === 2 ? 'Installed vtx does not support this read command' : 'vtx rejected this timeline or failed', 502)
        const diagnostic = handle.collected.stderr?.readFrom(0)
        error.details = { exitCode: outcome.exitCode, stderr: diagnostic?.text ?? '', stderr_lossy: diagnostic?.lossy ?? false }
        const output = handle.collected.stdout?.readFrom(0)
        if (output && !output.lossy) {
          try { error.details.diagnostics = parseJSON(output.text).diagnostics } catch { /* Retain stderr when error JSON is absent. */ }
        }
        throw error
      }
      const output = streaming ? { text: streamed, lossy: false } : handle.collected.stdout?.readFrom(0)
      if (!output || output.lossy) fail('TRUNCATED_JSON', 'vtx JSON exceeded the 8 MiB read limit; refusing truncated preview', 502)
      const result = decode(output.text)
      if (business && (outcome.exitCode === 0) !== result.ok) fail('PROTOCOL_MISMATCH', 'Store exit/envelope mismatch', 502)
      return result
    } finally {
      await this.cleanup(handle)
      signal.throwIfAborted()
    }
  }
  cleanup(handle) {
    if (!this.active.has(handle)) return Promise.resolve()
    const pending = this.active.get(handle)
    if (pending) return pending
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
  if (args[0] === 'store') return args[1] === 'capabilities' ? 'store-capabilities' : 'store'
  if (args[0] === 'timeline' && args[1] === 'frame') return 'frame'
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
