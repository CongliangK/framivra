# Framivra DSH sidebar — first read-only slice

This optional app belongs to Framivra's `apps/` layer. The Python wheel does **not** include this plugin. Timeline semantics and selection remain exclusively in the unified `vtx` read kernel: this app does not contain another query/editor/render implementation. No cross-directory catalog/manifest registration is included in this review slice.

## Current status

- Host and browser source: implemented. Host uses public DSH Session/workingDirectory/fs/subprocess/Connection Fetch contracts. Browser registers an independent `framivra` right-sidebar tab.
- Local regressions cover a fixed protocol mock, a **real fixture subprocess**, in-memory filesystem, wire-to-client precision and bounded range cleanup. Opt-in [real-runtime tests](tests/real-runtime.test.mjs) also passed against an installed `vtx` wheel and the public built DSH `LocalSubprocessRuntime`: inspect/query through Host Response and Client presentation/serialization, plus forked helpers during completion, cancellation, timeout and unload. These are not proof of DSH authentication or ZIP installation.
- Native lossless JSON was verified on Node **v22.23.3** and Chromium **149.0.7827.55**. An isolated actual-source `PreviewBody` / React **18.3.1** harness verified a visible `LOSSLESS_JSON_UNSUPPORTED` alert when rawJSON is unavailable. Firefox/other browser versions: **UNRUN**, not inferred from Node support.
- Public, tree-external DSH Client build SDK: integration pending. We do not duplicate `window.__ModuleLoader__` or import DSH checkout-private build scripts. A production ZIP is not yet claimed installable.
- Owner/anonymous HTTP carrier integration, complete live DSH GUI/React lifecycle, real SDK build and clean-profile ZIP installation/uninstallation: **UNRUN**.

## User flow / limits

Open the Framivra guide card. Enter a **relative timeline file path inside the current Session workspace**, e.g. `timeline.json` or `project/timeline.json`; this never changes the Session working directory. Open/refresh reads capabilities, the complete vtx-emitted document, then all tracks/blocks. Optional integer-frame queries are forwarded to `vtx`; no local interval/track selection algorithm exists. Frame input must be blank (all blocks) or canonical ASCII decimal digits in `0..9007199254740991`, matching the Host token contract: no signs (including `-0`), leading zeros, decimals (`1.0`), exponents (`1e0`) or whitespace. Client checks the exact text and safe bound **before** numeric conversion; lexically invalid or unsafe input shows `BAD_QUERY` without any request and keeps the preview/input for correction. Timeline bounds remain `vtx`'s responsibility (`0 <= frame < duration`, no clipping); a canonical safe integer outside them is forwarded unchanged and surfaces upstream `query.bounds` diagnostics. Each track/block shows all vtx-emitted fields; the complete emitted document and query envelope remain available. Content-version mismatch prevents mixed-version selection display. Incomplete external-subtitle coverage and diagnostics are prominent.

Only existing `out/final.mp4` and `out/draft_final.mp4` are offered, with **provenance UNKNOWN**. Existence is not evidence of correspondence to this timeline. Playback and query frames are intentionally not synchronized. No rendering, waveform/thumbnail generation, attachment, remote filesystem, external project browsing, timeline writes or editing are implemented. Empty/missing outputs never trigger render. Missing/unsupported installed CLI produces an explicit error; no silent fallback parses the timeline.

## Safety boundary

- Connection Fetch registers exact `/api/framivra/control` (POST) and `/api/framivra/media` (GET/HEAD) routes. The DSH carrier's owner-Peer admission is the authentication boundary; there is no raw WebServer route or standalone service.
- Host chooses Session cwd. Browser gets a 30-minute opaque grant (maximum 32 active) bound to the live Session object and cwd; the media route accepts only Session/grant/opaque-mediaID, never a path. Grants are process-local and disappear on unload.
- `ctx.fs.resolve/contains/stat` enforce canonical containment and regular files. Symlink escape, URL, NUL, traversal, absolute input and cross-session grants are rejected. Local shared-path mapping only; no `node:fs` permission bypass. Every media pull revalidates the grant and output identity/version. A filesystem cannot offer atomic “resolve + run child/open file” through these contracts: concurrent hostile filesystem replacement remains a host-level TOCTOU limitation, not a sandbox guarantee.
- Configure `vtxExecutable` as an administrator-trusted absolute path when outside system PATH. Default `vtx` resolution uses only `/usr/local/bin:/usr/bin:/bin`; custom `trustedPath` entries must all be absolute. Executables resolving inside the Session workspace are rejected. Child argv never uses a shell, cwd is the executable directory (not project cwd), stdin is ignored, safe Python environment clears `PYTHONPATH/PYTHONHOME` and sets `PYTHONDONTWRITEBYTECODE=1` / `PYTHONSAFEPATH=1`. The selected executable/parent directory must itself be trusted; a Python launcher is not accepted instead of installed `vtx`.
- Reads have a 30-second deadline, abort with request cancellation and plugin unload, and cap stdout to 8 MiB. `collected.stdout.readFrom(0).lossy` rejects truncated JSON. Every read, including successful commands, terminates its managed range and awaits **both** direct-result settlement and public `waitForExit()` quiescence before releasing ownership. Cleanup has an independent 5-second bound; an unobservable/stuck range produces `PROCESS_CLEANUP_FAILED`, retains ownership for unload retry, and unload reports failure rather than silently dropping the range. Real 30-second Host deadline testing returned only after cleanup (about 31 seconds). Provider containment limits still apply, including weaker fallback providers' inability to guarantee escaped descendants.
- Complete JSON uses the same native `JSON.parse` source-context codec on Host and Client, preserving numeric tokens with native `JSON.rawJSON` through HTTP, details and serialization. No handwritten JSON parser, network dependency, decimal/BigInt string substitution or second timeline parser is used. Native support is actually probed on each decode; missing support explicitly refuses instead of displaying rounded fields. Frame/index selectors and returned query indices/ranges must remain canonical nonnegative safe integers; vtx-emitted document/extension numeric tokens remain uninterpreted and wire-lossless. This is **not original-source numeric or lexical fidelity**: the current Python upstream already converts decimal/exponent source tokens to float before emitting read/v1 (e.g. source `1.0000000000000001` may emit `1.0`). The sidebar cannot recover that precision. Any “all fields” claim means the vtx-emitted read document only; `content_version` hashes source bytes but does not make the emitted document source-exact. A future `raw_document_utf8` protocol contract is separate upstream work, not implemented or required by this slice. Exit 1/2, schema mismatch and diagnostics are surfaced separately.
- Media is pull-streamed in at most 64 KiB windows using `ctx.fs.readByteRange`. Supports GET, HEAD, one bounded/open/suffix byte Range, 206/416, Content-Length and cancellation. Multiple/malformed ranges fail closed. It does not reuse `/api/file`, buffer whole MP4s, or accept arbitrary filesystem paths.

## Development

Requires Node >=22.15 (the source-view tests use public `node:module.registerHooks`); local tests have no installed dependencies:

```sh
node --test apps/dsh-sidebar/tests/*.test.mjs
```

The mock CLI returns a fixed envelope; it deliberately does not implement frame/range selection. Security tests exercise URL/NUL/traversal/symlink rejection, Session/expiry/cwd grants, remote mapping, executable hijack, full multi-family original fields, lossy JSON, unsupported CLI, request/unload cancellation, media ID injection, HEAD/Range and pull bounds/revocation. A route-registration test verifies use of authenticated Connection Fetch, **not** real owner authentication.

## Packaging / upstream requirements

The release ZIP must have `package.json`, `cordis.patch.yml`, ESM `lib/index.js`, `lib/client.js`, and `LICENSE` at its root, with no lifecycle scripts, dependencies, node_modules, checkout paths or second React/ReactDOM/Cordis copy. Browser third-party dependencies must be bundled except platform-shared peers. Runtime must work without a DSH source checkout.

Needed from the public SDK: a versioned tree-external import/CLI, explicit browser entry/output/package ID, shared-platform external handling, and the supported DSH module-registration wrapper emitted by the SDK. No permanent hand-written loader wrapper is acceptable. Client compilation remains pending; once the SDK emits its browser artifact, the dependency-free packaging stage (Node + build-time Python stdlib) is available:

```sh
node apps/dsh-sidebar/scripts/package.mjs \
  --client /absolute/SDK-output/client.js \
  --out /tmp/framivra-orchestrate-evidence/dsh-sidebar/framivra-dsh-sidebar.zip
```

The output must not already exist. The packager allowlists release metadata, copies ESM Host modules, rejects source maps/obvious checkout paths in the Client artifact, and omits source scripts/dependencies/node_modules. It does **not** certify that an arbitrary input is a valid SDK artifact or that installation works. The packaging test deliberately uses a marked, non-installable dummy Client; do not distribute that test ZIP.

Needed from the read CLI: `vtx -t PATH capabilities|inspect|query`, the `framivra.timeline.read/v1` envelope and exit 0/1/2 as agreed. Optional query selectors are passed as separate argv tokens (`--family`, `--track-index`, `--block-index`, `--frame` or `--interval`). No subprocess is kept alive between reads.

ZIP installation is being implemented separately in DSH; this app must not claim a one-click install until a clean-profile installation, owner-authenticated preview, uninstall and anonymous-denial smoke actually pass. Live 3080 was neither restarted nor replaced by this slice.
