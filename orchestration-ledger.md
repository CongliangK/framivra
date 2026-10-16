# Framivra orchestration ledger

Local draft, not published. Snapshot 2026-10-16T14:44:10Z. Retained-evidence reconciliation, not fresh remote polling. Fresh independent review required.
Repository: https://github.com/CongliangK/framivra. Metadata predecessor: e76af1024fc3cf9190968230f9615eea9df2d57f.

## Goal and verified retained state

Original goal: **6/20**, #44, #49, #53, #144, #68, #90. Last verified ordinary open count:46. New #153/#155 never increase original-goal completion. Aggregate PR count is not an issue count. Six validation-only PRs159–164 remain unmerged.
Main 1b39955b8d52a2e1efadfc898e9b056fbbc5d3f6; tree 36635f257a7c4dbb36049e578340bfa8ba155b83. PR165 merged; post-merge CI9/9 success. #18 and #155 remain OPEN: closure attempts denied and stopped14:24. No retry without new explicit user confirmation.

## Scope and decisions

Cloud non-CEF/Electron work in original scope; Coagentdraw paused. Preserve independent review, actual verification and serial PR integration. Security/access denials remain blocked. No source edits or remote actions performed for this ledger. Prior failed rounds and decisions are historical, not current pass claims.

## Active

### #155: Completion-marker publication race
- Issue / original DoD: https://github.com/CongliangK/framivra/issues/155
- state: merged_postmerge_ci_success_issue_open_closure_permission_denied
- owner_role: Workstream implementer; independent reviewer; integration maintainer
- environment: Temporary cloud Linux workspace
- branch: dot/license-segment-18-155-review
- base: 2bd870f63b7ba1f0a0d3ffa43f630ebdd50aaf0c
- local_candidate: bf13b8962cf15e0025ddcdc294a2fd5f84d498d9
- tree: 36635f257a7c4dbb36049e578340bfa8ba155b83
- parent: 2bd870f63b7ba1f0a0d3ffa43f630ebdd50aaf0c
- published_head: 41083fd418ea45fb240b8e28440008ff77323321
- published_tree: 36635f257a7c4dbb36049e578340bfa8ba155b83
- pr_url: https://github.com/CongliangK/framivra/pull/165
- ci_url: https://github.com/CongliangK/framivra/actions/runs/38913280077
- ci_status: completed_success
- worktree_relative: development/worktrees/framivra-license-segment-combined
- write_scope: ["tests/test_failure_recovery.py", "vtx/fstxn.py", "vtx/recovery.py"]
- repair_rounds_used: None
- pending_approval: New explicit user confirmation before any denied closure retry; stopped 14:24 UTC
- checks: {"premerge": "9/9 jobs run38908173602", "postmerge": "9/9 jobs run38913280077 exact merge; five Python aggregates each1606/42 optional skips; strict FFmpeg90/90, Chrome14/14, Windows24/24 plus installer/doctor; docs/static pass"}
- independent_review: Combined exact product review PASS; DoD map retained; issue closure separately blocked
- evidence_relative: ["outputs/issue18-155-publication-plan/publication-proof.json", "outputs/issue18-155-publication-plan/post-final.json", "outputs/issue18-155-publication-plan/closure-readback.json", "outputs/issue18-155-publication-plan/DOD-MAP.md"]
- next_gate: Do not retry closure absent new explicit user confirmation. Preserve original acceptance limits.
- limits: No legal clearance; unknown/conflicting rights remain blockers. Separate #45 runtime integration and native Windows locking coverage not established. Hosted skips remain skips.
- counts_toward_original_goal: False
- ordered_parents: ["2bd870f63b7ba1f0a0d3ffa43f630ebdd50aaf0c"]
- merge: 1b39955b8d52a2e1efadfc898e9b056fbbc5d3f6
- postmerge_ci_url: https://github.com/CongliangK/framivra/actions/runs/38913280077
- issue_state: open
- closed_at_utc: None
- closure_comment_url: https://github.com/CongliangK/framivra/issues/155#issuecomment-6217522105
- repair_count_detail: Prior counts retained historically; combined integration and extra repair recorded in DoD map, no guessed total
- original_dod_reference: https://github.com/CongliangK/framivra/issues/155
- dependencies: []
- published_source_url: https://github.com/CongliangK/framivra/tree/41083fd418ea45fb240b8e28440008ff77323321
- merge_url: https://github.com/CongliangK/framivra/commit/1b39955b8d52a2e1efadfc898e9b056fbbc5d3f6
- Earlier checkpoint and all nested failure history retained in companion JSON; older tests are not reassigned to this tree.

### #45: F12 cancellation and F13 adaptive concurrency
- Issue / original DoD: https://github.com/CongliangK/framivra/issues/45
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
- pending_approval: Ownership/lease architecture decision remains unresolved
- checks: {"author_focused": "136 run: 128 passed, 8 skipped; exit0 in 69.054s", "docs": "7/7 passed, exit0", "R4": "BLOCKING, reproduced unchanged: child alive, NVENC slot available, #44 used CPU=0 and active_leases=0. No budget schema or reclamation changes authorized or made.", "timing_gate": "Independent 3.187738266s miss of 3s gate retained as failure. Three observed current runs pass with load/affinity recorded, but do not explain historical failure or prove a hard bound. Threshold unchanged.", "aggregate": "Full final aggregate not run"}
- independent_review: BLOCKED / FAIL on exact repair2; formal report now present
- evidence_relative: ["outputs/issue45-runtime-repair2/manifest.json", "outputs/issue45-runtime-repair2-independent/review.md", "outputs/issue45-runtime-repair1-independent/review.md"]
- next_gate: Resolve owned-child/resource lease lifetime within original scope; fresh candidate, independent review, original timing/full-tree/#51/F13 acceptance
- limits: R4 remains: owned child alive while NVENC slot and #44 capacity are released. R5–R7 bounded corrections do not resolve ownership architecture. Full-tree cleanup, #51, F13, target backend and timing acceptance unmet; no publication.
- candidate_identity_scope: Exact final automatic repair2 candidate; independently BLOCKED / FAIL
- repair_round_active: None
- successor_candidate: None
- repair_count_detail: Two prior rounds retained; R4 unresolved. Historical round cap is not current status.
- original_dod_reference: https://github.com/CongliangK/framivra/issues/45
- dependencies: []
- Earlier checkpoint and all nested failure history retained in companion JSON; older tests are not reassigned to this tree.

### #52: Incremental impact analysis
- Issue / original DoD: https://github.com/CongliangK/framivra/issues/52
- state: frozen_candidate_failed_harness_review_successor_repair_active_unfrozen
- owner_role: Workstream implementer; independent reviewer; integration maintainer
- environment: Temporary cloud Linux workspace
- branch: cloud/issue52-current-html-20261016
- base: 1b39955b8d52a2e1efadfc898e9b056fbbc5d3f6
- local_candidate: 8e88d4edc0df1f5c974d426431f4b13acba1ad87
- tree: f40844061f9b45c43ecc6cee7d7e1edce3e923fb
- parent: fee158ce0e0825cafd5ecbacc436b6576cfb52b9
- published_head: None
- published_tree: None
- pr_url: None
- ci_url: None
- ci_status: not_run_for_candidate
- worktree_relative: development/worktrees/framivra-issue52-current-html
- write_scope: [".github/workflows/impact-html-acceptance.yml", "docs/cache-provenance.md", "tests/run_impact_gfx_reconciliation.py", "tests/run_impact_html_reconciliation.py", "tests/run_impact_reconciliation.py", "tests/test_frame_source.py", "tests/test_impact_html_harness.py", "tests/test_impact_identity.py", "tests/test_impact_repair.py", "vtx/acceptance.py", "vtx/cache_state.py", "vtx/cli.py", "vtx/composite.py", "vtx/hygiene.py", "vtx/impact.py", "vtx/preview.html", "vtx/stages.py", "vtx/timeline.schema.json", "vtx/timeline_diff.py"]
- repair_rounds_used: 1
- pending_approval: None
- checks: {"prior_author": "74 focused/2 opt-in skips;47 compatibility/3 skips; exact frozen candidate", "independent_focused": "120 tests,5 opt-in skips, no failures; earlier nonexistent-module invocation errors corrected", "independent_actual_media": "30 scenes64x64 cold/warm and four mutations ran; passed existing checks, whose F2 blind spots remain", "html_acceptance": "NOT RUN", "full_aggregate": "NOT RUN"}
- independent_review: FAIL acceptance-harness completeness on frozen 8e88d4e/tree f408440; not a demonstrated production renderer defect
- evidence_relative: ["outputs/issue52-current-html-integration/IDENTITY.json", "outputs/issue52-current-html-integration/REPORT.md", "outputs/issue52-current-html-integration/preservation.json", "outputs/issue52-independent-current-html-review/REPORT.md"]
- next_gate: Repair F1/F2 in isolated successor; freeze new head/tree, fresh independent review, then actual normal-sandbox hosted 1080p HTML acceptance and aggregate before original DoD/PR gates
- limits: Actual1080p HTML and aggregate unrun. The 64x64 companion does not establish HTML acceptance. F1/F2 undermine completeness of claimed mutation reconciliation. No production renderer defect inferred.
- integration_target: 1b39955b8d52a2e1efadfc898e9b056fbbc5d3f6
- candidate_identity_scope: Old8e88d4e/tree f408440 remains frozen and independently FAIL; successor is unfrozen
- ordered_parents: ["fee158ce0e0825cafd5ecbacc436b6576cfb52b9"]
- original_dod_reference: https://github.com/CongliangK/framivra/issues/52
- dependencies: ["Current main 1b39955b8d52a2e1efadfc898e9b056fbbc5d3f6; preserved #18/#155/#90/#68 implementations; original resolver contracts"]
- review_findings: {"F1": "Inventory omits unrelated audio/seg additions and can miss new PNGs in retired gfx directories", "F2": "Media companion misses product removals and same-size restored-mtime corruption"}
- successor_candidate: {"state": "active_unfrozen", "worktree_relative": "development/worktrees/framivra-issue52-html-repair1", "branch": "cloud/issue52-html-repair1-20261016", "base": "1b39955b8d52a2e1efadfc898e9b056fbbc5d3f6", "head": null, "tree": null, "starting_checkpoint": "953e85431a7c445d785fd0b619fd34338143aadb", "starting_tree": "f40844061f9b45c43ecc6cee7d7e1edce3e923fb", "note": "Dirty working source not inspected or represented as a frozen candidate; old8e88d4e preserved"}
- resource_limits: Two-worker CPU claim unsupported: VTX_VIDEO_WORKERS is not consumed by production code. Python3000s checks occur around subprocesses, not all inventory/decode work. Hard workflow ceiling65min, media step8min; disk/RSS are observations, not quotas or process-tree caps.
- Earlier checkpoint and all nested failure history retained in companion JSON; older tests are not reassigned to this tree.

### #100: Structured agent diagnostics
- Issue / original DoD: https://github.com/CongliangK/framivra/issues/100
- state: precision_coverage_implementation_unfrozen
- owner_role: Workstream implementer; independent reviewer; integration maintainer
- environment: Temporary cloud Linux workspace
- branch: dot/issue100-precise-operations
- base: 1b39955b8d52a2e1efadfc898e9b056fbbc5d3f6
- local_candidate: None
- tree: None
- parent: None
- published_head: None
- published_tree: None
- pr_url: None
- ci_url: None
- ci_status: not_run_for_candidate
- worktree_relative: development/worktrees/framivra-agent-diagnostics-100-precise
- write_scope: ["docs/agent-diagnostics.md", "plugins/bgm-render/__init__.py", "plugins/blender-bridge/__init__.py", "plugins/duck-static/__init__.py", "scripts/ci_checks.py", "tests/fixtures/agent-diagnostics/timeline.invalid.json", "tests/test_agent_diagnostics.py", "tests/test_agent_diagnostics_attribution.py", "tests/test_agent_diagnostics_boundaries.py", "tests/test_agent_diagnostics_loading.py", "tests/test_agent_diagnostics_plugins.py", "tests/test_agent_diagnostics_qc.py", "tests/test_agent_diagnostics_repair.py", "vtx/agent_diagnostics.py", "vtx/diagnostics.py", "vtx/infra.py", "vtx/pluginlib/hooks.py", "vtx/qc.py", "vtx/stages.py", "vtx/subtitles_src.py"]
- repair_rounds_used: None
- pending_approval: None
- checks: {"current": "Unfrozen edits; no exact final candidate test/review claim"}
- independent_review: New final candidate not frozen or reviewed
- evidence_relative: ["outputs/issue100-precise-coverage-plan/plan.md", "outputs/issue100-precise-coverage-plan/identity.json"]
- next_gate: Finish precise operation coverage; freeze exact tree, independently review and actually verify original DoD
- limits: Prior causal/Unicode PASS is bounded to old trees; three operation-coverage hard gates remain.
- repair_count_detail: Historical rounds retained; current precision-coverage work not frozen
- ordered_parents: []
- original_dod_reference: https://github.com/CongliangK/framivra/issues/100
- dependencies: []
- Earlier checkpoint and all nested failure history retained in companion JSON; older tests are not reassigned to this tree.

### #18: Distribution model-license inventory
- Issue / original DoD: https://github.com/CongliangK/framivra/issues/18
- published_head: 41083fd418ea45fb240b8e28440008ff77323321
- published_tree: 36635f257a7c4dbb36049e578340bfa8ba155b83
- pr_url: https://github.com/CongliangK/framivra/pull/165
- ci_url: https://github.com/CongliangK/framivra/actions/runs/38913280077
- state: merged_postmerge_ci_success_issue_open_closure_permission_denied
- owner_role: Workstream implementer; independent reviewer
- environment: Temporary cloud Linux workspace
- branch: dot/license-segment-18-155-review
- worktree_relative: development/worktrees/framivra-license-segment-combined
- repair_rounds_used: None
- repair_count_detail: Prior counts retained historically; combined integration and extra repair recorded in DoD map, no guessed total
- pending_approval: New explicit user confirmation before any denied closure retry; stopped 14:24 UTC
- checks: {"premerge": "9/9 jobs run38908173602", "postmerge": "9/9 jobs run38913280077 exact merge; five Python aggregates each1606/42 optional skips; strict FFmpeg90/90, Chrome14/14, Windows24/24 plus installer/doctor; docs/static pass"}
- independent_review: Combined exact product review PASS; DoD map retained; issue closure separately blocked
- next_gate: Do not retry closure absent new explicit user confirmation. Preserve original acceptance limits.
- limits: No legal clearance; unknown/conflicting rights remain blockers. Separate #45 runtime integration and native Windows locking coverage not established. Hosted skips remain skips.
- ci_status: completed_success
- evidence_relative: ["outputs/issue18-155-publication-plan/publication-proof.json", "outputs/issue18-155-publication-plan/post-final.json", "outputs/issue18-155-publication-plan/closure-readback.json", "outputs/issue18-155-publication-plan/DOD-MAP.md"]
- local_candidate: bf13b8962cf15e0025ddcdc294a2fd5f84d498d9
- base: 2bd870f63b7ba1f0a0d3ffa43f630ebdd50aaf0c
- tree: 36635f257a7c4dbb36049e578340bfa8ba155b83
- parent: 2bd870f63b7ba1f0a0d3ffa43f630ebdd50aaf0c
- ordered_parents: ["2bd870f63b7ba1f0a0d3ffa43f630ebdd50aaf0c"]
- write_scope: ["docs/model-license-inventory.json", "docs/third-party-licenses.md", "scripts/distribution_license_audit.py", "scripts/model_license_policy.py", "tests/test_distribution_license_audit.py", "tests/test_model_licenses.py"]
- merge: 1b39955b8d52a2e1efadfc898e9b056fbbc5d3f6
- postmerge_ci_url: https://github.com/CongliangK/framivra/actions/runs/38913280077
- issue_state: open
- closed_at_utc: None
- closure_comment_url: https://github.com/CongliangK/framivra/issues/18#issuecomment-6217519916
- original_dod_reference: https://github.com/CongliangK/framivra/issues/18
- dependencies: []
- published_source_url: https://github.com/CongliangK/framivra/tree/41083fd418ea45fb240b8e28440008ff77323321
- merge_url: https://github.com/CongliangK/framivra/commit/1b39955b8d52a2e1efadfc898e9b056fbbc5d3f6
- Earlier checkpoint and all nested failure history retained in companion JSON; older tests are not reassigned to this tree.

### #87: CI feedback and coverage partition
- Issue / original DoD: https://github.com/CongliangK/framivra/issues/87
- published_head: 93da8899bc82538cb9f7333a67d8c920f2d96f16
- published_tree: 9972e499e9dcbca9bf041a8174695bc0efa0f8f3
- pr_url: None
- ci_url: None
- branch: cloud/issue87-ci-feedback-repair3-20261016
- state: old_base_product_four_hosted_fixtures_pass_unmerged
- owner_role: Workstream implementer; independent reviewer; objective verifier
- environment: Temporary cloud Linux workspace
- worktree_relative: development/worktrees/framivra-ci-feedback-87-repair3
- repair_rounds_used: 3
- ci_status: four_validation_fixtures_success_not_production_main
- pending_approval: None
- checks: {"hosted": "Four fixture runs PASS, ten original jobs each; one selected Core3.11 warm rerun executed; wrapper IDs are not executions", "warm": "100 verified current APT archives; 0 B/155 MB archive download; pip cached-wheel use", "performance": "Zero speedup claim; warm selected job298s versus prior249s; no P50/P95 or causal savings"}
- independent_review: Bounded repair3 PASS on exact old-base product; not current-main integration review
- next_gate: Rebase/integrate serially on new main and reverify; resolve policy evidence and original main/schedule/manual/performance acceptance. Do not merge validation PRs or retry denied protection access.
- limits: Old-base fixture evidence only. No main production merge, issue closure, required-context proof or reliable <=4.5m claim.
- required_context_verification: {"state": "unverified_access_denied", "url": "https://api.github.com/repos/CongliangK/framivra/branches/main/protection", "http_status": 403, "error": "Resource not accessible by integration", "changes_made": false}
- evidence_relative: ["outputs/issue87-repair3/manifest.json", "outputs/issue87-repair3-independent-review/report.md", "outputs/issue87-warm-job/report.md", "outputs/issue87-warm-job/final-census.json"]
- local_candidate: 96b9613b72dd0b84cbc1c87f94851bb4d57d514c
- base: e928657f2833155423f6ccfca4869a1805b34bde
- tree: 9972e499e9dcbca9bf041a8174695bc0efa0f8f3
- parent: 81b9556adfe9ed86565e91fe531f4c4d9f3b084e
- ordered_parents: ["81b9556adfe9ed86565e91fe531f4c4d9f3b084e"]
- write_scope: [".github/workflows/ci.yml", "scripts/ci_apt_cache.py", "scripts/run_all_tests.sh", "tests/ci_partition.py", "tests/test_ci_apt_cache.py", "tests/test_ci_checks.py", "tests/test_ci_feedback.py"]
- hosted_runs: [{"url": "https://github.com/CongliangK/framivra/actions/runs/38899676429", "head": "5971b792ecf48fc16181a09f9595f93371ce53a3", "attempt": 2, "conclusion": "success"}, {"url": "https://github.com/CongliangK/framivra/actions/runs/38898678571", "head": "883ef9354698b9b4a1d6f08441c3e4b914cee89f", "attempt": 1, "conclusion": "success"}, {"url": "https://github.com/CongliangK/framivra/actions/runs/38897668067", "head": "7b233b97557f6233e7bfb25c5a947675cda61e27", "attempt": 1, "conclusion": "success"}, {"url": "https://github.com/CongliangK/framivra/actions/runs/38897111177", "head": "d2cbdf0d786ff02f86e1672fe789129a4f461002", "attempt": 1, "conclusion": "success"}]
- validation_prs: ["https://github.com/CongliangK/framivra/pull/159", "https://github.com/CongliangK/framivra/pull/160", "https://github.com/CongliangK/framivra/pull/161", "https://github.com/CongliangK/framivra/pull/162", "https://github.com/CongliangK/framivra/pull/163", "https://github.com/CongliangK/framivra/pull/164"]
- original_dod_reference: https://github.com/CongliangK/framivra/issues/87
- dependencies: []
- published_source_url: https://github.com/CongliangK/framivra/tree/93da8899bc82538cb9f7333a67d8c920f2d96f16
- Earlier checkpoint and all nested failure history retained in companion JSON; older tests are not reassigned to this tree.

### #50: Structured execution events and artifact links
- Issue / original DoD: https://github.com/CongliangK/framivra/issues/50
- owner_role: Workstream implementer; independent reviewer; integration maintainer
- environment: Temporary cloud Linux workspace
- state: frozen_repair2_bounded_independent_pass_actual_media_verification_running
- base: 1b39955b8d52a2e1efadfc898e9b056fbbc5d3f6
- local_candidate: f8d0d864807a8eef7880b7f388cd1d5d75f2d7e6
- tree: c83b54139d3e94be882cd83bdd4a49b9a24f3bca
- parent: 1b39955b8d52a2e1efadfc898e9b056fbbc5d3f6
- branch: dot/structured-events-50-repair2
- worktree_relative: development/worktrees/framivra-structured-events-50-repair2
- write_scope: ["docs/execution-events.md", "tests/test_execution_event_contract.py", "tests/test_execution_events.py", "tests/test_execution_invariants.py", "tests/test_execution_links.py", "vtx/acceptance.py", "vtx/cli.py", "vtx/composite.py", "vtx/encoders.py", "vtx/events.py", "vtx/execution_links.py", "vtx/infra.py", "vtx/qc.py", "vtx/stages.py"]
- repair_rounds_used: 2
- published_head: None
- published_tree: None
- pr_url: None
- ci_url: None
- ci_status: not_run
- pending_approval: None
- checks: {"focused": "83 passed", "regression": "79 run:78 passed,1 opt-in Chrome skip", "docs": "7 gates pass", "new_actual_media": "Verification started on exact f8d0d864; pending, no result claim", "independent_focused": "83 passed", "independent_regression": "129 passed,1 opt-in skip"}
- independent_review: Final bounded source/focused synthetic/compatibility PASS received14:43; whole original DoD INSUFFICIENT EVIDENCE
- next_gate: Finish exact-tree real CPU media/cache/hash/human-output verification and remaining original aggregate/acceptance gates; PR/integration gates remain
- limits: Bounded independent PASS does not establish actual media, full aggregate, real Chrome/GPU or original whole-DoD completion.
- evidence_relative: ["outputs/issue50-repair2/BINDING.json", "outputs/issue50-repair2/REPORT.md", "outputs/issue50-repair1-independent-review/REPORT.md", "outputs/issue50-repair2-independent-review/REPORT.md"]
- original_dod_reference: https://github.com/CongliangK/framivra/issues/50
- dependencies: ["Current main 1b39955b8d52a2e1efadfc898e9b056fbbc5d3f6; preserve original resource-null/event identity contracts; historical repair1 FAIL retained"]
- Earlier checkpoint and all nested failure history retained in companion JSON; older tests are not reassigned to this tree.

## Completed original goal

### #44: Issue 44
- Issue / original DoD: https://github.com/CongliangK/framivra/issues/44
- prs: [148, 151]
- merge: 1cecc40dba44eac912c39a35e8c4fff7066f700e
- postmerge_ci: 38855356287
- passed_jobs: 9
- original_dod_reference: https://github.com/CongliangK/framivra/issues/44
- dependencies: []
- merge_url: https://github.com/CongliangK/framivra/commit/1cecc40dba44eac912c39a35e8c4fff7066f700e
- Earlier checkpoint and all nested failure history retained in companion JSON; older tests are not reassigned to this tree.

### #49: Issue 49
- Issue / original DoD: https://github.com/CongliangK/framivra/issues/49
- prs: [148, 152]
- merge: d17d07e955153dddb0e20596d6f29e393bc38701
- postmerge_ci: 38858297056
- passed_jobs: 9
- original_dod_reference: https://github.com/CongliangK/framivra/issues/49
- dependencies: []
- merge_url: https://github.com/CongliangK/framivra/commit/d17d07e955153dddb0e20596d6f29e393bc38701
- Earlier checkpoint and all nested failure history retained in companion JSON; older tests are not reassigned to this tree.

### #53: Issue 53
- Issue / original DoD: https://github.com/CongliangK/framivra/issues/53
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
- original_dod_reference: https://github.com/CongliangK/framivra/issues/53
- dependencies: []
- published_source_url: https://github.com/CongliangK/framivra/tree/c353fae63866fa986f73460ab541d683d89bbcc5
- merge_url: https://github.com/CongliangK/framivra/commit/d9ce4aab52fec57b770f689550fbbde1a860243b
- Earlier checkpoint and all nested failure history retained in companion JSON; older tests are not reassigned to this tree.

### #144: Template layout migration
- Issue / original DoD: https://github.com/CongliangK/framivra/issues/144
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
- original_dod_reference: https://github.com/CongliangK/framivra/issues/144
- dependencies: []
- published_source_url: https://github.com/CongliangK/framivra/tree/1cb21b6874f721d15dd53c143079a152cca20d9d
- merge_url: https://github.com/CongliangK/framivra/commit/e8d9ee739f851bdf50acc4bc7f1567423d7dafb7
- Earlier checkpoint and all nested failure history retained in companion JSON; older tests are not reassigned to this tree.

### #68: PNG FrameSource consumer boundary
- Issue / original DoD: https://github.com/CongliangK/framivra/issues/68
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
- original_dod_reference: https://github.com/CongliangK/framivra/issues/68
- dependencies: []
- published_source_url: https://github.com/CongliangK/framivra/tree/52fa6809e78e698f313f2f1883d6c10e57eb20d8
- merge_url: https://github.com/CongliangK/framivra/commit/e928657f2833155423f6ccfca4869a1805b34bde
- Earlier checkpoint and all nested failure history retained in companion JSON; older tests are not reassigned to this tree.

### #90: Scoped quality and evidence labels
- Issue / original DoD: https://github.com/CongliangK/framivra/issues/90
- state: merged_postmerge_ci_success_issue_closed_completed
- owner_role: Workstream implementer; independent reviewer; integration maintainer
- environment: Temporary cloud Linux workspace
- branch: dot/quality-contract-90-review
- base: e928657f2833155423f6ccfca4869a1805b34bde
- local_candidate: None
- tree: 5866c19be108c356ac5b3e4ef5560fb37c9a8c2b
- parent: None
- published_head: 4c49cc25bf77fb3de11bb33da3f15e423c84a4bb
- published_tree: 5866c19be108c356ac5b3e4ef5560fb37c9a8c2b
- pr_url: https://github.com/CongliangK/framivra/pull/156
- ci_url: https://github.com/CongliangK/framivra/actions/runs/38905171203
- ci_status: completed_success
- worktree_relative: development/worktrees/framivra-pr156-main-integration
- write_scope: ["docs/cache-provenance.md", "scripts/gfx_evidence.py", "scripts/gfx_golden.py", "tests/test_gfx_evidence.py", "tests/test_gfx_golden.py", "tests/test_quality.py", "vtx/infra.py", "vtx/qc.py", "vtx/quality.py"]
- repair_rounds_used: 1
- pending_approval: None
- checks: {"postmerge": "Nine jobs success on exact 2bd870f; retained raw logs and DoD evidence map"}
- independent_review: Approved-main integration independent review PASS; bounded original issue closed after post-merge CI
- evidence_relative: ["outputs/issue90-approved-publication/closure.json", "outputs/issue90-approved-publication/post-run-final.json", "outputs/issue90-approved-publication/dod-evidence-map.md"]
- next_gate: Completed original issue; preserve annotation/evidence limits
- limits: Annotation-only; Fast runtime; visual_content unknown; no final-movie Exact upgrade, authentic-producer, browser/GPU determinism or performance certification
- ci_jobs: {"success": 9, "failed": 0}
- contract_url: https://github.com/CongliangK/framivra/blob/4c49cc25bf77fb3de11bb33da3f15e423c84a4bb/docs/cache-provenance.md
- ordered_parents: ["e928657f2833155423f6ccfca4869a1805b34bde", "4c49cc25bf77fb3de11bb33da3f15e423c84a4bb"]
- merge: 2bd870f63b7ba1f0a0d3ffa43f630ebdd50aaf0c
- postmerge_ci: 38905171203
- postmerge_ci_url: https://github.com/CongliangK/framivra/actions/runs/38905171203
- closed_at_utc: 2026-10-16T13:21:03Z
- state_reason: completed
- closure_comment_url: https://github.com/CongliangK/framivra/issues/90#issuecomment-6216412223
- original_dod_reference: https://github.com/CongliangK/framivra/issues/90
- dependencies: []
- published_source_url: https://github.com/CongliangK/framivra/tree/4c49cc25bf77fb3de11bb33da3f15e423c84a4bb
- merge_url: https://github.com/CongliangK/framivra/commit/2bd870f63b7ba1f0a0d3ffa43f630ebdd50aaf0c
- Earlier checkpoint and all nested failure history retained in companion JSON; older tests are not reassigned to this tree.

## Conditional queue: 16 candidates, no closure guarantee

Independent review and retained-evidence verification completed; native attachment delivered. #18/#155 excluded. The current #52/#50 identities above supersede old dependency hashes in queue planning. #52 has since failed harness review; its successor and #100 remain unfrozen.

### Queue 1: #52
- Original DoD: https://github.com/CongliangK/framivra/issues/52
- State: Active repair after frozen acceptance-harness FAIL F1/F2; successor unfrozen; whole original DoD incomplete
- Dependencies: ["#52 a7e77ad5ee6bde0396e39cd35d684be114746874, reviewed against 2bd870f"]
- Write scope: ["vtx/impact.py", "vtx/cache_state.py", "vtx/hygiene.py", "vtx/timeline_diff.py", "vtx/composite.py", "vtx/cli.py", "vtx/stages.py", "vtx/timeline.schema.json", "vtx/preview.html", "tests/run_impact_gfx_reconciliation.py", "tests/run_impact_reconciliation.py"]
- Owner/environment: Workstream implementer; independent reviewer; integration maintainer; Cloud, excluding local CEF/Electron
- Acceptance: python -m unittest tests.test_impact_identity tests.test_impact_repair tests.test_timeline_diff tests.test_cache_state -v; existing reconciliation scripts per retained commands.json; VTX_TEST_CHROME=1 VTX_TEST_FFMPEG=1 bash scripts/run_all_tests.sh; bash scripts/doc_gates.sh
- Original DoD assessment: All original analysis DoD is cloud-achievable; proposed full-size HTML gate needs hosted runner and final-tree fresh review.
- Next gate: Integrate existing reviewed candidate serially onto current main; preserve new marker fix. Finish 30-page 1920x1080 real local HTML producer on hosted normal-sandbox Chrome, not pre-generated PNGs; local/global restored-mtime dependency changes must match predicted exact gfx/vseg/vseg_qc/vseg_pass path sets with no unrelated rewrites. CPU reconciliation already passes but full aggregate and HTML remain unrun.

### Queue 2: #50
- Original DoD: https://github.com/CongliangK/framivra/issues/50
- State: Bounded independent PASS on frozen repair2; actual media verification running; whole DoD unverified
- Dependencies: ["#50 6b3617903a4640ab9bb41e7def6e5fdf3aa94e42 fresh FAIL"]
- Write scope: ["vtx/events.py", "vtx/qc.py", "tests/test_execution_event_contract.py", "tests/test_execution_events.py", "tests/test_execution_links.py"]
- Owner/environment: Workstream implementer; independent reviewer; integration maintainer; Cloud, excluding local CEF/Electron
- Acceptance: python -m unittest tests.test_execution_event_contract tests.test_execution_events tests.test_execution_links -v; rerun outputs/issue50-repair1-independent-review/adversarial.py and edge_checks.py against new candidate; bash scripts/run_all_tests.sh; bash scripts/doc_gates.sh
- Original DoD assessment: All original DoD is cloud-achievable. No need for actual GPU or #45 scheduler implementation; do not claim real cancellation from observed synthetic events.
- Next gate: Repair semantic attempt/lifetime contradictions and propagate observation context through actual QC nested executor workers. Existing 77-test PASS is insufficient. Fresh adversarial package tests must reject attempt 99/999, overlapping retries within one task, children preceding parents; independent tasks may overlap. Then new-tree CPU media parity, truncation/resource-null/error/cancel/retry/cache tests.

### Queue 3: #100
- Original DoD: https://github.com/CongliangK/framivra/issues/100
- State: Active candidate tracked separately
- Dependencies: []
- Write scope: "Repair producer-owned precise diagnostics across the existing operation inventory; preserve exception identity, evaluation order, human output and exit codes. Integrate 0166dfa after rebasing onto fresh main, not its old base."
- Owner/environment: Workstream implementer; independent reviewer; integration maintainer; Cloud, excluding local CEF/Electron
- Acceptance: Run every catalog disposition and fatal-operation negative; real seven-class repair fixture with FFmpeg and zero skips; relevant full batches, audit/QC, independent exact-commit red team, PR CI and post-merge readback.
- Original DoD assessment: Existing precision audit is FAIL; earlier causality success does not satisfy complete hard-gate coverage. Coordinate any cache_state diagnostic annotation with #85 without changing identity semantics.
- Next gate: Existing precision audit is FAIL; earlier causality success does not satisfy complete hard-gate coverage. Coordinate any cache_state diagnostic annotation with #85 without changing identity semantics.

### Queue 4: #47
- Original DoD: https://github.com/CongliangK/framivra/issues/47
- State: Conditional not launched; original DoD unchanged
- Dependencies: ["Integrate #52 resolver/clip-dependency implementation first; coordinate #85 and #48"]
- Write scope: ["vtx/cache_state.py", "vtx/hygiene.py", "vtx/impact.py", "vtx/timeline_diff.py", "vtx/stages.py", "vtx/timeline.schema.json", "vtx/gfx_render.py", "vtx/gfx_cli.py", "vtx/cli.py", "tests/test_cache_state.py", "tests/test_gfx_cli_fallback.py"]
- Owner/environment: Workstream implementer; independent reviewer; integration maintainer; Cloud, excluding local CEF/Electron
- Acceptance: python -m unittest tests.test_cache_state tests.test_timeline_diff tests.test_gfx_golden tests.test_gfx_cli_fallback -v; new strict fixture suite; hosted Chrome real nested CSS/font/image requests, dynamic fetch and forbidden remote negatives, same-byte HTML different directories, A-only/B-warm/global invalidation and mtime-restored tests; aggregate/doc_gates
- Original DoD assessment: All original DoD appears cloud-achievable with normal-sandbox hosted Chrome. Unknown dynamic closure must fail closed or explicitly remain unproved, never silently certify.
- Next gate: Complete single resolver across keys/receipts/gc/diff, retain project union page declarations and legacy identity where contracted, bind HTML resolution path. Strict mode must reject unproved dynamic/remote resources, including fallback; static scan and observed CDP requests alone cannot certify arbitrary future paths. Conservative strict rejection is an allowed engineering design, not a scope trim.

### Queue 5: #48
- Original DoD: https://github.com/CongliangK/framivra/issues/48
- State: Conditional not launched; original DoD unchanged
- Dependencies: ["#47 shared resolver; integrate #52 first; #62 identity consumer"]
- Write scope: ["vtx/hygiene.py", "vtx/cache_state.py", "vtx/gfx_render.py", "vtx/gfx_cli.py", "vtx/gfx_evidence.py", "scripts/gfx_golden.py", "tests/test_gfx_golden.py", "tests/test_gfx_evidence.py"]
- Owner/environment: Workstream implementer; independent reviewer; integration maintainer; Cloud, excluding local CEF/Electron
- Acceptance: python -m unittest tests.test_cache_state tests.test_gfx_golden tests.test_gfx_evidence -v; hosted normal-sandbox Chrome A/B and distinct font-file fixtures; verify actual browser/font changes invalidate, unknown never aliases, compression-neutral changes not overclaimed; aggregate/doc_gates
- Original DoD assessment: Full closure feasible in hosted cloud if two actual browser/font identities are available through approved standard runner setup. Browser version lookup alone is not the real-render gate.
- Next gate: Collect executable/version, font-file content digest, effective rendering flags/backend/viewport/scale/color/alpha/schema identity; unknown fail-closed or explicit low-trust non-reuse. Wire keys, receipts and strict Golden independent candidate roots. Make old-cache migration conservative. Compression-only changes need not invalidate if proven pixel-neutral.

### Queue 6: #63
- Original DoD: https://github.com/CongliangK/framivra/issues/63
- State: Conditional not launched; original DoD unchanged
- Dependencies: [62, 50]
- Write scope: "Add the single versioned lifecycle-event producer with run/key/page/block/frame/backend identity; keep legacy statistics. Cover startup, tabs, setup, retries, healing, copies and stop; record composite CDP RPC honestly. Build safe isolated benchmark wrapper."
- Owner/environment: Workstream implementer; independent reviewer; integration maintainer; Cloud, excluding local CEF/Electron
- Acceptance: At least three paired same-target cold runs on hosted real Chrome, pre-frozen #62 plan/producer provenance, decoded full-frame zero differences, complete wall/critical path/tree RSS, trace overhead, real strict Chrome/pipeline suites. GPU fields null with reason are allowed here.
- Original DoD assessment: Current render_page starts ms_total after Browser.start and computes dt before deferred copies; Tab.call drops events; CLI merges weighted approximations. Need trace-capable event/stream handling if trace option offered. No historical production speed claim.
- Next gate: Current render_page starts ms_total after Browser.start and computes dt before deferred copies; Tab.call drops events; CLI merges weighted approximations. Need trace-capable event/stream handling if trace option offered. No historical production speed claim.

### Queue 7: #64
- Original DoD: https://github.com/CongliangK/framivra/issues/64
- State: Conditional not launched; original DoD unchanged
- Dependencies: [62, 63, 48]
- Write scope: "Opt-in optimizeForSpeed propagated into every CDP screenshot/probe/twoshot; capability detection only downgrades explicit unsupported parameter, never real screenshot errors; Canvas unaffected and default argv/params unchanged."
- Owner/environment: Workstream implementer; independent reviewer; integration maintainer; Cloud, excluding local CEF/Electron
- Acceptance: Three isolated paired cold A/B rounds, opaque/override/twoshot/flatten/Canvas/CLI fallback, low/high entropy pages, all decoded RGBA identical; PNG bytes/size, FFmpeg tail decode, total wall and resource budget; unsupported/no-benefit result may validly close experiment when actually measured.
- Original DoD assessment: Do not use cache hits as speed results; bind candidate environment/config to #48. Full #63 events and #62 producer integration first.
- Next gate: Do not use cache hits as speed results; bind candidate environment/config to #48. Full #63 events and #62 producer integration first.

### Queue 8: #67
- Original DoD: https://github.com/CongliangK/framivra/issues/67
- State: Conditional not launched; original DoD unchanged
- Dependencies: [62, 63, 48]
- Write scope: "Pin official chrome-headless-shell and matching protocol; first actually probe controlled target+BeginFrame. Then isolated runner with explicit frame/run/generation binding and resource readiness; do not touch production NAVFREE."
- Owner/environment: Workstream implementer; independent reviewer; integration maintainer; Cloud, excluding local CEF/Electron
- Acceptance: If binary truly rejects required protocol, retained minimal reproduction and negative conclusion is legitimate research closure. If supported, fonts/images/SVG/blur/gradient/alpha long sequences, seeded repeats/seeks, eight-instance load, independent decoded full-frame reference and multi-round cold/full wave costs.
- Original DoD assessment: Missing download/runtime access is blocked, not negative research. Ordinary Chrome protocol schema is not proof. Comment requests baseline jitter evidence first; preserve no production activation.
- Next gate: Missing download/runtime access is blocked, not negative research. Ordinary Chrome protocol schema is not proof. Comment requests baseline jitter evidence first; preserve no production activation.

### Queue 9: #66
- Original DoD: https://github.com/CongliangK/framivra/issues/66
- State: Conditional not launched; original DoD unchanged
- Dependencies: [62, 63, 45, 48]
- Write scope: "Bounded command-lifetime whole-Browser lease pool, compatible configuration groups, fresh isolated targets per page, explicit stop ownership, bounded healing, atomic generation+pool publication."
- Owner/environment: Workstream implementer; independent reviewer; integration maintainer; Cloud, excluding local CEF/Electron
- Acceptance: Mock saturation/partial startup/lease timeouts/concurrent restarts/cache-zero-start. Real transparent→opaque→Canvas→flatten→transparent, repeated pages/blocks, kill one worker without affecting others, full RGBA identity and zero process/fd/profile residue. Three paired rounds wall/start count/RSS/hit benefit.
- Original DoD assessment: BLOCKED until #45 shared ownership/budget lifecycle contract is established. Highest concurrency/isolation risk in shortlist. Current heal publishes new pool outside lock and process lifetime is render_page. Do not substitute many tabs for independent workers. No default activation without reproducible benefit.
- Next gate: BLOCKED until #45 shared ownership/budget lifecycle contract is established. Highest concurrency/isolation risk in shortlist. Current heal publishes new pool outside lock and process lifetime is render_page. Do not substitute many tabs for independent workers. No default activation without reproducible benefit.

### Queue 10: #85
- Original DoD: https://github.com/CongliangK/framivra/issues/85
- State: Conditional not launched; original DoD unchanged
- Dependencies: [97]
- Write scope: "Formal Verified identity and explicit trust model shared by receipt, seg/audio/loudness and QC. Prefer full verified reads at declared trust boundaries plus within-run reuse, or genuinely controlled immutable ingestion; never stat-only sidecars."
- Owner/environment: Workstream implementer; independent reviewer; integration maintainer; Cloud, excluding local CEF/Electron
- Acceptance: Same-size restored-mtime middle mutation must invalidate all keys, receipts and QC exemptions. Actual >=1GB media project cold identity overhead <=max(60s,10% total cold time), repeated warm behavior and identity failure negatives; full CPU pipeline and independent audit.
- Original DoD assessment: No >=950MB file found by read-only metadata search in this workspace/shared. Need real licensed media project from an already authorized source; padded random/synthetic bytes are not proof of actual-project gate. Immutable trust model must not require security permission changes.
- Next gate: No >=950MB file found by read-only metadata search in this workspace/shared. Need real licensed media project from an already authorized source; padded random/synthetic bytes are not proof of actual-project gate. Immutable trust model must not require security permission changes.

### Queue 11: #87
- Original DoD: https://github.com/CongliangK/framivra/issues/87
- State: Conditional not launched; original DoD unchanged
- Dependencies: []
- Write scope: "Continue from independently reviewed 96b9613 product, instrument leaf test times, optimize only evidenced costs and preserve coverage; retain exact four path fixtures and warm-cache verification."
- Owner/environment: Workstream implementer; independent reviewer; integration maintainer; Cloud, excluding local CEF/Electron
- Acceptance: Freeze actionable-failure/required-green/all-complete definitions; matched cache-temperature runs with P50/P95/cost and <=4.5m designated goal, >=25% overall savings, weekly equivalence, every required status terminal.
- Original DoD assessment: Parent reports four 10/10 cases and 100 warm archives verified. Earlier timing report still shows HTML first fast 344s; do not substitute warm-cache pass for target. Required-slow policy NOT VERIFIED: an incidental ordinary branch summary exposed eight contexts, while the dedicated policy request returned 403 and was not bypassed. That summary is not authoritative complete current protection-policy evidence. A required aggregate depending on slow job is a possible code-only design but does not silently satisfy literal request that slow job itself be required; no security mutation authorized.
- Next gate: Parent reports four 10/10 cases and 100 warm archives verified. Earlier timing report still shows HTML first fast 344s; do not substitute warm-cache pass for target. Required-slow policy NOT VERIFIED: an incidental ordinary branch summary exposed eight contexts, while the dedicated policy request returned 403 and was not bypassed. That summary is not authoritative complete current protection-policy evidence. A required aggregate depending on slow job is a possible code-only design but does not silently satisfy literal request that slow job itself be required; no security mutation authorized.

### Queue 12: #141
- Original DoD: https://github.com/CongliangK/framivra/issues/141
- State: Conditional not launched; original DoD unchanged
- Dependencies: ["18 fixed official snapshot gate"]
- Write scope: ["apps/asr/isolation.py (proposed)", "apps/asr/client.py", "apps/asr/sidecar_server.py", "apps/asr/init.sh", "vtx/pluginlib/config.py", "config.example.json", "apps/asr/app.json", "apps/asr/README.md", "docs/catalog.md"]
- Owner/environment: Workstream implementer; independent reviewer; integration maintainer; Cloud, excluding local CEF/Electron
- Acceptance: ['env python3 -m unittest discover -s apps/asr/tests -t apps/asr -v', 'bash apps/asr/tests/test_init_offline.sh', 'bash scripts/run_all_tests.sh', 'bash scripts/doc_gates.sh']
- Original DoD assessment: ['No install/download/run performed during this read-only assessment.', 'Need bounded real CPU install→transcribe→timestamps→owned stop/restart evidence and measured RSS/time/disk.', 'Respect exact license acknowledgment and download opt-in; legacy ModelScope path stays blocked.', 'No genuine GPU co-residency test without separate authorization; unavailable GPU must fail closed.', 'Independent red team, fixes, full tests, PR merge and post-merge acceptance remain mandatory.']
- Next gate: ['No install/download/run performed during this read-only assessment.', 'Need bounded real CPU install→transcribe→timestamps→owned stop/restart evidence and measured RSS/time/disk.', 'Respect exact license acknowledgment and download opt-in; legacy ModelScope path stays blocked.', 'No genuine GPU co-residency test without separate authorization; unavailable GPU must fail closed.', 'Independent red team, fixes, full tests, PR merge and post-merge acceptance remain mandatory.']

### Queue 13: #142
- Original DoD: https://github.com/CongliangK/framivra/issues/142
- State: Conditional not launched; original DoD unchanged
- Dependencies: [141]
- Write scope: ["apps/asr/client.py", "apps/asr/sidecar_server.py", "apps/asr/asr_cli.py", "vt.py", "apps/asr/tests/test_client.py", "apps/asr/tests/test_cli_baseurl.py", "apps/asr/tests/test_isolation.py (proposed)"]
- Owner/environment: Workstream implementer; independent reviewer; integration maintainer; Cloud, excluding local CEF/Electron
- Acceptance: ['env python3 -m unittest discover -s apps/asr/tests -t apps/asr -v', 'bash apps/asr/tests/test_init_offline.sh', 'bash scripts/run_all_tests.sh', 'bash scripts/doc_gates.sh']
- Original DoD assessment: ['No install/download/run performed during this read-only assessment.', 'Need bounded real CPU install→transcribe→timestamps→owned stop/restart evidence and measured RSS/time/disk.', 'Respect exact license acknowledgment and download opt-in; legacy ModelScope path stays blocked.', 'No genuine GPU co-residency test without separate authorization; unavailable GPU must fail closed.', 'Independent red team, fixes, full tests, PR merge and post-merge acceptance remain mandatory.']
- Next gate: ['No install/download/run performed during this read-only assessment.', 'Need bounded real CPU install→transcribe→timestamps→owned stop/restart evidence and measured RSS/time/disk.', 'Respect exact license acknowledgment and download opt-in; legacy ModelScope path stays blocked.', 'No genuine GPU co-residency test without separate authorization; unavailable GPU must fail closed.', 'Independent red team, fixes, full tests, PR merge and post-merge acceptance remain mandatory.']

### Queue 14: #140
- Original DoD: https://github.com/CongliangK/framivra/issues/140
- State: Conditional not launched; original DoD unchanged
- Dependencies: [141, 142]
- Write scope: ["apps/asr/tests/", "scripts/run_all_tests.sh", "scripts/doc_gates.sh"]
- Owner/environment: Workstream implementer; independent reviewer; integration maintainer; Cloud, excluding local CEF/Electron
- Acceptance: ['env python3 -m unittest discover -s apps/asr/tests -t apps/asr -v', 'bash apps/asr/tests/test_init_offline.sh', 'bash scripts/run_all_tests.sh', 'bash scripts/doc_gates.sh']
- Original DoD assessment: ['No install/download/run performed during this read-only assessment.', 'Need bounded real CPU install→transcribe→timestamps→owned stop/restart evidence and measured RSS/time/disk.', 'Respect exact license acknowledgment and download opt-in; legacy ModelScope path stays blocked.', 'No genuine GPU co-residency test without separate authorization; unavailable GPU must fail closed.', 'Independent red team, fixes, full tests, PR merge and post-merge acceptance remain mandatory.']
- Next gate: ['No install/download/run performed during this read-only assessment.', 'Need bounded real CPU install→transcribe→timestamps→owned stop/restart evidence and measured RSS/time/disk.', 'Respect exact license acknowledgment and download opt-in; legacy ModelScope path stays blocked.', 'No genuine GPU co-residency test without separate authorization; unavailable GPU must fail closed.', 'Independent red team, fixes, full tests, PR merge and post-merge acceptance remain mandatory.']

### Queue 15: #51
- Original DoD: https://github.com/CongliangK/framivra/issues/51
- State: Conditional not launched; original DoD unchanged
- Dependencies: ["#45 shared F12 cancellation contract; candidate 91b3783269c3164d12bdb999cf4918440be9961a currently FAIL"]
- Write scope: ["vtx/api.py", "vtx/cli.py", "vtx/runtime.py", "new job-control module and tests"]
- Owner/environment: Workstream implementer; independent reviewer; integration maintainer; Cloud, excluding local CEF/Electron
- Acceptance: New API lifecycle suite plus tests.test_runtime_cancellation and real owned short FFmpeg fixture; replay first-failure bounded latency, idempotency, failure context/exit code and verified-output resume; aggregate/doc_gates
- Original DoD assessment: Conditionally feasible, not immediate. No independent third cancellation manager. If durable ownership capability cannot be established without permission changes, remain blocked.
- Next gate: Implement stable submit/status/cancel/resume consumer only after safe shared ownership and cancellation lifecycle; CLI consumes API. CPU/short-FFmpeg evidence can close this API scope without complete adaptive policy or DAG, but cannot paper over #45 failure to reclaim descendants or falsely release capacity.

### Queue 16: #46
- Original DoD: https://github.com/CongliangK/framivra/issues/46
- State: Conditional not launched; original DoD unchanged
- Dependencies: ["#89 PTS/CFR evidence; original #11 project/slice if generated heterogenous fixture fails to reproduce"]
- Write scope: ["tests/test_composite_determinism.py", "vtx/composite.py", "new diagnostic fixture/script"]
- Owner/environment: Workstream implementer; independent reviewer; integration maintainer; Cloud, excluding local CEF/Electron
- Acceptance: python -m unittest tests.test_composite_determinism -v; new independent cold-root repeated runs with ffmpeg decoded rawvideo/framehash and ffprobe/showinfo PTS logs; reconcile #89; aggregate/doc_gates
- Original DoD assessment: Research and harness feasible. Full original closure conditional on a stable original-equivalent reproducer; non-reproduction is not proof of fix. No request to access user-local original files tonight.
- Next gate: Generate heterogeneous H264/ProRes/PNG CPU inputs with dense layer boundaries, freeze seed/version/threads/cold roots, compare decoded frame hashes and PTS/framesync events. Existing test explicitly says synthetic PNG graph cannot reproduce original disease. Fix only demonstrated cause, no blind FFV1 passes.

## Four-repository audit and research

{
  "state": "Read-only report delivered 14:26 UTC; historical observation window13:49–13:50, superseded for PR165 by current merged evidence",
  "evidence_relative": [
    "outputs/four-repo-audit-20261016-1333/final/status-report.md",
    "outputs/four-repo-audit-20261016-1333/final/research-commissions.md"
  ],
  "new_research_questions": 2,
  "research_topics": [
    "#87 actionable feedback endpoint and matched protocol",
    "#89 per-frame identity observation bridge"
  ],
  "coagentdraw": "Paused",
  "router": "PAUSED / NOT READY; no training acceptance inferred",
  "ibus": "Zero open issues does not prove real desktop/device lifecycle acceptance"
}

## Recovery and publication

Previous published snapshot: https://github.com/CongliangK/framivra/blob/e76af1024fc3cf9190968230f9615eea9df2d57f/orchestration-ledger.md
Previous structured snapshot: https://github.com/CongliangK/framivra/blob/e76af1024fc3cf9190968230f9615eea9df2d57f/orchestration-facts.json
Retained predecessor proof: outputs/framivra-ledger-1048-publication/proof.json. This draft contains only orchestration-ledger.md and orchestration-facts.json. Fresh review, expected predecessor comparison and authorized publication gate remain; never merge the metadata branch into main.
No unknown gate is PASS. Local worktree availability is temporary; only published commit links are durable. Current draft was not published and no repository source was changed.

## Metadata predecessor review

The earlier local snapshot failed public-publication review. This successor removes private Library identifiers, corrects both queue #87 policy claims, and records #52 F1/F2 plus its unfrozen repair. The earlier frozen files remain unchanged. Fresh independent review is required.
