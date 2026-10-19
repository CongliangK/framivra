# Framivra paper-workshop film

An editable 120-second source project: 1920×1080, 30 fps, 3,600 frames, eight continuous scenes. The bird is cut from paper, gathers materials, assembles a gate, takes flight, unfolds a world, exposes its source layers, and returns to the Framivra wordmark.

This is a draft candidate. See [entry-manifest.json](entry-manifest.json) for the exact build-time source inputs and unresolved checks; preflight reports current local media availability. Source tests and static SVG images do not establish actual Framivra rendering, audio approval, playback quality, or performance.

## Prepare local media

Git contains the timeline, scene source, score, recipes, small vector outlines, licenses and exact hash manifests. It excludes all generated audio/video, final movies, caches and large outputs. A fresh clone deliberately cannot render until the five runtime inputs are prepared.

Unpack the separately supplied canonical runtime asset pack into a new local directory. It contains audio/T17.flac through T20.flac and media/paper-background.mp4. From the repository root run:

```bash
python3 templates/promo-demo/v1/production/scripts/prepare_assets.py --from /absolute/path/to/unpacked-pack
python3 templates/promo-demo/v1/production/scripts/preflight.py --source-only
```

The importer uses only that explicit local directory, verifies exact hashes and complete audio PCM, and refuses differing existing files. It rejects symlinks in any path component; on macOS use the canonical /private/tmp or /private/var path rather than its /tmp or /var alias. It does not download anything. Missing media is reported explicitly by preflight, including in source-only mode. Importing assets does not approve the final film or the audio. Keep media and output files ignored; do not force-add them to Git. The project source test enforces a 1 MiB per-file limit and rejects tracked generated audio/video/archive files.

For optional offline reconstruction, music/README.md documents the pinned external Scoreweft renderer; audio/stems.json records the exact subsequent lossless FLAC encoding command. media/paper-recipe.json contains the complete paper-generation command and working directory. Reproduction requires the documented existing tools. Different tool versions may change encoded bytes; a hash mismatch is an error, not permission to rewrite the reference manifest. Changed music or media needs a fresh reviewed binding.

The original Demo source, score and generated media use the repository MIT license; third-party font outlines and notices retain their original licenses. See notices/ORIGINAL-DEMO-MEDIA.md.

## Open and edit

[timeline.json](timeline.json) is the real project timeline. It has 15 visual tracks, one native subtitle stream and four distinct audio roles. Those 20 planned roles are not a claim that every contribution has passed acceptance. The four lossless FLAC stems are independently meaningful runtime inputs supplied separately; generated audio/video files are not stored in Git. The S5 waveform comes from the real native audio-stage PCM, not an invented signal. Missing or corrupted media is an explicit error.

The editable scene modules are in [src/scenes](src/scenes). They share one deterministic frame runtime and one geometry contract. [production/cues.json](production/cues.json) holds contact timing. [production/track-map.json](production/track-map.json) maps each role to its drawing ownership and removal test.

Rebuild after changing a scene, cue, font provider or timeline. This command writes generated HTML, the shared bundle and manifests within this package; it does not render video:

```bash
node templates/promo-demo/v1/production/scripts/build.js --draft
```

There are 19 thin HTML entries for the 14 GFX roles. Each contains an immutable clip ID. The renderer supplies local `?f=N`; the runtime maps it to that entry's global frame window. Only frame input is read from the query. No autonomous animation clock, network fetch or browser optimization is enabled.

## Check the source

These commands do not launch a browser. Run them from the repository root:

```bash
node --test templates/promo-demo/v1/production/tests/runtime.test.js templates/promo-demo/v1/production/tests/integration.test.js
env python3 templates/promo-demo/v1/production/scripts/preflight.py --source-only
```

The preflight checks the packaged paper media, fully decodes all four FLAC stems against exact PCM hashes, validates every waveform bin and its native-audio reference, and reports changed dependencies and caption-font problems. `--source-only` reports source-integrity success separately from unfinished acceptance. Without that flag, unfinished requirements give a nonzero exit status. A test skip is not a pass.

The native ASS subtitle style requests Noto Sans CJK SC Bold. The font is an external prerequisite; its license notice is included, with the exact expected family, version and SHA-256 in [production/caption-font.json](production/caption-font.json). The package does not install the font or change native font selection. The preflight checks the actual fontconfig match when available; alternatively supply `--caption-font /path/to/the/actual/font` to check its bytes. Actual ASS font selection remains a render-time check. An unavailable or different font must not be silently treated as verified.

Source labels and the brand use local vector outlines, with their licenses and exact provider identities preserved. They do not rely on machine text fallback. [production/source-bindings.actual.json](production/source-bindings.actual.json) records labels read from the emitted timeline, including the actual source basename, start and duration.

## Render on your own environment

Use a reviewed Framivra revision whose platform and renderer checks work on your machine. This package does not fix or bypass renderer eligibility. Import the exact runtime assets first; an available exact native subtitle font is also a prerequisite. Preserve the draft audio-approval status until you have listened to the complete arrangement.

Before a full render on a different FFmpeg build, run the native audio-only comparison below. Replace all paths and the commit with your verified checkout; use a new output directory outside this production folder. Select the already-qualified FFmpeg/ffprobe by process-local PATH, without dynamic-loader overrides. The helper checks the exact stem hashes and audio-graph equivalence and records the actual toolchain, receipt and PCM identity. Its detailed output contains local execution paths: keep it outside Git and do not upload it with the public source. The shipped audio/native-audio-provenance.json is a portable reference summary, not that raw execution proof or a receipt for your machine.

```bash
python3 templates/promo-demo/v1/production/scripts/run_native_audio_projection.py --repo /absolute/path/to/framivra --project /absolute/path/to/framivra/templates/promo-demo/v1/production --out /absolute/path/to/new/audio-comparison --expected-commit <reviewed-commit>
python3 templates/promo-demo/v1/production/scripts/preflight.py --source-only --postmix /absolute/path/to/new/audio-comparison/out/audio.wav
```

Require `supplied_postmix_pcm_verified: true` and no source errors. A decoded-PCM mismatch stops acceptance: preserve the actual output and regenerate the waveform, portable provenance/reference together as a newly reviewed binding, retaining the new raw proof and receipt locally, then rebuild. Do not edit an expected hash to make a mismatch pass. The full film must subsequently pass the same comparison against its own out/audio.wav.

### Bind a reviewed native audio result locally

Investigate a mismatch before selecting a new result. Once that specific native result has been reviewed, the explicit binder can create a separate draft project. It does not decide whether an audio difference is acceptable. Supply both the WAV-file and decoded-PCM hashes established by the comparison, the source checkout commit and the commit recorded by the native run.

The command below writes a new directory outside the repository, source project and native run. It never replaces the shipped reference or an existing destination. Keep the completed native run at its original location and use its same qualified FFmpeg/ffprobe. The binder verifies the recorded core bytes, runner, full/projection timeline, stems, graph, successful stages, receipt and selected PCM. A different source commit is permitted only with the same recorded core bytes, projection runner and full timeline. It copies tracked project source and the five verified media inputs; caches, output audio and raw execution receipts are not copied into the project.

```bash
python3 templates/promo-demo/v1/production/scripts/bind_native_postmix.py --repo /absolute/path/to/framivra --project /absolute/path/to/source-project --projection /absolute/path/to/completed/audio-comparison --out /absolute/path/to/new/bound-run --expected-commit <reviewed-source-commit> --expected-run-commit <recorded-native-run-commit> --expected-wav-sha256 <selected-wav-sha256> --expected-pcm-sha256 <selected-decoded-pcm-sha256>
```

The destination must stay private and unmodified while binding runs. The binder holds its newly created directories open, creates files exclusively without following links, and rejects changed directory or file identities before writes and build/check stages. It rechecks the selected WAV, native proof and receipt before completion. On failure it removes only entries it still owns; an incomplete directory may remain if an entry was replaced or an unknown file appeared. Do not reuse that incomplete directory. These integrity checks do not sandbox untrusted subprocesses.

Only a successful `binding-result.json` marks a completed draft binding. The new renderable source is `bound-run/project/`; sibling verification files stay private and outside Git. The binder recalculates all 480 fixed-scale stereo-union bins, rebuilds the shared bundle and dependency manifests, and verifies the exact PCM and bins together. Changed dependency bytes invalidate old GFX cache identities without changing HTML clip IDs or the timeline. The original source remains unchanged. The previous AAC diagnostic is not inherited; the new run needs its own encode and listening checks.

Review the generated binding and its T13 waveform before rendering this new project. Audio approval and full-film acceptance remain pending. The full render's own `out/audio.wav` must then pass the copied project's preflight with both `supplied_postmix_pcm_verified` and `supplied_postmix_bins_verified` true. No comparison uses an error tolerance, resampling or a gain adjustment.

The following command is a user-run recipe, not evidence that it has been executed. It writes caches and output inside this project:

```bash
env python3 -m vtx -t templates/promo-demo/v1/production/timeline.json all
```

Check the resulting file for exactly 3,600 decoded frames at 30 fps; inspect all seven scene handoffs, contacts, source-text readability and the ending. Listen for four independently useful audio roles, the deliberate quiet passages, synchronization and the in-window decay. Record the actual backend, font selection, warnings, output hashes, loudness and true peak. Before accepting S5, compare the actual user-run postmix with its reference:

```bash
python3 templates/promo-demo/v1/production/scripts/preflight.py --source-only --postmix templates/promo-demo/v1/production/out/audio.wav
```

The cloud reference used unchanged native audio stages at the pinned source revision and FFmpeg 7.1.5. Another FFmpeg build may produce different PCM; a mismatch must be investigated and the waveform regenerated from that actual PCM before acceptance. Do not substitute the rough animatic or static Inkscape images as renderer evidence.

## Edit the music

The delivered stems render directly without Scoreweft. [music/score.json](music/score.json) and [music/README.md](music/README.md) provide the original editable score, scheduler/master recipe, licenses, and exact source hashes. Recomposition requires an explicitly supplied pinned Scoreweft checkout and the documented existing tools; no engine is vendored and no script downloads or installs dependencies. Rebuild and re-review the native audio reference and waveform whenever sound changes.

## Optional effect comparisons

The film has six effects enabled. A diagnostic variant writes to a separate directory and never changes the all-on entry pages:

```bash
node templates/promo-demo/v1/production/scripts/build.js --draft --off=material_masks
```

Structural masks preserve bird/offcut order, gate crossings and physical contacts in every profile. The switch affects only optional weave and reveal workload. Other controls are path_deformation, text_reveal, translucent_trails, gradient_alpha_reveal and local_blur. Diagnostic images are comparisons, not finished footage.

## Scope notice

2026-10-18: the new production-only README and source package were announced for review before documentation was written. The previous preview, standard demo, SKILL routing, core schema/renderer, platform settings and permissions are unchanged. There is no installed consumer mount in this cloud checkout to certify. Combined-source independent review and the user's actual render remain separate acceptance steps.
