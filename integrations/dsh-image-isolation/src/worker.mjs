/**
 * Isolated raster worker for dsh-image-isolation.
 *
 * Runs inside a dedicated child process from the provider's persistent pool
 * (one in-flight operation per worker; idle workers are reused).
 * Every operation delegates to the official @deepseek-ai/dsh-attachment-local
 * helpers, so admission, normalization, and request-image semantics stay
 * byte-identical to the stock provider; only the process boundary differs.
 * A native crash (e.g. a libvips SIGSEGV) kills this process alone.
 *
 * Wire protocol (fork IPC, advanced serialization):
 *   parent -> child : { id: number, op: string, payload: object }
 *   child  -> parent: { id, ok: true, result } | { id, ok: false, error: { code, message } }
 *
 * Safety rules:
 *  - Inputs are byte-checked before any decode; outputs are byte-checked
 *    before every send — an over-bound result is answered with a refusal
 *    envelope, never with the bytes.
 *  - Only AttachmentError facts (code + message) travel back on failure.
 *    Any other failure collapses to a generic envelope carrying the error
 *    name only: no stack text, no paths, no stderr content, no image bytes.
 */

import { isAttachmentError } from '@deepseek-ai/dsh-attachment'
import {
  prepareImageFile,
  readImageFile,
  readRequestImageFile,
  validateImageFile,
} from '@deepseek-ai/dsh-attachment-local'
import { MAX_TRANSFER_BYTES, auditWireBytes, checkTransferBounds } from './protocol.mjs'

if (process.send === undefined || process.channel === undefined) {
  // Refuse to run as anything but a forked worker: the parent only ever
  // launches this file through child_process.fork with an IPC channel.
  process.stderr.write('dsh-image-isolation worker: no IPC channel; refusing to start\n')
  process.exit(74)
}

// Self-terminate when the IPC channel disconnects (parent hard-exited while a
// native job was running): without this, a worker stuck inside a long libvips
// call would outlive its pool as an orphan. Exit forces the call to unwind at
// the next boundary the runtime can honor.
process.on('disconnect', () => {
  try { process.exit(75) } catch { /* already exiting */ }
})

/**
 * Convert an operation failure into the wire error envelope.
 * AttachmentError keeps its code and message; anything else collapses to a
 * safe generic message so native stack garbage, paths, or byte fragments
 * never leak back to the parent. Both fields are bounded: the parent
 * validates the envelope shape and kills this process on violation.
 */
function errorEnvelope(error) {
  if (isAttachmentError(error)) {
    return { code: error.code ?? 'ATTACHMENT_WRITE_FAILED', message: String(error.message).slice(0, 4096) }
  }
  return {
    code: 'ATTACHMENT_WRITE_FAILED',
    message: `Isolated image worker failed: ${error instanceof Error ? error.name : 'non-Error rejection'}`,
  }
}

/**
 * Send a reply only if it fits the transfer bound; otherwise refuse with facts only.
 * Every send is guarded: a broken/disconnected IPC channel (parent died or
 * hard-exited) must not turn the reply into an unhandled rejection here.
 */
function sendBounded(message) {
  let envelope = null
  const bytes = message.ok === true ? auditWireBytes(message.result ?? {}) : 0
  if (!Number.isFinite(bytes) || bytes < 0 || bytes > MAX_TRANSFER_BYTES) {
    envelope = {
      id: message.id,
      ok: false,
      error: {
        code: 'ATTACHMENT_WRITE_FAILED',
        message: `Isolated image worker result violates the ${MAX_TRANSFER_BYTES}-byte transfer bound; refusing to send.`,
      },
    }
  } else {
    envelope = message
  }
  try {
    if (process.channel !== null) process.send(envelope)
  } catch {
    /* channel closed: the disconnect handler is already exiting this process */
  }
}

const operations = {
  /** Full admission policy for one image, no storage writes. */
  validate: async ({ input, limits, policy }) => {
    checkTransferBounds('validate', input, ['data'])
    await validateImageFile(input, limits, policy)
    return {}
  },

  /** Decode + normalize one image; publication stays in the parent. */
  prepare: async ({ input, limits, policy }) => {
    checkTransferBounds('prepare', input, ['data'])
    const prepared = await prepareImageFile(input, limits, policy)
    return { data: Buffer.from(prepared.data), ref: prepared.ref }
  },

  /** Read and digest-verify one stored image (probe included). */
  readImage: async ({ root, ref }) => {
    const stored = await readImageFile(root, ref)
    return { ref: stored.ref, data: Buffer.from(stored.data) }
  },

  /** Deterministic request-image variant with the official on-disk cache. */
  requestImage: async ({ cacheRoot, stored, target }) => {
    checkTransferBounds('requestImage', stored, ['data'])
    const request = await readRequestImageFile(cacheRoot, stored, target)
    return {
      variantId: request.variantId,
      attachment: request.attachment,
      data: Buffer.from(request.data),
      mediaType: request.mediaType,
      bytes: request.bytes,
      width: request.width,
      height: request.height,
      depth: request.depth,
      space: request.space,
      hasAlpha: request.hasAlpha,
    }
  },
}

process.on('message', (message) => {
  if (message === null || typeof message !== 'object') return
  const { id, op, payload } = message
  if (!Number.isSafeInteger(id) || typeof op !== 'string' || payload === null || typeof payload !== 'object') return
  const handler = operations[op]
  if (handler === undefined) {
    sendBounded({ id, ok: false, error: { code: 'ATTACHMENT_WRITE_FAILED', message: `Unknown worker operation ${JSON.stringify(op.slice(0, 40))}.` } })
    return
  }
  handler(payload).then(
    (result) => { sendBounded({ id, ok: true, result }) },
    (error) => {
      try {
        sendBounded({ id, ok: false, error: errorEnvelope(error) })
      } catch {
        /* channel closed; parent observes exit */
      }
    },
  )
})
