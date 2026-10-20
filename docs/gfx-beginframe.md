# Headless-shell BeginFrame research

**Issue #67 meets its negative-result research stopping condition for the tested prototype. The candidate fails the full-frame exactness gate and must not be adopted for production. Formal performance is UNRUN.** The host is undergoing substantial concurrent development, so saved wall/RSS observations cannot establish speed, efficiency or a performance NoGo. The production backend and NAVFREE default are unchanged.

The rejected prototype, its CPU checks and replay entry points are archived at [revision f1efe5f6](https://github.com/CongliangK/framivra/tree/f1efe5f688b543ca11902c9538dec02a5c265961/experiments/gfx_beginframe); its binary/distribution/protocol identity has one home in [the archived lock](https://github.com/CongliangK/framivra/blob/f1efe5f688b543ca11902c9538dec02a5c265961/experiments/gfx_beginframe/binary-lock.json). No new browser was downloaded. Original download URL and receipt are unknown and recorded as null; local Playwright distribution metadata and the actual executable hash are retained.

## Research premise and support

[DEV's historical NAVFREE record](DEV.md#navfree-免导航截图判负双杀方案-v2-阶段-2) records concurrent pixel drift and slower rendering. It does not identify a screenshot-versus-animation-clock race. The history establishes a reason to investigate, without establishing that BeginFrame is necessary or sufficient.

The locked local shell actually created an `enableBeginFrameControl` target. Its first BeginFrame call explicitly required `--run-all-compositor-stages-before-draw`; supplying that flag produced screenshots of real content. The live protocol was captured and checked against the lock. A remote schema was not treated as evidence of implementation support.

The same shell provides the ordinary navigation reference. Viewport, color profile and fonts match; normal navigation removes the two required control flags and sets the target control bit false. These necessary experimental axes are declared in the strict comparison plan. A different system Chrome version is not used to attribute pixel differences to BeginFrame.

## Frame and resource protocol

Persistent rendering requires a caller-declared pure `__vtx_render(f)`. Each request awaits that function, a microtask checkpoint, `document.fonts.ready`, successful image load/decode, and a layout read. Those observations do not constitute a compositor barrier. Monotonic synthetic ticks advance at the fixed research interval, independently of requested frame order. The screenshot comes from the corresponding BeginFrame response.

Each request uses a target-specific socket and CDP request ID. A page acknowledgement binds run, target, generation, request and frame and is checked before and after capture. Diagnostic runs additionally decode both framebuffer Canary corners. Correct corners do not prove that every interior layer has the right raster state; full-frame Golden comparison remains mandatory.

No-damage output requires its own screenshot response. Missing captures and page exceptions fail the run, without a completed receipt. Recovery starts a new target/run with initialized state and independent identity. Tabs are not transferred between runs or pages. The runner never fabricates a frame by copying the previous screenshot.

`strict.py` consumes the existing #62 input fingerprint, manifest and strict verifier contracts. It freezes actual configuration and frame orders before either arm renders. The final runner preflights routes serially, closes the probe browsers, then starts/renders/exits each actual arm independently. The saved pressure experiment used an earlier implementation with an idle second browser resident; it is functional evidence only. Its source is retained by commit `44df0ed`.

## Functional evidence

Evidence root: `/tmp/framivra-orchestration-20261018/issue67/`. These are local artifacts, separate from the Git source archive. For historical reproduction, use a separate checkout of the complete revision linked above and follow its [archived README](https://github.com/CongliangK/framivra/blob/f1efe5f688b543ca11902c9538dec02a5c265961/experiments/gfx_beginframe/README.md), retaining the matching product libraries, locked local browser and isolated output requirements. `evidence-snapshot.json` hashes the principal records.

Initial small probes passed: real c5a requests 0, 22, 1, 22 were full-frame exact; the self-contained resource fixture included an embedded font, PNG image, SVG blur, gradient and alpha. Its 21 fixed-seed requests, dual-corner diagnostics and error/new-run recovery passed. Idle BeginFrame returned `hasDamage=false` twice; a subsequent independent screenshot still matched. Eight CPU protocol checks cover exceptions, missing capture, stale identity and no-damage behavior. Independent review found that the functional runner recorded idle screenshot disagreement without rejecting the run; this is corrected. Two additional subprocess regression cases execute the actual entry point with matching or corrupted idle PNGs, checking persisted reports and exit codes. The negative case also fails against the pre-fix implementation, confirming that it detects the reported defect without a browser. These small passes do not override the pressure failure.

The pressure plan covers every frame 0–127 of eight independent 1920×1080 instances, with seed-bound shuffled order. Two instances each use the synthetic fixture, c5a, c5b and llm_post. The real source files were copied without modification into an isolated project. g6b was excluded after an explicit rejection because it has no `__vtx_render`; it must retain navigation.

The existing #62 verifier compared all 1024 requested output pairs and rejected 245. It reported no missing frames, unknown provenance, environment mismatch or coverage error. Per-instance differences:

| Page | First copy | Second copy |
| --- | ---: | ---: |
| Resource-heavy synthetic fixture | 0 | 0 |
| c5a_main_path | 46 | 29 |
| c5b_hotkey | 6 | 4 |
| llm_post | 64 | 96 |

The full receipts, frame orders and PNGs are in `matrix-round1/`; `difference-samples.json` records coordinates and RGBA values. Example c5a frame 22 differs at 726 pixels, maximum channel difference 9, bounding box (58,43)–(501,349). This is a failure of the exactness gate regardless of perceived visual closeness.

A reproducible single-instance counterexample is retained in `history-c5a/`: replay the original prefix through frame 22, then request frame 22 twice more. Two independent runs produced the same differing final image on all three repeated requests. Both Canary corners decoded the requested frame. The final serialized DOM bytes matched the fresh navigation reference exactly. A shorter 127→22→22 probe did not reproduce. These observations rule out an explanation based solely on differing serialized DOM attributes, but do not identify the browser's internal cause or prove an interior layer is current. They do not establish a screenshot/animation-clock race.

The final sequential strict runner passed a separate four-frame self-contained smoke (`strict-serial-tiny/`). This checks lifecycle/receipt integration, not remediation of the real-page failure.

## Cross-version stopping evidence

The same c5a counterexample was also reproduced using the existing Playwright revision 1243 headless-shell 153.0.8010.12 (executable SHA-256 `ded93a9c9a53a1ae040f08124badcca95c938e9d5015ff340c3b5538c41bf39e`). No download, installation or production-tree change was needed. Each of its two independent history runs was compared with a fresh navigation reference from that same version. All three final frame-22 requests in both runs differed at 726 pixels, maximum channel difference 9, bounding box (58,43)–(501,349), matching the preserved 149 result. The request prefix and source bytes were unchanged; Canary signatures and serialized DOM agreed, and the 153 font fingerprints were unchanged before/after. Saved runtime observations identify SwiftShader in all three 153 arms. The 149 minimal replay does not independently embed its renderer inventory; the existing lock and same-round matrix retain that evidence.

The supplement is retained under the local evidence root's `cross-version/`: `REPORT.md`, `result153/reproduction.json`, original PNG/DOM files, `saved-png-verification.json`, and a file-hash manifest for the retained records and tools. The manifest SHA-256 is `40b16853d7ca09c2bb921fe03b6cb15072c57b77aa520e391dcc9a4686e60f1b`; the report SHA-256 is `85672093be1064e84e21383ec00a32c67d413d14fdc7a4576c7fef4d97c480ce`. Temporary replay tools use the reviewed render logic with only its product-library ROOT and adjacent version lock changed. Original download receipts remain unknown. These are local retained artifacts, not public download links.

Fresh independent adjudication checked the original stopping clause, source differences, recorded identities, requests and saved verification results, and streamed every supplement manifest hash. It found the issue's explicit “pixels remain different across versions” negative-result branch satisfied. That adjudication did not rerun browser workloads or image decoding. Research can therefore end with this prototype rejected; positive performance and production-eligibility requirements are not declared passed. The retained 118-request prefix is a reproducible isolated counterexample, not a proof of the globally shortest history. A shared prototype or browser cause remains possible; no animation-clock race or universal BeginFrame limitation is proved.

## Remaining gates and research disposition

- **Exactness: FAIL for this candidate.** Stable real-page history-dependent pixel differences require investigation; no page or reference was altered to obtain a pass.
- **Formal performance: UNRUN.** Planned three-round independent cold measurements were cancelled when the user identified uncontrolled simultaneous development. Saved wave/process wall and sampled RSS are diagnostic only. RSS sums resident pages across a process tree, double-counting shared pages; the pressure experiment also kept both browsers resident. No speed ratio or performance verdict is derived.
- **Internal attribution: unavailable.** #63 has no merged timing interface on the frozen baseline; PR #169 was not imported or modified. Navigation/layout/raster/compositor/PNG timing fields remain null. External process timing cannot fill those missing fields.
- **Production eligibility: rejected for the current implementation.** The research closes through its explicit cross-version pixel-failure stopping branch after review and PR integration. The current prototype is rejected; unresolved correctness causes, controlled performance and attribution remain unestablished rather than passed. Binary support and small functional successes do not establish production eligibility.

The actual renderer was SwiftShader in both arms. An initial strict attempt correctly rejected changing unrelated host GPU memory occupancy. Software reruns use #62's existing software-backend nonapplicability contract and retain the raw GPU inventory separately; the verifier itself is unchanged.
