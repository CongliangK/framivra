"""Fail-closed path/layer contracts for the latency candidate (#87)."""
import contextlib
import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

import yaml
from tests import ci_partition as ci

ROOT = Path(__file__).resolve().parents[1]


class PartitionContract(unittest.TestCase):
    @staticmethod
    def fixture(names):
        tests = []
        for name in names:
            test = unittest.FunctionTestCase(lambda: None)
            test.id = lambda name=name: name
            tests.append(test)
        return unittest.TestSuite(tests)

    def test_union_disjoint_and_future_tests_cannot_disappear(self):
        names = ['slow.C.test_a', 'slow.Identity.test_x', 'fast.C.test_a',
                 'new_module.C.test_new', 'slow.NewClass.test_new']
        fast, slow = ci.partition(self.fixture(names), ('slow',), ('slow.Identity',))
        f, s = {t.id() for t in fast}, {t.id() for t in slow}
        self.assertFalse(f & s)
        self.assertEqual(f | s, set(names))
        self.assertIn('slow.Identity.test_x', f)
        self.assertIn('new_module.C.test_new', f)
        self.assertIn('slow.NewClass.test_new', s)

    def test_empty_stale_duplicate_and_single_layer_fail(self):
        for names, modules, exceptions in [
            ([], ('slow',), ()),
            (['fast.C.test_a'], ('missing',), ()),
            (['slow.C.test_a', 'slow.C.test_a'], ('slow',), ()),
            (['slow.C.test_a'], ('slow',), ()),
            (['slow.C.test_a', 'fast.C.test_a'], ('slow',), ('absent.Identity',)),
        ]:
            with self.subTest(names=names), self.assertRaises(RuntimeError):
                ci.partition(self.fixture(names), modules, exceptions)

    def test_import_error_fails_before_partial_selection(self):
        loader = unittest.TestLoader()
        suite = loader.loadTestsFromName('tests.this_module_does_not_exist')
        with self.assertRaisesRegex(RuntimeError, 'discovery failed'):
            ci.partition(suite, (), ())

    def test_actual_inventory_selectors_exist_and_partition_every_discovered_test(self):
        loader = unittest.TestLoader()
        suite = loader.discover(str(ROOT / 'tests'), top_level_dir=str(ROOT))
        self.assertFalse(loader.errors)
        all_tests = list(ci.leaves(suite))
        fast, slow = ci.partition(unittest.TestSuite(all_tests))
        f, s = {t.id() for t in fast}, {t.id() for t in slow}
        self.assertFalse(f & s)
        self.assertEqual(f | s, {t.id() for t in all_tests})
        for name in f | s:
            if any(name.startswith(case + '.') for case in ci.FAST_CLASSES):
                self.assertIn(name, f)


class PathClassification(unittest.TestCase):
    def test_required_path_fixtures(self):
        fixtures = [
            (['README.md', 'docs/guide.md', 'AGENTS.md', 'skill/SKILL.md'], True),
            (['skills/tech-talk/template.html'], False),
            (['vtx/core.py'], False),
            (['README.md', 'vtx/core.py'], False),
            (['skill/example.js'], False),
            (['skills/assets/picture.png'], False),
            (['.github/workflows/ci.yml'], False),
            ([], False),
            (['new.md'], False),
            (['docs/../vtx/core.md'], False),
            (['/docs/a.md'], False),
            (['docs//a.md'], False),
            (['docs\\a.md'], False),
        ]
        for paths, expected in fixtures:
            with self.subTest(paths=paths):
                self.assertEqual(ci.docs_only(paths), expected)

    def test_schedule_manual_unknown_and_bad_push_are_full(self):
        for env in ({}, {'GITHUB_EVENT_NAME': 'schedule'},
                    {'GITHUB_EVENT_NAME': 'workflow_dispatch'},
                    {'GITHUB_EVENT_NAME': 'push', 'CI_BEFORE': '0' * 40},
                    {'GITHUB_EVENT_NAME': 'push', 'CI_BEFORE': '--bad'}):
            with self.subTest(env=env):
                self.assertEqual(ci.classify(environ=env), 'full')

    def test_missing_history_and_invalid_utf8_fail_closed(self):
        env = {'GITHUB_EVENT_NAME': 'push', 'CI_BEFORE': 'a' * 40}
        for effect in (subprocess.CalledProcessError(128, 'git'), b'docs/\xff.md\0'):
            with mock.patch.object(ci.subprocess, 'check_output') as call:
                if isinstance(effect, Exception):
                    call.side_effect = effect
                else:
                    call.return_value = effect
                self.assertEqual(ci.classify(environ=env), 'full')

    def test_rename_old_code_path_is_not_lost(self):
        with tempfile.TemporaryDirectory() as directory:
            def git(*args):
                return subprocess.check_output(['git', '-C', directory, *args],
                                               stderr=subprocess.DEVNULL).decode().strip()
            git('init'); git('config', 'user.email', 'ci@example.invalid')
            git('config', 'user.name', 'CI Fixture')
            root = Path(directory)
            (root / 'code.py').write_text('example\n')
            git('add', '.'); git('commit', '-m', 'base')
            base = git('rev-parse', 'HEAD')
            (root / 'docs').mkdir()
            git('mv', 'code.py', 'docs/code.md'); git('commit', '-m', 'rename')
            env = {'GITHUB_EVENT_NAME': 'push', 'CI_BEFORE': base}
            self.assertEqual(ci.classify(root, env), 'full')
            # A real docs-only push is eligible; shallow/missing base is not.
            base = git('rev-parse', 'HEAD')
            (root / 'README.md').write_text('documentation\n')
            git('add', '.'); git('commit', '-m', 'docs')
            env['CI_BEFORE'] = base
            self.assertEqual(ci.classify(root, env), 'docs')
            self.assertEqual(ci.classify(root, {'GITHUB_EVENT_NAME': 'pull_request'}), 'full')


class RunnerAggregate(unittest.TestCase):
    def test_both_layers_run_and_either_failure_fails(self):
        # The fake interpreter records the actual shell runner's dispatch. No
        # fixture mutates the checkout or needs CPU media.
        for fast_status, slow_status in ((0, 0), (1, 0), (0, 1), (1, 2)):
            with self.subTest(fast=fast_status, slow=slow_status), \
                    tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                fake = root / 'python3'
                fake.write_text('#!/bin/bash\n'
                                'echo "$*" >> "$CALLS"\n'
                                'if [[ "$1" == tests/ci_partition.py ]]; then\n'
                                '  if [[ "$2" == fast ]]; then exit "$FAST_STATUS"; fi\n'
                                '  if [[ "$2" == slow ]]; then exit "$SLOW_STATUS"; fi\n'
                                'fi\nexit 0\n')
                fake.chmod(0o755)
                calls = root / 'calls'
                env = dict(os.environ, PATH=str(root) + os.pathsep + os.environ['PATH'],
                           CALLS=str(calls), FAST_STATUS=str(fast_status),
                           SLOW_STATUS=str(slow_status))
                proc = subprocess.run(['bash', str(ROOT / 'scripts/run_all_tests.sh'),
                                       '--layered'], env=env, capture_output=True)
                entries = calls.read_text().splitlines()
                self.assertEqual(entries.count('tests/ci_partition.py fast'), 1)
                self.assertEqual(entries.count('tests/ci_partition.py slow'), 1)
                self.assertEqual(len(entries), len(set(entries)))
                self.assertEqual(proc.returncode == 0, fast_status == slow_status == 0)
                self.assertEqual(entries[-1], 'tests/ci_partition.py slow')

    def test_unknown_runner_options_fail(self):
        proc = subprocess.run(['bash', str(ROOT / 'scripts/run_all_tests.sh'), '--fats'],
                              capture_output=True)
        self.assertEqual(proc.returncode, 2)


class WorkflowLatencyContract(unittest.TestCase):
    def test_existing_contexts_permissions_and_strict_lanes_preserved(self):
        workflow = yaml.load((ROOT / '.github/workflows/ci.yml').read_text(),
                             Loader=yaml.BaseLoader)
        self.assertEqual(set(workflow['jobs']),
                         {'core', 'contracts', 'ffmpeg', 'chrome', 'windows-install'})
        self.assertEqual(workflow['permissions'], {'contents': 'read'})
        self.assertFalse(workflow['on']['pull_request'])
        core = workflow['jobs']['core']
        self.assertEqual(core['name'], 'Core Python ${{ matrix.python }}')
        self.assertEqual(core['strategy']['matrix']['python'],
                         ['3.9', '3.10', '3.11', '3.12', '3.13'])
        commands = '\n'.join(step.get('run', '') for step in core['steps'])
        self.assertIn('"$GITHUB_EVENT_NAME" == schedule', commands)
        self.assertIn('"$GITHUB_EVENT_NAME" == workflow_dispatch', commands)
        self.assertIn('"$CI_PYTHON" == 3.13', commands)
        for mode in ('--all', '--fast', '--layered'):
            self.assertIn('bash scripts/run_all_tests.sh ' + mode, commands)
        for kind in ('ffmpeg', 'chrome'):
            job = workflow['jobs'][kind]
            self.assertNotIn('if', job)
            gate = [s for s in job['steps'] if
                    s.get('run') == 'python scripts/ci_checks.py ' + kind]
            self.assertEqual(len(gate), 1)
            self.assertNotIn('if', gate[0])
        setup = [s for s in core['steps'] if
                 s.get('uses', '').startswith('actions/setup-python@')][0]
        self.assertEqual(setup['with']['cache'], 'pip')
        self.assertEqual(setup['with']['cache-dependency-path'], '.github/workflows/ci.yml')
        scope = [s for s in core['steps'] if s.get('id') == 'scope'][0]
        self.assertNotIn('if', scope)
        for s in core['steps']:
            if 'if' in s:
                self.assertIn(s['if'], ("steps.scope.outputs.scope != 'docs'",
                                       "steps.scope.outputs.scope == 'docs'"))


if __name__ == '__main__':
    unittest.main()
