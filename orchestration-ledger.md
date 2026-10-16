# Framivra orchestration ledger

Frozen local draft, not published. Snapshot freeze: 2026-10-16T08:03:27.820436Z; GitHub observations: 2026-10-16T08:00:17.049Z. This is a historical handoff snapshot, not a zero-lag dashboard. Recheck moving facts before using it for publication, integration or closure.
Repository: [CongliangK/framivra](https://github.com/CongliangK/framivra). Published predecessor: [3f7359bd6f06faa4caef5abc289f3769751c25fd](https://github.com/CongliangK/framivra/blob/3f7359bd6f06faa4caef5abc289f3769751c25fd/orchestration-ledger.md).

## Goal and counting

Original 50-ordinary-issue baseline; target 20 genuine resolutions. Verified completion remains **3/20: #44, #49, #53**. Fresh search returned 49 ordinary open issues and one open draft PR (#156). Arithmetic: 50 − 3 + 2 newly added issues (#153, #155) = 49; including PR #156 = 50 aggregate. New issues do not count toward the original goal. Partial work, branch publication, review PASS and draft CI do not equal issue completion.
Main: d9ce4aab52fec57b770f689550fbbde1a860243b; tree 4efc0290ac73bec2a59299386bcb9197760f5374. No continuous-execution or rapid-completion guarantee.

## Scope and integration

Independent PRs may share files, with serial integration and fresh exact-tree verification. Preserve user-local changes and original issue contracts. Local backend implementation is excluded. #144 is resumed only for the authorized local template/path scope, including bounded CEF/Electron references. Coagentdraw remains paused apart from its already completed docs-only archive.

## #90: Scoped quality and evidence labels

- Issue/DoD: [90](https://github.com/CongliangK/framivra/issues/90); original issue contract remains authoritative.
- State: draft_pr_ci_pass_human_contract_pending. Roles: Workstream implementer; independent reviewer; integration maintainer.
- Environment: Temporary cloud Linux workspace. Worktree: development/worktrees/framivra-issue90.
- Identity: branch dot/quality-contract-90-review; base d9ce4aab52fec57b770f689550fbbde1a860243b; local candidate 39375e9cf2e9202171e429e95589e2bfec2af3f1; tree 982ceba0a129958f047c4129f5d7b0fd7a982d5e; parent 078f28b1713feb1cbccd0b29c70bab1e0ad296c6.
- Publication: head b82730bcfb2c2d73814c988c4cf62229565b2d52; published tree 982ceba0a129958f047c4129f5d7b0fd7a982d5e; PR https://github.com/CongliangK/framivra/pull/156; CI https://github.com/CongliangK/framivra/actions/runs/38869158822 (completed_success).
- Independent review: PASS bounded technical review; does not supply human contract approval.
- Repair rounds used: 1; no additional exception inferred.
- Pending decision: Human document/contract maintainer approval under DOC-CHARTER §25, then separate merge decision.
- Next gate: Keep draft until human contract decision; integrate serially and reverify changed tree.
- Limits: Annotation-only; Fast runtime; visual_content unknown; no final-movie Exact upgrade, authentic-producer, browser/GPU determinism or performance certification.
- Evidence actually reported:
  - focused: 87 tests; 4096 classifier combinations; 36 malformed receipt/begin-stage combinations
  - independent: 14 integration/adversarial cases PASS
  - cpu: 60-frame CPU/FFmpeg fixture structural QC 8/8; accepted=false; four tamper negatives reject
  - hosted_ci: Exact published head run 38869158822: all 9 jobs completed success, freshly read
- Exact base-to-candidate changed paths: docs/cache-provenance.md; scripts/gfx_evidence.py; scripts/gfx_golden.py; tests/test_gfx_evidence.py; tests/test_gfx_golden.py; tests/test_quality.py; vtx/infra.py; vtx/qc.py; vtx/quality.py.
- Preserved evidence: development/evidence/issue90/repair1/freeze.json.

## #68: PNG FrameSource consumer boundary

- Issue/DoD: [68](https://github.com/CongliangK/framivra/issues/68); original issue contract remains authoritative.
- State: review_pass_branch_published_pr_creation_cancelled. Roles: Workstream implementer; independent reviewer; integration maintainer.
- Environment: Temporary cloud Linux workspace. Worktree: development/worktrees/framivra-issue68.
- Identity: branch cloud/issue68-png-framesource-20261016; base d9ce4aab52fec57b770f689550fbbde1a860243b; local candidate a15ca93e72104fb181bbbd4512ec31bd33a949f3; tree 775e8a9511aae5a83d054e376577041f960a84bc; parent ebeb041b0ff897aede916b46992d828b4cd92b33.
- Publication: head 3b0d733ee06b96dbb690dfd5b96d36b27f05d9f8; published tree 775e8a9511aae5a83d054e376577041f960a84bc; PR none; CI none (not_run_for_candidate).
- Independent review: PASS bounded repair review.
- Repair rounds used: 1; no additional exception inferred.
- Pending decision: Explicit retry of cancelled PR creation requested from user; bundled #68/#155 retry response pending.
- Next gate: After retry authorization, create draft PR for exact published branch; hosted CI and final integration gates remain.
- Limits: Prior ten-case decoded-media parity belongs to ebeb041; fresh preview parity is finite fixture proof. No native backend, GPU, performance or #46 closure claim.
- Evidence actually reported:
  - strict: 10 PASS, zero skips on repaired candidate
  - adjacent: 56 PASS; eight argv/identity parity cases; mutation/PNG/readiness probes PASS
  - docs: 7 PASS
  - aggregate: Final aggregate unrun; older aggregate aborted
- Exact base-to-candidate changed paths: scripts/ci_checks.py; tests/test_composite_determinism.py; tests/test_frame_source.py; vtx/composite.py; vtx/frame_source.py; vtx/range.py; vtx/subs.py.
- Preserved evidence: outputs/issue68-redteam-a15ca93/manifest.json; outputs/issue68-redteam-a15ca93/REPORT.md; outputs/issue68-repair1-20261016/manifest.json; outputs/issue68-publication/publication.json.

## #155: Completion-marker publication race

- Issue/DoD: [155](https://github.com/CongliangK/framivra/issues/155); original issue contract remains authoritative.
- State: review_pass_publication_cancelled_no_pr. Roles: Workstream implementer; independent reviewer; integration maintainer.
- Environment: Temporary cloud Linux workspace. Worktree: development/worktrees/framivra-segment-marker-race.
- Identity: branch fix/segment-marker-race-20261016; base d9ce4aab52fec57b770f689550fbbde1a860243b; local candidate f3bae799da5b0ae3b2d9454822706fdb2f8d08c0; tree 780715f7babc1f5e03ec25bcac168f1e11a59d95; parent 3faae651385893da76397da7532637f85aa60180.
- Publication: head none; published tree none; PR none; CI none (not_run_for_candidate).
- Independent review: PASS standalone repair1; broader delivery and integration gates remain.
- Repair rounds used: 1; no additional exception inferred.
- Pending decision: Blob-publication cancellation: explicit bundled #68/#155 retry authorization requested, response pending.
- Next gate: Authorized retry, exact-tree publication, CI, final repeated-clip and #45 integrated-tree evidence.
- Limits: New issue outside original 50-issue goal. Earlier 2700-frame workload belongs to 3faae651, not final candidate. No Chrome/native Windows/full aggregate claim.
- Evidence actually reported:
  - focused: 43 PASS; one explicit Chrome skip
  - real_cpu: Fresh baseline failure and final-candidate two-writer success; flock exclusion and no temp leftovers
  - edge: Literal target names and failed-marker recovery PASS; fd and forced legacy
  - aggregate: Not run
- Exact base-to-candidate changed paths: tests/test_failure_recovery.py; vtx/fstxn.py; vtx/recovery.py.
- Preserved evidence: outputs/segment-marker-repair1-review-20261016/REPORT.md; outputs/segment-marker-publish-20261016/manifest.json.

## #45: F12 cancellation and F13 adaptive concurrency

- State: frozen repair2, independent BLOCKED / FAIL. Both automatic repair rounds exhausted. Roles: Workstream implementer; independent reviewer; integration maintainer.
- Original DoD: https://github.com/CongliangK/framivra/issues/45; full-tree, #51 and F13 gates remain unmet.
- Identity: branch cloud/issue45-runtime-cancellation-20261016; base d9ce4aab52fec57b770f689550fbbde1a860243b; candidate 91b3783269c3164d12bdb999cf4918440be9961a; tree fc8ce6f0eeffb3476b2aafa3ab9368782c26d1e6; parent 581d059fd334eedd20ae7ced734f5f1b9c84178a.
- Environment/worktree: cloud Linux, development/worktrees/framivra-issue45. No remote publication, PR or candidate CI.
- Review: BLOCKED / FAIL on exact repair2; formal report now present. R5–R7 bounded corrections do not fix R4 resource ownership. Owned child remains alive while NVENC slot and #44 capacity are released.
- Pending decision: Ownership/lease architecture decision and explicit further repair authorization required. No security-setting changes or wider signalling permission inferred.
- Next gate: Separate #44/#45 ownership and resource-retention design decision, explicit scope approval, new candidate review, original full-tree/#51/F13 and timing acceptance.
- Evidence:
  - author_focused: 136 run: 128 passed, 8 skipped; exit0 in 69.054s
  - docs: 7/7 passed, exit0
  - R4: BLOCKING, reproduced unchanged: child alive, NVENC slot available, #44 used CPU=0 and active_leases=0. No budget schema or reclamation changes authorized or made.
  - timing_gate: Independent 3.187738266s miss of 3s gate retained as failure. Three observed current runs pass with load/affinity recorded, but do not explain historical failure or prove a hard bound. Threshold unchanged.
  - aggregate: Full final aggregate not run
- Exact changed paths: docs/cancellation-resource-ownership.md; docs/runtime-cancellation.md; tests/test_nvenc_oom_guard.py; tests/test_runtime_cancellation.py; vtx/cli.py; vtx/composite.py; vtx/encoders.py; vtx/gfx_cli.py; vtx/gfx_render.py; vtx/infra.py; vtx/proc_supervisor.py; vtx/recovery.py; vtx/runtime.py.
- Sources: outputs/issue45-runtime-repair2/manifest.json; outputs/issue45-runtime-repair2-independent/review.md; outputs/issue45-runtime-repair1-independent/review.md.

## #52: Incremental impact analysis

- Issue/DoD: [52](https://github.com/CongliangK/framivra/issues/52); original issue contract remains authoritative.
- State: incomplete_checkpoint_waiting_contract_and_integration. Roles: Workstream implementer; independent reviewer; integration maintainer.
- Environment: Temporary cloud Linux workspace. Worktree: repos/framivra/development/worktrees/framivra-issue52.
- Identity: branch cloud/issue52-render-impact-20261016; base d9ce4aab52fec57b770f689550fbbde1a860243b; local candidate f9f351b5ac28e683b9bcf4184775b0d373dbd41e; tree 44a54141ea62d91415e1ad427722c749f4d875dd; parent d9ce4aab52fec57b770f689550fbbde1a860243b.
- Publication: head none; published tree none; PR none; CI none (not_run_for_candidate).
- Independent review: Not independently reviewed; not publication candidate.
- Repair rounds used: 0; no additional exception inferred.
- Pending decision: User decision on optional clip.cache_dependencies, project-root relative and union with project.cache_dependencies.
- Next gate: Resolve contract; integrate reviewed #68; shared resolver consumers; supported-resolution HTML acceptance; final rerun/freeze/review.
- Limits: Reduced-size CPU run is not production HTML or performance evidence. Source Fast sampled identity has known middle-byte/restored-mtime blind spot; #85 scope decision may be needed.
- Evidence actually reported:
  - focused: 9 impact tests; earlier 30 timeline_diff/cache_state tests; docs 7 PASS
  - cpu: 30 scenes, 2700 frames, 64x64 CPU reconciliation: trim 7/7, subtitle 1/1, duration 37/37
  - final_runner: Later audio/restored-mtime runner only py_compile; end-to-end rerun pending
- Exact base-to-candidate changed paths: docs/cache-provenance.md; tests/run_impact_reconciliation.py; tests/test_impact_identity.py; vtx/cli.py; vtx/composite.py; vtx/impact.py; vtx/timeline_diff.py.
- Preserved evidence: outputs/issue52-checkpoint-20261016/REPORT.md; outputs/issue52-cpu-distinct-20261016/reconciliation.json.

## #100: Structured agent diagnostics

- Issue/DoD: [100](https://github.com/CongliangK/framivra/issues/100); original issue contract remains authoritative.
- State: held_observed_fail_formal_review_interrupted. Roles: Workstream implementer; independent reviewer; integration maintainer.
- Environment: Temporary cloud Linux workspace. Worktree: development/worktrees/framivra-agent-diagnostics-100-attribution.
- Identity: branch dot/agent-diagnostics-100-attribution; base d9ce4aab52fec57b770f689550fbbde1a860243b; local candidate 545744d30f9982ab35180f75bfa166642cde8fbb; tree 55d41a5d900b71b6c0bc6df1d2dbf52166f06ff4; parent e3514676bb8056953daff6a62734271d3d15c75a.
- Publication: head none; published tree none; PR none; CI none (not_run_for_candidate).
- Independent review: Observed blocking FAIL; formal independent review interrupted; no formal PASS.
- Repair rounds used: 3; Two automatic rounds plus one explicitly approved bounded exception.
- Pending decision: User-requested review-mechanism adjustment pending; no further repair exception inferred.
- Next gate: Resolve review mechanism and explicit repair scope before resumption; maintain original DoD and exact-version gates.
- Limits: Malformed source UnicodeEncodeError can be attributed to null video track instead of runtime.unknown. Historical automatic rounds and bounded exception are exhausted; no publication or closure.
- Evidence actually reported:
  - focused: 146 passed, zero skips, 131.690 s
  - producer_and_acceptance: 148 passed, 31.489 s
  - strict_media: All four first selectors passed, zero skips, 22.057 s; includes PR154 acceptance selector plus three Issue100 actual-media fixtures
  - static: 291 Python, 28 Bash, 6 JS syntax files; combined ffmpeg/chrome inventory passes with all 30 gated selectors
  - docs: 7/7 leaves passed
  - qc_parity: Nine baseline d9ce4aa/opt-in/opt-out scenarios plus six warm exemptions passed
  - baseline_matrix: 47/47 exit codes equal fresh d9ce4aa; six inherited accepted cases retained
  - paired_reviewer_controls: 24 executions all exit 1; 8 baseline/human stdout and terminal exception messages equal; repaired actual-failure attribution passes
  - hosted_ci: Not run; no full aggregate-green claim
- Exact base-to-candidate changed paths: docs/agent-diagnostics.md; plugins/bgm-render/__init__.py; plugins/blender-bridge/__init__.py; plugins/duck-static/__init__.py; scripts/ci_checks.py; tests/fixtures/agent-diagnostics/timeline.invalid.json; tests/test_agent_diagnostics.py; tests/test_agent_diagnostics_attribution.py; tests/test_agent_diagnostics_boundaries.py; tests/test_agent_diagnostics_loading.py; tests/test_agent_diagnostics_plugins.py; tests/test_agent_diagnostics_qc.py; tests/test_agent_diagnostics_repair.py; vtx/agent_diagnostics.py; vtx/diagnostics.py; vtx/infra.py; vtx/pluginlib/hooks.py; vtx/qc.py; vtx/stages.py; vtx/subtitles_src.py.
- Preserved evidence: development/evidence/framivra-agent-diagnostics-100/exception-round/freeze.json; outputs/framivra-diagnostics-100-exception-review/.

## #144: Template layout migration

- Issue/DoD: [144](https://github.com/CongliangK/framivra/issues/144); original issue contract remains authoritative.
- State: resumed_local_only_review_and_verification_active. Roles: Workstream implementer; independent reviewer; integration maintainer.
- Environment: Temporary cloud Linux workspace. Worktree: development/worktrees/framivra-template-layout-144.
- Identity: branch cloud/issue144-template-layout-20261016; base d9ce4aab52fec57b770f689550fbbde1a860243b; local candidate 654856bd82bb68b86deddc0e2c7e632d50c83252; tree 3ee574ac2fb84a4274d39af08df9353e34f37ec3; parent d9ce4aab52fec57b770f689550fbbde1a860243b.
- Publication: head none; published tree none; PR none; CI none (not_run_for_candidate).
- Independent review: Pending on exact frozen candidate.
- Repair rounds used: 0; no additional exception inferred.
- Pending decision: Publication not authorized in current local-only resumption.
- Next gate: Await independent review and verification; preserve prior incomplete worktree; fresh decision before remote publication.
- Limits: Latest bounded CEF/Electron path updates only; no native backend implementation or actual Chrome/CEF/Electron/GPU validation. Earlier incomplete worktree preserved.
- Evidence actually reported:
  - author_focused: {"run": 135, "passed": 127, "skipped": 8, "limits": "5 real Chrome-canary and 3 native CEF tests explicitly gated; no browser/native/GPU execution"}
  - docs: {"passed": 7, "failed": 0, "skipped": 0, "degraded_compile_fallback": 2}
  - golden: {"passed": 4}
  - verification: Independent review and objective verification in progress; intermediate logs are not a final verdict
- Exact base-to-candidate changed paths: .gitignore; AGENTS.md; CONTRIBUTING.md; README.md; apps/cover/README.md; apps/cover/make_cover.py; apps/matting/matting.md; apps/research/README.md; docs/ARCHITECTURE.md; docs/DEV.md; docs/DOC-CHARTER.md; docs/skill/SKILL.md; docs/skill/assets/lower-third.html; docs/skill/assets/title-card.html; docs/skill/references/edit.md; docs/skill/references/gfx.md; docs/skill/references/runbook.md; examples/skill-demo/skill.json; experiments/gfx_cef_worker/README.md; experiments/gfx_cef_worker/probe_display_matrix.py; experiments/gfx_electron_worker/drill_sigkill.py; experiments/gfx_electron_worker/main.mjs; experiments/gfx_electron_worker/run_gfx_electron_worker.py; pyproject.toml; scripts/doc_budget.snapshot.json; scripts/docs_smoke.sh; scripts/new_project.sh; scripts/run_all_tests.sh; scripts/verify_contract_surface.py; templates/README.md; templates/hello-app/README.md; templates/hello-app/app.json; templates/hello-app/cli.py; templates/hello-app/health_check.py; templates/hello-app/tests/__init__.py; templates/hello-app/tests/test_hello.py; templates/hello-plugin/README.md; templates/hello-plugin/__init__.py; templates/hello-plugin/health_check.py; templates/hello-plugin/plugin.json; templates/hello-plugin/tests/__init__.py; templates/hello-plugin/tests/test_hello_plugin.py; templates/skill-demo/.gitignore; templates/skill-demo/SKILL.md; templates/skill-demo/expected_check.sh; templates/skill-demo/gen_fixture.sh; templates/skill-demo/gfxA.html; templates/skill-demo/skill.json; templates/skill-demo/timeline.json; templates/tech-talk/SKILL.md; templates/tech-talk/assets/brief-template.md; templates/tech-talk/assets/gfx-examples/gfxA.html; templates/tech-talk/assets/gfx-examples/gfxB.html; templates/tech-talk/assets/gfx-examples/gfxC.html; templates/tech-talk/assets/gfx-examples/motion.html; templates/tech-talk/assets/gfx-templates/INDEX.md; templates/tech-talk/assets/gfx-templates/_tokens.md; templates/tech-talk/assets/gfx-templates/chart_curve.html; templates/tech-talk/assets/gfx-templates/data_table_grouped.html; templates/tech-talk/assets/gfx-templates/endboard.html; templates/tech-talk/assets/gfx-templates/nav_card.html; templates/tech-talk/assets/gfx-templates/overlay_card_bottom.html; templates/tech-talk/assets/gfx-templates/overlay_card_top.html; templates/tech-talk/assets/presets/gates.json; templates/tech-talk/assets/presets/mixing.json; templates/tech-talk/assets/presets/placeholders/README.md; templates/tech-talk/assets/presets/placeholders/placeholder_audio.wav; templates/tech-talk/assets/presets/placeholders/placeholder_av.mp4; templates/tech-talk/assets/presets/placeholders/placeholder_gfx.html; templates/tech-talk/assets/presets/timeline.skeleton.json; templates/tech-talk/assets/supplement.template.json; templates/tech-talk/references/aroll-pipeline.md; templates/tech-talk/references/audio-mixing.md; templates/tech-talk/references/cover.md; templates/tech-talk/references/narrative.md; templates/tech-talk/references/production-workflow.md; templates/tech-talk/references/research.md; templates/tech-talk/references/runbook-r5r6r7.md; templates/tech-talk/references/shooting-guide.md; templates/tech-talk/references/shooting.md; templates/tech-talk/references/visual-baseline.md; templates/tech-talk/references/voice-ime-paradigm.md; templates/tech-talk/references/voice-style.md; templates/tech-talk/scripts/freeze.py; templates/tech-talk/scripts/new_project.sh; templates/tech-talk/scripts/publish_pack.py; templates/tech-talk/skill.json; templates/tech-talk/templates/00-structure.md; templates/tech-talk/templates/02-script.md; templates/tech-talk/templates/03-assets-plan.md; templates/tech-talk/templates/06-teleprompter.md; tests/gfx_bench_suite/suite_manifest.json; tests/test_cef_worker_supervision.py; tests/test_frame_canary.py; tests/test_gfx_bench_suite.py; tests/test_package_smoke.py; tests/test_root_alias_retirement.py; tests/test_template_layout.py; tests/test_vt_entry.py; vt.py; vtx/api.py; vtx/frame_canary.py.
- Preserved evidence: development/evidence/template-migration-resume/candidate-freeze.json; development/evidence/template-migration-resume/asset-inventory.json; development/evidence/template-migration-resume/pending-three-way.json; development/evidence/template-migration-resume/smoke-invariant.json.

## Completed in this goal

| Issue / original outcome | Merged delivery | Acceptance evidence and limits |
|---|---|---|
| [#44 resource budget](https://github.com/CongliangK/framivra/issues/44): bounded cooperative resource applications, reclamation and retained cross-project proof | [PR #148](https://github.com/CongliangK/framivra/pull/148) plus [PR #151](https://github.com/CongliangK/framivra/pull/151); final merge 1cecc40dba44eac912c39a35e8c4fff7066f700e | [Postmerge CI 38855356287](https://github.com/CongliangK/framivra/actions/runs/38855356287), all 9 jobs PASS; issue closed. Eight-project/96-acquisition fixture and adversarial accounting checks retained in repository. Cooperative tokens, simulated VRAM; no physical peak cap or GPU throughput claim. |
| [#49 compatibility matrix](https://github.com/CongliangK/framivra/issues/49): reject incompatible plugins before execution with guidance | [PR #148](https://github.com/CongliangK/framivra/pull/148) plus [PR #152](https://github.com/CongliangK/framivra/pull/152); final merge d17d07e955153dddb0e20596d6f29e393bc38701 | [Postmerge CI 38858297056](https://github.com/CongliangK/framivra/actions/runs/38858297056), all 9 jobs PASS; issue closed. Focused 99, protocol 10, external-entrypoint 104, hook combinations 320 and Core-conflict 8 checks documented. Local full aggregate incomplete; hosted checks supply separate acceptance evidence. No external model/API availability claim. |
| [#53 project acceptance package](https://github.com/CongliangK/framivra/issues/53): independently verifiable format, public CPU fixture and tamper rejection | [PR #154](https://github.com/CongliangK/framivra/pull/154); merge d9ce4aab52fec57b770f689550fbbde1a860243b | [Postmerge CI 38862855114](https://github.com/CongliangK/framivra/actions/runs/38862855114), all 9 jobs PASS; issue closed completed at 06:54:57 UTC. Original DoD and completion proof remain authoritative. accepted=false; visual content and dependency closure unknown; runtime identity Fast. No #43/#85/#91 completion claim. |

## Deferred and protected work

- #87: CI latency requires frozen timing/coverage metrics; no permission to weaken checks or alter branch protection.
- #18: licensing evidence remains incomplete; exact versions, hashes, source licenses and redistribution boundaries needed.
- #145 original-video acceptance, #153 CEF defect, #140/#142 ASR/native ownership: remain separate protected scopes; #144 path migration is not implementation approval for them.
- Coagentdraw PR #48 docs-only archive remains merged at 8c457d35ab14612c57e871bf85fb26135a6e0169; no new development/publication authorization.

## Durable home and next publication gate

Use metadata-only branch meta/cloud-orchestration-ledger, expected predecessor 3f7359bd6f06faa4caef5abc289f3769751c25fd. Proposed tree contains exactly orchestration-ledger.md and orchestration-facts.json, without a source base tree. Never merge this metadata branch into main. No remote writes were made for this snapshot.
Before publication, independently review the exact frozen bytes, obtain explicit approval, recheck current heads/CI/approvals and expected predecessor, publish only the approved two-file metadata tree/ref, then read back and hash-verify both blobs. Ancillary freeze, diff and observation files remain local review artifacts.
This ledger supplements original issues and product documentation; it does not change DoD or create a fifth product-documentation layer. GitHub links are durable where published; relative cloud paths are temporary location hints, not public evidence links. Their future availability is not guaranteed. Unknown and unrun gates remain unknown and unrun.
