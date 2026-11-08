// Regression against the REAL official @deepseek-ai/cordis (4.0.5) service
// identity semantics, driven through the ACTUAL src/client/index.mjs apply()
// and its registered slot closure — no production logic is copied here.
//
// Mechanism (official cordis source, service.ts/reflect.ts/utils.ts/fiber.ts):
//   - Every official service extends cordis `Service`, which stamps the
//     instance with `[symbols.tracker]`; `ctx.get(name)` / `ctx[name]` wrap
//     that instance in a NEW traceable Proxy on EVERY read. Same provider,
//     unchanged service — the returned reference is never `===` to the
//     previous read.
//   - src/client/index.mjs builds getDraftServices with a fresh
//     `ctx.get?.('conversation')` per call, while
//     src/client/draft-services.mjs gates stageDraft with a strict identity
//     check against the captured owner. A per-call getDraftServices can
//     therefore NEVER pass that gate under real cordis.
//   - Sanctioned pinning: a plugin declaring `inject` may hold the resolved
//     service reference for its whole apply() lifetime; cordis re-runs the
//     fiber whenever the providing fiber changes (epoch keyed on the
//     provider fiber uid), so a replacement re-pins while the old facade's
//     effects are disposed. No Host-private API, symbol or monkeypatch.
//
// The `applied` assertions below are expected RED until the production
// closure pins the injected services; the mechanism tests stay GREEN either
// way so the RED is isolated to the production seam.
import test from 'node:test'
import assert from 'node:assert/strict'
import { Context, Service } from '@deepseek-ai/cordis'
import { apply as clientApply, inject as clientInject } from '../src/client/index.mjs'
import { createOfficialDraftServices } from '../src/client/draft-services.mjs'

const PNG_BYTES = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])
const png = () => new File([PNG_BYTES], 'frame.png', { type: 'image/png' })

// Real cordis `Service` subclass shaped like the official ConversationController:
// exactly how the official class is registered, exposing only the public seams
// the facade consumes (see draft-services.test.mjs header for the seam list).
class ConversationController extends Service {
  constructor(ctx, state) {
    super(ctx, 'conversation')
    this.state = state
  }
  get input() {
    return {
      for: scope => {
        if (scope !== this.state.binding.ctx) throw new Error('conversation.input.for requires a retained Session scope')
        return { state: { getSnapshot: () => ({ draft: this.state.text, attachmentIds: [...this.state.order], draftRev: this.state.rev, phase: this.state.phase }) } }
      },
    }
  }
  createDrafts(sessionId, files) {
    this.state.calls.push({ op: 'createDrafts', sessionId })
    return files.map(file => {
      const id = `att-${++this.state.seq}`
      this.state.attachments.set(id, { id, kind: 'image', file })
      return { id, kind: 'image' }
    })
  }
  resolveDraftAttachments(ids) { return ids.map(id => this.state.attachments.get(id)) }
  releaseDraftAttachment(id) { this.state.calls.push({ op: 'releaseDraftAttachment', id }) }
}

function makeWorld() {
  const state = {
    text: 'SENTINEL_KEEP tail', rev: 3, phase: 'plain', caret: 13,
    order: [], attachments: new Map(), seq: 0,
    navKey: 's1', cwd: '/w',
    lifecycle: { removed: false, openState: 'open' },
    limits: { maxImageBytes: 1024 * 1024, maxImagesPerMessage: 2, maxMessageImageBytes: 2 * 1024 * 1024, mediaTypes: ['image/png'] },
    calls: [],
  }
  state.binding = {
    sessionId: 's1', ctx: { __agentScope: 's1' },
    session: {
      getSnapshot: () => state.lifecycle,
      projections: { faceOf: name => (name === 'imageLimits' ? { getSnapshot: () => state.limits } : undefined) },
    },
  }
  state.actions = {
    captureInsertion: () => ({ start: state.caret, end: state.caret, draftRev: state.rev }),
    insertText: (text, span) => {
      if (state.phase !== 'plain' || span.draftRev !== state.rev) return false
      state.text = state.text.slice(0, span.start) + text + state.text.slice(span.start)
      state.rev += 1
      return true
    },
    addAttachments: ids => { if (state.phase !== 'plain') return false; state.order = [...state.order, ...ids]; return true },
    removeAttachment: id => { state.order = state.order.filter(candidate => candidate !== id) },
  }
  state.sessions = { binding: () => state.binding, list: { getSnapshot: () => ({ byId: { s1: { cwd: state.cwd } } }) } }
  state.uiSession = { adapter: { current: { getSnapshot: () => ({ key: state.navKey }) } } }
  return state
}

// Plain-object mocks for the OTHER public DI seams (allowed surfaces); only
// conversation is a real Service. The slots mock records every registration
// and its disposal so tests can consume the ACTUAL production closures.
function makeHostMocks() {
  const slotRecords = []
  const tabsRecords = []
  const fibers = []
  const slots = {
    inject: (name, factory) => factory(),
    register: (config, component) => {
      const record = { config, component, disposed: false }
      slotRecords.push(record)
      return () => { record.disposed = true }
    },
  }
  const sidebarRightTabs = {
    register: config => {
      const record = { config, disposed: false }
      tabsRecords.push(record)
      return () => { record.disposed = true }
    },
  }
  const configForms = {
    get: () => ({ subscribe: () => () => {}, getSnapshot: () => ({ status: 'unavailable' }) }),
    whileServed: (_names, callback) => callback(),
  }
  return { slots, sidebarRightTabs, configForms, slotRecords, tabsRecords, fibers }
}

// Real Context + real cordis DI wiring: conversation via a Service subclass,
// everything else provided once; the ACTUAL client apply() runs as a plugin
// with its production inject list. Disposes every fiber even on assert failure.
async function makeHarness(t, { world = makeWorld() } = {}) {
  const ctx = new Context()
  const mocks = makeHostMocks()
  const disposers = [
    ctx.reflect.provide('slots', mocks.slots),
    ctx.reflect.provide('sidebarRightTabs', mocks.sidebarRightTabs),
    ctx.reflect.provide('configForms', mocks.configForms),
    ctx.reflect.provide('sessions', world.sessions),
    ctx.reflect.provide('uiSession', world.uiSession),
  ]
  const track = fiber => { mocks.fibers.push(fiber); return fiber }
  t.after(async () => {
    await Promise.allSettled(mocks.fibers.map(fiber => fiber.dispose()))
    await Promise.allSettled(disposers.map(dispose => dispose()))
  })
  return { ctx, world, mocks, track }
}

const paneTab = mocks => [...mocks.slotRecords].reverse().find(record => record.config.name === 'sidebar.right.pane.tab' && !record.disposed)

const facadeFor = (mocks, world) => {
  const record = paneTab(mocks)
  assert.ok(record, 'production registered the sidebar.right.pane.tab slot')
  const props = record.config.inject('s1') // ACTUAL production closure (index.mjs)
  assert.equal(props.sessionId, 's1')
  return createOfficialDraftServices({
    sessionId: 's1',
    getServices: () => props.getDraftServices(), // same wiring as PreviewBody
    getActions: () => world.actions,
    getAllowed: () => true,
  }).getServices().conversation
}

// --- mechanism (GREEN regardless of the production fix) ---

test('real cordis: ctx.get(conversation) twice returns two DIFFERENT proxies for the SAME service', async t => {
  const { ctx, track } = await makeHarness(t)
  const provider = track(ctx.plugin({ name: 'provider', apply: ctx => { new ConversationController(ctx, makeWorld()) } }))
  await provider.await()
  const a = ctx.get('conversation')
  const b = ctx.get('conversation')
  assert.ok(a && b, 'service resolved')
  assert.notEqual(a, b, 'each read wraps the same instance in a NEW traceable Proxy')
  assert.equal(typeof a.createDrafts, 'function')
  assert.equal(typeof b.resolveDraftAttachments, 'function')
})

test('real cordis: injected property access ctx.conversation also returns a fresh proxy per read', async t => {
  const { ctx, track } = await makeHarness(t)
  const provider = track(ctx.plugin({ name: 'provider', apply: ctx => { new ConversationController(ctx, makeWorld()) } }))
  await provider.await()
  const user = track(ctx.plugin({
    name: 'user', inject: ['conversation'],
    apply: ctx => { assert.notEqual(ctx.conversation, ctx.conversation, 'property reads are per-read proxies too') },
  }))
  await provider.await()
  await user.await()
})

// --- production seam: the ACTUAL index.mjs apply() and slot closure ---

test('REGRESSION (RED until pinned): actual apply props — same provider, consecutive capture/stage APPLY; sentinel kept, one PNG ordered, no send', async t => {
  const { ctx, world, mocks, track } = await makeHarness(t)
  const provider = track(ctx.plugin({ name: 'provider', apply: ctx => { new ConversationController(ctx, world) } }))
  await provider.await()
  const plugin = track(ctx.plugin({ name: 'framivra-dsh-sidebar', inject: clientInject, apply: clientApply }))
  await plugin.await()
  const facade = facadeFor(mocks, world)

  // Consecutive text stage through the production closure: must APPLY.
  const target1 = facade.captureDraftTarget(world.binding)
  const text = facade.stageDraft({ target: target1, text: '[ref-1]', files: [] })
  assert.equal(text.status, 'applied')
  assert.equal(text.atomicity, 'sequential')
  assert.equal(world.text, 'SENTINEL_KEEP[ref-1] tail', 'original draft preserved around the insertion')

  // Consecutive text+PNG stage: exactly one image draft, appended in order,
  // and only the public controller seam was touched (no send/upload).
  const beforeImage = world.text
  const imageCaret = world.caret // This fixture deliberately keeps its caret fixed.
  const target2 = facade.captureDraftTarget(world.binding)
  const image = facade.stageDraft({ target: target2, text: '帧引用', files: [png()] })
  assert.equal(image.status, 'applied')
  assert.equal(world.text, beforeImage.slice(0, imageCaret) + '帧引用' + beforeImage.slice(imageCaret), 'insert at the actual captured caret, never replace the first reference')
  assert.ok(world.text.includes('[ref-1]'), 'the earlier reference remains intact')
  assert.deepEqual(world.order, ['att-1'], 'exactly one PNG attachment, in stage order')
  assert.equal(world.attachments.get('att-1').kind, 'image')
  assert.deepEqual(world.calls.map(call => call.op), ['createDrafts'], 'no send/submit/upload/persist surface was called')
})

test('REGRESSION: provider disposed — old lifetime inactive, old target refused, old slots disposed', async t => {
  const { ctx, world, mocks, track } = await makeHarness(t)
  const provider = track(ctx.plugin({ name: 'provider', apply: ctx => { new ConversationController(ctx, world) } }))
  await provider.await()
  const plugin = track(ctx.plugin({ name: 'framivra-dsh-sidebar', inject: clientInject, apply: clientApply }))
  await plugin.await()
  const facade = facadeFor(mocks, world)
  const target = facade.captureDraftTarget(world.binding)
  const oldRecord = paneTab(mocks)

  await provider.dispose()
  await plugin.await()
  // The old slot registration is disposed with the fiber (slots disposal).
  assert.equal(oldRecord.disposed, true, 'old pane-tab registration disposed on fiber unload')
  // The old target must NOT apply after the provider is gone: either the old
  // facade reports plugin-inactive (pinned lifetime) or the stale closure
  // read throws in the inactive context — never a mutation.
  let outcome
  try { outcome = facade.stageDraft({ target, text: 'stale', files: [] }) } catch { outcome = { status: 'threw' } }
  assert.notEqual(outcome.status, 'applied')
  assert.equal(world.text, 'SENTINEL_KEEP tail', 'zero mutation from a stale target')
})

test('REGRESSION (RED until pinned): replacement provider re-runs apply — NEW slot props capture/stage APPLY', async t => {
  const { ctx, world, mocks, track } = await makeHarness(t)
  const provider1 = track(ctx.plugin({ name: 'provider1', apply: ctx => { new ConversationController(ctx, world) } }))
  await provider1.await()
  const plugin = track(ctx.plugin({ name: 'framivra-dsh-sidebar', inject: clientInject, apply: clientApply }))
  await plugin.await()
  const firstRecord = paneTab(mocks)

  await provider1.dispose()
  await plugin.await()
  const provider2 = track(ctx.plugin({ name: 'provider2', apply: ctx => { new ConversationController(ctx, world) } }))
  await provider2.await()
  await plugin.await()

  assert.equal(firstRecord.disposed, true, 'previous registration replaced by the apply re-run')
  // The NEW registration's closure resolves the NEW controller and applies.
  const facade = facadeFor(mocks, world)
  const target = facade.captureDraftTarget(world.binding)
  const result = facade.stageDraft({ target, text: '[ref-2]', files: [] })
  assert.equal(result.status, 'applied')
  assert.equal(world.text, 'SENTINEL_KEEP[ref-2] tail')
})
