# Workshop opening: HTML preview candidate

This is the **10-second opening candidate**, ported from the original workshop V03 rough animatic. User art approval is pending. It is not a frozen 120-second production, a finished promo, or macOS/Framivra rendering evidence. The rough Pillow/FFmpeg animatic's independent frame review does not certify this HTML port.

The authoritative preview dimensions, expected frame count, track accounting, source hashes and unrun gates are in [preview.json](preview.json). [timeline.json](timeline.json) contains only two real visual tracks: visible paper background and animated SVG foreground. There are no audio events, subtitles, empty padding tracks, or screen-recording claims. The broader planned 20-track production is separate and is not implemented by this preview.

## What is drawn

[opening.html](opening.html) is a self-contained local HTML/SVG producer. It reads integer frame `?f=N`, computes the whole scene synchronously, and uses a 1280×720 viewBox at the timeline's 1920×1080 resolution. The same four contacts, retained material, waste frame, camera, bird motion and carried-wing exit from V03 are preserved. SVG rasterization and antialiasing are different from Pillow; geometry agreement is not pixel agreement.

The default page is transparent over the visible paper video. For standalone frame inspection only, append `&paper=1` to add the original flat paper color, for example `opening.html?f=176&paper=1`. The timeline uses the default transparent mode. Frame input is floored and clamped to the preview range; missing or nonfinite input becomes frame zero. Optional `window.__vtx_render(f)` is synchronous and order-independent; the renderer's experimental navfree mode is not enabled.

The page has no external assets, fonts, text overlays, timers, random values, CSS animation, network requests or static-span optimization. No font is bundled or licensed by this package. Geometry and colors are original project artwork. The paper video is generated from a solid color, not third-party footage; its silent stream exists because the current media-source validator requires audio. No audio track consumes it.

[paper-recipe.json](paper-recipe.json) records the exact generation command, actual FFmpeg build, source hash and ffprobe result. Rebuilding with a different encoder build may change bytes; verify and review any changed asset rather than claiming cross-build byte determinism. The existing default gfx dependency policy is used: the current strict static-HTML subset rejects JavaScript and SVG. No core policy, renderer or security setting is modified.

## Offline contract checks

From the repository root, these checks do not launch a browser:

```bash
env python3 -m unittest tests.test_promo_opening_preview -v
```

The Node harness checks all frames with fresh-document, forward, reverse and repeated calls. The geometry oracle was captured from the frozen V03 source's drawing calls before rasterization; its normalized screen-space primitives and paint order are compared at four decimal places after eight-decimal stabilization. This is a source-geometry regression check, not a browser, screenshot, pixel, timing or playback test. Real FFmpeg tests separately decode the paper asset. Optional tools that are absent are reported as skips, not passes.

## Deferred manual/external Mac validation

**Mac gfx is BLOCKED on this candidate's exact `4e234e2` base, even with Chrome and FFmpeg installed.** The unchanged `vtx/gfx_environment.py` rejects Darwin with `platform resolver requires Linux`; metadata validation can succeed, but the gfx environment certificate still blocks rendering. This preview does not contain a Mac resolver fix.

[Issue #200](https://github.com/CongliangK/framivra/issues/200) and [PR #201](https://github.com/CongliangK/framivra/pull/201) are a **separate integration dependency**, not evidence of a merge or Mac readiness. Before the preview recipe can be used, that integration must be independently reviewed and fresh real-Mac environment/CDP validation must pass against its actual resulting commit. Record that exact commit and new runtime evidence; this candidate's offline results cannot certify the integrated tree.

**UNRUN; manual/external execution owner is pending.** The user has withdrawn local-computer control: dot is prohibited from controlling the user's computer. These commands are optional guidance for a separately appointed manual or external tester, not a scheduled or authorized dot action. A failed platform/renderer identity gate remains a blocker; do not bypass it, install anything, or change permissions or security settings for this preview.

The following commands **write a separate temporary run directory, cache and video output**. They are a deferred test recipe, not a validated Mac procedure. Only after the integration and real-runtime prerequisites above pass, run them from that exact reviewed repository root:

```bash
RUN="$(mktemp -d "${TMPDIR:-/tmp}/framivra-opening.XXXXXX")"
cp templates/promo-demo/v1/preview/{opening.html,timeline.json,paper-background.mp4} "$RUN/"
env VTX_GFX=cdp python3 -m vtx -t "$RUN/timeline.json" validate
env VTX_GFX=cdp python3 -m vtx -t "$RUN/timeline.json" prepare
env VTX_GFX=cdp python3 -m vtx -t "$RUN/timeline.json" gfx
env VTX_GFX=cdp python3 -m vtx -t "$RUN/timeline.json" video
```

Inspect `$RUN/out/video_noaudio.mp4`. The silent preview deliberately stops before audio/mux: the current audio mixer requires events, so `all` is not the recipe for this source. The source video has a silent audio stream; the composed preview video has no audio track. No music or soundtrack has been approved by this package.

Before delivery, verify the actual rendered file is 1920×1080, 30 fps and 300 decoded frames; inspect every contact and separation boundary, recognition hold, 263→264 continuity and final full red coverage. Check real-time motion, transparency/composition edges and repeat-render consistency. Record the actual backend and any fallback; a requested CDP setting alone is not evidence that CDP produced the pixels. Preserve the real renderer identity, command logs, frame checks and output hashes. Do not reuse the rough animatic's pixels or review as proof of this render.

## Consumer-surface notice

2026-10-18: a new preview-only package under `templates/promo-demo/v1/preview` was announced and its narrow README/assets/test scope reviewed before documentation was drafted. Scope follows [#91's explicit new promo task](https://github.com/CongliangK/framivra/issues/91#issuecomment-6246953934). Existing standard-demo, SKILL routing, core schema, renderer and Mac identity files remain outside this change. No live skill links were changed; this cloud checkout has no installed six-link consumer mount to certify. Independent review and actual Mac rendering remain required.
