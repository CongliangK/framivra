import { createDraftBridge } from './official-draft.mjs'

// An OWNED facade, not an addition to or patch of ctx.conversation. It consumes
// official session Slot InputActions and publicly exported controller methods.
// All live reads occur again at click/commit; no cached draft, automatic send,
// upload, DOM insertion, whole-draft replacement, or retry is permitted.
export function createOfficialDraftServices({ sessionId, getServices, getActions, getAllowed }) {
  let alive = true
  const targets = new WeakMap()
  const serviceTokens = new WeakMap()
  function facts() {
    const services = getServices?.()
    if (!alive || services?.active === false) throw new Error('plugin-inactive')
    const { conversation, sessions, uiSession } = services ?? {}
    const binding = sessions?.binding?.(sessionId)
    const navigation = uiSession?.adapter?.current?.getSnapshot()
    const summary = sessions?.list?.getSnapshot()?.byId?.[sessionId]
    if (!binding || binding.sessionId !== sessionId || !conversation || !navigation || typeof summary?.cwd !== 'string') throw new Error('session-unavailable')
    if (typeof conversation.input?.for !== 'function') throw new Error('input-unavailable')
    const input = conversation.input.for(binding.ctx).state.getSnapshot()
    const lifecycle = binding.session.getSnapshot()
    const actions = getActions?.()
    // Pin the actual conversation generation as well as the Session binding.
    let bindings = serviceTokens.get(conversation)
    if (!bindings) { bindings = new WeakMap(); serviceTokens.set(conversation, bindings) }
    let token = bindings.get(binding)
    if (!token) { token = Object.freeze({}); bindings.set(binding, token) }
    const limits = binding.session.projections.faceOf('imageLimits').getSnapshot()
    const attachments = conversation.resolveDraftAttachments(input.attachmentIds)
    if (!Array.isArray(attachments) || attachments.length !== input.attachmentIds.length) throw new Error('attachment-state-unavailable')
    const images = attachments.filter(item => item.kind === 'image')
    const bytes = images.reduce((sum, item) => sum + item.file.size, 0)
    if (!Number.isSafeInteger(bytes)) throw new Error('image-budget-unavailable')
    const imageLimits = limits && {
      maxBytes: limits.maxImageBytes,
      remainingImages: limits.maxImagesPerMessage - images.length,
      remainingBytes: limits.maxMessageImageBytes - bytes,
      mediaTypes: limits.mediaTypes,
    }
    return {
      services, binding, conversation,
      live: {
        active: navigation.key === sessionId && getAllowed?.() === true && lifecycle.removed === false && lifecycle.openState === 'open' && (!lifecycle.subagent || lifecycle.subagent.parentAvailable === true),
        session: { sessionId, binding: token, navigation: navigation.key, cwd: summary.cwd },
        input, actions, imageLimits,
      },
    }
  }
  const facade = {
    captureDraftTarget(binding) {
      const current = facts()
      if (current.binding !== binding) throw new Error('DRAFT_UNAVAILABLE: 会话绑定已变化')
      // Resource creation and release must stay on the SAME controller, even
      // if a synchronous subscriber replaces the live service during append.
      const owner = current.conversation
      const bridge = createDraftBridge({ readLive: () => facts().live, conversation: owner })
      const intent = bridge.captureIntent()
      if (!intent) throw new Error('DRAFT_UNAVAILABLE: 官方草稿状态尚未就绪')
      targets.set(intent, { owner, bridge })
      return intent
    },
    stageDraft({ target, text, files = [], signal }) {
      signal?.throwIfAborted()
      const captured = target && targets.get(target)
      if (!captured) return { status: 'refused', reason: 'target-invalid-or-used', atomicity: 'sequential' }
      targets.delete(target)
      const { owner, bridge } = captured
      if (facts().conversation !== owner) return { status: 'refused', reason: 'conversation-changed', atomicity: 'sequential' }
      if (!Array.isArray(files) || files.length > 1) return { status: 'refused', reason: 'one-png-only', atomicity: 'sequential' }
      const result = files.length ? bridge.appendTextWithImage(target, text, files[0]) : bridge.appendText(target, text)
      return result.status === 'appended' ? { ...result, status: 'applied' } : result
    },
  }
  return {
    getServices() {
      const services = getServices?.() ?? {}
      return { ...services, conversation: facade }
    },
    dispose() { alive = false },
  }
}

export function draftResultMessage(result, image = false) {
  if (result.status === 'applied') return result.atomicity === 'sequential'
    ? `已通过官方顺序接口加入引用${image ? '和一张 PNG' : ''}；保留原有草稿，未发送/上传。`
    : `已追加到点击时草稿${image ? '（文本和一张 PNG）' : ''}；未发送/上传。`
  if (result.status === 'partial') return `草稿可能已有部分变化（${result.detail ?? result.reason ?? '结果未知'}）。请查看原目标草稿和附件；不会自动补投或重试。`
  return `本次加入未完成：${result.status}/${result.reason ?? '已拒绝'}。已确认的补偿见回执；请核对目标后重新点击。`
}
