import { fail } from './protocol.mjs'

/** Exactly one RFC byte range. Unsupported/malformed ranges fail closed as 416. */
export function byteRange(header, size) {
  if (!Number.isSafeInteger(size) || size < 0) fail('BAD_MEDIA_SIZE', 'Media size is unavailable', 502)
  if (header === null) return { start: 0, end: size - 1, status: 200 }
  const match = /^bytes=(\d*)-(\d*)$/.exec(header)
  if (!match || size === 0 || (!match[1] && !match[2])) return undefined
  const a = match[1] ? Number(match[1]) : undefined
  const b = match[2] ? Number(match[2]) : undefined
  if ((a !== undefined && !Number.isSafeInteger(a)) || (b !== undefined && !Number.isSafeInteger(b))) return undefined
  const start = a === undefined ? Math.max(0, size - b) : a
  const end = a === undefined || b === undefined ? size - 1 : Math.min(size - 1, b)
  if (start >= size || end < start || (a === undefined && b === 0)) return undefined
  return { start, end, status: 206 }
}

/** Pull bounded windows via ctx.fs; no node:fs and no full-file allocation. */
export async function mediaResponse({ request, fs, validate, signal, release, chunkBytes = 64 * 1024 }) {
  try {
    const { target, info } = await validate(signal)
    const range = byteRange(request.headers.get('range'), info.size)
    const headers = new Headers({ 'Accept-Ranges': 'bytes', 'Content-Type': 'video/mp4', 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff' })
    if (!range) {
      headers.set('Content-Range', `bytes */${info.size}`)
      headers.set('Content-Length', '0')
      release()
      return new Response(null, { status: 416, headers })
    }
    const length = Math.max(0, range.end - range.start + 1)
    headers.set('Content-Length', String(length))
    if (range.status === 206) headers.set('Content-Range', `bytes ${range.start}-${range.end}/${info.size}`)
    if (request.method === 'HEAD' || length === 0) {
      release()
      return new Response(null, { status: range.status, headers })
    }
    let offset = range.start
    const body = new ReadableStream({
      async pull(controller) {
        try {
          signal.throwIfAborted()
          const current = await validate(signal)
          if (current.target.targetKey !== target.targetKey || current.info.version !== info.version) fail('MEDIA_CHANGED', 'Media changed; reopen preview', 409)
          const length = Math.min(chunkBytes, range.end - offset + 1)
          const bytes = await fs.readByteRange(current.target, { offset, length }, signal)
          signal.throwIfAborted()
          if (bytes.byteLength !== length) fail('MEDIA_CHANGED', 'Media changed during playback', 409)
          offset += bytes.byteLength
          controller.enqueue(bytes)
          if (offset > range.end) { controller.close(); release() }
        } catch (error) { controller.error(error); release() }
      },
      cancel() { release() },
    }, { highWaterMark: 0 })
    return new Response(body, { status: range.status, headers })
  } catch (error) { release(); throw error }
}
