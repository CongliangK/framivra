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
