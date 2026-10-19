# Isolated fast-PNG pilot

This is a **bounded experiment, not a production setting**. It has no CLI,
production import, environment switch, cache certificate, CI registration or
claim of speedup. Real Chrome, Mac, resource and performance validation are UNRUN
at this implementation checkpoint. Run only after independent review, using the
separately reviewed `experiments/render_pilot_runner/` harness.

## Identity and prior work

The fixed source is b287b86d95565e308eaf8fa64900f9bc6d62ae8e, tree
d281bafa0af5073f73cae2170304be76fa040d23. `adapter.py` pins the complete production
`vtx/gfx_render.py` bytes to SHA-256
f4fa354b7fee6b3951d11a8f0e6c77a8540393ae9d07bbf5c740611c2c91f6ee.
A different renderer must receive its own review; the adapter fails closed.

This work belongs to [issue #64](https://github.com/CongliangK/framivra/issues/64).
[PR #193](https://github.com/CongliangK/framivra/pull/193), head
d4ce0ff142121e0d0ec0f58d5ba40ae2571b5a68, remains HOLD. Its published description
and complete diff were read on 2026-10-19. The HOLD concerns overlapping production
GFX/environment/cache work and requires review of the eventual combined tree.
This pilot neither resumes integration of that PR nor inherits its old synthetic
pixel or timing qualification. No part of its CLI/fstxn suffix repair is included.

The relevant prior contract is reused: default screenshots have no new field,
all screenshot paths must receive the same explicit option, and full decoded RGBA
must match even when PNG compressed bytes differ. Differences from PR #193 are:

- No `vtx` constructor, production method, fallback or cache-identity change
- A temporary experiment-only adapter around the original renderer
- This running Chrome's `/json/protocol` and `/json/version` are archived before
  production setup, rather than adding a screenshot initialization probe
- Unsupported fast PNG is an experimental failure, never a downgrade to baseline
- Real bound Demo frames will be used by the reviewed harness, not a new synthetic
  fixture matrix or an assumed arbitrary memory limit

## Interface and single-variable boundary

Only a dedicated child process running one `render_page` at a time, one tab and
no unrelated renderer may enter `capture_scope`. Python class references are
process-global while the scope is active; this is intentionally unsuitable for a
persistent/concurrent production service. A nonblocking lock rejects overlapping
or nested scopes. Each page uses a new scope/evidence directory.

The runner calls `capture_scope(mode, evidence_dir)` with mode `baseline` or `fast`,
then the unchanged production `gfx_render.render_page` with its original
navigation, readiness, transparency probes, screenshot and atomic-write policy.
After the call it must invoke `trace.assert_valid(stats, expected_frames=N)`.
An exception restores the two production classes and observed function references
in `finally` and records failure.
Exiting without clean validation is an error. `trace.report()` returns the same
structured record persisted as `trace.json` on exit.

`ExperimentalTab` invokes the complete original `Tab.shot`, `navigate`, `eval`,
and `close` through exception-observation wrappers. These wrappers also retain
base64 decode, readiness-timeout and evaluation value-extraction errors outside
`Tab.call`, even if a production feature probe catches an exception.
Its `call` method passes every non-screenshot request to the original implementation
and retains any exception without changing its type or dropping it. For screenshots
it requires the original `{format: png}` and
30-second timeout exactly. Baseline sends that unchanged; fast adds the single
field `optimizeForSpeed: true`. There is no quality, size, alpha, wait, static-span,
NAVFREE, BeginFrame, Canvas or PNG_LEVEL modification. Error-free production alpha
override, flatten detection and white/black two-shot
recombination are preserved. Any CDP error, including unsupported Emulation
background override, instead invalidates this strict experimental arm: its normal
production twoshot/opaque fallback cannot obtain pilot qualification. Error-free
flatten detection leading to twoshot is a distinct, admissible route.

`ExperimentalBrowser` invokes original startup, tab allocation and cleanup with
operation observations. A production `hard_restart` request permanently invalidates
the arm; the original cleanup still runs, while a replacement start is rejected.
Startup attempts and recovery attempts are counted independently of successful
browser identities. It observes the already-started local Chrome's version and
protocol. Fast mode requires the optional boolean field to be
advertised. Baseline still records unsupported capability and can be used as the
neutral observation scope for the independent grouped-layer experiment.

Capability probing adds no screenshot and changes no page. It proves advertised
support, and the capture trace records requests accepted by CDP. Chrome does not
return the encoder's internal decision, so internal effect remains explicitly
unknown. A faster encoder is never inferred from request acceptance alone.

## Evidence and failure contract

The new evidence directory contains exact returned version/protocol JSON bytes
and their SHA-256, Chrome path/version, viewport, tab count, requested and actual
capture parameters, capture sequence, last navigation URL, background injections,
response base64 lengths, errors, original render stats and adapter/source SHA-256.
No production event schema is changed. `cdp_call_wall_s` includes original CDP
transport/JSON response processing but excludes `Tab.shot` base64 decoding; it is
not pure PNG-encoder time. Existing `timings_ms` semantics remain unchanged.

Every CDP, complete navigation/screenshot, initialization or observed output
operation error permanently invalidates that scope, including errors production
retry or alpha setup might otherwise obscure. Scoped wrappers call the original
`_atomic_write_bytes` and `_two_shot_combine` unchanged and record their exceptions.
Any recovery request or repeated browser start disqualifies the arm even if an
unobserved internal operation caused it. Later screenshots or browser restarts
cannot turn it into a successful fast arm. Operation attempts and failures are
persisted independently of successful frame timings. Structured CDP result failures
are also retained: exceptionDetails, Runtime error objects, and navigation errorText
or download results cannot be interpreted as ordinary successful values.
`Runtime.evaluate` must contain an object-valued `result` with a recognized
RemoteObject `type`; if present, `subtype` must be a string. Missing/non-object
containers and malformed type fields fail. Legal `undefined` and object/null
RemoteObjects remain accepted, and the original `Tab.eval` returns their values
unchanged. This is a bounded evaluated-value check, not a general CDP schema or
validation of every page-defined value. A caught
Browser constructor or single-worker guard failure remains fatal to that scope;
correcting configuration requires a fresh scope.

This scope observes the current protocol operations. It does not enable additional
Runtime events or inject a general JavaScript monitor, and does not certify all
asynchronous page errors or general JavaScript health. The runner must check any
existing page error/status contract separately, and full RGBA comparison remains
an independent mandatory gate. No performance result substitutes for product AC.
Missing capability or
unreadable identity fails before the first production screenshot. Existing
production fallback semantics outside this opt-in scope are unchanged.

A valid renderer result must newly render every requested frame with zero failed
frames, one tab, no Canvas export/fallback, no NAVFREE and no static-copy output.
Accepted screenshot paths are error-free opaque, transparent override and
production white/black two-shot. Unsupported Emulation override is rejected even
if unadapted production could recover through twoshot/opaque. Thus this limited
pilot does not cover all of issue #64's original transparent/fallback acceptance.
The runner must separately enforce empty isolated output,
input/dependency/Chrome-binary identity, exact global-to-entry-local frame mapping,
request order, deadline/cancellation, and full output verification. It must retain
complete PNG files for both arms and reject invalid/incomplete arms instead of
counting prior cache hits or a CLI fallback as fast-PNG rendering.

`compare_png_files(baseline, candidate, width=1920, height=1080)` reuses the
production full PNG validator (CRC/inflate/dimensions), binds comparison bytes to
that validator's digest, then uses the production RGBA decoder. It compares every
RGBA component, including RGB below alpha=0, and alpha separately; both original
PNG files remain untouched. PNG byte SHA/size and full RGBA/alpha SHA, unequal pixel
and component counts are reported. Exact RGBA equality also preserves deterministic
black/white background composition; the runner owns any actual N1-reference checks.
Only single-image, 8-bit RGB/RGBA without tRNS or unsupported ancillary chunks is
accepted. In particular RGB+tRNS is rejected before production decoding because
that decoder does not preserve this variant's alpha. The compressed input is
bounded to 32 MiB per frame. No tolerant metric, JPEG,
resizing, re-encoding or golden replacement is provided.

## Explicit offline validation

These commands run tests/bytecode checks and may create normal Python bytecode;
they do not launch Chrome or FFmpeg:

```bash
python3 -m unittest experiments.fast_png_pilot.test_adapter -v
python3 -m py_compile experiments/fast_png_pilot/adapter.py experiments/fast_png_pilot/test_adapter.py
```

The tests include protocol and exact parameter contracts, optional-parameter
rejection, renderer/timeout/connection errors, cleanup/restoration, nested scopes,
warm/cache/Canvas/static-copy rejection, and one real production `render_page`
invocation per arm with a mocked CDP transport. PNG fixtures exercise distinct
compressed bytes with identical pixels, hidden-RGB/alpha differences, corruption,
wrong dimensions and mutation after validation. Transient navigation, evaluation,
readiness, base64 and output-write failure injections in both arms must retain
the original failure and fail qualification, even when final production stats
might otherwise recover. Raw WebSocket response negatives use the unmodified
production Tab.call and eval to verify structured evaluation/navigation errors,
malformed RemoteObject containers, and valid undefined/null/false probes. Caught
constructor failures cannot be washed into a later successful attempt. RGB+tRNS is
a mandatory rejection negative case. These are explicit local tests,
not registered CI coverage and not actual-browser evidence. Original media/CI
selectors, workflows, shards and pass criteria are untouched.

## Rollback and next gate

Do not enter the scope to use unmodified production behavior. The two arms have
separate new output directories; no existing cache, certificate or baseline needs
to be deleted. Failed evidence is retained. The separately approved first Mac
pair is limited to the reviewed manifest's 120 target frames per arm and 15-minute
whole-pair wall budget, after independent review. Any mismatch or capability/error
failure stops this candidate. PNG-size growth and whole-arm I/O/downstream costs
must accompany timings; local export speed alone is not a net or full-film gain.

Independent review of the first c662f98 snapshot rejected swallowed retry failures
and an RGB+tRNS false-equivalence case. That rejected snapshot and its evidence are
retained in Git/history; the repaired candidate must receive a new independent
review. These repairs do not grant actual-browser or performance qualification.

Independent review of d7af7b3 additionally rejected ignored structured CDP failures
and caught Browser-constructor failures. This revision adds failure-response and
whole-constructor observations only in the experiment; both rejected snapshots and
review evidence remain preserved. Its stricter error gate deliberately excludes
unsupported-Emulation fallback qualification without changing production behavior.

Independent review of d5d2144 found malformed evaluate RemoteObject containers
whose original `Tab.eval` exception could be swallowed by the static-span probe.
The bounded response check and complete original eval observation address that
case; its original review evidence remains retained. These offline protocol
negatives are not claimed to be failures observed in actual Chrome.

## Opt-in navigation poll observation

`capture_scope(mode, evidence_dir, *, poll_observation=False, job_context=None)`
provides a separate observation seam. Off retains the existing tab/browser method
paths, request sequence and trace schema: it creates no observer, polls, additional
clocks or caller-frame inspection. On is permitted only with `mode="baseline"`;
there is no second executable entry point. The runner owns the fixed off/on,
on/off A/A plans, shared deadline and symmetric runtime/resource evidence. This
module does not combine observation with fast PNG or create a continuous profile.

Supply the verified runner job as `job_context`. Only `entry.entryId`, `entry.src`,
`entry.windowStart`, `batch`, `arm`, `mapping` and optional `job_id`, `run_id`,
`attempt` are copied. Mapping rows contain integer `global`/`local` and must agree
with `windowStart`. The caller's dictionary is never modified. Each scope is one
page and its evidence directory identifies that scope; records refer to its
context rather than duplicating the full mapping on each poll. Missing context,
unknown callers or unbound mapping cannot earn COMPLETE observation status.

On wraps the original `navigate` and intercepts only `Runtime.evaluate` with the
exact frozen query/readiness expression while that navigation is active. It
captures the original `Tab.call` reference and delegates to it. CDP timeout values,
transport, single receiver, interleaved Fetch processing, structured-response
validation, exception identity and production navigation/sleep remain unchanged.
It adds no evaluate, event subscription, receiver, global tracing or sleep hook.
The original navigation loop can still spend the original call timeout inside an
evaluate even though its outer readiness deadline is shorter.

Caller roles use one immediately released direct frame and the exact loaded code
object plus frozen source line: `render_page:999` initial, `render_page:1060`
flatten probe, `do_frame:857` target. The code objects are compared in-process to
the pinned source compiled without execution; this is a bounded caller binding,
not whole-process runtime attestation. Three calls to the same URL can have three
different roles. Global frames come only from the supplied local mapping and the
caller's actual local frame/URL. Unknown callers remain unknown. Targets on the
renderer worker thread are observed normally. The scope owner-thread restriction
continues to apply only to original browser construction. Completed `open_tabs`
associates each actual tab with the browser's current generation and tab ordinal.
Recovery still permanently fails the original capture trace; later successes
cannot remove failed navigation/poll attempts or make the arm eligible.

Every observation timestamp uses `clock_gettime(CLOCK_MONOTONIC)` in seconds.
`call_start`/`call_end` bracket the delegated original call, excluding
`check_response` and record allocation; unavoidable Python clock-boundary dispatch
is part of the measurement overhead. `wrapper_end` is sampled after response
validation and observation bookkeeping. It excludes the final assignment/return.
`inter_poll_gap_seconds` is the next start minus the preceding call end within the
same navigation. It includes validation, branch handling, observation, sleep and
scheduling; the browser can continue working during the gap. These timestamps
must not be mixed with legacy process-local `time.monotonic` absolute origins.

`planned_sleep` is a frozen-source branch inference after a normally returned,
parseable unmatched result, including the final unmatched poll before the loop
notices timeout. It follows the original falsey-value coercion and partition.
Truthy non-string values and protocol/transport errors are never ordinary
unmatched results. Every planned branch identifies its poll and requested delay;
its actual sleep entry/exit are null. Actual sleep duration, browser ready time
and recoverable wait remain null with explicit reasons. Initial setup delay and
recovery delay are outside this poll scope. Neither gaps nor residual wall time
are reported as measured sleep or potential savings.

Bounds are fixed in `poll_observer.LIMITS`, with no runtime enlargement option.
The navigation cap covers the fixed page target ceiling, setup probes and original
retry paths; the poll cap conservatively allows a full frozen readiness window
per retained navigation. Conservative per-record byte reservations can become the
first cap. Oversized URLs, record-cap exhaustion or byte-reservation exhaustion
stop detail appends, retain the earlier samples and keep bounded scalar counts.
They never abort or accelerate the original navigation. Overflow explicitly marks
INCOMPLETE and disqualifies observation. A final encoded-byte check enforces the
serialized cap as well. Observation errors and primary renderer errors remain
separate; unclosed intervals keep null ends and a reason, never fabricated zeros.

After page cleanup and restoration, on writes `poll-observation.json` once.
`trace.poll_observation_report()` returns a small path/bytes/SHA-256 index suitable
for the runner attempt and arm records. On also includes this index in its
`trace.json`. The descriptor starts NOT_STARTED/MISSING and reports WRITTEN only
after writing exact sidecar bytes. Missing persistence or serialization failure
cannot qualify; an instrumentation/write exception does not replace a primary
renderer exception. Off's explicit descriptor is DISABLED with null measurements,
and off has no sidecar or new legacy trace field. A missing descriptor is UNKNOWN,
not an implicit zero. COMPLETE requires nonempty mapped navigation/poll evidence,
closed intervals, preserved references and no original/observation errors or
recovery. FAILED, INCOMPLETE, UNKNOWN and NOT_STARTED remain ineligible. Scope
exit rejects an ineligible on observation even if original stats looked valid.

The capture scope's existing wall closes before sidecar serialization, as it does
before legacy trace serialization. The enclosing attempt and whole-arm wall must
include both writes. Consumers must validate the sidecar digest and scope, retain
missing/failed pages through arm/pair summaries, and merge only equivalent complete
scopes. Do not pool page p95s, subtract DISABLED nulls as zero, or relabel this
schema for older consumers. PR221 consumers remain independent/UNSUPPORTED until
separately reviewed; this module does not modify them.

Explicit ordinary offline checks (may write Python bytecode; no browser launch):

```bash
python3 -m unittest experiments.fast_png_pilot.test_adapter experiments.fast_png_pilot.test_poll_observer -v
python3 -m py_compile experiments/fast_png_pilot/adapter.py experiments/fast_png_pilot/poll_observer.py experiments/fast_png_pilot/test_poll_observer.py
```

The additional mocks exercise immediate/multiple/final-timeout polls, falsey and
unparseable values, protocol/transport failures, same-URL roles, worker threads,
actual generation/recovery contamination, bounded records, unclosed intervals,
restoration, missing writes, off equivalence and original Fetch-interleaved receive
handling. Mocked time provides control-flow evidence only. Actual Mac/browser
observation, resource limits, loaded-runtime identity, quality and A/A disturbance
measurement remain UNRUN, pending final runner integration and independent review.
