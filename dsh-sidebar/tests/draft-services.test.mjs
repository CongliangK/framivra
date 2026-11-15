// Unit tests for the parent-owned facade src/client/draft-services.mjs.
// Stubs mirror the OFFICIAL seams the facade consumes:
//   - sessions.binding(id) -> SessionBinding {sessionId, ctx, session} with
//     session.getSnapshot() lifecycle and projections.faceOf('imageLimits');
//   - conversation.input.for(binding.ctx) -> {state:{getSnapshot()}} — for()
//     throws unless the scope is retained (official hub.ts:55);
//   - ConversationController.createDrafts / resolveDraftAttachments /
//     releaseDraftAttachment (public, exported class client/index.ts);
//     createDrafts is the official THREE-argument form (dsh-v0.2.2-rc.1
//     lib/types/client/service.d.ts) and the bridge must pass
//     uploadImages: false (no service-owned background upload);
//   - uiSession.adapter.current.getSnapshot() -> SessionFace {key};
//   - sessions.list.getSnapshot().byId[id].cwd;
//   - slot InputActions (removeAttachment returns void).
// Image byte VALIDATION belongs to Frame; these tests only exercise the
// facade's budget math and refusal ordering, using valid PNG-mock Files.
import test from 'node:test'
import assert from 'node:assert/strict'
import { createOfficialDraftServices, draftResultMessage } from '../src/client/draft-services.mjs'

const PNG_BYTES = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])
const png = (bytes = PNG_BYTES) => new File([bytes], 'frame.png', { type: 'image/png' })

function makeWorld(overrides = {}) {
  const state = {
    // draft model (authoritative input state projection)
    text: 'SENTINEL_KEEP tail',
    rev: 3,
    phase: 'plain',
    caret: 13, // inside the sentinel region boundary, zero-width caret
    order: [], // attachment ids in draft order
    attachments: new Map(),
    seq: 0,
    // host facts
    retained: true,
    allowed: true,
    servicesActive: true,
    navKey: 's1',
    cwd: '/w',
    lifecycle: { removed: false, openState: 'open' },
    limits: { maxImageBytes: 1024 * 1024, maxImagesPerMessage: 2, maxMessageImageBytes: 2 * 1024 * 1024, mediaTypes: ['image/png'] },
    limitsSnapshot: undefined, // when set, faceOf getSnapshot returns this
    resolveOverride: undefined,
    createThrows: false,
    calls: [],
    ...overrides,
  }
  const binding = {
    sessionId: 's1',
    ctx: { __agentScope: 's1' },
    session: {
      getSnapshot: () => state.lifecycle,
      projections: { faceOf: name => (name === 'imageLimits' ? { getSnapshot: () => state.limitsSnapshot ?? state.limits } : undefined) },
    },
  }
  state.binding = binding

  const actions = {
    captureInsertion: () => ({ start: state.caret, end: state.caret, draftRev: state.rev }),
    insertText: (text, span) => {
      if (state.phase !== 'plain' || span.draftRev !== state.rev) return false
      state.text = state.text.slice(0, span.start) + text + state.text.slice(span.start)
      state.rev += 1
      return true
    },
    addAttachments: ids => {
      if (state.phase !== 'plain') return false
      state.order = [...state.order, ...ids]
      return true
    },
    removeAttachment: id => { state.order = state.order.filter(candidate => candidate !== id) },
  }
  state.actionsRef = actions

  const conversation = {
    input: {
      for: ctx => {
        if (!state.retained || ctx !== binding.ctx) throw new Error('conversation.input.for requires a retained Session scope')
        return { state: { getSnapshot: () => ({ draft: state.text, attachmentIds: [...state.order], draftRev: state.rev, phase: state.phase }) } }
      },
    },
    createDrafts(sessionId, files, uploadImages) {
      state.calls.push({ op: 'createDrafts', sessionId, files, uploadImages })
      if (state.createThrows) throw new Error('boom')
      return files.map(file => {
        const id = `att-${++state.seq}`
        state.attachments.set(id, { id, kind: file.type === 'image/png' ? 'image' : 'file', file })
        return { id, kind: file.type === 'image/png' ? 'image' : 'file' }
      })
    },
    resolveDraftAttachments(ids) {
      return state.resolveOverride ? state.resolveOverride(ids) : ids.map(id => state.attachments.get(id))
    },
    releaseDraftAttachment(id) { state.calls.push({ op: 'releaseDraftAttachment', id }) },
  }
  state.conversationRef = conversation

  const sessions = {
    binding: id => (id === 's1' && state.binding.sessionId === 's1' ? state.binding : state.binding),
    list: { getSnapshot: () => ({ byId: { s1: { cwd: state.cwd } } }) },
  }
  const uiSession = { adapter: { current: { getSnapshot: () => ({ key: state.navKey }) } } }

  const services = () => ({
    active: state.servicesActive ? true : false,
    conversation: state.conversationRef,
    sessions,
    uiSession,
  })

  const make = () => createOfficialDraftServices({
    sessionId: 's1',
    getServices: services,
    getActions: () => state.actionsRef,
    getAllowed: () => state.allowed,
  })
  return { state, make, worldServices: services }
}

const stage = (svc, target, text, files) => svc.getServices().conversation.stageDraft({ target, text, files })

// --- normal text: sentinel and surrounding characters preserved ---

test('text append inserts at the collapsed caret and preserves the sentinel and tail', () => {
  const { state, make } = makeWorld()
  const svc = make()
  const target = svc.getServices().conversation.captureDraftTarget(state.binding)
  const result = stage(svc, target, '[ref-1]', [])
  assert.equal(result.status, 'applied')
  assert.equal(result.atomicity, 'sequential')
  assert.equal(result.textApplied, true)
  assert.equal(state.text, 'SENTINEL_KEEP[ref-1] tail')
  assert.equal(state.rev, 4)
  assert.deepEqual(state.order, [])
  assert.deepEqual(state.calls, [], 'no controller mutation for text-only append')
})

test('text+PNG append preserves existing text and appends exactly one image draft', () => {
  const { state, make } = makeWorld()
  const svc = make()
  const target = svc.getServices().conversation.captureDraftTarget(state.binding)
  const result = stage(svc, target, '帧引用', [png()])
  assert.equal(result.status, 'applied')
  assert.equal(result.textApplied, true)
  assert.equal(state.text, 'SENTINEL_KEEP帧引用 tail')
  assert.deepEqual(state.order, ['att-1'])
  assert.equal(state.attachments.get('att-1').kind, 'image')
  assert.deepEqual(state.calls.map(call => call.op), ['createDrafts'])
  assert.equal(state.calls[0].sessionId, 's1')
  assert.equal(state.calls[0].files[0].type, 'image/png')
  // Official 3-arg contract (dsh-v0.2.2-rc.1): explicit uploadImages:false.
  assert.equal(state.calls[0].uploadImages, false)
})

test('image-only stage (empty text) applies the attachment without touching text', () => {
  const { state, make } = makeWorld()
  const svc = make()
  const target = svc.getServices().conversation.captureDraftTarget(state.binding)
  const result = stage(svc, target, '', [png()])
  assert.equal(result.status, 'applied')
  assert.equal(result.textApplied, false)
  assert.equal(state.text, 'SENTINEL_KEEP tail')
  assert.deepEqual(state.order, ['att-1'])
})

// --- refusal gates: each changed fact refuses with zero mutation ---

test('prompt permission false refuses as session-inactive with zero mutation', () => {
  const { state, make } = makeWorld({ allowed: false })
  const svc = make()
  const target = svc.getServices().conversation.captureDraftTarget(state.binding)
  const result = stage(svc, target, 'x', [png()])
  assert.equal(result.status, 'refused')
  assert.equal(result.reason, 'session-inactive')
  assert.deepEqual(state.calls, [])
  assert.deepEqual(state.order, [])
})

test('navigation away before the async stage returns: session-inactive (active=key===sessionId shadows navigation-changed)', () => {
  const { state, make } = makeWorld()
  const svc = make()
  const target = svc.getServices().conversation.captureDraftTarget(state.binding)
  state.navKey = 's2'
  const result = stage(svc, target, 'x', [png()])
  // The facade computes active as navigation.key === sessionId, so a
  // navigation change surfaces as session-inactive (gate checks active
  // first); either way: refused, zero mutation.
  assert.equal(result.status, 'refused')
  assert.equal(result.reason, 'session-inactive')
  assert.deepEqual(state.calls, [])
})

test('re-bound session object (same ctx) refuses as binding-changed', () => {
  const { state, make } = makeWorld()
  const svc = make()
  const target = svc.getServices().conversation.captureDraftTarget(state.binding)
  state.binding = { ...state.binding } // new binding object, SAME retained ctx
  const result = stage(svc, target, 'x', [png()])
  assert.equal(result.status, 'refused')
  assert.equal(result.reason, 'binding-changed')
  assert.deepEqual(state.calls, [])
})

test('re-bound session with a NEW ctx makes input.for throw loudly (not a silent refusal)', () => {
  const { state, make } = makeWorld()
  const svc = make()
  const target = svc.getServices().conversation.captureDraftTarget(state.binding)
  state.binding = { ...state.binding, ctx: { __agentScope: 's1-new' } }
  assert.throws(() => stage(svc, target, 'x', [png()]), /conversation\.input\.for requires a retained Session scope/)
})

test('cwd changed refuses as cwd-changed', () => {
  const { state, make } = makeWorld()
  const svc = make()
  const target = svc.getServices().conversation.captureDraftTarget(state.binding)
  state.cwd = '/elsewhere'
  assert.equal(stage(svc, target, 'x', [png()]).reason, 'cwd-changed')
})

test('draftRev bumped (user typed) refuses as draft-changed with zero mutation', () => {
  const { state, make } = makeWorld()
  const svc = make()
  const target = svc.getServices().conversation.captureDraftTarget(state.binding)
  state.rev += 1
  const result = stage(svc, target, 'x', [png()])
  assert.equal(result.status, 'refused')
  assert.equal(result.reason, 'draft-changed')
  assert.deepEqual(state.calls, [])
})

test('replaced InputActions face (same scalars) refuses as actions-changed', () => {
  const { state, make } = makeWorld()
  const svc = make()
  const target = svc.getServices().conversation.captureDraftTarget(state.binding)
  const world2 = makeWorld()
  state.actionsRef = world2.state.actionsRef
  const result = stage(svc, target, 'x', [png()])
  assert.equal(result.status, 'refused')
  assert.equal(result.reason, 'actions-changed')
})

test('replaced ConversationController between capture and stage refuses as conversation-changed', () => {
  const { state, make } = makeWorld()
  const svc = make()
  const target = svc.getServices().conversation.captureDraftTarget(state.binding)
  state.conversationRef = { ...state.conversationRef } // clone sharing the same retained scope
  const result = stage(svc, target, 'x', [png()])
  assert.equal(result.status, 'refused')
  assert.equal(result.reason, 'conversation-changed')
})

test('closed/removed lifecycle refuses as session-inactive', () => {
  for (const lifecycle of [{ removed: false, openState: 'closed' }, { removed: true, openState: 'open' }]) {
    const { state, make } = makeWorld({ lifecycle })
    const svc = make()
    const target = svc.getServices().conversation.captureDraftTarget(state.binding)
    assert.equal(stage(svc, target, 'x', [png()]).reason, 'session-inactive', JSON.stringify(lifecycle))
  }
})

// --- one-use target and file-shape gates ---

test('the draft target is one-use: a second stage is refused as target-invalid-or-used', () => {
  const { state, make } = makeWorld()
  const svc = make()
  const target = svc.getServices().conversation.captureDraftTarget(state.binding)
  assert.equal(stage(svc, target, 'a', []).status, 'applied')
  const second = stage(svc, target, 'b', [])
  assert.equal(second.status, 'refused')
  assert.equal(second.reason, 'target-invalid-or-used')
  assert.equal(state.text, 'SENTINEL_KEEPa tail', 'second stage must not mutate')
})

test('more than one file refuses as one-png-only (and consumes the target)', () => {
  const { state, make } = makeWorld()
  const svc = make()
  const target = svc.getServices().conversation.captureDraftTarget(state.binding)
  const result = stage(svc, target, 'x', [png(), png()])
  assert.equal(result.status, 'refused')
  assert.equal(result.reason, 'one-png-only')
  assert.deepEqual(state.calls, [])
  assert.equal(stage(svc, target, 'x', [png()]).reason, 'target-invalid-or-used')
})

// --- image budget math (count + bytes) from resolved attachments ---

test('image count budget exhaustion refuses before any mutation', () => {
  const { state, make } = makeWorld({ limits: { maxImageBytes: 1024 * 1024, maxImagesPerMessage: 2, maxMessageImageBytes: 4 * 1024 * 1024, mediaTypes: ['image/png'] } })
  state.attachments.set('pre-1', { id: 'pre-1', kind: 'image', file: png(new Uint8Array(16)) })
  state.attachments.set('pre-2', { id: 'pre-2', kind: 'image', file: png(new Uint8Array(16)) })
  state.order = ['pre-1', 'pre-2']
  const svc = make()
  const target = svc.getServices().conversation.captureDraftTarget(state.binding)
  const result = stage(svc, target, 'x', [png()])
  assert.equal(result.status, 'refused')
  assert.equal(result.reason, 'image-refused:attachment-count-exhausted')
  assert.deepEqual(state.calls, [])
})

test('image byte budget exhaustion (existing image bytes + new size) refuses with the remaining-bytes detail', () => {
  const { state, make } = makeWorld({ limits: { maxImageBytes: 1024 * 1024, maxImagesPerMessage: 3, maxMessageImageBytes: 20, mediaTypes: ['image/png'] } })
  state.attachments.set('pre-1', { id: 'pre-1', kind: 'image', file: png(new Uint8Array(16)) })
  state.order = ['pre-1'] // remainingBytes = 20 - 16 = 4 < png size 8
  const svc = make()
  const target = svc.getServices().conversation.captureDraftTarget(state.binding)
  const result = stage(svc, target, 'x', [png()])
  assert.equal(result.status, 'refused')
  assert.equal(result.reason, 'image-invalid:size:8>remainingBytes:4')
  assert.deepEqual(state.calls, [])
})

// --- unavailable official state: loud, not silent ---

test('missing retained scope: input.for throws and captureDraftTarget propagates the official error', () => {
  const { state, make } = makeWorld({ retained: false })
  const svc = make()
  assert.throws(() => svc.getServices().conversation.captureDraftTarget(state.binding), /conversation\.input\.for requires a retained Session scope/)
})

test('missing imageLimits projection fails closed loudly at capture (even text-only)', () => {
  const { state, make } = makeWorld()
  state.limits = undefined
  const svc = make()
  // The live record itself is incomplete, so no target can even be captured.
  assert.throws(() => svc.getServices().conversation.captureDraftTarget(state.binding), /DRAFT_UNAVAILABLE/)
})

test('limits present but mediaTypes absent: PNG fails closed at stage, text-only still applies', () => {
  const { state, make } = makeWorld({ limits: { maxImageBytes: 1024 * 1024, maxImagesPerMessage: 2, maxMessageImageBytes: 2 * 1024 * 1024 } })
  const svc = make()
  const target = svc.getServices().conversation.captureDraftTarget(state.binding)
  const result = stage(svc, target, 'x', [png()])
  assert.equal(result.status, 'refused')
  assert.equal(result.reason, 'fail-closed:imageLimits.mediaTypes')
  const target2 = svc.getServices().conversation.captureDraftTarget(state.binding)
  assert.equal(stage(svc, target2, 'note', []).status, 'applied')
})

test('attachment resolution mismatch makes captureDraftTarget throw attachment-state-unavailable', () => {
  const { state, make } = makeWorld({ resolveOverride: () => [] })
  state.attachments.set('pre-1', { id: 'pre-1', kind: 'image', file: png(new Uint8Array(16)) })
  state.order = ['pre-1']
  const svc = make()
  assert.throws(() => svc.getServices().conversation.captureDraftTarget(state.binding), /attachment-state-unavailable/)
})

test('dispose() makes captureDraftTarget throw plugin-inactive', () => {
  const { state, make } = makeWorld()
  const svc = make()
  svc.dispose()
  assert.throws(() => svc.getServices().conversation.captureDraftTarget(state.binding), /plugin-inactive/)
})

test('phase locked (adjudicating) at stage time refuses with zero mutation', () => {
  const { state, make } = makeWorld()
  const svc = make()
  const target = svc.getServices().conversation.captureDraftTarget(state.binding)
  state.phase = 'adjudicating'
  const result = stage(svc, target, 'x', [png()])
  assert.equal(result.status, 'refused')
  assert.equal(result.reason, 'phase-locked:adjudicating')
  assert.deepEqual(state.calls, [])
})

// --- forbidden surfaces are never touched ---

test('the facade never calls send/submit/prompt/upload/setDraft/persistDraft on any seam', () => {
  const { state, make } = makeWorld()
  const forbiddenOnActions = ['setDraft', 'persistDraft', 'submit']
  for (const name of forbiddenOnActions) state.actionsRef[name] = () => { throw new Error(`forbidden:actions.${name}`) }
  const forbiddenOnConversation = ['send', 'sendSession', 'prompt', 'upload', 'updateQueue']
  for (const name of forbiddenOnConversation) state.conversationRef[name] = () => { throw new Error(`forbidden:conversation.${name}`) }
  const svc = make()
  const target = svc.getServices().conversation.captureDraftTarget(state.binding)
  const result = stage(svc, target, 'safe text', [png()])
  assert.equal(result.status, 'applied')
})

// --- draftResultMessage distinguishes applied/partial/refused honestly ---

test('draftResultMessage: applied+sequential names the sequential interface and no send/upload', () => {
  const message = draftResultMessage({ status: 'applied', atomicity: 'sequential' }, true)
  assert.ok(message.includes('顺序接口'))
  assert.ok(message.includes('PNG'))
  assert.ok(message.includes('未发送'))
})

test('draftResultMessage: partial reports possible partial change, never atomic/unchanged claims', () => {
  const message = draftResultMessage({ status: 'partial', detail: 'add-attachments-threw:compensated', addedAttachmentIds: ['att-1'] }, true)
  assert.ok(message.includes('部分变化'))
  assert.ok(message.includes('add-attachments-threw:compensated'))
  assert.ok(message.includes('不会自动补投'))
  assert.ok(!message.includes('原子'))
  assert.ok(!message.includes('未发生变化'))
})

test('draftResultMessage: refused carries the status/reason and no applied language', () => {
  const message = draftResultMessage({ status: 'refused', reason: 'reentrant-add:navigation-changed', releasedAttachmentIds: ['att-1'] }, true)
  assert.ok(message.includes('refused/reentrant-add:navigation-changed'))
  assert.ok(!message.includes('已加入'))
  assert.ok(!message.includes('部分变化'))
})

test('draftResultMessage: hypothetical non-sequential applied result gets the plain-append copy', () => {
  const message = draftResultMessage({ status: 'applied', atomicity: 'other' }, false)
  assert.ok(message.includes('追加'))
  assert.ok(!message.includes('顺序接口'))
})

// --- resource-owner regression: the controller that CREATED a draft must be
// --- the one releasing it, even if services.conversation swaps synchronously
// --- inside createDrafts (singleton bridge resolves facts() per call).

test('REGRESSION (expected RED): draft created by ORIGINAL controller is released by the ORIGINAL controller, never a replacement', () => {
  const { state, make } = makeWorld()
  const original = state.conversationRef
  const originalReleases = []
  original.releaseDraftAttachment = id => { originalReleases.push(id); state.calls.push({ op: 'releaseDraftAttachment', id }) }
  const replacementReleases = []
  const replacement = {
    ...original,
    releaseDraftAttachment: id => { replacementReleases.push(id) },
  }
  const originalCreate = original.createDrafts.bind(original)
  original.createDrafts = (sessionId, files, uploadImages) => {
    const drafts = originalCreate(sessionId, files, uploadImages)
    // Synchronous reentrant swap: services now expose a different controller.
    state.conversationRef = replacement
    return drafts
  }
  const svc = make()
  const target = svc.getServices().conversation.captureDraftTarget(state.binding)
  const result = stage(svc, target, 'x', [png()])
  // The re-gate legitimately refuses (binding token is keyed per conversation,
  // so the swap looks like a re-bind) — but the CREATED id is owned by the
  // ORIGINAL controller: only it may release.
  assert.equal(result.status, 'refused')
  assert.equal(result.reason, 'reentrant-create:binding-changed')
  assert.deepEqual(result.releasedAttachmentIds, ['att-1'])
  assert.deepEqual(originalReleases, ['att-1'], 'the creating controller must release its own draft')
  assert.deepEqual(replacementReleases, [], 'a replacement controller must never be asked to release an id it does not own')
})

test('unchanged owner: normal net-zero release goes to the same controller that created the draft', () => {
  const { state, make } = makeWorld()
  const original = state.conversationRef
  const releases = []
  original.releaseDraftAttachment = id => { releases.push(id); state.calls.push({ op: 'releaseDraftAttachment', id }) }
  const svc = make()
  const target = svc.getServices().conversation.captureDraftTarget(state.binding)
  // Locked admission: addAttachments false ⇒ bridge releases the owned draft.
  state.actionsRef.addAttachments = () => false
  const result = stage(svc, target, 'x', [png()])
  assert.equal(result.status, 'refused')
  assert.equal(result.reason, 'attachments-locked')
  assert.deepEqual(result.releasedAttachmentIds, ['att-1'])
  assert.deepEqual(releases, ['att-1'])
  assert.equal(state.conversationRef, original, 'no swap occurred')
})
