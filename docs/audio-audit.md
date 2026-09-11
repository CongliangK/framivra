# Faithful audio audit bundles

`blender_bridge/audit_bundle.py` exports the selected tracks for listening before ducking and master loudness processing. Production media is never modified. MP3 is a lossy preview; a validated audit timeline is not proof that its mix equals the delivered film.

```sh
python blender_bridge/audit_bundle.py -t /project/timeline.json --prefix bed --prefix sfx --skip-blend
# Preserve every event without silence detection:
python blender_bridge/audit_bundle.py -t /project/timeline.json --prefix bed --whole --skip-blend
```

Python, FFmpeg and ffprobe must be on PATH. These commands work with quoted Windows paths; omit `--skip-blend` only when Blender is installed. Cross-drive media directories use absolute paths and therefore require rebasing if moved to another computer.

## Event fidelity

Every referenced source is exported and retains its source ID. A track can use several files; repeated references share an export. `--whole` copies every event including its volume, pan, source offset, loop and fade fields. Track-level ducking is deliberately excluded and listed in `_meta.excluded_duck_tracks`.

Default silence detection runs on the original first audio stream, not MP3 padding. Source content `[a,b)` is intersected with an event's `[src_in, src_in + out - in)`; intersection `[u,v)` becomes `src_in=u, in=event.in+u-src_in, out=event.in+v-src_in`. This preserves late placements and reused source excerpts. Seconds are rounded outward to frames, then overlapping/adjacent frame spans are merged. Detection failure exits nonzero.

Loop and fade events remain whole: naively splitting them would restart their cycle or envelope. Events with no detected content also remain whole, retaining quiet/silent intentional material and a valid nonempty track. Each such fallback is printed and recorded in `_meta.whole_fallbacks`. Detection thresholds are approximate; use `--whole` for conservative review.

An audio-only audit does not inherit `project.cache_dependencies` from a relocated visual project. Removed declarations are listed in its metadata. The exported media itself is tracked by vtx as ordinary sources.

## Cache and failure handling

MP3 names are based on source bytes, conversion settings and FFmpeg version, with output SHA256 checked before reuse. Same-time/same-size replacement, a different source and a damaged MP3 cannot silently reuse old audio. The first audio stream is exported at 48 kHz stereo, 320k libmp3lame. Equal source bytes share a single transcode job. Old track-name/mtime files are not trusted or deleted automatically.

Encoding and strict candidate decoding finish before atomic MP3 replacement. Failures before replacement preserve any valid cached MP3 and its receipt, and clean the candidate. A missing/corrupt receipt causes regeneration. The directory as a whole is not transactional: use the command exit status and successful validate output, not the presence of a prior timeline or `.blend`, to determine success. Do not edit sources or run multiple writers against the same audit directory during a build. This change does not introduce automatic cache garbage collection.

## Regression coverage and remaining limits

```sh
python -m unittest discover -s tests -p test_audio_audit.py -v
```

Tests use synthetic audio and real FFmpeg/vtx validation for relocated dependencies, multiple sources and volumes, delayed nonzero source offsets, actual trimming, whole/fallback behavior, all-silent audio, content replacement with preserved timestamps, corrupt-cache repair, forced-encoder failure preservation and production timeline overwrite rejection. Additional boundary tests cover detector failure, subframe merging and cross-drive fallback. Set `VTX_AUDIT_TEST_OUTPUT` to keep fixtures/logs; otherwise temporary fixtures are cleaned. FFmpeg-dependent tests explicitly skip when unavailable; a skipped run is not acceptance.

Blender playback is a separate boundary. In particular, `json2blend.py` loop expansion with nonzero source offsets and `verify_blend.py`'s limited audio-field checks still require focused Blender tests. Retaining fields in JSON does not certify Blender playback equivalence. Linux/macOS, multiwriter behavior and long-form projects have not been established by the Windows regression.
