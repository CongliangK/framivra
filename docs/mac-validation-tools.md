# Frozen Mac validation tools

This Draft delivers reviewed validation tools for [PR226](https://github.com/CongliangK/framivra/pull/226). Source delivery and cloud tests do not establish actual Mac, production, performance or pixel-equivalence acceptance. This branch must not be merged as an acceptance result.

## Source and checkout identity

The delivery base is main commit `f1efe5f688b543ca11902c9538dec02a5c265961`. The current sources are [owner r12](../tools/testing/mac_validation/owned_launcher_r12/README.md), its [resource summary](../tools/testing/mac_validation/owned_launcher_r12/resource_summary.py), and the unchanged [bounded-page harness r1](../tools/testing/mac_validation/bounded_page_harness_r1/harness.py). Prior revisions remain recoverable in Git history rather than duplicate current-tree directories.

The separate production checkout must remain at PR226 commit `85fb352f9f95f9882222b2eacee2c887b520f495`, tree `7d499f03b734cb87c0b4286947b9fb60bd5d303c`. Keep validation tools outside that checkout; do not substitute this delivery checkout or a floating ref. The [source manifest](../tools/testing/mac_validation/SOURCE-MANIFEST.json) records delivered bytes, SHA-256, Git blob identities and the distinction between executed review bytes and the static portable test-binding adaptation.

## Mode, schema and control boundaries

The frozen [mode and schema contract](../tools/testing/mac_validation/owned_launcher_r12/MODE-AND-SCHEMA.txt) defines strict default behavior, explicit diagnostic opt-in, census membership, reported-RSS accounting, caps, guards, record fields and limits. Diagnostic observations are not live ownership. Unknown rows never gain signal, guard, ancestry or cleanup authority. Common PGIDs 0/1 are refused before group-wide seeding; only related triggering rows remain in PID/PPID closure. Positive retained-start or PGID contradictions remain fatal before descendant adoption in both modes.

The strict observation/recovery implementation, BudgetState, root-only signaling, cleanup functions, native ABI and existing budgets retain their reviewed code. The unchanged overall PASS and next-arm expressions are extracted into `finalize_status`; cleanup UNKNOWN continues to block the next arm. There is no scoped-absence exception. The retained-start guard and common-group refusal received static review only, with no new identity-reuse/security fixture executed.

Reported sampled RSS is neither an upper nor a lower bound on actual task or physical memory. Unknown attribution, short-lived processes, escaped processes, shared pages and between-sample peaks limit the evidence. A complete producer result can be reported separately; partial resource evidence does not establish a successful paired comparison, performance promotion or Mac readiness. Existing deadlines are cooperative, and the post-cycle wait is not a maximum sample-age guarantee.

## Bounded-page harness r1

The harness, its portable test source and its 19-ID list are unchanged. The harness provides thin `route-run` and `pages12` handling while preserving original C300/T10 semantics. Canonical validate precedes gfx under the existing owner; gfx requires successful validation, events, receipt and provenance. There is no new shell, process group, supervisor, retry or automatic next arm. The log destination must be a fresh child directory, not the owner evidence directory itself.

Both pages1/pages2 arms require an explicitly frozen common FAST value; only PAGES differs. Attempt overlap does not establish simultaneous screenshots or full-scene speedup. Per-page counts, complete RGBA comparison, resources, cleanup and actual stage approval remain separate gates.

## Exact qualification and exclusions

The inherited [68-ID list](../tools/testing/mac_validation/owned_launcher_r12/APPROVED-TEST-IDS.txt) is byte-identical. The separate [six diagnostic IDs](../tools/testing/mac_validation/owned_launcher_r12/DIAGNOSTIC-TEST-IDS.txt) contain 17 ordinary mocked fixtures. [The complete selected list](../tools/testing/mac_validation/owned_launcher_r12/APPROVED-ALL-TEST-IDS.txt) is exactly the original 68 followed by the new six, without discovery or extra IDs.

Frozen revision-2 source qualification: author attempt 2 passed 74/74, no failures/errors/skips, exit 0, unittest 0.624 seconds and measured wall 0.806155285 seconds. Independent attempt 1 passed the same exact 74/74, no failures/errors/skips, exit 0, unittest 0.559 seconds and measured wall 0.717009054 seconds. All 18 frozen source/test/list/document hashes stayed unchanged. These are original-source executions, not executions of this portable package.

The six [round-excluded methods](../tools/testing/mac_validation/owned_launcher_r12/ROUND-EXCLUDED-TEST-IDS.txt) and two [historical UNRUN methods](../tools/testing/mac_validation/owned_launcher_r12/UNRUN-TEST-IDS.txt) are preserved and unselected. They include two real-process exercises and four changed-contract mixed methods, plus two earlier identity/lineage exclusions. Their source assertions and historical evidence are unchanged. The previously proposed extra cleanup identity-mismatch fixture also remains unexecuted/static-only. No actual child process, Mac/native workload, new security/PID-reuse/path-attack fixture, network request or installation is part of the 74-ID qualification.

The [harness selection](../tools/testing/mac_validation/bounded_page_harness_r1/APPROVED-TEST-IDS.txt) retains 19 exact ordinary CPU/mock IDs. Its historical independent result was 19/19, exit 0, 0.709 seconds, without real validate/gfx or browsers. It was not rerun for r12. The harness author's earlier accidental-discovery attempt remains excluded from exact-ID evidence. No whole-class/module run or discovery is permitted in place of exact lists.

## Portable binding and generated evidence

Test binding requires verified nonempty absolute paths:
- `FRAMIVRA_VALIDATION_REPO`: the separate frozen production checkout, for both suites.
- `FRAMIVRA_VALIDATION_BOUND`: the genuine frozen bound-input project, for harness tests.
- `FRAMIVRA_VALIDATION_INVENTORY`: the genuine frozen 114-item inventory file, for harness tests.
- `FRAMIVRA_VALIDATION_PLAN`: the genuine frozen plan file, for harness tests.

Missing variables fail directly. Do not substitute defaults, relative paths, another checkout, stand-ins, fabricated files/hashes or weakened assertions. Verify commit/tree, tracked cleanliness and actual imported production-module paths. Missing genuine inputs block the relevant suite.

Only the owner's repository assignment uses the same established single-line environment adaptation. The harness's explicit environment bindings are unchanged. All test methods, assertions, expected identities and selections retain their reviewed bytes. Runtime and summary match independent review exactly. Packaging review is static only; no packaging tests are run.

Tests are not side-effect-free. `test_independent_r5.py` writes `ordinary-reparent.actual.json`, and `test_independent_fresh_child.py` writes `fresh-visible-child.actual.json`, beside their own modules through `Path(__file__).parent`. Changing cwd does not redirect them. Preserve confirmed diagnostic outputs with hashes and provenance; do not erase them or rerun tests to disguise a dirty checkout. Generated outputs and attempt evidence are not delivery source.

## Actual evidence and remaining gates

Actual r12 diagnostic-mode Mac qualification is UNRUN. Historical r11 failure/incomplete evidence remains unchanged; the earlier r10 reverse comparison remains FAILED with resource INCOMPLETE and cleanup UNKNOWN. Source/mock success does not repair missing intervals, establish counterfactual success or complete a failed comparison. Original Mac raw bytes were not independently read by this packaging stage.

Separate gates remain: actual tool/production/input identities, explicit diagnostic run spec, frozen producer configuration and budgets, genuine input binding, complete pixel comparison, resource evidence and cleanup. Missing evidence stays UNKNOWN and blocks advancement. Tool delivery does not authorize Mac execution or a next stage.

The unchanged [ordinary Mac ABI fixture and contract](../tools/testing/mac_validation/mac_zombie_abi_r1/README.md) retain their source-only static review and are not rerun for r12. A historical ordinary-zombie ABI PASS was reported by the coordinator without an original receipt digest supplied to packaging review. It is not independently recertified here and does not qualify r12 resource accounting, cleanup or production acceptance. Its original header/README preserve source-delivery review scope, not a claim that it has never run elsewhere.

No production source, CI policy, permission or security setting is changed. No media, raw runtime logs, input bundle, private filesystem path, credential or captured environment value is included.
