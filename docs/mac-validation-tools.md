# Frozen Mac validation tools

This Draft delivers reviewed validation tools for [PR226](https://github.com/CongliangK/framivra/pull/226). Source delivery and cloud tests do not establish Mac, production, performance, or pixel-equivalence acceptance. Do not merge this branch as an acceptance result.

## Source and checkout identity

The delivery branch is based on main commit `f1efe5f688b543ca11902c9538dec02a5c265961`. Its current sources are [owner r8](../tools/testing/mac_validation/owned_launcher_r8/owned_launcher.py), its [resource parser](../tools/testing/mac_validation/owned_launcher_r8/resource_summary.py), and unchanged [bounded-page harness r1](../tools/testing/mac_validation/bounded_page_harness_r1/harness.py). Older revisions remain recoverable in Git history rather than duplicate current-tree directories.

The separate production checkout must remain at PR226 commit `85fb352f9f95f9882222b2eacee2c887b520f495`, tree `7d499f03b734cb87c0b4286947b9fb60bd5d303c`. Keep validation tools outside that checkout; do not substitute this delivery checkout or a floating ref. The [source manifest](../tools/testing/mac_validation/SOURCE-MANIFEST.json) records bytes, SHA-256, Git blob identities, and the distinction between executed review bytes and statically adapted test bindings.

## Owner r8 observation contract

Resource traversal probes every eligible independent live branch in the snapshot before reconciling candidate coverage. A failed probe is cached for that snapshot; it cannot suppress later siblings or be retried through another traversal path. Descendants behind an unverified parent remain blocked, without gaining ownership. Every original/fresh related candidate still needs verified live coverage or separately qualified terminal evidence. Uncovered candidates remain UNKNOWN.

The observation-only Darwin terminal probe uses the existing frozen BSDINFO interface with `flavor=3, arg=1`. A ps Z state alone is insufficient. Qualification requires one full structure, matching PID/PPID/PGID, positive valid start identity, SZOMB status, and any previously retained identity match. Only a terminal leaf with a verified live parent or retained identity may qualify. Roots, unresolved nonterminal rows, unproved terminal rows, and terminal rows with visible descendants remain UNKNOWN. Diagnostic terminal anchors retain candidate coverage across ticks but confer no live ownership, child ancestry, group, guard, or signaling authority.

Live RSS means the sum of verified live-process ps RSS. Qualified terminal observations are separate metadata. Their printed ps RSS/CPU fields are reported values, not measured zero usage, and do not enter live RSS, recent CPU sums, or observed CPU-time maxima. Prior nonzero CPU maxima remain retained. Residual kernel/corpse memory is not estimated. This is a revised observation contract, not a claim that the old all-visible-process metric passed unchanged.

One already permitted bounded fresh query may produce a separate usable observation, but it never erases the original INCOMPLETE row, its failure, or its verified live RSS lower bound. Keep original rows and PARTIAL summary diagnostics. Total JSONL row count is not valid-sample count. Resource thresholds, aggregate deadlines, cleanup policy, root-only signal boundary, producer argv, and production supervisor remain unchanged. Deadline checks are cooperative before/after probes, not syscall preemption.

Unverified rows retain structural fields, full-row hashes, and command-free prefixes. Full command text is retained only for actually live-verified rows. Terminal proof emits no command/name/UID fields. The existing live identity API remains unchanged. Auxiliary groups are never automatically signaled; bounded natural cleanup still requires the original identity, generation/guard, reap, empty-group, and directory gates. Cleanup-tail snapshots establish identity/emptiness, not complete tail RSS/CPU accounting. A cleanup PASS describes the outer tool policy, not production-supervisor full-tree control.

Historical failed attempts remain failures. This change does not retroactively diagnose every earlier Mac BA identity failure or qualify prior incomplete resource evidence.

## Bounded-page harness r1 contract

The harness, its portable test source, and its 19-ID list are unchanged from the preceding delivery. It adds thin `route-run` and `pages12` handling while preserving original C300/T10 semantics. A single route synchronously calls canonical validate then gfx argv under the existing owner; gfx requires successful validate exit, events, receipt, and provenance. It adds no shell, process group, supervisor, retries, or automatic next arm. The log destination must be a fresh child directory, not the existing owner evidence directory itself.

The pages12 projection retains exact T10.01 300-frame and T12.01 180-frame clips and other timeline fields. Both pages1/pages2 arms require an explicitly frozen common FAST value; only PAGES differs. Render-attempt overlap does not prove simultaneous individual screenshots, full-scene speedup, or eligibility for higher concurrency. Per-page counts, complete RGBA comparison, resources, cleanup, and actual stage approval remain separate gates.

## Exact test selection and exclusions

The [owner approved list](../tools/testing/mac_validation/owned_launcher_r8/APPROVED-TEST-IDS.txt) contains exactly 51 pure-mock methods: 39 unchanged historical methods plus 12 new complete-snapshot/terminal-observation methods. It is not the previous 43-ID run relabeled. No actual child processes, Mac probes, browsers, CEF, or production commands were executed by this 51-ID qualification.

The [round-excluded list](../tools/testing/mac_validation/owned_launcher_r8/ROUND-EXCLUDED-TEST-IDS.txt) contains four r7 methods that were not executed this round:
- Two real self-owned sleep/ignore-TERM process exercises: UNRUN this round.
- The old mixed original-branch coverage method: its early-abort assumption changed; new tests distinguish genuinely probed independent branches from unavailable ancestry.
- The old mixed visible S/Z method: its blanket absence-of-terminal-proof assumption changed; new tests separately preserve nonterminal/unproved-terminal UNKNOWN and positive terminal qualification.

Their historical source and evidence remain intact. These two changed-contract methods are excluded, not silently weakened or claimed passing. The [historical UNRUN list](../tools/testing/mac_validation/owned_launcher_r8/UNRUN-TEST-IDS.txt) retains the two earlier identity/lineage exclusions. All six excluded methods remain outside this round's execution.

The [harness approved list](../tools/testing/mac_validation/bounded_page_harness_r1/APPROVED-TEST-IDS.txt) contains the unchanged 19 exact ordinary CPU/mock IDs. Do not use discovery, whole-class/module runs, extra IDs, or the excluded methods.

## Portable bindings

Test binding must use verified nonempty absolute paths:
- `FRAMIVRA_VALIDATION_REPO`: the separate frozen production checkout, for both suites.
- `FRAMIVRA_VALIDATION_BOUND`: the genuine frozen bound-input project, for harness tests.
- `FRAMIVRA_VALIDATION_INVENTORY`: the genuine frozen 114-item inventory file, for harness tests.
- `FRAMIVRA_VALIDATION_PLAN`: the genuine frozen plan file, for harness tests.

Missing variables fail directly. Do not substitute relative paths, defaults, another checkout, generated stand-ins, fabricated files/hashes, or weakened assertions. Independently verify commit/tree, tracked cleanliness, and actual imported production-module paths. Missing genuine inputs block the relevant suite.

Only the owner's repository assignment is adapted in r8, with the same single-line environment binding used previously. The harness's four explicit environment bindings remain unchanged. All test methods, assertions, expected identities, and exact selections remain intact. Runtime bytes match their independent review. Portable binding review is static only; original cloud executions are not relabeled as execution of adapted source. Generated evidence remains local.

## Review and remaining gates

The current independent owner qualification is exactly 51/51 pure-mock IDs, 0 FAIL, 0 SKIP, exit 0, 0.145 seconds, on the frozen original test source. Source identities are recorded in the manifest. The retained historical harness qualification is 19/19 ordinary CPU/mock IDs, exit 0, 0.709 seconds, without real validate/gfx processes or browsers.

The harness author's old first attempt accidentally used default discovery after an empty argument list. It remains excluded from exact-ID evidence; qualification rests on its corrected exact-ID attempt and independent exact-ID review. Historical results are not rewritten.

No tests are executed for this packaging adaptation. Actual Mac ABI behavior, a separately authorized run specification and environment, genuine input binding, complete pixel comparison, cleanup, resource evidence, and production/performance acceptance remain separate gates. Missing evidence stays UNKNOWN and blocks advancement. Tool delivery does not authorize a next stage.

## Separate ordinary Mac ABI fixture

The [source-only fixture and contract](../tools/testing/mac_validation/mac_zombie_abi_r1/README.md) have separate independent static review. Actual Mac execution is UNRUN. The fixture creates one direct child that exits normally, observes only that known child's structural state and BSDINFO responses, and reaps it. It requires already-default SIGCHLD, pinned owner source/import, and a fresh evidence path. It sends no child/group signals; a timed-out ps query may terminate only its own ps helper.

Its at-most-ten-second budget is cooperative, not an OS hard timeout or a guarantee of cleanup after forced interpreter/OS termination. Actual absolute paths and the one command still require separate review and authorization. Missing evidence or cleanup remains UNKNOWN. Even a future fixture PASS would qualify only ordinary ABI/zombie behavior, not full sampler or production acceptance.

No media, raw runtime logs, input bundle, personal filesystem path, credential, or captured environment value is included.
