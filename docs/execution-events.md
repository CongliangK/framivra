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
