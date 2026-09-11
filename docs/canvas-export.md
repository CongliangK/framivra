# Portable graphics rendering, optional export channels and static-span dedup

Local HTML URLs are generated with `Path.resolve().as_uri()` in both CDP and CLI
renderers. This preserves Windows drive letters and encodes spaces, Unicode, `#`
and other URL delimiters. Subtitle filenames are escaped for both FFmpeg's option
parser and filtergraph parser; command arguments are passed directly, without a shell.

A page whose **entire intended output** is one canvas may opt in:

```javascript
// Draw the requested ?f=N frame synchronously before exposing this function.
window.__vtx_export_png = () => canvas.toDataURL('image/png');
```

The canvas bitmap must have exactly the requested render width and height (normally
1920 x 1080), in 8-bit RGB or RGBA PNG format. Use a transparent canvas for overlays.
CSS decorations and other DOM elements are NOT included by canvas.toDataURL.
Do not opt in if those elements are part of the intended output. This protocol
expects an immediate string, not a Promise, and does not wait for asynchronous assets.

The renderer validates the data URL, PNG chunk boundaries/checksums, header,
dimensions and end marker before caching the frame. This is an envelope validator,
not a general untrusted-file PNG decoder. Invalid exports fall back to two-background
screenshots of the same frame. Pages without the function keep the existing path
(including the CDP transparent-background override fast path and its flatten guard —
opt-in canvas pages bypass screenshot machinery entirely).
The `canvas_export` statistic reports page opt-in, not a guarantee that every frame
used the fast path. Project caches are content-addressed per artifact;
see `cache-provenance.md` for tracked inputs, explicit HTML dependencies and
migration requirements.

The canvas channel is per-frame by design. The batch snapshot channel that once
aimed to replace it (DOM-serialized SVG via `foreignObject`) was falsified and
removed — see "Falsified export channels" below.

## Static spans (`window.__vtx_static_spans`)

A page may declare frame ranges in which the picture is guaranteed constant:

```javascript
// Frame numbers, half-open intervals [a, b); must stay inside [0, N) and not overlap.
window.__vtx_static_spans = [[40, 184], [540, 772]];
```

Semantics: within each span every frame is pixel-identical to the span's first
frame. The renderer consumes declarations by default (`VTX_GFX_STATIC=1`): only
each span's head frame is rendered, member frames are byte-copied from it
(`static_copied` counter, `span×k` per-page tag). Members that already exist on
disk are left untouched; a malformed declaration degrades lazily to plain
per-frame rendering. Declaration missing or partial? Only declared ranges are
deduplicated — everything else renders normally.

In the voice-ime benchmark project 8 pages declare spans; 1474 of 12024 frames
(≈12.4%) are byte-copied instead of rendered.

Rules for page authors:

- Declarations must be **generated, never hand-written**: scan the page frame by
  frame with a DOM-serialization SHA-1 fingerprint (DOM equality ⇒ raster
  equality ⇒ byte equality). Tooling is archived with the voice-ime project at
  `cache/reports/gfx_opt_20260911/` (`scan.py`, `inject.py`, `decl.json`).
- Spans below ~30 frames are not worth declaring.
- Pages containing `<canvas>` must not opt in: canvas bitmaps do not enter DOM
  serialization, so the fingerprint would produce false positives.
- Anything that varies with `t`/`f` (sin breathing, in-flight number rolls) must
  not fall inside a span.
- Correctness is double-checked: endpoint re-verification by the generator plus
  full golden comparison (`scripts/gfx_golden.py`). After editing a page that
  declares spans, re-scan before re-declaring.

## Falsified export channels (do not resurrect)

Both channels below were implemented, measured against a zero-diff golden
baseline (`scripts/gfx_golden.py`), and rejected. Details and full data:
`DEV.md` §二 (gfx v2 证伪记录).

- **foreignObject batch snapshot** (DOM serialized into SVG, exported via
  Image → canvas → toDataURL): text/font/transform/drop-shadow matched the
  screenshot path bit-for-bit, but the SVG-image rasterization context itself
  introduces irreducible deviation — deterministic ±1/255 jitter on
  `filter:blur(150px)` pages (~1.5% of pixels) and max|Δ|=25–61 / 7–14% differing
  pixels on radial-gradient pages (gradient dithering + low-opacity text AA).
  A control run of unmodified original frames through the same pipeline produced
  bit-identical deviation, proving it is intrinsic to the pipeline, not to page
  refactoring. Driver and `VTX_GFX_BATCH` env removed.
- **navfree screenshot** (`__vtx_render(f)` mutates the page in place, one
  navigation per tab; `VTX_GFX_NAVFREE`): 40/40 frames byte-identical on an idle
  single page, but under an 8-instance load the four `__vtx_render` pages drift
  from ~f22 on (387/127/641/430 differing frames on golden full comparison) and
  the same pages run 1.5–4.4× slower per frame than per-frame navigation (DOM
  mutation triggers full-page re-rasterization; blur pages worst). The code is
  kept behind the default-off env with fallback and warnings — **do not enable**.
  The `__vtx_render` page protocol itself stays (it is harmless and is the
  intended entry point for future channels).

## Verification

```sh
python -m unittest discover -s tests -v
```

Install the existing NumPy renderer dependency. Set `VTX_TEST_CHROME` to the Chrome
executable and `VTX_TEST_FFMPEG` to an FFmpeg build with the subtitles filter to run
the real integration tests. Without them those tests are explicitly skipped.
The fixtures are generated in the system temporary directory (`TEMP`/`TMP` may be
set to a project-local scratch directory); no personal media is needed.

Coverage: valid/truncated/corrupt PNG and dimension mismatch; real two-frame Canvas,
ordinary transparent HTML and invalid-export fallback; alpha and black/white
compositing agreement; special-character paths; actual subtitle burn-in. Tested on
Windows by the PR; the merge re-ran the full set on Linux (Chrome 152 + ffmpeg)
before acceptance. Existing CDP socket ResourceWarnings are outside this patch's scope.

## Full pipeline regression

`tests/run_e2e.py --repo <checkout> --output <new-directory> --ffmpeg <executable> --chrome <executable>`
generates synthetic 12-second media and independently runs all stages with one
and two video segments, then strict QC, full decode, black-frame/true-peak checks
and a 360-frame downscaled comparison. The output directory must not exist.
`tests/verify_e2e.py <output-directory> --ffmpeg <executable>` compares decoded audio
sample-for-sample and checks 19 full-resolution frames, including the segment seam,
subtitle boundaries and graphics boundaries. Both scripts require NumPy.

The full test additionally exposed concat relative-path resolution in a Windows
folder containing `#`. Segment lists now use UTF-8 absolute slash-separated paths
with concat quoting; this avoids resolving relative URLs against that folder.
