import { useLayoutEffect, useMemo, useReducer, useState } from 'react'
import { boundedText, PROGRAM_RUN_MAX_BYTES, PROGRAM_RUN_MAX_WINDOWS, PROGRAM_RUN_TTL, PROGRAM_RUN_WINDOW_BYTES } from '../bounded.mjs'
import { matchesPreviewGrant, supportsWindow } from './preview-session.mjs'
import { projectClock } from './timecode.mjs'

const cacheName = 'framivra-private-program-preview-v1'
const versionToken = /^sha256:[a-f0-9]{64}$/
const digestToken = /^[a-f0-9]{64}$/
const timeout = () => AbortSignal.timeout(45_000)
const fault = code => Object.assign(new Error(code), { code })
const codeOf = value => typeof value === 'string' && /^[A-Z][A-Z0-9_]{0,63}$/.test(value) ? value : 'PROGRAM_RUN_FAILED'
const isLimit = error => error.code === 'PROGRAM_RUN_LIMIT' || error.name === 'QuotaExceededError'
const pinIn = (binding, frame) => Number.isSafeInteger(frame) && frame >= 0 && frame < binding.duration
const grid = (binding, frame) => frame - frame % binding.span
const original = grant => ({ grant: grant.openGrant, previewGrant: grant.id, ownerId: grant.ownerId })

// One fixed, origin-private browser cache, never a reusable archive. The Web
// Lock is shared across tabs. A crash can leave one bounded cache; holding the
// lock and starting a new explicit run is the only way to open it, after purge.
function openCache(signal) {
  const pending = Promise.withResolvers(), release = Promise.withResolvers()
  const lock = navigator.locks.request(cacheName, { ifAvailable: true }, async held => {
    if (!held) throw fault('PROGRAM_STORAGE_BUSY')
    signal.throwIfAborted()
    await caches.delete(cacheName)
    const cache = await caches.open(cacheName)
    const key = start => new URL(`/__framivra_private_program_preview__/${start}`, location.origin).href
    pending.resolve({
      put(start, blob) { return cache.put(key(start), new Response(blob)) },
      get(start) { return cache.match(key(start)) },
      async close() {
        // Retain the lock if deletion fails. Only a confirmed deletion may
        // permit a new run; retry cannot risk deleting another tab's cache.
        await caches.delete(cacheName)
        release.resolve()
        await lock
      },
    })
    await release.promise
  })
  void lock.catch(pending.reject)
  return pending.promise
}

function request(grant, opId, frame, signal) {
  return fetch('/api/framivra/window', { method: 'POST', credentials: 'same-origin', cache: 'no-store', redirect: 'error',
    headers: { 'Content-Type': 'application/json' }, signal, body: JSON.stringify({ op: 'program-render', sessionId: grant.sessionId,
      ...original(grant), operationId: opId, frame, contentVersion: grant.programRun.contentVersion }) })
}

async function verifiedBlob(response, size, sha256, signal) {
  const reader = response.body?.getReader()
  if (!reader || !Number.isSafeInteger(size) || size < 1 || size > PROGRAM_RUN_WINDOW_BYTES || !digestToken.test(sha256)) {
    await reader?.cancel().catch(() => {})
    reader?.releaseLock()
    throw fault('PROGRAM_MEDIA_INVALID')
  }
  const bytes = new Uint8Array(size)
  const abort = () => { void reader.cancel(signal.reason).catch(() => {}) }
  signal.addEventListener('abort', abort, { once: true })
  let at = 0, complete = false
  try {
    for (;;) {
      signal.throwIfAborted()
      const { done, value } = await reader.read()
      signal.throwIfAborted()
      if (done) { complete = true; break }
      if (!ArrayBuffer.isView(value) || value.BYTES_PER_ELEMENT !== 1 || value.byteLength !== value.length || value.length > size - at) {
        throw fault('PROGRAM_MEDIA_INVALID')
      }
      bytes.set(value, at); at += value.length
    }
  } finally {
    signal.removeEventListener('abort', abort)
    if (!complete) abort()
    reader.releaseLock()
  }
  if (at !== size) throw fault('PROGRAM_MEDIA_INVALID')
  const digest = await crypto.subtle.digest('SHA-256', bytes)
  signal.throwIfAborted()
  if (Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('') !== sha256) throw fault('PROGRAM_MEDIA_INVALID')
  return new Blob([bytes], { type: 'video/mp4' })
}

async function accept(response, run, start, operationId, signal) {
  if (!response.ok) {
    const value = JSON.parse(await boundedText(response, 16_384, signal))
    throw fault(codeOf(value?.error))
  }
  const { binding } = run, { headers } = response
  const end = Math.min(start + binding.span, binding.duration)
  const sha256 = headers.get('X-Framivra-SHA256'), inputEpoch = headers.get('X-Framivra-Input-Epoch')
  const length = headers.get('Content-Length'), size = Number(length)
  if (response.status !== 200 || headers.get('Content-Type') !== 'video/mp4' || headers.get('X-Framivra-Media-Kind') !== 'program-preview-window/v1' ||
      headers.get('X-Framivra-Operation-Id') !== operationId || headers.get('X-Framivra-Version') !== binding.contentVersion ||
      headers.get('X-Framivra-Start') !== String(start) || headers.get('X-Framivra-End') !== String(end) ||
      !versionToken.test(inputEpoch) || (run.inputEpoch && run.inputEpoch !== inputEpoch) || !/^[1-9][0-9]*$/.test(length) ||
      !Number.isSafeInteger(size) || size > PROGRAM_RUN_WINDOW_BYTES || !digestToken.test(sha256)) {
    await response.body?.cancel().catch(() => {})
    throw fault('PROGRAM_MEDIA_INVALID')
  }
  if (size > run.grant.programRun.maxBytes - run.bytes) {
    await response.body?.cancel().catch(() => {})
    throw fault('PROGRAM_RUN_LIMIT')
  }
  return { entry: { start, end, coverageEnd: end, size, sha256, inputEpoch, contentVersion: binding.contentVersion },
    blob: await verifiedBlob(response, size, sha256, signal) }
}

export class PreviewProgramRun {
  constructor(notify, { open = openCache, render = request, now = Date.now } = {}) {
    Object.assign(this, { notify, open, render, now })
  }

  changed(run) {
    run.events.dispatchEvent(new Event('change'))
    this.notify()
  }

  start(binding, frame) {
    if (!binding || binding.tabSignal.aborted || !pinIn(binding, frame) || (this.run && !this.run.cleaned)) return false
    const run = this.run = { binding, frame, status: 'authorizing', entries: new Map(), bytes: 0, completedFrames: 0,
      events: new EventTarget(), controller: new AbortController() }
    run.onTabAbort = () => { void this.retire(run, 'cancelled') }
    binding.tabSignal.addEventListener('abort', run.onTabAbort, { once: true })
    run.finished = this.execute(run)
    this.changed(run)
    return true
  }

  async execute(run) {
    const { binding, frame, controller: { signal } } = run
    const { call, sessionId, grant: openGrant, ownerId, contentVersion, duration, span } = binding
    try {
      run.cache = await this.open(signal)
      signal.throwIfAborted()
      // A response may mint authority after the view is gone. Wait for the
      // bounded response so cleanup can revoke exactly that authority.
      const authorizationId = run.authorizationId = crypto.randomUUID()
      let answer
      try {
        answer = await call(sessionId, { op: 'authorize-preview-session', grant: openGrant, scope: 'whole-timeline', authorizationId,
          frame, contentVersion, renderConsent: 'cache/preview-only', prepareInputs: true, storageConsent: 'temporary-browser-cache' }, timeout())
      } catch (error) {
        // The real control adapter throws a typed error on an authoritative
        // Host refusal. A disconnected/malformed response remains ambiguous.
        if (typeof error?.code === 'string') run.authorizationId = undefined
        throw error
      }
      if (answer?.ok === false) { run.authorizationId = undefined; throw fault(codeOf(answer.error)) }
      const grant = answer.grant
      if (!matchesPreviewGrant(binding, ownerId, grant)) throw fault('PROGRAM_GRANT_INVALID')
      run.grant = grant
      signal.throwIfAborted()
      const remaining = grant.expiresAt - this.now(), scope = grant.programRun
      if (!Number.isSafeInteger(grant.expiresAt) || remaining <= 0 || remaining > PROGRAM_RUN_TTL || scope?.version !== 1 ||
          scope.contentVersion !== contentVersion || scope.frame !== frame || scope.duration !== duration || scope.span !== span ||
          !Number.isSafeInteger(grant.maxWindows) || grant.maxWindows < 1 || grant.maxWindows > PROGRAM_RUN_MAX_WINDOWS ||
          !Number.isSafeInteger(scope.maxBytes) || scope.maxBytes < 1 || scope.maxBytes > PROGRAM_RUN_MAX_BYTES) throw fault('PROGRAM_GRANT_INVALID')
      run.expiry = setTimeout(() => { void this.retire(run, 'error', 'PROGRAM_RUN_EXPIRED') }, remaining)
      // Background tabs can delay the expiry timer. Check the lease at each
      // asynchronous handoff before requesting, storing or crediting a window.
      const assertLive = () => {
        signal.throwIfAborted()
        if (this.now() >= grant.expiresAt) throw fault('PROGRAM_RUN_EXPIRED')
      }
      run.status = 'running'
      let cursor = grid(binding, frame), first = true
      const advance = start => start + span < duration ? start + span : 0
      while (run.completedFrames < duration) {
        assertLive()
        if (run.entries.size >= grant.maxWindows || run.bytes >= scope.maxBytes) throw fault('PROGRAM_RUN_LIMIT')
        while (run.entries.has(cursor)) cursor = advance(cursor)
        const start = !first && run.wanted !== undefined && !run.entries.has(run.wanted) ? run.wanted : cursor
        first = false
        const id = crypto.randomUUID()
        // This is the requested interval, not evidence of rendered or cached frames.
        run.pendingWindow = { start, end: Math.min(duration, start + span) }
        this.changed(run)
        assertLive()
        const response = await this.render(grant, id, start, signal)
        assertLive()
        const { blob, entry } = await accept(response, run, start, id, signal)
        assertLive()
        await run.cache.put(start, blob)
        assertLive()
        run.pendingWindow = undefined
        run.inputEpoch = entry.inputEpoch
        run.entries.set(start, entry)
        run.bytes += entry.size
        run.completedFrames += entry.end - start
        this.changed(run)
      }
      assertLive()
      run.status = 'complete'
      this.changed(run)
    } catch (error) {
      run.pendingWindow = undefined
      if (signal.aborted) return
      if (run.grant && this.now() >= run.grant.expiresAt) {
        void this.retire(run, 'error', 'PROGRAM_RUN_EXPIRED')
      } else if (isLimit(error) && run.completedFrames > 0) {
        run.status = 'partial'; run.code = 'PROGRAM_RUN_LIMIT'
        this.changed(run)
      } else void this.retire(run, 'error', isLimit(error) ? 'PROGRAM_RUN_LIMIT' : codeOf(error.code))
    }
  }

  async read(binding, frame, observer) {
    const run = this.run
    if (!run || run.binding !== binding || !pinIn(binding, frame) || run.controller.signal.aborted) throw fault('PROGRAM_WINDOW_UNAVAILABLE')
    const reader = new AbortController()
    // The monitor has one current foreground/prefetch intent. Replacing it
    // only changes the next pick; it never starts or aborts a second worker.
    run.reader?.abort()
    run.reader = reader
    const signal = AbortSignal.any([run.controller.signal, reader.signal, ...(observer ? [observer] : [])])
    const start = run.wanted = grid(binding, frame)
    try {
      while (!run.entries.has(start)) {
        signal.throwIfAborted()
        if (run.status !== 'running' && run.status !== 'authorizing') throw fault('PROGRAM_WINDOW_UNAVAILABLE')
        await new Promise((resolve, reject) => {
          const remove = () => { run.events.removeEventListener('change', update); signal.removeEventListener('abort', abort) }
          const update = () => { remove(); resolve() }
          const abort = () => { remove(); reject(signal.reason) }
          run.events.addEventListener('change', update, { once: true })
          signal.addEventListener('abort', abort, { once: true })
        })
      }
      signal.throwIfAborted()
      const entry = run.entries.get(start)
      const response = await run.cache.get(start)
      if (!response) throw fault('PROGRAM_MEDIA_INVALID')
      const blob = await verifiedBlob(response, entry.size, entry.sha256, signal)
      await this.checkEntry(run, entry, signal)
      return { ...entry, blob }
    } catch (error) {
      if (!signal.aborted && error.code !== 'PROGRAM_WINDOW_UNAVAILABLE') void this.retire(run, 'error', codeOf(error.code))
      throw error
    } finally {
      if (run.reader === reader) { run.reader = undefined; run.wanted = undefined }
    }
  }

  async checkEntry(run, entry, signal) {
    const { binding, grant } = run
    const assertCurrent = () => {
      signal.throwIfAborted()
      if (this.run !== run || run.grant !== grant || !grant || this.now() >= grant.expiresAt ||
          grant.programRun.contentVersion !== entry.contentVersion || run.entries.get(entry.start) !== entry) throw fault('PROGRAM_GRANT_INVALID')
    }
    assertCurrent()
    const answer = await binding.call(binding.sessionId, { op: 'program-check', ...original(grant), frame: entry.start, sha256: entry.sha256 }, signal)
    assertCurrent()
    if (answer?.ok !== true) throw fault(codeOf(answer?.error))
    if (answer.grantId !== grant.id || answer.frame !== entry.start || answer.sha256 !== entry.sha256) throw fault('PROGRAM_GRANT_INVALID')
  }

  async recheck(binding, media, observer) {
    const run = this.run
    if (!run || run.binding !== binding || run.controller.signal.aborted) throw fault('PROGRAM_WINDOW_UNAVAILABLE')
    const signal = AbortSignal.any([run.controller.signal, observer])
    try {
      signal.throwIfAborted()
      const entry = run.entries.get(media.frame)
      if (!entry || entry.end !== media.end || entry.contentVersion !== media.contentVersion ||
          entry.inputEpoch !== media.inputEpoch || entry.sha256 !== media.sha256) throw fault('PROGRAM_MEDIA_INVALID')
      await this.checkEntry(run, entry, signal)
    } catch (error) {
      if (!signal.aborted) void this.retire(run, 'error', codeOf(error.code))
      throw error
    }
  }

  cancel() { return this.run ? this.retire(this.run, 'cancelled') : Promise.resolve(true) }

  retire(run, status, code) {
    if (run.cleaned) return Promise.resolve(true)
    if (run.cleanup) return run.cleanup
    run.controller.abort(fault(code ?? 'PROGRAM_RUN_CANCELLED'))
    run.binding.tabSignal.removeEventListener('abort', run.onTabAbort)
    clearTimeout(run.expiry)
    run.entries.clear()
    run.completedFrames = 0
    run.pendingWindow = undefined
    run.status = status
    run.code = code
    run.cleanup = (async () => {
      const host = async () => {
        // A known lease can be revoked while a render response or cache write
        // is still pending. Only authorization itself may return a late grant.
        if (!run.grant) await run.finished
        const { call, sessionId, grant, ownerId } = run.binding
        if (run.grant) {
          const answer = await call(sessionId, { op: 'revoke-preview-session', ...original(run.grant) }, timeout())
          if (answer?.ok !== true || answer.revoked !== true || answer.grantId !== run.grant.id) throw fault('PROGRAM_CLEANUP_UNCONFIRMED')
          run.grant = undefined
        } else if (run.authorizationId) {
          const answer = await call(sessionId, { op: 'cancel-program-authorization', grant, ownerId, authorizationId: run.authorizationId }, timeout())
          if (answer?.ok !== true || answer.revoked !== true || answer.authorizationId !== run.authorizationId) throw fault('PROGRAM_CLEANUP_UNCONFIRMED')
        }
        run.authorizationId = undefined
      }
      // Do not delete/release the private cache until the last put has settled.
      const browser = async () => { await run.finished; await run.cache?.close(); run.cache = undefined }
      const results = await Promise.allSettled([Promise.resolve().then(host), browser()])
      run.cleaned = results.every(result => result.status === 'fulfilled')
      if (!run.cleaned) { run.status = 'error'; run.code = 'PROGRAM_CLEANUP_UNCONFIRMED' }
      run.cleanup = undefined
      this.changed(run)
      return run.cleaned
    })()
    this.changed(run)
    return run.cleanup
  }
}

export function usePreviewProgramRun({ opened, sessionId, project, tabSignal, call, suspended }) {
  const owner = opened?.previewSession, contentVersion = opened?.snapshot?.content_version
  const saved = opened?.snapshot?.document?.project, duration = saved?.duration_frames
  const clock = projectClock(saved?.fps), span = clock && Math.ceil(clock.num / clock.den)
  const openGrant = opened?.grant
  const eligible = supportsWindow(opened) && owner?.programRun?.version === 1 && owner?.version === 1 && owner.capability === 'preview-session' &&
    typeof owner.ownerId === 'string' && !!owner.ownerId && versionToken.test(contentVersion) && Number.isSafeInteger(duration) && duration > 0 && !!span &&
    typeof navigator !== 'undefined' && typeof navigator.locks?.request === 'function' && typeof globalThis.caches?.open === 'function' &&
    !!globalThis.crypto?.subtle && typeof crypto.randomUUID === 'function'
  const binding = useMemo(() => eligible && !suspended && openGrant && sessionId && project && tabSignal &&
    ({ ownerId: owner.ownerId, grant: openGrant, sessionId, project, tabSignal, call, contentVersion, duration, span }),
  [eligible, suspended, owner?.ownerId, openGrant, sessionId, project, tabSignal, call, contentVersion, duration, span])
  const [, redraw] = useReducer(value => value + 1, 0)
  const [controller] = useState(() => new PreviewProgramRun(redraw))
  useLayoutEffect(() => () => {
    if (binding && controller.run?.binding === binding) void controller.cancel()
  }, [binding, controller])
  if (!eligible) return undefined
  const current = controller.run, run = current?.binding === binding ? current : undefined
  return { start: frame => controller.start(binding, frame), cancel: () => controller.cancel(),
    read: (frame, signal) => controller.read(binding, frame, signal),
    recheck: (media, signal) => controller.recheck(binding, media, signal),
    canPlay: () => !!run?.grant && controller.run === run && !run.controller.signal.aborted && controller.now() < run.grant.expiresAt,
    signal: run?.controller.signal,
    status: run?.status ?? 'idle', completedFrames: run?.completedFrames ?? 0, totalFrames: duration, code: run?.code,
    frame: run?.frame, pendingWindow: run?.pendingWindow, cleanupPending: !!current?.cleanup, cleanupFailed: !!current && !current.cleaned && current.code === 'PROGRAM_CLEANUP_UNCONFIRMED' }
}
