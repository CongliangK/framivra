import { useCallback, useLayoutEffect, useMemo, useReducer, useRef } from 'react'
import { supportsWindow, supportsWindowContinuation, supportsWindowFirstFrame, supportsWindowProgramAudio, supportsWindowProgramAudioContinuation, matchesPreviewGrant } from './preview-session.mjs'

const terminal = new Set(['SESSION_EXPIRED', 'SESSION_LIMIT', 'BAD_SESSION_GRANT', 'CONFIG_CHANGED', 'BAD_GRANT', 'EXECUTABLE_CHANGED', 'PATH_CHANGED'])
export const previewSessionLimits = { minutes: 10, windows: 120 }
const pendingTimeout = () => AbortSignal.timeout(45_000)

// Separate from the manual checkbox path, including cancellation. The caller
// supplies the original grant and a cleanup signal that survives revocation.
function requestWindow(op, { grant, operationId, contentVersion, frame, end, firstFrame, previousOperationId, inputEpoch }, signal) {
  return fetch('/api/framivra/window', { method: 'POST', credentials: 'same-origin', cache: 'no-store', redirect: 'error',
    headers: { 'Content-Type': 'application/json' }, signal, body: JSON.stringify({
      op: `session-${op}`, sessionId: grant.sessionId,
      grant: grant.openGrant, previewGrant: grant.id, ownerId: grant.ownerId, operationId,
      ...(op !== 'cancel' ? { frame, contentVersion } : {}),
      ...(op === 'continue' ? { previousOperationId, inputEpoch } : {}),
      ...(op === 'render' && firstFrame ? { firstFrame: true } : {}),
      ...((op === 'render' || op === 'continue') && grant.useProgramAudio === true ? { useProgramAudio: true } : {}),
      ...(op === 'first-frame' ? { end, project: grant.project } : {}),
    }) })
}

// The workbench owns authorization, not the grant/version-keyed video. A stale
// view, edit or mode change detaches the monitor but does not renew the permit.
export function usePreviewSessionAccess({ opened, sessionId, project, tabSignal, call }) {
  const candidate = opened?.previewSession
  const ownerId = supportsWindow(opened) && candidate?.capability === 'preview-session' && candidate.version === 1 &&
    typeof candidate.ownerId === 'string' && candidate.ownerId ? candidate.ownerId : undefined
  const openGrant = opened?.grant, supportsContinuation = supportsWindowContinuation(opened)
  const supportsWarmSvg = !!ownerId && opened.capabilities.capabilities.preview_window.warm_svg_media === true
  const supportsFirstFrame = !!ownerId && supportsWindowFirstFrame(opened)
  const supportsProgramAudio = supportsWindowProgramAudio(opened)
  const supportsProgramAudioContinuation = supportsWindowProgramAudioContinuation(opened)
  const scope = useMemo(() => ownerId && openGrant && project ? { ownerId, grant: openGrant, sessionId, project, tabSignal } : undefined,
    [ownerId, openGrant, sessionId, project, tabSignal, supportsContinuation, supportsWarmSvg, supportsFirstFrame, supportsProgramAudio, supportsProgramAudioContinuation])
  const current = useRef()
  const [, redraw] = useReducer(value => value + 1, 0)

  const release = useCallback(async (record, reason, retire = false) => {
    if (!record) return
    record.retired ||= retire
    record.stopped = true
    record.controller?.abort()
    clearTimeout(record.timer)
    record.note = reason ?? record.note
    if (record.pending || record.releasing) return
    if (!record.grant) {
      if (current.current === record && record.retired) { current.current = undefined; redraw() }
      return
    }
    record.releasing = true
    redraw()
    try {
      const { id, ownerId, openGrant, sessionId } = record.grant
      const answer = await call(sessionId, { op: 'revoke-preview-session', grant: openGrant, previewGrant: id, ownerId }, pendingTimeout())
      if (answer?.ok !== true || answer.revoked !== true || answer.grantId !== id) throw new Error('Revocation not confirmed')
      record.grant = undefined
      record.failed = false
    } catch {
      record.failed = true
    } finally {
      record.releasing = false
      if (current.current === record) {
        if (record.retired) current.current = undefined
        redraw()
      }
    }
  }, [call])

  const stop = useCallback(() => { void release(current.current, undefined, true) }, [release])
  useLayoutEffect(() => {
    if (!scope) return
    const retire = () => { if (current.current?.scope === scope) stop() }
    tabSignal.addEventListener('abort', retire, { once: true })
    return () => { tabSignal.removeEventListener('abort', retire); retire() }
  }, [scope])

  async function authorize({ continuation = false, readWarmSvg = false, firstFrame = false, useProgramAudio = false } = {}) {
    const prior = current.current
    if (!scope || tabSignal.aborted || (continuation !== false && (continuation !== true || !supportsContinuation)) || (readWarmSvg && !supportsWarmSvg) ||
        (firstFrame && (!supportsFirstFrame || continuation || readWarmSvg)) ||
        (useProgramAudio !== false && (useProgramAudio !== true || !supportsProgramAudio || firstFrame ||
          (continuation && !supportsProgramAudioContinuation))) ||
        (prior && !prior.retired && (prior.pending || prior.grant || prior.releasing))) return
    const record = { scope, pending: true, used: 0 }
    current.current = record
    redraw()
    try {
      // Authorization may mint a grant after the view has gone away. Let the
      // bounded answer settle, then revoke that exact identity; never attach it.
      const answer = await call(sessionId, { op: 'authorize-preview-session', grant: openGrant, scope: 'current-project',
        renderConsent: 'cache/preview-only', prepareInputs: true, ...(continuation ? { continuation: true } : {}),
        ...(readWarmSvg ? { readWarmSvg: true } : {}), ...(firstFrame ? { firstFrame: true } : {}),
        ...(useProgramAudio ? { useProgramAudio: true } : {}) }, pendingTimeout())
      if (answer?.ok !== true || !matchesPreviewGrant(scope, ownerId, answer.grant)) throw new Error('Authorization identity mismatch')
      const grant = record.grant = Object.freeze({ ...answer.grant })
      record.pending = false
      const remaining = grant.expiresAt - Date.now()
      if (record.stopped || current.current !== record || tabSignal.aborted) return release(record, undefined, true)
      if (!Number.isSafeInteger(grant.expiresAt) || remaining <= 0 || remaining > previewSessionLimits.minutes * 60_000 ||
          !Number.isSafeInteger(grant.maxWindows) || grant.maxWindows < 1 || grant.maxWindows > previewSessionLimits.windows) {
        return release(record, '宿主未返回有效的限时、限次授权。')
      }
      // Only owner opt-in adds continuation to the adapter qualification;
      // an unsolicited Host flag must not stop ordinary single-window playback.
      if (continuation && grant.continuation !== true) return release(record, '宿主未确认连续播放的独立许可。')
      if (grant.readWarmSvg !== (readWarmSvg ? true : undefined)) return release(record, '宿主回执不匹配已经选择的暖 SVG 读取许可。')
      if (grant.firstFrame !== (firstFrame ? true : undefined) || (firstFrame && grant.continuation === true))
        return release(record, '宿主回执不匹配已经选择的临时首图许可。')
      if (grant.useProgramAudio !== (useProgramAudio ? true : undefined) ||
          (useProgramAudio && !continuation && Object.hasOwn(grant, 'continuation'))) {
        return release(record, '宿主回执不匹配已经选择的整片音频基准混音许可。')
      }
      record.controller = new AbortController()
      record.adapter = { qualification: { capability: 'preview-session', version: 1, ...(continuation ? { continuation: { version: 1 } } : {}),
        ...(useProgramAudio && continuation ? { programAudioContinuation: { version: 1 } } : {}),
        ...(firstFrame ? { firstFrame: { version: 1 } } : {}) },
        ownerId, grant, revoked: record.controller.signal,
        canDispatch: () => record.used < grant.maxWindows,
        onFailure: code => { if (terminal.has(code)) void release(record, '自动预览授权已结束；不会自动续期。') },
        request(op, binding, signal) {
          if (op === 'render' || op === 'continue') { record.used++; redraw() }
          return requestWindow(op, binding, signal)
        },
      }
      record.timer = setTimeout(() => { void release(record, '自动预览授权已到期；不会自动续期。') }, remaining)
    } catch {
      record.note = '宿主未确认自动预览授权。'
    } finally {
      record.pending = false
      if (current.current === record) redraw()
    }
  }

  const record = scope && current.current?.scope === scope ? current.current : undefined
  const adapter = record?.stopped ? undefined : record?.adapter
  return {
    stop, adapter,
    controls: scope ? { adapter, supportsContinuation, supportsWarmSvg, supportsFirstFrame, supportsProgramAudio, supportsProgramAudioContinuation, authorize,
      revoke: () => release(record, '已停止自动预览。'), pending: !!(record?.pending || record?.releasing),
      needsRevoke: !!record?.grant, failed: record?.failed, note: record?.note, used: record?.used } : undefined,
  }
}
