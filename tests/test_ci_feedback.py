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


class MandatorySlowExecution(unittest.TestCase):
    def run_cases(self, case, mandatory=True):
        tests = list(unittest.defaultTestLoader.loadTestsFromTestCase(case))
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            return ci.run_layer(tests, mandatory=mandatory, stream=io.StringIO())

    def test_success_and_ordinary_failures_keep_unittest_semantics(self):
        class Good(unittest.TestCase):
            def test_body(self):
                pass
        class Bad(unittest.TestCase):
            def test_body(self):
                self.fail('intentional')
        class Error(unittest.TestCase):
            def test_body(self):
                raise RuntimeError('intentional')
        self.assertEqual(self.run_cases(Good), 0)
        self.assertEqual(self.run_cases(Bad), 1)
        self.assertEqual(self.run_cases(Error), 1)

    def test_skipped_method_subtest_and_class_are_failures(self):
        class Method(unittest.TestCase):
            @unittest.skip('intentional')
            def test_body(self):
                pass
        class Class(unittest.TestCase):
            @classmethod
            def setUpClass(cls):
                raise unittest.SkipTest('class skips all bodies')
            def test_body(self):
                self.fail('must not run')
        class Subtest(unittest.TestCase):
            def test_body(self):
                with self.subTest():
                    self.skipTest('subtest skip')
        for case in (Method, Class, Subtest):
            with self.subTest(case=case):
                self.assertEqual(self.run_cases(case), 1)
                self.assertEqual(self.run_cases(case, mandatory=False), 0)

    def test_reviewer_reproduction_main_rejects_all_class_skips(self):
        class Skipped(unittest.TestCase):
            @classmethod
            def setUpClass(cls):
                raise unittest.SkipTest('mandatory fixture never executes')
            def test_body(self):
                self.fail('must not run')
        names = [selector + '.test_body' for selector in ci.SLOW_SELECTORS]
        names += [case + '.test_body' for case in ci.FAST_CLASSES]
        tests = []
        for name in names:
            test = Skipped('test_body')
            test.id = lambda name=name: name
            tests.append(test)
        with mock.patch.object(unittest.TestLoader, 'discover',
                               return_value=unittest.TestSuite(tests)), \
                contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(ci.main(['slow']), 1)

    def test_discovered_leaf_that_never_executes_fails(self):
        class NeverRun(unittest.TestCase):
            def test_body(self):
                pass
            def run(self, result=None):
                return result
        self.assertEqual(self.run_cases(NeverRun), 1)

    def test_duplicate_execution_is_rejected(self):
        class Twice(unittest.TestCase):
            def test_body(self):
                pass
            def run(self, result=None):
                super().run(result)
                return super().run(result)
        self.assertEqual(self.run_cases(Twice), 1)

    def test_empty_and_duplicate_selected_leaves_are_rejected(self):
        test = unittest.FunctionTestCase(lambda: None)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(ci.run_layer([], mandatory=True), 1)
            self.assertEqual(ci.run_layer([test, test], mandatory=True), 1)

    def test_hygiene_cheap_cases_remain_fast(self):
        names = [
            'tests.test_hygiene_ledger_txn.CrashSafety.test_kill_after_replace_leaves_new_value',
            'tests.test_hygiene_ledger_txn.GcResidueAndConcurrency.test_gc_plan_does_not_list_ledger_or_lock',
            'tests.test_hygiene_ledger_txn.GcResidueAndConcurrency.test_worker_fixture_creation_survives_gc_between_mkdir_and_write',
        ]
        for name in names:
            self.assertFalse(any(ci.matches(name, selector) for selector in ci.SLOW_SELECTORS))



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
    def test_actual_core_shell_routes_fast_and_weekly_full(self):
        workflow = yaml.load((ROOT / '.github/workflows/ci.yml').read_text(),
                             Loader=yaml.BaseLoader)
        step = [s for s in workflow['jobs']['core']['steps']
                if s.get('name') == 'Run kernel, app, plugin and example unittest batches'][0]
        self.assertEqual(step['if'],
                         "steps.scope.outputs.scope != 'docs'")
        cases = [(event, 'full', version,
                  '--all' if event in ('schedule', 'workflow_dispatch') else '--fast')
                 for event in ('pull_request', 'push', 'schedule', 'workflow_dispatch')
                 for version in ('3.9', '3.10', '3.11', '3.12', '3.13')]
        for event, scope, version, mode in cases:
            with self.subTest(event=event, scope=scope, version=version), \
                    tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                fake = root / 'bash'
                fake.write_text('#!/bin/sh\nprintf "%s\\n" "$*" > "$CALLS"\n')
                fake.chmod(0o755)
                calls = root / 'calls'
                env = dict(os.environ, PATH=directory + os.pathsep + os.environ['PATH'],
                           CALLS=str(calls), RUNNER_TEMP=directory,
                           GITHUB_EVENT_NAME=event, CI_SCOPE=scope, CI_PYTHON=version)
                proc = subprocess.run(['/bin/bash', '-e', '-c', step['run']], env=env,
                                      capture_output=True, text=True)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(calls.read_text().strip(), 'scripts/run_all_tests.sh ' + mode)

    def test_separate_slow_runs_even_for_docs_or_failed_fast_jobs(self):
        workflow = yaml.load((ROOT / '.github/workflows/ci.yml').read_text(),
                             Loader=yaml.BaseLoader)
        job = workflow['jobs']['slow-integration']
        self.assertEqual(job['name'], 'Slow integration Python 3.13 (no skips)')
        self.assertNotIn('needs', job)
        self.assertNotIn('if', job)
        self.assertNotIn('continue-on-error', job)
        gate = [s for s in job['steps'] if
                'bash scripts/run_all_tests.sh --slow' in s.get('run', '')]
        self.assertEqual(len(gate), 1)
        self.assertNotIn('if', gate[0])
        self.assertNotIn('continue-on-error', gate[0])
        # All workflow events include this same unconditional job. In particular
        # neither docs classification nor another job failure can remove it.
        self.assertEqual(set(workflow['on']),
                         {'pull_request', 'push', 'schedule', 'workflow_dispatch'})
        self.assertEqual(len(workflow['jobs']) - 1 + 5, 10)
        self.assertEqual(workflow['jobs']['core']['strategy']['fail-fast'], 'false')

    def test_actual_slow_shell_failure_is_fatal_on_every_event(self):
        workflow = yaml.load((ROOT / '.github/workflows/ci.yml').read_text(),
                             Loader=yaml.BaseLoader)
        step = [s for s in workflow['jobs']['slow-integration']['steps']
                if 'bash scripts/run_all_tests.sh --slow' in s.get('run', '')][0]
        for event in ('pull_request', 'push', 'schedule', 'workflow_dispatch'):
            for status in (0, 1, 2):
                with self.subTest(event=event, status=status), \
                        tempfile.TemporaryDirectory() as directory:
                    fake = Path(directory) / 'bash'
                    fake.write_text('#!/bin/sh\nprintf "%s\\n" "$*" > "$CALLS"\nexit "$STATUS"\n')
                    fake.chmod(0o755)
                    calls = Path(directory) / 'calls'
                    env = dict(os.environ, PATH=directory + os.pathsep + os.environ['PATH'],
                               RUNNER_TEMP=directory, CALLS=str(calls), STATUS=str(status),
                               GITHUB_EVENT_NAME=event, CI_SCOPE='docs')
                    proc = subprocess.run(['/bin/bash', '-e', '-c', step['run']],
                                          env=env, capture_output=True, text=True)
                    self.assertEqual(proc.returncode, status, proc.stderr)
                    self.assertEqual(calls.read_text().strip(), 'scripts/run_all_tests.sh --slow')

    def test_cache_failure_cannot_mask_install_or_media_failure(self):
        workflow = yaml.load((ROOT / '.github/workflows/ci.yml').read_text(),
                             Loader=yaml.BaseLoader)
        pin = '55cc8345863c7cc4c66a329aec7e433d2d1c52a9'
        for name in ('core', 'slow-integration', 'ffmpeg', 'chrome'):
            job = workflow['jobs'][name]
            steps = job['steps']
            restore = [s for s in steps if s.get('id') == 'apt-restore'][0]
            install = [s for s in steps if s.get('id') == 'apt-install'][0]
            save = [s for s in steps if s.get('uses', '').startswith('actions/cache/save@')][0]
            self.assertEqual(restore['uses'], 'actions/cache/restore@' + pin)
            self.assertEqual(save['uses'], 'actions/cache/save@' + pin)
            for cache in (restore, save):
                self.assertEqual(cache['continue-on-error'], 'true')
                self.assertNotIn('restore-keys', cache['with'])
                self.assertEqual(cache['with']['key'], '${{ steps.apt-key.outputs.key }}')
                self.assertEqual(cache['with']['path'], '${{ steps.apt-key.outputs.path }}')
            self.assertIn("steps.apt-key.outputs.cacheable == 'true'", restore['if'])
            self.assertIn("steps.apt-install.outputs.cacheable == 'true'", save['if'])
            self.assertEqual(install['run'], 'python scripts/ci_apt_cache.py install')
            self.assertNotIn('continue-on-error', install)
            self.assertNotIn('cache-hit', install.get('if', ''))
            if name == 'core':
                self.assertEqual(install['if'], "steps.scope.outputs.scope != 'docs'")
            else:
                self.assertNotIn('if', install)
            self.assertLess(steps.index(restore), steps.index(install))
            self.assertLess(steps.index(install), steps.index(save))

    def test_existing_contexts_permissions_and_strict_lanes_preserved(self):
        workflow = yaml.load((ROOT / '.github/workflows/ci.yml').read_text(),
                             Loader=yaml.BaseLoader)
        self.assertEqual(set(workflow['jobs']),
                         {'core', 'slow-integration', 'contracts', 'ffmpeg', 'chrome', 'windows-install'})
        self.assertEqual(workflow['permissions'], {'contents': 'read'})
        names = {job['name'] for key, job in workflow['jobs'].items() if key != 'core'}
        names.update('Core Python ' + version for version in
                     workflow['jobs']['core']['strategy']['matrix']['python'])
        self.assertEqual(names, {
            'Core Python 3.9', 'Core Python 3.10', 'Core Python 3.11',
            'Core Python 3.12', 'Core Python 3.13',
            'Documentation and static syntax', 'FFmpeg CPU integration (no skips)',
            'Chrome and pipeline CPU integration (no skips)',
            'Windows one-command install candidate',
            'Slow integration Python 3.13 (no skips)',
        })
        self.assertFalse(workflow['on']['pull_request'])
        core = workflow['jobs']['core']
        self.assertEqual(core['name'], 'Core Python ${{ matrix.python }}')
        self.assertEqual(core['strategy']['matrix']['python'],
                         ['3.9', '3.10', '3.11', '3.12', '3.13'])
        commands = '\n'.join(step.get('run', '') for step in core['steps'])
        self.assertIn('"$GITHUB_EVENT_NAME" == schedule', commands)
        self.assertIn('"$GITHUB_EVENT_NAME" == workflow_dispatch', commands)
        self.assertNotIn('--layered', commands)
        self.assertNotIn('--slow', commands)
        for mode in ('--all', '--fast'):
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
            if 'if' in s and not s.get('uses', '').startswith('actions/cache/'):
                self.assertIn(s['if'], ("steps.scope.outputs.scope != 'docs'",
                                       "steps.scope.outputs.scope == 'docs'"))


if __name__ == '__main__':
    unittest.main()
