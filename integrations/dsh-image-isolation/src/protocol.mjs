/**
 * Shared wire-protocol helpers for dsh-image-isolation, used by BOTH the
 * parent pool (src/pool.mjs) and the worker (src/worker.mjs) so the byte
 * budget, the audit algorithm, and the reply-validation rules can never
 * drift between the two sides.
 *
 * Design rules (fail-loud, never silently under-count):
 *  - Real buffers (ArrayBuffer views) count as their byteLength.
 *  - Plain objects exposing a numeric `byteLength` count as that declared
 *    value (tests use declarations to avoid allocating hundreds of MiB);
 *    negative or non-finite declarations are NaN → refused.
 *  - Strings count their UTF-8 size; nothing on the wire is free.
 *  - Containers deeper than MAX_WIRE_DEPTH or wider than MAX_OBJECT_KEYS,
 *    and any nonsense declaration, return NaN — callers must refuse.
 */

import { types } from 'node:util'

import { AttachmentError } from '@deepseek-ai/dsh-attachment'

/** Byte budget enforced parent-side before any IPC send (per field and per result). */
export const MAX_TRANSFER_BYTES = 256 * 1024 * 1024
/** Total declared bytes of queued (not yet started) jobs allowed at once. */
export const MAX_TOTAL_PENDING_BYTES = MAX_TRANSFER_BYTES
/** Wire objects are shallow protocol messages; anything deeper is refused. */
export const MAX_WIRE_DEPTH = 6
/** Per-object key budget: a wider object is treated as malformed, not free. */
export const MAX_OBJECT_KEYS = 64

/** Option caps shared by the pool constructor and the provider Config schema. */
export const MAX_POOL_CONCURRENCY = 8
export const MAX_WORKER_TIMEOUT_MS = 300_000
export const MAX_WORKER_MAX_QUEUE = 1024

/**
 * Sum the byte mass of a wire object. Returns NaN for nonsense (the caller
 * must refuse); over-budget totals are returned inflated so callers reject.
 */
export function auditWireBytes(node, depth = 0) {
  if (node === null || node === undefined) return 0
  const type = typeof node
  if (type === 'string') return Buffer.byteLength(node, 'utf8')
  if (type === 'number') return Number.isFinite(node) ? 8 : NaN
  if (type === 'boolean') return 1
  if (type !== 'object') return NaN
  if (ArrayBuffer.isView(node)) return node.byteLength
  // Fail closed on native containers whose payload hides outside own
  // enumerable keys (Map entries, Set members, RegExp source, Error message
  // and stack, boxed primitives, Date): the V8 structured serializer can
  // encode several of them, so they count as nonsense, never as zero.
  // Judged by internal slots via util.types — a Map with a swapped prototype
  // is still a Map, and a plain object wearing Map.prototype is not one.
  if (types.isMap(node) || types.isSet(node) || types.isWeakMap(node) || types.isWeakSet(node)
    || types.isRegExp(node) || types.isDate(node) || types.isNativeError(node) || types.isBoxedPrimitive(node)) {
    return NaN
  }
  let total = 0
  if (node.byteLength !== undefined) {
    const declared = node.byteLength
    if (typeof declared !== 'number' || !Number.isFinite(declared) || declared < 0) return NaN
    // A declaration is not a real typed view. Its siblings still count;
    // otherwise { byteLength: 0, data: hugeBuffer } bypasses the budget.
    total = declared
  }
  if (depth >= MAX_WIRE_DEPTH) return NaN
  let keys = 0
  for (const key in node) {
    if (!Object.hasOwn(node, key)) continue
    if (++keys > MAX_OBJECT_KEYS || Buffer.byteLength(key, 'utf8') > 256) return NaN
    if (key === 'byteLength') continue // the declaration was counted above
    const bytes = auditWireBytes(node[key], depth + 1)
    if (!Number.isFinite(bytes)) return NaN
    total += bytes
    if (!Number.isFinite(total) || total > MAX_TRANSFER_BYTES * 4) return total
  }
  return total
}

/**
 * Verify a payload's declared byte fields fit the transfer bound before forking work.
 * @param {string} op - operation name, for the refusal message.
 * @param {object} payload - the object about to be sent over IPC.
 * @param {string[]} fields - names of fields holding buffers or declared lengths.
 */
export function checkTransferBounds(op, payload, fields) {
  for (const field of fields) {
    const value = field.split('.').reduce((node, key) => (node === null || node === undefined ? undefined : node[key]), payload)
    if (value === undefined) continue
    const bytes = typeof value === 'object' && value !== null && value.byteLength !== undefined
      ? value.byteLength
      : Number(value)
    if (!Number.isFinite(bytes) || bytes < 0 || bytes > MAX_TRANSFER_BYTES) {
      throw new AttachmentError(
        `Isolated image operation ${op} refused: field ${field} exceeds the ${MAX_TRANSFER_BYTES}-byte transfer bound.`,
        'ATTACHMENT_WRITE_FAILED',
      )
    }
  }
}

/** Known AttachmentError code shape: UPPER_SNAKE, bounded. */
const CODE_PATTERN = /^[A-Z][A-Z0-9_]*$/

/**
 * Strict wire error envelope validation: both fields present, correctly
 * typed, bounded in length, and the code shaped like a real AttachmentError
 * code. Anything else is a malformed reply, not an error to surface.
 */
export function validateWireError(envelope) {
  return envelope !== null && typeof envelope === 'object'
    && typeof envelope.code === 'string' && envelope.code.length > 0 && envelope.code.length <= 64 && CODE_PATTERN.test(envelope.code)
    && typeof envelope.message === 'string' && envelope.message.length <= 4096
}

const isPositiveInteger = (value) => Number.isSafeInteger(value) && value > 0

/** Stored/prepared image reference shape: digest id, media type, dimensions, byte count. */
function isRefShape(ref) {
  return ref !== null && typeof ref === 'object'
    && typeof ref.attachmentId === 'string' && /^sha256:[a-f0-9]{64}$/.test(ref.attachmentId)
    && typeof ref.mediaType === 'string' && /^image\/[a-z0-9.+-]{1,96}$/.test(ref.mediaType)
    && isPositiveInteger(ref.bytes) && isPositiveInteger(ref.width) && isPositiveInteger(ref.height)
}

/**
 * Per-op result schema. A worker reply that does not match its operation's
 * shape is a protocol violation (the worker is killed), never a success.
 */
export function validateOpResult(op, result) {
  if (result === null || typeof result !== 'object' || Array.isArray(result)) return false
  const keys = Object.keys(result)
  switch (op) {
    case 'validate':
      return keys.length === 0
    case 'prepare':
    case 'readImage':
      return keys.length === 2 && result.data instanceof Uint8Array && isRefShape(result.ref)
        && result.ref.bytes === result.data.byteLength
    case 'requestImage': {
      if (keys.length !== 10) return false
      return result.data instanceof Uint8Array
        && typeof result.variantId === 'string' && result.variantId.length > 0 && result.variantId.length <= 256
        && isRefShape(result.attachment)
        && typeof result.mediaType === 'string' && result.mediaType.length > 0 && result.mediaType.length <= 128
        && isPositiveInteger(result.bytes) && result.bytes === result.data.byteLength
        && isPositiveInteger(result.width) && isPositiveInteger(result.height)
        && typeof result.depth === 'string' && result.depth.length > 0 && result.depth.length <= 32
        && typeof result.space === 'string' && result.space.length > 0 && result.space.length <= 64
        && typeof result.hasAlpha === 'boolean'
    }
    default:
      return false
  }
}
