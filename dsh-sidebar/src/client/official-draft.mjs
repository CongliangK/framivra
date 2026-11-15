// Plugin-owned safe draft-append bridge for the OFFICIAL DSH composer
// (dsh-v0.2.2-rc.1) of @deepseek-ai/dsh-client-ui-conversation
// (lib/types/client/service.d.ts; the historical target dsh-v0.2.1-rc.2 =
// 687f282e declared the same seam without the uploadImages parameter).
//
// ATOMICITY GAP (public API, recorded deliberately): the official composer has
// NO single atomic text+image append API. This bridge sequences the public
// calls createDrafts -> addAttachments -> insertText and reports every outcome
// exactly: 'appended', 'partial', or 'refused'. It never claims atomicity and
// never reports a possibly-mutated draft as unchanged.
//
// REENTRANCY: the calls run without an await, but each official facade call
// (createDrafts, addAttachments, publish()) can run synchronous subscribers
// that navigate, re-bind, or bump draftRev BETWEEN our calls. The bridge
// therefore re-reads and re-gates the live record after createDrafts and
// after addAttachments, before the next call, with the SAME captured span —
// no recapture, no retry. A reentrant change compensates the bridge-owned
// attachment id or reports 'partial'; it is never silently absorbed.
//
// Official public seams consumed (all injected, never imported):
//   - InputActions (every session-scope slot entry receives it):
//       captureInsertion(): TokenSpan {start, end, draftRev}
//       insertText(text, span): boolean — false on draftRev mismatch or a
//         locked admission phase; replaces the span's characters, so only a
//         ZERO-WIDTH collapsed span is safe when nothing must be replaced.
//       addAttachments(ids): boolean — false during adjudicating/submitting;
//         a THROW does not prove the ids were not attached (synchronous
//         publish may have landed them first), so a throw is 'partial' with
//         conservative identity-guarded cleanup, never a clean refusal.
//       removeAttachment(id): VOID (official contract) — success cannot be
//         read from the return value; removal is verified by re-reading the
//         authoritative input.attachmentIds, guarded so a DIFFERENT
//         session's empty id list is never mistaken for successful
//         compensation.
//     The actions object identity is pinned at capture time: a re-staged
//     session whose InputActions face was replaced is refused/compensated
//     even when every scalar fact still matches.
//   - ConversationController (publicly exported class, client/index.ts):
//       createDrafts(sessionId, files, uploadImages) — may throw or
//       half-create; a throw is reported 'partial' (mutation state
//       unknown), never 'refused'. The third official parameter
//       (dsh-v0.2.2-rc.1 lib/types/client/service.d.ts: "whether the
//       Session accepts image uploads") is passed as literal false by this
//       bridge: an image draft then sends its bytes with the prompt, while
//       a truthy value would start a service-owned background upload the
//       bridge must never leave behind. An image-MIME File becomes an
//       image draft; any other MIME becomes a file draft with a background
//       upload, which this bridge must never leave behind, so a non-image
//       result is released immediately.
//       releaseDraftAttachment(id) — may throw; a throw during cleanup is
//       reported 'partial' with a detail, never a clean 'refused'.
//
// IMAGE VALIDATION SCOPE: this bridge checks File-ness, MIME type, and byte
// budgets only. It does NOT decode or verify PNG bytes; the caller owns
// passing a File whose bytes are already-verified PNG (Frame's decoder).
//
// Forbidden by design: send/submit/prompt/upload/setDraft (flattens reference
// chips)/persistDraft/DOM access/private host fields/CAS retry loops.
//
// All host facts arrive through the parent-injected readLive(). Each bridge
// step reads readLive() EXACTLY ONCE and validates that captured snapshot
// (never re-invokes and never lets a second throw escape):
//   - pre-mutation gaps/throws fail closed as 'refused' (zero host calls);
//   - post-mutation gaps/throws are 'partial' (mutation state unknown).
//
// imageLimits must be the parent-normalized budget object with ALL four
// fields: { maxBytes, remainingImages, remainingBytes, mediaTypes } —
// mediaTypes must include 'image/png', remainingImages >= 1, and
// file.size <= min(maxBytes, remainingBytes). Any gap fails closed.
//
// RESIDUAL 'partial' SEMANTICS (exact): the bridge reports 'partial' —
// never a clean 'refused' — whenever it cannot PROVE net-zero, namely when
// createDrafts throws (draft may be half-created), when any
// releaseDraftAttachment throws (draft may still be registered), when
// addAttachments throws (ids may have landed via synchronous publish), when
// a post-mutation readLive throws or is incomplete (state unknowable), when
// compensation cannot be verified under the same session/binding/actions
// identity, or when removeAttachment provably did not take effect. A
// 'refused(insert-refused-compensated)' / 'refused(reentrant-…)' means
// proven net-zero: the id was verified absent from the authoritative input
// state under the same identity AND its release returned.

export const DRAFT_BRIDGE_SPEC = 'official-687-draft-bridge/1'

/** Constant marker on every append result: the official API is sequential. */
export const APPEND_ATOMICITY = 'sequential'

/** Fields readLive() must supply; a gap in a pre-gate read fails closed. */
const LIVE_FIELDS = ['active', 'session', 'session.sessionId', 'session.binding', 'session.navigation', 'session.cwd', 'actions', 'actions.captureInsertion', 'actions.insertText', 'actions.addAttachments', 'actions.removeAttachment', 'input', 'input.draftRev', 'input.phase', 'input.attachmentIds', 'imageLimits']

const isObject = value => typeof value === 'object' && value !== null

const missingFields = live => {
  const missing = []
  for (const path of LIVE_FIELDS) {
    let value = live
    for (const part of path.split('.')) value = isObject(value) ? value[part] : undefined
    if (value === undefined || value === null) missing.push(path)
  }
  return missing
}

/**
 * Report which required public live-record fields are present, without
 * touching any host behavior. For parent wiring self-checks only.
 * @param {() => object|null} readLive
 * @returns {{ spec: string, complete: boolean, missing: string[] }}
 */
export function bridgeWiring(readLive) {
  let live = null
  try { live = readLive?.() ?? null } catch { live = null }
  const missing = missingFields(live)
  return { spec: DRAFT_BRIDGE_SPEC, complete: missing.length === 0, missing }
}

const refused = (reason, extra = {}) =>
  ({ status: 'refused', reason, atomicity: APPEND_ATOMICITY, textApplied: false, addedAttachmentIds: [], releasedAttachmentIds: [], ...extra })

const partial = (detail, extra = {}) =>
  ({ status: 'partial', atomicity: APPEND_ATOMICITY, textApplied: false, addedAttachmentIds: [], releasedAttachmentIds: [], detail, ...extra })

/**
 * Create the draft bridge.
 * @param {object} deps
 * @param {() => object|null} deps.readLive - parent-injected public live-record reader.
 * @param {{ createDrafts: Function, releaseDraftAttachment: Function }} deps.conversation
 *   - ConversationController handle (public methods only).
 * @param {{ warn?: Function }=} deps.log
 */
export function createDraftBridge({ readLive, conversation, log }) {
  // One-use tracking for frozen intents: the first append attempt (any
  // outcome past argument validation) consumes the intent permanently.
  const consumed = new WeakSet()
  const warn = message => { try { log?.warn?.(message) } catch { /* reporting must never throw */ } }

  // Read readLive() EXACTLY ONCE, validate the captured snapshot, and never
  // let a throw escape. The error string is PRE-mutation flavored; callers
  // in a post-mutation context convert it to 'partial'.
  const readLiveOnce = () => {
    let live
    try { live = readLive() } catch (error) {
      warn(`official-draft: readLive threw ${error}`)
      return { error: 'read-live-threw' }
    }
    const missing = missingFields(live)
    if (missing.length !== 0) {
      warn(`official-draft: fail closed, missing ${missing.join(',')}`)
      return { error: `fail-closed:${missing[0]}` }
    }
    return { live }
  }

  /**
   * Capture the one-use insertion intent at click time. Collapses the caret
   * span to zero width so a later insertText replaces no selected characters;
   * the insertion point sits at the captured selection start (not a tail
   * append — official insertText is caret/selection based). The intent pins
   * the InputActions object identity it was captured from.
   * @returns {object|null} frozen intent, or null when the live record is unreadable/incomplete.
   */
  function captureIntent() {
    const checked = readLiveOnce()
    if (checked.error) return null
    const { live } = checked
    let span
    try { span = live.actions.captureInsertion() } catch (error) {
      warn(`official-draft: captureInsertion threw ${error}`)
      return null
    }
    if (!isObject(span) || !Number.isSafeInteger(span.start) || !Number.isSafeInteger(span.end) || span.start < 0 || span.end < span.start) return null
    const intent = {
      spec: DRAFT_BRIDGE_SPEC,
      sessionId: live.session.sessionId,
      binding: live.session.binding,
      navigation: live.session.navigation,
      cwd: live.session.cwd,
      draftRev: live.input.draftRev,
      actionsToken: live.actions,
      span: Object.freeze({ start: span.start, end: span.start, draftRev: span.draftRev }),
    }
    return Object.freeze(intent)
  }

  // Staleness gate over a FRESH captured live snapshot, checked against the
  // frozen intent. `skipUsed` lets post-mutation re-gates reuse the same
  // (already consumed) intent without recapture.
  const gate = (intent, live, skipUsed) => {
    if (!isObject(intent) || intent.spec !== DRAFT_BRIDGE_SPEC) return 'intent-invalid'
    if (!skipUsed && consumed.has(intent)) return 'intent-used'
    if (live.active !== true) return 'session-inactive'
    if (live.session.sessionId !== intent.sessionId) return 'session-changed'
    if (live.session.binding !== intent.binding) return 'binding-changed'
    if (live.session.navigation !== intent.navigation) return 'navigation-changed'
    if (live.session.cwd !== intent.cwd) return 'cwd-changed'
    if (live.actions !== intent.actionsToken) return 'actions-changed'
    if (live.input.draftRev !== intent.draftRev) return 'draft-changed'
    if (live.input.phase !== 'plain') return `phase-locked:${String(live.input.phase)}`
    return null
  }

  // Identity guard for compensation readbacks: the snapshot must still belong
  // to the SAME session, binding, and InputActions face the intent captured,
  // so a new session's empty id list can never read as successful removal.
  const sameIdentity = (intent, live) => {
    if (live.session.sessionId !== intent.sessionId) return 'session-changed'
    if (live.session.binding !== intent.binding) return 'binding-changed'
    if (live.actions !== intent.actionsToken) return 'actions-changed'
    return null
  }

  const consume = intent => { if (isObject(intent)) consumed.add(intent) }

  // Validate the normalized four-field image budget. Returns a refuse reason
  // or null. Byte decoding is NOT performed here (caller owns PNG validity).
  const imageGate = (file, limits) => {
    if (!(typeof File === 'function' && file instanceof File)) return 'image-invalid:not-a-file'
    if (file.type !== 'image/png') return `image-invalid:mime:${String(file.type)}`
    if (file.size <= 0) return 'image-invalid:empty'
    const { maxBytes, remainingImages, remainingBytes, mediaTypes } = limits ?? {}
    if (!Number.isSafeInteger(maxBytes) || maxBytes <= 0) return 'fail-closed:imageLimits.maxBytes'
    if (!Number.isSafeInteger(remainingImages) || remainingImages < 0) return 'fail-closed:imageLimits.remainingImages'
    if (!Number.isSafeInteger(remainingBytes) || remainingBytes < 0) return 'fail-closed:imageLimits.remainingBytes'
    if (!Array.isArray(mediaTypes) || !mediaTypes.includes('image/png')) return 'fail-closed:imageLimits.mediaTypes'
    if (remainingImages < 1) return 'image-refused:attachment-count-exhausted'
    if (file.size > maxBytes) return `image-invalid:size:${file.size}>${maxBytes}`
    if (file.size > remainingBytes) return `image-invalid:size:${file.size}>remainingBytes:${remainingBytes}`
    return null
  }

  // Release a bridge-owned draft id that was never attached; a throw
  // converts the caller's clean refusal into an explicit partial.
  const releaseOwned = (id, refusal) => {
    try { conversation.releaseDraftAttachment(id) } catch (error) {
      warn(`official-draft: release of ${String(id)} threw ${error}`)
      return partial(`release-threw:${refusal}`)
    }
    return refused(refusal, { releasedAttachmentIds: [id] })
  }

  // Compensate the bridge-owned, already-ATTACHED id after the text step
  // failed (refused, threw, or re-gated). Uses the ORIGINAL actions face
  // pinned by the intent, with identity-guarded authoritative readbacks:
  // removal is proven only when the SAME session/binding/actions state no
  // longer lists the id. Returns refused (proven net-zero) or partial.
  const compensate = (intent, id, reason) => {
    // Pre-removal read: identity must hold, or the id's presence is unknowable.
    const before = readLiveOnce()
    if (before.error) return partial(`${reason}:compensation-unverified:${before.error}`, { addedAttachmentIds: [id] })
    const identity = sameIdentity(intent, before.live)
    if (identity !== null) return partial(`${reason}:compensation-unverified:${identity}`, { addedAttachmentIds: [id] })
    try { intent.actionsToken.removeAttachment(id) } catch (error) {
      warn(`official-draft: compensation removeAttachment threw ${error}`)
      return partial(`${reason}:compensation-threw`, { addedAttachmentIds: [id] })
    }
    // Post-removal readback: the id must be gone under the SAME identity.
    const after = readLiveOnce()
    if (after.error) return partial(`${reason}:compensation-unverified:${after.error}`, { addedAttachmentIds: [id] })
    const identityAfter = sameIdentity(intent, after.live)
    if (identityAfter !== null) return partial(`${reason}:compensation-unverified:${identityAfter}`, { addedAttachmentIds: [id] })
    if (after.live.input.attachmentIds.includes(id)) {
      warn('official-draft: attachment removal not provable from authoritative input state')
      return partial(`${reason}:compensation-unverified`, { addedAttachmentIds: [id] })
    }
    try { conversation.releaseDraftAttachment(id) } catch (error) {
      warn(`official-draft: compensated release threw ${error}`)
      return partial(`${reason}:release-threw`, { addedAttachmentIds: [] })
    }
    // Proven net-zero: id verified absent under the same identity AND released.
    return refused(reason, { releasedAttachmentIds: [id] })
  }

  // addAttachments threw: synchronous publish may have landed the ids first,
  // so no clean refusal is possible. Conservatively establish where the id
  // stands, clean it up when provably owned, and report 'partial' either way.
  const handleAddThrew = (intent, id) => {
    const checked = readLiveOnce()
    if (checked.error) {
      // State unknowable: touching the id could corrupt an attached draft.
      return partial(`add-attachments-threw:${checked.error}`, { addedAttachmentIds: [id] })
    }
    const identity = sameIdentity(intent, checked.live)
    if (identity !== null) return partial(`add-attachments-threw:compensation-unverified:${identity}`, { addedAttachmentIds: [id] })
    if (checked.live.input.attachmentIds.includes(id)) {
      // Landed before the throw: full compensation, but STILL partial —
      // the throw means the host's outcome is not fully known.
      const outcome = compensate(intent, id, 'add-attachments-threw')
      return outcome.status === 'refused'
        ? partial('add-attachments-threw:compensated', { releasedAttachmentIds: [id] })
        : outcome
    }
    // Provably not attached: release the owned draft, still partial.
    try { conversation.releaseDraftAttachment(id) } catch (error) {
      warn(`official-draft: release after addAttachments throw threw ${error}`)
      return partial('release-threw:add-attachments-threw')
    }
    return partial('add-attachments-threw:released', { releasedAttachmentIds: [id] })
  }

  /**
   * Text-only append through the captured zero-width span. One attempt, no
   * CAS retry: a refusal means the caller must re-capture.
   * @returns {object} result
   */
  function appendText(intent, text) {
    if (typeof text !== 'string' || text === '') return refused('text-empty')
    const checked = readLiveOnce()
    if (checked.error) { consume(intent); return refused(checked.error) }
    const reason = gate(intent, checked.live, false)
    if (reason) { consume(intent); return refused(reason) }
    consume(intent)
    let applied = false
    try { applied = checked.live.actions.insertText(text, intent.span) === true } catch (error) {
      warn(`official-draft: insertText threw ${error}`)
      return partial('insert-threw')
    }
    return applied
      ? { status: 'appended', atomicity: APPEND_ATOMICITY, textApplied: true, addedAttachmentIds: [], releasedAttachmentIds: [] }
      : refused('insert-refused')
  }

  /**
   * Sequential text+PNG append (NOT atomic — see APPEND_ATOMICITY). Re-gates
   * the live record after createDrafts and after addAttachments (reentrant
   * subscribers may navigate/re-bind/bump rev); every unprovable state is
   * 'partial'. An empty `text` performs the image-only path (still an image
   * draft — never a background file upload).
   * @returns {object} result: 'appended' | 'partial' | 'refused'
   */
  function appendTextWithImage(intent, text, file) {
    if (typeof text !== 'string') return refused('text-invalid')
    const checked = readLiveOnce()
    if (checked.error) { consume(intent); return refused(checked.error) }
    let reason = gate(intent, checked.live, false) ?? imageGate(file, checked.live.imageLimits)
    if (reason) { consume(intent); return refused(reason) }
    consume(intent)

    // --- mutation section: sequential public calls with fresh gates between
    // them; results are verified, never assumed. ---

    // Step 1: createDrafts may throw OR half-create — both 'partial'. The
    // official third parameter (uploadImages, boolean) is pinned to literal
    // false: the image draft keeps sending its bytes with the prompt, and no
    // service-owned background upload is ever created to leak across the bridge.
    let drafts
    try { drafts = conversation.createDrafts(intent.sessionId, [file], false) } catch (error) {
      warn(`official-draft: createDrafts threw ${error}`)
      return partial('create-drafts-threw')
    }
    const draft = Array.isArray(drafts) && drafts.length === 1 ? drafts[0] : undefined
    if (!isObject(draft) || draft.kind !== 'image' || draft.id === undefined) {
      // Never leave a non-image (file-upload) draft or a multi result behind.
      let cleanupThrew = false
      try { for (const item of Array.isArray(drafts) ? drafts : []) conversation.releaseDraftAttachment(item.id) } catch (error) {
        warn(`official-draft: cleanup release threw ${error}`)
        cleanupThrew = true
      }
      return cleanupThrew
        ? partial('cleanup-release-threw:not-one-image')
        : refused('create-drafts:not-one-image')
    }
    const id = draft.id

    // Step 2: re-gate BEFORE addAttachments — createDrafts may have run
    // reentrant subscribers (navigate/re-bind/bump rev). Nothing is attached
    // yet, so a proven release makes the refusal net-zero.
    const postCreate = readLiveOnce()
    if (postCreate.error) return releaseOwned(id, `post-create-state-unreadable:${postCreate.error}`)
    const reReason = gate(intent, postCreate.live, true)
    if (reReason !== null) return releaseOwned(id, `reentrant-create:${reReason}`)

    // Step 3: addAttachments. False ⇒ locked: release the owned draft.
    // Throw ⇒ ids may have landed first: conservative partial path.
    let attached = false
    try { attached = postCreate.live.actions.addAttachments([id]) === true } catch (error) {
      warn(`official-draft: addAttachments threw ${error}`)
      return handleAddThrew(intent, id)
    }
    if (!attached) return releaseOwned(id, 'attachments-locked')

    if (text === '') {
      return { status: 'appended', atomicity: APPEND_ATOMICITY, textApplied: false, addedAttachmentIds: [id], releasedAttachmentIds: [] }
    }

    // Step 4: re-gate BEFORE insertText (same captured span — no recapture,
    // no retry). addAttachments' publish() may have navigated or re-bound.
    // Any failure compensates the bridge-owned id through the ORIGINAL
    // actions face with identity-guarded readback.
    const postAdd = readLiveOnce()
    if (postAdd.error) return compensate(intent, id, `post-add-state-unreadable:${postAdd.error}`)
    const pre = gate(intent, postAdd.live, true)
    if (pre !== null) return compensate(intent, id, `reentrant-add:${pre}`)

    let inserted = false
    try { inserted = postAdd.live.actions.insertText(text, intent.span) === true } catch (error) {
      warn(`official-draft: insertText threw ${error}`)
      const outcome = compensate(intent, id, 'insert-threw')
      return outcome.status === 'refused'
        ? partial('insert-threw:compensated', { releasedAttachmentIds: outcome.releasedAttachmentIds })
        : outcome
    }
    if (inserted) {
      return { status: 'appended', atomicity: APPEND_ATOMICITY, textApplied: true, addedAttachmentIds: [id], releasedAttachmentIds: [] }
    }
    return compensate(intent, id, 'insert-refused-compensated')
  }

  return { captureIntent, appendText, appendTextWithImage, wiring: () => bridgeWiring(readLive) }
}
