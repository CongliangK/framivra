# Composition cost observations

Opt-in experimental tools. Production does not import this directory. No
production source, events, selector, test registration, CI, renderer setting,
cache policy or quality threshold is changed.

## Result that motivated this scope

The reviewed 120-second workload has 31 logical layers: 19 GFX inputs and 12
paper clips sharing one preparation specification. The existing producer table
already schedules one paper pre-encode while retaining 12 consumers and their
validation lifetime. Reducing the FFmpeg inputs would change the original N1
filtergraph. Reusing a prior validation without a new identity proof would
weaken a separate contract.

The original N1 dry command construction takes a fraction of a millisecond in
the bounded Linux measurement. This excludes input validation and all media IO;
it is not a Mac end-to-end result. There is no evidence here for a useful
production graph-construction change. Production remains unchanged. The
observer instead separates the outstanding video-before-FFmpeg interval.

## Read-only dry planning

This command reads the timeline, constructs modeled paths, and prints JSON. It
does not construct Ctx, inspect media, validate caches, launch FFmpeg or write a
project. Synthetic path identities are explicitly not actual seg/cache keys.

```bash
python3 -m experiments.perf_composite.dry_plan PATH_TO_TIMELINE --repeats 1000
python3 -m unittest experiments.perf_composite.test_observer -v
```

The report includes the input timeline hash, actual logical clip multiplicity,
original filtergraph hash, median/p95 batch samples and exclusions. No timing
threshold is a test or gate. Exact clip descriptors excluding dst_in provide a
static repetition count, not proof that a real media key or cache entry matches.

## Optional observer: actual rendering is a write operation

The observer records calls without modifying their arguments or return values.
The command it surrounds is ordinary production rendering: video writes cache
and out; all additionally includes the browser/GFX, audio, mux and QC paths.
**The entire invocation is not read-only.** Real invocation is refused outside
macOS. This does not authorize a Mac run. The coordinator must separately check
and launch a new isolated project copy; never use the user's current project,
old final or golden. No default invocation, automatic rerun or background work
exists.

The launcher requires an explicit isolated project, acknowledgement, new
external report path and explicit video or all command. The timeline must be
directly inside that project. Existing final.mp4, video_noaudio.mp4 or draft.mp4
is refused. These checks do not prove copy provenance: the operator still owns
that check. Required receipts/inputs/cache may be copied only by the separately
approved preparation workflow. This tool performs no copy, removal or reset.

Help is safe everywhere:

```bash
python3 -m experiments.perf_composite.observe_video --help
```

Write-operation example for the coordinator's authorized, fresh Mac copy only:

```bash
python3 -m experiments.perf_composite.observe_video \
  --report NEW_EXTERNAL_EVIDENCE/video-observation.json \
  --isolated-project NEW_ISOLATED_PROJECT --ack-render-writes \
  -- -t NEW_ISOLATED_PROJECT/timeline.json video
```

Replacing video with all keeps production stage scheduling. Optional native
--events-jsonl NEW_FILE is forwarded unchanged. Other native flags are refused
in this narrow full-quality N1 experiment. The approved environment must already
have VTX_VIDEO_SEGMENTS=1; the launcher checks it without setting it. It does not
alter presets, resolution, thread counts, browser flags, alpha, audio or effects.
There is no dependency on or change to render_pilot_runner.

## What the report means

- video_stage starts at the original _observed_stage call, just before its event
  scope and begin_stage. It is a distinct, slightly wider boundary than the
  production event timestamp and must not be silently substituted for it.
- first_ctx_run is the first Ctx.run call in that interval. It may be a probe or
  another command. first_ffmpeg is the first actual Python subprocess.run entry
  with an ffmpeg executable, not kernel process creation. It can validate old
  cache data, probe an encoder, or encode. Its role remains unknown, and observed
  ancestor categories provide evidence without recording argv or media paths.
- Each window is clipped from actual function spans. Included functions cover
  budget/manifest, input identity/fingerprint/environment, preparation/key
  calculation, input probe, PNG validation, stable-format/derivative work,
  graph/input/filter construction and process calls.
- Inclusive times contain nested calls. Exclusive means outside *other observed
  calls*, including uninstrumented work and observer overhead; it is not a claim
  that all remaining work is validation, protection, disk IO or graph assembly.
  For example, png_stable_input self time includes unwrapped conversion
  preparation and writes; it is not a pure conversion timer.
- Exclusive sums are meaningful within one thread. Per-thread categories and
  interval unions are separate from cross-thread work-time. Never add overlapping
  inclusive categories, or use concurrent thread sums as elapsed wall. The
  outer video_stage self time remains unattributed function-body work.
- Null boundaries mean unobserved, never zero. Missing video_stage can mean an
  earlier command failure. At the finite span limit the report becomes UNKNOWN;
  recording exhaustion does not replace production failure semantics.
- Reports contain labels, counts, relative time offsets, outcomes and status.
  They omit function args, paths, environment values, media bytes, exception
  text and credentials. This is diagnostic data, not an authenticity certificate.

Only the experimental process temporarily wraps known functions and vtx import
aliases. functools.wraps preserves inspect.getsource identity. Before running,
all target source strings and the recomputed graph digest must match the
unwrapped values; a mismatch refuses execution. Installation and restoration
are in finally. Lazy import aliases are restored too. Return objects and raised
exception objects, SystemExit codes and KeyboardInterrupt propagate unchanged.
An evidence-write failure cannot replace an already-raised production error.

## Observer overhead and bounded native control

The following **writes only a new synthetic evidence directory** and invokes
local FFmpeg on generated tiny PNGs. Never supply an existing directory. It is
allowed as a CPU/native-media control; it does not run a production timeline or
browser and is not a bypass of the Mac-only launcher.

```bash
python3 -m experiments.perf_composite.calibrate NEW_SYNTHETIC_DIRECTORY
```

It reports alternating unwrapped/wrapped no-op and actual PNG-validator batch
samples, then four small native renders in ABBA order. Original N1 filtergraph
strings and every decoded frame MD5 must match. Final x264 parameters are the
original ones. Source identity, exception propagation and report arithmetic are
also covered by explicit ordinary tests. No path-security negative tests or
whole-suite selection is included.

The local overhead is a measurement control, not a correction factor. Native
render order, cache and scheduling noise can make an observed sample faster;
that is not an optimization. Do not subtract a no-op median from Mac wall, and
do not claim speedup without independent same-input/profile unobserved A/B.
Mac 300-frame and full-film performance, original 1080p pixels/alpha/color,
audio and recovery equivalence remain UNRUN. This tool earns no automatic
promotion, merge, release or change to the frozen Demo.
