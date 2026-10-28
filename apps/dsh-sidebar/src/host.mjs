/** Host-only orchestration through public DSH filesystem/process capabilities. */
import { randomUUID } from 'node:crypto'
import { dirname, isAbsolute } from 'node:path'
import { PreviewError, TimelineReader, fail } from './protocol.mjs'
import { mediaResponse } from './media.mjs'
import { parseJSON, stringifyJSON, LosslessJSONError } from './json.mjs'

const MEDIA = ['out/final.mp4', 'out/draft_final.mp4']
export function relativeTimeline(path) {
  if (typeof path !== 'string' || !path || path.includes('\0') || path.includes('\\') || path.includes(':') || isAbsolute(path) || path.split('/').some(part => !part || part === '.' || part === '..')) {
    fail('BAD_PATH', 'Choose a relative timeline file inside the current Session workspace; URLs and external paths are not supported')
  }
  return path
}
const json = (value, status = 200) => new Response(stringifyJSON(value), { status, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' } })

export function createPreviewHost(ctx, config = {}) {
  const reader = new TimelineReader(ctx.subprocess, config)
  const lifecycle = new AbortController()
  const grants = new Map()
  const operations = new Set()
  const streams = new Set()
  const clock = config.clock ?? Date.now
  const ttlMs = 30 * 60 * 1000

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
    if (optional && !info) return undefined
    if (info?.type !== type) fail('NOT_REGULAR', `Expected an existing regular ${type}`, 400)
    return { target, info }
  }
  function expire() {
    for (const [id, grant] of grants) if (clock() >= grant.expiresAt) grants.delete(id)
  }
  function grantFor(sessionId, id) {
    expire()
    const session = sessionFor(sessionId)
    const grant = grants.get(id)
    if (!grant || grant.session !== session || grant.cwd !== ctx.workingDirectory.get(session)) fail('BAD_GRANT', 'Preview grant expired, closed, belongs to another Session, or workspace changed', 403)
    return grant
  }
  async function validateTimeline(grant, signal) {
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
  async function control(request) {
    const hold = lease(request)
    try {
      const data = parseJSON(await request.text())
      if (!data || typeof data !== 'object' || Array.isArray(data)) fail('BAD_REQUEST', 'Expected a JSON object')
      const session = sessionFor(data.sessionId)
      if (data.op === 'open') {
        const relative = relativeTimeline(data.timeline)
        const cwd = ctx.workingDirectory.get(session) // Never accept cwd/root from the browser.
        const root = await localTarget(cwd, undefined, hold.signal)
        if ((await ctx.fs.stat(root, hold.signal))?.type !== 'directory') fail('BAD_WORKSPACE', 'Session workspace is unavailable')
        const { target: timeline } = await contained(relative, cwd, root, 'file', hold.signal)
        const projectPath = dirname(ctx.fs.processPath(timeline))
        const project = await localTarget(projectPath, undefined, hold.signal)
        const executable = await reader.executable(hold.signal)
        const executableTarget = await localTarget(executable, undefined, hold.signal)
        if (ctx.fs.contains(root, executableTarget)) fail('UNTRUSTED_EXECUTABLE', 'Do not execute vtx from the project workspace', 403)
        const capabilities = await reader.read(executable, ctx.fs.processPath(timeline), 'capabilities', undefined, hold.signal)
        const snapshot = await reader.read(executable, ctx.fs.processPath(timeline), 'inspect', undefined, hold.signal)
        const media = new Map()
        for (const path of MEDIA) {
          const item = await contained(path, projectPath, project, 'file', hold.signal, true)
          if (item) media.set(randomUUID(), { path, ...item })
        }
        hold.signal.throwIfAborted()
        if (ctx.workingDirectory.get(session) !== cwd || ctx.sessions.get(data.sessionId) !== session) fail('PATH_CHANGED', 'Session workspace changed; reopen preview', 409)
        expire()
        if (grants.size >= 32) fail('GRANT_LIMIT', 'Close an existing preview before opening another', 429)
        const grant = randomUUID()
        grants.set(grant, { session, cwd, root, relative, timeline, project, projectPath, executable, media, expiresAt: clock() + ttlMs })
        return json({ grant, snapshot, capabilities, expires_in_ms: ttlMs,
          media: [...media].map(([mediaId, item]) => ({ mediaId, label: item.path, provenance: 'unknown', warning: 'Existing output; correspondence to this timeline content_version is NOT verified' })) })
      }
      const grant = grantFor(data.sessionId, data.grant)
      if (data.op === 'close') { grants.delete(data.grant); return json({ ok: true }) }
      if (!['inspect', 'query'].includes(data.op)) fail('BAD_OPERATION', 'Only open, inspect, query and close are supported')
      const timeline = await validateTimeline(grant, hold.signal)
      const result = await reader.read(grant.executable, ctx.fs.processPath(timeline), data.op, data.query, hold.signal)
      grantFor(data.sessionId, data.grant)
      return json(result)
    } finally { hold.release() }
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
        try { return await handler(request) } catch (error) {
          if (error instanceof LosslessJSONError) return json({ ok: false, error: 'LOSSLESS_JSON_UNSUPPORTED', message: error.message }, 502)
          const aborted = request.signal.aborted || lifecycle.signal.aborted || error?.name === 'AbortError' || error?.name === 'TimeoutError'
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
  return {
    control: safe(control), media: safe(media),
    async dispose() {
      lifecycle.abort()
      grants.clear()
      for (const release of streams) release()
      try { await reader.dispose() }
      finally { await Promise.allSettled([...operations]) }
    },
  }
}
