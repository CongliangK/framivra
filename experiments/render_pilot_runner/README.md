# Bounded b287 render pilot

Isolated opt-in experiment for the frozen TODAY plan identified in contract.py.
Nothing imports this directory from production. It changes no CI discovery,
selector, shard, required check, event schema, old cache or manifest.

## Explicit continuous300-v1 preparation

`plan --target-profile continuous300 --mode fast-png` selects the only new
`render-pilot/2` tuple. Its fixed workload and resource-policy fields live in
`profiles.py`; there is no arbitrary frame/window/deadline option. Omitting this
flag preserves the complete `render-pilot/1` plan and digest. The original first
pilot and group contracts below remain v1 contracts; no old evidence is relabelled.

The new window is global 1380..1679, in original paint order T08.01 then T07.01.
Each arm runs two original render_page calls with full original page durations,
explicit original local frame numbers and fresh output/cache/profile storage.
Each arm has 600 target PNGs and quality requires exactly 600 distinct
(entry, global, local) pairs. Shared omissions, duplicates and incorrect local
mappings fail. Setup probes are reported separately from targets from the actual
capture trace; incomplete counts are flagged. A remains baseline PNG and B remains
fast PNG in both AB and BA schedules. The shared pair deadline, original supervisor,
cleanup bounds and exact decoded RGBA comparison remain in force.

V2 workers retain namespace/spec/plan/arm/attempt identity, PID/PPID/PGID and the
existing supervisor start identity, argv/cwd, Python flags/version, executable
entry and resolved actual runtime image file identities. For the finite functions
listed by runtime_identity.WorkerBinding, source is compiled without execution
in that same interpreter, then complete code objects (including nested code and
constants), module/global identity and defaults are compared. Original adapter
delegates and restoration are checked. Source bytes alone are not loaded-code
proof. This binds only the listed objects for this worker; mutable global values,
third-party dependencies, the interpreter as a whole and historical runs are not
attested. Fingerprints are interpreter/path-specific audit hints, not cross-runtime
portable identities. A missing binding cannot pass or start the next arm.

Resource observation is deliberately disabled in this source preparation. Values
and thresholds remain unknown/unfrozen, and the pair explicitly reports
promotion_eligible=false even if its local pixel gate passes. The controller must
freeze current Mac resource limits and the missing-sample policy before any formal
execution. This code does not infer Chrome limits, claim sampled peaks, or authorize
a run. Resource status is separate from local pixels; it does not replace the
existing deadline or cleanup. This continuous profile never enables O1 poll observation; the separate O1 mode is documented below.
Actual Mac/Python 3.9/browser/1080p performance and resource sampling are UNRUN.

Ordinary focused offline checks (no browser, real worker, security path negative,
or CI registration):

```bash
python3 -m unittest experiments.render_pilot_runner.test_continuous300 experiments.render_pilot_runner.test_runtime_identity experiments.render_pilot_runner.test_arm_order -v
```

Planning is stdout-only and still re-reads the pinned inventory and bound payloads.
The caller must supply paths verified on the intended executor; an offline fixture
or a cloud path is not a qualified Mac plan. The original `run` command consumes the
frozen plan without changing its profile/window and still requires the separately
reviewed exact implementation commit and tree. No formal trial was performed by
adding this option.

## Fast-PNG order-balanced replication preparation

`run --arm-order AB|BA` is an optional fast-png-only execution setting. A always
means baseline PNG; B always means optimizeForSpeed candidate PNG. Omission keeps
the existing AB order. BA starts candidate before baseline, with quality last.
It does not swap directory names, capture modes, frame mappings or left/right
quality identities. An explicit order on any other mode is refused before work.
The stopped group experiment is not resumed or otherwise extended by this change.

The fast-png spec/result record `arm_order` and `arm_sequence` as requested schedule.
The result's `processes` array records actual stages started, including failures;
a two-item schedule is not evidence that both arms completed. Existing per-attempt
raw stats, capture traces, PNG sizes, exits, cleanup and full-wall records remain
authoritative. The plan and its digest are unchanged; order is separate execution
configuration in spec/result. Each pair still has one shared deadline and its own
fresh output, cache and profiles. No retries, concurrency or timing system is added.

After independent review of the new exact implementation commit/tree, the proposed
formal replication is three separate serial pairs, AB / BA / AB, each in a new
directory. The original 1c01574 first pair remains a pilot outside these formal
samples; do not pool its timing or reuse its cache. Keep the same reviewed bound
input, browser/environment, profile and 120 targets per arm. Order varies only
between whole arms; each arm retains continuous then diagnostic batches. This
small schedule includes both orders but is not an equal-count randomized design.

Record every pair and every attempt, including unsuccessful/partial runs. Do not
replace failed samples with hidden reruns or proceed after unverified cleanup.
Report continuous 90 globals / 180 layer-frames separately from diagnostic
30 globals / 58 layer-frames. Preserve arm wall, supervised wall and whole-pair
wall as separate measurements; retain each render_page timing sum/count/p95
without manufacturing pooled percentiles. For each pair and each batch sum all
fresh target PNG byte counts by named arm, and report the candidate/baseline size
ratio beside time differences. Initial screenshot probes are not target frames.
Diagnostics do not establish throughput. Exact 238-frame RGBA/alpha equality,
no fallback/recovery, unchanged identities and verified cleanup remain required.
Cross-pair stability, thermal/resource observations and the PNG-size tradeoff must
be assessed separately; no new acceptance threshold or stable speed claim is added.

Ordinary explicit regression command (no browser, FFmpeg, real worker or path attack):

```bash
python3 -m unittest experiments.render_pilot_runner.test_arm_order -v
```

The following example WRITES a fresh pair and starts real browsers; it is only
for the separately authorized Mac execution after review, not a cloud test:

```bash
python3 -m experiments.render_pilot_runner.runner run --plan /ABS/FAST-PNG-plan.json --output /ABS/NEW-FORMAL-PAIR-2 --chrome /ABS/QUALIFIED-CHROME --implementation-commit FULL-REVIEWED-COMMIT --implementation-tree FULL-REVIEWED-TREE --arm-order BA
```

## Boundaries and inputs

- b287/d281 is the workload baseline, not the execution implementation identity.
  `run` requires the exact clean final implementation commit AND tree, containing
  this directory, the separately reviewed fast-PNG adapter and (for group mode)
  the separately reviewed group preparer. Production/template diffs are refused.
- `plan` verifies the exact reviewed 114-file inventory hash and all 109 source
  payload bytes, including the seven native waveform/audio binding derivatives.
  Five large media identities are recorded from that reviewed inventory, not
  falsely reported as rehashed/consumed. No old warm cache is copied or read.
- One mode per pair: `fast-png` changes only screenshot PNG options; `group`
  uses the fast-PNG adapter in baseline mode solely for symmetric capture/error
  evidence. All jobs use production gfx_render.render_page, explicit frames,
  1920x1080, tabs=1 and a new output directory. NAVFREE/BeginFrame stay closed.
- Each arm targets the same 120 global frames: 90 continuous frames, then 30
  diagnostics, listed once in contract.py. Original T08 local=global-18 and T07
  local=global. Inactive T08 global 0 and 17 are never rendered. Baseline has 238
  actual layer renders, group candidate 120; these are not final film frames.
- Continuous/diagnostic jobs and timing are separate. Diagnostic throughput must
  not be extrapolated. A page is restarted at the batch boundary. Every raw
  render_page result, per-attempt sum/p95 and derived count, exact call wall,
  full attempt/arm/pair wall, PNG hash/size and raw child return code are kept.
  Percentiles are never summed or averaged into a fake pooled percentile.
- This first pair has one shared 900-second monotonic deadline covering checks,
  both arms, quality and final verification. Existing SupervisedProcessGroup
  owns each worker; Browser receives an isolated context and reuses the same
  production supervisor. Timeout uses its normal stop/cleanup, never killall.
  All identity git waits, including parent diff and final checks, use the
  remaining deadline. Cleanup may exceed the scoring deadline; timed-out
  attempts remain failures. No
  daemon, recurring task, new scheduler or cache completion marker is created.
- Cleanup is independently reported as PASS, FAIL or UNKNOWN. Before original
  stop clears references, retain each actual outer worker/guard and nested Browser
  group identity. After stop, require exited worker and guard, an observable empty
  group and removal of that group's run/profile directories. A removed ledger or
  a zero worker exit is insufficient. Nested Browser journals survive arm exit.
  On interruption, only that arm's new cache is offered to the original
  identity/lineage-checked orphan reaper; retained IDs are checked afterward.
  An incomplete start journal, unavailable observation, residual group/guard or
  directory blocks the next arm and any local PASS. No new signalling strategy
  is introduced. Group emptiness uses one strictly parsed `/bin/ps -axo
  pid=,pgid=,stat=` snapshot with a maximum 10-second timeout on Linux/Darwin, retaining
  only matching owned-group rows plus snapshot hash/count. Empty, failed, duplicate
  or malformed observations remain UNKNOWN. Original Popen/guard identities stay
  authoritative; ps grants no signalling authority. Unrelated PID native-info
  readability is not required. Actual Mac observation is UNRUN. Original worker
  cancellation/error is retained separately if cleanup also fails.
  During the scoring window each ps wait is capped by the remaining pair
  deadline. Necessary cleanup after that deadline uses an independent 30-second
  observation budget shared by the outer and all nested postflights for that arm;
  no later ps starts after that budget. Original owner stop/reaper waits keep their
  existing bounds. All cleanup wall remains in pair wall, and exceeding 900 seconds
  makes the performance attempt FAILED even if cleanup eventually passes.
  Existing owner waits are bounded; a guard observation adds at
  most three seconds per journal. Performance FAILED never implies cleanup PASS.
- Real execution refuses non-Darwin hosts. This is not permission to run on a Mac;
  use the existing authorized executor only after final independent review,
  preflight memory/pressure recording and artifact/identity checks. Resource
  metrics not observed by this tool are `unknown`; concurrency is never raised.

## Local correctness gates

Fast-PNG compares every decoded RGBA byte, including hidden RGB under alpha=0,
with zero tolerance. Original PNG byte equality is separate. Unsupported PNG
variants, empty work, copied/cached work, fallback, failed/retried captures,
changed input/environment and partial outputs cannot earn a local pass.

Group captures have an extra experimental pre-capture state check. The group
page must report its requested global frame, hidden failure plate and matching
active/local/global/implemented status of each member. Missing, error or stale
state permanently invalidates the capture trace, even if renderer healing recovers.

For the group pair, fresh target PNGs are copied into dense 0..119 sequences;
a separate manifest maps every dense index to its original global frame. Only
the two inactive T08 positions receive explicit transparent quality placeholders,
reported as non-rendering work. No active missing PNG may be filled.

The quality entry calls the unmodified RenderPlan.composite with draft=False,
a=0, b=None, nseg=1. The original argv/filtergraph is retained. The final lossy
sink is replaced with lossless PNG transport. Black/white probes retain the
original yuv420p canvas AND original encoder-input yuv420p constraint before
conversion to RGBA for file transport, preserving that local graph's format
negotiation. Verbose FFmpeg logs retain actual negotiated formats. A separate
transparent probe changes the canvas to RGBA; this is explicitly a probe, not
claimed to be the opaque original N1 canvas. All three must match exactly.

These are local two-role pixel tests only. The other 17 entries, original
3,600-frame scheduling, subtitles, audio, final lossy encoding and actual paper
background are UNRUN. Real paper must come from qualified original prepared
media and the original timeline's repeated source-frame mapping; global-time
seek into the 300-frame source is invalid. A local PASS cannot promote a candidate
to 300 frames/full-film while these remaining required checks are missing.
The synthetic 2x2 CPU probe establishes command wiring and frame changes only;
it establishes no actual 1080p, Chrome or performance result.

## Explicit commands

From the final integrated repository root, read-only planning (stdout only):

```bash
python3 -m experiments.render_pilot_runner.runner plan --bound-project /ABS/REVIEWED-BOUND --inventory /ABS/project-114-file-inventory.json --mode fast-png
python3 -m experiments.render_pilot_runner.runner plan --bound-project /ABS/REVIEWED-BOUND --inventory /ABS/project-114-file-inventory.json --mode group --group-plan /ABS/NEW-GROUP/group-plan.json
python3 -m unittest experiments.render_pilot_runner.test_group_contract -v
python3 -m experiments.render_pilot_runner.runner clock-probe
```

The following commands WRITE only new experimental output directories. The real
run also starts browsers/FFmpeg and is for the reviewed authorized Mac executor:

```bash
python3 -m experiments.render_pilot_runner.cpu_probe --output /ABS/NEW-CPU-PROBE
python3 -m experiments.render_pilot_runner.runner run --plan /ABS/REVIEWED-plan.json --output /ABS/NEW-PAIR --chrome /ABS/QUALIFIED-CHROME --implementation-commit FULL-COMMIT --implementation-tree FULL-TREE
```

The plan must first be saved outside the clean checkout; its root paths are
executor-local. `run` revalidates the complete plan and source bytes. Never feed
cloud paths to a Mac or substitute the unbound source bundle. Keep full PNG/media
on the Mac. Return the small spec/result/SUMMARY/log evidence and exact candidate
identity. Explicit offline tests are not automatically registered in CI.

The cleanup tests use ordinary mocks plus two subprocesses created by that test
through the existing supervisor. They run no Chrome and perform no restricted
link or security-negative test. Final integration must use the independently
reviewed fast-PNG candidate cf2834ba7ffa309c29dbdcfd527ae356fea3366f
(tree 10976c246182cd508051a7f86a5bf251aedf81d0) or a later reviewed repair;
API compatibility alone is not approval of an older adapter.

Runtime target is the qualified Mac `/usr/bin/python3` 3.9.6. The experiment uses
no Python 3.10+ grammar, zip(strict), dataclass slots or tomllib. Cloud validation
uses Python 3.12 plus a 3.9 grammar parse and API audit, not a 3.9 runtime result.
Actual Mac 3.9 import/`runner --help` is UNRUN and is required as a lightweight
preflight before any authorized real pilot. Do not install or change system Python.

The parent/arm/quality shared deadline and harness wall measurements all use
`time.clock_gettime(time.CLOCK_MONOTONIC)`, explicitly recorded as clock_kind.
Availability/errors are hard failures, never fallback to a process-relative clock.
Python's [3.10 time documentation](https://docs.python.org/3.10/library/time.html#time.monotonic)
notes the macOS monotonic system-wide change in 3.10; [the 3.9 clock_gettime API](https://docs.python.org/3.9/library/time.html#time.clock_gettime)
already supports selecting the native clock explicitly. This is the reason for
avoiding Python 3.9's `time.monotonic` absolute values across processes.

`clock-probe` launches one ordinary short Python child (five-second maximum),
prints clock kind and parent-before/child/parent-after, and requires the child
value to lie inside that interval. It writes no cache and starts no browser.
Actual `run` repeats this probe within its 900-second budget before any worker.
The exact Mac 3.9.6 probe must pass before real execution; cloud probe success
cannot substitute for it. CPU fixture wall and original renderer timings remain
same-process relative measurements; they do not transport an absolute deadline.


## Frozen group-manifest completeness repair

Group input qualification supports exactly the independently reviewed generator
30e9d97ffb606e7cae991de1ab724d9178479075 / tree
395b3c1b7b4e0b020e758cf486c7924f64672c29. `group_contract.py` fixes its complete
six-file implementation set and hashes, none of which is a harness file. The
integrated checkout must contain those exact paths/bytes. A clean integrated Git
identity remains an additional requirement, not a substitute for these pins.

The runner verifies the 109 original bound bytes against that pinned input spec,
then calls only the reviewed generator's pure original_options/make_page helpers.
It independently reconstructs the two required derived files (group HTML and
runtime adapter), the full content object and all dependency rows. The declared
content must match exactly. Missing, empty, duplicate, extra, reordered or changed
rows cannot be accepted by recomputing a self-reported content_identity. Actual
derived bytes must also match reconstruction; implementations are checked again
after reconstruction. The verified generator/file identities enter the harness
plan's group_verified field, so old harness plans must be regenerated. Existing
group-plan.json and bound input files are never rewritten by the harness.

A future generator version requires independent review, an explicit update of
these supported pins/sets and expected schema, ordinary completeness regressions,
and a new integrated commit/tree. This is not a generic/permanent manifest format
or permission to change the group author's files. No self-hash cycle is introduced.

For this repair, the displayed unittest command deliberately selects only ordinary
JSON/list/mock tests. The old test_fresh_alias_and_traversal and similar security
path tests are outside the permitted run scope. They remain unchanged in source;
do not run the full test_contract module under this restricted repair scope.
No CI discovery/selection/required checks are changed.

## Fixed O1 poll-observation A/A contract

`plan --mode poll-observation` explicitly creates `render-pilot/2` with
`target_profile=poll-observation120-v1`. It reuses the original v1 targets, batches,
eight jobs and global/local mappings, requiring exactly 238 complete RGBA pairs.
Both named arms use ordinary baseline PNG and the original navigation. A disables
the observer and B enables it; AB/BA changes only arm order. Combining this mode
with continuous300, fast PNG, group input, arbitrary targets or modified observer
limits is refused. Omitting this new mode keeps the old v1 default unchanged;
continuous300 remains its independently selectable fixed profile.

The plan records the observer's fixed caps, schema and native clock definition.
Both O1 arms use the same finite runtime/source binding (including the observer
module), resource-unknown policy, PNG parameters and cleanup/deadline path.
The extra observed subclass layer must delegate to the same frozen original Tab,
Browser and Tab.call references. Neither side gets a new navigation implementation.

One successful sidecar cannot certify an attempt. The runner retains the original
scope exit and primary error, then independently reads trace.json and, for B,
poll-observation.json. It checks exact bytes/SHA indices, baseline request mode,
render validation, namespace/job/entry/global/local scope, fixed limits/source,
closed nonempty intervals, failure/drop state and complete target navigation
coverage. Missing trace, sidecar, descriptor or a wrong scope fails. These checks
repeat before another arm and before quality. A's descriptor stays DISABLED with
null measurements and has no sidecar. Failed/NOT_STARTED attempts remain in the
arm report even if a sidecar descriptor itself says COMPLETE.

Page-scope records remain separate; arm/pair reports retain their verified indices
without pooling p95s, turning off into zero, subtracting A/A disturbance, or inferring
actual sleep/recoverable wait. Sidecar persistence and validation remain within
attempt/arm/pair wall and the original shared deadline. No PR221 consumer or event
strategy is modified. Real Mac, observer perturbation, resources and any performance
claim remain UNRUN; local PNG equality alone never grants promotion.

Explicit additional offline regression (the original renderer runs only against a
one-pixel mock CDP transport, never a browser or real supervisor worker):

```bash
python3 -m unittest experiments.render_pilot_runner.test_poll_integration -v
```
