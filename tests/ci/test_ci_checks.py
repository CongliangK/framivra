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
import types
import unittest
from unittest import mock

import yaml  # test-only dependency, installed by the CI core matrix

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('ci_checks', ROOT / 'scripts/ci_checks.py')
ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci)


def tests_of(case):
    return list(unittest.defaultTestLoader.loadTestsFromTestCase(case))


class StrictMediaGate(unittest.TestCase):
    def test_chrome_groups_preserve_all_test_identities_and_whole_modules(self):
        groups = [ci.chrome_group(group) for group in ('1', '2')]
        self.assertEqual(Counter(sum(groups, ())), Counter(ci.MEDIA_TESTS['chrome']))
        self.assertEqual(groups[0], ('tests.execution.test_pipeline.TestPipelineIntegration',))
        def identities(names):
            def leaves(suite):
                for test in suite:
                    if isinstance(test, unittest.TestSuite):
                        yield from leaves(test)
                    else:
                        yield test.id()
            loader = unittest.TestLoader()
            result = Counter(leaves(loader.loadTestsFromNames(names)))
            self.assertFalse(loader.errors)
            return result
        combined = sum((identities(group) for group in groups), Counter())
        self.assertEqual(combined, identities(ci.MEDIA_TESTS['chrome']))
        self.assertTrue(all(count == 1 for count in combined.values()))
        modules = [{identity.rsplit('.', 2)[0] for identity in identities(group)}
                   for group in groups]
        self.assertFalse(modules[0] & modules[1])

    def test_new_chrome_selectors_keep_deterministic_complete_module_ownership(self):
        names = ('tests.execution.test_pipeline.NewCase', 'tests.execution.test_pipeline.OtherCase',
                 'tests.test_future_media.First', 'tests.test_future_media.Second')
        with mock.patch.dict(ci.MEDIA_TESTS, {'chrome': names}):
            self.assertEqual(ci.chrome_group('1'), names[:2])
            self.assertEqual(ci.chrome_group('2'), names[2:])

    def test_invalid_empty_duplicate_or_unknown_chrome_groups_fail(self):
        for group in ('0', '3', 'unknown'):
            with self.subTest(group=group), self.assertRaises(ValueError):
                ci.chrome_group(group)
        for names in ((), ci.MEDIA_TESTS['chrome'] * 2,
                      ('tests.execution.test_pipeline',), ('tests.test_future_media',)):
            with self.subTest(names=names), \
                    mock.patch.dict(ci.MEDIA_TESTS, {'chrome': names}), \
                    self.assertRaises(ValueError):
                ci.chrome_group('1')
        for modules in ((), ('unknown',), ci.CHROME_GROUP_ONE_MODULES * 2, ('tests',)):
            with self.subTest(modules=modules), \
                    mock.patch.object(ci, 'CHROME_GROUP_ONE_MODULES', modules), \
                    self.assertRaises(ValueError):
                ci.chrome_group('2')

    def test_chrome_grouped_and_default_entrypoints_keep_real_preflight_and_flags(self):
        for group in (None, '1', '2'):
            with self.subTest(group=group), \
                    mock.patch.dict(os.environ, {}, clear=True), \
                    mock.patch.object(ci, 'check_media_inventory') as inventory, \
                    mock.patch.object(ci, 'executable', side_effect=lambda name, override=None: '/bin/' + name), \
                    mock.patch.object(ci, 'run') as media_smoke, \
                    mock.patch.object(ci, 'prepare_chrome', return_value='/chrome') as chrome, \
                    mock.patch.object(ci, 'strict_suite', return_value=0) as strict:
                self.assertEqual(ci.main(['chrome'] + (['--group', group] if group else [])), 0)
                inventory.assert_called_once_with()
                chrome.assert_called_once_with()
                self.assertEqual(media_smoke.call_count, 3)
                strict.assert_called_once_with(
                    ci.chrome_group(group) if group else ci.MEDIA_TESTS['chrome'])
                self.assertEqual(os.environ['VTX_TEST_CHROME'], '/chrome')
                self.assertEqual(os.environ['VTX_CHROME'], '/chrome')
                self.assertEqual(os.environ['VTX_PIPE_INTEG'], '1')
                self.assertNotIn('VTX_RANGE_INTEG', os.environ)
                self.assertNotIn('VTX_AUDIO_REPORT_INTEG', os.environ)
                self.assertNotIn('VTX_TEST_DEMO_TOOLCHAIN', os.environ)

    def test_chrome_dependency_failure_blocks_both_groups_and_default(self):
        for group in (None, '1', '2'):
            with self.subTest(group=group), \
                    mock.patch.object(ci, 'check_media_inventory'), \
                    mock.patch.object(ci, 'executable', side_effect=lambda name, override=None: '/bin/' + name), \
                    mock.patch.object(ci, 'run'), \
                    mock.patch.object(ci, 'prepare_chrome', side_effect=RuntimeError('missing Chrome')), \
                    mock.patch.object(ci, 'strict_suite') as strict, \
                    mock.patch.dict(os.environ, {}, clear=True), \
                    contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(ci.main(['chrome'] + (['--group', group] if group else [])), 1)
                strict.assert_not_called()

    def test_ffmpeg_groups_preserve_every_selected_test_without_overlap(self):
        groups = [ci.ffmpeg_group(number) for number in ('1', '2', '3')]
        self.assertTrue(all(groups))
        combined_names = sum(groups, ())
        self.assertEqual(Counter(combined_names), Counter(ci.MEDIA_TESTS['ffmpeg']))
        self.assertEqual(len(combined_names), len(set(combined_names)))
        def identities(names):
            def leaves(suite):
                for test in suite:
                    if isinstance(test, unittest.TestSuite):
                        yield from leaves(test)
                    else:
                        yield test.id()
            loader = unittest.TestLoader()
            result = Counter(leaves(loader.loadTestsFromNames(names)))
            self.assertFalse(loader.errors)
            return result
        combined = sum((identities(group) for group in groups), Counter())
        self.assertEqual(combined, identities(ci.MEDIA_TESTS['ffmpeg']))
        self.assertTrue(all(count == 1 for count in combined.values()))

    def test_new_ffmpeg_selectors_have_deterministic_complete_module_ownership(self):
        names = ('tests.media.test_filter_threads.NewCase',
                 'tests.gfx.test_frame_source.NewCase',
                 'tests.test_future_media.First', 'tests.test_future_media.Second')
        with mock.patch.dict(ci.MEDIA_TESTS, {'ffmpeg': names}):
            self.assertEqual(ci.ffmpeg_group('1'), names[:1])
            self.assertEqual(ci.ffmpeg_group('2'), names[1:2])
            self.assertEqual(ci.ffmpeg_group('3'), names[2:])

    def test_empty_duplicate_or_overlapping_ffmpeg_group_configuration_fails(self):
        invalid_names = ((), ci.MEDIA_TESTS['ffmpeg'] * 2,
                         ('tests.media.test_filter_threads',), ('tests.gfx.test_frame_source',),
                         ('tests.test_future_media',))
        for names in invalid_names:
            with self.subTest(names=names), \
                    mock.patch.dict(ci.MEDIA_TESTS, {'ffmpeg': names}), \
                    self.assertRaises(ValueError):
                ci.ffmpeg_group('1')
        for modules in (ci.FFMPEG_GROUP_ONE_MODULES, ('tests',)):
            with mock.patch.object(ci, 'FFMPEG_GROUP_TWO_MODULES', modules), \
                    self.assertRaises(ValueError):
                ci.ffmpeg_group('2')

    def test_unknown_ffmpeg_group_and_group_on_other_gates_fail(self):
        with self.assertRaises(ValueError):
            ci.ffmpeg_group('other')
        for args in (['chrome', '--group', '3'], ['static', '--group', '2'],
                     ['demo-toolchain', '--group', '3'], ['ffmpeg', '--group', '4']):
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()), \
                    self.assertRaises(SystemExit) as raised:
                ci.main(args)
            self.assertEqual(raised.exception.code, 2)

    def test_ffmpeg_grouped_and_default_entrypoints_keep_preflight_and_strict_runner(self):
        for group in (None, '1', '2', '3'):
            with self.subTest(group=group), \
                    mock.patch.dict(os.environ, {}, clear=True), \
                    mock.patch.object(ci, 'check_media_inventory') as inventory, \
                    mock.patch.object(ci, 'prepare_media') as preflight, \
                    mock.patch.object(ci, 'prepare_chrome', return_value='/chrome') as chrome, \
                    mock.patch.object(ci, 'strict_suite', return_value=0) as strict:
                args = ['ffmpeg'] + (['--group', group] if group else [])
                self.assertEqual(ci.main(args), 0)
                inventory.assert_called_once_with()
                preflight.assert_called_once_with('ffmpeg')
                strict.assert_called_once_with(
                    ci.ffmpeg_group(group) if group else ci.MEDIA_TESTS['ffmpeg'])
                if group in (None, '2'):
                    chrome.assert_called_once_with()
                    self.assertEqual(os.environ['VTX_CHROME'], '/chrome')
                else:
                    chrome.assert_not_called()
                    self.assertNotIn('VTX_CHROME', os.environ)
                self.assertNotIn('VTX_TEST_CHROME', os.environ)
                self.assertNotIn('VTX_PIPE_INTEG', os.environ)

    def test_frame_source_chrome_dependency_failure_prevents_test_execution(self):
        for group in (None, '2'):
            with self.subTest(group=group), \
                    mock.patch.object(ci, 'check_media_inventory'), \
                    mock.patch.object(ci, 'prepare_media'), \
                    mock.patch.object(ci, 'prepare_chrome',
                                      side_effect=RuntimeError('missing Chrome')), \
                    mock.patch.object(ci, 'strict_suite') as strict, \
                    contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(ci.main(['ffmpeg'] + (['--group', group] if group else [])), 1)
                strict.assert_not_called()

    def test_chrome_preflight_requires_real_smoke_without_changing_gate_flags(self):
        for output in ('<p>vtx-ci-ready</p>', 'broken launch'):
            with self.subTest(output=output), mock.patch.dict(os.environ, {}, clear=True), \
                    mock.patch.object(ci, 'executable', return_value='/chrome') as exe, \
                    mock.patch.object(ci, 'run', return_value=types.SimpleNamespace(stdout=output)) as run:
                if 'vtx-ci-ready' in output:
                    self.assertEqual(ci.prepare_chrome(), '/chrome')
                else:
                    with self.assertRaisesRegex(RuntimeError, 'DOM smoke'):
                        ci.prepare_chrome()
                exe.assert_called_once_with('google-chrome', 'VTX_TEST_CHROME')
                self.assertEqual(run.call_args_list[0].args[0], ['/chrome', '--version'])
                self.assertIn('--headless=new', run.call_args_list[1].args[0])
                self.assertIn('--dump-dom', run.call_args_list[1].args[0])
                self.assertNotIn('VTX_TEST_CHROME', os.environ)
                self.assertNotIn('VTX_PIPE_INTEG', os.environ)

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
        events = []
        module = types.ModuleType('media_lifecycle_fixture')
        module.setUpModule = lambda: events.append('module_setup')
        module.tearDownModule = lambda: events.append('module_teardown')
        class Case(unittest.TestCase):
            @classmethod
            def setUpClass(cls):
                events.append('class_setup')
            @classmethod
            def tearDownClass(cls):
                events.append('class_teardown')
            def test_body(self):
                self.addCleanup(lambda: events.append('cleanup'))
                for _ in range(2):
                    with self.subTest(part='same'):
                        with self.subTest(part='nested'):
                            events.append('body')
            @unittest.expectedFailure
            def test_expected(self):
                events.append('expected_failure')
                self.fail('ordinary expected failure is still supported')
        Case.__module__ = module.__name__
        module.Case = Case
        with mock.patch.dict(sys.modules, {module.__name__: module}):
            self.assertEqual(ci.strict_suite([module.__name__], stream=io.StringIO()), 0)
        self.assertEqual(events, ['module_setup', 'class_setup', 'body', 'body',
                                  'cleanup', 'expected_failure', 'class_teardown',
                                  'module_teardown'])

    def test_skip_fails_even_when_unittest_was_successful(self):
        class Case(unittest.TestCase):
            @unittest.skip('tool unavailable')
            def test_skipped(self):
                pass
        self.assertEqual(self.suite_exit({'fixture': tests_of(Case)}), 1)

    def test_empty_suite_fails(self):
        self.assertEqual(self.suite_exit({'fixture': []}), 1)
        self.assertEqual(self.suite_exit({}, names=()), 1)
        module = types.ModuleType('media_discovery_error_fixture')
        ran = []
        def load_tests(loader, tests, pattern):
            loader.loadTestsFromName('media_missing_discovery_fixture')
            return unittest.TestSuite(self.healthy_suite(ran))
        module.load_tests = load_tests
        output = io.StringIO()
        with mock.patch.dict(sys.modules, {module.__name__: module}), \
                mock.patch.object(ci.unittest, 'TextTestRunner') as runner:
            self.assertEqual(ci.strict_suite([module.__name__], stream=output), 1)
            runner.assert_not_called()
        self.assertEqual(ran, [])
        self.assertIn('discovery errors', output.getvalue())

    def test_test_error_fails(self):
        for mode in ('body_error', 'no_execution', 'no_outcome', 'no_stop',
                     'post_stop', 'two_successes', 'subtest_without_parent',
                     'exit_zero', 'exit_none', 'exit_nonzero'):
            bodies = []
            class Case(unittest.TestCase):
                def test_broken(self):
                    bodies.append(True)
                    raise RuntimeError('intentional')
                def run(self, result=None):
                    if mode == 'body_error':
                        return super().run(result)
                    if mode.startswith('exit_'):
                        raise SystemExit({'exit_zero': 0, 'exit_none': None,
                                          'exit_nonzero': 2}[mode])
                    if mode == 'no_execution':
                        return result
                    result.startTest(self)
                    if mode in ('no_stop', 'two_successes'):
                        result.addSuccess(self)
                    if mode == 'two_successes':
                        result.addSuccess(self)
                    if mode == 'subtest_without_parent':
                        result.addSubTest(self, unittest.case._SubTest(self, None, {}), None)
                    if mode != 'no_stop':
                        result.stopTest(self)
                    if mode == 'post_stop':
                        result.addSuccess(self)
                    return result
            with self.subTest(mode=mode):
                # An ordinary passing test must not hide the incomplete one.
                suite = tests_of(Case) + self.healthy_suite()
                self.assertEqual(self.suite_exit({'fixture': suite}), 1)
                self.assertEqual(bodies, [True] if mode == 'body_error' else [])
        for code in (0, None, 2):
            with self.subTest(discovery_exit=code), \
                    mock.patch.object(unittest.TestLoader, 'loadTestsFromNames',
                                      side_effect=SystemExit(code)), \
                    mock.patch.object(ci.unittest, 'TextTestRunner') as runner:
                self.assertEqual(ci.strict_suite(['fixture'], stream=io.StringIO()), 1)
                runner.assert_not_called()
            module = types.ModuleType('media_discovery_fixture')
            def stop(*args):
                raise SystemExit(code)
            module.load_tests = stop
            with self.subTest(load_tests_exit=code), \
                    mock.patch.dict(sys.modules, {module.__name__: module}):
                self.assertEqual(ci.strict_suite([module.__name__], stream=io.StringIO()), 1)
        class DroppedSuite(unittest.TestSuite):
            def run(self, result, debug=False):
                return result
        for suite in (DroppedSuite(self.healthy_suite()),
                      unittest.TestSuite([DroppedSuite(self.healthy_suite())])):
            with self.subTest(custom_suite=type(suite).__name__), \
                    mock.patch.object(unittest.TestLoader, 'loadTestsFromNames',
                                      return_value=suite), \
                    mock.patch.object(ci.unittest, 'TextTestRunner') as runner:
                self.assertEqual(ci.strict_suite(['fixture'], stream=io.StringIO()), 1)
                runner.assert_not_called()

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
        import tests.media.test_range_render as range_tests
        target = range_tests.TestRangeIntegration
        original = target.__dict__['test_range_render']
        healthy = self.healthy_suite()
        delattr(target, 'test_range_render')
        try:
            for names in (['tests.media.test_range_render.TestRangeIntegration',
                           'fixture'],
                          ['fixture',
                           'tests.media.test_range_render.TestRangeIntegration']):
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

    def test_grouped_media_entrypoints_preserve_nonzero_suite_results(self):
        for gate, groups in (('ffmpeg', ('1', '2', '3')), ('chrome', ('1', '2'))):
            for group in groups:
                with self.subTest(gate=gate, group=group), \
                        mock.patch.dict(os.environ, {}, clear=True), \
                        mock.patch.object(ci, 'check_media_inventory'), \
                        mock.patch.object(ci, 'prepare_media'), \
                        mock.patch.object(ci, 'prepare_chrome', return_value='/chrome'), \
                        mock.patch.object(ci, 'strict_suite', return_value=1) as suite:
                    self.assertEqual(ci.main([gate, '--group', group]), 1)
                    suite.assert_called_once_with(
                        ci.ffmpeg_group(group) if gate == 'ffmpeg' else ci.chrome_group(group))

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
        self.assertEqual(inventory['tests.gfx.test_gfx_cache_integrity.RealBrowserIntegrity'], 'chrome')
        self.assertEqual(inventory['tests.execution.test_failure_recovery.RealChromeRecovery'], 'chrome')
        self.assertEqual(inventory['tests.execution.test_failure_recovery.RealFFmpegRecovery'], 'ffmpeg')
        for selector in ('tests.gfx.test_gfx_cache_integrity.RealBrowserIntegrity',
                         'tests.execution.test_failure_recovery.RealChromeRecovery',
                         'tests.execution.test_failure_recovery.RealFFmpegRecovery'):
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


class SafetyCommandHangGuard(unittest.TestCase):
    def sandbox(self, case_type):
        case = case_type()
        case.setUp()
        self.addCleanup(case.doCleanups)
        return case

    def test_only_positive_timeline_controls_extend_the_finite_hang_guard(self):
        from tests.safety import _support as support
        from tests.safety import test_timeline_escape_safety as timeline
        self.assertEqual(support.SandboxCase.vtx_timeout, 30)
        self.assertFalse(support.SandboxCase.vtx_diagnostics)
        self.assertFalse(support.SandboxCase.vtx_environment_fixture)
        for case_type, timeout in ((support.SandboxCase, 30),
                                   (timeline.SnapshotTimelineEscape, 30),
                                   (timeline.WriteRootEscape, 30),
                                   (timeline.LegitUseControl, 60)):
            case = self.sandbox(case_type)
            completed = subprocess.CompletedProcess([], 0, 'ok', '')
            with self.subTest(case=case_type.__name__), \
                    mock.patch.object(support.subprocess, 'run', return_value=completed) as run, \
                    contextlib.redirect_stderr(io.StringIO()):
                self.assertIs(case.run_vtx('validate'), completed)
                command = run.call_args.args[0]
                if case_type is support.SandboxCase:
                    self.assertEqual(command[:2], ['env', 'python3'])
                    self.assertEqual(command[2], str(support.REPO / 'vtx/vtx.py'))
                    self.assertNotIn('-c', command)
                else:
                    self.assertTrue(case.vtx_environment_fixture)
                    self.assertEqual(command[:3], ['env', 'python3', '-c'])
                    self.assertIn('from tests.gfx._environment import collect, find_chrome', command[3])
                    self.assertIn("runpy.run_path(sys.argv[0], run_name='__main__')", command[3])
                    self.assertEqual(command[4], str(support.REPO / 'vtx/vtx.py'))
                self.assertEqual(command[-3:], ['-t', str(case.timeline), 'validate'])
                run.assert_called_once_with(
                    command,
                    cwd=case.root, env=case.env, capture_output=True, text=True,
                    timeout=timeout)

    def test_path_fixture_keeps_real_receipt_asset_identity_and_missing_asset_failure(self):
        from tests.safety import _support as support
        from tests.safety.test_timeline_escape_safety import LegitUseControl
        case = self.sandbox(LegitUseControl)
        case.vtx_traceback_after = 45
        command = case._vtx_command(('validate',))
        self.assertIn('after = 45', command[3])
        self.assertIn('faulthandler.dump_traceback_later(after, repeat=False)', command[3])
        self.assertIn('faulthandler.cancel_dump_traceback_later()', command[3])
        self.assertEqual(case.vtx_timeout, 60)
        asset = case.put(case.sandbox / 'shared.html', b'<html>one</html>')
        case.tl['video_tracks'] = [{'name': 'gfx', 'kind': 'gfx', 'clips': [
            {'src': '../shared.html', 'dst_in': 0, 'dur': 30}]}]
        case.save_timeline()
        inherited_environment = dict(case.env)
        real_run = support.subprocess.run
        with mock.patch.object(support.subprocess, 'run', wraps=real_run) as run, \
                contextlib.redirect_stderr(io.StringIO()):
            case.assert_ok(case.run_vtx('validate'))
        self.assertEqual(run.call_args.kwargs['env'], inherited_environment)
        receipt_path = case.root / 'out/receipt.json'
        before = json.loads(receipt_path.read_text())
        self.assertTrue(before['stages']['validate']['ok'])
        self.assertTrue(before['gfx_backend']['environment_identity'].startswith('vtx.gfx-environment/1|'))
        asset.write_bytes(b'<html>two</html>')
        with contextlib.redirect_stderr(io.StringIO()):
            case.assert_ok(case.run_vtx('validate'))
        after = json.loads(receipt_path.read_text())
        self.assertNotEqual(before['input_fingerprint'], after['input_fingerprint'])
        asset.unlink()
        with contextlib.redirect_stderr(io.StringIO()):
            failed = case.run_vtx('validate')
        self.assertNotEqual(failed.returncode, 0, failed.stdout + failed.stderr)
        self.assertFalse(json.loads(receipt_path.read_text())['stages']['validate']['ok'])
        case.assert_outside_unchanged()

    def test_positive_command_reports_real_elapsed_time_without_environment_dump(self):
        from tests.safety import _support as support
        from tests.safety.test_timeline_escape_safety import LegitUseControl
        case = self.sandbox(LegitUseControl)
        case.env['FIXTURE_SECRET'] = 'must-not-be-logged'
        completed = subprocess.CompletedProcess([], 0, 'ok', '')
        output = io.StringIO()
        with mock.patch.object(support.subprocess, 'run', return_value=completed), \
                mock.patch.object(support.time, 'monotonic', side_effect=(100, 101.25)), \
                contextlib.redirect_stderr(output):
            self.assertIs(case.run_vtx('gc'), completed)
        self.assertIn("command=('gc',) elapsed=1.250s timeout=60s exit=0", output.getvalue())
        self.assertNotIn('must-not-be-logged', output.getvalue())

    def test_timeout_still_raises_without_retry_and_reports_bounded_captured_output(self):
        from tests.safety import _support as support
        from tests.safety.test_timeline_escape_safety import LegitUseControl
        for case_type in (support.SandboxCase, LegitUseControl):
            case = self.sandbox(case_type)
            error = subprocess.TimeoutExpired(['fixture'], case.vtx_timeout,
                                               output=b'prefix' + b'x' * 3000,
                                               stderr=b'failure\xff')
            output = io.StringIO()
            with self.subTest(case=case_type.__name__), \
                    mock.patch.object(support.subprocess, 'run', side_effect=error) as run, \
                    contextlib.redirect_stderr(output), \
                    self.assertRaises(subprocess.TimeoutExpired) as raised:
                case.run_vtx('validate')
            self.assertIs(raised.exception, error)
            run.assert_called_once()
            if case_type is LegitUseControl:
                self.assertIn('timeout=60s timeout', output.getvalue())
                self.assertIn('stdout tail=', output.getvalue())
                self.assertIn('stderr tail=', output.getvalue())
                self.assertIn('[truncated]', output.getvalue())
                self.assertNotIn('prefix', output.getvalue())
                self.assertLess(len(output.getvalue()), 2500)
            else:
                self.assertEqual(output.getvalue(), '')

    def test_positive_failure_is_not_success_and_missing_output_is_not_claimed_empty(self):
        from tests.safety import _support as support
        from tests.safety.test_timeline_escape_safety import LegitUseControl
        case = self.sandbox(LegitUseControl)
        completed = subprocess.CompletedProcess([], 1, 'failed', '')
        output = io.StringIO()
        with mock.patch.object(support.subprocess, 'run', return_value=completed), \
                contextlib.redirect_stderr(output):
            result = case.run_vtx('gc')
        self.assertIs(result, completed)
        with self.assertRaises(AssertionError):
            case.assert_ok(result)
        self.assertIn("stdout tail='failed'", output.getvalue())
        self.assertIn("stderr tail=''", output.getvalue())
        output = io.StringIO()
        with mock.patch.object(support.subprocess, 'run',
                               side_effect=subprocess.TimeoutExpired(['fixture'], 60)), \
                contextlib.redirect_stderr(output), self.assertRaises(subprocess.TimeoutExpired):
            case.run_vtx('validate')
        self.assertIn("stdout tail='<not captured>'", output.getvalue())
        self.assertIn("stderr tail='<not captured>'", output.getvalue())


class WorkflowContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = (ROOT / '.github/workflows/ci.yml').read_text(encoding='utf-8')
        # BaseLoader avoids YAML 1.1 treating GitHub's "on" as a boolean.
        cls.workflow = yaml.load(cls.text, Loader=yaml.BaseLoader)

    def test_events_and_least_privilege(self):
        self.assertTrue({'pull_request', 'push', 'workflow_dispatch', 'schedule'} <=
                        self.workflow['on'].keys())
        self.assertEqual(self.workflow['permissions'], {'contents': 'read'})
        self.assertNotIn('pull_request_target', self.workflow['on'])

    def core_matrix_entries(self, event):
        matrix = self.workflow['jobs']['core']['strategy']['matrix']
        # Include-only prevents an accidental Cartesian product or extra jobs.
        self.assertEqual(set(matrix), {'include'})
        match = re.fullmatch(
            r"\$\{\{ fromJSON\(\(github.event_name == 'schedule' \|\| "
            r"github.event_name == 'workflow_dispatch'\) && '([^']+)' \|\| '([^']+)'\) \}\}",
            matrix['include'])
        self.assertIsNotNone(match)
        return json.loads(match.group(1 if event in ('schedule', 'workflow_dispatch') else 2))

    def test_declared_supported_python_versions_are_tested(self):
        """Keep the frozen regression ID; all versions run on extended events."""
        pyproject = (ROOT / 'pyproject.toml').read_text(encoding='utf-8')
        declared = set(re.findall(r'Programming Language :: Python :: (3\.\d+)', pyproject))
        for event in ('schedule', 'workflow_dispatch'):
            with self.subTest(event=event):
                entries = self.core_matrix_entries(event)
                self.assertEqual(entries, [dict(python=version, group='full')
                                           for version in sorted(declared, key=lambda v: int(v.split('.')[1]))])
                self.assertEqual(len(entries), 5)

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
        for group, job in (('1', 'ffmpeg-one'), ('2', 'ffmpeg-two'),
                           ('3', 'ffmpeg-three')):
            self.assertIn('python scripts/ci_checks.py ffmpeg --group ' + group,
                          commands(job))
        for group, job in (('1', 'chrome-one'), ('2', 'chrome-two')):
            self.assertIn('python scripts/ci_checks.py chrome --group ' + group,
                          commands(job))
            self.assertIn('sudo apt-get install -y ffmpeg fonts-noto-cjk', commands(job))
            self.assertEqual(jobs[job]['runs-on'], 'ubuntu-24.04')
        # Unit tests simulate NVENC and must not be overridden globally.
        self.assertNotIn('VTX_NO_NVENC', self.workflow['env'])
        self.assertNotIn('VTX_SEG_NVENC', self.workflow['env'])

    def test_default_core_covers_each_curated_group_once_on_one_python(self):
        core = self.workflow['jobs']['core']
        self.assertEqual(core['strategy']['fail-fast'], 'false')
        for event in ('pull_request', 'push'):
            with self.subTest(event=event):
                entries = self.core_matrix_entries(event)
                self.assertEqual(entries, [
                    {'python': '3.9', 'group': '1'},
                    {'python': '3.11', 'group': '2'},
                    {'python': '3.13', 'group': '3'},
                ])
                self.assertEqual(Counter(entry['group'] for entry in entries),
                                 Counter(('1', '2', '3')))
        self.assertIn('matrix.python', core['name'])
        self.assertIn('full regression', core['name'])
        self.assertIn('curated group {0}/3', core['name'])
        curated = next(s for s in core['steps'] if 'python scripts/ci_pr.py' in s.get('run', ''))
        self.assertEqual(curated['run'].splitlines()[-1],
                         "python scripts/ci_pr.py --group '${{ matrix.group }}'")
        self.assertIn('each whole-module group runs on one Python version', curated['run'])
        self.assertIn('omitted tests and group/version combinations are not certified', curated['run'])

    def test_event_expansion_has_ten_default_or_twelve_extended_real_jobs(self):
        jobs = self.workflow['jobs']
        self.assertEqual(set(jobs), {'core', 'contracts', 'ffmpeg-one', 'ffmpeg-two',
                                     'ffmpeg-three', 'chrome-one', 'chrome-two',
                                     'windows-install'})
        retired_names = {'Core regression (all selected versions and groups)',
                         'FFmpeg CPU integration (no skips)',
                         'Chrome and pipeline CPU integration (no skips)'}
        self.assertFalse(retired_names & {job['name'] for job in jobs.values()})
        for event, count in (('pull_request', 10), ('push', 10),
                             ('schedule', 12), ('workflow_dispatch', 12)):
            with self.subTest(event=event):
                self.assertEqual(len(jobs) - 1 + len(self.core_matrix_entries(event)), count)
        for name, job in jobs.items():
            with self.subTest(job=name):
                # Every actual job reports its own result: no synthetic aggregate,
                # conditional job, dependency skip, or failure-to-success wrapper.
                self.assertNotIn('if', job)
                self.assertNotIn('needs', job)
                if name != 'core':
                    self.assertNotIn('strategy', job)
                for step in job['steps']:
                    if name != 'core':
                        self.assertNotIn('if', step)

    def test_ffmpeg_required_check_needs_both_real_groups_and_rejects_non_success(self):
        """Keep the frozen ID; direct strict group jobs replace the old aggregate.

        The historical method name does not certify branch-protection settings.
        FFmpeg and Chrome now report each actual group result independently.
        """
        jobs = self.workflow['jobs']
        expected = {
            'ffmpeg-one': ('FFmpeg CPU group 1 (no skips)', 'ffmpeg', '1'),
            'ffmpeg-two': ('FFmpeg CPU group 2 + Chrome fixture (no skips)', 'ffmpeg', '2'),
            'ffmpeg-three': ('FFmpeg CPU group 3 (no skips)', 'ffmpeg', '3'),
            'chrome-one': ('Chrome CPU group 1 pipeline (no skips)', 'chrome', '1'),
            'chrome-two': ('Chrome CPU group 2 graphics (no skips)', 'chrome', '2'),
        }
        for name, (title, gate, group) in expected.items():
            with self.subTest(job=name):
                job = jobs[name]
                self.assertEqual(job['name'], title)
                steps = [step for step in job['steps']
                         if 'scripts/ci_checks.py' in step.get('run', '')]
                self.assertEqual(len(steps), 1)
                self.assertEqual(steps[0]['run'],
                                 'python scripts/ci_checks.py %s --group %s' % (gate, group))
                self.assertNotIn('if', steps[0])
                self.assertNotIn('shell', steps[0])

    def test_ungrouped_local_full_runner_remains_original_discovery(self):
        runner = (ROOT / 'scripts/run_all_tests.sh').read_text(encoding='utf-8')
        self.assertIn('if [ "$#" -gt 0 ]; then', runner)
        self.assertIn('exec env python3 scripts/ci_partition.py "$@"', runner)
        self.assertIn('env python3 -m unittest discover -s tests -t . -v || rc=$?', runner)
        self.assertIn('for d in apps/*/tests plugins/*/tests templates/*/tests; do', runner)
        self.assertNotIn('ci_pr.py', runner)


if __name__ == '__main__':
    unittest.main()
