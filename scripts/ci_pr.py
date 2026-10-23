#!/usr/bin/env python3
"""Standard-library PR smoke, intentionally narrower than run_all_tests.sh.

Only the explicit whole modules below run, with no third-party/media setup.
Full discovery and strict media checks run in scheduled/manual integration CI.
A missing/empty module, incomplete execution or any skipped test fails.
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
from scripts.ci_partition import (OriginLoader, PartitionError,
                                  flatten, run_reconciled_suite, unique)

# Deliberate smoke coverage, not automatic discovery or a latency guarantee.
# New modules enter full discovery; ordinary PR inclusion requires review.
PR_MODULES = (
    'tests.ci.test_ci_pr',
    'tests.distribution.test_version_sources',
    'tests.cache.test_graph_digest',
    'tests.plugins.test_config_unified',
)


def run_curated(names=PR_MODULES, stream=None):
    try:
        return _run_curated(names, stream)
    except SystemExit as error:
        # Import/load_tests/custom TestCase.run may exit(0) before any result.
        # Such an exit is never a successful gate. KeyboardInterrupt remains
        # uncaught so a real interruption cannot become success either.
        print('CI standard-library smoke FAILED: unexpected SystemExit(%r)'
              % error.code, file=sys.stderr if stream is None else stream)
        return 1


def _run_curated(names, stream):
    stream = sys.stderr if stream is None else stream
    if not names or len(set(names)) != len(names):
        print('CI standard-library smoke FAILED: empty or duplicate module list',
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
        print('CI standard-library smoke FAILED: %s' % error, file=stream)
        return 1
    expected = suite.countTestCases()
    print('CI standard-library smoke: %d tests; full regression NOT RUN by this '
          'command. Full coverage: scheduled/manual CI or run_all_tests.sh.'
          % expected, file=stream, flush=True)
    try:
        run_reconciled_suite(suite, instances, selected_ids, batch='smoke', stream=stream)
    except PartitionError as error:
        print('CI standard-library smoke FAILED: %s' % error, file=stream)
        return 1
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    return run_curated(PR_MODULES)


if __name__ == '__main__':
    sys.exit(main())
