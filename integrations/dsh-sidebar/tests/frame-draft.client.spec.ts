// @vitest-environment jsdom
// Explicit source-runtime probe, not a browser/native-DSH claim or CI gate.
// Run via an evidence-local DSH Vitest config, with SIDEBAR_FRAME_ENVELOPE from
// a real installed CLI/public-provider Host run. Native PNG decode below is an
// explicitly supplied jsdom adapter, not a fake 'successful' image decoder.
import { afterEach, describe, expect, it, onTestFinished, vi } from 'vitest'
import { readFile } from 'node:fs/promises'
import { spawnSync } from 'node:child_process'
import { webcrypto } from 'node:crypto'
import { SlotTestRuntime, makeTranslate } from '@deepseek-ai/dsh-client-test-runtime'
import { createSnapshotStore } from '@deepseek-ai/dsh-client-store'
import type { SessionId } from '@deepseek-ai/dsh-session/types'
import { ConversationController } from '@deepseek-ai/dsh-client-ui-conversation/src/client/service.ts'
import { InputHub } from '@deepseek-ai/dsh-client-ui-conversation/src/client/input/hub.ts'
import { ComposerBlockRegistry } from '@deepseek-ai/dsh-client-ui-conversation/src/client/input/blocks.ts'
import { zh } from '@deepseek-ai/dsh-client-ui-conversation/src/client/locales.ts'
import { captureCurrentDraft } from '../src/client/reference.mjs'
import { verifyFrame, frameDraftText, FrameEpoch } from '../src/client/frame.mjs'
const SID = 'frame-source-runtime' as SessionId
const limits = { mediaTypes: ['image/png'], maxImagesPerMessage: 4, maxImageBytes: 8 * 1024 * 1024, maxMessageImageBytes: 16 * 1024 * 1024 }
const envelope = JSON.parse(await readFile(process.env.SIDEBAR_FRAME_ENVELOPE!, 'utf8'))
const python = process.env.SIDEBAR_PNG_DECODER_PYTHON!
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })
function bytes(file: Blob): Promise<ArrayBuffer> { return new Promise((resolve, reject) => { const r = new FileReader(); r.onload = () => resolve(r.result as ArrayBuffer); r.onerror = () => reject(r.error); r.readAsArrayBuffer(file) }) }
async function bench() {
  const runtime = await SlotTestRuntime.create()
  onTestFinished(() => runtime.dispose())
  const prompt = vi.fn(async () => ({ ok: true as const, value: { accepted: true as const } }))
  await runtime.sessions.add({ id: SID, summary: { cwd: '/own-before' }, session: { prompt } })
  const reference = runtime.sessions.retain(SID, { source: 'mainView' }); await reference.ready
  await runtime.sessions.setProjection(SID, 'imageLimits', limits)
  const hub = new InputHub(runtime.ctx, makeTranslate(zh, {})), blocks = new ComposerBlockRegistry()
  const fiber = runtime.ctx.plugin(ConversationController, { input: hub, blocks, maxConcurrentFileUploads: 2 }); await fiber.await()
  const binding = reference.binding, shell = hub.shellFor(binding), conversation = runtime.ctx.conversation
  const uploads = vi.spyOn(runtime.fileUpload, 'upload')
  let serial = 0, hold: Promise<void> | undefined
  const urls = new Map<string, Blob>()
  vi.spyOn(URL, 'createObjectURL').mockImplementation(blob => { const key = `blob:source-native-${++serial}`; urls.set(key, blob as Blob); return key })
  vi.spyOn(URL, 'revokeObjectURL').mockImplementation(url => { urls.delete(url) })
  vi.stubGlobal('Image', class {
    src = ''; naturalWidth = 0; naturalHeight = 0
    async decode() {
      const file = urls.get(this.src); if (!file) throw new Error('unknown native adapter URL')
      const png = Buffer.from(await bytes(file))
      const decoded = spawnSync(python, ['-I', '-B', '-c', 'import io,json,sys; from PIL import Image; i=Image.open(io.BytesIO(sys.stdin.buffer.read())); i.load(); print(json.dumps(i.size))'], { input: png, maxBuffer: 4096 })
      if (decoded.status !== 0) throw new Error(`native PNG decode refused: ${decoded.stderr}`)
      ;[this.naturalWidth, this.naturalHeight] = JSON.parse(decoded.stdout.toString())
      if (hold) await hold
    }
  })
  vi.stubGlobal('crypto', webcrypto)
  const verified = await verifyFrame(envelope, { frame: 6, contentVersion: envelope.content_version, signal: new AbortController().signal, limits })
  shell.setDraft('existing draft')
  const seed = await conversation.stageDraft({ target: conversation.captureDraftTarget(binding), text: ' + existing image', files: [verified.file] })
  expect(seed.status).toBe('applied')
  const getServices = () => ({ conversation, sessions: runtime.ctx.sessions, uiSession: runtime.ctx.uiSession })
  return { runtime, reference, fiber, binding, shell, conversation, uploads, prompt, urls, verified, getServices, holdDecode: (p: Promise<void>) => { hold = p } }
}
describe('real core frame wire -> verified exact File -> actual public ConversationController', () => {
  for (const change of ['none', 'revision', 'navigation', 'cwd', 'permission', 'cancel', 'dispose', 'limits'] as const) {
    it(`append-only actual draft intake: ${change}`, async () => {
      const b = await bench(), before = b.shell.snapshot, beforeURLs = b.urls.size
      const capture = vi.spyOn(b.conversation, 'captureDraftTarget'), stage = vi.spyOn(b.conversation, 'stageDraft')
      const permission = createSnapshotStore(true)
      if (change === 'permission') vi.spyOn(b.runtime.ctx.remoteAccess, 'source').mockReturnValue(permission)
      const captured = captureCurrentDraft(b.getServices, SID)
      expect(capture).toHaveBeenCalledOnce()
      let release!: () => void
      b.holdDecode(new Promise<void>(resolve => { release = resolve }))
      const controller = new AbortController(), text = frameDraftText(b.verified, { kind: 'block', address: '/video_tracks/1/clips/0' })
      const pending = captured.conversation.stageDraft({ target: captured.target, text, files: [b.verified.file], signal: controller.signal })
      expect(stage).toHaveBeenCalledOnce()
      if (change === 'revision') b.shell.setDraft('typed while decoding')
      if (change === 'navigation') { await b.runtime.sessions.add({ id: 'other' as SessionId }); b.reference.release(); const other = b.runtime.sessions.retain('other' as SessionId, { source: 'mainView' }); await other.ready; onTestFinished(() => other.release()) }
      if (change === 'cwd') await b.runtime.sessions.updateSummary(SID, { cwd: '/own-after' })
      if (change === 'permission') permission.set(false)
      if (change === 'cancel') controller.abort()
      if (change === 'dispose') await b.fiber.dispose()
      if (change === 'limits') await b.runtime.sessions.setProjection(SID, 'imageLimits', { ...limits, maxImageBytes: 1 })
      release(); const result = await pending
      if (change === 'none') {
        expect(result.status).toBe('applied')
        expect(b.shell.snapshot.draft).toBe(before.draft + text)
        expect(b.shell.snapshot.attachmentIds).toHaveLength(2)
        expect(b.shell.snapshot.attachmentIds[0]).toBe(before.attachmentIds[0])
        expect(text).toContain(envelope.image.sha256)
        expect(text).toContain('9007199254740993')
        expect(text).toContain('"raw"')
        expect(Buffer.from(await bytes(b.verified.file)).equals(Buffer.from(envelope.image.inline.data, 'base64'))).toBe(true)
        expect(b.urls.size).toBe(beforeURLs + 1)
      } else {
        expect(result.status).not.toBe('applied')
        expect(b.shell.snapshot.draft).toBe(change === 'revision' ? 'typed while decoding' : before.draft)
        expect(b.shell.snapshot.attachmentIds).toEqual(before.attachmentIds)
        expect(b.urls.size).toBe(change === 'dispose' || change === 'navigation' ? 0 : beforeURLs)
      }
      expect(capture).toHaveBeenCalledOnce()
      expect(b.uploads).not.toHaveBeenCalled(); expect(b.prompt).not.toHaveBeenCalled()
    })
  }
  it('dirty/unknown parent invalidation clears generated File before any draft intake', async () => {
    const b = await bench(), owner = new FrameEpoch(), before = b.shell.snapshot
    owner.generated = b.verified
    const operation = owner.begin(new AbortController().signal)
    owner.editChanged({ dirty: true, unknown: true })
    expect(owner.generated).toBeUndefined(); expect(operation.signal.aborted).toBe(true)
    expect(() => owner.check(operation)).toThrow()
    expect(b.shell.snapshot).toBe(before); expect(b.uploads).not.toHaveBeenCalled(); expect(b.prompt).not.toHaveBeenCalled()
  })
})
