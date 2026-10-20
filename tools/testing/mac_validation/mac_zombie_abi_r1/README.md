# Ordinary Mac zombie ABI fixture: source only

`mac_zombie_observation_fixture.py` has SHA-256 `c4b90fee936bd4714080b3cc1f8a461582f84bb4016018437104c1268b4d6777`. Its independent review is static only; no import, test execution, Mac call, or actual ABI qualification is claimed. Delivery does not authorize execution.

Before a single invocation, separately verify and approve the actual Mac interpreter, script, production checkout, working directory, output path, and command. Use the interpreter's `-B` flag. Any authorized invocation must explicitly supply `--repo`, `--out`, and `--deadline-seconds` of at most 10; the CLI default is not authorization to omit the frozen command value. The output parent must already exist and the output file must be new. No actual environment values or filesystem paths are supplied in this package.

The production checkout is PR226 commit `85fb352f9f95f9882222b2eacee2c887b520f495`, tree `7d499f03b734cb87c0b4286947b9fb60bd5d303c`. The script checks the source SHA-256 of `vtx/proc_supervisor.py` against `6f5c92471f4479b0bf4ba94a75cf079767b142598fc7aec04741a9031fd0ddd6`, verifies the imported module path, disables repository bytecode writes, and uses the existing loaded libproc/structure. It does not independently verify the entire checkout commit/tree; that remains a preflight requirement.

The inherited SIGCHLD disposition must already be default; the script reads it and does not change handlers. One self-created direct child immediately exits normally. The parent briefly delays its wait, queries only that child's structural ps row, compares BSDINFO `arg=0` and observation-only `arg=1`, then nonblockingly waits for the same child and checks its absence.

Expected normal evidence is one exclusive-created JSON record containing the known parent/child/group, query times, structural ps result, byte counts/errno, permitted kernel structural fields, normal reap result, and final child absence. PASS requires positive Z/SZOMB evidence, arg=0 zero-byte ESRCH, a full matching arg=1 structure, valid start timeval, successful normal exit/reap, subsequent empty ps exit 1, no observation/cleanup error, and in-budget wall duration. Missing or inconsistent evidence stays UNKNOWN.

There are no child/group signals, process-group/session changes, external target PIDs, producer commands, browsers, installations, or permission changes. A `subprocess.run` timeout may terminate and reap only its own ps helper; this is explicitly distinct from signaling the fixture child.

The ten-second budget is cooperative, begins before owner preflight/import, and cannot preempt a stuck kernel/API call. It is not an OS-enforced hard timeout and cannot guarantee `finally` after interpreter/OS termination. Startup/preflight or JSON/filesystem failures can leave no JSON record. An overdue, missing, unreaped, or failed result is never success; do not fabricate cleanup or automatically rerun. Any outer execution facility and its behavior must be reviewed separately.

Even a future PASS would establish only this ordinary lifecycle/ABI observation. It would not establish full sampler correctness, producer performance, pixel equivalence, resource-budget compliance, or production acceptance.
