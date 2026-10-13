"""CI contract regressions, including fail-closed integration skip handling."""
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import re
import shlex
import sys
import unittest
from unittest import mock

import yaml  # test-only dependency, installed by the CI core matrix

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('ci_checks', ROOT / 'scripts/ci_checks.py')
ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci)


def tests_of(case):
    return list(unittest.defaultTestLoader.loadTestsFromTestCase(case))


class StrictMediaGate(unittest.TestCase):
    def suite_exit(self, by_name, names=('fixture',), runner_spy=None):
        """by_name maps each selector to the suite discovery must return."""
        real_load = unittest.TestLoader.loadTestsFromNames

        def fake_load(selected, *args, **kwargs):
            mocked = [name for name in selected if name in by_name]
            if len(mocked) != len(selected):
                return real_load(unittest.defaultTestLoader, selected)
            return unittest.TestSuite(
                test for name in selected for test in by_name[name])

        patches = [mock.patch.object(unittest.TestLoader, 'loadTestsFromNames',
                                     side_effect=fake_load),
                   contextlib.redirect_stderr(io.StringIO())]
        if runner_spy is not None:
            patches.append(mock.patch.object(ci.unittest, 'TextTestRunner',
                                             side_effect=runner_spy))
        with contextlib.ExitStack() as stack:
            for patch in patches:
                stack.enter_context(patch)
            return ci.strict_suite(list(names), stream=io.StringIO())

    @staticmethod
    def healthy_suite(ran=None):
        class Case(unittest.TestCase):
            def test_ok(self):
                if ran is not None:
                    ran.append(True)
        return tests_of(Case)

    def test_success_executes(self):
        self.assertEqual(self.suite_exit({'fixture': self.healthy_suite()}), 0)

    def test_skip_fails_even_when_unittest_was_successful(self):
        class Case(unittest.TestCase):
            @unittest.skip('tool unavailable')
            def test_skipped(self):
                pass
        self.assertEqual(self.suite_exit({'fixture': tests_of(Case)}), 1)

    def test_empty_suite_fails(self):
        self.assertEqual(self.suite_exit({'fixture': []}), 1)

    def test_test_error_fails(self):
        class Case(unittest.TestCase):
            def test_broken(self):
                raise RuntimeError('intentional')
        self.assertEqual(self.suite_exit({'fixture': tests_of(Case)}), 1)

    def test_empty_selector_alongside_healthy_is_refused_either_order(self):
        for names in (['empty', 'healthy'], ['healthy', 'empty']):
            ran = []
            by_name = {'empty': [], 'healthy': self.healthy_suite(ran)}
            spy = mock.Mock()
            with self.subTest(order=names):
                self.assertEqual(
                    self.suite_exit(by_name, names=names, runner_spy=spy), 1)
                # Refusal happens before the runner: no remaining body runs.
                self.assertEqual(ran, [])
                spy.assert_not_called()

    def test_empty_selector_message_names_the_selector(self):
        stderr = io.StringIO()
        with mock.patch.object(unittest.TestLoader, 'loadTestsFromNames',
                               return_value=unittest.TestSuite()), \
                contextlib.redirect_stderr(stderr):
            code = ci.strict_suite(['tests.test_missing.Selector'],
                                   stream=io.StringIO())
        self.assertEqual(code, 1)
        self.assertIn('tests.test_missing.Selector', stderr.getvalue())

    def test_real_range_class_emptied_in_memory_is_refused_with_healthy(self):
        sys.path.insert(0, str(ROOT))
        import tests.test_range_render as range_tests
        target = range_tests.TestRangeIntegration
        original = target.__dict__['test_range_render']
        healthy = self.healthy_suite()
        delattr(target, 'test_range_render')
        try:
            for names in (['tests.test_range_render.TestRangeIntegration',
                           'fixture'],
                          ['fixture',
                           'tests.test_range_render.TestRangeIntegration']):
                with self.subTest(order=names):
                    self.assertEqual(
                        self.suite_exit({'fixture': healthy}, names=names), 1)
        finally:
            setattr(target, 'test_range_render', original)

    def test_multiple_healthy_selectors_pass(self):
        class Second(unittest.TestCase):
            def test_other(self):
                pass
        by_name = {'first': self.healthy_suite(),
                   'second': tests_of(Second)}
        self.assertEqual(self.suite_exit(by_name, names=('first', 'second')), 0)

    def test_missing_executable_is_not_a_skip(self):
        with mock.patch.object(ci.shutil, 'which', return_value=None):
            with self.assertRaisesRegex(RuntimeError, 'missing'):
                ci.executable('ffmpeg')

    def test_preflight_failure_prevents_discovery(self):
        with mock.patch.object(ci, 'prepare_media', side_effect=RuntimeError('missing')), \
                mock.patch.object(ci, 'strict_suite') as suite, \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(ci.main(['chrome']), 1)
            suite.assert_not_called()

    def test_flags_enabled_before_test_import(self):
        with mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(ci, 'executable', side_effect=lambda n, override=None: '/bin/' + n), \
                mock.patch.object(ci, 'run'):
            ci.prepare_media('ffmpeg')
            self.assertEqual(os.environ['VTX_RANGE_INTEG'], '1')
            self.assertEqual(os.environ['VTX_AUDIO_REPORT_INTEG'], '1')
            self.assertEqual(os.environ['VTX_NO_NVENC'], '1')


class WorkflowContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = (ROOT / '.github/workflows/ci.yml').read_text()
        # BaseLoader avoids YAML 1.1 treating GitHub's "on" as a boolean.
        cls.workflow = yaml.load(cls.text, Loader=yaml.BaseLoader)

    def test_events_and_least_privilege(self):
        self.assertTrue({'pull_request', 'push', 'workflow_dispatch', 'schedule'} <=
                        self.workflow['on'].keys())
        self.assertEqual(self.workflow['permissions'], {'contents': 'read'})
        self.assertNotIn('pull_request_target', self.workflow['on'])

    def test_declared_supported_python_versions_are_tested(self):
        pyproject = (ROOT / 'pyproject.toml').read_text()
        declared = set(re.findall(r'Programming Language :: Python :: (3\.\d+)', pyproject))
        matrix = set(self.workflow['jobs']['core']['strategy']['matrix']['python'])
        self.assertEqual(matrix, declared)
        self.assertIn('3.9', matrix)

    def test_core_declares_dependencies_for_all_unittest_batches(self):
        # The app batches are mandatory too: imgen's mock gen/batch chains
        # import requests even though no real image service is contacted.
        installs = [step['run'] for step in self.workflow['jobs']['core']['steps']
                    if 'run' in step and 'python -m pip install ' in step['run']]
        self.assertEqual(len(installs), 1)
        argv = shlex.split(installs[0])
        self.assertEqual(argv[:4], ['python', '-m', 'pip', 'install'])
        dependencies = {re.split(r'[<>=!~\[]', requirement, maxsplit=1)[0].lower()
                        for requirement in argv[4:]}
        self.assertEqual(dependencies,
                         {'numpy', 'pillow', 'jsonschema', 'pyyaml', 'requests'})

    def test_core_installs_cpu_tools_and_isolates_private_configuration(self):
        commands = '\n'.join(step.get('run', '') for step in
                             self.workflow['jobs']['core']['steps'])
        self.assertIn('sudo apt-get install -y ffmpeg fonts-noto-cjk', commands)
        self.assertIn('export HOME="$RUNNER_TEMP/framivra-core-home"', commands)
        self.assertIn('export XDG_CONFIG_HOME="$HOME/.config"', commands)
        self.assertIn('export VTX_CONFIG="$HOME/config.json"', commands)
        self.assertIn('unset PYTHONPATH PYTHONHOME BGM_CLI BGM_REPO', commands)
        self.assertNotIn('apt-get install -y blender', commands)

    def test_actions_are_commit_pinned_and_checkout_drops_credentials(self):
        for job in self.workflow['jobs'].values():
            for step in job['steps']:
                if 'uses' in step:
                    self.assertRegex(step['uses'], r'^[\w/-]+@[0-9a-f]{40}$')
                    if step['uses'].startswith('actions/checkout@'):
                        self.assertEqual(step['with']['persist-credentials'], 'false')
                self.assertNotIn('continue-on-error', step)
            self.assertNotIn('continue-on-error', job)

    def test_core_docs_static_and_separate_strict_media_commands(self):
        jobs = self.workflow['jobs']
        commands = lambda job: '\n'.join(s.get('run', '') for s in jobs[job]['steps'])
        self.assertIn('bash scripts/run_all_tests.sh', commands('core'))
        self.assertIn('bash scripts/doc_gates.sh', commands('contracts'))
        self.assertIn('python scripts/ci_checks.py static', commands('contracts'))
        for kind in ('ffmpeg', 'chrome'):
            self.assertIn('python scripts/ci_checks.py ' + kind, commands(kind))
        # Unit tests simulate NVENC and must not be overridden globally.
        self.assertNotIn('VTX_NO_NVENC', self.workflow['env'])
        self.assertNotIn('VTX_SEG_NVENC', self.workflow['env'])


if __name__ == '__main__':
    unittest.main()
