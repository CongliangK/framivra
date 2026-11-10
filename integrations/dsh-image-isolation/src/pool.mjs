/**
 * Bounded pool of isolated raster workers for dsh-image-isolation.
 *
 * Lifecycle is a strict per-worker state machine:
 *
 *   spawn ──▶ busy ──▶ idle ──▶ busy ──▶ …
 *              │         │
 *              ▼         ▼
 *            closing ◀── (timeout | abort | send failure | dispose |
 *              │          protocol violation)
 *              ▼
 *            closed   (terminal; only the child's real 'close' event may enter it)
 *
 * Invariants:
 *  - A worker record leaves the registry ONLY in `closed`, which is entered
 *    exclusively from the child's real 'close' event (fires after 'exit',
 *    or after a spawn 'error'). Capacity is released only then; a killed
 *    worker keeps its slot until the exit is actually observed, so dispose
 *    can never claim success while a cancelled child is un-reaped.
 *  - A job settles exactly once: on the worker's reply (validated), or when
 *    its worker reaches `closed` (with the pending reason: timeout, abort,
 *    dispose, or the observed exit/signal facts). Failed jobs are never
 *    retried; the next job may spawn a fresh worker.
 *  - Both directions of the wire are byte-bounded: per-field input bounds
 *    before any IPC send, a total pending-queue byte budget, and a full
 *    audit of result messages (shape, id, and summed buffer bytes) before
 *    a job is resolved. A worker that answers out of bounds is killed.
 *  - Error surfaces carry only AttachmentError codes/messages or safe facts
 *    (op name, worker id, exit code / signal name, byte counts). Raw pipe
 *    output, file paths, and image bytes never enter errors or logs; worker
 *    pipes are drained and discarded.
 */

import { fork } from 'node:child_process'
import { dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { AttachmentError } from '@deepseek-ai/dsh-attachment'
import {
  MAX_POOL_CONCURRENCY,
  MAX_TOTAL_PENDING_BYTES,
  MAX_TRANSFER_BYTES,
  MAX_WORKER_MAX_QUEUE,
  MAX_WORKER_TIMEOUT_MS,
  auditWireBytes,
  checkTransferBounds,
  validateOpResult,
  validateWireError,
} from './protocol.mjs'

export { MAX_TOTAL_PENDING_BYTES, MAX_TRANSFER_BYTES, auditWireBytes, checkTransferBounds }

function assertOption(value, name, { min = 0, max = Number.MAX_SAFE_INTEGER, allowZero = true } = {}) {
  if (!Number.isInteger(value) || value < min || value > max || (!allowZero && value === 0)) {
    throw new TypeError(`WorkerPool option ${name} must be an integer in [${min}, ${max}] (got ${String(value)})`)
  }
}

/** Safe fact for logs and refusals: an error's class name, nothing else. */
function safeErrorName(error) {
  return error instanceof Error ? error.name : 'NonError'
}

/** Safe shared refusal error. */
function attachmentRefusal(message) {
  return new AttachmentError(message, 'ATTACHMENT_WRITE_FAILED')
}

export class WorkerPool {
  /**
   * @param {object} options
   * @param {string} options.workerUrl - URL of worker.mjs, resolved against this module.
   * @param {number} options.concurrency - maximum simultaneous workers (one job each).
   * @param {number} options.timeoutMs - per-job deadline before the worker is SIGKILLed (ms, >= 1).
   * @param {number} options.maxQueue - queued jobs allowed while all workers are busy.
   * @param {(message: string) => void} [options.warn] - optional diagnostics sink; its
   *   failures are swallowed and never affect lifecycle.
   */
  constructor({ workerUrl, concurrency, timeoutMs, maxQueue, warn }) {
    if (typeof workerUrl !== 'string' || workerUrl === '') throw new TypeError('WorkerPool option workerUrl must be a non-empty string')
    assertOption(concurrency, 'concurrency', { min: 1, max: MAX_POOL_CONCURRENCY })
    assertOption(timeoutMs, 'timeoutMs', { min: 1, max: MAX_WORKER_TIMEOUT_MS })
    assertOption(maxQueue, 'maxQueue', { min: 0, max: MAX_WORKER_MAX_QUEUE })
    if (warn !== undefined && typeof warn !== 'function') throw new TypeError('WorkerPool option warn must be a function when provided')
    this.workerPath = fileURLToPath(new URL(workerUrl, import.meta.url).href)
    this.concurrency = concurrency
    this.timeoutMs = timeoutMs
    this.maxQueue = maxQueue
    this.#warnFn = warn ?? (() => {})
    /** @type {Set<import('./pool.mjs').WorkerRecord>} every live record, removed only in `closed`. */
    this.workers = new Set()
    /** Records currently running a job. */
    this.active = []
    /** Records with no job, available for immediate reuse. */
    this.idle = []
    /** @type {Array<QueueEntry>} jobs waiting for a slot; declared bytes summed in #pendingBytes. */
    this.queue = []
    this.pendingBytes = 0
    this.disposed = false
    /** @type {Promise<void> | undefined} */ this.disposePromise = undefined
    this.disposeResolve = undefined
  }

  #warnFn

  /** Bounded safe diagnostics; a throwing sink must never disturb lifecycle. */
  #warn(message) {
    try { this.#warnFn(message) } catch { /* diagnostics only */ }
  }

  /** Minimal worker environment: nothing inherited except locale/temp basics. */
  #workerEnv() {
    const env = { DSH_IMAGE_ISOLATION_WORKER: '1' }
    for (const key of ['TMPDIR', 'TZ', 'LANG', 'LC_ALL']) {
      const value = process.env[key]
      if (typeof value === 'string' && value !== '') env[key] = value
    }
    return env
  }

  /** Slots occupied: busy plus closing records (a killed worker holds capacity until closed). */
  #usedSlots() {
    let used = this.active.length
    for (const record of this.workers) if (record.state === 'closing') used += 1
    return used
  }

  #spawn() {
    const child = fork(this.workerPath, [], {
      execPath: process.execPath,
      execArgv: [],
      serialization: 'advanced',
      env: this.#workerEnv(),
      cwd: dirname(this.workerPath),
      stdio: ['ignore', 'pipe', 'pipe', 'ipc'],
    })
    // Pipes are drained and discarded: bounded, never logged, never surfaced.
    child.stdout?.resume()
    child.stderr?.resume()
    /** @type {WorkerRecord} */
    const record = {
      child,
      recordId: ++this.#recordSeq,
      state: 'busy',
      job: undefined,
      pendingReason: undefined,
      exitCode: undefined,
      exitSignal: undefined,
      exited: new Promise((resolve) => { child.once('close', () => resolve()) }),
    }
    this.workers.add(record)
    child.on('message', (message) => this.#onMessage(record, message))
    child.on('exit', (code, signal) => {
      // Facts only, captured for the close-time diagnostics; nothing settles here.
      record.exitCode = code
      record.exitSignal = signal
    })
    child.on('close', () => this.#onClosed(record))
    child.on('error', (error) => {
      // An 'error' event (spawn/send/kill failure) is NOT an exit: record the
      // safe fact, start a close, and let only the real 'close' event settle
      // the record and release capacity. A still-alive child is killed here;
      // one that never reaped keeps its slot — dispose never fakes success.
      this.#warn(`dsh-image-isolation: worker ${record.recordId} channel error (${safeErrorName(error)}); killing it`)
      this.#beginClose(record, attachmentRefusal(
        `Isolated image worker ${record.recordId} failed (${safeErrorName(error)}) during an operation; the worker was killed without retry.`,
      ))
    })
    return record
  }

  #recordSeq = 0

  /**
   * Validate and apply a worker reply. Only a busy worker with a matching job
   * may settle it; anything else is either ignored (stale noise) or treated as
   * a protocol violation, which kills the worker.
   */
  #onMessage(record, message) {
    if (record.state !== 'busy') return
    const job = record.job
    if (job === undefined) return
    if (message === null || typeof message !== 'object' || Array.isArray(message)) return
    if (message.id !== job.id) return
    if (message.ok !== true && message.ok !== false) {
      this.#beginClose(record, attachmentRefusal(`Isolated image worker ${record.recordId} sent a malformed reply for ${job.description}; the worker was killed.`))
      return
    }
    if (message.ok === false) {
      const envelope = message.error
      if (!validateWireError(envelope)) {
        this.#beginClose(record, attachmentRefusal(`Isolated image worker ${record.recordId} sent a malformed error envelope for ${job.description}; the worker was killed.`))
        return
      }
      this.#settleJob(job, 'reject', wireError(envelope, job))
      record.job = undefined
      this.#releaseToIdle(record)
      return
    }
    const result = message.result
    const bytes = auditWireBytes(result)
    if (!Number.isFinite(bytes) || bytes < 0 || bytes > MAX_TRANSFER_BYTES) {
      this.#beginClose(record, attachmentRefusal(`Isolated image worker ${record.recordId} replied to ${job.description} with more than the ${MAX_TRANSFER_BYTES}-byte transfer bound; the worker was killed.`))
      return
    }
    if (!validateOpResult(job.op, result)) {
      this.#beginClose(record, attachmentRefusal(`Isolated image worker ${record.recordId} replied to ${job.description} with a result that violates the ${job.op} schema; the worker was killed.`))
      return
    }
    this.#settleJob(job, 'resolve', result)
    record.job = undefined
    this.#releaseToIdle(record)
  }

  /** A worker finished a job normally: back to idle, slot reusable. */
  #releaseToIdle(record) {
    if (record.state !== 'busy') return
    record.state = 'idle'
    this.active = this.active.filter((entry) => entry !== record)
    this.idle.push(record)
    this.#pump()
  }

  /**
   * Start tearing a worker down. The record keeps occupying capacity and stays
   * in the registry until its real 'close' event arrives (#onClosed); the job
   * is settled there with `reason`.
   */
  #beginClose(record, reason) {
    if (record.state === 'closing' || record.state === 'closed') return
    if (record.state === 'busy') {
      record.pendingReason = reason
      this.active = this.active.filter((entry) => entry !== record)
    }
    record.state = 'closing'
    this.idle = this.idle.filter((entry) => entry !== record)
    try { record.child.kill('SIGKILL') } catch { /* 'close' still arrives */ }
    this.#pump()
  }

  /** Terminal transition, entered only from the child's real close/error event. */
  #onClosed(record) {
    if (record.state === 'closed') return
    record.state = 'closed'
    this.workers.delete(record)
    this.idle = this.idle.filter((entry) => entry !== record)
    this.active = this.active.filter((entry) => entry !== record)
    const job = record.job
    record.job = undefined
    if (job !== undefined) {
      const reason = record.pendingReason ?? workerDeathError(record, job)
      this.#settleJob(job, 'reject', reason)
    }
    this.#pump()
    this.#maybeFinishDisposal()
  }

  /** Idempotent job settle: clears the deadline and abort wiring exactly once. */
  #settleJob(job, kind, value) {
    if (job.settled) return
    job.settled = true
    clearTimeout(job.timer)
    job.signal?.removeEventListener('abort', job.onAbort)
    if (kind === 'resolve') job.resolve(value)
    else job.reject(value)
  }

  #pump() {
    while (
      !this.disposed
      && this.queue.length > 0
      && (this.idle.length > 0 || this.#usedSlots() < this.concurrency)
    ) {
      const entry = this.queue.shift()
      this.pendingBytes -= entry.bytes
      if (entry.signal?.aborted) {
        // Aborted between dequeue and start: never spawn for it.
        this.#settleEntry(entry, 'reject', abortReason(entry.signal))
        continue
      }
      // Hand the abort wiring over to the per-job handler: the queued-entry
      // listener MUST come off the caller's signal now, or every started job
      // would leave a second, payload-capturing abort listener behind.
      entry.signal?.removeEventListener('abort', entry.onAbort)
      this.#start(entry)
    }
  }

  #start(entry) {
    const record = this.idle.pop() ?? this.#spawn()
    record.state = 'busy'
    this.active.push(record)
    const id = entry.id
    const description = `${entry.op}#${id}`
    /** @type {JobHandle} */
    const job = {
      id,
      op: entry.op,
      description,
      settled: false,
      signal: entry.signal,
      onAbort: () => {
        const reason = abortReason(job.signal)
        this.#warn(`dsh-image-isolation: ${description} aborted; killing worker ${record.recordId}`)
        this.#beginClose(record, reason)
      },
      timer: undefined,
      resolve: entry.resolve,
      reject: entry.reject,
    }
    job.timer = setTimeout(() => {
      this.#warn(`dsh-image-isolation: ${description} timed out after ${this.timeoutMs}ms; killing worker ${record.recordId}`)
      this.#beginClose(record, attachmentRefusal(
        `Isolated image operation ${description} timed out after ${this.timeoutMs}ms; the worker was killed without retrying.`,
      ))
    }, this.timeoutMs)
    job.signal?.addEventListener('abort', job.onAbort, { once: true })
    record.job = job
    try {
      const sent = record.child.send({ id, op: entry.op, payload: entry.payload })
      if (sent === false) this.#warn(`dsh-image-isolation: worker ${record.recordId} backpressured on send for ${description}`)
    } catch (error) {
      this.#beginClose(record, attachmentRefusal(
        `Isolated image operation ${description} could not reach worker ${record.recordId} (${safeErrorName(error)}); the worker was killed.`,
      ))
    }
  }

  /** Settle a queue entry directly (never started, no cleanup beyond the abort wiring). */
  #settleEntry(entry, kind, value) {
    if (entry.settled) return
    entry.settled = true
    entry.signal?.removeEventListener('abort', entry.onAbort)
    if (kind === 'resolve') entry.resolve(value)
    else entry.reject(value)
  }

  /**
   * Run one operation in an isolated worker.
   * @param {string} op - worker operation name.
   * @param {object} payload - operation payload (buffers allowed).
   * @param {AbortSignal} [signal] - cancellation; kills the dedicated worker and rejects with the signal reason.
   * @returns {Promise<any>} the worker's validated result object.
   */
  async run(op, payload, signal) {
    if (this.disposed) throw attachmentRefusal('dsh-image-isolation worker pool is disposed')
    if (typeof op !== 'string' || op === '') throw new TypeError('op must be a non-empty string')
    if (payload === null || typeof payload !== 'object') throw new TypeError('payload must be an object')
    signal?.throwIfAborted()
    const bytes = auditWireBytes(payload)
    if (!Number.isFinite(bytes) || bytes < 0) {
      throw attachmentRefusal(`Isolated image operation ${op} refused: payload carries an invalid byte declaration.`)
    }
    if (this.#usedSlots() >= this.concurrency && this.queue.length >= this.maxQueue) {
      throw attachmentRefusal(
        `Isolated image workers are saturated (${this.active.length} busy, ${this.queue.length} queued); retry when fewer images are in flight.`,
      )
    }
    if (this.pendingBytes + bytes > MAX_TOTAL_PENDING_BYTES) {
      throw attachmentRefusal(
        `Isolated image operation ${op} refused: pending queue byte budget of ${MAX_TOTAL_PENDING_BYTES} exceeded (${this.pendingBytes} already queued).`,
      )
    }
    return new Promise((resolve, reject) => {
      /** @type {QueueEntry} */
      const entry = {
        id: this.#nextJobId(),
        op,
        payload,
        bytes,
        settled: false,
        signal,
        resolve,
        reject,
        onAbort: () => {
          const index = this.queue.indexOf(entry)
          if (index !== -1) {
            // Still queued: remove, refund the byte budget, reject with the caller reason.
            this.queue.splice(index, 1)
            this.pendingBytes -= bytes
            this.#settleEntry(entry, 'reject', abortReason(signal))
          }
          // If already started, the per-job abort handler owns the kill+settle path.
        },
      }
      signal?.addEventListener('abort', entry.onAbort, { once: true })
      this.queue.push(entry)
      this.pendingBytes += bytes
      this.#pump()
    })
  }

  #jobSeq = 0

  #nextJobId() {
    this.#jobSeq += 1
    return this.#jobSeq
  }

  #maybeFinishDisposal() {
    if (this.disposeResolve === undefined) return
    if (this.workers.size > 0) return
    const resolve = this.disposeResolve
    this.disposeResolve = undefined
    resolve()
  }

  /**
   * Kill every owned worker, reject queued work, and resolve only after every
   * child's real close event has been observed. There is no grace fallback:
   * the promise stays pending until the exits are actually reaped.
   */
  dispose() {
    if (this.disposePromise !== undefined) return this.disposePromise
    this.disposed = true
    const reason = attachmentRefusal('dsh-image-isolation worker pool is disposed')
    for (const entry of this.queue.splice(0)) {
      this.pendingBytes -= entry.bytes
      this.#settleEntry(entry, 'reject', reason)
    }
    for (const record of [...this.workers]) this.#beginClose(record, reason)
    if (this.workers.size === 0) return Promise.resolve()
    this.disposePromise = new Promise((resolve) => { this.disposeResolve = resolve })
    return this.disposePromise
  }
}

function abortReason(signal) {
  if (signal?.reason instanceof Error) return signal.reason
  return new Error('Image operation cancelled.')
}

/** Turn a validated wire error envelope into a real AttachmentError. */
function wireError(envelope, job) {
  const code = envelope.code !== '' ? envelope.code : 'ATTACHMENT_WRITE_FAILED'
  const message = envelope.message !== ''
    ? envelope.message
    : `Isolated image operation ${job.description} failed in worker.`
  return new AttachmentError(message, code)
}

/** Exit facts only: worker id, exit code / signal name, op. No raw output, no paths. */
function workerDeathError(record, job) {
  const exit = record.exitCode !== null && record.exitCode !== undefined
    ? `exit code ${record.exitCode}`
    : `signal ${record.exitSignal ?? 'unknown'}`
  return attachmentRefusal(
    `Isolated image worker ${record.recordId} died (${exit}) during ${job.description}; the operation failed without retry.`,
  )
}
