"""CI contract regressions, including fail-closed integration skip handling."""
import contextlib
from collections import Counter
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
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
    def test_ffmpeg_groups_preserve_every_selected_test_without_overlap(self):
        one, two = ci.ffmpeg_group('1'), ci.ffmpeg_group('2')
        self.assertTrue(one)
        self.assertTrue(two)
        self.assertFalse(set(one) & set(two))
        self.assertEqual(Counter(one + two), Counter(ci.MEDIA_TESTS['ffmpeg']))
        def identities(names):
            def leaves(suite):
                for test in suite:
                    if isinstance(test, unittest.TestSuite):
                        yield from leaves(test)
                    else:
                        yield test.id()
            return Counter(leaves(unittest.TestLoader().loadTestsFromNames(names)))
        first, second = identities(one), identities(two)
        self.assertFalse(first.keys() & second.keys())
        self.assertEqual(first + second, identities(ci.MEDIA_TESTS['ffmpeg']))
        self.assertTrue(all(count == 1 for count in (first + second).values()))

    def test_new_ffmpeg_selectors_have_deterministic_complete_module_ownership(self):
        names = ('tests.test_filter_threads.NewCase',
                 'tests.test_future_media.First', 'tests.test_future_media.Second')
        with mock.patch.dict(ci.MEDIA_TESTS, {'ffmpeg': names}):
            self.assertEqual(ci.ffmpeg_group('1'), names[:1])
            self.assertEqual(ci.ffmpeg_group('2'), names[1:])

    def test_unknown_ffmpeg_group_and_group_on_other_gates_fail(self):
        with self.assertRaises(ValueError):
            ci.ffmpeg_group('other')
        for args in (['chrome', '--group', '1'], ['ffmpeg', '--group', '3']):
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()), \
                    self.assertRaises(SystemExit) as raised:
                ci.main(args)
            self.assertEqual(raised.exception.code, 2)

    def test_ffmpeg_grouped_and_default_entrypoints_keep_preflight_and_strict_runner(self):
        for group in (None, '1', '2'):
            with self.subTest(group=group), \
                    mock.patch.object(ci, 'check_media_inventory') as inventory, \
                    mock.patch.object(ci, 'prepare_media') as preflight, \
                    mock.patch.object(ci, 'strict_suite', return_value=0) as strict:
                args = ['ffmpeg'] + (['--group', group] if group else [])
                self.assertEqual(ci.main(args), 0)
                inventory.assert_called_once_with()
                preflight.assert_called_once_with('ffmpeg')
                strict.assert_called_once_with(
                    ci.ffmpeg_group(group) if group else ci.MEDIA_TESTS['ffmpeg'])

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


class MediaInventoryContract(unittest.TestCase):
    def fixture_inventory(self, source, selections=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tests = root / 'tests'
            tests.mkdir()
            (tests / 'test_media.py').write_text(source, encoding='utf-8')
            if selections is None:
                return ci.media_inventory(root)
            with contextlib.redirect_stdout(io.StringIO()):
                return ci.check_media_inventory(root, selections)

    def test_source_scan_resolves_tools_aliases_helpers_and_method_gates(self):
        source = '''import os, shutil, unittest
FF = shutil.which('ffmpeg')
def have_encoder():
    return FF is not None
@unittest.skipUnless(have_encoder(), 'needs encoder')
class Encoder(unittest.TestCase):
    def test_encode(self): pass
class Browser(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('VTX_TEST_CHROME'), 'needs browser')
    def test_draw(self): pass
raise AssertionError('inventory must not import or execute test modules')
'''
        self.assertEqual(self.fixture_inventory(source), {
            'tests.test_media.Encoder': 'ffmpeg',
            'tests.test_media.Browser.test_draw': 'chrome',
        })

    def test_dynamic_skip_and_setup_conditions_are_inventoried(self):
        source = '''import os, unittest
class Pipeline(unittest.TestCase):
    def setUpClass(cls):
        if not os.environ.get('VTX_PIPE_INTEG'):
            cls.skipTest('enable pipeline')
    def test_pipeline(self): pass
class Probe(unittest.TestCase):
    def test_probe(self):
        if not __import__('shutil').which('ffprobe'):
            self.skipTest('enable probe')
'''
        self.assertEqual(self.fixture_inventory(source), {
            'tests.test_media.Pipeline': 'chrome',
            'tests.test_media.Probe.test_probe': 'ffmpeg',
        })

    def test_optional_models_music_and_gpu_do_not_enter_cpu_jobs(self):
        source = '''import os, unittest
@unittest.skipUnless(os.environ.get('VTX_TEST_BGM_INTEG'), 'music_tools')
class Music(unittest.TestCase):
    def test_render(self): pass
@unittest.skipUnless(os.environ.get('VTX_TEST_DENOISE'), 'model weights')
class Model(unittest.TestCase):
    def test_denoise(self): pass
class Gpu(unittest.TestCase):
    def test_nvenc(self):
        if not export_4k.nvenc_probe():
            self.skipTest('NVENC unavailable')
'''
        self.assertEqual(self.fixture_inventory(source), {})

    def test_new_unlisted_media_gate_fails_then_explicit_selection_passes(self):
        source = '''import os, unittest
@unittest.skipUnless(os.environ.get('VTX_TEST_CHROME'), 'real browser')
class NewBrowser(unittest.TestCase):
    def test_real(self): pass
'''
        with self.assertRaisesRegex(RuntimeError, 'NewBrowser.*chrome'):
            self.fixture_inventory(source, {'ffmpeg': (), 'chrome': ()})
        # Listing a Chrome suite only in the FFmpeg job cannot satisfy coverage.
        with self.assertRaisesRegex(RuntimeError, 'NewBrowser.*chrome'):
            self.fixture_inventory(source, {'ffmpeg': ('tests.test_media',), 'chrome': ()})
        inventory = self.fixture_inventory(source, {'chrome': ('tests.test_media.NewBrowser',)})
        self.assertEqual(inventory, {'tests.test_media.NewBrowser': 'chrome'})

    def test_current_repository_cpu_media_gates_are_all_selected(self):
        with contextlib.redirect_stdout(io.StringIO()):
            inventory = ci.check_media_inventory()
        self.assertEqual(inventory['tests.test_gfx_cache_integrity.RealBrowserIntegrity'], 'chrome')
        self.assertEqual(inventory['tests.test_failure_recovery.RealChromeRecovery'], 'chrome')
        self.assertEqual(inventory['tests.test_failure_recovery.RealFFmpegRecovery'], 'ffmpeg')
        for selector in ('tests.test_gfx_cache_integrity.RealBrowserIntegrity',
                         'tests.test_failure_recovery.RealChromeRecovery',
                         'tests.test_failure_recovery.RealFFmpegRecovery'):
            with self.subTest(selector=selector):
                selections = {kind: tuple(name for name in names if name != selector)
                              for kind, names in ci.MEDIA_TESTS.items()}
                with self.assertRaisesRegex(RuntimeError, selector):
                    ci.check_media_inventory(selections=selections)

    def test_media_inventory_failure_prevents_tool_launch_and_execution(self):
        with mock.patch.object(ci, 'check_media_inventory', side_effect=RuntimeError('unlisted')), \
                mock.patch.object(ci, 'prepare_media') as preflight, \
                mock.patch.object(ci, 'strict_suite') as suite, \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(ci.main(['chrome']), 1)
            preflight.assert_not_called()
            suite.assert_not_called()


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
        matrix = self.workflow['jobs']['core']['strategy']['matrix']['python']
        match = re.fullmatch(
            r"\$\{\{ fromJSON\(\(github.event_name == 'schedule' \|\| "
            r"github.event_name == 'workflow_dispatch'\) && '([^']+)' \|\| '([^']+)'\) \}\}",
            matrix)
        self.assertIsNotNone(match)
        self.assertEqual(set(json.loads(match.group(1))), declared)
        self.assertEqual(json.loads(match.group(2)), ['3.9', '3.11', '3.13'])

    def test_curated_and_full_commands_have_exclusive_event_conditions(self):
        steps = self.workflow['jobs']['core']['steps']
        curated = [s for s in steps if 'python scripts/ci_pr.py' in s.get('run', '')]
        full = [s for s in steps if 'bash scripts/run_all_tests.sh' in s.get('run', '')]
        self.assertEqual(len(curated), 1)
        self.assertEqual(len(full), 1)
        self.assertEqual(curated[0]['if'],
                         "github.event_name != 'schedule' && github.event_name != 'workflow_dispatch'")
        self.assertEqual(full[0]['if'],
                         "github.event_name == 'schedule' || github.event_name == 'workflow_dispatch'")
        self.assertIn('Full discovery', curated[0]['run'])
        self.assertIn('GITHUB_STEP_SUMMARY', curated[0]['run'])

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
        for group, job in (('1', 'ffmpeg-one'), ('2', 'ffmpeg-two')):
            self.assertIn('python scripts/ci_checks.py ffmpeg --group ' + group,
                          commands(job))
        self.assertIn('python scripts/ci_checks.py chrome', commands('chrome'))
        # Unit tests simulate NVENC and must not be overridden globally.
        self.assertNotIn('VTX_NO_NVENC', self.workflow['env'])
        self.assertNotIn('VTX_SEG_NVENC', self.workflow['env'])

    def test_ffmpeg_required_check_needs_both_real_groups_and_rejects_non_success(self):
        job = self.workflow['jobs']['ffmpeg']
        self.assertEqual(job['name'], 'FFmpeg CPU integration (no skips)')
        self.assertEqual(set(job['needs']), {'ffmpeg-one', 'ffmpeg-two'})
        self.assertEqual(job['if'], 'always()')
        self.assertEqual(len(job['steps']), 1)
        step = job['steps'][0]
        self.assertEqual(step['env'], {
            'FIRST_RESULT': '${{ needs.ffmpeg-one.result }}',
            'SECOND_RESULT': '${{ needs.ffmpeg-two.result }}',
        })
        for first in ('success', 'failure', 'cancelled', 'skipped', ''):
            for second in ('success', 'failure', 'cancelled', 'skipped', ''):
                with self.subTest(first=first, second=second):
                    result = subprocess.run(['bash', '-e', '-c', step['run']],
                                            env=dict(os.environ, FIRST_RESULT=first,
                                                     SECOND_RESULT=second),
                                            capture_output=True, text=True, timeout=10)
                    self.assertEqual(result.returncode == 0,
                                     first == second == 'success')


if __name__ == '__main__':
    unittest.main()
