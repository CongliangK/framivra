// Unit tests for the official-687 draft bridge (src/client/official-draft.mjs).
// Mocks mirror the OFFICIAL contract exactly:
//   - InputActions.removeAttachment(id) returns VOID (contract input.ts:264);
//     removal is verified through the authoritative attachmentIds readback.
//   - facade addAttachments publishes synchronously; mock subscribers can
//     flip phase / bump draftRev mid-call, exercising reentrant refusals.
//   - ConversationController.createDrafts / releaseDraftAttachment may throw.
import test from 'node:test'
import assert from 'node:assert/strict'
import { createDraftBridge, bridgeWiring, DRAFT_BRIDGE_SPEC, APPEND_ATOMICITY } from '../src/client/official-draft.mjs'

const PNG_BYTES = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])
const png = (bytes = PNG_BYTES) => new File([bytes], 'frame.png', { type: 'image/png' })

const baseLimits = () => ({ maxBytes: 10 * 1024 * 1024, remainingImages: 9, remainingBytes: 20 * 1024 * 1024, mediaTypes: ['image/png', 'image/jpeg'] })

function makeLive(overrides = {}) {
  const live = {
    active: true,
    session: { sessionId: 's1', binding: { sessionId: 's1' }, navigation: 'main', cwd: '/w' },
    input: { draftRev: 7, phase: 'plain', attachmentIds: ['old-1'] },
    imageLimits: baseLimits(),
    onAdd: undefined,
    removeActuallyRemoves: true,
    ...overrides,
  }
  live.actions = {
    captureInsertion: () => ({ start: 3, end: 6, draftRev: live.input.draftRev }),
    insertText: (text, span) => {
      if (live.input.phase !== 'plain' || span.draftRev !== live.input.draftRev) return false
      live.input.draftRev += 1
      return true
    },
    // Official facade: appends ids, then publish() — the onAdd subscriber hook
    // runs INSIDE the call (synchronous), matching reentrant publish semantics.
    addAttachments: ids => {
      if (live.input.phase !== 'plain') return false
      live.input.attachmentIds = [...live.input.attachmentIds, ...ids]
      live.onAdd?.(ids)
      return true
    },
    // Official contract: returns void; default mock performs the removal,
    // removeActuallyRemoves=false simulates a locked host that ignores it.
    removeAttachment: id => {
      if (live.input.phase !== 'plain') return
      if (!live.removeActuallyRemoves) return
      live.input.attachmentIds = live.input.attachmentIds.filter(candidate => candidate !== id)
    },
    ...overrides.actions,
  }
  return live
}

function makeConversation(overrides = {}) {
  let seq = 0
  const controller = {
    calls: [],
    createThrows: false,
    wrongKind: false,
    releaseThrowsOn: undefined,
    createDrafts(sessionId, files) {
      this.calls.push({ op: 'createDrafts', sessionId, files })
      if (controller.createThrows) throw new Error('boom')
      if (controller.wrongKind) return files.map(() => ({ id: `bad-${++seq}`, kind: 'file' }))
      return files.map(() => ({ id: `new-${++seq}`, kind: 'image' }))
    },
    releaseDraftAttachment(id) {
      this.calls.push({ op: 'releaseDraftAttachment', id })
      if (controller.releaseThrowsOn === id) throw new Error('release boom')
    },
    ...overrides,
  }
  return controller
}

const freshBridge = (liveOverrides = {}, conversationOverrides = {}, log) => {
  const live = makeLive(liveOverrides)
  const conversation = makeConversation(conversationOverrides)
  const bridge = createDraftBridge({ readLive: () => live, conversation, log })
  return { bridge, live, conversation }
}

test('spec markers are exported and sequential; wiring reports completeness', () => {
  assert.equal(DRAFT_BRIDGE_SPEC, 'official-687-draft-bridge/1')
  assert.equal(APPEND_ATOMICITY, 'sequential')
  const { live } = freshBridge()
  const report = bridgeWiring(() => live)
  assert.equal(report.complete, true)
  assert.deepEqual(report.missing, [])
})

test('captureIntent collapses the caret span to zero width and freezes the intent', () => {
  const { bridge } = freshBridge()
  const intent = bridge.captureIntent()
  assert.deepEqual(intent.span, { start: 3, end: 3, draftRev: 7 })
  assert.ok(Object.isFrozen(intent))
  assert.equal(intent.draftRev, 7)
})

test('appendTextWithImage appends text then image id in order (plain happy path)', () => {
  const { bridge, live, conversation } = freshBridge()
  const intent = bridge.captureIntent()
  const result = bridge.appendTextWithImage(intent, 'caption', png())
  assert.equal(result.status, 'appended')
  assert.equal(result.atomicity, 'sequential')
  assert.equal(result.textApplied, true)
  assert.deepEqual(result.addedAttachmentIds, ['new-1'])
  assert.deepEqual(live.input.attachmentIds, ['old-1', 'new-1'])
  assert.deepEqual(conversation.calls.map(call => call.op), ['createDrafts'])
  assert.equal(live.input.draftRev, 8)
})

test('image-only append lands the attachment and never inserts text', () => {
  const { bridge, live } = freshBridge()
  const intent = bridge.captureIntent()
  const result = bridge.appendTextWithImage(intent, '', png())
  assert.equal(result.status, 'appended')
  assert.equal(result.textApplied, false)
  assert.deepEqual(live.input.attachmentIds, ['old-1', 'new-1'])
  assert.equal(live.input.draftRev, 7)
})

test('stale draftRev is refused with zero mutation (no CAS retry)', () => {
  const { bridge, live, conversation } = freshBridge()
  const intent = bridge.captureIntent()
  live.input.draftRev = 8
  const result = bridge.appendTextWithImage(intent, 'x', png())
  assert.equal(result.status, 'refused')
  assert.equal(result.reason, 'draft-changed')
  assert.deepEqual(conversation.calls, [])
  assert.deepEqual(live.input.attachmentIds, ['old-1'])
})

test('binding / sessionId / navigation / cwd changes each refuse with zero mutation', () => {
  for (const [field, value] of [['binding', { sessionId: 's2' }], ['sessionId', 's2'], ['navigation', 'other'], ['cwd', '/else']]) {
    const { bridge, live, conversation } = freshBridge()
    const intent = bridge.captureIntent()
    live.session[field] = value
    const result = bridge.appendTextWithImage(intent, 'x', png())
    assert.equal(result.status, 'refused', field)
    assert.ok(result.reason.startsWith(`${field === 'sessionId' ? 'session' : field}-changed`), field)
    assert.deepEqual(conversation.calls, [], field)
    assert.deepEqual(live.input.attachmentIds, ['old-1'], field)
  }
})

test('inactive session refuses with zero mutation', () => {
  const { bridge, live, conversation } = freshBridge()
  const intent = bridge.captureIntent()
  live.active = false
  const result = bridge.appendTextWithImage(intent, 'x', png())
  assert.equal(result.reason, 'session-inactive')
  assert.deepEqual(conversation.calls, [])
})

test('phase-locked (adjudicating/submitting/claimed) refuses with zero mutation', () => {
  for (const phase of ['adjudicating', 'submitting', 'claimed']) {
    const { bridge, live, conversation } = freshBridge()
    const intent = bridge.captureIntent()
    live.input.phase = phase
    const result = bridge.appendTextWithImage(intent, 'x', png())
    assert.equal(result.status, 'refused', phase)
    assert.equal(result.reason, `phase-locked:${phase}`)
    assert.deepEqual(conversation.calls, [], phase)
  }
})

test('replaced InputActions identity (same binding scalars) is refused as actions-changed', () => {
  const { bridge, live, conversation } = freshBridge()
  const intent = bridge.captureIntent()
  // Re-staged session: a NEW actions object with identical scalar facts.
  live.actions = makeLive().actions
  const result = bridge.appendTextWithImage(intent, 'x', png())
  assert.equal(result.status, 'refused')
  assert.equal(result.reason, 'actions-changed')
  assert.deepEqual(conversation.calls, [])
})

test('REENTRANT publish: addAttachments subscriber flips phase and bumps draftRev; insertText refuses, compensation verified by readback', () => {
  const { bridge, live, conversation } = freshBridge()
  live.onAdd = () => {
    live.input.phase = 'adjudicating'
    live.input.draftRev += 1
  }
  const intent = bridge.captureIntent()
  const result = bridge.appendTextWithImage(intent, 'caption', png())
  // Text could not land (post-add re-gate catches the reentrant lock before
  // insertText); the mock's locked removeAttachment ignores the call, so the
  // authoritative readback cannot prove removal — explicit partial, never refused/unchanged.
  assert.equal(result.status, 'partial')
  assert.equal(result.detail, 'reentrant-add:draft-changed:compensation-unverified')
  assert.deepEqual(result.addedAttachmentIds, ['new-1'])
  assert.deepEqual(live.input.attachmentIds, ['old-1', 'new-1'])
  assert.deepEqual(conversation.calls.map(call => call.op), ['createDrafts'])
})

test('REENTRANT publish with an effective removal: net-zero compensated refused', () => {
  // Same reentrant lock, but the host performs the removal anyway: the
  // readback proves the id gone and the release succeeds -> net-zero refused.
  const { bridge, live, conversation } = freshBridge()
  live.onAdd = () => {
    live.input.phase = 'adjudicating'
    live.input.draftRev += 1
  }
  live.actions.removeAttachment = id => {
    live.input.attachmentIds = live.input.attachmentIds.filter(candidate => candidate !== id)
  }
  const intent = bridge.captureIntent()
  const result = bridge.appendTextWithImage(intent, 'caption', png())
  assert.equal(result.status, 'refused')
  assert.equal(result.reason, 'reentrant-add:draft-changed')
  assert.equal(result.textApplied, false)
  assert.deepEqual(result.releasedAttachmentIds, ['new-1'])
  assert.deepEqual(live.input.attachmentIds, ['old-1'])
  assert.deepEqual(conversation.calls.map(call => call.op), ['createDrafts', 'releaseDraftAttachment'])
})

test('compensation removeAttachment throwing is partial(compensation-threw)', () => {
  const { bridge, live } = freshBridge()
  live.actions.insertText = () => false
  live.actions.removeAttachment = () => { throw new Error('locked') }
  const intent = bridge.captureIntent()
  const result = bridge.appendTextWithImage(intent, 'caption', png())
  assert.equal(result.status, 'partial')
  assert.equal(result.detail, 'insert-refused-compensated:compensation-threw')
  assert.deepEqual(result.addedAttachmentIds, ['new-1'])
})

test('happy path: addAttachments only carries the new id; original attachments preserved', () => {
  const { bridge, live } = freshBridge()
  const seen = []
  const originalAdd = live.actions.addAttachments
  live.actions.addAttachments = ids => { seen.push([...ids]); return originalAdd(ids) }
  const intent = bridge.captureIntent()
  bridge.appendTextWithImage(intent, 'note', png())
  assert.deepEqual(seen, [['new-1']])
  assert.deepEqual(live.input.attachmentIds, ['old-1', 'new-1'])
})

test('locked addAttachments (returns false) releases the draft and refuses', () => {
  const { bridge, live, conversation } = freshBridge()
  live.actions.addAttachments = () => false
  const intent = bridge.captureIntent()
  const result = bridge.appendTextWithImage(intent, 'x', png())
  assert.equal(result.status, 'refused')
  assert.equal(result.reason, 'attachments-locked')
  assert.deepEqual(result.releasedAttachmentIds, ['new-1'])
  assert.deepEqual(conversation.calls.map(call => call.op), ['createDrafts', 'releaseDraftAttachment'])
})

test('release throwing after locked addAttachments is partial, never a clean refused', () => {
  const { bridge, live, conversation } = freshBridge()
  conversation.releaseThrowsOn = 'new-1'
  live.actions.addAttachments = () => false
  const intent = bridge.captureIntent()
  const result = bridge.appendTextWithImage(intent, 'x', png())
  assert.equal(result.status, 'partial')
  assert.equal(result.detail, 'release-threw:attachments-locked')
})

test('non-image createDrafts result is released immediately (never a background file upload)', () => {
  const { bridge, live, conversation } = freshBridge()
  conversation.wrongKind = true
  const intent = bridge.captureIntent()
  const result = bridge.appendTextWithImage(intent, 'x', png())
  assert.equal(result.status, 'refused')
  assert.equal(result.reason, 'create-drafts:not-one-image')
  assert.deepEqual(conversation.calls.map(call => call.op), ['createDrafts', 'releaseDraftAttachment'])
  assert.equal(conversation.calls[1].id, 'bad-1')
  assert.deepEqual(live.input.attachmentIds, ['old-1'])
})

test('createDrafts throwing is PARTIAL (half-creation unknown), never refused', () => {
  const { bridge, live, conversation } = freshBridge()
  conversation.createThrows = true
  const intent = bridge.captureIntent()
  const result = bridge.appendTextWithImage(intent, 'x', png())
  assert.equal(result.status, 'partial')
  assert.equal(result.detail, 'create-drafts-threw')
  assert.equal(result.textApplied, false)
})

test('cleanup release throwing after not-one-image is partial', () => {
  const { bridge, live, conversation } = freshBridge()
  conversation.wrongKind = true
  conversation.releaseThrowsOn = 'bad-1'
  const intent = bridge.captureIntent()
  const result = bridge.appendTextWithImage(intent, 'x', png())
  assert.equal(result.status, 'partial')
  assert.equal(result.detail, 'cleanup-release-threw:not-one-image')
})

test('image validation fails closed: not a File / wrong MIME / empty / oversize', () => {
  const { bridge } = freshBridge()
  const i = bridge.captureIntent()
  assert.equal(bridge.appendTextWithImage(i, '', new Blob(['x'], { type: 'image/png' })).reason, 'image-invalid:not-a-file')
  const j = bridge.captureIntent()
  assert.equal(bridge.appendTextWithImage(j, '', new File([PNG_BYTES], 'f.jpg', { type: 'image/jpeg' })).reason, 'image-invalid:mime:image/jpeg')
  const k = bridge.captureIntent()
  assert.equal(bridge.appendTextWithImage(k, '', new File([], 'empty.png', { type: 'image/png' })).reason, 'image-invalid:empty')
  const { bridge: b2 } = freshBridge({ imageLimits: { maxBytes: 4, remainingImages: 9, remainingBytes: 100, mediaTypes: ['image/png'] } })
  const i2 = b2.captureIntent()
  assert.equal(b2.appendTextWithImage(i2, '', png()).reason, 'image-invalid:size:8>4')
})

test('imageLimits must be the FULL normalized budget: each missing/invalid field fails closed', () => {
  const cases = [
    [{ remainingImages: 9, remainingBytes: 10, mediaTypes: ['image/png'] }, 'fail-closed:imageLimits.maxBytes'],
    [{ maxBytes: 10, remainingBytes: 10, mediaTypes: ['image/png'] }, 'fail-closed:imageLimits.remainingImages'],
    [{ maxBytes: 10, remainingImages: 1, mediaTypes: ['image/png'] }, 'fail-closed:imageLimits.remainingBytes'],
    [{ maxBytes: 10, remainingImages: 1, remainingBytes: 10 }, 'fail-closed:imageLimits.mediaTypes'],
    [{ maxBytes: 10, remainingImages: 1, remainingBytes: 10, mediaTypes: ['image/jpeg'] }, 'fail-closed:imageLimits.mediaTypes'],
    [{ maxBytes: 100, remainingImages: 0, remainingBytes: 10, mediaTypes: ['image/png'] }, 'image-refused:attachment-count-exhausted'],
    [{ maxBytes: 100, remainingImages: 1, remainingBytes: 4, mediaTypes: ['image/png'] }, 'image-invalid:size:8>remainingBytes:4'],
  ]
  for (const [limits, expected] of cases) {
    const { bridge } = freshBridge({ imageLimits: limits })
    const intent = bridge.captureIntent()
    const result = bridge.appendTextWithImage(intent, '', png())
    assert.equal(result.status, 'refused', JSON.stringify(limits))
    assert.equal(result.reason, expected, JSON.stringify(limits))
  }
})

test('live-record field gaps fail closed with zero mutation', () => {
  for (const field of ['active', 'session.binding', 'input.phase', 'actions.insertText', 'imageLimits']) {
    const { bridge, live, conversation } = freshBridge()
    let target = live
    const parts = field.split('.')
    for (const part of parts.slice(0, -1)) target = target[part]
    target[parts.at(-1)] = undefined
    const intent = bridge.captureIntent()
    const result = bridge.appendTextWithImage(intent, 'x', png())
    assert.equal(result.status, 'refused', field)
    assert.ok(result.reason.startsWith('fail-closed:'), field)
    assert.deepEqual(conversation.calls, [], field)
  }
})

test('one-use: any append outcome consumes the intent', () => {
  const { bridge } = freshBridge()
  const intent = bridge.captureIntent()
  const first = bridge.appendTextWithImage(intent, 'x', png())
  assert.equal(first.status, 'appended')
  const second = bridge.appendTextWithImage(intent, 'x', png())
  assert.equal(second.status, 'refused')
  assert.equal(second.reason, 'intent-used')
})

test('the bridge never invokes send/submit/prompt/setDraft/persistDraft', () => {
  const { bridge, live } = freshBridge()
  for (const name of ['send', 'submit', 'prompt', 'setDraft', 'persistDraft']) live.actions[name] = () => { throw new Error(`forbidden:${name}`) }
  const intent = bridge.captureIntent()
  const result = bridge.appendTextWithImage(intent, 'safe', png())
  assert.equal(result.status, 'appended')
})

test('text-only append is one CAS insert with no retry', () => {
  const { bridge } = freshBridge()
  const intent = bridge.captureIntent()
  const result = bridge.appendText(intent, 'plain')
  assert.equal(result.status, 'appended')
  assert.equal(result.textApplied, true)
  const { bridge: b2, live } = freshBridge()
  const i2 = b2.captureIntent()
  live.input.draftRev += 1
  const refusedResult = b2.appendText(i2, 'plain')
  assert.equal(refusedResult.status, 'refused')
  assert.equal(refusedResult.reason, 'draft-changed')
})

// --- round 4: forced reentrancy, state-read, and compensation-identity tests ---

test('REENTRANT createDrafts: subscriber navigates away — fresh gate refuses before addAttachments, net-zero release', () => {
  const { bridge, live, conversation } = freshBridge()
  const originalCreate = conversation.createDrafts.bind(conversation)
  const adds = []
  const originalAdd = live.actions.addAttachments
  live.actions.addAttachments = ids => { adds.push([...ids]); return originalAdd(ids) }
  conversation.createDrafts = (sessionId, files) => {
    const drafts = originalCreate(sessionId, files)
    live.session.navigation = 'other' // synchronous subscriber inside createDrafts
    return drafts
  }
  const intent = bridge.captureIntent()
  const result = bridge.appendTextWithImage(intent, 'caption', png())
  assert.equal(result.status, 'refused')
  assert.equal(result.reason, 'reentrant-create:navigation-changed')
  assert.deepEqual(result.releasedAttachmentIds, ['new-1'])
  assert.deepEqual(adds, [], 'addAttachments must never run after a failed re-gate')
  assert.deepEqual(live.input.attachmentIds, ['old-1'])
  assert.deepEqual(conversation.calls.map(call => call.op), ['createDrafts', 'releaseDraftAttachment'])
})

test('REENTRANT addAttachments: navigation flips WITHOUT draftRev bump — same-span re-gate refuses, compensation via ORIGINAL actions', () => {
  const { bridge, live, conversation } = freshBridge()
  let inserted = 0
  const originalInsert = live.actions.insertText
  live.actions.insertText = (text, span) => { inserted += 1; return originalInsert(text, span) }
  live.onAdd = () => { live.session.navigation = 'other' }
  const intent = bridge.captureIntent()
  const result = bridge.appendTextWithImage(intent, 'caption', png())
  assert.equal(result.status, 'refused')
  assert.equal(result.reason, 'reentrant-add:navigation-changed')
  assert.deepEqual(result.releasedAttachmentIds, ['new-1'])
  assert.equal(inserted, 0, 'insertText must never run after a failed post-add re-gate')
  assert.deepEqual(live.input.attachmentIds, ['old-1'])
  assert.deepEqual(conversation.calls.map(call => call.op), ['createDrafts', 'releaseDraftAttachment'])
})

test('COMPENSATION IDENTITY: re-staged session with EMPTY ids is never read as successful compensation', () => {
  const { bridge, live, conversation } = freshBridge()
  const oldRemoveCalls = []
  const oldRemove = live.actions.removeAttachment
  live.actions.removeAttachment = id => { oldRemoveCalls.push(id); return oldRemove(id) }
  live.onAdd = () => {
    const fresh = makeLive({ input: { draftRev: 7, phase: 'plain', attachmentIds: [] } })
    live.session = fresh.session
    live.actions = fresh.actions
  }
  const intent = bridge.captureIntent()
  const result = bridge.appendTextWithImage(intent, 'caption', png())
  assert.equal(result.status, 'partial')
  assert.equal(result.detail, 'reentrant-add:binding-changed:compensation-unverified:binding-changed')
  assert.deepEqual(result.addedAttachmentIds, ['new-1'], 'id stays owned and reported')
  assert.deepEqual(oldRemoveCalls, [], 'neither the old nor the new actions face may be touched')
  assert.deepEqual(conversation.calls.map(call => call.op), ['createDrafts'], 'no release without verified removal')
})

test('throwing readLive: pre-mutation refuses; post-attach throw is partial with the id reported and no blind cleanup', () => {
  let throwing = false
  const live = makeLive()
  const conversation = makeConversation()
  const readLive = () => { if (throwing) throw new Error('live gone'); return live }
  const bridge = createDraftBridge({ readLive, conversation })
  throwing = true
  assert.equal(bridge.captureIntent(), null, 'unreadable live record yields no intent')
  throwing = false
  const intent = bridge.captureIntent()
  throwing = true
  const pre = bridge.appendTextWithImage(intent, 'x', png())
  assert.equal(pre.status, 'refused')
  assert.equal(pre.reason, 'read-live-threw')
  assert.deepEqual(conversation.calls, [])
  throwing = false
  // Post-attach: addAttachments' publish makes the live record unreadable.
  const intent2 = bridge.captureIntent()
  live.onAdd = () => { throwing = true }
  let removed = 0
  const originalRemove = live.actions.removeAttachment
  live.actions.removeAttachment = id => { removed += 1; return originalRemove(id) }
  const post = bridge.appendTextWithImage(intent2, 'caption', png())
  assert.equal(post.status, 'partial')
  assert.equal(post.detail, 'post-add-state-unreadable:read-live-threw:compensation-unverified:read-live-threw')
  assert.deepEqual(post.addedAttachmentIds, ['new-1'])
  assert.equal(removed, 0, 'compensation must not act when readback is impossible')
  assert.deepEqual(conversation.calls.map(call => call.op), ['createDrafts'], 'no release without verified state')
})

test('addAttachments THROWING with the id NOT landed: provable release, still explicit partial', () => {
  const { bridge, live, conversation } = freshBridge()
  live.actions.addAttachments = () => { throw new Error('publish boom') }
  const intent = bridge.captureIntent()
  const result = bridge.appendTextWithImage(intent, 'x', png())
  assert.equal(result.status, 'partial')
  assert.equal(result.detail, 'add-attachments-threw:released')
  assert.deepEqual(result.releasedAttachmentIds, ['new-1'])
  assert.deepEqual(conversation.calls.map(call => call.op), ['createDrafts', 'releaseDraftAttachment'])
})

test('addAttachments THROWING after the id landed (synchronous publish first): compensated but STILL partial', () => {
  const { bridge, live, conversation } = freshBridge()
  const ids = live.input.attachmentIds
  live.actions.addAttachments = newIds => {
    live.input.attachmentIds = [...ids, ...newIds]
    throw new Error('publish boom after landing')
  }
  const intent = bridge.captureIntent()
  const result = bridge.appendTextWithImage(intent, 'x', png())
  assert.equal(result.status, 'partial')
  assert.equal(result.detail, 'add-attachments-threw:compensated')
  assert.deepEqual(result.releasedAttachmentIds, ['new-1'])
  assert.deepEqual(live.input.attachmentIds, ['old-1'], 'compensation removed the landed id via original actions')
  assert.deepEqual(conversation.calls.map(call => call.op), ['createDrafts', 'releaseDraftAttachment'])
})
