# Execution observations

`vtx/events.py` defines optional, local JSONL observations. `--events-jsonl PATH`
selects an exclusively created file in an existing directory; stdout retains its
human-readable format. Existing destinations and unsafe paths are never adopted.
Dry plans and read-only commands do not create event files. A sink error produces
a bounded stderr warning and incomplete evidence without changing rendering exits.

Execution UUIDs identify invocations, including unsuccessful input loading. A
resumed invocation gets a new UUID; `--resumed-from-run-id UUID` records an explicit
predecessor without bypassing current input/artifact validators. Content keys and
input fingerprints do not include execution IDs. A library caller can construct
an `ExecutionContext` before loading `Ctx(..., execution=...)` and finish it in its
own invocation boundary. No evidence is written merely by constructing a context.

## Interpretation

The event schema and accepted vocabulary live in `vtx/events.py`. Each sink
serializes complete lines under one lock, assigning run-relative sequence IDs.
Task occurrences have independent IDs, even when they reuse the same content.
Workers receive captured task objects; overlapping stages do not share mutable
stage attribution. QC and encoder-probe executor submissions each receive a separate
copied context, including nested QC probes. Attempt terminals retain failed work
before retries/fallbacks.
Validated cache hits have unknown original producers unless separately proven.

Durations are monotonic milliseconds, with clock metadata and run-relative
positions. Raw clock readings must be finite numbers and nondecreasing, including
samples taken for task/attempt starts. Equal readings legitimately cost zero;
invalid values or backwards readings are never clamped into credible zero costs.
A non-callable injected clock is a configuration error. A callable that fails
with an ordinary exception or returns invalid samples, initially or later, makes
observation incomplete for the rest of that invocation. It does not change task
results, replace business exceptions or retry using a different clock. An active
sink attempts one bounded warning; disabled observation stays quiet. Ordinary
stderr write failures (including closed streams and encoding errors) are ignored
without fallback output, while incomplete remains sticky. The same best-effort
diagnostic boundary covers event-sink and acceptance-bridge degradation. Clock error
messages and values are not exported. Process-control exceptions still propagate.
Overlapping task costs must not be summed as run wall-time. Unmeasured
CPU/RSS/GPU/VRAM resources are null with a reason. Errors omit free text, argv,
paths, environment and source text. A bounded chain of fixed exception-type
labels retains underlying causes, including errors translated to CLI exits.
Successful terminal events have no error. The diagnostic adapter accepts only bounded
rule IDs; it does not imply that the separate diagnostic producer is installed.

`read_events` is a pure reducer: it never executes recorded commands. Exact
repeated event IDs may be deduplicated in explicit replay mode; conflicting
records reject. Receipt observations must belong to an active task and match
the committed stage. Timing/resource shapes, units, finite bounds and unavailable
reasons are validated. Duration must equal end offset minus start within the
clock-resolution/rounding tolerance defined in the validator; task/attempt
terminals use their matching start. Child tasks and attempts stay within enclosing
lifetimes. Attempts of one task are sequential, retry requests identify the next
attempt, and a task terminal identifies its last executed attempt (or one when
there were no attempts). Independent tasks may still overlap. Legacy null start
timestamps preserve uncertainty: they cannot certify complete timing evidence,
and known contradictory enclosing bounds still reject.

Metadata is limited to the vocabulary in `events.py`: opaque IDs, backend enums,
bounded frame geometry, cache validator names, rule IDs and numeric progress.
Unsupported producer metadata is removed with a fixed redaction marker; readers
reject unknown fields and malformed values. Geometry and event-size limits live
in that same module. The sink completes bounded short writes under its lock, or
warns and marks evidence incomplete without advancing a failed event sequence.
Ordinary custom-stream exceptions are handled the same way; process-control
exceptions such as KeyboardInterrupt and SystemExit still propagate.

Gaps, missing boundaries and a truncated last line are incomplete.
A killed process without a terminal is unknown, not a successful cancellation.
Cancellation observations are supplied by a lifecycle owner; the adapter neither
cancels subprocesses nor proves cleanup. Synthetic CPU tests cover that separation,
not native renderer/process cleanup.

## Acceptance bridge

`vtx/execution_links.py` owns the fixed private request/response bridge. An exporter
allocates its export UUID before launching children, issues a fresh nonce and role
for each attempted command, and retains only the matching response. The CLI reads
from the timeline's project directory, independently of its working directory.
Requests cannot name an output path or an executable. Private ownership,
no-symlink descriptor traversal, exclusive response creation and nonce/role/export
matching reject accidental stale or swapped evidence. Unsupported safe-filesystem
operations leave linkage incomplete; they do not stop the legacy build.

Acceptance v1's manifest `run_id` remains an export UUID. Its strict manifest,
command, environment and plugin-config shapes are unchanged. Inventoried and
hashed sidecar/request/event files carry actual observed child IDs: zero, one or
two according to delivered start records, never parent-generated substitutes.
New verification reports execution linkage as complete, incomplete or unknown
(old packages). Old v1 verifiers check the extra files' hashes only. These labels
do not change existing acceptance gates, quality criteria or the accepted flag.

A receipt's per-stage `observed_by` references an event emitted after the existing
receipt transaction commits. A crash between commit and emission leaves incomplete
linkage. This does not retroactively attribute legacy stages or certify sole output
ownership when invocations share a project. UUIDs and nonce matching are correlation
under the trusted-exporter boundary, not authenticity against a rewritten package;
the independent manifest digest anchor remains necessary.

## Verification boundary

Focused CPU tests cover concurrency, failure/retry, synthetic cancellation, warm
cache observations, strict sidecar tampering, real child-start cardinality and
legacy-format compatibility. Render/backend validation uses the repository's
separate explicit integration gates; a skipped Chrome test is not a pass. There
are no CEF/Electron backend changes or GPU performance claims in this observation
layer.

## Opt-in host lifecycle extension (work in progress)

`--lifecycle-events` selects `vtx.events/2` in the selected execution sink; it
creates no sink on its own. The default writer remains v1, and the reader accepts
both versions but rejects mixed-version streams. Library callers can select
`ExecutionContext(..., lifecycle=True)`. The v2 extension uses the same sequence,
writer lock, task identities and monotonic origin, with a fixed operation enum and
bounded opaque browser/page/block IDs, frame indexes and content-key digests.
Redacted v2 metadata makes coverage incomplete even if later observations succeed.

Current CDP hooks cover the render invocation before cache checks through startup,
setup, worker joins, shutdown, deferred copies and final frame validation. This
invocation may be a page block; it is not a complete split-page wall measurement.
Workers receive independent copied contexts. Frame scopes describe individual
tries, including failed writes; attempt_index counts tries even for navigation
fallback. Successful screenshot RPCs include setup probes and both two-shot
captures; they are not committed-frame counts. Static copies are separate from
rendered tries. A successful atomic_commit reports host publication, not strict
current-run provenance or a decoded-pixel equality verdict.

`lifecycle_summary` retains raw samples, failure/success counts, cumulative work
and exact nearest-rank P50/P95 for each operation, plus invocation wall time.
Nested operation work overlaps and must not be added as wall time. No reconstructed
DAG critical path is claimed. Page.captureScreenshot RPC timing includes capture,
browser PNG encoding and response transfer; those browser-internal components
remain null/not_independently_observed. Host base64 decode, PNG decode, alpha
arithmetic and PNG encode are separately scoped without changing their algorithm.

This checkpoint does not implement tracing or the strict #62 producer bridge. There are no
extra CDP probes or trace requests. GPU/resource values retain explicit unavailable
reasons. Actual environment/config identity integration (#48), isolated paired
hosted runs and all-frame decoded comparison remain required before #63 acceptance.


`--lifecycle-process-samples` additionally opts into a 200 ms read-only Linux
process-tree sampler (requires --lifecycle-events). It starts before spawn and
finishes after browser cleanup, using the same event sink. Each process is keyed
locally by PID and creation ticks; process names, arguments and PIDs are not
exported. Descendants are deduplicated, remembered across reparenting and separated
from reused PIDs and restart roots. Scans, identities and total sample count have
fixed limits in process_samples.py. Resource events record observed interval,
missed intervals and availability; unsupported/partial observations are null,
not measured zero. Exhaustion is explicit and resource coverage is incomplete.

The summary keeps each sampled tree separate: sampled peak RSS and the sum of
each identity's largest observed lifetime CPU are not a global simultaneous peak
or CPU utilization. Shared RSS pages are double-counted, and short-lived children
and exited processes' final CPU increments may be missed. GPU/VRAM are unmeasured.
A complete event transport does not turn incomplete sampling into full coverage;
process_trees exposes sampling_complete separately. Sampling defaults off.

In v2, page_job spans cover pre-dispatch queueing through the last block's
completion, including page validation and manifest publication. Each block has a
queue span ending at worker entry. Each occurrence retains its unique page_id.
Repeated split occurrences sharing the existing (cid, gfx_key) completion owner
expose a common validation_group_id and all await that actual validation result;
no independent success is inferred from one occurrence finishing its blocks. The summary reports each page's elapsed time, outcome and
observed block count independently from render_page invocation samples. Failed or
unstarted blocks cannot certify a successful completed page. This is elapsed
job time, not a reconstructed dependency critical path. static_read measures
source reads (including deferred source validation), separately from static_copy
publication; both identify the head frame without exporting paths.

Resource sampling_complete means no reported gaps within observed_window_ms,
which spans the first and last resource event offsets. It does not assert
continuous accounting or coverage outside that window. Any missed scheduled
interval, unavailable/partial sample, exhausted bound or scheduler-clock failure
keeps sampling_complete false even after later valid samples. Scheduler failures
stop the sampler and retain unavailable status through final close without
changing the render result. Top-level complete describes event transport and
attribution; it can be true while resource sampling_complete is false.

A bounded experimental PublicationLedger can attach to the v2 execution context.
Successful renderer/CLI atomic publications emit frame_commit on the same sink,
with frame/key/origin/head and PNG-byte digest only. The ledger subsequently
verifies those actual publications and decodes RGBA completion records; cached,
missing, changed or ambiguous outputs do not acquire current-run provenance.
Static copies require a current rendered head with identical decoded pixels.
An internal lifecycle_operations=false option leaves the common sparse producer
active while disabling detailed lifecycle scopes and sampling for paired arms.
This interface alone does not produce a complete #62 receipt: frozen input/plan,
actual runtime environment and strict experiment verification are still required.

Static provenance snapshots the head's already-recorded publications before the
source read and binds matching copied PNG bytes to copy_source_seq. The reducer
checks that the referenced earlier event was a rendered head with the same key,
head index and byte digest. A later same-pixel head cannot retroactively certify
an old-cache source. Final RGBA/head equality remains an additional check.
completion_records requires the actual events_data: a successfully closed shared
stream for the same invocation and exact linkage to every captured publication.
Object flags are insufficient. This produces verified frame records, not the
still-required frozen-input/plan/environment run receipt.

### Experimental bounded runtime observation bridge

`vtx.gfx_runtime.collect(port, run_id=...)` is an opt-in, currently unwired
collector for the future isolated experiment runner. It does not change startup,
rendering, cache identity or ordinary event metadata. Its raw artifact is private:
actual executable paths and effective argv must never be copied into event rows.
It is not a reusable environment credential or a completed #62 run receipt.

The collector discovers only a loopback, same-port browser-root CDP endpoint and
reads Browser.getVersion, SystemInfo.getInfo and SystemInfo.getProcessInfo. HTTP
body is capped at 64 KiB; combined sent/received root-session wire bytes at 1 MiB; each response search at
64 messages. Ping/control traffic and fragmented messages share that wire budget.
Text payloads decode as strict UTF-8; all nonfinite numbers, including exponent
overflow, are rejected before retaining observations. HTTP discovery and the
entire root session each have a 5-second absolute socket
deadline, including connection/handshake; per-read progress cannot reset it. No
Tracing, stream protocol or browser-internal timing is claimed.

Exactly one positive integer browser PID is required from the root session.
Linux process start ticks, executable inode metadata and effective argv are
checked around hashing; the same open CDP session must report that PID again.
The opened executable inode must match both process-side checks. No supervisor
PID or caller identity dictionary is accepted as a substitute. The executable
primitive is narrowly transplanted from #48 commit
`6da2c8a40eaa2bd464f3ba60ee55f1cbc8708705`, with a 512 MiB byte cap, cooperative
5-second hash deadline and a private opened-inode comparison token. It does not
bring in #48 cache/key changes. File I/O deadlines cannot forcibly interrupt a
stalled OS syscall; this is not a hard teardown bound.

Missing observations remain null with fixed reason codes, without raw exception
messages. Viewport/scale and alpha need page-level observations; color_space stays
null instead of turning the configured sRGB policy into measured color. The raw
schema-1 shape is an experimental adapter input, not an expansion of the public
event protocol or a guarantee that #48 will accept it. Full-run linkage, page
witnesses, font/config evidence and producer/run receipts remain to be integrated.

### Limited experiment producer and fresh-root runner

`scripts/bench_lifecycle.py` is the opt-in short four-page #63 profile, not a user
project benchmark wrapper. It creates a new owned directory beneath an existing
canonical output parent, generates its own four 30-frame 1080p/30fps pages and
2-second CPU media, and creates a separate fresh project/cache for every arm and
scope. It never cleans a project or calls bench_cold.sh. Source must be frozen and
clean. All plans and arm IDs are written before either arm; inputs, source, host,
recorded font files/configuration and effective runtime identities are rechecked.
The explicit font-config inventory and environment are evidence for this bounded
no-text fixture, not proof of a general font fallback closure.

Both arms use the same sparse frame publisher and actual runtime/page probes.
Baseline disables lifecycle operations and process sampling; candidate enables
them; STATIC and NAVFREE are off in both and trace is off. The common producer's
cost is part of both arms, so this does not measure against an unmodified binary.
Every actual Browser start/restart is probed; each rendered publication includes
only runtime_sha256 and page_witness_sha256 in the shared stream. The separate
private artifact holds the raw runtime and page viewport/device-scale/actual
export-path witnesses. Copies inherit their prior rendered-head binding. CLI
fallback still renders normally, but cannot obtain a CDP experiment credential.
Observation errors leave rendered results intact and prevent complete evidence.

The runner calls the real CLI gfx path or the real all pipeline followed by strict
QC, closes the one shared producer stream, verifies final PNGs/current publication
records and environment/input stability, then emits the #62 gfx_run receipt.
The strict consumer reopens and verifies the bound event stream, raw observation
digests, per-frame publication sequences and PNG bytes for this profile before
full decoded-RGBA comparison. Missing inputs/observations, defaults masquerading
as color evidence, failed/nonclosed streams and unbound fallback frames reject.
Legacy ordinary comparison is unchanged. sRGB is an explicit configuration
contract with measured=false; raw measured color stays null. A missing policy can
retain the old display default, but the strict consumer rejects its unavailable
source marker even after repeated normalization.

Default matrix is three paired rounds A→B, B→A, A→B, each with separate standalone
GFX and full-chain roots (12 cold invocations). Cold means fresh application caches
and browser profiles, not flushed OS caches. Each pair retains RC/wall/load,
raw events/runtime, strict receipts/full-frame comparison, lifecycle/resource
summaries, paired deltas, scoped throughput and medians/ranges. A one-round or
GFX-only run is diagnostic. Resource sampling gaps remain explicit; baseline
sampling-disabled values are not zero measurements. No reconstructed DAG critical
path or population P95 is claimed. The output deliberately never declares full
#63 completion: hosted execution, trace-overhead work, final main integration and
independent acceptance remain separate gates. Actual Chrome/FFmpeg runs must be
scheduled in the approved hosted environment; no sandbox bypass is provided.

The candidate records an enclosing host `wave` span from before job submission
through all joins (including failure); it is not a sum of overlapping child times.
Process-tree samples cover the launched tree, which may include the Python Chrome
supervisor. They are not labelled Chrome-only RSS; shared pages may be counted
more than once and short-lived processes/final CPU increments can be missed.

The isolated experiment supplies an explicit `srgb` policy to its publication
ledger (and preflight Browser). This adds `--force-color-profile=srgb` to both
supervised and standalone launches within this experiment; ordinary rendering
has no new environment knob or changed cache identity. The consumer requires the
unique flag in actual observed process argv. This proves requested browser
configuration, not measured output gamut or monitor behavior.

The dedicated `lifecycle-63.yml` PR workflow only selects same-repository heads
under `acceptance/lifecycle-63/` and relevant changed paths. It checks out the
actual head SHA with read-only contents permission and no persisted credentials.
The 35-minute job has a 30-minute GNU timeout around the twelve-run paired matrix,
plus a ten-second TERM-to-KILL grace for that owned runner process group. This
external deadline is distinct from cooperative runtime observation deadlines;
Chrome supervisor parent-pipe cleanup remains responsible for separately owned
Chrome groups. No host security setting or Chrome sandbox override is provided.
Missing dependencies or failed/timeout runs fail the job instead of skipping.

The artifact packer retains all twelve receipts/event streams, six full-frame
comparison reports, plans, raw observations and summaries, plus the source SHA,
return code and log. PNGs/media are omitted. Selected data plus metadata must fit
the 32-MiB budget; exceeding it marks evidence incomplete and fails packaging,
never trims successful evidence and still claims PASS. Artifacts expire after
seven days and can contain runner paths/argv, so they should be treated as raw
experiment evidence. The workflow can establish only this short profile's partial
acceptance; trace, graph critical path and Chrome-only RSS remain separate gaps.
