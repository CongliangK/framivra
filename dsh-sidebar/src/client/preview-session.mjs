export function supportsWindow(opened) {
  const cap = opened?.capabilities?.capabilities?.preview_window
  return cap?.schema === 'framivra.timeline.preview-window/v1' && cap.inline_media === true && cap.prepare_inputs === true &&
    Array.isArray(opened.host?.ops) && opened.host.ops.includes('preview-window')
}

export function supportsWindowContinuation(opened) {
  const qualification = opened?.previewSession
  return supportsWindow(opened) && qualification?.capability === 'preview-session' && qualification.version === 1 && qualification.continuation?.version === 1
}

export function supportsWindowFirstFrame(opened) {
  const window = opened?.capabilities?.capabilities?.preview_window
  const session = opened?.previewSession
  return supportsWindow(opened) && window.first_frame?.version === 1 && session?.capability === 'preview-session' &&
    session.version === 1 && session.firstFrame?.version === 1
}

export function supportsWindowProgramAudio(opened) {
  const session = opened?.previewSession
  return supportsWindow(opened) && opened.capabilities.capabilities.preview_window.program_audio === true &&
    session?.capability === 'preview-session' && session.version === 1 && typeof session.ownerId === 'string' && !!session.ownerId
}

export function supportsWindowProgramAudioContinuation(opened) {
  return supportsWindowProgramAudio(opened) && supportsWindowContinuation(opened) &&
    opened.previewSession.programAudioContinuation?.version === 1 && opened.capabilities.capabilities.preview_window.input_epoch === true
}

export function matchesPreviewGrant(binding, ownerId, grant) {
  return typeof ownerId === 'string' && !!ownerId && grant?.ownerId === ownerId &&
    typeof grant.id === 'string' && !!grant.id && grant.id !== binding.grant && grant.openGrant === binding.grant &&
    grant.sessionId === binding.sessionId && !!binding.project && grant.project === binding.project
}

// Private adapter, not the manual-window protocol. The Host owns the canonical
// scope, deadline, budget, and worker; cleanup must outlive revocation.
function qualify(binding, adapter) {
  const { qualification, ownerId, grant, revoked, request, canDispatch, onFailure } = adapter ?? {}
  if (qualification?.capability !== 'preview-session' || qualification.version !== 1 || !matchesPreviewGrant(binding, ownerId, grant) ||
      !Number.isSafeInteger(grant.expiresAt) || revoked?.aborted !== false || typeof revoked.addEventListener !== 'function' ||
      binding.tabSignal.aborted || typeof request !== 'function') return
  const remaining = grant.expiresAt - Date.now()
  if (remaining <= 0 || remaining > 2_147_483_647) return
  // Cleanup always carries the identity captured when the window was started.
  const continuation = binding.continuationOwner === ownerId && qualification.continuation?.version === 1 && grant.continuation === true
  const firstFrame = binding.firstFrameOwner === ownerId && qualification.firstFrame?.version === 1 && grant.firstFrame === true &&
    grant.continuation !== true && grant.readWarmSvg !== true
  const useProgramAudio = grant.useProgramAudio === true
  if (Object.hasOwn(grant, 'useProgramAudio') && (!useProgramAudio || binding.programAudioOwner !== ownerId ||
      Object.hasOwn(grant, 'firstFrame') || (Object.hasOwn(grant, 'continuation') &&
        (!continuation || binding.programAudioContinuationOwner !== ownerId || qualification.programAudioContinuation?.version !== 1)))) return
  return { grant: Object.freeze({ ...grant }), revoked, request, remaining, canDispatch, onFailure, continuation, firstFrame, useProgramAudio }
}

export class PreviewSessionController {
  #scope
  #intent
  #queued = false
  #lastGrantId
  #heldGrant

  constructor(monitor) { this.monitor = monitor }

  connect(binding, adapter, frame) {
    const access = qualify(binding, adapter)
    if (!access) return
    const scope = { binding, adapter, ...access }
    const stop = () => {
      if (this.#scope !== scope) return
      this.#scope = undefined
      this.#intent = undefined
      clearTimeout(timer)
      access.revoked.removeEventListener('abort', stop)
      binding.tabSignal.removeEventListener('abort', stop)
      const { job, active } = this.monitor.state
      if (job?.automatic === scope || active?.automatic === scope) this.monitor.interrupt()
    }
    const timer = setTimeout(stop, access.remaining)
    this.#scope = scope
    this.#lastGrantId = scope.grant.id
    access.revoked.addEventListener('abort', stop, { once: true })
    binding.tabSignal.addEventListener('abort', stop, { once: true })
    if (this.#heldGrant !== scope.grant.id) this.pin(frame, false)
    return stop
  }

  // An explicit pin is new intent even while a previous worker is reaping.
  // Playback observations never come here (and never drive another seek).
  pin(frame, userPin = true) {
    const scope = this.#scope
    if (!scope) return false
    this.#heldGrant = undefined
    const { duration } = scope.binding
    const { job, active } = this.monitor.state
    this.#intent = undefined
    if (!Number.isSafeInteger(frame) || frame < 0 || frame >= duration) {
      this.monitor.interrupt()
      return true
    }
    if (active?.automatic === scope && this.monitor.owns(active) && active.ready && frame >= active.frame && frame < active.end) {
      this.monitor.seek(active, frame)
      return true
    }
    if (userPin && (this.monitor.retainPendingRender(scope, frame) || this.monitor.promoteSpareForPin(scope, frame))) return true
    // The legacy same-start shortcut must not swallow a pin back to the start
    // after a retargeted receipt arrived (or while its initial seek is pending).
    if ((active?.automatic === scope && this.monitor.owns(active) && !active.initialPin && frame === active.frame) ||
        (job?.automatic === scope && !job.previousOperationId && this.monitor.ownsJob(job) &&
          frame === job.frame && (job.pinFrame ?? job.frame) === job.frame)) return true
    this.#intent = { scope, frame }
    this.monitor.interrupt()
    this.drain()
    return true
  }

  discardIntent() { this.#intent = undefined }

  holdForEdit(operation) {
    // A real drag can detach the scope and reap its job before pointer-up.
    // Remember the qualified lease identity, not its authority, across that gap.
    const id = this.#scope?.grant.id ?? operation?.automatic?.grant.id ?? this.#lastGrantId
    if (id) { this.#heldGrant = id; this.discardIntent() }
  }
  resumeAfterEdit() { this.#heldGrant = undefined }

  isLive(scope) {
    return this.#scope === scope && !scope.revoked.aborted && !scope.binding.tabSignal.aborted && Date.now() < scope.grant.expiresAt
  }

  drain() {
    if (!this.#intent || this.#queued) return
    this.#queued = true
    queueMicrotask(() => {
      this.#queued = false
      const intent = this.#intent
      if (!intent || this.monitor.state.job || this.monitor.state.active) return
      this.#intent = undefined
      const { scope, frame } = intent
      if (!this.isLive(scope) || scope.canDispatch?.() === false) return
      void this.monitor.start({ ...scope.binding, frame, automatic: scope })
    })
  }
}
