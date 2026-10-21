# Agent diagnostics

The optional module entry point invokes the existing `Ctx`, stage dependency
checks, and validate/audit/QC implementations. It does not maintain a second
validator and does not apply the production JSON Schema as a new runtime gate.
The usual CLI and its text, severity, exit codes and receipt behavior remain
unchanged. Runtime small-canvas fixtures remain valid; unknown keys remain
warnings.

Read-only usage discovery:

```bash
python3 -m vtx.agent_diagnostics --help
```

The interface accepts `-t TIMELINE`, one of `validate`, `audit`, or `qc`, and
`--format agent-json` (default: `text`). QC also accepts `--strict`. Running a
stage is **writeful**: it retains normal prerequisite checks, downstream receipt
invalidation, reports and receipts. It does not provide a bypass for missing
mux receipts or unsafe output paths. It never edits the input timeline.

## JSON contract

Standard output is exactly one JSON object, including loading/probe failures.
It contains `schema_version: 1`, `command`, `exit_code`, `diagnostics`, and
`legacy_output` with captured stdout/stderr. Captured text is for inspection;
it is never parsed to infer diagnostics. JSON numbers are finite; unavailable
or non-finite values are null, never invented zero measurements. Output uses
ASCII JSON escapes for all non-ASCII characters, including lone surrogates from
escaped input strings. Parsing the JSON preserves those strings exactly; this
does not certify them as valid filenames or Unicode scalar values.

Each diagnostic carries:

- `json_path`: original timeline location, preserving array indices before sorting.
- `rule_id`: stable producer/rule identifier.
- `measured` and `threshold`: actual evidence and predicate constraints; numeric
  quantities carry explicit units. They can be null when not known.
- `severity`: error, warning, or info, preserving existing runtime severity.
- `status`: fail, warn, pass, info, skipped, or unknown. Unknown means evidence
  could not be established, not successful validation.
- `suggested_actions`: `{op, params}` proposals. These are reviewable suggestions,
  not authority to change creative choices, install plugins or overwrite media.
- `message`: original producer text.

External subtitles use `$.subtitles_src` plus `source` containing the file,
format, original index and (where available) source JSONPath or SRT line.
Arbitrary keys use quoted bracket notation. Opaque third-party plugin strings
remain readable and receive an `unknown` rule without fabricated measurements.
Unexpected runtime exceptions likewise report unknown, rather than pretending
that a schema rule was evaluated. Ordinary callback exceptions and nonnumeric
`SystemExit` failures also produce one JSON document; `KeyboardInterrupt` still
propagates. An unexpected interruption is recorded even after earlier rule
errors, with `measured.interrupted: true`. Handled producer failures are tracked
by their exact exception, rather than by the presence of any earlier error.

Required-field and iterable diagnostics retain Python's existing acceptance
behavior, including empty-object collections. Native-operation diagnostics
observe the actual executed access, arithmetic, comparison, sort or conversion;
they retain its exception type/message and original location. A failed operation
is not a completed bounds predicate. Its `review_operand` action asks for review
of the operation's inputs rather than guessing which operand should change.
Short-circuit evaluation, original exceptions and existing validation acceptance
are preserved. There is no second validation pass or exception traceback scan.

Cache dependency predicates emit at their actual rejection. Fingerprint accesses,
path construction and encoding carry the original source/clip/dependency origin;
hash bytes, ordering and resource reads are unchanged. Actual I/O or plugin
callback failures remain unknown, even when a later input is malformed. No
filesystem check, validator or fingerprint is replayed to locate an error.
Provider metadata operations carry provider/resource/field context and unknown
status: a malformed ffprobe response does not prove an invalid timeline field.
Existing probe fallback paths remain fallbacks, not new hard gates. Prerequisite
findings still originate at the actual missing-prerequisite predicate.
Finite JSON serialization does not imply every nonfinite input is rejected.

Validation failures and QC failures exit 1. Audit findings are advisory and exit
0; invalid inputs or execution failures can still prevent the audit from
running. QC's existing loudness warning band stays a successful check. Cached
probe exemptions are skipped evidence, not fresh measurements. Missing
measurements are unknown and retain the original failure behavior. The current
QC checks, even when all pass, do not certify semantic visual correctness.

## Coverage and reproducible fixtures

Reached producer annotations cover existing validate branches, plugin-key scanning,
three bundled validators, subtitle source resolution, loading/fps/dependency
and probe failures (with opaque I/O/callback failures reported as unknown), individual audit CPS/duration/overlap/voice/gap findings,
and actual QC metadata, frame comparisons, LUFS, true peak and audio energy.

`tests/diagnostics/test_agent_diagnostics*.py` maps rules to emitted evidence and checks
original indices, strict JSON, error exits, source mapping, prerequisite/path
protection and legacy output compatibility. CPU tests are registered in the
existing fail-closed media inventory. Real FFmpeg fixtures render through the
normal stages before exercising successful and deliberately damaged QC output;
no successful receipt is fabricated.

The public fixture `tests/fixtures/agent-diagnostics/timeline.invalid.json`
contains seven distinct violations, including missing resources.
`tests/diagnostics/test_agent_diagnostics_repair.py` copies only that exact fixture to a
new temporary directory, consumes its suggested operations through an explicit
allowlist, creates deterministic synthetic resources and reruns the real
validator to green. The repair implementation is test-only, cannot accept a
user timeline, and is not a production auto-editor.

### Attribution coverage boundary

- Reached validation predicates: retain precise rule, original index, measurement,
  constraint and suggested action; an ordinary producer failure is handled.
- Direct required-field/iterable access and other annotated loading operations:
  retain precise diagnostics for the exact exception raised by that operation.
- Native preparation operations: retain operation-level evidence without inventing a
  completed validation or QC finding. Provider failures remain identified as provider
  evidence rather than a guessed input defect.
- Opaque I/O/callback interruptions: retain the original exception as unknown at
  `$`; a later malformed field is not evidence of the cause. Validation is not
  rerun after loading fails.

The structural regression matrix distinguishes these categories explicitly.
Unknown opaque failures are not counted as precise hard-gate coverage. Full
Issue #100 acceptance requires the source-operation catalog, independent review,
repair-to-green fixture and relevant aggregate results on the exact candidate. A changed-file count is not a diagnostic-path inventory.
