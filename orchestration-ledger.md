# Framivra orchestration ledger

Frozen local draft, not published. Snapshot 2026-10-16T09:21:15Z; GitHub observations through 2026-10-16T09:18:31Z. Historical handoff, not a live dashboard.
Repository: https://github.com/CongliangK/framivra. Metadata predecessor: 743bcae2c08d4ba12633122ffea230a2f8643360.

## Goal and verified remote state

Verified original-goal completion: **5/20: #44, #49, #53, #144, #68**. 47 ordinary open issues plus one open draft PR #156 = 48 aggregate. Baseline 50 - 5 closures + new issues #153/#155 = 47. New issues do not count toward original goal.
Main e928657f2833155423f6ccfca4869a1805b34bde; tree acc8a2ef5776614927f4e93f03e37d062eec4445. No rapid-completion or continuous-execution guarantee.

## Scope and decisions

Cloud-only. User-local Codex publication withdrawn. No credential-invalid claim is made.
Independent changes may overlap; integrate serially and reverify exact trees. Preserve original contracts, user-local work and all prior failures. No security-setting changes. Coagentdraw remains paused after its docs-only archive.
08:36 UTC decision batch remains pending: #90 human §25 contract, #52 optional clip.cache_dependencies, one extra mechanism-adjusted repair round each #100/#18, #155 cloud publication retry. #45 ownership/lease decision is still needed but has not yet been asked.

## Active

### #90: Scoped quality and evidence labels
- Issue: https://github.com/CongliangK/framivra/issues/90
- state: draft_pr_ci_pass_human_contract_pending
- owner_role: Workstream implementer; independent reviewer; integration maintainer
- environment: Temporary cloud Linux workspace
- branch: cloud/pr156-main-integration-20261016
- base: e8d9ee739f851bdf50acc4bc7f1567423d7dafb7
- local_candidate: ac86eee8b30db596cd319f8e1a5ef1d77097a9e4
- tree: 26cd182944e9af05b6feddf8bed789cb4a881787
- parent: b82730bcfb2c2d73814c988c4cf62229565b2d52
- published_head: f145d849296088ad5ac61700b0478e890071348c
- published_tree: 26cd182944e9af05b6feddf8bed789cb4a881787
- pr_url: https://github.com/CongliangK/framivra/pull/156
- ci_url: https://github.com/CongliangK/framivra/actions/runs/38876253662
- ci_status: completed_success
- worktree_relative: development/worktrees/framivra-pr156-main-integration
- write_scope: ["docs/cache-provenance.md", "scripts/gfx_evidence.py", "scripts/gfx_golden.py", "tests/test_gfx_evidence.py", "tests/test_gfx_golden.py", "tests/test_quality.py", "vtx/infra.py", "vtx/qc.py", "vtx/quality.py"]
- repair_rounds_used: 1
- pending_approval: Human document/contract maintainer approval under DOC-CHARTER §25, then separate merge decision
- checks: {"focused": "87 tests; 4096 classifier combinations; 36 malformed receipt/begin-stage combinations", "independent": "14 integration/adversarial cases PASS", "cpu": "60-frame CPU/FFmpeg fixture structural QC 8/8; accepted=false; four tamper negatives reject", "hosted_ci": "Run 38876253662, exact f145d849 head: all 9 PASS"}
- independent_review: PASS bounded technical review; does not supply human contract approval
- evidence_relative: ["development/evidence/issue90/repair1/freeze.json", "development/evidence/issue90/main-integration/freeze.json", "development/evidence/issue90/main-integration/publication-proof.json"]
- next_gate: Human DOC-CHARTER §25 approval pending; integrate against new e928657 main, review changed exact tree, CI and separate merge decision
- limits: Annotation-only; Fast runtime; visual_content unknown; no final-movie Exact upgrade, authentic-producer, browser/GPU determinism or performance certification
- ci_jobs: {"success": 9, "failed": 0}
- contract_url: https://github.com/CongliangK/framivra/blob/f145d849296088ad5ac61700b0478e890071348c/docs/cache-provenance.md
- ordered_parents: ["b82730bcfb2c2d73814c988c4cf62229565b2d52", "e8d9ee739f851bdf50acc4bc7f1567423d7dafb7"]
- Prior checkpoint preserved verbatim under historical_checkpoint in companion JSON; older test claims are not reassigned to current SHA.

### #155: Completion-marker publication race
- Issue: https://github.com/CongliangK/framivra/issues/155
- state: integrated_review_and_repeated_clip_pass_cloud_publication_cancelled
- owner_role: Workstream implementer; independent reviewer; integration maintainer
- environment: Temporary cloud Linux workspace
- branch: None
- base: e8d9ee739f851bdf50acc4bc7f1567423d7dafb7
- local_candidate: eac6b250fd8c3d6204abab67036b9ea5b21b5dc3
- tree: ac4ebe1b35d57c979526c793f84de97dc1d8bbc2
- parent: cdeeee2d62b0515965945c81beda693b694270c4
- published_head: None
- published_tree: None
- pr_url: None
- ci_url: None
- ci_status: not_run_for_candidate
- worktree_relative: development/worktrees/framivra-segment-marker-main
- write_scope: ["tests/test_failure_recovery.py", "vtx/fstxn.py", "vtx/recovery.py"]
- repair_rounds_used: 1
- pending_approval: Explicit cloud-publication retry pending after two cancellations; local Codex publication withdrawn
- checks: {"focused_review": "48 tests,47 PASS,one Chrome skip", "real_writers": "Two-writer barrier and locking PASS on exact candidate", "repeated_clip": "30 repeated clips,2700 frames,64x64,30fps; cold/warm decode PASS and identical output SHA256; all 11 extant-media certificates verified; no temporary leftovers; 29 retired-intermediate sidecars explicitly retained", "hosted_ci": "Not run; no PR"}
- independent_review: PASS bounded exact eac6b250 technical review
- evidence_relative: ["outputs/segment-marker-main-integration-20261016/REPORT.md", "outputs/segment-marker-main-review-20261016/REPORT.md", "outputs/segment-marker-main-acceptance-20261016/REPORT.md"]
- next_gate: Explicit cloud retry authorization; new main integration and exact-tree review before publication; hosted CI and standalone issue DoD gates
- limits: 2700-frame CPU fixture now fresh on exact eac6b250. No full aggregate/native Windows/GPU/Chrome claim. Future #45 integration is separate and not a dependency for standalone #155 closure.
- counts_toward_original_goal: False
- ordered_parents: ["cdeeee2d62b0515965945c81beda693b694270c4"]
- Prior checkpoint preserved verbatim under historical_checkpoint in companion JSON; older test claims are not reassigned to current SHA.

### #45: F12 cancellation and F13 adaptive concurrency
- Issue: https://github.com/CongliangK/framivra/issues/45
- state: frozen_repair2_independent_blocked_fail
- owner_role: Workstream implementer; independent reviewer; integration maintainer
- environment: Temporary cloud Linux workspace
- branch: cloud/issue45-runtime-cancellation-20261016
- base: d9ce4aab52fec57b770f689550fbbde1a860243b
- local_candidate: 91b3783269c3164d12bdb999cf4918440be9961a
- tree: fc8ce6f0eeffb3476b2aafa3ab9368782c26d1e6
- parent: 581d059fd334eedd20ae7ced734f5f1b9c84178a
- published_head: None
- published_tree: None
- pr_url: None
- ci_url: None
- ci_status: not_run_for_candidate
- worktree_relative: development/worktrees/framivra-issue45
- write_scope: ["docs/cancellation-resource-ownership.md", "docs/runtime-cancellation.md", "tests/test_nvenc_oom_guard.py", "tests/test_runtime_cancellation.py", "vtx/cli.py", "vtx/composite.py", "vtx/encoders.py", "vtx/gfx_cli.py", "vtx/gfx_render.py", "vtx/infra.py", "vtx/proc_supervisor.py", "vtx/recovery.py", "vtx/runtime.py"]
- repair_rounds_used: 2
- pending_approval: Ownership/lease architecture decision and explicit further repair authorization needed; decision has not yet been asked
- checks: {"author_focused": "136 run: 128 passed, 8 skipped; exit0 in 69.054s", "docs": "7/7 passed, exit0", "R4": "BLOCKING, reproduced unchanged: child alive, NVENC slot available, #44 used CPU=0 and active_leases=0. No budget schema or reclamation changes authorized or made.", "timing_gate": "Independent 3.187738266s miss of 3s gate retained as failure. Three observed current runs pass with load/affinity recorded, but do not explain historical failure or prove a hard bound. Threshold unchanged.", "aggregate": "Full final aggregate not run"}
- independent_review: BLOCKED / FAIL on exact repair2; formal report now present
- evidence_relative: ["outputs/issue45-runtime-repair2/manifest.json", "outputs/issue45-runtime-repair2-independent/review.md", "outputs/issue45-runtime-repair1-independent/review.md"]
- next_gate: Separate #44/#45 ownership and resource-retention design decision, explicit scope approval, new candidate review, original full-tree/#51/F13 and timing acceptance
- limits: R4 remains: owned child alive while NVENC slot and #44 capacity are released. R5–R7 bounded corrections do not resolve ownership architecture. Full-tree cleanup, #51, F13, target backend and timing acceptance unmet; no publication.
- candidate_identity_scope: Exact final automatic repair2 candidate; independently BLOCKED / FAIL
- repair_round_active: None
- successor_candidate: None
- repair_rounds_remaining_after_active: 0
- repair_count_detail: Both automatic repair rounds exhausted; no additional repair exception inferred
- repair_rounds_remaining: 0

### #52: Incremental impact analysis
- Issue: https://github.com/CongliangK/framivra/issues/52
- state: frozen_integrated_incomplete_checkpoint_contract_pending
- owner_role: Workstream implementer; independent reviewer; integration maintainer
- environment: Temporary cloud Linux workspace
- branch: cloud/issue52-integrated-20261016
- base: e928657f2833155423f6ccfca4869a1805b34bde
- local_candidate: 8a5b71bce0ac67b6159dbb4e381e9173171e87cd
- tree: 174f9da7907f656a61751ca922500397e7fb018a
- parent: e928657f2833155423f6ccfca4869a1805b34bde
- published_head: None
- published_tree: None
- pr_url: None
- ci_url: None
- ci_status: not_run_for_candidate
- worktree_relative: development/worktrees/framivra-issue52-integrated
- write_scope: ["docs/cache-provenance.md", "tests/run_impact_reconciliation.py", "tests/test_frame_source.py", "tests/test_impact_identity.py", "vtx/cli.py", "vtx/composite.py", "vtx/impact.py", "vtx/timeline_diff.py"]
- repair_rounds_used: 0
- pending_approval: User decision on optional clip.cache_dependencies, project-root relative and union with project.cache_dependencies
- checks: {"focused": "69 tests,2 opt-in skips; final identity 30 tests,2 skips", "layout": "14 passed", "docs": "7 passed", "cpu": "30 scenes,2700 frames,64x64; trim7/7,subtitle1/1,duration37/37,restored-mtime audio1/1; no unexpected rewrites; no-op size/mtime preserved", "png_consumer": "3 opt-in generated-PNG/subtitle tests passed", "aggregate": "Not run"}
- independent_review: Not independently reviewed; not publication candidate
- evidence_relative: ["outputs/issue52-integration-20261016/manifest.json", "outputs/issue52-integration-20261016/CHECKPOINT.md"]
- next_gate: Optional clip.cache_dependencies contract decision; page-aware resolver; production HTML acceptance; final independent review
- limits: Reduced-size CPU and generated PNG evidence only; no production HTML or performance proof. Existing Fast source-media identity limitation retained.
- integration_target: e928657f2833155423f6ccfca4869a1805b34bde
- candidate_identity_scope: Exact new integrated checkpoint; old f9f351 preserved; not publication candidate
- ordered_parents: ["e928657f2833155423f6ccfca4869a1805b34bde"]
- Prior checkpoint preserved verbatim under historical_checkpoint in companion JSON; older test claims are not reassigned to current SHA.

### #100: Structured agent diagnostics
- Issue: https://github.com/CongliangK/framivra/issues/100
- state: held_observed_fail_formal_review_interrupted
- owner_role: Workstream implementer; independent reviewer; integration maintainer
- environment: Temporary cloud Linux workspace
- branch: dot/agent-diagnostics-100-attribution
- base: d9ce4aab52fec57b770f689550fbbde1a860243b
- local_candidate: 545744d30f9982ab35180f75bfa166642cde8fbb
- tree: 55d41a5d900b71b6c0bc6df1d2dbf52166f06ff4
- parent: e3514676bb8056953daff6a62734271d3d15c75a
- published_head: None
- published_tree: None
- pr_url: None
- ci_url: None
- ci_status: not_run_for_candidate
- worktree_relative: development/worktrees/framivra-agent-diagnostics-100-attribution
- write_scope: ["docs/agent-diagnostics.md", "plugins/bgm-render/__init__.py", "plugins/blender-bridge/__init__.py", "plugins/duck-static/__init__.py", "scripts/ci_checks.py", "tests/fixtures/agent-diagnostics/timeline.invalid.json", "tests/test_agent_diagnostics.py", "tests/test_agent_diagnostics_attribution.py", "tests/test_agent_diagnostics_boundaries.py", "tests/test_agent_diagnostics_loading.py", "tests/test_agent_diagnostics_plugins.py", "tests/test_agent_diagnostics_qc.py", "tests/test_agent_diagnostics_repair.py", "vtx/agent_diagnostics.py", "vtx/diagnostics.py", "vtx/infra.py", "vtx/pluginlib/hooks.py", "vtx/qc.py", "vtx/stages.py", "vtx/subtitles_src.py"]
- repair_rounds_used: 3
- pending_approval: Additional mechanism-adjusted repair round requested in 08:36 UTC decision batch; no answer/authorization recorded
- checks: {"focused": "146 passed, zero skips, 131.690 s", "producer_and_acceptance": "148 passed, 31.489 s", "strict_media": "All four first selectors passed, zero skips, 22.057 s; includes PR154 acceptance selector plus three Issue100 actual-media fixtures", "static": "291 Python, 28 Bash, 6 JS syntax files; combined ffmpeg/chrome inventory passes with all 30 gated selectors", "docs": "7/7 leaves passed", "qc_parity": "Nine baseline d9ce4aa/opt-in/opt-out scenarios plus six warm exemptions passed", "baseline_matrix": "47/47 exit codes equal fresh d9ce4aa; six inherited accepted cases retained", "paired_reviewer_controls": "24 executions all exit 1; 8 baseline/human stdout and terminal exception messages equal; repaired actual-failure attribution passes", "hosted_ci": "Not run; no full aggregate-green claim"}
- independent_review: Observed blocking FAIL; formal independent review interrupted; no formal PASS
- evidence_relative: ["development/evidence/framivra-agent-diagnostics-100/exception-round/freeze.json", "outputs/framivra-diagnostics-100-exception-review/"]
- next_gate: Await explicit additional-round authorization; preserve all prior failures and exhausted rounds
- limits: Malformed source UnicodeEncodeError can be attributed to null video track instead of runtime.unknown. Historical automatic rounds and bounded exception are exhausted; no publication or closure
- repair_count_detail: Two automatic rounds plus one explicitly approved bounded exception
- review_findings: ["Absent tr local can match null track, suppressing runtime.unknown; empty-track control reports runtime.unknown"]
- repair_rounds_remaining: 0

### #18: Distribution model-license inventory
- Issue: https://github.com/CongliangK/framivra/issues/18
- published_head: None
- published_tree: None
- pr_url: None
- ci_url: None
- state: parked_review_blocked
- owner_role: Workstream implementer; independent reviewer
- environment: Temporary cloud Linux workspace
- branch: None
- worktree_relative: None
- repair_rounds_used: 2
- repair_rounds_remaining: 0
- repair_count_detail: Two automatic repair rounds exhausted; requested extra round remains unapproved
- pending_approval: One extra mechanism-adjusted repair round requested at 08:36 UTC; no response recorded
- checks: {"focused-tests": {"exit": 0, "count": 30}, "doc-gates": {"exit": 0, "count": 7}, "payload-audit": {"exit": 0}, "build": {"exit": 0}, "sdist-rebuild": {"exit": 0}}
- independent_review: BLOCKED: Required tar evidence members may be symlinks whose target strings hash to expected notice bytes; regular-file type must be required before content hashes can establish notice presence.
- next_gate: Explicit additional-round authorization; regular-member auditor repair and fresh review
- limits: Artifact integrity checks do not grant legal clearance; aggregate aborted exit130
- ci_status: not_run_for_candidate
- evidence_relative: ["outputs/framivra-license-inventory-18-final/candidate-ledger.json", "outputs/framivra-license-inventory-18-final-redteam/review.md"]
- local_candidate: 646bd34da88681ab4785ac1464efe9790c3f7c8b
- base: d9ce4aab52fec57b770f689550fbbde1a860243b
- tree: d14a897891aa526f6a18cc13702216afd9f72f87
- parent: 2ea56005c515d20b0f1ba59c23272ed8caf52411
- ordered_parents: ["2ea56005c515d20b0f1ba59c23272ed8caf52411"]
- write_scope: ["docs/model-license-inventory.json", "docs/third-party-licenses.md", "scripts/distribution_license_audit.py", "scripts/model_license_policy.py", "tests/test_distribution_license_audit.py", "tests/test_model_licenses.py"]

### #87: CI feedback and coverage partition
- Issue: https://github.com/CongliangK/framivra/issues/87
- published_head: None
- published_tree: None
- pr_url: None
- ci_url: None
- branch: None
- state: repair1_technical_review_pass_objective_verification_pending
- owner_role: Workstream implementer; independent reviewer; objective verifier
- environment: Temporary cloud Linux workspace
- worktree_relative: development/worktrees/framivra-ci-feedback-87-repair1
- repair_rounds_used: 1
- ci_status: not_run_for_candidate
- pending_approval: No publication authorization; owner-supplied required-context verification needed
- checks: {"focused_py312": "42 pass", "focused_py313": "42 pass", "base_environment_attribution": "Unchanged base reproduces installed Blender 4.3.2 incompatibility; 36 CLI tests pass with real Chromium PATH alias. These are not hosted Chrome evidence."}
- independent_review: TECHNICAL PASS on exact repair1; EVIDENCE INSUFFICIENT to close original Issue87
- next_gate: Finish objective/layered strict-media verification; authorized hosted controlled before/after runs and required-context confirmation
- limits: No proven <=4.5m target, P50/P95 or cost reduction; n=1 baseline. No separate slow context; no apt cache. Branch protection GET403 respected; no settings changes
- required_context_verification: {"state": "unverified_access_denied", "url": "https://api.github.com/repos/CongliangK/framivra/branches/main/protection", "http_status": 403, "error": "Resource not accessible by integration", "changes_made": false}
- evidence_relative: ["outputs/issue87-repair1/manifest.json", "outputs/issue87-repair1-review/review.txt"]
- local_candidate: 391a0861fab5d48a0858cfecd3084f9fd334090f
- base: e928657f2833155423f6ccfca4869a1805b34bde
- tree: a9be67c90a462f77dab4131b77ec3a6e158a83bb
- parent: 2a804f23366cb584174e3b6f1b0facfb9c0731bc
- ordered_parents: ["2a804f23366cb584174e3b6f1b0facfb9c0731bc"]
- write_scope: [".github/workflows/ci.yml", "scripts/run_all_tests.sh", "tests/ci_partition.py", "tests/test_ci_feedback.py"]

## Completed

### #44: Completed workstream
- Issue: https://github.com/CongliangK/framivra/issues/44
- prs: [148, 151]
- merge: 1cecc40dba44eac912c39a35e8c4fff7066f700e
- postmerge_ci: 38855356287
- passed_jobs: 9

### #49: Completed workstream
- Issue: https://github.com/CongliangK/framivra/issues/49
- prs: [148, 152]
- merge: d17d07e955153dddb0e20596d6f29e393bc38701
- postmerge_ci: 38858297056
- passed_jobs: 9

### #53: Completed workstream
- Issue: https://github.com/CongliangK/framivra/issues/53
- state: merged_postmerge_ci_success_issue_closed_completed
- owner_role: Acceptance-package implementer; independent reviewer; integration maintainer
- branch: dot/project-acceptance-53-ci-registration
- base: d17d07e955153dddb0e20596d6f29e393bc38701
- local_candidate: 3edcd5ba390629fa22957ebbdd7474215d6c6d7a
- published_head: c353fae63866fa986f73460ab541d683d89bbcc5
- tree: 4efc0290ac73bec2a59299386bcb9197760f5374
- repair_rounds_used: 3
- pending_approval: None
- pr_url: https://github.com/CongliangK/framivra/pull/154
- ci_url: https://github.com/CongliangK/framivra/actions/runs/38861826658
- ci_status: completed_success
- worktree_relative: development/worktrees/framivra-acceptance-53-ci-registration
- write_scope: ["docs/project-acceptance.md", "scripts/acceptance_fixture.py", "tests/test_acceptance.py", "vtx/acceptance.py", "scripts/ci_checks.py"]
- ci_jobs: {"failed": 0, "success": 9}
- evidence_relative: ["outputs/acceptance-53-publication/terminal-proof.json", "outputs/acceptance-53-publication/proof.json", "outputs/framivra-acceptance-53-redteam/transplant-review/identity.txt", "outputs/acceptance-53-ci-registration/freeze.json", "outputs/acceptance-53-ci-registration/independent-review/review.md", "outputs/acceptance-53-ci-registration/publication-proof.json", "outputs/acceptance-53-ci-registration/final-gate-proof.json", "outputs/acceptance-53-ci-registration/completion-proof.json"]
- published_branch: dot/project-acceptance-53-final
- parent: 897edd6606155abb7a3da797f5d1d0951245be00
- published_tree: 4efc0290ac73bec2a59299386bcb9197760f5374
- repair_count_detail: 2 automatic rounds plus 1 explicit user-approved CI-registration-only exception
- approval: User approved the single bounded extra CI-registration round at 2026-10-16 06:25 UTC; no further exception inferred
- repair_review: PASS exact selector-only candidate; 12-worktree local-overlap clearance reported 06:29:56 UTC
- repair_checks: {"static": "pass", "ci_contract": "23 passed", "strict_selector": "1 passed, zero skips"}
- remote_update_performed: True
- repair_delta: {"scripts/ci_checks.py": "one added strict FFmpeg selector; four acceptance blobs unchanged"}
- merge_gate: Merged exact reviewed tree; all nine postmerge CI jobs PASS; fresh DoD decision recorded and Issue53 closed completed
- merge: d9ce4aab52fec57b770f689550fbbde1a860243b
- postmerge_ci_url: https://github.com/CongliangK/framivra/actions/runs/38862855114
- postmerge_ci_status: completed_success
- postmerge_ci_jobs: {"success": 9, "failed": 0}
- closed_at_utc: 2026-10-16T06:54:57Z
- state_reason: completed
- prs: [154]
- postmerge_ci: 38862855114
- passed_jobs: 9

### #144: Template layout migration
- Issue: https://github.com/CongliangK/framivra/issues/144
- state: merged_postmerge_ci_success_issue_closed_completed
- owner_role: Workstream implementer; independent reviewer; integration maintainer
- environment: Temporary cloud Linux workspace
- branch: cloud/issue144-template-layout-20261016
- base: d9ce4aab52fec57b770f689550fbbde1a860243b
- local_candidate: fae083447ba71a2ddc61865678e400a72be40521
- tree: dd64238dc720f90cac02617293ab4864229efb98
- parent: 654856bd82bb68b86deddc0e2c7e632d50c83252
- published_head: 1cb21b6874f721d15dd53c143079a152cca20d9d
- published_tree: dd64238dc720f90cac02617293ab4864229efb98
- pr_url: https://github.com/CongliangK/framivra/pull/158
- ci_url: https://github.com/CongliangK/framivra/actions/runs/38875272909
- ci_status: completed_success
- worktree_relative: development/worktrees/framivra-template-layout-144
- write_scope: [".gitignore", "AGENTS.md", "CONTRIBUTING.md", "README.md", "apps/cover/README.md", "apps/cover/make_cover.py", "apps/matting/matting.md", "apps/research/README.md", "docs/ARCHITECTURE.md", "docs/DEV.md", "docs/DOC-CHARTER.md", "docs/skill/SKILL.md", "docs/skill/assets/lower-third.html", "docs/skill/assets/title-card.html", "docs/skill/references/edit.md", "docs/skill/references/gfx.md", "docs/skill/references/runbook.md", "examples/skill-demo/skill.json", "experiments/gfx_cef_worker/README.md", "experiments/gfx_cef_worker/probe_display_matrix.py", "experiments/gfx_electron_worker/drill_sigkill.py", "experiments/gfx_electron_worker/main.mjs", "experiments/gfx_electron_worker/run_gfx_electron_worker.py", "pyproject.toml", "scripts/doc_budget.snapshot.json", "scripts/docs_smoke.sh", "scripts/new_project.sh", "scripts/run_all_tests.sh", "scripts/verify_contract_surface.py", "templates/README.md", "templates/hello-app/README.md", "templates/hello-app/app.json", "templates/hello-app/cli.py", "templates/hello-app/health_check.py", "templates/hello-app/tests/__init__.py", "templates/hello-app/tests/test_hello.py", "templates/hello-plugin/README.md", "templates/hello-plugin/__init__.py", "templates/hello-plugin/health_check.py", "templates/hello-plugin/plugin.json", "templates/hello-plugin/tests/__init__.py", "templates/hello-plugin/tests/test_hello_plugin.py", "templates/skill-demo/.gitignore", "templates/skill-demo/SKILL.md", "templates/skill-demo/expected_check.sh", "templates/skill-demo/gen_fixture.sh", "templates/skill-demo/gfxA.html", "templates/skill-demo/skill.json", "templates/skill-demo/timeline.json", "templates/tech-talk/SKILL.md", "templates/tech-talk/assets/brief-template.md", "templates/tech-talk/assets/gfx-examples/gfxA.html", "templates/tech-talk/assets/gfx-examples/gfxB.html", "templates/tech-talk/assets/gfx-examples/gfxC.html", "templates/tech-talk/assets/gfx-examples/motion.html", "templates/tech-talk/assets/gfx-templates/INDEX.md", "templates/tech-talk/assets/gfx-templates/_tokens.md", "templates/tech-talk/assets/gfx-templates/chart_curve.html", "templates/tech-talk/assets/gfx-templates/data_table_grouped.html", "templates/tech-talk/assets/gfx-templates/endboard.html", "templates/tech-talk/assets/gfx-templates/nav_card.html", "templates/tech-talk/assets/gfx-templates/overlay_card_bottom.html", "templates/tech-talk/assets/gfx-templates/overlay_card_top.html", "templates/tech-talk/assets/presets/gates.json", "templates/tech-talk/assets/presets/mixing.json", "templates/tech-talk/assets/presets/placeholders/README.md", "templates/tech-talk/assets/presets/placeholders/placeholder_audio.wav", "templates/tech-talk/assets/presets/placeholders/placeholder_av.mp4", "templates/tech-talk/assets/presets/placeholders/placeholder_gfx.html", "templates/tech-talk/assets/presets/timeline.skeleton.json", "templates/tech-talk/assets/supplement.template.json", "templates/tech-talk/references/aroll-pipeline.md", "templates/tech-talk/references/audio-mixing.md", "templates/tech-talk/references/cover.md", "templates/tech-talk/references/narrative.md", "templates/tech-talk/references/production-workflow.md", "templates/tech-talk/references/research.md", "templates/tech-talk/references/runbook-r5r6r7.md", "templates/tech-talk/references/shooting-guide.md", "templates/tech-talk/references/shooting.md", "templates/tech-talk/references/visual-baseline.md", "templates/tech-talk/references/voice-ime-paradigm.md", "templates/tech-talk/references/voice-style.md", "templates/tech-talk/scripts/freeze.py", "templates/tech-talk/scripts/new_project.sh", "templates/tech-talk/scripts/publish_pack.py", "templates/tech-talk/skill.json", "templates/tech-talk/templates/00-structure.md", "templates/tech-talk/templates/02-script.md", "templates/tech-talk/templates/03-assets-plan.md", "templates/tech-talk/templates/06-teleprompter.md", "tests/gfx_bench_suite/suite_manifest.json", "tests/test_cef_worker_supervision.py", "tests/test_frame_canary.py", "tests/test_gfx_bench_suite.py", "tests/test_package_smoke.py", "tests/test_root_alias_retirement.py", "tests/test_template_layout.py", "tests/test_vt_entry.py", "vt.py", "vtx/api.py", "vtx/frame_canary.py"]
- repair_rounds_used: 1
- pending_approval: None
- checks: {"hosted_postmerge_ci": "All nine jobs PASS, exact merge SHA freshly verified", "historical_checks": "Prior checks retained under historical_checkpoint, not reassigned to final candidate"}
- independent_review: PASS exact integrated tree; closure verified freshly on GitHub
- evidence_relative: ["development/evidence/template-migration-resume/candidate-freeze.json", "development/evidence/template-migration-resume/final-redteam-review.md"]
- next_gate: Completed original issue; preserve bounded acceptance limits
- limits: Scoped template/path migration; no native backend implementation or native/GPU performance claim
- ordered_parents: ["654856bd82bb68b86deddc0e2c7e632d50c83252"]
- merge: e8d9ee739f851bdf50acc4bc7f1567423d7dafb7
- prs: [158]
- postmerge_ci: 38875272909
- passed_jobs: 9
- postmerge_ci_url: https://github.com/CongliangK/framivra/actions/runs/38875272909
- postmerge_ci_status: completed_success
- closed_at_utc: 2026-10-16T08:51:30Z
- state_reason: completed
- Prior checkpoint preserved verbatim under historical_checkpoint in companion JSON; older test claims are not reassigned to current SHA.

### #68: PNG FrameSource consumer boundary
- Issue: https://github.com/CongliangK/framivra/issues/68
- state: merged_postmerge_ci_success_issue_closed_completed
- owner_role: Workstream implementer; independent reviewer; integration maintainer
- environment: Temporary cloud Linux workspace
- branch: cloud/issue68-png-framesource-20261016
- base: e8d9ee739f851bdf50acc4bc7f1567423d7dafb7
- local_candidate: 7fdec502b9ef02b4caf0f16be74b01f7d1b6c4ff
- tree: acc8a2ef5776614927f4e93f03e37d062eec4445
- parent: b9e44b30d3acb4df476a7087104831e2b26fa411
- published_head: 52fa6809e78e698f313f2f1883d6c10e57eb20d8
- published_tree: acc8a2ef5776614927f4e93f03e37d062eec4445
- pr_url: https://github.com/CongliangK/framivra/pull/157
- ci_url: https://github.com/CongliangK/framivra/actions/runs/38877365335
- ci_status: completed_success
- worktree_relative: development/worktrees/framivra-issue68
- write_scope: ["scripts/ci_checks.py", "tests/fixtures/png_graph_d9ce4aa.json", "tests/test_composite_determinism.py", "tests/test_frame_source.py", "vtx/composite.py", "vtx/frame_source.py", "vtx/range.py", "vtx/subs.py"]
- repair_rounds_used: 2
- pending_approval: None
- checks: {"hosted_postmerge_ci": "All nine jobs PASS, exact merge SHA freshly verified", "historical_checks": "Prior checks retained under historical_checkpoint, not reassigned to final candidate"}
- independent_review: PASS exact integrated tree; closure verified freshly on GitHub
- evidence_relative: ["outputs/issue68-integration-e8d9ee7-20261016/manifest.json"]
- next_gate: Completed original issue; preserve bounded acceptance limits
- limits: Existing PNG internal seam only; no new backend, GPU, performance, universal visual equivalence or #46 closure claim
- published_branch: dot/png-frame-source-68-reviewed
- ordered_parents: ["b9e44b30d3acb4df476a7087104831e2b26fa411", "e8d9ee739f851bdf50acc4bc7f1567423d7dafb7"]
- merge: e928657f2833155423f6ccfca4869a1805b34bde
- prs: [157]
- postmerge_ci: 38877365335
- passed_jobs: 9
- postmerge_ci_url: https://github.com/CongliangK/framivra/actions/runs/38877365335
- postmerge_ci_status: completed_success
- closed_at_utc: 2026-10-16T09:11:29Z
- state_reason: completed
- closure_comment_url: https://github.com/CongliangK/framivra/issues/68#issuecomment-6212133080
- Prior checkpoint preserved verbatim under historical_checkpoint in companion JSON; older test claims are not reassigned to current SHA.

## Recovery and historical evidence

{"classification": "private owner recovery artifact; not public proof", "library_file_id": "libfile_60e26eebd7c4819195f5f2ac2e96fefd", "sha256": "89669712ff13ef9b6b4cc997e43fc5a754f191364ac6cfb2584466f70a6cd2d3", "bytes": 129650, "preserves_historical_heads": [100, 45, 18, 52], "delivered_at_utc": "2026-10-16T09:11:00Z", "does_not_prove": "current acceptance, public availability or newer integration state"}
Previous immutable snapshot: https://github.com/CongliangK/framivra/blob/743bcae2c08d4ba12633122ffea230a2f8643360/orchestration-facts.json
Relative evidence paths refer to temporary cloud project workspace; availability is not guaranteed. Private recovery archive is not public acceptance proof. Unknown/null values are not PASS.

## Publication gate

Only these two sanitized files are candidates. No remote writes performed. Fresh independent review and explicit publication authorization required; compare expected predecessor 743bcae2c08d4ba12633122ffea230a2f8643360; never merge metadata into main.
