# Headless-shell BeginFrame research

Issue #67 studies an isolated persistent-page renderer. The production backend and NAVFREE default are unchanged. The experimental implementation and entry points are in [experiments/gfx_beginframe](../experiments/gfx_beginframe/README.md); its distribution identity is [locked separately](../experiments/gfx_beginframe/binary-lock.json).

## Research premise

[DEV's historical NAVFREE record](DEV.md#navfree-免导航截图判负双杀方案-v2-阶段-2) records real concurrent pixel drift and slower rendering. That record does not identify a screenshot-versus-animation-clock race. It therefore establishes a reason to test, without establishing that BeginFrame is necessary or sufficient. The ordinary navigation reference remains the control arm.

The existing local headless-shell successfully created an `enableBeginFrameControl` target. Its first BeginFrame call explicitly required `--run-all-compositor-stages-before-draw`; adding that flag yielded a real screenshot of a production heavy page. The binary's live protocol was captured and hashed rather than treating a remote protocol schema as proof of support. No new browser was downloaded. The original download receipt and URL are unknown and are recorded as null.

## Frame and resource protocol

Persistent rendering requires a caller-declared pure `__vtx_render(f)`. Each request awaits that function, a microtask checkpoint, `document.fonts.ready`, all images' successful load/decode, and a layout read. These are separate readiness observations; none is called a compositor barrier. A monotonic synthetic tick sequence advances by the declared fixed interval independently of requested frame order. The screenshot comes from the corresponding BeginFrame response.

Every request uses a dedicated target socket and CDP request ID, with a page acknowledgement containing run, target, generation, request and frame. The acknowledgement is checked before and after capture. Diagnostic output also verifies the shared two-corner framebuffer Canary. No-damage output requires a new screenshot response; absent screenshots fail rather than copying an earlier frame. Exceptions fail the run. A new target/run is required for recovery, with an independent identity and initialized page state.

The reference uses the same shell binary, viewport, color profile and fonts, with normal compositor scheduling and ordinary navigation screenshots. Its two control flags and target control bit differ by necessity and are declared experimental axes. System Chrome from another version is not used to attribute differences to BeginFrame.

## Evidence and limits

Evidence root: `/tmp/framivra-orchestration-20261018/issue67/`. These files are local research artifacts, not portable published assets.

- `probe.json` and `protocol.json`: actual binary, protocol and controlled target support.
- `real-beginframe/` and `real-navigation/`: production c5a page requests 0, 22, 1, 22; decoded full-frame RGBA hashes are equal across arms and repeated frame 22.
- `functional-v2/functional.json`: fixed-seed resource-heavy fixture, 21 requests, full-frame exact comparison, repeated/seek history independence, dual-corner diagnostics and error/new-run recovery. Two idle BeginFrame calls returned `hasDamage=false`; the following independent screenshot retained the correct pixels.
- `strict-software/strict-report.json`: existing #62 strict verification passed for a complete 32-frame isolated project. The actual renderer in both arms is SwiftShader. An earlier attempt (`strict-first/`) correctly failed environment comparison because unrelated host GPU memory use changed; the software rerun uses the verifier's existing software-backend nonapplicability contract and retains raw GPU inventory separately.

These are functional evidence, not performance acceptance. Internal timing fields remain null because #63 has no merged timing interface on the frozen baseline; PR #169 is not imported or modified. External whole-wave timing can be measured independently, but cannot supply missing navigation/layout/raster/compositor/PNG internal attribution.

The long fixed-seed sequence, complete eight-instance load, multi-round cold whole-wave measurements, and independent review remain required before deciding whether the research meets its closure conditions. A successful small probe does not authorize production adoption. No technical Go/NoGo is inferred from environment failure or from these initial functional samples.
