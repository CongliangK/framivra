# HTML/CDP workflow study

Opt-in measurement for [#346](https://github.com/CongliangK/framivra/issues/346), not a production optimization or CI gate. Conclusions live in [the research index](../../docs/reports/development-history.md#htmlcdp-工作流研究346). The primary 30s SVG benchmark remains unchanged.

## Reproduce

Requires Python 3.12, NumPy, Pillow, psutil, Chrome, fontconfig, installed Noto Sans CJK SC, and a complete FFmpeg/FFprobe build with libx264, AAC, PNG and H.264 decoding. The captured identities pin the versions actually measured; other environments are new observations. No package is installed by the runner.

**Writes media, logs and caches, and starts local browsers/processes.** Choose a new output directory on a disk with adequate space. Keep `TMPDIR` short: Chrome's Unix socket pathname limit also applies to its temporary socket directory. A long study output path is fine; a long socket path is not. Do not run competing benchmarks or change inputs during a run.

```bash
# Replace these with existing physical paths; mkdir is an explicit write.
PYTHON=/path/to/venv/bin/python
FFMPEG=/path/to/ffmpeg
CHROME=/path/to/google-chrome
SHORT_TMP=/short/physical/study-tmp
mkdir -p "$SHORT_TMP"
"$PYTHON" -m experiments.perf_html_workflow.run \
  --output /disk/new-html-baseline --tmpdir "$SHORT_TMP" \
  --ffmpeg "$FFMPEG" --chrome "$CHROME" --pairs 3 --tabs 4
```

The unchanged public `chart_curve.html` is a production-derived tech-talk DOM/SVG/text page, 600 frames / 20s / 1920×1080 / 30fps. The generated timeline uses its full declared duration with a deterministic stereo calibration tone, no private assets, no narration/PIP/subtitles. This tests the actual `vtx all` chain, **not a complete tech-talk episode or artistic acceptance**. It is not the retired 12s baseline.

`cold` creates new project cache/out directories; `warm` immediately reruns the same project with retained caches, not a prewarmed browser. OS page caches are not cleared. Each all run retains command, exit, stdout/stderr, event stream, observation, cache inventory, certificates/receipt, built-in QC report and a copy of the final movie. The runner pins an empty task-local configuration rather than inheriting checkout settings. Default `core` cache policy retains a `.vtxp` lossless graphics pack, retires loose PNGs, and does not retain working media caches. The warm run must preserve the pack bytes and mtime; it still rebuilds downstream media. Independent quality verification reads all 600 original PNGs from that pack without restoring or changing it. Failed attempts remain intact; reattempts use a new output directory. No fallback is qualified as CDP success.

Only after inspecting the baseline should an opt-in single variable be tested (the runner exposes tabs). Keep all other settings, input bytes, tool/font/runtime identities, and the quality checks fixed. For paired A/B runs use independent output roots and alternate arm order; do not call a separate later batch a randomized comparison. CPU encoding, one video segment, two IO workers and existing CDP split settings are fixed study controls, not changes to application defaults.

## Measurements and quality

- Outer `all` wall includes CLI startup, identity work, full pipeline and observer serialization; excludes fixture creation and later quality checks.
- Browser start covers process launch → DevTools readiness → tab target creation; WebSocket/session setup is outside that timer.
- `navigate` includes readiness/font waiting. `capture_png_transport_json` combines browser capture/PNG encoding, response transport and JSON parsing, excluding host base64 decoding. Renderer export statistics include base64 and only successful target frames; observed capture spans include setup probes and failures.
- Atomic PNG writes are producer writes, not downstream reads. The event stream's video-stage wall combines PNG read/decode, composition, video encode and output writes. Read-only, encoder-only and write-only costs are `null` with reasons, never inferred by subtraction.
- Per-call work sums overlap across threads. Each category's interval union is elapsed coverage, but categories can also overlap; neither sums into overall wall. Successful-frame p50/p95 are per block, not pooled.
- CPU and peak process-tree summed RSS are 100ms sampled lower bounds; short-lived children may be missed and summed RSS includes shared pages. Linux per-process IO can include waited-for children, so the recorded tree IO sum may double-count: it is a sampled aggregate, **not a lower bound or physical/device IO**. No exact peak or GPU/VRAM attribution is claimed.

After measurement, run strict QC and independently decode **both** retained cold/warm movies (commands write evidence; supply the same fixed environment/PATH as the run):

```bash
"$PYTHON" -m vtx -t /disk/new-html-baseline/pair-1/timeline.json qc --strict
"$PYTHON" -m experiments.perf_html_workflow.quality \
  --project /disk/new-html-baseline/pair-1 \
  --video /disk/new-html-baseline/1-cold/final.mp4 \
  --output /disk/new-html-baseline/1-cold-quality.json \
  --ffmpeg "$FFMPEG" --ffprobe /path/to/ffprobe
"$PYTHON" -m experiments.perf_html_workflow.summarize \
  /disk/new-html-baseline --output /disk/new-summary.json
```

Repeat for every pair/state/arm. Compare all 600 PNG RGBA hashes, all decoded video frames/timestamps, and normalized decoded audio samples/timing. Retain byte hashes separately. QC, equality and black-frame checks do not certify content or universal cross-machine equivalence. Human visual inspection is separate. The summary reads existing evidence only and does not grant acceptance.

Ordinary unit checks (no media-performance claim):

```bash
python3 -m unittest experiments.perf_html_workflow.test_study -v
```
