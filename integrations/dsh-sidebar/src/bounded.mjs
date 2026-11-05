/** Feature-owned bounds, before text/JSON/base64 allocation. Shared by Host/browser. */
export const FRAME_WIRE_LIMIT = 8 * 1024 * 1024
export const FRAME_REQUEST_LIMIT = 16 * 1024
export async function boundedText(message, limit, signal) {
  const length = message.headers.get('content-length')
  if (length !== null && (!/^(0|[1-9][0-9]*)$/.test(length) || Number(length) > limit)) {
    await message.body?.cancel().catch(() => {})
    throw new Error('BODY_LIMIT: Invalid or oversized Content-Length')
  }
  signal?.throwIfAborted()
  if (!message.body) return ''
  const reader = message.body.getReader(), chunks = []
  let size = 0, complete = false
  const abort = () => { void reader.cancel(signal.reason).catch(() => {}) }
  signal?.addEventListener('abort', abort, { once: true })
  try {
    for (;;) {
      signal?.throwIfAborted()
      const { done, value } = await reader.read()
      signal?.throwIfAborted()
      if (done) { complete = true; break }
      if (!(value instanceof Uint8Array)) throw new Error('BODY_LIMIT: A byte stream is required')
      size += value.byteLength
      if (size > limit) throw new Error('BODY_LIMIT: Actual stream exceeds feature byte limit')
      chunks.push(value)
    }
    const bytes = new Uint8Array(size)
    let at = 0
    for (const chunk of chunks) { bytes.set(chunk, at); at += chunk.byteLength }
    return new TextDecoder('utf-8', { fatal: true }).decode(bytes)
  } finally {
    signal?.removeEventListener('abort', abort)
    if (!complete) await reader.cancel().catch(() => {})
    reader.releaseLock()
    chunks.length = 0
  }
}
