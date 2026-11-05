import { stringifyJSON } from '../json.mjs'

const LIMIT = 8 * 1024 * 1024
export function referenceQuery(selection, contentVersion, frame) {
  if (!selection || !['track', 'block'].includes(selection.kind) || typeof selection.address !== 'string') throw new Error('NO_SELECTION: 请先选择轨道或块')
  return { [`${selection.kind}_addresses`]: [selection.address], expected_content_version: contentVersion, ...(frame === undefined ? {} : { frame }) }
}

export function selectedReference(envelope, selection, contentVersion) {
  if (envelope.content_version !== contentVersion) throw new Error('STALE_REFERENCE: 时间线版本变化，请刷新')
  const item = envelope.selection?.[`${selection.kind}s`]?.find(item => item.address === selection.address)
  if (!item) throw new Error('REFERENCE_MISSING: vtx 未返回选中对象；请刷新，不使用旧选择')
  return item
}

export function diagnosticText(envelope) {
  const text = stringifyJSON(envelope, 2)
  if (new TextEncoder().encode(text).byteLength > LIMIT) throw new Error('REFERENCE_LIMIT: 诊断文本超过 8 MiB；未截断、未复制、未加入草稿')
  return text
}

/** Called synchronously in the click handler, before any reference IO. No retain/create/fallback. */
export function captureCurrentDraft(getServices, sessionId) {
  const { conversation, sessions } = getServices?.() ?? {}
  if (typeof conversation?.captureDraftTarget !== 'function' || typeof conversation?.stageDraft !== 'function' || typeof sessions?.binding !== 'function') {
    throw new Error('DRAFT_UNAVAILABLE: 宿主未装配公开草稿 API；复制仍可用（需 DSH conversation-draft 候选宿主）')
  }
  const binding = sessions.binding(sessionId)
  if (!binding) throw new Error('DRAFT_UNAVAILABLE: 当前会话尚无已保留绑定；请先创建/打开会话，复制仍可用')
  return { conversation, target: conversation.captureDraftTarget(binding) }
}

/** Only stages append-only text. Never calls prompt/send or captures a replacement token. */
export async function performReferenceAction({ action, selection, contentVersion, frame, signal, read, clipboard, captured, onReference }) {
  signal.throwIfAborted()
  const envelope = await read(referenceQuery(selection, contentVersion, frame), signal)
  signal.throwIfAborted()
  const item = selectedReference(envelope, selection, contentVersion)
  onReference?.(envelope, item)
  if (action === 'read') return { status: 'ready' }
  if (action === 'draft') {
    const text = `\nFramivra 时间线引用（仅 stored reference；未生成截图，不是帧精确画面）：\n\`\`\`json\n${diagnosticText(envelope)}\n\`\`\`\n`
    signal.throwIfAborted()
    return captured.conversation.stageDraft({ target: captured.target, text, signal })
  }
  let text
  if (action === 'id') text = item.stable_id ?? `transient:${item.address}\ncontent_version:${envelope.content_version}`
  else if (action === 'raw') {
    if (typeof item.raw !== 'string') throw new Error('RAW_UNAVAILABLE: 此虚拟轨道没有存储 JSON 原文')
    text = item.raw // Exact vtx substring; never parse/stringify this string as a document.
  } else if (action === 'diagnostic') text = diagnosticText(envelope)
  else throw new Error('UNKNOWN_ACTION')
  if (typeof clipboard?.writeText !== 'function') throw new Error('CLIPBOARD_UNAVAILABLE: 剪贴板不可用；下方原文可手动复制')
  signal.throwIfAborted()
  await clipboard.writeText(text)
  return { status: 'copied' }
}
