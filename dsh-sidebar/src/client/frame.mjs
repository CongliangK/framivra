import React, { useEffect, useState } from 'react'
import { parseJSON, stringifyJSON } from '../json.mjs'
import { boundedText, FRAME_WIRE_LIMIT } from '../bounded.mjs'
import { captureCurrentDraft, referenceQuery, selectedReference } from './reference.mjs'
import { draftResultMessage } from './draft-services.mjs'
import { Icon } from './icons.mjs'
import { ToolButton, ToolSummary, iconControlCSS } from './icon-controls.mjs'

const h = React.createElement, rawSHA = /^[a-f0-9]{64}$/, versionSHA = /^sha256:[a-f0-9]{64}$/
const bad = message => { throw new Error(`FRAME_INVALID: ${message}`) }
const whole = value => Number.isSafeInteger(value) && value >= 0

/** Exact FPS identity between the certificate rational num/den and the raw core
 * token. Never Number(rawJSON): binary rounding would silently equate
 * 30.0000000000000000000001 with 30. Canonical safe-integer numbers (the only
 * numbers parseJSON keeps) compare by exact integer math; rawJSON tokens are
 * compared as exact decimals with tightly bounded digit/exponent ranges, so a
 * second JSON parser is not needed. Strings and lookalikes are rejected. */
export const FPS_TOKEN_LIMIT = 64, FPS_DIGIT_LIMIT = 40, FPS_EXPONENT_LIMIT = 60
const fpsTokenPattern = /^([+-]?)(0|[1-9][0-9]*)(?:\.([0-9]+))?(?:[eE]([+-]?[0-9]+))?$/
export function fpsMatchesReference(num, den, referenceFps) {
  if (!whole(num) || !whole(den) || num < 1 || den < 1) return false
  if (typeof referenceFps === 'number') return Number.isSafeInteger(referenceFps) && referenceFps >= 1 && num % den === 0 && num / den === referenceFps
  if (typeof JSON.isRawJSON !== 'function' || !JSON.isRawJSON(referenceFps)) return false
  const token = stringifyJSON(referenceFps)
  if (typeof token !== 'string' || token.length > FPS_TOKEN_LIMIT) return false
  const match = fpsTokenPattern.exec(token)
  if (!match) return false
  const [, sign, intPart, fracPart = '', expPart = '0'] = match
  const digits = intPart + fracPart
  if (digits.length > FPS_DIGIT_LIMIT) return false
  const exponent = Number(expPart) - fracPart.length
  if (!Number.isSafeInteger(exponent) || Math.abs(exponent) > FPS_EXPONENT_LIMIT) return false
  const magnitude = BigInt(digits)
  if (magnitude === 0n) return false
  const signedMagnitude = sign === '-' ? -magnitude : magnitude
  const numerator = BigInt(num), denominator = BigInt(den)
  // referenceFps = signedMagnitude * 10^exponent must equal num/den exactly.
  return exponent >= 0
    ? numerator === denominator * signedMagnitude * 10n ** BigInt(exponent)
    : numerator * 10n ** BigInt(-exponent) === denominator * signedMagnitude
}
export const FRAME_EDGE_LIMIT = 8192, FRAME_PIXEL_LIMIT = 16777216

/** Synchronous parent-owned invalidation; effects are cleanup, not the coherence boundary. */
export class FrameEpoch {
  epoch = 0
  generated = undefined
  edit = { dirty: false, busy: false, unknown: false }
  ghost = undefined
  // Only the originating gesture may release its exclusion. Never fold this
  // into the editor's dirty/busy/unknown report, which has a different lifetime.
  gestureChanged(token, active) {
    if (!active && this.ghost !== token) return false
    this.ghost = active ? token : undefined
    this.invalidate()
    return true
  }
  assertReady() {
    if (this.ghost || this.edit.dirty || this.edit.busy || this.edit.unknown) throw new Error('FRAME_STALE: selection/pin/edit/session changed or write unresolved')
  }
  invalidate() { this.epoch++; this.controller?.abort(); this.controller = undefined; this.generated = undefined }
  begin(tabSignal) {
    this.controller?.abort()
    this.controller = new AbortController()
    return { epoch: this.epoch, signal: AbortSignal.any([this.controller.signal, tabSignal]) }
  }
  check(operation) {
    operation.signal.throwIfAborted()
    this.assertReady()
    if (operation.epoch !== this.epoch) throw new Error('FRAME_STALE: selection/pin/edit/session changed or write unresolved')
  }
  // Semantically idempotent: read-only polls re-report identical edit flags,
  // and only a real dirty/busy/unknown transition may abort in-flight renders
  // or drop a verified PNG. Ghost and other owners are untouched here.
  editChanged(next) {
    const merged = { ...this.edit, ...next }
    const changed = merged.dirty !== this.edit.dirty || merged.busy !== this.edit.busy || merged.unknown !== this.edit.unknown
    this.edit = merged
    if (changed) this.invalidate()
    return changed
  }
}
export async function frameRequest(sessionId, payload, signal, fetcher = fetch) {
  const response = await fetcher('/api/framivra/frame', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...payload, sessionId }), signal })
  const value = parseJSON(await boundedText(response, FRAME_WIRE_LIMIT, signal))
  if (!response.ok || value?.ok === false) throw new Error(`${value?.error ?? 'FRAME_REFUSED'}: ${value?.message ?? stringifyJSON(value?.diagnostics ?? [])}`)
  return value
}

export async function decodePNG(blob, width, height, signal, browser = globalThis) {
  signal.throwIfAborted()
  if (typeof browser.Image !== 'function' || !browser.URL?.createObjectURL) throw new Error('FRAME_UNSUPPORTED: Browser full image decode unavailable')
  const url = browser.URL.createObjectURL(blob), image = new browser.Image()
  let abort
  try {
    const cancelled = new Promise((_, reject) => { abort = () => { image.src = ''; reject(signal.reason ?? new DOMException('Aborted', 'AbortError')) }; signal.addEventListener('abort', abort, { once: true }) })
    image.src = url
    await Promise.race([image.decode(), cancelled])
    signal.throwIfAborted()
    if (image.naturalWidth !== width || image.naturalHeight !== height) bad('Full decoded dimensions do not match certificate')
  } finally { signal.removeEventListener('abort', abort); image.src = ''; browser.URL.revokeObjectURL(url) }
}

/** No filesystem paths, image.path access, resizing, data URLs, or lossy reference conversion. */
export async function verifyFrame(envelope, { frame, contentVersion, signal, limits, crypto = globalThis.crypto, decode = decodePNG, File = globalThis.File }) {
  signal.throwIfAborted()
  const image = envelope?.image, reference = envelope?.reference
  if (envelope?.schema !== 'framivra.timeline.frame/v1' || envelope.operation !== 'frame' || envelope.ok !== true || envelope.content_version !== contentVersion || !versionSHA.test(contentVersion) || !Array.isArray(envelope.diagnostics)) bad('Frame envelope/version/operation')
  if (reference?.schema !== 'framivra.timeline.reference/v1' || reference.operation !== 'reference' || reference.ok !== true || reference.content_version !== contentVersion || reference.selection?.global_frame !== frame || !Array.isArray(reference.selection.tracks) || !Array.isArray(reference.selection.blocks) || typeof reference.document?.raw !== 'string' || reference.document.content_version !== contentVersion || !Array.isArray(reference.diagnostics) || typeof reference.coverage?.complete !== 'boolean') bad('Same saved core reference required')
  for (const item of [...reference.selection.tracks, ...reference.selection.blocks]) if (!item || typeof item.address !== 'string' || item.content_version !== contentVersion || !(item.stable_id === null || typeof item.stable_id === 'string') || !(item.raw === null || typeof item.raw === 'string')) bad('Malformed core reference selection')
  if (image?.state !== 'generated' || image.kind !== 'current-composite' || image.approximate !== false || image.mime_type !== 'image/png' || image.frame !== frame || image.content_version !== contentVersion || !whole(frame)) bad('Exact current-composite PNG certificate required')
  if (!whole(image.width) || !whole(image.height) || !image.width || !image.height || image.width > FRAME_EDGE_LIMIT || image.height > FRAME_EDGE_LIMIT || image.width * image.height > FRAME_PIXEL_LIMIT) bad('Decode dimensions exceed feature limits')
  if (!fpsMatchesReference(image.fps?.num, image.fps?.den, reference.selection?.fps)) bad('FPS does not match core reference')
  if (!rawSHA.test(image.sha256) || !rawSHA.test(image.render_key) || !rawSHA.test(image.input_fingerprint) || typeof image.identity_semantics !== 'string' || !image.identity_semantics.includes('Fast media stat identity') || !whole(image.bytes) || image.bytes < 33 || image.bytes > FRAME_WIRE_LIMIT) bad('Missing PNG identity/size certificates')
  if (limits && (!limits.mediaTypes?.includes('image/png') || image.bytes > limits.maxImageBytes || image.bytes > limits.maxMessageImageBytes || limits.maxImagesPerMessage < 1)) bad('DSH image policy rejects this frame')
  const inline = image.inline
  if (inline?.encoding !== 'base64' || typeof inline.data !== 'string' || inline.data.length !== 4 * Math.ceil(image.bytes / 3)) bad('Canonical bounded base64 required')
  // Constant-stack scan: repeated regex groups can overflow on valid in-budget PNGs.
  const dataEnd = inline.data.length - (3 - image.bytes % 3) % 3
  for (let at = 0; at < inline.data.length; at++) {
    const code = inline.data.charCodeAt(at)
    if (at < dataEnd ? !((code >= 65 && code <= 90) || (code >= 97 && code <= 122) || (code >= 48 && code <= 57) || code === 43 || code === 47) : code !== 61) bad('Canonical bounded base64 required')
  }
  const binary = atob(inline.data)
  if (binary.length !== image.bytes || btoa(binary) !== inline.data) bad('Noncanonical base64 or byte-count mismatch')
  const bytes = Uint8Array.from(binary, char => char.charCodeAt(0)), view = new DataView(bytes.buffer)
  if ([137,80,78,71,13,10,26,10].some((byte, at) => bytes[at] !== byte) || view.getUint32(8) !== 13 || String.fromCharCode(...bytes.subarray(12,16)) !== 'IHDR' || view.getUint32(16) !== image.width || view.getUint32(20) !== image.height) bad('PNG signature/IHDR mismatch')
  if (!crypto?.subtle) throw new Error('FRAME_UNSUPPORTED: WebCrypto SHA-256 unavailable')
  const digest = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)), byte => byte.toString(16).padStart(2, '0')).join('')
  const documentDigest = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(reference.document.raw))), byte => byte.toString(16).padStart(2, '0')).join('')
  signal.throwIfAborted()
  if (`sha256:${documentDigest}` !== contentVersion) bad('Exact raw document/version SHA-256 mismatch')
  if (digest !== image.sha256) bad('SHA-256 mismatch')
  if (typeof File !== 'function') throw new Error('FRAME_UNSUPPORTED: Browser File unavailable')
  const file = new File([bytes], `framivra-frame-${frame}-${digest.slice(0,16)}.png`, { type: 'image/png', lastModified: 0 })
  await decode(file, image.width, image.height, signal)
  signal.throwIfAborted()
  // Deliberately exclude inline/base64 and path from retained certificate/message.
  const certificate = Object.fromEntries(['state','kind','approximate','mime_type','width','height','frame','fps','content_version','input_fingerprint','identity_semantics','render_key','cache_hit','bytes','sha256'].map(key => [key, image[key]]))
  return { file, certificate, reference }
}
export function frameDraftText(generated, selection, positionOnly = false) {
  const reference = generated.reference
  let selected
  if (selection && !positionOnly) {
    const list = selection.kind === 'track' ? reference.selection.tracks : reference.selection.blocks
    selected = list.find(item => item.address === selection.address)
    if (!selected) throw new Error('FRAME_NOT_HIT: 选中对象不在该帧命中集合；明确选择仅位置引用或重新固定帧')
  }
  const payload = { certificate: generated.certificate, association: positionOnly || !selection ? 'position-only; no association to non-hit selection' : 'selected core frame hit', selected: selected ?? null, reference }
  const text = `\nFramivra 当前合成帧 + core 原文引用（不是 final/QC 证书）。Fast 媒体 stat 身份；证书仅对渲染时输入成立，加入草稿时不保证素材 live-atomic。全局整数帧由 core 给出；无自动发送。\n\`\`\`json\n${stringifyJSON(payload, 2)}\n\`\`\`\n`
  if (new TextEncoder().encode(text).byteLength > FRAME_WIRE_LIMIT) throw new Error('FRAME_REFERENCE_LIMIT: 引用超限，不截断、不加入草稿')
  return text
}

export function FrameTools({ owner, opened, sessionId, frame, selection, tabSignal, getDraftServices, call }) {
  const [state, setState] = useState({}), [consent, setConsent] = useState(false), [positionOnly, setPositionOnly] = useState(false)
  const version = opened.snapshot.content_version
  useEffect(() => () => owner.invalidate(), [owner])
  const generated = owner.generated
  function imageLimits(required = false) {
    const services = getDraftServices?.(), binding = services?.sessions?.binding?.(sessionId)
    const limits = binding?.session.projections?.faceOf('imageLimits').getSnapshot()
    if (required && !limits) throw new Error('DRAFT_UNAVAILABLE: DSH imageLimits 尚未就绪')
    return limits
  }
  async function generate() {
    let operation
    try {
      owner.assertReady()
      operation = owner.begin(tabSignal)
      owner.check(operation)
      if (!consent || !whole(frame)) throw new Error('FRAME_CONSENT: 请固定整数帧并明确允许仅 cache/preview 写入')
      owner.generated = undefined
      setState({ busy: true, message: '正在显式生成当前合成 PNG…' })
      const envelope = await frameRequest(sessionId, { op: 'render', grant: opened.grant, frame, contentVersion: version, renderConsent: 'cache/preview-only', operationId: crypto.randomUUID() }, operation.signal)
      const verified = await verifyFrame(envelope, { frame, contentVersion: version, signal: operation.signal, limits: imageLimits() })
      owner.check(operation)
      // Validate association before making either stage/copy available.
      frameDraftText(verified, selection, positionOnly)
      owner.generated = verified
      setState({ message: '已验证 PNG，未发送。复制与入稿请展开图片工具。' })
    } catch (error) { if (!operation?.signal.aborted) setState({ message: error.message }); else setState({ message: '操作已失效/取消；未加入草稿。' }) }
  }
  async function stage() {
    // Guard even retained handlers before capture; clean clicks still capture
    // synchronously before begin and before the first reference await.
    let captured, operation
    try {
      owner.assertReady()
      captured = captureCurrentDraft(getDraftServices, sessionId)
      operation = owner.begin(tabSignal)
      owner.check(operation)
      const current = owner.generated
      if (!current || current.certificate.frame !== frame || current.certificate.content_version !== version) throw new Error('FRAME_STALE: 请重新生成当前固定帧')
      imageLimits(true)
      setState({ busy: true, message: '核对保存版本后追加到点击时草稿…' })
      const fresh = await call(sessionId, { op: 'reference', grant: opened.grant, query: selection && !positionOnly ? referenceQuery(selection, version, frame) : { expected_content_version: version, frame } }, operation.signal)
      if (fresh.ok !== true || fresh.content_version !== version || fresh.selection?.global_frame !== frame) throw new Error('STALE_REFERENCE: 保存文档已变化')
      if (selection && !positionOnly) selectedReference(fresh, selection, version)
      owner.check(operation)
      const text = frameDraftText(current, selection, positionOnly)
      const result = await captured.conversation.stageDraft({ target: captured.target, text, files: [current.file], signal: operation.signal })
      // Do not turn an already-observed append/partial result into a false
      // "unchanged" claim when a subscriber invalidates the view afterwards.
      setState({ message: draftResultMessage(result, true) })
    } catch (error) { setState({ message: operation?.signal.aborted ? '操作已失效/取消；未补投草稿。' : error.message }) }
  }
  async function copy() {
    let operation
    try {
      owner.assertReady()
      operation = owner.begin(tabSignal)
      owner.check(operation)
      const current = owner.generated
      if (!current || !navigator.clipboard?.write || typeof ClipboardItem !== 'function' || (ClipboardItem.supports && !ClipboardItem.supports('image/png'))) throw new Error('CLIPBOARD_UNSUPPORTED: 当前浏览器不能写 PNG；未自动降级为文本')
      // No await before clipboard.write: this is the second explicit user gesture.
      await navigator.clipboard.write([new ClipboardItem({ 'image/png': current.file })])
      setState({ message: operation.signal.aborted ? '剪贴板写入可能已完成；OS 无法撤回，当前选择已变化。' : 'PNG 已复制；OS 可能重新编码，不承诺剪贴板字节恒等。' })
    } catch (error) { setState({ message: `图片未确认复制：${error.message}；无自动文本降级。` }) }
  }
  const blocked = !whole(frame) || !!owner.ghost || owner.edit.dirty || owner.edit.busy || owner.edit.unknown
  // Verified preview object URL: created only from the verified owner.generated.file
  // (never image.path or unverified bytes), revoked on replacement/invalidation/unmount.
  // jsdom has no URL.createObjectURL; the guard keeps tests and non-browser hosts clean.
  const verifiedFile = generated?.file
  // Preview state carries the File it was created from: after owner.invalidate()
  // the render itself (not just the effect tick) must stop showing the stale URL.
  const [preview, setPreview] = useState()
  useEffect(() => {
    if (!verifiedFile || typeof URL?.createObjectURL !== 'function') return
    const url = URL.createObjectURL(verifiedFile)
    setPreview({ url, file: verifiedFile })
    return () => { URL.revokeObjectURL(url); setPreview(undefined) }
  }, [verifiedFile])
  const shownPreview = generated && preview && preview.file === generated.file ? preview.url : null
  const cancel = () => { owner.invalidate(); setState({ busy: true, message: '取消并等待实际进程退出…' }); void frameRequest(sessionId, { op: 'cancel', grant: opened.grant }, tabSignal).then(() => setState({ message: '已回收；未自动重试。' }), error => setState({ message: error.message })) }
  const cancelButton = () => h(ToolButton, { icon: 'cancel', variant: 'danger', label: '显式取消帧并等待回收', tooltip: '取消当前帧操作并等待实际进程回收', onClick: cancel })
  const imageTools = h('details', { className: 'fv-frame-tools-details' },
    h(ToolSummary, { icon: 'more', label: '图片与草稿 · 验证详情', tooltip: '展开图片复制 / 入稿与验证详情' }),
    h('div', { className: 'fv-frame-tools-tools' },
      h(ToolButton, { icon: 'copy', label: '复制已生成 PNG（再次点击）', tooltip: '把已验证的当前合成 PNG 写入剪贴板（再次点击确认）', disabled: !generated || blocked || state.busy, onClick: () => void copy() }),
      h(ToolButton, { icon: 'draft', label: '将该帧 + 精确引用加入当前草稿（不发送）', tooltip: '把已验证帧与精确 core 引用追加到当前草稿，不发送', disabled: !generated || blocked || state.busy, onClick: () => void stage() }),
      !state.busy ? cancelButton() : null),
    h('label', null, h('input', { type: 'checkbox', checked: positionOnly, onChange: event => { owner.invalidate(); setPositionOnly(event.target.checked); setState({}) } }), '仅位置引用，不关联未命中选区'),
    h('p', null, '生成前须单独允许本次 cache/preview 写入；不授予 timeline/history 写权限。图片和引用仅加入草稿，不发送。'),
    h('p', null, `保存版本 ${version} · 固定全局帧 ${frame ?? '未设置'}。current-composite / Fast 身份，不是成片 QC。不会准备缺失输入；只接受 core inline PNG，绝不读取 image.path。`),
    generated ? h('pre', null, stringifyJSON(generated.certificate, 2)) : null)
  return h('section', { 'aria-label': '当前合成帧与草稿', className: 'fv-frame-tools', 'aria-busy': state.busy ? 'true' : 'false' },
    h('style', null, `
.fv-frame-tools{min-width:0;max-width:100%;font:inherit;color:inherit}.fv-frame-tools *{box-sizing:border-box}.fv-frame-tools button,.fv-frame-tools input{font:inherit}
.fv-frame-tools .fv-frame-tools-head{display:flex;flex-wrap:wrap;gap:4px 10px;align-items:baseline;margin:0 0 6px}
.fv-frame-tools h3{margin:0;font-size:13px;font-weight:600}
.fv-frame-tools .fv-frame-tools-meta{font-size:11px;font-variant-numeric:tabular-nums;color:color-mix(in srgb,currentColor 62%,transparent);overflow-wrap:anywhere}
.fv-frame-tools .fv-frame-tools-stage{position:relative;width:100%;aspect-ratio:16/9;max-height:320px;margin:0 0 8px;background:#000;border-radius:6px;display:flex;align-items:center;justify-content:center;overflow:hidden}
.fv-frame-tools .fv-frame-tools-stage img{width:100%;height:100%;max-width:100%;max-height:100%;object-fit:contain;display:block;user-select:none}
.fv-frame-tools .fv-frame-tools-empty{display:flex;flex-direction:column;align-items:center;gap:12px;font-size:12px;color:#b1b8c4;text-align:center;padding:16px 12px}
.fv-frame-tools .fv-frame-tools-empty svg{opacity:.65}
.fv-frame-tools .fv-frame-tools-check{display:flex;flex-wrap:wrap;gap:2px 14px;margin:0 0 6px;font-size:12px}
.fv-frame-tools .fv-frame-tools-tools{display:flex;flex-wrap:wrap;gap:6px;margin:0 0 6px}
.fv-frame-tools button{border:1px solid color-mix(in srgb,currentColor 45%,transparent);border-radius:5px;background:color-mix(in srgb,currentColor 6%,transparent);padding:4px 10px;font-size:12px;cursor:pointer}
.fv-frame-tools button:disabled{opacity:.45;cursor:default}
.fv-frame-tools :focus-visible{outline:2px solid var(--fv-tl-accent,#579deb);outline-offset:2px}
.fv-frame-tools [role=alert],.fv-frame-tools [role=status]{font-size:12px;margin:4px 0}
.fv-frame-tools details{margin:4px 0;font-size:12px}
.fv-frame-tools details summary{cursor:pointer;color:color-mix(in srgb,currentColor 68%,transparent)}
.fv-frame-tools details p,.fv-frame-tools details pre{margin:6px 0}
.fv-frame-tools pre{white-space:pre-wrap;overflow-wrap:anywhere}
` + iconControlCSS),
    h('div', { className: 'fv-frame-tools-head' },
      h('h3', null, '当前合成帧'),
      h('span', { className: 'fv-frame-tools-meta', title: version }, `版本 ${version.slice(7, 19)} · 帧 ${whole(frame) ? frame : '未固定'}`)),
    h('div', { className: 'fv-frame-tools-stage' },
      shownPreview
        ? h('img', { src: shownPreview, width: generated.certificate.width, height: generated.certificate.height, alt: `已验证合成帧 ${generated.certificate.frame}（${generated.certificate.width}×${generated.certificate.height} PNG）` })
        : h('div', { className: 'fv-frame-tools-empty' }, h(Icon, { name: state.busy ? 'refresh' : 'film', size: 32 }), h('span', null, state.busy ? '正在生成并验证…' : whole(frame) ? '未生成画面（需显式生成）' : '未固定帧'))),
    h('div', { className: 'fv-frame-tools-tools' },
      h('label', { title: '单独允许本次点击向此工程 cache/preview 写入；不授予 timeline/history 写权限。' }, h('input', { type: 'checkbox', checked: consent, onChange: event => { owner.invalidate(); setConsent(event.target.checked); setState({}) } }), '允许写入预览缓存（不改时间线）'),
      h(ToolButton, { icon: 'camera', variant: 'primary', label: '显式生成并验证固定帧 PNG', tooltip: '显式生成当前固定帧合成 PNG 并逐项验证证书', disabled: !consent || blocked || state.busy, onClick: () => void generate() }),
      state.busy ? cancelButton() : null,
      imageTools),
    blocked ? h('p', { role: 'alert' }, '当前未固定帧，或编辑/队列/计划/写结果未解决；拒绝生成和加入草稿。') : null,
    state.message ? h('p', { role: 'status' }, state.message) : null)
}
