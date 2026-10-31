/** Adapter only: timeline interpretation and selection belong exclusively to vtx. */
import { dirname, isAbsolute } from 'node:path'
import { Readable } from 'node:stream'
import { boundedText, FRAME_WIRE_LIMIT } from './bounded.mjs'
import { parseJSON, stringifyJSON, LosslessJSONError } from './json.mjs'

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
  constructor(subprocess, { vtxExecutable = 'vtx', trustedPath = '/usr/local/bin:/usr/bin:/bin' } = {}) {
    if (vtxExecutable !== 'vtx' && !isAbsolute(vtxExecutable)) fail('BAD_EXECUTABLE', 'vtxExecutable must be absolute')
    if (trustedPath.split(':').some(path => !isAbsolute(path))) fail('BAD_EXECUTABLE', 'trustedPath entries must be absolute')
    this.subprocess = subprocess
    this.config = { vtxExecutable, trustedPath }
    this.active = new Map() // Ownership survives direct-command settlement and failed cleanup.
    this.lifecycle = new AbortController()
  }
  async executable(signal) {
    const path = await this.subprocess.resolveExecutable(this.config.vtxExecutable, { PATH: this.config.trustedPath }, signal)
    if (!isAbsolute(path)) fail('BAD_EXECUTABLE', 'Resolver returned a nonabsolute executable', 502)
    return path
  }
  async read(executable, timeline, operation, query, signal) {
    if (!['inspect', 'query', 'reference', 'capabilities'].includes(operation)) fail('BAD_OPERATION', 'Only read operations are supported')
    const args = operation === 'query' ? queryArgs(query) : operation === 'reference' ? referenceArgs(query) : []
    return this.command(executable, timeline, [operation, ...args], signal, text => decodeEnvelope(text, operation))
  }
  async frame(executable, timeline, frame, version, signal) {
    if (!integer(frame) || !/^sha256:[a-f0-9]{64}$/.test(version)) fail('BAD_FRAME', 'Exact saved version and integer pin required')
    return this.command(executable, timeline, ['timeline', 'frame', '--frame', String(frame), `--expected-content-version=${version}`, '--render', '--include-png', '--max-png-bytes=8388608'], signal, text => {
      const value = parseJSON(text)
      if (!object(value) || value.schema !== 'framivra.timeline.frame/v1' || value.operation !== 'frame' || typeof value.ok !== 'boolean' || !Array.isArray(value.diagnostics)) fail('PROTOCOL_MISMATCH', 'Official frame/v1 envelope required', 502)
      return value
    }, undefined, true, true)
  }
  async reap() {
    await Promise.all([...this.active.keys()].map(handle => this.cleanup(handle)))
  }
  async store(executable, timeline, operation, request, signal) {
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
    }, input, true)
  }
  async command(executable, timeline, args, signal, decode, input, business = false, streaming = false) {
    signal = AbortSignal.any([signal, this.lifecycle.signal])
    signal.throwIfAborted()
    const handle = this.subprocess.spawn({
      argv: [executable, '-t', timeline, ...args],
      cwd: dirname(executable), // Never execute Python/imports from an untrusted project cwd.
      stdio: { stdin: input === undefined ? 'ignore' : { data: input }, stdout: streaming ? 'pipe' : { maxBytes: 8 * 1024 * 1024 }, stderr: { maxBytes: 16 * 1024 } },
      graceMs: 1000, signal,
      env: { PATH: this.config.trustedPath, PYTHONDONTWRITEBYTECODE: '1', PYTHONSAFEPATH: '1', PYTHONPATH: undefined, PYTHONHOME: undefined },
    })
    this.active.set(handle, undefined)
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
      } catch {
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
function untilAborted(promise, signal) {
  return new Promise((resolve, reject) => {
    const abort = () => reject(signal.reason)
    signal.addEventListener('abort', abort, { once: true })
    Promise.resolve(promise).then(resolve, reject).finally(() => signal.removeEventListener('abort', abort))
    if (signal.aborted) { signal.removeEventListener('abort', abort); abort() }
  })
}
