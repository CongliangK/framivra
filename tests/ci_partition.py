"""CI-only conservative path classification and exhaustive core test layers.

Slow modules are the measured dry-run, composite re-render, and ledger stress
families. New tests in those modules stay slow; every other discovered test is
fast. Partitioning never changes the strict media selectors in ci_checks.py.
"""
import argparse
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SLOW_MODULES = (
    'tests.test_dry_run',
    'tests.test_composite_determinism',
    'tests.test_hygiene_ledger_txn',
)
# Cheap identity/invalidation contracts must run on every supported Python.
FAST_CLASSES = (
    'tests.test_composite_determinism.TestGraphCodeDigest',
    'tests.test_composite_determinism.TestPassFreshSidecar',
    'tests.test_composite_determinism.TestPassKeySubsSeparation',
    'tests.test_hygiene_ledger_txn.LedgerCorruptSemantics',
)


def leaves(suite):
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from leaves(test)
        else:
            yield test


def partition(suite, slow_modules=SLOW_MODULES, fast_classes=FAST_CLASSES):
    """Refuse empty/stale selectors, duplicate IDs, or discovery errors."""
    tests = list(leaves(suite))
    ids = [test.id() for test in tests]
    if not ids or len(ids) != len(set(ids)):
        raise RuntimeError('empty discovery or duplicate test IDs')
    if any(isinstance(test, unittest.loader._FailedTest) for test in tests):
        raise RuntimeError('test discovery failed; refusing partial inventory')
    for selector in slow_modules + fast_classes:
        if not any(name.startswith(selector + '.') for name in ids):
            raise RuntimeError('empty or stale selector: ' + selector)
    fast, slow = [], []
    for test in tests:
        name = test.id()
        is_slow = any(name.startswith(module + '.') for module in slow_modules)
        is_fast = any(name.startswith(case + '.') for case in fast_classes)
        (slow if is_slow and not is_fast else fast).append(test)
    if not fast or not slow:
        raise RuntimeError('empty test layer')
    # A total binary assignment over unique discovery IDs is disjoint/exhaustive.
    if len(fast) + len(slow) != len(tests):
        raise RuntimeError('partition lost tests')
    return fast, slow


def docs_only(paths):
    """Only Markdown in known documentation surfaces can lighten core work."""
    if not paths:
        return False
    for name in paths:
        path = PurePosixPath(name)
        if (path.is_absolute() or '..' in path.parts or '\\' in name or
                str(path) != name):
            return False
        if name in ('README.md', 'AGENTS.md'):
            continue
        if (len(path.parts) > 1 and path.parts[0] in ('docs', 'skill', 'skills')
                and path.suffix == '.md'):
            continue
        return False
    return True


def classify(root=ROOT, environ=None):
    env = os.environ if environ is None else environ
    event = env.get('GITHUB_EVENT_NAME')
    if event == 'pull_request':
        # Actions checks out the synthetic merge: its first parent is the base.
        parents = subprocess.check_output(
            ['git', 'rev-list', '--parents', '-n', '1', 'HEAD'], cwd=root,
            text=True).split()
        if len(parents) != 3:
            return 'full'
        base = parents[1]
    elif event == 'push':
        base = env.get('CI_BEFORE', '')
        if len(base) != 40 or any(c not in '0123456789abcdef' for c in base):
            return 'full'
        if base == '0' * 40:
            return 'full'
    else:
        return 'full'
    try:
        # --no-renames includes BOTH names for a rename as delete/add; NUL
        # parsing handles spaces/newlines without broadening the safe allowlist.
        data = subprocess.check_output(
            ['git', 'diff', '--name-only', '--no-renames', '-z', base, 'HEAD'],
            cwd=root, stderr=subprocess.DEVNULL)
        paths = data.decode('utf-8', errors='strict').split('\0')
        return 'docs' if docs_only([name for name in paths if name]) else 'full'
    except (subprocess.CalledProcessError, UnicodeError):
        return 'full'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('fast', 'slow', 'inventory', 'classify'))
    args = parser.parse_args(argv)
    if args.mode == 'classify':
        try:
            scope = classify()
        except (OSError, subprocess.CalledProcessError):
            scope = 'full'
        print('Core CI scope: ' + scope)
        with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as out:
            out.write('scope=' + scope + '\n')
        return 0
    sys.path.insert(0, str(ROOT))
    loader = unittest.TestLoader()
    suite = loader.discover(str(ROOT / 'tests'), top_level_dir=str(ROOT))
    try:
        fast, slow = partition(suite)
        if loader.errors:
            raise RuntimeError('test discovery errors: ' + '\n'.join(loader.errors))
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print('Core inventory: fast=%d slow=%d total=%d' %
          (len(fast), len(slow), len(fast) + len(slow)), flush=True)
    if args.mode == 'inventory':
        for label, tests in (('fast', fast), ('slow', slow)):
            for test in tests:
                print(label + '\t' + test.id())
        return 0
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.TestSuite(fast if args.mode == 'fast' else slow))
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    sys.exit(main())
