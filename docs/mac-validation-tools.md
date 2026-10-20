# Frozen Mac validation tools

This Draft delivers reviewed validation tools for [PR226](https://github.com/CongliangK/framivra/pull/226). It does not modify production code, CI, permissions, security settings, main, PR226, or PR230. Source delivery and cloud tests do not establish Mac, production, performance, or pixel-equivalence acceptance. Do not merge this branch as an acceptance result.

## Source and checkout identity

The delivery branch is based on main commit `f1efe5f688b543ca11902c9538dec02a5c265961`. Its current sources are [owner r7](../tools/testing/mac_validation/owned_launcher_r7/owned_launcher.py), its unchanged [resource parser](../tools/testing/mac_validation/owned_launcher_r7/resource_summary.py), and [bounded-page harness r1](../tools/testing/mac_validation/bounded_page_harness_r1/harness.py). Older tool revisions remain available in Git history rather than duplicate current-tree directories.

The separate production checkout must remain at PR226 commit `85fb352f9f95f9882222b2eacee2c887b520f495`, tree `7d499f03b734cb87c0b4286947b9fb60bd5d303c`. Keep validation tools outside that checkout; do not substitute this delivery checkout or a floating ref. [Source manifest](../tools/testing/mac_validation/SOURCE-MANIFEST.json) records exact bytes, SHA-256, Git blob identities, and the distinction between executed review bytes and statically adapted test bindings.

## Owner r7 scope

The owner uses a conservative completeness gate for the original and fresh related-process candidate sets. Every visible related candidate must be covered by a verified identity before a complete sample is accepted; uncovered candidates remain UNKNOWN. A single bounded fresh query, when allowed, does not erase its original INCOMPLETE observation. Preserve that original row and its evidence; a later complete row is separate and linked to it. The resource summary may report PARTIAL and diagnostic errors for incomplete rows; total JSONL row count is not valid-sample count, and rows must not be removed to make errors disappear.

Unverified rows retain structural fields, the full-row hash, and a command-free prefix. Full command text is retained only for actually verified rows. Candidate relationships used to reject incomplete observations do not grant ownership, signal permission, or permission to record unverified command text.

The r4 resource thresholds, deadlines, cleanup policy, root-only signal boundary, producer argv, and production supervisor remain unchanged. Auxiliary process groups are never automatically signaled. Bounded natural cleanup still requires the original complete identity, generation/guard, reap, empty-group, and directory gates. Cleanup-tail snapshots establish identity/emptiness, not full-tail RSS/CPU usage. A cleanup PASS describes the outer tool policy; it does not establish production-supervisor full-tree control or erase an old failure. The cause of the earlier Mac BA identity failure remains unknown and is not retroactively attributed to a repaired race.

## Bounded-page harness r1 scope

The harness extends the reviewed r2 thin caller with `route-run` and `pages12`; original C300 and T10 off/on semantics remain unchanged. A single `route-run` synchronously calls the canonical validate then gfx argv under the existing owner. Gfx is permitted only after validate exit, events, receipt and provenance checks pass. It adds no shell, process group, supervisor, retries, or automatic next arm. Its log destination must be a fresh child directory, not the existing owner evidence directory itself.

The two-page projection retains the exact T10.01 300-frame and T12.01 180-frame clips and preserves other timeline fields. Both pages1/pages2 arms require an explicitly frozen FAST value; only PAGES differs between the arms. Attempt-overlap inspection, per-page counts and complete RGBA comparisons remain separate evidence. Overlapping render attempts do not prove simultaneous individual screenshots, full-scene performance, or safe higher concurrency. PAGES2 and the final FAST choice remain separately gated by actual baseline, resource, pixel and cleanup evidence.

## Portable test bindings and exact scope

The [owner approved list](../tools/testing/mac_validation/owned_launcher_r7/APPROVED-TEST-IDS.txt) contains 43 exact IDs, including its three independent ordinary-mock modules. Its [UNRUN list](../tools/testing/mac_validation/owned_launcher_r7/UNRUN-TEST-IDS.txt) retains two old methods that were explicitly not run in this review round. The [harness approved list](../tools/testing/mac_validation/bounded_page_harness_r1/APPROVED-TEST-IDS.txt) contains 19 exact IDs. Do not use discovery, whole-class/module runs, additional IDs, or the owner UNRUN methods.

Test binding is explicit and must use verified nonempty absolute paths:
- `FRAMIVRA_VALIDATION_REPO`: the separate frozen production checkout, for both test suites.
- `FRAMIVRA_VALIDATION_BOUND`: the genuine frozen bound-input project, for harness tests.
- `FRAMIVRA_VALIDATION_INVENTORY`: the genuine frozen 114-item inventory file, for harness tests.
- `FRAMIVRA_VALIDATION_PLAN`: the genuine frozen plan file, for harness tests.

Missing required variables fail directly. Do not substitute relative paths, defaults, another checkout, generated fixture stand-ins, or fabricated files/hashes. Independently verify checkout commit/tree, tracked cleanliness and actual imported production-module paths. The harness's existing inventory, timeline, entry and plan hash assertions remain unchanged. If any genuine input is unavailable, that suite is blocked until the genuine input is supplied; a missing plan is not permission to reconstruct different bytes or weaken its pinned hash.

Only the owner test's repository assignment and the harness tests' four path assignments are adapted; all reviewed methods, assertions, expected identities and approved/UNRUN lists remain unchanged. The runtime files are exact reviewed bytes. These portable bindings receive static review only and are not represented as the original cloud test execution. Generated test evidence stays local and must not be committed.

## Review evidence and remaining gates

Independent owner review executed exactly 43 IDs: 43 PASS, 0 FAIL, 0 SKIP, exit 0, 0.393 seconds. The 43 include only the original two real self-owned standard-library sleep/ignore-TERM roots; the added process-coverage cases are ordinary mocks. The two listed old methods remained UNRUN. Independent harness review executed exactly its 19 ordinary CPU/mock IDs: 19 PASS, exit 0, 0.709 seconds, without real validate/gfx processes or browsers.

The harness author's first attempt accidentally invoked default discovery after an empty argument list; it is excluded as exact-ID evidence and remains a recorded deviation. Qualification rests on the corrected exact-ID attempt and the independent exact-ID review. Neither that correction nor this delivery rewrites historical results.

No additional tests are executed for this packaging change. Mac execution, a fresh authorized run specification and complete environment, exact input binding, complete pixel comparison, process cleanup, resource evidence and production/performance acceptance remain separate gates. Missing evidence remains UNKNOWN and blocks advancement. This tool delivery does not authorize the next stage.

No media, raw runtime logs, input bundle, personal filesystem path, credential or captured environment value is included.
