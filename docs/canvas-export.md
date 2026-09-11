# Portable graphics rendering and optional Canvas export

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
