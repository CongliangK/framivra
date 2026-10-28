# Framivra DSH sidebar — first read-only slice

This optional app belongs to Framivra's `apps/` layer. The Python wheel does **not** include this plugin. Timeline semantics and selection remain exclusively in the unified `vtx` read kernel: this app does not contain another query/editor/render implementation. No cross-directory catalog/manifest registration is included in this review slice.

## Current status

- Host and browser source: implemented. Host uses public DSH Session/workingDirectory/fs/subprocess/Connection Fetch contracts. Browser registers an independent `framivra` right-sidebar tab.
- Local tests: fixed protocol mock, a **real fixture subprocess** and an in-memory filesystem. These are not proof of installed-vtx behavior, real DSH authentication, browser rendering or ZIP installation.
- Public, tree-external DSH Client build SDK: integration pending. We do not duplicate `window.__ModuleLoader__` or import DSH checkout-private build scripts. A production ZIP is not yet claimed installable.
- Real `vtx` read CLI, owner/anonymous HTTP carrier integration, and live GUI/ZIP smoke: **UNRUN** until their respective upstream changes are available.

## User flow / limits

Open the Framivra guide card. Enter a **relative timeline file path inside the current Session workspace**, e.g. `timeline.json` or `project/timeline.json`; this never changes the Session working directory. Open/refresh reads capabilities, the complete stored document, then all tracks/blocks. Optional integer-frame queries are forwarded to `vtx`; no local interval/track selection algorithm exists. Each track/block shows its original fields; the entire raw document and query envelope remain available. Content-version mismatch prevents mixed-version selection display. Incomplete external-subtitle coverage and diagnostics are prominent.

Only existing `out/final.mp4` and `out/draft_final.mp4` are offered, with **provenance UNKNOWN**. Existence is not evidence of correspondence to this timeline. Playback and query frames are intentionally not synchronized. No rendering, waveform/thumbnail generation, attachment, remote filesystem, external project browsing, timeline writes or editing are implemented. Empty/missing outputs never trigger render. Missing/unsupported installed CLI produces an explicit error; no silent fallback parses the timeline.

## Safety boundary

- Connection Fetch registers exact `/api/framivra/control` (POST) and `/api/framivra/media` (GET/HEAD) routes. The DSH carrier's owner-Peer admission is the authentication boundary; there is no raw WebServer route or standalone service.
- Host chooses Session cwd. Browser gets a 30-minute opaque grant (maximum 32 active) bound to the live Session object and cwd; the media route accepts only Session/grant/opaque-mediaID, never a path. Grants are process-local and disappear on unload.
- `ctx.fs.resolve/contains/stat` enforce canonical containment and regular files. Symlink escape, URL, NUL, traversal, absolute input and cross-session grants are rejected. Local shared-path mapping only; no `node:fs` permission bypass. Every media pull revalidates the grant and output identity/version. A filesystem cannot offer atomic “resolve + run child/open file” through these contracts: concurrent hostile filesystem replacement remains a host-level TOCTOU limitation, not a sandbox guarantee.
- Configure `vtxExecutable` as an administrator-trusted absolute path when outside system PATH. Default `vtx` resolution uses only `/usr/local/bin:/usr/bin:/bin`; custom `trustedPath` entries must all be absolute. Executables resolving inside the Session workspace are rejected. Child argv never uses a shell, cwd is the executable directory (not project cwd), stdin is ignored, safe Python environment clears `PYTHONPATH/PYTHONHOME` and sets `PYTHONDONTWRITEBYTECODE=1` / `PYTHONSAFEPATH=1`. The selected executable/parent directory must itself be trusted; a Python launcher is not accepted instead of installed `vtx`.
- Reads have a 30-second deadline, abort with request cancellation and plugin unload, and cap stdout to 8 MiB. `collected.stdout.readFrom(0).lossy` rejects truncated JSON. Exit 1/2, schema mismatch and diagnostics are surfaced separately.
- Media is pull-streamed in at most 64 KiB windows using `ctx.fs.readByteRange`. Supports GET, HEAD, one bounded/open/suffix byte Range, 206/416, Content-Length and cancellation. Multiple/malformed ranges fail closed. It does not reuse `/api/file`, buffer whole MP4s, or accept arbitrary filesystem paths.

## Development

Requires Node >=22.15 (the source-view tests use public `node:module.registerHooks`); local tests have no installed dependencies:

```sh
node --test apps/dsh-sidebar/tests/*.test.mjs
```

The mock CLI returns a fixed envelope; it deliberately does not implement frame/range selection. Security tests exercise URL/NUL/traversal/symlink rejection, Session/expiry/cwd grants, remote mapping, executable hijack, full multi-family original fields, lossy JSON, unsupported CLI, request/unload cancellation, media ID injection, HEAD/Range and pull bounds/revocation. A route-registration test verifies use of authenticated Connection Fetch, **not** real owner authentication.

## Packaging / upstream requirements

The release ZIP must have `package.json`, `cordis.patch.yml`, ESM `lib/index.js`, `lib/client.js`, and `LICENSE` at its root, with no lifecycle scripts, dependencies, node_modules, checkout paths or second React/ReactDOM/Cordis copy. Browser third-party dependencies must be bundled except platform-shared peers. Runtime must work without a DSH source checkout.

Needed from the public SDK: a versioned tree-external import/CLI, explicit browser entry/output/package ID, shared-platform external handling, and the supported DSH module-registration wrapper emitted by the SDK. No permanent hand-written loader wrapper is acceptable. Build integration and exact commands will be recorded once that API is delivered.

Needed from the read CLI: `vtx -t PATH capabilities|inspect|query`, the `framivra.timeline.read/v1` envelope and exit 0/1/2 as agreed. Optional query selectors are passed as separate argv tokens (`--family`, `--track-index`, `--block-index`, `--frame` or `--interval`). No subprocess is kept alive between reads.

ZIP installation is being implemented separately in DSH; this app must not claim a one-click install until a clean-profile installation, owner-authenticated preview, uninstall and anonymous-denial smoke actually pass. Live 3080 was neither restarted nor replaced by this slice.
