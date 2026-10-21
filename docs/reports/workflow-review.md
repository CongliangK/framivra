# Production workflow review: correctness before throughput

This review follows a Windows 54-second 1080p30 production and targeted synthetic regressions. It distinguishes a completed component from a verified editing or listening workflow. No private production media is included in the repository.

## What this contribution changes

An audio audit should let a creator judge the same source excerpts and event settings as the production timeline. Previously, it always exported the first source of each track, copied the first event's volume in whole mode, and intersected source-time silence spans with destination-time events. This can play the wrong clip, flatten intentional level changes or omit a delayed sound altogether. Relocated graphical dependencies can also prevent an audio-only audit from validating.

The correction is deliberately scoped: per-source exports, full event preservation, explicit source-to-destination mapping, conservative whole-event fallback for loops/fades, and cache reuse tied to actual content. See [the contract and limitations](https://github.com/CongliangK/framivra/blob/cbfaed74761657401b04119602f2a87dd1fd0ee7/docs/audio-audit.md). A separate preceding [post-mix fix](https://github.com/CongliangK/framivra/blob/cbfaed74761657401b04119602f2a87dd1fd0ee7/docs/post-loudness.md) measures the current WAV instead of reusing the raw mix's pre-gain loudness.

The first five real FFmpeg regression cases failed against the pre-fix audit. The corrected tests directory passed 23/23 on Windows, including 14 audit tests and the existing Chrome/FFmpeg and post-loudness regressions. An actual production audit then validated directly without the former project-specific dependency workaround: two sources, two tracks, ten events; music retained its envelope and nine sound-effect spans were mapped. Blender was not installed, so this result certifies the audit data and media chain, not `.blend` playback.

## Follow-up priorities and acceptance gates

| Priority | Observed issue or open boundary | Next focused change | Evidence required before acceptance |
|---|---|---|---|
| High | Blender loop expansion with nonzero `src_in` uses a full-length first repetition; audio verification focuses on coverage | Verify source offsets, volume, pan and loop boundaries in the bridge | Real Blender fixture with a distinguishable sound in each half of a source; inspect strips and rendered audio against the timeline |
| High | A prior bundle can remain after a failed run | Publish an explicit bundle receipt binding timeline, exports and optional blend; invalidate before rebuilding | Inject failure into every stage and prove old success cannot be presented as current |
| Medium | Numeric length-based subtitles can create unreadable fragments; recognition confuses homophones | Add word-boundary/minimum-display-time constraints and an explicit correction record | Multilingual fixtures plus a human-checked ambiguous phrase; never substitute confidence score for confirmed wording |
| Medium | A 54-second graphics stage took about 540 seconds on the tested machine; video composition took about 33 seconds | Measure per-page decode/draw/PNG costs and emit progress before optimizing | Equal-pixel before/after comparisons, repeated cold/warm timings, peak memory and cache size; one timing is not a benchmark |
| Medium | Model/server modules and Blender require separate installations | Component-selective dependency probes with clear `PASS/FAIL/NOT_RUN` | Clean Windows install path and one actual request per enabled service, not import-only tests |
| Medium | Historical browser socket ResourceWarning remains in integration tests | Close browser/CDP sockets in every failure path | Repeated launches and injected failures with stable process/socket counts |
| Lower | MP3 audit caches retain earlier content generations | Add a dry-run reachability cleanup tied to audit references | Preserve all referenced exports and unique recovery material, reject concurrent writers |

## Repeatable production handoff

1. Record the input material, source commit and enabled components before large downloads or rendering.
2. Lock reviewed wording and frame-based event placements; preserve manual corrections separately from raw ASR.
3. Validate after timeline edits, preview the actual framing/subtitles, then execute only affected render stages with their required predecessors.
4. Measure current audio, re-mux, and check decoded delivery loudness/peak, frame count, black frames and sampled synchronization.
5. Generate a fresh audit package and explicitly distinguish MP3/event validation, Blender playback and subjective listening.
6. Bind deliverables to current source/input evidence; retain editable sources, unique failures and reproducible tests. Remove only demonstrated redundant checkouts/caches after checking references.

Technical gates do not establish artistic quality, platform acceptance or full availability of ASR/TTS/image/matting services. Performance and cross-platform claims should name their actual tested scope.
