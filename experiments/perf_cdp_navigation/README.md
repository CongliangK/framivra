# Offline navigation-event model

This experiment models one complete `Page.navigate` per requested frame. It has
no connection, browser runner, renderer hook, production flag or default-path
effect. Passing its tests proves only the modeled message transitions. It is not
qualified for actual pixels, paint readiness, speed improvement or production use.

## Scope and source

- Baseline commit: `b8cffd07bbf8694d207bf9e012f45381d31bbe3d`.
- Baseline tree: `a0d12eaccc2a3fd029706f745e973aa19b9fb19b`.
- Existing behavior: [Tab.call and Tab.navigate](https://github.com/CongliangK/framivra/blob/b8cffd07bbf8694d207bf9e012f45381d31bbe3d/vtx/gfx_render.py#L310-L361).
- Protocol references: [Page](https://chromedevtools.github.io/devtools-protocol/tot/Page/)
  (`navigate`, `frameNavigated`, `lifecycleEvent`) and
  [Runtime](https://chromedevtools.github.io/devtools-protocol/tot/Runtime/)
  (`evaluate`). Current protocol documentation is not a capability check on an
  installed Chrome. Protocol and actual event ordering need later observation.

This directory's README describes this experiment, not a new product contract.
`vtx/`, existing runners, CI, request policy, permissions and browser flags are
untouched. The state machine does not import them.

## Explicit offline test

From the repository root, run only the named module:

```bash
python3 -B -m unittest -v experiments.perf_cdp_navigation.test_navigation
```

This uses only Python's standard library and ordinary dictionaries, states and
synthetic monotonic times. It starts no process other than the test interpreter,
opens no network connection and generates no renderer output. No test discovery,
browser run, filesystem security probe or existing suite is requested here.

## Inputs and actions

`NavigationWait` is single-use: one target, main frame, browser generation and
navigation attempt. `start(now)` describes the full `Page.navigate` request.
`receive(message, generation=..., target=..., now=...)` consumes decoded messages;
`tick(now)` supplies deadline progress even when no message arrives. `disconnect`
terminates a live attempt. A recovery requires a fresh instance and a new owner
generation; records are never shared between attempts.

The owner must provide a verified target/main-frame identity, reserve distinct
navigate/check IDs outside its other command IDs, and supply the generation and
target from its connection ownership. Payload fields cannot establish those
identities. The owner must have actually enabled lifecycle events before setting
`lifecycle_enabled=True`. These are model inputs, not an implemented handshake.

Returned `Action` objects describe:

- `send`: only `Page.navigate` or the existing `Runtime.evaluate` predicate.
- `forward`: the exact unmodified input object, for its existing owner.
- `fallback_required`: the candidate cannot conclude even the legacy predicate.
  The owner must run the existing query/readyState wait, still draining strict
  policy work and respecting the remaining total budget. This prototype does
  not execute that fallback and never records it as success.
- `failed`: stop this attempt; no automatic retry, browser recovery or capture.
- `legacy_check_passed`: historical observation of only the original predicate,
  after tracked strict policy work has completed. This is not `ready`, a pixel
  result, a paint barrier or authorization to capture or publish anything.

There is no socket reader, asynchronous dispatcher, sleeping, CDP setup, generic
request scheduler, screenshot action or final image verification in this model.
The caller owns all action delivery; returning an action does not execute it.

## Correlation and bounds

An event pair may arrive in either order, before or after the navigate response.
The candidate requires all of the following before requesting the final check:

1. A successful matching navigate response without protocol error, errorText or
   download, naming the expected main frame and a nonempty loader.
2. `Page.frameNavigated` for that main frame and loader, with the exact requested
   URL (including the event's optional `urlFragment`) and no parent-frame marker.
3. `Page.lifecycleEvent` named `load`, for the same frame and loader.
4. No unresolved tracked strict Fetch operation.

Generation and target filter every message. Other-frame events and nonmatching
loader load events cannot wake the attempt. An unexpected main-frame document
event after binding conservatively requests fallback, even when it might be a
delayed old event. Bare `loadEventFired` lacks the identities and cannot qualify.
Conflicting URL evidence cannot be repaired by a later matching URL event. Main
document transitions invalidate evidence for the document they supersede even
before the navigate response binds its loader. A repeated matching event or an
equal final query does not erase that invalidation. A pre-response same-document
event for the main frame is sticky and requires fallback after the response.

Context destruction/clearing invalidates evidence already collected, including
before the response. Without a context-to-frame map, this model conservatively
invalidates all its collected records. This can cause extra fallback for unrelated
context events; it is deliberately not proof of efficient real-browser behavior.
Normal context notifications before any candidate evidence do not create ready
evidence or prevent a subsequent clean event pair.

Only bounded loader records and bounded Fetch request IDs are retained, with
bounded stored identifier/URL lengths. Duplicate lifecycle events do not grow
records. Event-cache overflow explicitly fails the attempt, including before the
navigate reply; it cannot fall back while silently assuming navigation succeeded.
The final limits and their validation live in `navigation.py`.

The model uses one finite monotonic deadline starting before navigation, not a
fresh timeout for each phase. Returned actions carry the remaining budget.
An event grace interval after the response reserves a chance for a real legacy
fallback; absence of events is not success. Missing loader (including same-document
navigation), missing capability and fragment URLs are explicit fallback cases.
This prototype does not normalize or reinterpret the original query predicate.

`location.search+'|'+document.readyState` and `returnByValue=True` are unchanged.
Wrong query, incomplete document, context changes during the check, exception
details and protocol errors cannot become a successful predicate result. A
main-frame detach, disconnection or expired deadline fails. Terminal states are
historical results; later messages are forwarded, not consumed as fresh evidence.

## Strict Fetch ownership

Every `Fetch.requestPaused` is immediately forwarded unchanged, including after a
terminal state, for another target/generation, or on the input that exhausts a
budget. Unowned responses, including policy-command errors, are also forwarded.
They are never interpreted as navigation or readiness evidence.

With `strict=True`, the model tracks the paused request ID and blocks the final
check or its completion. The existing policy owner must process its command,
observe its matching acknowledgement, run its existing policy validation, and
only then call `fetch_finished(..., success=True)`. An error receipt fails;
unknown, duplicated or overflowing requests never simulate successful draining.
Outstanding tracked IDs remain inspectable after failure. No Fetch continuation,
fulfilment, denial decision or permission is generated here.

This callback is an explicit offline seam, not proof that a future transport
preserves strict semantics. A future owner must keep its existing event budget,
policy-command budget and policy checks, process every forwarded message once,
and retain single-receiver ownership. It must enforce remaining deadlines at the
actual socket/blocking-call boundary. None of those transport guarantees has
been implemented or tested by this experiment.

## What remains unqualified

Tests cover event/response permutations, stale generation/loader, child frames,
URL mismatch, same-document and no-event fallback, errors/downloads, clocks,
disconnection, bounded records, strict interleaving and final predicate checks.
They do not validate actual Chrome capability, event subscription timing,
fonts/images/media, asynchronous paint, capture-frame identity, RGBA equality,
alpha handling, cold-cache costs, transport overhead or whole-film throughput.

Before any real integration: independently review the frozen source/tree, obtain
actual event and polling observations, preserve existing strict/screenshot/
recovery behavior, and obtain the separately authorized real pixel acceptance.
Then measure any candidate against the identical full-navigation baseline with
the same input and quality contract. Offline tests cannot confer that permission
or prove any speedup, let alone the whole-chain 50% goal.

No `__vtx_render` frame driving, NAVFREE, BeginFrame, frame skipping, format or
quality change, concurrency expansion or browser installation is part of this
prototype. Those are not fallback options.
