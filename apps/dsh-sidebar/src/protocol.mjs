/** Adapter only: timeline interpretation and selection belong exclusively to vtx. */
import { dirname, isAbsolute } from 'node:path'

export class PreviewError extends Error {
  constructor(code, message, status = 400) {
    super(message)
    this.code = code
    this.status = status
  }
}
export const fail = (code, message, status) => { throw new PreviewError(code, message, status) }
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value)
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

export function decodeEnvelope(text, operation) {
  let value
  try { value = JSON.parse(text) } catch { fail('INVALID_JSON', 'vtx did not return complete JSON', 502) }
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
    this.active = new Set()
  }
  async executable(signal) {
    const path = await this.subprocess.resolveExecutable(this.config.vtxExecutable, { PATH: this.config.trustedPath }, signal)
    if (!isAbsolute(path)) fail('BAD_EXECUTABLE', 'Resolver returned a nonabsolute executable', 502)
    return path
  }
  async read(executable, timeline, operation, query, signal) {
    if (!['inspect', 'query', 'capabilities'].includes(operation)) fail('BAD_OPERATION', 'Only read operations are supported')
    const args = operation === 'query' ? queryArgs(query) : []
    signal.throwIfAborted()
    const handle = this.subprocess.spawn({
      argv: [executable, '-t', timeline, operation, ...args],
      cwd: dirname(executable), // Never execute Python/imports from an untrusted project cwd.
      stdio: { stdin: 'ignore', stdout: { maxBytes: 8 * 1024 * 1024 }, stderr: { maxBytes: 16 * 1024 } },
      graceMs: 1000, signal,
      env: { PATH: this.config.trustedPath, PYTHONDONTWRITEBYTECODE: '1', PYTHONSAFEPATH: '1', PYTHONPATH: undefined, PYTHONHOME: undefined },
    })
    this.active.add(handle)
    try {
      const outcome = await handle.done
      signal.throwIfAborted()
      if (outcome.exitCode !== 0) {
        const error = new PreviewError(outcome.exitCode === 2 ? 'CLI_UNSUPPORTED' : 'CLI_FAILED',
          outcome.exitCode === 2 ? 'Installed vtx does not support this read command' : 'vtx rejected this timeline or failed', 502)
        const diagnostic = handle.collected.stderr?.readFrom(0)
        error.details = { exitCode: outcome.exitCode, stderr: diagnostic?.text ?? '', stderr_lossy: diagnostic?.lossy ?? false }
        const output = handle.collected.stdout?.readFrom(0)
        if (output && !output.lossy) {
          try { error.details.diagnostics = JSON.parse(output.text).diagnostics } catch { /* Retain stderr when error JSON is absent. */ }
        }
        throw error
      }
      const output = handle.collected.stdout?.readFrom(0)
      if (!output || output.lossy) fail('TRUNCATED_JSON', 'vtx JSON exceeded the 8 MiB read limit; refusing truncated preview', 502)
      return decodeEnvelope(output.text, operation)
    } finally { this.active.delete(handle) }
  }
  async dispose() {
    const active = [...this.active]
    for (const handle of active) handle.terminate()
    await Promise.allSettled(active.map(handle => handle.done))
  }
}
