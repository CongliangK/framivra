# Frozen Mac validation tools

This Draft change delivers reviewed validation source for [PR226](https://github.com/CongliangK/framivra/pull/226). It does not change production code or CI and does not establish production, performance, pixel-equivalence, process-cleanup, or Mac resource qualification. Do not merge this delivery branch as an acceptance result.

## Source and checkout identity

The delivery branch starts from main commit `f1efe5f688b543ca11902c9538dec02a5c265961`. That is the base of this delivery, not the production checkout to execute.

The tools must consume a separate clean production checkout at PR226 commit `85fb352f9f95f9882222b2eacee2c887b520f495`, tree `7d499f03b734cb87c0b4286947b9fb60bd5d303c`. Keep the tools outside that checkout. Do not substitute this delivery branch or a floating ref for the frozen production checkout.

- [Owned-process launcher r3](../tools/testing/mac_validation/owned_launcher_r3/owned_launcher.py) consumes an explicit run specification. Its [resource parser](../tools/testing/mac_validation/owned_launcher_r3/resource_summary.py) must stay beside it.
- [Production fast-PNG harness r2](../tools/testing/mac_validation/fast_png_r2/harness.py) consumes explicit production, project, inventory, and evidence paths. It does not provide its own process supervisor or automatically advance to another arm.
- [Source manifest](../tools/testing/mac_validation/SOURCE-MANIFEST.json) records byte counts, SHA-256 and Git blob identities for the delivered runtime sources and test-ID inventories.

The three runtime source files are byte-for-byte copies of the independently reviewed revisions. No path adaptation, source rewriting, wrapper injection, permission change, or additional validation run was performed for this delivery.

## Review scope

The launcher review covered 27 exact CPU/parser/mock and ordinary self-owned-process test IDs on a Linux host. The harness review covered 12 exact CPU/mock contract test IDs. These historical results do not establish that the tools have run on the Mac or that real browser/rendering acceptance passed.

The two `APPROVED-TEST-IDS.txt` files are inventories of those reviewed test IDs. They are not runnable test suites or permission to execute additional tests. The original test sources depend on a separate frozen checkout and input metadata; those sources and fixtures are not included in this initial runtime-source delivery. Any later test binding must preserve the original assertions and expected source identities and identify the actual local inputs explicitly.

## Before use

Use only the existing authorized Mac execution workflow. First verify this delivery's source hashes and the separate production checkout's exact commit, tree, tracked cleanliness, and actual imported module paths. The launcher consumes a complete explicit environment file and approved argv through its run specification; the harness consumes explicit CLI paths. Do not replace missing inputs, lower expected coverage, or change pinned identities to make checks pass.

Mac self-owned-process qualification, legitimate resource-read qualification, exact run specification, fresh input/evidence locations, all owned-process cleanup, complete pixel comparison, and the separately approved stage gates remain required. Resource limits are sampled stop thresholds, not operating-system hard limits or a guarantee against memory exhaustion. Missing or unverifiable evidence remains UNKNOWN and blocks advancement.

No media, runtime logs, input bundles, personal filesystem paths, credentials, or actual environment values are part of this delivery. Only explicitly configured local paths may be used when execution is separately authorized.
