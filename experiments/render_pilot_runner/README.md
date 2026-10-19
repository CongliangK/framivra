# Bounded b287 render pilot

Isolated opt-in experiment for the frozen TODAY plan identified in contract.py.
Nothing imports this directory from production. It changes no CI discovery,
selector, shard, required check, event schema, old cache or manifest.

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
