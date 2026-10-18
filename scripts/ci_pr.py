#!/usr/bin/env python3
"""Curated PR regression, intentionally narrower than run_all_tests.sh.

The explicit modules retain CLI/contracts, real filesystem/process safety, and
CI negative controls. Full discovery remains the local default and runs in the
scheduled/manual CI matrix. A missing/empty module or any skipped test fails.
"""
import argparse
import importlib
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.ci_partition import (OriginLoader, PartitionError, RecordingResult,
                                  flatten, reconcile, unique)

# Deliberate coverage budget, not automatic discovery or the old fast partition.
# New modules enter full discovery immediately; adding PR coverage is reviewed.
PR_MODULES = (
    'tests.test_ci_checks',
    'tests.test_ci_partition',
    'tests.test_doc_gates',
    'tests.test_ci_pr',
    'tests.safety.test_cleanup_adversarial',
    'tests.safety.test_hygiene_safety',
    'tests.safety.test_plugin_paths_safety',
    'tests.safety.test_timeline_escape_safety',
    'tests.test_fstxn',
    'tests.test_cleanup_toctou',
    'tests.test_input_identity_guard',
    'tests.test_input_toctou_guard',
    'tests.test_proc_supervisor',
    'tests.test_resource_budget',
    'tests.test_vt_entry',
    'tests.test_fps_constraints',
    'tests.test_config_unified',
    'tests.test_protocol_v12',
    'tests.test_plugin_protocol_matrix',
    'tests.test_plugin_code_digest',
    'tests.test_plugin_compatibility',
    'tests.test_graph_digest',
    'tests.test_timeline_diff',
    'tests.test_execution_event_contract',
    'tests.test_version_sources',
    'apps.asr.tests.test_security_admission',
    'apps.research.tests.test_requests',
    'templates.hello-app.tests.test_hello',
)


def run_curated(names=PR_MODULES, stream=None):
    try:
        return _run_curated(names, stream)
    except SystemExit as error:
        # Import/load_tests/custom TestCase.run may exit(0) before any result.
        # Such an exit is never a successful gate. KeyboardInterrupt remains
        # uncaught so a real interruption cannot become success either.
        print('CI curated regression FAILED: unexpected SystemExit(%r)'
              % error.code, file=sys.stderr if stream is None else stream)
        return 1


def _run_curated(names, stream):
    stream = sys.stderr if stream is None else stream
    if not names or len(set(names)) != len(names):
        print('CI curated regression FAILED: empty or duplicate module list',
              file=stream)
        return 1
    loader = OriginLoader()
    suite = unittest.TestSuite()
    try:
        for name in names:
            selected = loader.loadTestsFromModule(importlib.import_module(name))
            if loader.errors or not selected.countTestCases():
                raise RuntimeError('missing tests or discovery errors: ' + name)
            print('Selected %s: %d tests' % (name, selected.countTestCases()),
                  file=stream)
            suite.addTest(selected)
        tests = list(flatten(suite))
        instances = {id(test): loader.origins[id(test)] + '::' + test.id()
                     for test in tests}
        selected_ids = unique((instances[id(test)] for test in tests),
                              'curated test instances')
    except (ImportError, AttributeError, RuntimeError) as error:
        print('CI curated regression FAILED: %s' % error, file=stream)
        return 1
    expected = suite.countTestCases()
    print('CI curated regression: %d tests; full regression NOT RUN by this '
          'command. Full coverage: scheduled/manual CI or run_all_tests.sh.'
          % expected, file=stream, flush=True)
    def recording_result(*args, **kwargs):
        return RecordingResult(*args, instances=instances, **kwargs)
    result = unittest.TextTestRunner(verbosity=2, stream=stream,
                                     resultclass=recording_result).run(suite)
    # Reuse the reviewed lifecycle/outcome contract in memory. A unittest OK
    # or matching testsRun count alone does not prove selected tests completed.
    request = dict(attempt_id='in-process', batch='curated',
                   selected=selected_ids, discovered=selected_ids)
    receipt = dict(request, records=result.records, suite_events=result.suite_events,
                   unittest_success=result.wasSuccessful(), testsRun=result.testsRun,
                   skipped=len(result.skipped), failures=len(result.failures),
                   errors=len(result.errors),
                   unexpectedSuccesses=len(result.unexpectedSuccesses),
                   expectedFailures=len(result.expectedFailures))
    try:
        reconcile(request, receipt, 0 if result.wasSuccessful() else 1, strict=True)
    except PartitionError as error:
        print('CI curated regression FAILED: %s' % error, file=stream)
        return 1
    return 0


def main(argv=None):
    argparse.ArgumentParser(description=__doc__).parse_args(argv)
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    return run_curated()


if __name__ == '__main__':
    sys.exit(main())
