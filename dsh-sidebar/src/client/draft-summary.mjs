// Draft text is deliberately independent of diagnostic/raw JSON exports.
export const DRAFT_SUMMARY_LIMIT = 300
const controls = /[\p{Cc}\p{Cs}\p{Zl}\p{Zp}\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069]/u
const segments = new Intl.Segmenter(undefined, { granularity: 'grapheme' })

function clip(text, budget) {
  if (text.length <= budget) return text
  let result = ''
  for (const { segment } of segments.segment(text)) {
    if (result.length + segment.length + 1 > budget) break
    result += segment
  }
  return budget > 0 ? result + '…' : ''
}
function label(value) {
  return typeof value === 'string' ? value.replace(/[\p{Cc}\p{Cs}\p{Zl}\p{Zp}\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069`<>«»]/gu, ' ').replace(/\s+/gu, ' ').trim() : ''
}
function exact(value) {
  return typeof value === 'string' && value.length > 0 && !controls.test(value) && !/[`<>«»]/u.test(value)
}

/** Essential locators are never shortened: refuse an unrepresentable reference
 * rather than silently turning a clipped path/id/version into a different one.
 * Display metadata is optional, same-version, quoted data, never raw JSON. */
export function draftSummary({ timeline, contentVersion, selection, item, reference, frame, attached = false, positionOnly = false, selectionDisplay }) {
  if (!/^sha256:[a-f0-9]{64}$/.test(contentVersion)) throw new Error('SUMMARY_VERSION: 缺少精确版本')
  if (timeline !== undefined && (!exact(timeline) || /^(?:[\\/]|[a-z]:)/iu.test(timeline) || timeline.split(/[\\/]/u).includes('..'))) throw new Error('SUMMARY_TIMELINE: 需要无控制字符的工程相对时间线路径')
  const parts = [attached ? 'Framivra 当前完整合成帧 PNG' : 'Framivra 引用（未生成截图）', timeline === undefined ? 'timeline 未提供' : `timeline=«${timeline}»`, contentVersion]
  if (!positionOnly && item) {
    const locator = exact(item.stable_id) ? `id=«${item.stable_id}»` : exact(item.address) ? `address=«${item.address}»` : undefined
    if (!locator) throw new Error('SUMMARY_LOCATOR: 缺少可用对象定位')
    parts.push(`${selection?.kind === 'track' ? 'track' : 'block'} ${locator}`)
  } else if (attached) parts.push('position-only（不关联选区）')
  const at = frame ?? reference?.selection?.global_frame
  if (Number.isSafeInteger(at) && at >= 0) parts.push(`frame=${at}`)
  if (Array.isArray(item?.range) && item.range.length === 2 && item.range.every(n => Number.isSafeInteger(n) && n >= 0)) parts.push(`range=[${item.range[0]},${item.range[1]})`)
  if (attached) parts.push('Fast 媒体 stat；非 live-atomic；不是 final/音频 QC')
  const base = parts.join('；')
  if (base.length + 2 > DRAFT_SUMMARY_LIMIT) throw new Error('SUMMARY_LIMIT: 精确引用超过 300 字符；未截断定位、未加入草稿')
  const display = selectionDisplay?.content_version === contentVersion && (!selectionDisplay.address || selectionDisplay.address === item?.address) ? selectionDisplay : undefined
  const name = label(display?.name ?? display?.blockName ?? display?.trackName)
  const source = label(item?.source_id ?? display?.source_id ?? display?.source)
  const detail = [name && `名称 «${name}»`, source && `源 «${source}»`].filter(Boolean).join('；')
  const remaining = DRAFT_SUMMARY_LIMIT - base.length - 2
  return `\n${base}${detail && remaining > 2 ? '；' + clip(detail, remaining - 1) : ''}\n`
}
