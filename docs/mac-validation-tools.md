# Frozen Mac validation tools

This Draft delivers reviewed validation tools for [PR226](https://github.com/CongliangK/framivra/pull/226). Source delivery and cloud tests do not establish Mac, production, performance, or pixel-equivalence acceptance. Do not merge this branch as an acceptance result.

## Source and checkout identity

The delivery branch is based on main commit `f1efe5f688b543ca11902c9538dec02a5c265961`. Its current sources are [owner r11](../tools/testing/mac_validation/owned_launcher_r11/owned_launcher.py), its [resource parser](../tools/testing/mac_validation/owned_launcher_r11/resource_summary.py), and unchanged [bounded-page harness r1](../tools/testing/mac_validation/bounded_page_harness_r1/harness.py). Older revisions remain recoverable in Git history rather than duplicate current-tree directories.

The separate production checkout must remain at PR226 commit `85fb352f9f95f9882222b2eacee2c887b520f495`, tree `7d499f03b734cb87c0b4286947b9fb60bd5d303c`. Keep validation tools outside that checkout; do not substitute this delivery checkout or a floating ref. The [source manifest](../tools/testing/mac_validation/SOURCE-MANIFEST.json) records bytes, SHA-256, Git blob identities, and the distinction between executed review bytes and statically adapted test bindings.

## Owner r11 finite resource candidate-set recovery

The r11 increment changes only `observe_resources`: 40 added and 11 removed lines against r10. The frozen set contains every original related candidate lacking verified live identity or positively qualified terminal evidence. These are observation candidates, not an owned cohort. Every candidate must have been independently probed, be non-root, have no visible descendants, and be previously retained/diagnostically anchored or directly under a live-verified parent. Unprobed or ancestry-blocked candidates remain UNKNOWN.

The eligible set shares exactly the existing one fresh validated inventory under the original deadline. No wait loop, third query, per-PID retry budget, new deadline, sleep, native API, or permission is added. Every original candidate must be globally absent or positively terminal-qualified in that fresh inventory. A visible S, ?, or unproved Z blocks the entire set. Original candidate PIDs remain diagnostic closure anchors, so reparenting cannot remove a visible candidate from coverage. Descendants of any failed candidate and incomplete fresh live coverage remain blocking.

The original INCOMPLETE row and its measured live RSS lower bound remain recorded. A usable fresh observation contains only independently measured live rows, with terminal proof kept separate. The summary remains PARTIAL. Absence does not invent a start identity, RSS/CPU zero, ownership, process group, guard, or signal authority. Original singular diagnostics remain alongside additive set-wide evidence.

Registry, terminal-information implementation, resource summary, thresholds, cadence, cleanup, signals, native ABI, and budget allocations retain their reviewed bytes. The root, original coverage, and verified resource-limit gates keep precedence. This is bounded observation recovery, not reconstruction of unseen CPU/RSS or a hard peak guarantee.

## Retained finite-cohort cleanup contract

The r10 cleanup implementation is retained byte-for-byte in r11. At entry to a qualifying pending sequence it copies all already-retained PID/start/PGID records and freezes the auxiliary cohort, excluding the entire explicit root PGID. It does not mutate the registry, adopt identities, grant ancestry, or add signal authority. Existing terminal diagnostic anchors remain outside cohort membership.

Entry still requires healthy producer exit, an original unavailable live identity result of exactly None for a retained auxiliary member at its retained PGID, and a complete validated structural inventory. Root failures, unretained members, non-None mismatches, missing structural proof, or query errors do not gain a route to PASS. Original UNKNOWN/INCOMPLETE checks and prior failures remain intact.

Independently retained parents, children, and siblings may enter None in any order within this one fixed cohort. A known child may reparent while keeping its retained PGID. No parent identity is used to grant new child ownership. Follow-up identity checks use the frozen copied records, not subsequently mutable registry entries. Every visible related row must be covered by the frozen known record at its retained PGID; an unretained related row, including Z, remains UNKNOWN. Diagnostic anchors and previously observed related candidates preserve closure across groups or an absent ancestor.

Resolution requires every frozen auxiliary cohort PID to be globally absent from the full validated fresh inventory, with complete related coverage. A still-live member with a matching identity also keeps the sequence pending. S, ?, Z, or another valid printed state is never absence or positive terminal proof; ? is not assumed to mean Z. The existing Z behavior skips a live-only identity call but cannot satisfy the whole-cohort absence gate.

The existing 0.1-second cadence and remaining 60-second cooperative cleanup budget are unchanged. Persistent S/None, ?/None, or Z reaches the deadline as UNKNOWN. Changed PGID, mismatched non-None identity, query/probe exceptions, deadline, root residual, failed nested evidence, unreaped root, run-directory failures, and prior failures remain blocking. A helper query error ends that recovery sequence; an ordinary outer observation may still occur later, but cannot erase the failure or rehabilitate the sequence.

The full structural PID/PPID/PGID/state inventory remains transient. Serialized evidence retains only matching/related rows, fixed cohort identities, checks, query timestamps/deadline, and inventory hashes. No command, arguments, process name, UID, RSS, or CPU data are added by cleanup. There are no new signals, terminal API calls, resource probes, process launches, dependencies, permissions, or budgets.

Every original observation and each follow-up remains recorded. A PASS establishes only a later bounded observed finite-cohort cleanup postcondition, subject to the unchanged root/nested/reap/directory gates. It does not prove the complete history of every process that ever ran or reconstruct CPU/RSS after producer exit. The budget remains cooperative, not an OS hard timeout. Old r8 and r9 Mac cleanup results remain UNKNOWN; final empty filtered groups alone never retroactively establish this new proof.

## Retained resource observation contract

Resource traversal probes every eligible independent live branch in the snapshot before reconciling candidate coverage. A failed probe is cached for that snapshot; it cannot suppress later siblings or be retried through another traversal path. Descendants behind an unverified parent remain blocked, without gaining ownership. Every original/fresh related candidate still needs verified live coverage or separately qualified terminal evidence. Uncovered candidates remain UNKNOWN.

The observation-only Darwin terminal probe uses the existing frozen BSDINFO interface with `flavor=3, arg=1`. A ps Z state alone is insufficient. Qualification requires one full structure, matching PID/PPID/PGID, positive valid start identity, SZOMB status, and any previously retained identity match. Only a terminal leaf with a verified live parent or retained identity may qualify. Roots, unresolved nonterminal rows, unproved terminal rows, and terminal rows with visible descendants remain UNKNOWN. Diagnostic terminal anchors retain candidate coverage across ticks but confer no live ownership, child ancestry, group, guard, or signaling authority.

Live RSS means the sum of verified live-process ps RSS. Qualified terminal observations are separate metadata. Their printed ps RSS/CPU fields are reported values, not measured zero usage, and do not enter live RSS, recent CPU sums, or observed CPU-time maxima. Prior nonzero CPU maxima remain retained. Residual kernel/corpse memory is not estimated. This is a revised observation contract, not a claim that the old all-visible-process metric passed unchanged.

One already permitted bounded fresh query may produce a separate usable observation, but it never erases the original INCOMPLETE row, its failure, or its verified live RSS lower bound. Keep original rows and PARTIAL summary diagnostics. Total JSONL row count is not valid-sample count. Resource thresholds, aggregate deadlines, cleanup budget, root-only signal boundary, producer argv, and production supervisor remain unchanged; the narrow cleanup observation resolution rule is described above. Deadline checks are cooperative before/after probes, not syscall preemption.

Unverified rows retain structural fields, full-row hashes, and command-free prefixes. Full command text is retained only for actually live-verified rows. Terminal proof emits no command/name/UID fields. The existing live identity API remains unchanged. Auxiliary groups are never automatically signaled; bounded natural cleanup still requires the original identity, generation/guard, reap, empty-group, and directory gates. Cleanup-tail snapshots establish identity/emptiness, not complete tail RSS/CPU accounting. A cleanup PASS describes the outer tool policy, not production-supervisor full-tree control.

Historical failed attempts remain failures. This change does not retroactively diagnose every earlier Mac BA identity failure or qualify prior incomplete resource evidence.

## Bounded-page harness r1 contract

The harness, its portable test source, and its 19-ID list are unchanged from the preceding delivery. It adds thin `route-run` and `pages12` handling while preserving original C300/T10 semantics. A single route synchronously calls canonical validate then gfx argv under the existing owner; gfx requires successful validate exit, events, receipt, and provenance. It adds no shell, process group, supervisor, retries, or automatic next arm. The log destination must be a fresh child directory, not the existing owner evidence directory itself.

The pages12 projection retains exact T10.01 300-frame and T12.01 180-frame clips and other timeline fields. Both pages1/pages2 arms require an explicitly frozen common FAST value; only PAGES differs. Render-attempt overlap does not prove simultaneous individual screenshots, full-scene speedup, or eligibility for higher concurrency. Per-page counts, complete RGBA comparison, resources, cleanup, and actual stage approval remain separate gates.

## Exact test selection and exclusions

The [owner approved list](../tools/testing/mac_validation/owned_launcher_r11/APPROVED-TEST-IDS.txt) contains exactly 68 pure-mock methods: all 62 inherited methods unchanged, plus six resource candidate-set methods containing 20 fixtures. The new cases cover two first-seen leaves in different row orders; retained and mixed candidate sets; absent and positively terminal-qualified candidates; visible unproved candidates; original/fresh descendants; fresh coverage; reparenting; query/parsing errors; and the original deadline. No actual child processes, Mac probes, browsers, CEF, or production commands are part of this qualification. The earlier proposed additional cleanup identity-mismatch fixture remains excluded from execution; its existing guard has static review only.

The [round-excluded list](../tools/testing/mac_validation/owned_launcher_r11/ROUND-EXCLUDED-TEST-IDS.txt) contains six preserved methods that were not executed this round:
- Two real self-owned sleep/ignore-TERM process exercises: UNRUN this round.
- The old mixed original-branch coverage method: its early-abort assumption changed; new tests distinguish genuinely probed independent branches from unavailable ancestry.
- The old mixed visible S/Z method: its blanket absence-of-terminal-proof assumption changed; selected tests separately preserve nonterminal/unproved-terminal UNKNOWN and positive terminal qualification.
- The r9 mixed leaf-resolution method: its sibling subcase expects return while an independently retained sibling is still alive; the new contract waits for whole-cohort absence.
- The r9 mixed fresh-coverage method: its first subcase treats a second retained None as fatal; that member now remains pending. Its unretained-member and nested-UNKNOWN negatives are explicitly retained in the new methods.

Their historical source and evidence remain intact. The changed-contract methods are excluded, not silently weakened or claimed passing. The [historical UNRUN list](../tools/testing/mac_validation/owned_launcher_r11/UNRUN-TEST-IDS.txt) retains the two earlier identity/lineage exclusions. All eight excluded methods remain outside this round's execution.

The [harness approved list](../tools/testing/mac_validation/bounded_page_harness_r1/APPROVED-TEST-IDS.txt) contains the unchanged 19 exact ordinary CPU/mock IDs. Do not use discovery, whole-class/module runs, extra IDs, or the excluded methods.

## Portable bindings

Test binding must use verified nonempty absolute paths:
- `FRAMIVRA_VALIDATION_REPO`: the separate frozen production checkout, for both suites.
- `FRAMIVRA_VALIDATION_BOUND`: the genuine frozen bound-input project, for harness tests.
- `FRAMIVRA_VALIDATION_INVENTORY`: the genuine frozen 114-item inventory file, for harness tests.
- `FRAMIVRA_VALIDATION_PLAN`: the genuine frozen plan file, for harness tests.

Missing variables fail directly. Do not substitute relative paths, defaults, another checkout, generated stand-ins, fabricated files/hashes, or weakened assertions. Independently verify commit/tree, tracked cleanliness, and actual imported production-module paths. Missing genuine inputs block the relevant suite.

Only the owner's repository assignment is adapted in r11, with the same single-line environment binding used previously. The harness's four explicit environment bindings remain unchanged. All test methods, assertions, expected identities, and exact selections remain intact. Runtime bytes match their independent review. Portable binding review is static only; original cloud executions are not relabeled as execution of adapted source. Generated evidence remains local. The tests are not side-effect-free: `test_independent_r5.py` line 20 writes `ordinary-reparent.actual.json`, and `test_independent_fresh_child.py` line 16 writes `fresh-visible-child.actual.json`, beside their own modules through `Path(__file__).parent`. Changing the working directory does not redirect those outputs. Account for both files before an authorized run. Preserve any confirmed new diagnostics with hashes and provenance in the evidence location; do not erase them, alter source, or rerun tests merely to disguise a dirty checkout.

## Review and remaining gates

The current independent owner qualification is exactly 68/68 pure-mock IDs, 0 FAIL, 0 ERROR, 0 SKIP, exit 0, 0.190 seconds, on the frozen original test source. Source identities are recorded in the manifest. The retained historical harness qualification is 19/19 ordinary CPU/mock IDs, exit 0, 0.709 seconds, without real validate/gfx processes or browsers.

The harness author's old first attempt accidentally used default discovery after an empty argument list. It remains excluded from exact-ID evidence; qualification rests on its corrected exact-ID attempt and independent exact-ID review. Historical results are not rewritten.

No tests are executed for this packaging adaptation. Actual r11 Mac execution is UNRUN. The supplied actual r10 reverse-comparison result remains FAILED, with resource INCOMPLETE and cleanup UNKNOWN. The immediate old single-unresolved-leaf gate is identified from source and the coordinator report; original Mac raw bytes were not independently read for this review. Later absence cannot prove the contents of a fresh inventory that was never taken, and the separate successful original AB pair does not complete the failed reverse comparison. Actual Mac ABI behavior, a separately authorized run specification and environment, genuine input binding, complete pixel comparison, cleanup, resource evidence, and production/performance acceptance remain separate gates. Missing evidence stays UNKNOWN and blocks advancement. Tool delivery does not authorize a next stage.

## Separate ordinary Mac ABI fixture

The [source-only fixture and contract](../tools/testing/mac_validation/mac_zombie_abi_r1/README.md) retain their original source-only independent static review. They are unchanged and not rerun in this r11 delivery. A separate historical Mac run was reported PASS by the task coordinator; the original receipt digest was not supplied to this packaging review, so this package does not independently certify that run or claim a fresh ABI execution. The fixture creates one direct child that exits normally, observes only that known child's structural state and BSDINFO responses, and reaps it. It requires already-default SIGCHLD, pinned owner source/import, and a fresh evidence path. It sends no child/group signals; a timed-out ps query may terminate only its own ps helper.

Its at-most-ten-second budget is cooperative, not an OS hard timeout or a guarantee of cleanup after forced interpreter/OS termination. Actual absolute paths and the one command still require separate review and authorization. Missing evidence or cleanup remains UNKNOWN. The reported historical fixture PASS concerns ordinary ABI/zombie behavior only; it does not qualify r11 resource recovery, cleanup, full sampler correctness, or production acceptance. The unchanged fixture header and adjacent README preserve their original source-delivery review scope, not a claim that the fixture has never been run elsewhere.

No media, raw runtime logs, input bundle, personal filesystem path, credential, or captured environment value is included.
