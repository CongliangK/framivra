# Offline CDP cost ledger

This opt-in experiment reads existing evidence. It does not import renderers,
start browsers, benchmark, scan directories, read recorded PNG paths, inspect
processes, change cache state or grant performance/quality acceptance. It uses
Python's standard library and the existing pure `vtx.runtime.events.read_events` reducer.
Run from the repository root. No installation is needed.

## Explicit inputs and output

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m experiments.perf_cdp_cost.report \
  --events /evidence/all.events.jsonl --time-l /evidence/all.stderr-time-l.log \
  --identity /evidence/starting-identities.json \
  --arm /evidence/baseline/result.json --arm /evidence/candidate/result.json \
  --pair /evidence/pair/result.json --quality /evidence/quality/result.json
```

Default output is JSON on stdout. Use `--format text` for a readable summary.
`--output /evidence/new-cost-report.json` explicitly creates a NEW file; its parent
must already exist. Existing files are refused, never overwritten. All input
options are repeatable. No missing input is searched for or fetched. Avoid shell
output redirection onto an input: the shell can truncate it before Python runs.
Use `--output` for exclusive creation. The consumer is not a filesystem security
validator, provenance authenticator, full execution validator or PNG decoder.

Supported inputs are the producers' actual schemas:

- `--events`: `vtx.events/1` JSONL, parsed with replay disabled. Duplicate IDs,
  contradictory intervals, mixed runs/units are rejected by the existing reducer.
  Incomplete streams remain UNKNOWN. Each interval group exposes its own
  completeness and started/terminal/measured counts. Empty or incomplete totals
  are null; observed subtotals remain separately labelled. Counts cover parsed records.
- `--time-l`: one macOS `/usr/bin/time -l` `real … user … sys` line. Other text is
  ignored. Missing timing is UNOBSERVED; multiple matching lines are ambiguous.
- `--arm`: `render-pilot-arm/1`, including per-attempt stats, cleanup and embedded
  capture traces. Re-indexed identical attempt contents or duplicate embedded
  capture traces within one arm are refused before aggregation. Distinct failure
  and recovery traces remain separate. `continuous` and `diagnostic` stay separate. Target-map count,
  inclusive extrema and canonical fingerprint identify actual sampling scope.
  PNG global/local identities are validated as unique nonnegative integers and,
  when mapping exists, exact pairs must belong to it. Partial failed PNG
  inventories remain labelled subsets, not complete target coverage.
- `--capture`: `fast-png-pilot-v1`. Identical embedded traces are linked by their
  canonical JSON hash, never added again to arm totals. Capture calls include
  setup/probes; they are not target-frame counts. Operation durations may nest.
- `--pair`: `render-pilot-result/1`, preserving source commit/tree, plan hash,
  whole runner and supervised-process walls and reported cleanup/quality.
- `--quality`: `render-pilot-quality/1`. Frame equality is a source assertion;
  this consumer never independently re-decodes images or certifies full scenes.
- `--observer`: `perf-composite-observer/1`, or the verified
  `perf-composite-calibration/1` container's `observer_reports`. Calibration data
  is explicitly synthetic, never promoted to actual Mac/full-film observation.
  Missing summary maps/numbers stay missing and UNKNOWN. Known thread unions
  cannot exceed global union or wall; known thread/global category sums must
  agree. Each category inclusive cannot exceed its known calls times the window,
  global union, or corresponding thread union. Multiple/nested/parallel calls
  can still accumulate more inclusive work than wall. This checks necessary
  constraints without recreating raw spans.
- `--identity`: the explicit existing starting-identities object, allowing only
  known-format commit/tree/hash fields. It is separate provenance, not an
  automatically authenticated binding to another file.

Each source includes its explicit input path, byte size and SHA-256. Tool version,
Python version, consumer source hash and existing reducer hash are retained.
Producer hashes/browser versions are copied selectively; recorded filesystem
paths, URLs, environment values, errors and credentials are not forwarded.
The input filenames themselves are included; review them before sharing.

## Accounting boundaries

Internal all wall, external time-l real, runner wall, arm wall, attempt wall,
full render_page wall, renderer ms_total and capture-scope wall are different
measurements. They are not combined. Stage/wave/block/process interval unions are
elapsed coverage; work sums can overlap. Observer inclusive/exclusive categories
are producer summaries: raw spans are not exported, so their union cannot be
independently reconstructed here. Cross-thread sums are work-time, not wall.

Nav/export/decode/write sums are source successful-sample statistics. Failed
attempts and operation failures remain listed, including recovery counts and
available wall. There is no disjoint failed/recovery-frame timer, so its extra
wall stays unknown; nested failed operations are not summed into total overhead.
Export includes screenshot/transport/host work, not pure PNG encoding. Renderer
decode is the twoshot host slot, not all downstream PNG decoding. Missing fields
never become zero. `value: null`, status and reason distinguish missing coverage;
partial totals include an observed subtotal and expected/observed record counts.
Zero-record work totals are UNKNOWN, even when the recorded event count is zero.

PNG bytes sum explicit inventory records, not physical IO or base64-decoded
bytes. P50/P95 remain per attempt; no pooled percentile or FPS is reconstructed.
Known timing summaries must satisfy count and percentile/sum constraints.
With known count, NumPy linear p50/p95 imply nonnegative-sample sum lower bounds;
these necessary checks neither reconstruct samples nor certify all possible
summary combinations. Source summaries remain producer assertions.
Missing counts are not reconstructed; positive-count 0ms decode remains valid.
No fixed 20 ms is assigned per frame. Actual poll sleep, browser encoder-only
cost, process-tree peak, full-film effect and independent quality are UNKNOWN
or UNRUN. No cross-input A/B ratio or speedup verdict is produced. In particular,
possibly repeated runs/attempts in different source files remain separate reports,
never a shared total. Exact duplicate files within one input kind are refused.

## Ordinary offline tests

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest experiments.perf_cdp_cost.test_report -v
```

This named module tests ordinary JSON, interval overlap, missing/empty evidence,
failed/recovered attempts, duplicate identities, strict schema/numbers, and explicit
new-file output. It does not perform full test discovery or symlink, alias,
traversal, ancestor, redirection or other filesystem-security probes. These tests
validate the report implementation; they are not independent red-team approval,
new browser measurements, performance qualification or CI integration.
