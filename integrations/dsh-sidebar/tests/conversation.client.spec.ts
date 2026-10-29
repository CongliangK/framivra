// @vitest-environment jsdom
// Opt-in through the reviewed DSH source test config; production imports no DSH private modules.
import { afterEach, describe, expect, onTestFinished, vi } from 'vitest'
import { createSnapshotStore } from '@deepseek-ai/dsh-client-store'
afterEach(() => vi.restoreAllMocks())
import { createClientTest, webApp } from '@deepseek-ai/dsh-client-test-runtime/src/assembly/index.ts'
import { SessionId } from '@deepseek-ai/dsh-session/types'
import { sessionWorld } from 'sidebar-session-world'
import { captureCurrentDraft, performReferenceAction } from '../src/client/reference.mjs'

const it = createClientTest({ roster: webApp.closure(['@deepseek-ai/dsh-client-ui-conversation']) })
const version = `sha256:${'a'.repeat(64)}`
const selection = { kind: 'block', address: '/subtitles/0' }
const envelope = { content_version: version, selection: { global_frame: 0, fps: 30, tracks: [], blocks: [{ address: selection.address, stable_id: null, raw: '{ "id":9007199254740993 }' }] }, image: { state: 'not-generated' } }

describe('sidebar -> real shipped public conversation service composition', () => {
  for (const change of ['none', 'revision', 'navigation', 'cwd', 'permission', 'cancel', 'dispose', 'blank'] as const) {
    it(`pins click target and refuses ${change} without replacing/sending any draft`, async ({ mock, start }) => {
      mock.load(sessionWorld)
      const id = SessionId(`sidebar-${change}`)
      mock.remote.session.list.mockResolvedValue({ ok: true, value: { items: [{ sessionId: id, updatedAt: 1, running: false, blank: false, agentAvailable: true, cwd: '/before' }, { sessionId: SessionId('sidebar-other'), updatedAt: 1, running: false, blank: false, agentAvailable: true }] } })
      const client = await start()
      const ctx = client.ctx
      await ctx.sessions.refresh()
      const reference = ctx.sessions.retain(id, { source: 'controllerOperation' })
      onTestFinished(() => reference.release())
      await reference.ready
      const input = ctx.conversation.input.for(reference.binding.ctx)
      input.setDraft('existing draft')
      const getServices = () => ({ conversation: ctx.get('conversation'), sessions: ctx.get('sessions') })
      if (change === 'blank') {
        expect(() => captureCurrentDraft(getServices, undefined)).toThrow('尚无已保留绑定')
        expect(mock.remote.session.prompt).not.toHaveBeenCalled()
        return
      }
      const conversation = ctx.conversation
      const allowed = createSnapshotStore(true)
      if (change === 'permission') vi.spyOn(ctx.remoteAccess, 'source').mockReturnValue(allowed)
      const capture = vi.spyOn(conversation, 'captureDraftTarget')
      const stage = vi.spyOn(conversation, 'stageDraft')
      const captured = captureCurrentDraft(getServices, id)
      expect(capture).toHaveBeenCalledOnce()
      let finish: (value: typeof envelope) => void
      const reading = new Promise<typeof envelope>(resolve => { finish = resolve })
      const controller = new AbortController()
      const result = performReferenceAction({ action: 'draft', selection, contentVersion: version, signal: controller.signal, captured, read: () => reading })
      expect(stage).not.toHaveBeenCalled()
      if (change === 'revision') input.setDraft('typed while reading')
      if (change === 'navigation') {
        const other = ctx.sessions.retain(SessionId('sidebar-other'), { source: 'mainView' })
        onTestFinished(() => other.release())
        await other.ready
        expect(ctx.uiSession.adapter.current.getSnapshot().key).toBe('sidebar-other')
      }
      if (change === 'cwd') {
        mock.remote.session.list.mockResolvedValue({ ok: true, value: { items: [{ sessionId: id, updatedAt: 2, running: false, blank: false, agentAvailable: true, cwd: '/after' }] } })
        await ctx.sessions.refresh()
      }
      if (change === 'permission') allowed.set(false)
      if (change === 'cancel') controller.abort()
      if (change === 'dispose') await reference.binding.ctx.fiber.dispose()
      finish!(envelope)
      if (change === 'cancel') await expect(result).rejects.toMatchObject({ name: 'AbortError' })
      else {
        const outcome = await result
        expect(outcome).toMatchObject(change === 'none' ? { status: 'applied' } : change === 'permission' ? { status: 'blocked', reason: 'permission' } : { status: 'stale', reason: change === 'dispose' ? 'binding' : change })
        expect(stage).toHaveBeenCalledOnce()
      }
      expect(capture).toHaveBeenCalledOnce()
      const draft = input.state.getSnapshot().draft
      if (change === 'none') { expect(draft.startsWith('existing draft')).toBe(true); expect(draft).toContain('9007199254740993') }
      else expect(draft).toBe(change === 'revision' ? 'typed while reading' : 'existing draft')
      expect(mock.remote.session.prompt).not.toHaveBeenCalled()
    })
  }
})
