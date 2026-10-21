# Test suites

Run all unit-test discovery from the repository root:

```sh
python3 -m unittest discover -s tests -t . -v
```

Run one domain with `python3 -m unittest discover -s tests/<domain> -t . -v`;
individual module, class, and method IDs remain supported. Domains are:

- `media`: audio, compositing, subtitles, frame timing, and encoding.
- `cache`: cache identity, filesystem transactions, cleanup, and input safety.
- `execution`: pipeline scheduling, execution events, process supervision, recovery, and resource budgets.
- `plugins`: configuration, plugin protocols, discovery, and component health.
- `distribution`: installation, release, licensing, packaging, templates, and CLI entrypoints.
- `contracts`: documentation gates and demo fixture contracts.
- `ci`: test selection, strict media gates, and PR job contracts.
- `acceptance`: acceptance receipts and impact evidence contracts.
- `gfx`, `qc`, `diagnostics`, `safety`: graphics, quality checks, diagnostics, and safety regressions.

`runners/` contains explicit acceptance/benchmark commands, not another unit-test
framework. Run a runner as `python3 -m tests.runners.<name> --help` when that
runner provides help; its own documented arguments and environment still apply.
`fixtures/`, `doc_gates_golden/`, and other named evidence directories retain their
original data and ownership. `_support.py` contains shared fixtures, not test cases.

Ordinary discovery can report optional integration skips; it does not replace
`bash scripts/run_all_tests.sh`, strict gates in `scripts/ci_checks.py`, or the
platform-specific acceptance evidence required by their documented contracts.
