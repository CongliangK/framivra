# Portable R4 score and generation recipe

This folder contains the complete editable 120-second score and project-specific generator, scheduler, master, and waveform scripts. It contains no Scoreweft engine copy. All dependencies must already be available; the scripts never fetch, install or update them.

## Required dependencies

- Python 3.12.14 and NumPy 2.3.5 were tested.
- FFmpeg and ffprobe 7.1.5 were tested.
- An explicitly supplied Scoreweft checkout or source snapshot pinned to aa5e7781952418f5881c41f166f237dc7a24111c: https://github.com/CongliangK/scoreweft/tree/aa5e7781952418f5881c41f166f237dc7a24111c
- The required 87 bgm_gen runtime files plus LICENSE/NOTICE are listed in SOURCE_INTEGRITY.json. Their exact hashes and runtime-file inventory are checked before any external runtime import; use a clean pinned source tree. Unlisted repository files and .git metadata are not read. A mismatched source fails rather than downloading a replacement.
- The included finite render guard requires Linux /proc and pidfd support. Other operating systems need an equivalent reviewed resource supervisor. No unguarded render is recommended.

The tested runtime versions are the reproducibility reference; other versions are not promised bit-identical. Scoreweft's full MIT license and notices are retained in notices/. The original score, project recipes and generated Demo media are also distributed under the Framivra repository MIT license, as explicitly authorized by the project owner; see ../notices/ORIGINAL-DEMO-MEDIA.md. This does not relicense third-party fonts or runtime dependencies.

## Render

From this folder, provide the absolute path to the already pinned Scoreweft source and choose a new output/report path:

    PYTHONDONTWRITEBYTECODE=1 BGM_NO_PARALLEL=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 python3 scripts/render_guard.py --report render-guard.json --timeout 600 --cap-gb 3 --floor-gb 6 -- python3 scripts/render.py --scoreweft-root /path/to/pinned-scoreweft --out rendered

Only one heavy audio job at a time. The guard refuses to overwrite an existing report and keeps at least 6 GiB available memory. Its unchanged SHA-256 is fffc34497f93fabdd734e84c5aea6401ebf8df5efdbb06b818b18ce819936102. No system, authentication or permission settings change.

score.json is the exact-sample score of record. Edit its events, palette and explicit project-level processing, then render it. scripts/compose.py regenerates the original score and standard editing projection, overwriting manual score changes if run. The projection alone omits the custom sample scheduler, per-event gain/pan and mastering and is not claimed bit-equivalent.

The generation/mixing logic is identical to frozen R4. Dependency loading differs only to require the external source root and verify the recorded file set before import. The engine uses an explicit diagnostic runtime override and does not create runtime.json. Event velocity is linear gain after a unit-velocity one-shot; nylon has the documented R4 attack envelope. The renderer produces four PCM24 stems, their bit-exact unity sum, lossless FLAC and an actual-PCM S5 waveform.

## Native Framivra integration

Native Framivra processes the supplied four lossless stems through its unchanged audio pipeline. Its actual PCM is the source for the final S5 waveform. Do not substitute the standalone music-export envelope after native mastering changes the signal.

    python3 scripts/waveform_from_pcm.py --pcm <native-audio.wav> --out <project/audio/waveform-S5.json> --stage framivra-audio-stage --source-label out/audio.wav --recipe <native-recipe.json>

This validates one PCM stream at native 48 kHz stereo and exactly 5,760,000 sample frames. Keep the waveform in T13 cache dependencies. Native audio-stage and final MP4 AAC loudness/true-peak checks remain required. Numeric validation and byte reproducibility do not replace actual listening or user aesthetic approval.

## Completed portability validation

A fresh render through the explicit external source-root route reproduced all 177 original one-shots, the processed one-shot bank, common master curve, four stems, mix WAV and FLAC byte-for-byte (185 files total). The separate author verifier also passed. Raw execution/resource reports are retained separately, outside this source package. The portable source and asset manifests carry the content identities needed for reconstruction. The frozen R4 inputs were not modified.

To run the same local output checks after rendering, use another new report path:

    PYTHONDONTWRITEBYTECODE=1 BGM_NO_PARALLEL=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 python3 scripts/render_guard.py --report verify-guard.json --timeout 300 --cap-gb 3 --floor-gb 6 -- python3 scripts/verify_delivery.py --build rendered

The author verifier measures format, exact stem sum, cue alignment, waveform bins and a separate AAC diagnostic. Its PASS is technical evidence, not a listening assessment. The independent music review also disclosed a small DC offset and the deliberate S5 quiet interval as a continuous-background-music silence-screen exception.

## Reconstruct the local runtime pack

This optional path uses the existing Linux-supported music render/verification commands above. Keep the new render and pack outside Git. After rendering and verifying the canonical score into `$RENDERED`, create a new empty `$PACK` directory and encode the four role WAV files using the command recorded in ../audio/stems.json:

```bash
mkdir -p "$PACK/audio" "$PACK/media"
for role in T17 T18 T19 T20; do
  ffmpeg -nostdin -v error -xerror -n -i "$RENDERED/audio/$role.wav" \
    -map 0:a:0 -c:a flac -compression_level 8 "$PACK/audio/$role.flac"
done
(
  cd "$PACK/media"
  ffmpeg -nostdin -hide_banner -loglevel error -n \
    -f lavfi -i 'color=c=0xF1EDE1:s=1920x1080:r=30,format=rgb24' \
    -f lavfi -i 'anullsrc=r=48000:cl=stereo' \
    -frames:v 300 -t 10 -c:v libx264rgb -crf 0 -preset medium \
    -pix_fmt rgb24 -c:a aac -b:a 96k -movflags +faststart paper-background.mp4
)
python3 ../scripts/prepare_assets.py --from "$PACK"
```

Set `$RENDERED` and `$PACK` to explicit absolute paths first; `$PACK` must not exist before the mkdir command. The paper command matches ../media/paper-recipe.json with no-clobber `-n`. The asset importer requires the exact reference file hashes and decoded PCM. Different FFmpeg builds may produce different encoded bytes; preserve such outputs separately and investigate rather than changing expected hashes. Use the separately supplied canonical pack when exact reconstruction is unavailable. These commands do not download or install Scoreweft or any dependency. Import itself is supported on macOS/Linux; the supplied full music-synthesis guard requires Linux.
