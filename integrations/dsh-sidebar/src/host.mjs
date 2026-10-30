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
  function revoke(grant) {
    grant.write?.abort()
    grant.write = undefined
    grant.plan = undefined
  }
  function expire() {
    for (const [id, grant] of grants) if (clock() >= grant.expiresAt) { revoke(grant); grants.delete(id) }
  }
  function grantFor(sessionId, id) {
    expire()
    const session = sessionFor(sessionId)
    const grant = grants.get(id)
    if (!grant || grant.session !== session || grant.cwd !== ctx.workingDirectory.get(session)) {
      if (grant && (ctx.sessions.get(grant.session.id) === undefined || grant.cwd !== ctx.workingDirectory.get(grant.session))) revoke(grant)
      fail('BAD_GRANT', 'Preview grant expired, closed, belongs to another Session, or workspace changed', 403)
    }
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
        grants.set(grant, { session, cwd, root, relative, timeline, project, projectPath, executable, media, contentVersion: snapshot.content_version, expiresAt: clock() + ttlMs })
        return json({ grant, snapshot, capabilities, expires_in_ms: ttlMs,
          media: [...media].map(([mediaId, item]) => ({ mediaId, label: item.path, provenance: 'unknown', warning: 'Existing output; correspondence to this timeline content_version is NOT verified' })) })
      }
      const grant = grantFor(data.sessionId, data.grant)
      if (data.op === 'close') { revoke(grant); grants.delete(data.grant); await grant.runningDone; return json({ ok: true }) }
      if (['authorize-write', 'revoke-write', 'invalidate-plan', 'cancel-write', 'plan', 'commit', 'status', 'recover', 'adopt', 'undo'].includes(data.op)) {
        return await editControl(data, grant, hold.signal)
      }
      if (!['inspect', 'query', 'reference'].includes(data.op)) fail('BAD_OPERATION', 'Unknown operation')
      if (data.op === 'reference' && data.query?.expected_content_version !== grant.contentVersion) fail('STALE_REFERENCE', 'Reference version must match this opened preview; refresh first', 409)
      const timeline = await validateTimeline(grant, hold.signal)
      const result = await reader.read(grant.executable, ctx.fs.processPath(timeline), data.op, data.query, hold.signal)
      grantFor(data.sessionId, data.grant)
      if (data.op === 'inspect') {
        if (grant.contentVersion !== result.content_version) { grant.plan = undefined; grant.planEpoch = (grant.planEpoch ?? 0) + 1 }
        grant.contentVersion = result.content_version
      }
      return json(result)
    } finally { hold.release() }
  }
  async function editControl(data, grant, signal) {
    await validateTimeline(grant, signal)
    grantFor(data.sessionId, data.grant)
    if (data.op === 'revoke-write') { revoke(grant); await grant.runningDone; return json({ ok: true }) }
    if (data.op === 'authorize-write') {
      if (process.platform !== 'linux') fail('WRITE_UNSUPPORTED', 'Semantic writes are verified only on the local Linux backend', 422)
      revoke(grant)
      await grant.runningDone
      const capabilities = await reader.store(grant.executable, ctx.fs.processPath(grant.timeline), 'capabilities', undefined, signal)
      grantFor(data.sessionId, data.grant)
      await validateTimeline(grant, signal)
      if (!capabilities.ok || !capabilities.operations?.includes('edit') || !capabilities.edit?.operations?.length) fail('WRITE_UNSUPPORTED', 'Installed vtx does not advertise semantic edit/plan', 422)
      const transport = capabilities.request_transport?.stdin
      if (transport?.flag !== '--request-stdin' || transport.max_bytes !== 8388608) fail('WRITE_UNSUPPORTED', 'Installed vtx lacks the official bounded --request-stdin transport; read/copy remain available. Reopening /dev/stdin is not a supported fallback.', 422)
      revoke(grant)
      grant.write = new AbortController()
      grant.writeKey = randomUUID()
      return json({ ok: true, writeGrant: grant.writeKey, capabilities })
    }
    if (!grant.write || grant.write.signal.aborted || data.writeGrant !== grant.writeKey) fail('WRITE_DENIED', 'Read access is not write permission; explicitly authorize this Session/project first', 403)
    if (data.op === 'cancel-write') { grant.running?.abort(); await grant.runningDone; return json({ ok: true, message: 'Process settled and reaped; original operation may have committed. Check status/recover.' }) }
    if (data.op === 'invalidate-plan') { grant.plan = undefined; grant.planEpoch = (grant.planEpoch ?? 0) + 1; return json({ ok: true }) }
    if (grant.running) fail('WRITE_BUSY', 'Wait for managed process cleanup before another command', 409)
    const controller = new AbortController()
    grant.running = controller
    let settled
    grant.runningDone = new Promise(resolve => { settled = resolve })
    const combined = AbortSignal.any([signal, grant.write.signal, controller.signal])
    // Observe changes during a child too; get() is the public Session/cwd contract.
    const monitor = setInterval(() => {
      if (ctx.sessions.get(data.sessionId) !== grant.session || ctx.workingDirectory.get(grant.session) !== grant.cwd) controller.abort()
    }, 50)
    monitor.unref?.()
    const epoch = grant.planEpoch ?? 0
    let mutating = false, request
    try {
      const path = ctx.fs.processPath(await validateTimeline(grant, combined))
      if (data.op === 'status' || data.op === 'recover') {
        mutating = true // Even status may initialize lock/manifest; write grant required.
        const result = await reader.store(grant.executable, path, data.op, undefined, combined)
        grant.plan = undefined
        return json(result)
      }
      if (data.op === 'commit') {
        const plan = grant.plan
        if (!plan || plan.token !== data.planToken) fail('PLAN_INVALID', 'Plan expired or changed; request a new plan', 409)
        request = plan.request
        // No body replacement, fresh ID, auto-rebase or automatically repeated commit.
        grant.plan = undefined
        mutating = true
        const result = await reader.store(grant.executable, path, 'commit', request, combined)
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
        return json({ ...await reader.store(grant.executable, path, 'commit', request, combined), submitted_request: request })
      }
      grant.plan = undefined
      const result = await reader.store(grant.executable, path, 'plan', request, combined)
      grantFor(data.sessionId, data.grant)
      await validateTimeline(grant, combined)
      if (!result.ok) return json({ ...result, submitted_request: request })
      combined.throwIfAborted()
      if ((grant.planEpoch ?? 0) !== epoch) fail('PLAN_INVALID', 'Selection or intent changed while planning', 409)
      const token = randomUUID()
      grant.plan = { token, request }
      return json({ ...result, planToken: token, submitted_request: request })
    } catch (error) {
      if (mutating) return json({ ok: false, schema: 'framivra.timeline.store/v1', code: 'outcome_unknown', op_id: request?.op_id ?? null,
        submitted_request: request, message: 'Command interrupted or result unavailable. It may have committed; use explicit core status/recover. Never generate a replacement operation to retry.',
        diagnostics: { error: error.code ?? error.name, message: error.message } })
      throw error
    } finally { clearInterval(monitor); grant.running = undefined; settled() }
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
      for (const grant of grants.values()) revoke(grant)
      grants.clear()
      for (const release of streams) release()
      try { await reader.dispose() }
      finally { await Promise.allSettled([...operations]) }
    },
  }
}
