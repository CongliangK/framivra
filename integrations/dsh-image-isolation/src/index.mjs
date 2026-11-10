/**
 * dsh-image-isolation — a drop-in LocalAttachmentStore provider that executes
 * every image raster operation (admission validation, decode, normalization,
 * digest probing, request-image variants) inside dedicated child processes
 * drawn from a persistent pool, delegating to the official
 * @deepseek-ai/dsh-attachment-local helpers so semantics stay identical to
 * the stock provider.
 *
 * A native crash inside the image library (e.g. a libvips SIGSEGV) kills one
 * worker child; the failing operation reports a clean AttachmentError, other
 * workers and the DSH host process keep running, and the next operation
 * gets a fresh worker (idle workers are reused, crashed ones replaced).
 * Failed operations are never retried automatically.
 *
 * Storage publication (commit) stays in the parent process and re-verifies
 * the prepared digest via the official commitPreparedImageFile, so the
 * process boundary cannot corrupt integrity. Non-image file paths are
 * inherited untouched from the stock provider.
 */

import { dshCachePath, resolveDshHome } from '@deepseek-ai/dsh-home-paths'
import {
  DEFAULT_IMAGE_COMPRESSION_CONCURRENCY,
  DEFAULT_MAX_IMAGES_PER_MESSAGE,
  DEFAULT_MAX_IMAGE_BYTES,
  DEFAULT_MAX_IMAGE_DIMENSION,
  DEFAULT_MAX_IMAGE_PIXELS,
  DEFAULT_MAX_MESSAGE_IMAGE_BYTES,
  DEFAULT_NORMALIZED_IMAGE_MAX_BYTES,
  DEFAULT_NORMALIZED_IMAGE_MAX_DIMENSION,
  DEFAULT_NORMALIZED_IMAGE_MAX_PIXELS,
  MAX_IMAGE_COMPRESSION_CONCURRENCY,
  LocalAttachmentStore,
  commitPreparedImageFile,
  requestImageVariantId,
} from '@deepseek-ai/dsh-attachment-local'
import z from '@deepseek-ai/schemastery'
import { WorkerPool, checkTransferBounds } from './pool.mjs'
import { MAX_WORKER_MAX_QUEUE, MAX_WORKER_TIMEOUT_MS } from './protocol.mjs'

const DEFAULT_WORKER_TIMEOUT_MS = 30_000
const DEFAULT_WORKER_MAX_QUEUE = 64

/**
 * Isolated LocalAttachmentStore: same public surface and on-disk layout as
 * the official local provider, with all raster work moved to child processes.
 */
export class IsolatedAttachmentStore extends LocalAttachmentStore {
  static Config = z.object({
    dshHome: z.string(),
    maxImageBytes: z.number().step(1).min(1).default(DEFAULT_MAX_IMAGE_BYTES),
    maxImagesPerMessage: z.number().step(1).min(1).default(DEFAULT_MAX_IMAGES_PER_MESSAGE),
    maxMessageImageBytes: z.number().step(1).min(1).default(DEFAULT_MAX_MESSAGE_IMAGE_BYTES),
    maxImagePixels: z.number().step(1).min(1).default(DEFAULT_MAX_IMAGE_PIXELS),
    maxImageDimension: z.number().step(1).min(1).default(DEFAULT_MAX_IMAGE_DIMENSION),
    normalizedImageMaxPixels: z.number().step(1).min(1).default(DEFAULT_NORMALIZED_IMAGE_MAX_PIXELS),
    normalizedImageMaxDimension: z.number().step(1).min(1).default(DEFAULT_NORMALIZED_IMAGE_MAX_DIMENSION),
    normalizedImageMaxBytes: z.number().step(1).min(1).default(DEFAULT_NORMALIZED_IMAGE_MAX_BYTES),
    imageCompressionConcurrency: z.number().step(1).min(1).max(MAX_IMAGE_COMPRESSION_CONCURRENCY)
      .default(DEFAULT_IMAGE_COMPRESSION_CONCURRENCY),
    workerTimeoutMs: z.number().step(1).min(1_000).max(MAX_WORKER_TIMEOUT_MS).default(DEFAULT_WORKER_TIMEOUT_MS),
    workerMaxQueue: z.number().step(1).min(0).max(MAX_WORKER_MAX_QUEUE).default(DEFAULT_WORKER_MAX_QUEUE),
  })

  /**
   * @param {import('@deepseek-ai/cordis').Context} ctx - host context.
   * @param {z.infer<typeof IsolatedAttachmentStore.Config>} config - resolved provider configuration.
   */
  constructor(ctx, config) {
    super(ctx, config)
    this.cacheRoot = dshCachePath({ dshHome: resolveDshHome(config.dshHome) }, 'attachments')
    this.workerPool = new WorkerPool({
      workerUrl: './worker.mjs',
      concurrency: this.imageCompressionConcurrency,
      timeoutMs: config.workerTimeoutMs,
      maxQueue: config.workerMaxQueue,
      warn: (message) => { try { ctx.logger?.('attachments')?.warn?.(message) } catch { /* diagnostics only */ } },
    })
    // Cordis unloads fiber effects; it does not emit a 'dispose' event.
    // Return the pool promise so unload waits for every child's real close.
    ctx.effect(() => () => this.workerPool.dispose())
    /** In-flight shared request-image operations, mirroring the stock provider's dedupe. */
    this.requestInflight = new Map()
  }

  /** Run one worker op with parent-side transfer-bound enforcement. */
  _runIsolated(op, payload, boundFields = [], signal = undefined) {
    checkTransferBounds(op, payload, boundFields)
    return this.workerPool.run(op, payload, signal)
  }

  /** Admission validation: full official policy, executed in a worker. */
  async validateImage(input) {
    await this._runIsolated('validate', { input, limits: this.imageLimits, policy: this.normalizationPolicy }, ['input.data'])
  }

  /** Decode + normalize in a worker; publish in the parent with digest re-verification. */
  async saveImage(input) {
    const { data, ref } = await this._runIsolated('prepare', { input, limits: this.imageLimits, policy: this.normalizationPolicy }, ['input.data'])
    return commitPreparedImageFile(this.root, { data, ref })
  }

  /**
   * Batch save: validate every input, prepare every image, and commit only
   * when all preparations succeeded — no partial references on mid-batch
   * failure, matching the stock provider's all-or-nothing contract.
   */
  async saveImages(inputs) {
    await this.validateImageBatch(inputs)
    const prepared = await Promise.all(inputs.map((input) => this._runIsolated(
      'prepare',
      { input, limits: this.imageLimits, policy: this.normalizationPolicy },
      ['input.data'],
    )))
    const refs = []
    for (const { data, ref } of prepared) refs.push(await commitPreparedImageFile(this.root, { data, ref }))
    return refs
  }

  /** Read one stored image: the fs read, digest verification, and probe all happen in a worker. */
  async readImage(ref, signal) {
    const { ref: verifiedRef, data } = await this._runIsolated('readImage', { root: this.root, ref }, [], signal)
    return { ref: verifiedRef, data }
  }

  /**
   * Request-image variant with the official deterministic cache layout.
   * Concurrent requests for the same variant share one worker operation; one
   * waiter aborting cancels only when it is the last waiter.
   */
  async readImageRequest(ref, target, signal) {
    signal?.throwIfAborted()
    const variantId = requestImageVariantId(ref, target)
    let shared = this.requestInflight.get(variantId)
    if (shared !== undefined && shared.cancelled) {
      // A cancelled shared operation is dead: a new caller must never receive
      // the previous cancellation. Drop it and start a fresh operation
      // (mirrors the stock provider's requestVersion controller.signal.aborted check).
      this.requestInflight.delete(variantId)
      shared = undefined
    }
    if (shared === undefined) {
      shared = makeSharedRequest((abortSignal) => this._runIsolatedRequest(ref, target, abortSignal))
      this.requestInflight.set(variantId, shared)
      shared.finished.then(() => {
        if (this.requestInflight.get(variantId) === shared) this.requestInflight.delete(variantId)
      })
    }
    return shared.join(signal)
  }

  /** Read (or reuse cached) the requested variant in a worker; falls back to a worker-side image read. */
  async _runIsolatedRequest(ref, target, signal) {
    const stored = await this._runIsolated('readImage', { root: this.root, ref }, [], signal)
    return this._runIsolated('requestImage', { cacheRoot: this.cacheRoot, stored, target }, ['stored.data'], signal)
  }
}

/**
 * Minimal SharedRequest: many waiters, one operation, last-waiter-out cancels.
 * Mirrors the official provider's single-lifecycle semantics:
 *  - each waiter's abort rejects only that waiter and removes its listener;
 *  - when the last waiter leaves while the operation is still running, the
 *    underlying operation is cancelled;
 *  - once the operation has settled, late joiners get the stored outcome
 *    immediately (they never attach to an already-dead controller);
 *  - all abort listeners are removed on settle — nothing leaks or dangles.
 */
export function makeSharedRequest(run) {
  const controller = new AbortController()
  /** @type {Array<{ resolve: (value: any) => void, reject: (reason: Error) => void, signal?: AbortSignal, onAbort?: () => void }>} */
  const waiters = []
  let settled = false
  /** @type {{ ok: boolean, value?: any, error?: Error } | undefined} */ let outcome
  const finished = (async () => {
    try {
      const value = await run(controller.signal)
      outcome = { ok: true, value }
    } catch (error) {
      outcome = { ok: false, error }
    } finally {
      settled = true
      for (const waiter of waiters.splice(0)) {
        waiter.signal?.removeEventListener('abort', waiter.onAbort)
        settleWaiter(waiter, outcome)
      }
    }
  })()
  function settleWaiter(waiter, result) {
    if (result.ok) waiter.resolve(result.value)
    else waiter.reject(result.error)
  }
  return {
    finished,
    /** True once the last waiter cancelled the underlying operation. */
    get cancelled() { return controller.signal.aborted },
    join(signal) {
      if (signal?.aborted) return Promise.reject(abortReasonOf(signal))
      return new Promise((resolve, reject) => {
        const waiter = { resolve, reject, signal }
        waiter.onAbort = () => {
          // { once: true } already removed this listener from the caller's signal.
          const index = waiters.indexOf(waiter)
          if (index !== -1) waiters.splice(index, 1)
          if (waiters.length === 0 && !settled && !controller.signal.aborted) {
            // Last waiter out cancels the shared operation; other variants are unaffected.
            controller.abort(new Error('Image request cancelled by the last waiter.'))
          }
          reject(abortReasonOf(signal))
        }
        if (signal !== undefined) signal.addEventListener('abort', waiter.onAbort, { once: true })
        if (settled) {
          // Operation already finished: replay the outcome, never queue a dead waiter.
          if (signal !== undefined) signal.removeEventListener('abort', waiter.onAbort)
          settleWaiter(waiter, outcome)
          return
        }
        waiters.push(waiter)
      })
    },
  }
}

function abortReasonOf(signal) {
  return signal?.reason instanceof Error ? signal.reason : new Error('Image operation cancelled.')
}

export default IsolatedAttachmentStore
