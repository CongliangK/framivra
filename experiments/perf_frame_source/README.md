# FrameSource preparation experiment

This opt-in CPU experiment measures PNG consumer preparation only. It starts no
browser and does not render a user project. `measure.py` makes bounded synthetic
ordinary PNG files beneath an explicitly supplied scratch directory, removes its
temporary directory afterward, and prints JSON to stdout. It does not change CI.

## Narrow optimization

A homogeneous page still receives the complete authoritative PNG validation on
every consumer call. Mixed RGB8/RGBA8 pages also receive that validation before
conversion or derivative reuse. Their final source-change check additionally
rereads every original file as a bounded stream. When that SHA-256 equals the
fingerprint of the bytes just parsed in the same transaction, it reuses the
validated dimensions, format, CRC, zlib, filter and metadata conclusions. This
avoids a second decompression of identical bytes; it does not certify new bytes.

The trust assumption is SHA-256 collision resistance, already used by the source
and derivative digest gates. No stat result, completion marker, earlier consumer,
or previous process substitutes for the initial PNG parser. Changed bytes fail
the source-digest gate immediately, without adopting the new hash. Missing
or unreadable files fail. Environment checks, actual EOF discovery, derivative
validation, locks and directory checks all remain active. The bounded reread uses
at most a 64 KiB block and the opened file's initial size plus one byte, so a growing
file cannot extend that reread indefinitely. No persistent validation cache is added.

This changes the compositor's implementation digest, as required by its existing
cache contract. Segment/pass caches must therefore be compared at equivalent cold
or warm states in any downstream A/B run. Gfx source and canonical derivative key
algorithms are unchanged.

## Explicit checks

The following commands create temporary fixture files only. The measurement also
creates the named scratch parent if absent; use an owned scratch directory.

```bash
python3 -m unittest -v experiments.perf_frame_source.test_preparation
VTX_TEST_FFMPEG=ffmpeg python3 -m unittest -v tests.test_png_format_stability tests.test_png_pts_precision
python3 experiments/perf_frame_source/measure.py --scratch /tmp/vtx-frame-source-measure --frames 48 --repeats 3
python3 experiments/perf_frame_source/measure.py --help
```

For a comparison, run this same measurement script with `--repo` pointing to each
exact clean checkout, alternate baseline/candidate order, and retain the JSON.
Fixture generation and a separate cProfile pass are outside timing samples.
`homogeneous` measures one consumer; `readiness_then_input` demonstrates a separate
readiness boundary that this optimization deliberately leaves unchanged;
`mixed_cold` creates a derivative and `mixed_warm` consumes a prebuilt derivative.
The profile's cumulative times overlap and must not be summed. The hash-only
sample is a cost diagnostic, not an alternative correctness gate.

Synthetic fixtures use one RGB frame and otherwise RGBA with varying alpha and
hidden RGB. Their compressibility, cache state and Linux host are not a proxy for
real Mac render time. The environment callback is explicitly absent to isolate
PNG work. A mixed-page improvement cannot be extrapolated to all video preparation
or to homogeneous pages. No browser, full-project render, Mac run, CI suite or
independent review is implied by these explicit checks.

## Minimal Mac follow-up

Use the separately authorized common Mac validation window, the pinned baseline
and integrated candidate, identical existing gfx source bytes, dimensions, fps and
backend profile. Record function time and PNG parser calls for the two actual
mixed source pages, their warm derivatives, and one homogeneous control. Alternate
A/B order and preserve environment/source certificates. Compare all emitted input
arguments, derivative frame order/count/EOF, file hashes for unchanged RGBA, and
full decoded RGBA plus frame/packet PTS for converted RGB. Keep preparation time
separate from FFmpeg and account for the implementation-digest cache invalidation.
Measure the actual full video command only under the same downstream cache state;
never claim the synthetic saving explains the previous unassigned 70-second gap.

## Pinned historical-input replay candidate

`real_fixture.py` is a separate, opt-in experiment entry point for the reviewed
historical-input Mac window. Its CLI rejects hosts other than macOS / Python 3.9
and FFmpeg 9. It does not authorize that window, install tools, invoke a renderer,
or certify the historical producer environment as the current environment.
The baseline/candidate commits, four evidence-document hashes, two source pages,
small windows, order and budgets are fixed in the script. A different source or
larger budget requires a reviewed change, not a Local patch.

The input documents describe the saved b287 source bytes. The caller must resolve
all actual paths and the original binding hash from the existing verified
manifest. `--protected-file PATH=SHA256` registers additional original timeline,
configuration and output files for before/after checks. Source pages are read only;
original derived pages are never consumed. A new, previously absent output root
must be outside the original project and both checkouts. Each stage/round/page/arm
gets ordinary source copies, a new historical-fixture identity with an exact index
mapping, and its own physical canonical namespace. Both arms share the fixed input
fingerprints, not source or derivative files. Current code/graph digests are
recorded separately. Before production imports, each `-I -S -B` worker installs
a source-only loader for every tracked `vtx` Python module: it checks the pinned
source bytes, compiles those bytes directly, and records the actual source/code
hashes and runtime `_validated` signature. Existing `.pyc` files are neither
read by that loader nor changed; source SHA alone is not execution identity. This cannot re-sign an old cache into current production use.

The runner calls the existing `PngSequence.input_args()` twice in its selected arm
without replacing production checks. The fixture callback rechecks the fixed
mapping and copied historical marker; it explicitly does not call the production
current-gfx environment certificate provider. Timing covers that complete call,
including the callback, but excludes copy, audit, import and FFmpeg decode work.
Cold means no canonical derivative exists, not an OS-cold file cache. Warm uses
only the derivative just created in the same arm. No OS cache eviction is used.

Small physical copies are renumbered and checked before any full-page work.
Successful small checks unlock three full-page paired rounds, AB/BA/AB. All source
and canonical file hashes, warm no-rewrite checks, normalized input arguments,
full RGBA framehash rows and exact PTS/DTS/duration are checked. Every RGB source
is additionally decoded individually and matched to its canonical RGBA pixels;
alpha must be opaque. Later identical canonical bytes reuse the first round's
pixel qualification. No rawvideo file is written. Because decoding uses `-v error`, any nonempty
FFmpeg stderr is a failure even when rc is zero and framehash rows are complete.
Raw stdout/stderr, rc and the diagnostic failure remain in process receipts.
The B observer is not enabled.

Budgets are enforced cooperatively during local file operations and by polling
only directly owned subprocesses. RSS and disk observations are sampled; they
are not instantaneous hard OS limits. Deadlines include preflight time, but a
blocked filesystem call is not preempted. Only a live owned child is killed on a
budget breach; failure retains partial evidence and cannot advance to full pages.
There is no cleanup or resume mode. This requires ordinary local manifest-resolved
files; unexpected blocking or an exceeded budget stops the authorized attempt.

After only the output isolation/new-directory boundary is validated, the runner
creates an `attempt.json` receipt. Input, binding, repository, tool-version and
resource preflight checks then run within the failure-receipt boundary. Failure
or cancellation writes `result.json` with the exact phase and marks unperformed
checks UNRUN. Rejected output roots and argument errors cannot safely receive a
file; instead the frozen CLI emits a structured JSON attempt failure on stderr
without writing there. Local must retain the invocation stdout/stderr/exit code
as the outer attempt log, including these no-safe-root cases. Abrupt external
process termination or filesystem failure cannot guarantee a complete receipt.

Successful output has `result.json`, a small-stage receipt and a hierarchical
`MANIFEST.json`. Each case's `files.json` inventories every file with size/SHA256,
including source copies, derived PNGs/markers, fixture mappings, worker output,
FFmpeg framehash output and stderr. The root manifest hashes those case manifests
and root receipts; it cannot self-hash. Return its external SHA256 separately.
Failed runs are marked incomplete and retain whatever evidence was produced.
`PASS` denotes these fixture-equivalence checks, not a performance threshold,
current-renderer certification or full-film acceptance. Paired per-round timing
and medians remain observations with no whole-film extrapolation.

The zero-write CLI synopsis is:

```bash
python3 experiments/perf_frame_source/real_fixture.py --help
```

After separate approval, the reviewed script requires all of:
`--baseline-repo`, `--candidate-repo`, `--original-project-root`, `--binding`,
`--binding-sha256`, `--source-root`, `--inventory`, `--summary`,
`--bound-inventory`, `--historical-environment`, `--ffmpeg`, and `--output`.
Do not invent paths from the historical environment document.

The explicit tests below create only tiny ordinary temporary fixtures and owned
Python/FFmpeg processes. The flow tests require exact clean pinned checkout paths
and an already installed FFmpeg. They patch only synthetic fixture manifests and
the experiment's Mac preflight, not `PngSequence` or its checks. They exercise
Linux synthetic plumbing and provide no Mac, real-page or performance result.
They are not registered with CI and fail if their explicit tool paths are absent.

```bash
env PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v experiments.perf_frame_source.test_real_fixture_units
env PYTHONDONTWRITEBYTECODE=1 VTX_FIXTURE_BASELINE=/verified/base-checkout VTX_FIXTURE_CANDIDATE=/verified/candidate-checkout VTX_TEST_FFMPEG=/verified/existing/ffmpeg python3 -m unittest -v experiments.perf_frame_source.test_real_fixture_flow
```

The explicit R1/R2 review regressions additionally use ordinary source copies and
a deliberately stale ordinary `.pyc` in a temporary directory, and controlled
Python subprocess diagnostics. They do not alter either pinned checkout or any
user bytecode cache; no path-security negative is included.

```bash
env PYTHONDONTWRITEBYTECODE=1 VTX_FIXTURE_BASELINE=/verified/base-checkout VTX_FIXTURE_CANDIDATE=/verified/candidate-checkout python3 -m unittest -v experiments.perf_frame_source.test_real_fixture_review
```
