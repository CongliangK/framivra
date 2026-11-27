import test from 'node:test'
import assert from 'node:assert/strict'
import { FrameEpoch } from '../src/client/frame.mjs'
import { WindowMonitor } from '../src/client/monitor.mjs'
import { connectProgramPlayback } from '../src/client/preview-program-playback.mjs'

// Exercise the foreground/background join, without claiming a browser decoded these bytes.
test('a playhead pin supersedes its pending read; pausing does not revoke the background run', async t => {
  const owner = new FrameEpoch(), monitor = owner.previewWindow = new WindowMonitor()
  const tab = new AbortController(), run = new AbortController(), calls = []
  const version = `sha256:${'1'.repeat(64)}`, epoch = `sha256:${'2'.repeat(64)}`
  const binding = { duration: 96, fps: 30, clock: { num: 30, den: 1 }, contentVersion: version, tabSignal: tab.signal }
  const read = (frame, signal) => new Promise(resolve => calls.push({ frame, signal, deliver() {
    const start = Math.floor(frame / 30) * 30, end = Math.min(start + 30, binding.duration)
    resolve({ start, end, coverageEnd: end, contentVersion: version, inputEpoch: epoch, sha256: '3'.repeat(64), blob: new Blob(['test'], { type: 'video/mp4' }) })
  } }))
  const stop = connectProgramPlayback(monitor, owner, binding, { signal: run.signal, read }, 34)
  t.after(stop)
  assert.deepEqual(calls.map(call => call.frame), [34])
  assert.equal(owner.programTransport(34), true)
  assert(calls[0].signal.aborted); assert(!run.signal.aborted)
  assert.equal(owner.programTransport(34), true)
  owner.pin(63)
  assert.deepEqual(calls.map(call => call.frame), [34, 34, 63]); assert(calls[1].signal.aborted)
  assert.equal(owner.programTransport(63), true, 'Space during a paused scrub joins that read and requests playback')
  assert.equal(monitor.program.wantsPlayback, true)
  assert.equal(calls.length, 3)
  for (const call of calls) call.deliver()
  await Promise.resolve()
  const media = monitor.state.active
  assert.equal(media.frame, 60); assert.equal(media.initialPin.frame, 63); assert.equal(monitor.playOperation, media.operationId)
  assert(owner.pin(64, media)); assert.equal(monitor.state.active, media, 'native playback notification is not a seek')
  owner.pin(90)
  assert.equal(calls.at(-1).frame, 90)
  run.abort()
  calls.at(-1).deliver(); await Promise.resolve()
  assert.equal(monitor.state.active, undefined); assert.equal(owner.programTransport, undefined)
  assert(!owner.pin(90, media), 'revoked media must no longer publish its clock')
})
