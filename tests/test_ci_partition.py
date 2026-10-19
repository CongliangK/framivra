"""Fail-closed partitioning preserves discovery instances and real execution.

These tests exercise synthetic subprocess suites, including failure, timeout,
setup-level skip and an incorrectly zero-execution TestCase. They never launch
native renderer integrations or replace the existing strict media preflight.
"""
import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('ci_partition_under_test', ROOT / 'scripts/ci_partition.py')
ci = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ci)


def instance(name='test_ok', module='tests.test_sample', case='Case', origin=None):
    return (origin or module) + '::' + module + '.' + case + '.' + name


def policy_for(discovered):
    return {'schema_version': 1, 'baseline': {'batches': copy.deepcopy(discovered)}, 'placements': []}


class InventoryContract(unittest.TestCase):
    def setUp(self):
        self.discovered = {'tests': [instance()]}
        self.policy = policy_for(self.discovered)

    def build(self, discovered=None, policy=None, media=None, strict=None):
        return ci.policy_inventory(self.discovered if discovered is None else discovered,
                                   self.policy if policy is None else policy,
                                   {} if media is None else media,
                                   {} if strict is None else strict)

    def test_original_and_new_cases_stay_fast_and_visible(self):
        discovered = {'tests': [instance(), instance('test_new')]}
        result = self.build(discovered)
        self.assertEqual([r['partition'] for r in result], ['fast', 'fast'])
        self.assertEqual([r['new_since_baseline'] for r in result], [False, True])

    def test_new_discovery_batch_stays_visible(self):
        found = dict(self.discovered, **{'apps/new/tests': [instance()]})
        result = self.build(found)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[1]['canonical'], 'apps.new.tests.test_sample.Case.test_ok')

    def test_same_test_id_in_different_batches_is_not_deduplicated(self):
        found = {'tests': [instance()], 'apps/example/tests': [instance()]}
        result = self.build(found, policy_for(found))
        self.assertEqual(len({r['key'] for r in result}), 2)
        self.assertEqual(len({r['id'] for r in result}), 1)

    def test_imported_case_aliases_preserve_both_original_executions(self):
        found = {'tests': [instance(), instance(origin='tests.test_alias')]}
        result = self.build(found, policy_for(found))
        self.assertEqual(len({r['key'] for r in result}), 2)
        self.assertEqual(len({r['id'] for r in result}), 1)

    def test_missing_original_case_fails(self):
        with self.assertRaisesRegex(ci.PartitionError, 'missing baseline test'):
            self.build({'tests': [instance('test_replacement')]})

    def test_missing_original_batch_fails(self):
        with self.assertRaisesRegex(ci.PartitionError, 'missing baseline batch'):
            self.build({'apps/new/tests': [instance()]})

    def test_duplicate_discovery_instance_fails(self):
        with self.assertRaisesRegex(ci.PartitionError, 'duplicate'):
            self.build({'tests': [instance(), instance()]})

    def test_duplicate_baseline_instance_fails(self):
        self.policy['baseline']['batches']['tests'].append(instance())
        with self.assertRaisesRegex(ci.PartitionError, 'duplicate'):
            self.build()

    def test_empty_and_stale_selectors_fail(self):
        for selector in ('', ' ', 'tests.test_missing.Case'):
            self.policy['placements'] = [{'selector': selector, 'partition': 'optional', 'reason': 'fixture'}]
            with self.subTest(selector=selector), self.assertRaises(ci.PartitionError):
                self.build()

    def test_overlapping_selectors_fail_even_same_partition(self):
        self.policy['placements'] = [
            {'selector': s, 'partition': 'optional', 'reason': 'fixture'}
            for s in ('tests.test_sample', 'tests.test_sample.Case')]
        with self.assertRaisesRegex(ci.PartitionError, 'overlapping'):
            self.build()

    def test_media_and_optional_overlap_fails(self):
        self.policy['placements'] = [{'selector': 'tests.test_sample', 'partition': 'optional', 'reason': 'fixture'}]
        with self.assertRaisesRegex(ci.PartitionError, 'overlapping'):
            self.build(media={'tests.test_sample.Case': 'ffmpeg'})

    def test_new_gated_media_is_real_and_strict_membership_visible(self):
        result = self.build(media={'tests.test_sample.Case': 'ffmpeg'},
                            strict={'ffmpeg': ['tests.test_sample']})
        self.assertEqual(result[0]['partition'], 'real')
        self.assertEqual(result[0]['strict_gates'], ['ffmpeg'])

    def test_empty_and_stale_strict_selectors_fail(self):
        for strict in ({'ffmpeg': []}, {'ffmpeg': ['tests.missing']}):
            with self.subTest(strict=strict), self.assertRaises(ci.PartitionError):
                self.build(strict=strict)

    def test_malformed_or_empty_baseline_fails(self):
        for baseline in ({}, {'tests': []}):
            self.policy['baseline']['batches'] = baseline
            with self.subTest(baseline=baseline), self.assertRaises(ci.PartitionError):
                self.build()

    def test_json_duplicate_keys_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'policy.json'
            path.write_text('{"schema_version": 1, "schema_version": 1}')
            with self.assertRaisesRegex(ci.PartitionError, 'duplicate JSON keys'):
                ci.load_json(path)

    def test_reviewed_environment_migrations_remain_protected(self):
        policy = ci.load_json(ROOT / 'scripts/ci_partition_policy.json')
        found = copy.deepcopy(policy['baseline']['batches'])
        replacements = policy['baseline']['refresh']['replacements']
        expected = {
            ('test_gfx_cache_integrity', 'CacheIntegrity',
             'test_missing_or_malformed_marker_migrates_only_after_full_validation',
             'test_missing_or_malformed_marker_requires_environment_rerender'),
            ('test_gfx_cli_fallback', 'GfxCliFallbackTests',
             'test_cli_shot_cmd_matches_legacy_flags', 'test_cli_shot_cmd_pins_visual_flags'),
            ('test_impact_identity', 'ClipDependencies',
             'test_legacy_and_empty_keys_unchanged',
             'test_environment_migration_preserves_empty_dependency_equivalence'),
        }
        expected_pairs = set()
        for module, case, before, after in expected:
            prefix = 'tests.' + module + '::tests.' + module + '.' + case + '.'
            expected_pairs.add((prefix + before, prefix + after))
        self.assertEqual({(r['previous_instance'], r['replacement_instance'])
                          for r in replacements}, expected_pairs)
        self.assertEqual(len(replacements), 3)
        protected = []
        for row in replacements:
            self.assertEqual(row['batch'], 'tests')
            self.assertTrue(row['reason'])
            self.assertNotIn(row['previous_instance'], found['tests'])
            self.assertIn(row['replacement_instance'], found['tests'])
            protected.append(row['replacement_instance'])
        # These 14 additional PR #196 contracts must be protected, not merely
        # discovered as unprotected new cases after refreshing the three names.
        environment = [name for name in found['tests']
                       if name.startswith('tests.test_gfx_environment::')]
        self.assertEqual(len(environment), 13)
        evidence = ('tests.test_gfx_evidence::tests.test_gfx_evidence.Evidence.'
                    'test_production_input_environment_is_plan_bound_and_unknown_never_passes')
        self.assertIn(evidence, found['tests'])
        for name in protected + environment + [evidence]:
            missing = copy.deepcopy(found)
            missing['tests'].remove(name)
            with self.subTest(instance=name), self.assertRaisesRegex(
                    ci.PartitionError, 'missing baseline test IDs'):
                ci.policy_inventory(missing, policy, {}, {})

    def test_refreshed_baseline_preserves_alias_occurrences_and_unknown_tests(self):
        policy = ci.load_json(ROOT / 'scripts/ci_partition_policy.json')
        found = copy.deepcopy(policy['baseline']['batches'])
        originals = ci.policy_inventory(found, policy, {}, {})
        aliases = [r for r in originals if r['discovery_module'] != r['id'].rsplit('.', 2)[0]]
        self.assertEqual(len(aliases), 2)
        for record in aliases:
            matching = [r for r in originals
                        if r['batch'] == record['batch'] and r['id'] == record['id']]
            self.assertEqual(len(matching), 2)
            for occurrence in matching:
                missing = copy.deepcopy(found)
                missing[occurrence['batch']].remove(occurrence['instance'])
                with self.subTest(instance=occurrence['instance']), self.assertRaisesRegex(
                        ci.PartitionError, 'missing baseline test IDs'):
                    ci.policy_inventory(missing, policy, {}, {})
        unknown = instance('test_future', module='tests.test_future')
        found['tests'].append(unknown)
        records = ci.policy_inventory(found, policy, {}, {})
        self.assertEqual(len(records), len(originals) + 1)
        added = next(r for r in records if r['instance'] == unknown)
        self.assertEqual(added['partition'], 'fast')
        self.assertTrue(added['new_since_baseline'])
        self.assertEqual({r['key'] for r in originals},
                         {r['key'] for r in records if not r['new_since_baseline']})

    def test_current_repository_inventory_preserves_original_and_strict_scopes(self):
        with contextlib.redirect_stdout(io.StringIO()):
            result = ci.inventory(ROOT)
        self.assertEqual(result['baseline']['discovered_count'], 2346)
        self.assertEqual(result['baseline']['unique_test_ids'], 2344)
        self.assertEqual(result['strict_counts'], {'ffmpeg': 115, 'chrome': 14, 'demo-toolchain': 5})
        utf8_subtitle = next(record for record in result['tests'] if record['canonical'] ==
                             'apps.blender-bridge.tests.test_subs_alpha.HappyPath.'
                             'test_utf8_timeline_with_utf8_mode_disabled')
        self.assertEqual(utf8_subtitle['partition'], 'real')
        self.assertIn('ffmpeg', utf8_subtitle['strict_gates'])
        self.assertEqual(len(result['batches']), 16)
        self.assertEqual(sum(result['counts'].values()), len(result['tests']))
        self.assertEqual(len({r['key'] for r in result['tests']}), len(result['tests']))
        # All harness negatives and real file/process safety remain resident.
        for record in result['tests']:
            if record['id'].startswith(('tests.test_ci_partition.', 'tests.test_proc_supervisor.', 'tests.safety.')):
                self.assertEqual(record['partition'], 'fast', record['id'])
        self.assertEqual(result['compatibility']['versions'], list(ci.PYTHONS))


class ExecutionReconciliation(unittest.TestCase):
    def setUp(self):
        self.request = {'attempt_id': 'unique-attempt', 'batch': 'tests',
                        'selected': [instance()], 'discovered': [instance()]}
        self.receipt = dict(self.request, unittest_success=True, suite_events=[], records=[
            {'id': instance(), 'started': 1.0, 'completed': 2.0, 'outcome': 'success',
             'outcomes': ['success'], 'stop_count': 1,
             'events': [{'kind': 'start', 'time': 1.0},
                        {'kind': 'outcome', 'outcome': 'success', 'time': 1.5},
                        {'kind': 'stop', 'time': 2.0}]}], testsRun=1, skipped=0,
            failures=0, errors=0, unexpectedSuccesses=0, expectedFailures=0)

    def test_complete_execution_passes(self):
        self.assertEqual(ci.reconcile(self.request, self.receipt, 0), 'PASS')

    def test_suite_events_requires_an_explicit_list(self):
        for strict in (False, True):
            missing = dict(self.receipt)
            del missing['suite_events']
            for label, receipt in [('missing', missing)] + [
                    (repr(value), dict(self.receipt, suite_events=value))
                    for value in ({}, '', None, False, 0, ())]:
                with self.subTest(strict=strict, value=label), self.assertRaisesRegex(
                        ci.PartitionError, 'suite_events must be an explicit list'):
                    ci.reconcile(self.request, receipt, 0, strict=strict)

    def test_missing_start_or_empty_selection_fails(self):
        self.receipt['records'] = []
        with self.assertRaisesRegex(ci.PartitionError, 'selected/started'):
            ci.reconcile(self.request, self.receipt, 0)
        self.request['selected'] = []
        self.receipt['selected'] = []
        with self.assertRaisesRegex(ci.PartitionError, 'empty execution'):
            ci.reconcile(self.request, self.receipt, 0)

    def test_missing_completion_or_nonfinite_timestamps_fail(self):
        for end in (None, float('nan'), float('inf'), 0.0, True):
            self.receipt['records'][0]['completed'] = end
            with self.subTest(end=end), self.assertRaisesRegex(ci.PartitionError, 'not completed'):
                ci.reconcile(self.request, self.receipt, 0)

    def test_duplicate_execution_fails(self):
        self.receipt['records'] *= 2
        with self.assertRaisesRegex(ci.PartitionError, 'duplicate execution'):
            ci.reconcile(self.request, self.receipt, 0)

    def test_failure_unknown_and_missing_outcomes_fail(self):
        for outcome in ('failure', 'error', 'unexpected_success', None, 'made_up'):
            self.receipt['records'][0]['outcome'] = outcome
            with self.subTest(outcome=outcome), self.assertRaisesRegex(ci.PartitionError, 'unsuccessful'):
                ci.reconcile(self.request, self.receipt, 0)

    def test_skip_is_never_plain_pass_and_strict_skip_fails(self):
        self.receipt['records'][0]['outcome'] = 'skip'
        self.receipt['records'][0]['outcomes'] = ['skip']
        self.receipt['records'][0]['events'][1]['outcome'] = 'skip'
        self.receipt['skipped'] = 1
        self.assertEqual(ci.reconcile(self.request, self.receipt, 0), 'COMPLETE_WITH_SKIPS')
        with self.assertRaisesRegex(ci.PartitionError, 'strict execution skipped'):
            ci.reconcile(self.request, self.receipt, 0, strict=True)

    def test_nonzero_cancel_timeout_and_hidden_failure_fail(self):
        for code in (1, 7, -15, 124, None):
            with self.subTest(code=code), self.assertRaisesRegex(ci.PartitionError, 'worker failed'):
                ci.reconcile(self.request, self.receipt, code)
        self.receipt['unittest_success'] = False
        with self.assertRaisesRegex(ci.PartitionError, 'worker failed'):
            ci.reconcile(self.request, self.receipt, 0)

    def test_wrong_attempt_batch_selection_or_discovery_fails(self):
        for field, value in (('attempt_id', 'old'), ('batch', 'apps/new/tests'),
                             ('selected', [instance('test_other')]), ('discovered', [])):
            bad = dict(self.receipt, **{field: value})
            with self.subTest(field=field), self.assertRaisesRegex(ci.PartitionError, 'mismatch'):
                ci.reconcile(self.request, bad, 0)

    def test_suite_event_cannot_be_accepted_as_test_execution(self):
        self.receipt['suite_events'] = [{'id': 'setUpClass', 'outcome': 'skip'}]
        self.receipt['skipped'] = 1
        with self.assertRaisesRegex(ci.PartitionError, 'setup/teardown'):
            ci.reconcile(self.request, self.receipt, 0)

    def test_unittest_execution_count_is_required_and_exact(self):
        for count in (0, 2, -1, True, None):
            self.receipt['testsRun'] = count
            with self.subTest(count=count), self.assertRaisesRegex(ci.PartitionError, 'testsRun'):
                ci.reconcile(self.request, self.receipt, 0)
        del self.receipt['testsRun']
        with self.assertRaisesRegex(ci.PartitionError, 'testsRun'):
            ci.reconcile(self.request, self.receipt, 0)

    def test_unittest_outcome_counts_must_match_observed_callbacks(self):
        for field in ('skipped', 'failures', 'errors', 'unexpectedSuccesses', 'expectedFailures'):
            bad = dict(self.receipt, **{field: 1})
            with self.subTest(field=field), self.assertRaisesRegex(ci.PartitionError, 'count mismatch'):
                ci.reconcile(self.request, bad, 0)

    def test_duplicate_stop_or_success_callback_fails(self):
        self.receipt['records'][0]['stop_count'] = 2
        with self.assertRaisesRegex(ci.PartitionError, 'not completed'):
            ci.reconcile(self.request, self.receipt, 0)
        self.receipt['records'][0]['stop_count'] = 1
        self.receipt['records'][0]['outcomes'] *= 2
        self.receipt['records'][0]['events'].insert(2,
            {'kind': 'outcome', 'outcome': 'success', 'time': 1.75})
        with self.assertRaisesRegex(ci.PartitionError, 'duplicate terminal'):
            ci.reconcile(self.request, self.receipt, 0)


    def test_boolean_stop_count_and_inconsistent_summary_fail(self):
        self.receipt['records'][0]['stop_count'] = True
        with self.assertRaisesRegex(ci.PartitionError, 'not completed'):
            ci.reconcile(self.request, self.receipt, 0)
        self.receipt['records'][0]['stop_count'] = 1
        self.receipt['records'][0]['outcome'] = 'expected_failure'
        with self.assertRaisesRegex(ci.PartitionError, 'inconsistent outcome'):
            ci.reconcile(self.request, self.receipt, 0)

    def test_missing_malformed_and_unordered_events_fail(self):
        original = copy.deepcopy(self.receipt['records'][0])
        malformed = [None, [], {}, original['events'][:-1], original['events'][1:],
                     list(reversed(original['events']))]
        for event in ({'kind': 'start', 'time': 1.25}, {'kind': 'stop', 'time': 1.25},
                      {'kind': 'unknown', 'time': 1.25},
                      {'kind': 'outcome', 'outcome': 'unknown', 'time': 1.25}):
            malformed.append([original['events'][0], event] + original['events'][1:])
        for stamp in (True, None, float('nan'), float('inf'), 0.5, 2.5):
            events = copy.deepcopy(original['events'])
            events[1]['time'] = stamp
            malformed.append(events)
        for events in malformed:
            with self.subTest(events=events), self.assertRaises(ci.PartitionError):
                self.receipt['records'][0] = dict(original, events=events)
                ci.reconcile(self.request, self.receipt, 0)
        self.receipt['records'][0] = dict(original, outcomes=['expected_failure'])
        with self.assertRaisesRegex(ci.PartitionError, 'do not match event log'):
            ci.reconcile(self.request, self.receipt, 0)


class EventLifecycleContract(unittest.TestCase):
    def setUp(self):
        class Case(unittest.TestCase):
            def runTest(self): pass
        self.case = Case()
        self.result = ci.RecordingResult(unittest.runner._WritelnDecorator(io.StringIO()),
                                         True, 0, instances={id(self.case): instance()})
        self.request = {'attempt_id': 'event-attempt', 'batch': 'tests',
                        'selected': [instance()], 'discovered': [instance()]}
        self.subtest = unittest.case._SubTest(self.case, 'subtest', {})
        try:
            raise AssertionError('test callback')
        except AssertionError:
            self.error = sys.exc_info()

    def emit(self, *sequence):
        for event in sequence:
            if event == 'start': self.result.startTest(self.case)
            elif event == 'stop': self.result.stopTest(self.case)
            elif event == 'success': self.result.addSuccess(self.case)
            elif event == 'expected_failure': self.result.addExpectedFailure(self.case, self.error)
            elif event == 'unexpected_success': self.result.addUnexpectedSuccess(self.case)
            elif event == 'skip': self.result.addSkip(self.case, 'phase skipped')
            elif event == 'failure': self.result.addFailure(self.case, self.error)
            elif event == 'error': self.result.addError(self.case, self.error)
            elif event == 'subtest_skip': self.result.addSkip(self.subtest, 'subtest skipped')
            elif event == 'subtest_success': self.result.addSubTest(self.case, self.subtest, None)
            elif event == 'subtest_failure': self.result.addSubTest(self.case, self.subtest, self.error)
            else: self.fail('unknown fixture event: ' + event)

    def reconcile(self, strict=False):
        result = self.result
        receipt = dict(self.request, records=result.records, suite_events=result.suite_events,
                       unittest_success=result.wasSuccessful(), testsRun=result.testsRun,
                       skipped=len(result.skipped), failures=len(result.failures), errors=len(result.errors),
                       unexpectedSuccesses=len(result.unexpectedSuccesses), expectedFailures=len(result.expectedFailures))
        return ci.reconcile(self.request, receipt, 0 if result.wasSuccessful() else 1, strict)

    def test_every_callback_before_start_or_after_stop_fails(self):
        callbacks = ('success', 'expected_failure', 'unexpected_success', 'skip', 'failure',
                     'error', 'subtest_skip', 'subtest_success', 'subtest_failure')
        for callback in callbacks:
            for before in (True, False):
                with self.subTest(callback=callback, before=before):
                    self.setUp()
                    valid = ('start', 'success', 'stop')
                    self.emit(*((callback,) + valid if before else valid + (callback,)))
                    with self.assertRaises(ci.PartitionError):
                        self.reconcile()
                    self.assertTrue(self.result.suite_events)

    def test_mixed_and_repeated_aggregate_terminal_outcomes_fail(self):
        for first in ci.TERMINAL_OUTCOMES:
            for second in ci.TERMINAL_OUTCOMES + ('skip', 'failure', 'error', 'subtest_success'):
                with self.subTest(first=first, second=second):
                    self.setUp()
                    self.emit('start', first, second, 'stop')
                    with self.assertRaisesRegex(ci.PartitionError, 'terminal'):
                        self.reconcile()
        for phase in ('skip', 'failure', 'error', 'subtest_skip', 'subtest_failure'):
            for terminal in ci.TERMINAL_OUTCOMES:
                with self.subTest(phase=phase, terminal=terminal):
                    self.setUp()
                    self.emit('start', phase, terminal, 'stop')
                    with self.assertRaisesRegex(ci.PartitionError, 'terminal'):
                        self.reconcile()

    def test_missing_duplicate_and_restarted_lifecycles_fail(self):
        for sequence in (('success',), ('start', 'stop'), ('start', 'success'),
                         ('stop', 'start', 'success', 'stop'),
                         ('start', 'start', 'success', 'stop'),
                         ('start', 'success', 'stop', 'stop'),
                         ('start', 'success', 'stop', 'start', 'success', 'stop'),
                         ('start', 'subtest_success', 'stop'),
                         ('start', 'stop', 'success'), ('start', 'stop', 'expected_failure')):
            with self.subTest(sequence=sequence):
                self.setUp()
                self.emit(*sequence)
                with self.assertRaises(ci.PartitionError):
                    self.reconcile()

    def test_subtest_has_only_one_outcome_per_actual_instance(self):
        for first in ('subtest_success', 'subtest_skip', 'subtest_failure'):
            for second in ('subtest_success', 'subtest_skip', 'subtest_failure'):
                with self.subTest(first=first, second=second):
                    self.setUp()
                    self.emit('start', first, second, 'stop')
                    with self.assertRaisesRegex(ci.PartitionError, 'duplicate subtest'):
                        self.reconcile()

    def test_wrong_subtest_parent_cannot_contribute_success(self):
        self.emit('start')
        wrong = unittest.case._SubTest(unittest.TestCase(), 'wrong parent', {})
        self.result.addSubTest(self.case, wrong, None)
        self.emit('success', 'stop')
        with self.assertRaises(ci.PartitionError):
            self.reconcile()

    def test_successful_subtests_before_parent_terminal_are_valid(self):
        for terminal in ('success', 'expected_failure'):
            with self.subTest(terminal=terminal):
                self.setUp()
                self.emit('start', 'subtest_success', terminal, 'stop')
                self.assertEqual(self.reconcile(strict=True), 'PASS')

    def test_separate_skipped_phases_remain_visible_and_strictly_fail(self):
        self.emit('start', 'skip', 'skip', 'stop')
        self.assertEqual(self.reconcile(), 'COMPLETE_WITH_SKIPS')
        with self.assertRaisesRegex(ci.PartitionError, 'strict execution skipped'):
            self.reconcile(strict=True)


class WorkerIntegration(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ci-partition-negative-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'tests').mkdir()
        (self.root / 'tests/__init__.py').write_text('')

    def source(self, body):
        (self.root / 'tests/test_sample.py').write_text('import unittest\n' + body)

    def run_fixture(self, strict=False):
        found = ci.collect(self.root)
        records = ci.policy_inventory(found, policy_for(found), {}, {})
        manifest = {'batches': found, 'tests': records, 'source': {'fixture': True}, 'policy_sha256': 'fixture'}
        with contextlib.redirect_stdout(io.StringIO()):
            return ci.execute(self.root, manifest, 'fast', 15, strict)

    def test_terminal_order_regressions_fail_in_actual_workers(self):
        statements = {
            'success': 'result.addSuccess(self)',
            'expected_failure': 'result.addExpectedFailure(self, error)',
            'stop': 'result.stopTest(self)',
        }
        for sequence in (('success', 'expected_failure', 'stop'),
                         ('expected_failure', 'success', 'stop'),
                         ('stop', 'success'), ('stop', 'expected_failure')):
            with self.subTest(sequence=sequence):
                self.source("""class Case(unittest.TestCase):
    def test_ok(self): pass
    def run(self, result=None):
        import sys
        try: raise AssertionError('expected failure fixture')
        except AssertionError: error = sys.exc_info()
        result.startTest(self)
""" + ''.join('        ' + statements[event] + '\n' for event in sequence) +
                            '        return result\n')
                report = self.run_fixture(strict=True)
                self.assertEqual(report['status'], 'FAIL', report)
                self.assertEqual(report['attempts'][0]['exit_code'], 0, report)
                self.assertIn('receipt', report['attempts'][0])

    def test_ordinary_expected_failures_and_nested_subtests_pass(self):
        self.source("""class Case(unittest.TestCase):
    @unittest.expectedFailure
    def test_expected(self): self.fail('expected')
    @unittest.expectedFailure
    def test_expected_after_subtests(self):
        with self.subTest(part='success'): pass
        with self.subTest(part='failure'): self.fail('expected in subtest')
    def test_repeated_and_nested_subtests(self):
        for _ in range(2):
            with self.subTest(part='same'):
                with self.subTest(part='nested'): pass
""")
        report = self.run_fixture(strict=True)
        self.assertEqual(report['status'], 'PASS', report)
        self.assertEqual(report['attempts'][0]['receipt']['expectedFailures'], 2)

    def test_ordinary_multiple_skipped_phases_and_subtests_remain_visible(self):
        self.source("""class Case(unittest.TestCase):
    def setUp(self): self.test_case = {'ordinary': True}
    def test_skipped_phases(self):
        self.addCleanup(self.skipTest, 'cleanup skip')
        self.skipTest('body skip')
    def tearDown(self): self.skipTest('teardown skip')
    def test_repeated_skipped_subtests(self):
        for _ in range(2):
            with self.subTest(part='same'): self.skipTest('subtest skip')
""")
        # Stock unittest itself emits three parent skip callbacks for body,
        # tearDown and cleanup. TestResult supplies no phase label, so repeated
        # parent skips cannot be classified as fabricated from callbacks alone.
        control = subprocess.run([sys.executable, '-m', 'unittest', 'discover',
                                  '-s', 'tests', '-t', '.', '-v'], cwd=self.root,
                                 capture_output=True, text=True, timeout=15)
        self.assertEqual(control.returncode, 0, control.stderr)
        self.assertIn('OK (skipped=6)', control.stderr)
        report = self.run_fixture()
        self.assertEqual(report['status'], 'COMPLETE_WITH_SKIPS', report)
        self.assertEqual(report['attempts'][0]['receipt']['skipped'], 6)
        self.assertEqual(self.run_fixture(strict=True)['status'], 'FAIL')

    def test_ordinary_failure_and_multiple_cleanup_errors_remain_failures(self):
        self.source("""class Case(unittest.TestCase):
    def broken(self): raise RuntimeError('cleanup error')
    def test_bad(self):
        self.addCleanup(self.broken)
        self.addCleanup(self.broken)
        with self.subTest(part='bad'): self.fail('subtest failure')
        self.fail('body failure')
    def tearDown(self): raise RuntimeError('teardown error')
""")
        report = self.run_fixture()
        self.assertEqual(report['status'], 'FAIL', report)
        receipt = report['attempts'][0]['receipt']
        self.assertEqual(receipt['failures'], 2)
        self.assertEqual(receipt['errors'], 3)
        self.assertEqual(ci.reconcile_events(receipt['records'][0]),
                         ['failure', 'failure', 'error', 'error', 'error'])

    def test_ordinary_unexpected_success_remains_a_failure(self):
        self.source("""class Case(unittest.TestCase):
    @unittest.expectedFailure
    def test_unexpected(self): pass
""")
        report = self.run_fixture()
        self.assertEqual(report['status'], 'FAIL', report)
        self.assertEqual(report['attempts'][0]['receipt']['unexpectedSuccesses'], 1)

    def test_real_worker_explicit_empty_suite_events_pass(self):
        self.source('class Case(unittest.TestCase):\n    def test_ok(self): pass\n')
        for strict in (False, True):
            with self.subTest(strict=strict):
                report = self.run_fixture(strict=strict)
                self.assertEqual(report['status'], 'PASS', report)
                self.assertEqual(len(report['attempts']), 1)
                receipt = report['attempts'][0]['receipt']
                self.assertIs(type(receipt['suite_events']), list)
                self.assertEqual(receipt['suite_events'], [])
                self.assertEqual(receipt['testsRun'], 1)

    def test_successful_dynamic_load_tests_is_discovered_and_executed(self):
        self.source('''class Hidden(unittest.TestCase):
    def test_ok(self): pass
def load_tests(loader, tests, pattern):
    return unittest.TestSuite([Hidden('test_ok')])
''')
        report = self.run_fixture()
        self.assertEqual(report['status'], 'PASS', report)
        self.assertEqual(len(report['attempts'][0]['receipt']['records']), 1)

    def test_custom_suite_validation_cannot_disappear_in_any_partition(self):
        self.source('''class Case(unittest.TestCase):
    def test_ok(self): pass
''')
        found = ci.collect(self.root)
        manifest = {'batches': found,
                    'tests': ci.policy_inventory(found, policy_for(found), {}, {}),
                    'source': {'fixture': True}, 'policy_sha256': 'fixture'}
        self.source('''class Case(unittest.TestCase):
    def test_ok(self): pass
class ValidatedSuite(unittest.TestSuite):
    def run(self, result, debug=False):
        try: raise RuntimeError('required suite validation failed')
        except RuntimeError:
            import sys
            result.addError(unittest.suite._ErrorHolder('suite validation'), sys.exc_info())
        return super().run(result, debug)
def load_tests(loader, tests, pattern):
    return unittest.TestSuite([ValidatedSuite([Case('test_ok')])])
''')
        control = subprocess.run([sys.executable, '-m', 'unittest', 'discover',
                                  '-s', 'tests', '-t', '.', '-v'], cwd=self.root,
                                 capture_output=True, text=True, timeout=15)
        self.assertEqual(control.returncode, 1)
        self.assertIn('required suite validation failed', control.stderr)
        with self.assertRaisesRegex(ci.PartitionError, 'discovery worker failed'):
            ci.collect(self.root)
        # Even a previously valid inventory cannot bypass execution rediscovery.
        for partition in ('fast', 'all'):
            for strict in (False, True):
                with self.subTest(partition=partition, strict=strict), contextlib.redirect_stdout(io.StringIO()):
                    report = ci.execute(self.root, manifest, partition, 15, strict)
                    self.assertEqual(report['status'], 'FAIL', report)
                    self.assertNotEqual(report['attempts'][0]['exit_code'], 0)

    def test_instance_overridden_suite_hook_is_rejected(self):
        self.source('''class Case(unittest.TestCase):
    def test_ok(self): pass
def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite([Case('test_ok')])
    suite.run = lambda result: result
    return suite
''')
        with self.assertRaisesRegex(ci.PartitionError, 'discovery worker failed'):
            ci.collect(self.root)

    def test_custom_base_suite_container_is_rejected(self):
        self.source('''class Case(unittest.TestCase):
    def test_ok(self): pass
def load_tests(loader, tests, pattern):
    return unittest.BaseTestSuite([Case('test_ok')])
''')
        with self.assertRaisesRegex(ci.PartitionError, 'discovery worker failed'):
            ci.collect(self.root)

    def test_global_suite_method_replacement_is_rejected(self):
        self.source('''class Case(unittest.TestCase):
    def test_ok(self): pass
def load_tests(loader, tests, pattern):
    unittest.TestSuite.run = lambda self, result, debug=False: result
    return tests
''')
        with self.assertRaisesRegex(ci.PartitionError, 'discovery worker failed'):
            ci.collect(self.root)

    def test_import_failure_is_not_a_valid_inventory(self):
        self.source("raise ImportError('deliberate import failure')\n")
        with self.assertRaisesRegex(ci.PartitionError, 'discovery worker failed'):
            ci.collect(self.root)

    def test_empty_discovery_fails(self):
        self.source('')
        with self.assertRaisesRegex(ci.PartitionError, 'discovery worker failed'):
            ci.collect(self.root)

    def test_duplicate_instance_from_load_tests_fails(self):
        self.source('''class Case(unittest.TestCase):
    def test_ok(self): pass
def load_tests(loader, tests, pattern):
    return unittest.TestSuite([Case('test_ok'), Case('test_ok')])
''')
        with self.assertRaisesRegex(ci.PartitionError, 'discovery worker failed'):
            ci.collect(self.root)

    def test_setup_skip_records_reason_and_unstarted_method_as_failure(self):
        self.source('''class Case(unittest.TestCase):
    @classmethod
    def setUpClass(cls): raise unittest.SkipTest('missing optional device')
    def test_ok(self): pass
''')
        report = self.run_fixture()
        self.assertEqual(report['status'], 'FAIL')
        receipt = report['attempts'][0]['receipt']
        self.assertEqual(receipt['records'], [])
        self.assertEqual(receipt['suite_events'][0]['detail'], 'missing optional device')

    def test_zero_execution_override_cannot_pass_on_unittest_ok(self):
        self.source('''class Case(unittest.TestCase):
    def test_ok(self): pass
    def run(self, result=None): return result
''')
        report = self.run_fixture()
        self.assertEqual(report['status'], 'FAIL')
        self.assertIn('selected/started', report['attempts'][0]['error'])

    def test_real_subtest_failure_reaches_nonzero_exit(self):
        self.source('''class Case(unittest.TestCase):
    def test_bad(self):
        with self.subTest(index=1): self.fail('deliberate')
''')
        report = self.run_fixture()
        self.assertEqual(report['status'], 'FAIL')
        self.assertEqual(report['attempts'][0]['exit_code'], 1)
        self.assertEqual(report['attempts'][0]['receipt']['records'][0]['outcome'], 'failure')

    def test_strict_body_skip_fails(self):
        self.source('''class Case(unittest.TestCase):
    def test_skip(self): self.skipTest('missing dependency')
''')
        report = self.run_fixture(strict=True)
        self.assertEqual(report['status'], 'FAIL')
        self.assertIn('strict execution skipped', report['attempts'][0]['error'])

    def test_bounded_worker_timeout_is_an_error(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            ci.run_process([sys.executable, '-c', 'import time; time.sleep(30)'], self.root, 0.1)

    def test_empty_selected_partition_fails(self):
        self.source('''class Case(unittest.TestCase):
    def test_ok(self): pass
''')
        found = ci.collect(self.root)
        manifest = {'batches': found, 'tests': ci.policy_inventory(found, policy_for(found), {}, {})}
        with self.assertRaisesRegex(ci.PartitionError, 'empty partition'):
            ci.execute(self.root, manifest, 'slow', 1)

    def test_missing_manifest_batch_cannot_pass_with_zero_attempts(self):
        self.source('''class Case(unittest.TestCase):
    def test_ok(self): pass
''')
        found = ci.collect(self.root)
        records = ci.policy_inventory(found, policy_for(found), {}, {})
        with self.assertRaisesRegex(ci.PartitionError, 'every discovery batch'):
            ci.execute(self.root, {'batches': {}, 'tests': records}, 'fast', 1)

    def test_valid_work_cannot_hide_selected_ghost_or_omit_discovered_test(self):
        self.source('''class Case(unittest.TestCase):
    def test_ok(self): pass
''')
        found = ci.collect(self.root)
        records = ci.policy_inventory(found, policy_for(found), {}, {})
        ghost = dict(records[0], batch='ghost', key='ghost::' + records[0]['instance'])
        with self.assertRaisesRegex(ci.PartitionError, 'do not match all discovered'):
            ci.execute(self.root, {'batches': found, 'tests': records + [ghost]}, 'fast', 1)
        with self.assertRaisesRegex(ci.PartitionError, 'no test records'):
            ci.execute(self.root, {'batches': found, 'tests': []}, 'fast', 1)

    def test_forged_selected_key_cannot_report_different_executed_identity(self):
        self.source('''class Case(unittest.TestCase):
    def test_ok(self): pass
''')
        found = ci.collect(self.root)
        records = ci.policy_inventory(found, policy_for(found), {}, {})
        records[0]['key'] = 'NEVER_EXECUTED'
        with self.assertRaisesRegex(ci.PartitionError, 'declared selection key'):
            ci.execute(self.root, {'batches': found, 'tests': records}, 'fast', 1)

    def test_skip_followed_by_success_cannot_become_strict_pass(self):
        self.source('''class Case(unittest.TestCase):
    def test_ok(self): pass
    def run(self, result=None):
        result.startTest(self)
        result.addSkip(self, 'dependency absent')
        result.addSuccess(self)
        result.stopTest(self)
        return result
''')
        report = self.run_fixture(strict=True)
        self.assertEqual(report['status'], 'FAIL')
        self.assertEqual(report['attempts'][0]['receipt']['skipped'], 1)
        self.assertEqual(report['attempts'][0]['receipt']['records'][0]['outcome'], 'skip')

    def test_actual_worker_tampered_tests_run_is_rejected(self):
        self.source('''class Case(unittest.TestCase):
    def test_ok(self): pass
    def run(self, result=None):
        super().run(result)
        result.testsRun = 0
        return result
''')
        report = self.run_fixture()
        self.assertEqual(report['status'], 'FAIL')
        self.assertIn('testsRun', report['attempts'][0]['error'])
        self.assertEqual(report['attempts'][0]['receipt']['testsRun'], 0)

    def test_subtest_skip_is_visible_and_strictly_rejected(self):
        self.source('''class Case(unittest.TestCase):
    def test_ok(self):
        with self.subTest(part='missing'): self.skipTest('optional missing')
''')
        report = self.run_fixture()
        self.assertEqual(report['status'], 'COMPLETE_WITH_SKIPS')
        self.assertEqual(report['attempts'][0]['receipt']['suite_events'], [])
        self.assertEqual(self.run_fixture(strict=True)['status'], 'FAIL')

    def test_ordinary_test_case_attribute_is_not_a_subtest_parent(self):
        self.source('''class Case(unittest.TestCase):
    def setUp(self): self.test_case = {'fixture': 'ordinary user attribute'}
    def test_ok(self): self.assertEqual(self.test_case['fixture'], 'ordinary user attribute')
''')
        control = subprocess.run([sys.executable, '-m', 'unittest', 'discover',
                                  '-s', 'tests', '-t', '.', '-v'], cwd=self.root,
                                 capture_output=True, text=True, timeout=15)
        self.assertEqual(control.returncode, 0, control.stderr)
        report = self.run_fixture(strict=True)
        self.assertEqual(report['status'], 'PASS', report)
        receipt = report['attempts'][0]['receipt']
        self.assertEqual(receipt['suite_events'], [])
        self.assertEqual(receipt['records'][0]['outcomes'], ['success'])

    def test_subtest_skip_with_ordinary_parent_attribute_is_still_recorded(self):
        self.source('''class Case(unittest.TestCase):
    def setUp(self): self.test_case = {'fixture': 'ordinary user attribute'}
    def test_ok(self):
        with self.subTest(part='missing'): self.skipTest('optional missing')
''')
        report = self.run_fixture()
        self.assertEqual(report['status'], 'COMPLETE_WITH_SKIPS', report)
        receipt = report['attempts'][0]['receipt']
        self.assertEqual(receipt['suite_events'], [])
        self.assertEqual(receipt['records'][0]['outcomes'], ['skip'])
        self.assertEqual(receipt['skipped'], 1)
        self.assertEqual(self.run_fixture(strict=True)['status'], 'FAIL')


class ShellDefaultContract(unittest.TestCase):
    def test_no_argument_runner_keeps_all_batches_and_failed_exit(self):
        with tempfile.TemporaryDirectory(prefix='ci-shell-default-') as directory:
            root = Path(directory)
            (root / 'scripts').mkdir()
            (root / 'scripts/run_all_tests.sh').write_bytes((ROOT / 'scripts/run_all_tests.sh').read_bytes())
            roots = ['tests', 'apps/a/tests', 'plugins/b/tests', 'templates/c/tests']
            for batch in roots:
                (root / batch).mkdir(parents=True)
            fake = root / 'python3'
            fake.write_text('#!/bin/bash\nprintf "%s\\n" "$*" >> "$CALL_LOG"\n'
                            'if [[ "$*" == *"-s tests -t ."* ]]; then exit 7; fi\nexit 0\n')
            fake.chmod(0o755)
            log = root / 'calls.txt'
            env = dict(os.environ, PATH=str(root) + os.pathsep + os.environ['PATH'], CALL_LOG=str(log))
            result = subprocess.run(['bash', str(root / 'scripts/run_all_tests.sh')], env=env)
            self.assertEqual(result.returncode, 7)
            calls = log.read_text().splitlines()
            self.assertEqual(len(calls), 4)
            for batch, call in zip(roots, calls):
                self.assertIn('-m unittest discover -s ' + batch + ' -t ', call)
            result = subprocess.run(['bash', str(root / 'scripts/run_all_tests.sh'), '--inventory'], env=env)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(log.read_text().splitlines()[-1], 'scripts/ci_partition.py --inventory')


if __name__ == '__main__':
    unittest.main()
