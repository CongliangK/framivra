# NAVFREE pixel pilot r1

Source-only delivery of a separately reviewed 48-target pixel-equivalence pilot. This directory does not authorize Mac execution, alter existing C300/PAGES NAVFREE=0 plans, or grant production/performance qualification.

## Scope and identities

The harness calls production `render_page` for T08.01 then T07.01, with 24 selected global frames per page. The three separately released arms are navigation-ascending, NAVFREE-ascending and NAVFREE-descending. All use FAST=1, PAGES=1, TABS=1, SPLIT_FRAMES=0 and SPLIT_MAX=1. The exact frame list and global-to-local mapping are in the source. Three complete arms produce 144 target PNGs and require 96 full-RGBA comparisons. Setup probes are counted separately. Coverage does not extend to the other S4 frames, other pages or arbitrary rendering histories.

- Production commit: `85fb352f9f95f9882222b2eacee2c887b520f495`; tree: `7d499f03b734cb87c0b4286947b9fb60bd5d303c`.
- Reused sibling r1 harness SHA256: `e4a067f86e8914b4f9ccb40c42fbf22c95db700e36ac1c9aabad7df3c21cbdaf`.
- New `navfree_pilot.py` SHA256: `c3c3e8c6611e0f92507d759a6170eb4b1064ff9174830784dc21c4c263627e57`.
- `test_navfree_pilot.py` SHA256: `8aca40771f6c26aee8399e3820b5d8f8b02e07c39219d4a509dc300f6dcb7958`.
- `APPROVED-TEST-IDS.txt` SHA256: `29800a08665cbd0705c8e68c2b7f16799b34ee4b2d577222ff2a4d290749650b`.

Runtime, tests and the 12-ID list are byte-identical to the independently reviewed source. Existing r1/r12 files and their frozen source manifest remain unchanged. This extension reuses the hash-verified r1 input-copy, production-import and strict PNG/RGBA helpers. Its separate NAVFREE gate does not relabel raw stats or weaken the original override-only gate.

## Ordinary contract tests

The original and independent repaired-source runs passed exactly the 12 IDs in `APPROVED-TEST-IDS.txt`. They use ordinary CPU/mock fixtures; do not use discovery or expand to native/browser/media/security/process-fault tests. A source/mock PASS is not a Mac pixel or cleanup result.

The test module requires four explicit external bindings:

- `NAVFREE_BASE_HARNESS`: the unchanged sibling `bounded_page_harness_r1/harness.py`.
- `NAVFREE_PRODUCTION_REPO`: the exact clean production checkout above. The inherited helper also permits the reviewed same-tree synthetic commit for cloud contract checks only.
- `NAVFREE_BOUND`: the reviewed 109-file audit bundle, with the five intentionally omitted media files named in test 01. It is a test fixture, not a complete runnable project.
- `NAVFREE_INVENTORY`: the frozen 114-entry input manifest expected by the sibling helper.

No input bundle, private environment value or machine path is included. Test 01 deliberately checks the audit bundle's omissions; substituting the complete runtime project changes that fixture. Real `run-arm` always requires all original 114 files and never adopts the test fixture's partial-input rule.

After authorized bindings are available, select only the exact list from this directory:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest -v $(cat APPROVED-TEST-IDS.txt)
```

## Runtime release remains separate

The `run-arm` CLI accepts one arm only and requires a reviewed r12 owner spec. It starts no additional supervisor, session or subprocess and never starts the next arm. Use the existing `owned_launcher_r12` workflow and [delivery guide](../../../../docs/mac-validation-tools.md). A real release must bind installed Python/Chrome identities, the unchanged complete input, full environment, fresh project/namespace/log/evidence paths, source hashes, current cumulative output roots and actual resource observations. No runnable spec is shipped here.

Preserve two expected browser generations, the 600-second run deadline, the existing 60-second total cleanup budget and explicit `diagnostic_census_v1` mode. All r12 ownership, resource, pressure/swap, cumulative disk, stop and cleanup gates remain mandatory. Existing failed/incomplete/unknown attempts retain their labels and evidence. Cleanup UNKNOWN/FAILED blocks progression; no alternative cleanup exception is introduced.

Every target must have the expected actual route count, zero capture failures/fallback/reuse and the fixed FAST provenance. Requested NAVFREE is not proof of its actual use. Production event geometry is a sorted sparse target set; exact requested execution order is recorded separately. No independent per-frame CDP order/paint trace is claimed, and no hook-return check or paint barrier is inserted.

`compare-pair` uses the inherited strict PNG decoder, checks exact target sets and compares all RGBA bytes including hidden RGB at alpha zero. It writes append-only MATCH, MISMATCH or INCOMPLETE reports; decode errors retain the completed count, partial rows and failed target when available. Failures and mismatches return nonzero. Full source/route/pixel/owner/cleanup review remains required before another arm or any broader claim.
