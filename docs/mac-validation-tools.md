# Frozen Mac validation tools

This Draft delivers validation tools for [PR226](https://github.com/CongliangK/framivra/pull/226). It does not change production code, CI, permissions, or security settings. Source delivery and Linux tests do not establish Mac, production, performance, or pixel-equivalence acceptance. Do not merge this branch as an acceptance result.

## Source and checkout identity

The delivery branch is based on main commit `f1efe5f688b543ca11902c9538dec02a5c265961`. The separate production checkout must remain at PR226 commit `85fb352f9f95f9882222b2eacee2c887b520f495`, tree `7d499f03b734cb87c0b4286947b9fb60bd5d303c`. Keep validation tools outside that checkout; do not substitute the delivery checkout or a floating ref.

- [Launcher r4](../tools/testing/mac_validation/owned_launcher_r4/owned_launcher.py) consumes an explicit run specification. Keep its unchanged [resource parser](../tools/testing/mac_validation/owned_launcher_r4/resource_summary.py) beside it.
- [Fast-PNG harness r2](../tools/testing/mac_validation/fast_png_r2/harness.py) remains byte-identical to its reviewed revision. It consumes explicit paths and does not provide an independent supervisor or automatically advance arms.
- [Source manifest](../tools/testing/mac_validation/SOURCE-MANIFEST.json) records exact byte counts and SHA-256/Git blob identities.

## Launcher r4 cleanup scope

Only an explicitly launched Popen root with its own retained, currently matching identity may authorize signals to its root process group. An auxiliary process group never becomes a signal target, even when its members were observed as descendants. No process name grants permission. The tool does not automatically TERM or KILL auxiliary groups.

When the producer has exited successfully and no stop was requested, already tracked auxiliary groups may be observed while they exit naturally, within the remaining original cleanup deadline. A successful root group with residual live members remains UNKNOWN. Missing identity, uncovered members, observation errors, incomplete generation/guard evidence, remaining run directories, or an exhausted deadline remain blocking. Earlier failures are sticky; later empty observations do not erase them. A successful result that used the bounded natural wait is labeled `after bounded natural wait`.

The original resource accounting, limits, producer argv, production supervisor, and complete environment binding are unchanged. Natural-wait cleanup snapshots establish identity and group emptiness only; they do not establish complete RSS/CPU sampling for the cleanup tail. The cleanup deadline is cooperative, not an operating-system hard guarantee. Any previous Mac failure or UNKNOWN keeps its original result; r4 cannot retroactively qualify an earlier attempt. A new cleanup PASS describes only this outer bounded-natural-cleanup policy; it does not establish that the production supervisor itself controls or cleans the full process tree.

## Exact test scope and portable binding

The launcher/parser directory contains the r4 test sources, [35-ID approved list](../tools/testing/mac_validation/owned_launcher_r4/APPROVED-TEST-IDS.txt), and [two explicitly unrun IDs](../tools/testing/mac_validation/owned_launcher_r4/UNRUN-TEST-IDS.txt). The two unrun test methods remain in source; their presence is not permission to execute them.

The only portability change relative to the reviewed r4 test is selecting the production fixture through `FRAMIVRA_VALIDATION_REPO`; an absent variable fails directly. Before loading the approved exact IDs, explicitly set it to the verified absolute path of the separate production checkout and verify the commit, tree, tracked cleanliness, and actual imported `vtx.proc_supervisor` file. Do not use an empty value, relative path, fallback checkout, discovery, whole-class/module execution, or additional IDs. All r4 test methods and assertions are preserved by the portability change.

The approved list contains ordinary mocks/parsers and the two original self-owned standard-library Python sleep/ignore-TERM root cases. Auxiliary-process behavior is tested with mocks only. No real auxiliary process, browser, or Mac rendering is part of this cloud test qualification. Generated evidence stays local and must not be committed.

The frozen r4 runtime and original r4 test source passed an independent Linux run of the exact 35 approved IDs: 35/35 PASS, exit 0, 0.372 seconds, including the two self-owned root cases. The portable environment-binding change is reviewed separately and is not represented as that historical execution. The two excluded IDs, Mac, Chrome, and real production remain UNRUN in this r4 qualification.

The harness 12-ID inventory remains historical provenance only; its test suite and bound input fixtures are not included. Mac execution still requires its separate authorization and exact local input binding.

## Remaining gates

Before a new Mac attempt, verify all delivered source hashes, the frozen production checkout, legitimate resource-read capability, the exact authorized run specification and argv/environment, fresh project/evidence paths, and owned-process cleanup. Pixel comparison and production/performance acceptance remain separate gates. Missing evidence remains UNKNOWN and blocks advancement. The tool never grants permission to start the next stage.

No media, runtime logs, input bundles, personal filesystem paths, credentials, or actual environment values are included in this source delivery.
