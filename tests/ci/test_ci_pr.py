"""The standard-library PR smoke gate must never turn missing or skipped tests into success."""
import contextlib
from collections import Counter
import io
import sys
import types
import unittest
from unittest import mock

from scripts import ci_pr


class CuratedRegressionGate(unittest.TestCase):
    @staticmethod
    def module(body='success'):
        class Case(unittest.TestCase):
            def test_example(self):
                if body == 'failure':
                    self.fail('intentional negative control')
                if body == 'error':
                    raise RuntimeError('intentional negative control')
                if body == 'skip':
                    self.skipTest('intentional negative control')
            @classmethod
            def setUpClass(cls):
                if body == 'class_skip':
                    raise unittest.SkipTest('intentional fixture skip')
        module = types.ModuleType('fixture')
        module.Case = Case
        return module

    def run_fixture(self, module, names=('fixture',)):
        output = io.StringIO()
        with mock.patch.object(ci_pr.importlib, 'import_module', return_value=module):
            result = ci_pr.run_curated(names, stream=output)
        return result, output.getvalue()

    def test_success_executes_and_discloses_reduced_scope(self):
        code, text = self.run_fixture(self.module())
        self.assertEqual(code, 0)
        self.assertIn('Ran 1 test', text)
        self.assertIn('full regression NOT RUN', text)

    def test_empty_module_is_refused_before_running_other_modules(self):
        for names in (('empty', 'healthy'), ('healthy', 'empty')):
            with self.subTest(names=names), \
                    mock.patch.object(ci_pr.importlib, 'import_module',
                                      side_effect=lambda name: types.ModuleType(name)
                                      if name == 'empty' else self.module()), \
                    mock.patch.object(ci_pr.unittest, 'TextTestRunner') as runner:
                self.assertEqual(ci_pr.run_curated(names, stream=io.StringIO()), 1)
                runner.assert_not_called()

    def test_missing_module_fails_before_execution(self):
        with mock.patch.object(ci_pr.importlib, 'import_module',
                               side_effect=ModuleNotFoundError('missing module')), \
                mock.patch.object(ci_pr.unittest, 'TextTestRunner') as runner:
            self.assertEqual(ci_pr.run_curated(('missing',), stream=io.StringIO()), 1)
            runner.assert_not_called()

    def test_empty_and_duplicate_lists_fail(self):
        for names in ((), ('fixture', 'fixture')):
            with self.subTest(names=names):
                self.assertEqual(self.run_fixture(self.module(), names)[0], 1)

    def test_failures_errors_and_method_or_class_skips_fail(self):
        for body in ('failure', 'error', 'skip', 'class_skip'):
            with self.subTest(body=body):
                self.assertEqual(self.run_fixture(self.module(body))[0], 1)

    def test_discovery_error_fails_even_when_unittest_returns_a_test(self):
        module = self.module()
        def load_tests(loader, tests, pattern):
            raise RuntimeError('broken load_tests')
        module.load_tests = load_tests
        code, text = self.run_fixture(module)
        self.assertEqual(code, 1)
        self.assertIn('discovery errors', text)

    def test_suite_silently_dropping_a_test_fails(self):
        class DroppedTest(unittest.TestSuite):
            def run(self, result, debug=False):
                return result
        module = self.module()
        module.load_tests = lambda loader, tests, pattern: DroppedTest(tests)
        code, text = self.run_fixture(module)
        self.assertEqual(code, 1)
        self.assertIn('unsupported discovery suite semantics', text)

    def test_incomplete_or_incompatible_lifecycles_fail(self):
        for mode in ('no_outcome', 'no_stop', 'post_stop', 'two_successes',
                     'success_then_expected_failure', 'subtest_without_parent'):
            with self.subTest(mode=mode):
                body_ran = []
                class Case(unittest.TestCase):
                    def test_body(self):
                        body_ran.append(True)
                    def run(self, result=None):
                        result.startTest(self)
                        if mode == 'no_stop':
                            result.addSuccess(self)
                            return result
                        if mode == 'post_stop':
                            result.stopTest(self)
                            result.addSuccess(self)
                            return result
                        if mode in ('two_successes', 'success_then_expected_failure'):
                            result.addSuccess(self)
                            if mode == 'two_successes':
                                result.addSuccess(self)
                            else:
                                try:
                                    self.fail('intentional contradictory callback')
                                except AssertionError:
                                    result.addExpectedFailure(self, sys.exc_info())
                        if mode == 'subtest_without_parent':
                            subtest = unittest.case._SubTest(self, None, {})
                            result.addSubTest(self, subtest, None)
                        result.stopTest(self)
                        return result
                module = types.ModuleType('fixture')
                module.Case = Case
                code, output = self.run_fixture(module)
                self.assertEqual(body_ran, [])
                self.assertEqual(code, 1, output)

    def test_normal_module_class_body_and_cleanup_fixtures_are_preserved(self):
        events = []
        module = types.ModuleType('curated_fixture')
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
                with self.subTest(value=1):
                    events.append('body')
        Case.__module__ = module.__name__
        module.Case = Case
        with mock.patch.dict(sys.modules, {module.__name__: module}):
            code, output = self.run_fixture(module)
        self.assertEqual(code, 0, output)
        self.assertEqual(events, ['module_setup', 'class_setup', 'body', 'cleanup',
                                  'class_teardown', 'module_teardown'])

    def test_matching_count_cannot_replace_missing_test_with_another_identity(self):
        bodies = []
        class First(unittest.TestCase):
            def test_first(self):
                bodies.append('first')
        first = First('test_first')
        class Second(unittest.TestCase):
            def test_second(self):
                bodies.append('second')
            def run(self, result=None):
                return first.run(result)
        module = types.ModuleType('fixture')
        module.load_tests = lambda loader, tests, pattern: unittest.TestSuite(
            [first, Second('test_second')])
        code, output = self.run_fixture(module)
        self.assertEqual(bodies, ['first', 'first'])
        self.assertIn('Ran 2 tests', output)
        self.assertEqual(code, 1, output)
        self.assertIn('duplicate execution IDs', output)

    def test_unexpected_system_exit_from_import_discovery_or_run_always_fails(self):
        for code in (0, 2, None):
            for boundary in ('import', 'load_tests', 'run'):
                with self.subTest(code=code, boundary=boundary):
                    def stop(*args, **kwargs):
                        raise SystemExit(code)
                    module = self.module()
                    if boundary == 'import':
                        with mock.patch.object(ci_pr.importlib, 'import_module',
                                               side_effect=stop):
                            self.assertEqual(ci_pr.run_curated(('fixture',),
                                                              stream=io.StringIO()), 1)
                    else:
                        if boundary == 'load_tests':
                            module.load_tests = stop
                        else:
                            module.Case.run = stop
                        result, output = self.run_fixture(module)
                        self.assertEqual(result, 1, output)
                        self.assertIn('unexpected SystemExit', output)

    def test_keyboard_interrupt_is_not_converted_to_success(self):
        with mock.patch.object(ci_pr.importlib, 'import_module',
                               side_effect=KeyboardInterrupt), \
                self.assertRaises(KeyboardInterrupt):
            ci_pr.run_curated(('fixture',), stream=io.StringIO())

    def test_required_modules_are_unique_and_include_safety_and_gate_controls(self):
        self.assertEqual(len(ci_pr.PR_MODULES), len(set(ci_pr.PR_MODULES)))
        # Keep the historical test ID; the approved smoke inventory replaces
        # the earlier curated safety/media set, which remains in full discovery.
        self.assertEqual(ci_pr.PR_MODULES, (
            'tests.ci.test_ci_pr',
            'tests.distribution.test_version_sources',
            'tests.cache.test_graph_digest',
            'tests.plugins.test_config_unified',
        ))

    def test_unrecognized_argument_is_an_error(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            ci_pr.main(['--pretend-full'])
        self.assertEqual(raised.exception.code, 2)


class SmokeInventoryContract(unittest.TestCase):
    def test_inventory_discovers_complete_nonempty_modules_without_duplicate_ids(self):
        loader = ci_pr.OriginLoader()
        keepalive = unittest.TestSuite()
        selected = []
        for name in ci_pr.PR_MODULES:
            suite = loader.loadTestsFromModule(ci_pr.importlib.import_module(name))
            keepalive.addTest(suite)
            self.assertFalse(loader.errors)
            self.assertGreater(suite.countTestCases(), 0)
            selected.extend(loader.origins[id(test)] + '::' + test.id()
                            for test in ci_pr.flatten(suite))
        self.assertTrue(selected)
        self.assertEqual(len(selected), len(set(selected)))

    def test_default_cli_uses_same_fail_closed_runner(self):
        for code in (0, 1):
            with self.subTest(code=code), \
                    mock.patch.object(ci_pr, 'run_curated', return_value=code) as run:
                self.assertEqual(ci_pr.main([]), code)
                run.assert_called_once_with(ci_pr.PR_MODULES)

    def test_empty_or_duplicate_inventory_is_rejected_by_default_cli(self):
        for names in ((), ('fixture', 'fixture')):
            with self.subTest(names=names), \
                    mock.patch.object(ci_pr, 'PR_MODULES', names), \
                    contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(ci_pr.main([]), 1)

    def test_retired_group_selection_is_not_a_second_entrypoint(self):
        for group in ('1', '2', '3', 'full'):
            with self.subTest(group=group), \
                    contextlib.redirect_stderr(io.StringIO()), \
                    self.assertRaises(SystemExit) as raised:
                ci_pr.main(['--group', group])
            self.assertEqual(raised.exception.code, 2)

    def test_whole_module_discovery_includes_unlisted_new_methods(self):
        modules = {}
        bodies = []
        for name in ('first', 'second', 'future'):
            module = types.ModuleType(name)
            class Case(unittest.TestCase):
                def test_original(self):
                    bodies.append((self.__class__.__module__, 'original'))
                def test_new_unknown_method(self):
                    bodies.append((self.__class__.__module__, 'new'))
            Case.__module__ = name
            module.Case = Case
            modules[name] = module
        with mock.patch.dict(sys.modules, modules):
            self.assertEqual(ci_pr.run_curated(tuple(modules),
                                              stream=io.StringIO()), 0)
        self.assertEqual(Counter(bodies), Counter(
            (name, method) for name in modules for method in ('original', 'new')))


if __name__ == '__main__':
    unittest.main()
