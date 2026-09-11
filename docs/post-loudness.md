# Post-mix loudness check

Run after `vtx audio`, before `vtx mux`:

```sh
python scripts/post_loudness.py /path/to/project --ffmpeg /path/to/ffmpeg
```

Windows supports the same Python entrypoint and quoted paths. The Bash wrapper remains compatible. `TARGET_LUFS` (default -14) or `--target` selects the target; `FFMPEG` or `--ffmpeg` selects FFmpeg.

The old wrapper treated `stages.audio.raw_i` as current WAV loudness. It actually describes the raw mix before `gain_db` was applied, so the wrapper applied the gain twice. The replacement always measures current audio bytes with EBU R128, applies the needed static gain and the existing 0.599 limiter ceiling, then measures again. Latency compensation retains sample alignment. Candidate duration must match within one 48 kHz sample and measured loudness within 1.5 LU before replacement. A failed render or verification preserves the original audio and receipt; temporary audio is removed.

Only after validation does the script atomically invalidate `audio` and its `DOWNSTREAM` consumers in the existing receipt, replace audio with a same-directory candidate, then restore the previous audio stage with a `post_loudness` object containing measurements and output SHA256. It preserves input provenance and never invents a missing successful audio stage. An interrupted finalization can leave audio unacknowledged, requiring an audio rerun, but cannot leave obsolete mux/QC success. Existing raw mix metadata remains raw mix metadata. It does not update cached audio: rerunning `vtx audio` may restore the pre-postprocessed mix, so rerun this command afterward.

Repeated execution is loudness-stable within measurement precision, not guaranteed bit-identical. Do not run simultaneous writers or edit timeline inputs during postprocessing. PCM WAV input is required. The limiter headroom is a heuristic; decoded AAC true peak still needs final QC. This is not certification to a Bilibili delivery standard.

Regression tests:

```sh
python tests/test_post_loudness.py --ffmpeg /path/to/ffmpeg --output /path/to/evidence
```

Tests cover a real stale-raw-I reproduction, two processing passes, unchanged duration, receipt invalidation, and preservation on encoder, duration, and loudness gate failures.
