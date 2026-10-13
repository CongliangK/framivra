"""CI contract regressions, including fail-closed integration skip handling."""
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import re
import shlex
import unittest
from unittest import mock

import yaml  # test-only dependency, installed by the CI core matrix

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('ci_checks', ROOT / 'scripts/ci_checks.py')
ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci)


class StrictMediaGate(unittest.TestCase):
    def suite_exit(self, suite):
        with mock.patch.object(unittest.TestLoader, 'loadTestsFromNames', return_value=suite), \
                contextlib.redirect_stderr(io.StringIO()):
            return ci.strict_suite(['fixture'], stream=io.StringIO())

    def test_success_executes(self):
        class Case(unittest.TestCase):
            def test_ok(self):
                pass
        self.assertEqual(self.suite_exit(unittest.defaultTestLoader.loadTestsFromTestCase(Case)), 0)

    def test_skip_fails_even_when_unittest_was_successful(self):
        class Case(unittest.TestCase):
            @unittest.skip('tool unavailable')
            def test_skipped(self):
                pass
        self.assertEqual(self.suite_exit(unittest.defaultTestLoader.loadTestsFromTestCase(Case)), 1)

    def test_empty_suite_fails(self):
        self.assertEqual(self.suite_exit(unittest.TestSuite()), 1)

    def test_test_error_fails(self):
        class Case(unittest.TestCase):
            def test_broken(self):
                raise RuntimeError('intentional')
        self.assertEqual(self.suite_exit(unittest.defaultTestLoader.loadTestsFromTestCase(Case)), 1)

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
