"""Approved finite retained-cohort cleanup mocks: five methods, 17 fixtures.

No actual process, Mac, security/PID-reuse, path or new mismatch exercise.
"""
from collections import deque
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import owned_launcher as launcher
from test_owned_launcher import FakeChild, FakeRegistry


class RetainedCohortCleanupTests(unittest.TestCase):
    ROOT = 876543
    PARENT = 876545
    CHILD = 876546
    NEW = 876548
    OTHER = 876549
    OBSERVER = 876560

    def row(self, pid, *, ppid=None, pgid=None, state='S'):
        return dict(pid=pid, ppid=(1 if pid == self.PARENT else self.PARENT) if ppid is None else ppid,
                    pgid=self.PARENT if pgid is None else pgid, state=state)

    def inventory(self, rows):
        other = [self.row(self.OBSERVER, ppid=1, pgid=self.OBSERVER),
                 self.row(self.OTHER, ppid=1, pgid=self.OTHER)]
        return ''.join('{pid} {ppid} {pgid} {state}\n'.format(**row) for row in other + rows)

    def fixture(self, snapshots, *, identities=None, child_group=None,
                repeat_last=False, total=1, nested='PASS'):
        registry = FakeRegistry(nested)
        registry.processes.update({
            self.PARENT: dict(pid=self.PARENT, pgid=self.PARENT, start_identity=200),
            self.CHILD: dict(pid=self.CHILD, pgid=child_group or self.PARENT, start_identity=300)})
        registry.groups.update({self.PARENT, child_group or self.PARENT})
        before_registry = json.dumps(registry.processes, sort_keys=True)
        before_groups = set(registry.groups)
        registry.terminal_anchors = {}
        owner = SimpleNamespace(_assert_kill_whitelisted=Mock())
        runtime = launcher.Runtime(owner)
        runtime.now = 0.0
        runtime.clock = lambda: runtime.now
        runtime.sleep = Mock(side_effect=lambda seconds: setattr(runtime, 'now', runtime.now + seconds))
        values = {self.ROOT: 100, self.PARENT: 200, self.CHILD: None}
        values.update(identities or {})
        values = {pid: deque(value) if isinstance(value, list) else value for pid, value in values.items()}
        def identity(pid):
            value = values.get(pid)
            if isinstance(value, deque):
                value = value.popleft() if len(value) > 1 else value[0]
            return value(runtime) if callable(value) else value
        owner.proc_starttime = Mock(side_effect=identity)
        pending = iter(snapshots)
        last = []
        def query(*args, **kwargs):
            nonlocal last
            item = next(pending, last if repeat_last else [])
            last = item
            if isinstance(item, Exception):
                raise item
            return SimpleNamespace(stdout=self.inventory(item), returncode=0)
        runtime.run = Mock(side_effect=query)
        with patch.object(launcher.os, 'getpid', return_value=self.OBSERVER), \
                patch.object(launcher.os, 'getpgid', return_value=self.OBSERVER), \
                patch.object(launcher.os, 'killpg') as kill, \
                patch.object(launcher, 'observe_resources') as resources:
            result = launcher.cleanup(FakeChild(), registry, runtime,
                                     request_stop=False, total_seconds=total)
        kill.assert_not_called()
        resources.assert_not_called()
        owner._assert_kill_whitelisted.assert_not_called()
        self.assertEqual(result['steps'], [])
        self.assertEqual(json.dumps(registry.processes, sort_keys=True), before_registry)
        self.assertEqual(registry.groups, before_groups)
        self.assertEqual(registry.terminal_anchors, {})
        serialized = json.dumps(result)
        self.assertNotIn('"pid": ' + str(self.OTHER), serialized)
        self.assertNotIn('"pid": ' + str(self.OBSERVER), serialized)
        original = result['observations'][0]
        self.assertEqual(original['identity_coverage_status'], 'INCOMPLETE')
        self.assertTrue(any(c['status'] == 'UNKNOWN' for c in original['identity_checks']))
        self.assertEqual(original['observation']['snapshot_sha256'],
                         launcher.hashlib.sha256(self.inventory(snapshots[0]).encode()).hexdigest())
        evidence = original['disappearance_recheck']
        self.assertEqual({r['pid'] for r in evidence['target_cohort_identities']},
                         {self.PARENT, self.CHILD})
        self.assertNotIn(self.NEW, {r['pid'] for r in evidence['target_cohort_identities']})
        return SimpleNamespace(result=result, evidence=evidence, runtime=runtime,
                               attempts=evidence['followup_observations'])

    def assert_resolved(self, f):
        self.assertEqual(f.result['status'], 'PASS', f.result)
        self.assertEqual(f.evidence['status'], 'RESOLVED_ABSENT')
        self.assertEqual(f.attempts[-1]['pending_visible_pids'], [])
        self.assertEqual(f.attempts[-1]['status'], 'RESOLVED_ABSENT')
        self.assertTrue(all(a['status'] == 'PENDING_ABSENCE' for a in f.attempts[:-1]))
        self.assertEqual(f.result['final_observation']['matching_rows'], [])
        self.assertEqual(f.result['failure_reasons'], [])
        self.assertEqual(f.result['recovered_incomplete_observations'], 1)

    def test_all_retained_exit_orders_require_global_cohort_absence(self):
        p, c = self.row(self.PARENT), self.row(self.CHILD)
        cases = [
            # Exact supplied r9 shape; ? is recorded, never interpreted as Z.
            ('child_then_parent', [[p, c], [self.row(self.PARENT, state='?')], []],
             {self.PARENT: [200, None], self.CHILD: None}),
            ('both_none', [[p, c], [self.row(self.PARENT, state='Z'), c],
                           [self.row(self.CHILD, state='Z')], []],
             {self.PARENT: None, self.CHILD: None}),
            ('parent_then_child', [[p, c], [c], [self.row(self.CHILD, state='?')], []],
             {self.PARENT: None, self.CHILD: [300, 300, None]}),
            ('verified_peer_outlives_first', [[p, self.row(self.CHILD, ppid=1)],
                 [self.row(self.CHILD, ppid=1)], [self.row(self.CHILD, ppid=1)], []],
             {self.PARENT: None, self.CHILD: 300})]
        for label, snapshots, identities in cases:
            with self.subTest(label=label):
                f = self.fixture(snapshots, identities=identities)
                self.assert_resolved(f)
                self.assertTrue(f.attempts[0]['pending_visible_pids'])
                self.assertEqual(f.runtime.run.call_count, len(snapshots) + 1)
                if label == 'child_then_parent':
                    checks = f.attempts[0]['identity_checks']
                    self.assertEqual([(x['pid'], x['observed_start_identity'], x['status']) for x in checks],
                                     [(self.PARENT, None, 'UNKNOWN')])
                    self.assertEqual(f.attempts[0]['observation']['matching_rows'][0]['state'], '?')
                    self.assertEqual(f.evidence['failed_pids'], [self.CHILD])
                if label == 'verified_peer_outlives_first':
                    self.assertEqual(len(f.attempts), 3)
                    self.assertTrue(all(a['identity_checks'][0]['status'] == 'PASS'
                                        for a in f.attempts[:-1]))

    def test_retained_reparenting_preserves_coverage_without_adoption(self):
        p, c = self.row(self.PARENT), self.row(self.CHILD)
        cases = [
            ('reparented', [[p, c], [self.row(self.CHILD, ppid=1)],
                            [self.row(self.CHILD, ppid=1, state='?')], []],
             {self.PARENT: None, self.CHILD: [300, 300, None]}, None, True),
            ('two_retained_groups', [[p, self.row(self.CHILD, pgid=self.CHILD)],
                                    [self.row(self.CHILD, pgid=self.CHILD)], []],
             {self.PARENT: None, self.CHILD: [300, None]}, self.CHILD, True),
            ('unretained_descendant', [[p, c],
                 [c, self.row(self.NEW, ppid=self.PARENT, pgid=self.NEW)], []],
             {self.PARENT: None, self.CHILD: 300}, None, False)]
        for label, snapshots, identities, group, expected_pass in cases:
            with self.subTest(label=label):
                f = self.fixture(snapshots, identities=identities, child_group=group)
                if expected_pass:
                    self.assert_resolved(f)
                else:
                    self.assertEqual(f.result['status'], 'UNKNOWN')
                    self.assertEqual(f.evidence['status'], 'UNKNOWN')
                    self.assertIn(self.NEW, {r['pid'] for r in f.attempts[0]['related_rows']})
                    self.assertEqual(f.result['final_observation']['matching_rows'], [])

    def test_any_unresolved_cohort_member_reaches_original_deadline(self):
        for state in ('S', '?', 'Z'):
            with self.subTest(state=state):
                f = self.fixture([[self.row(self.PARENT), self.row(self.CHILD)],
                                  [self.row(self.CHILD, state=state)]],
                    identities={self.PARENT: None, self.CHILD: [300, None]},
                    repeat_last=True, total=.3)
                self.assertEqual(f.result['status'], 'UNKNOWN')
                self.assertEqual(f.evidence['status'], 'UNKNOWN')
                self.assertIn('TimeoutError', f.evidence['error'])
                self.assertEqual(f.runtime.run.call_count, 3)
                self.assertAlmostEqual(f.result['wall_seconds'], .3)
                self.assertTrue(all(a['pending_visible_pids'] == [self.CHILD] for a in f.attempts))
                self.assertTrue(all(a['status'] == 'PENDING_ABSENCE' for a in f.attempts))
                self.assertEqual(f.result['budget_seconds'], .3)
                if state != 'Z':
                    self.assertTrue(all(a['identity_checks'][0]['observed_start_identity'] is None
                                        for a in f.attempts))
                else:
                    self.assertTrue(all(a['identity_checks'] == [] for a in f.attempts))

    def test_unretained_or_changed_group_never_joins_cohort(self):
        cases = [
            ('new_live', self.row(self.NEW, ppid=1)),
            ('new_terminal', self.row(self.NEW, ppid=1, state='Z')),
            ('changed_group', self.row(self.CHILD, pgid=self.CHILD))]
        for label, row in cases:
            with self.subTest(label=label):
                f = self.fixture([[self.row(self.PARENT), self.row(self.CHILD)], [row], []],
                                 identities={self.PARENT: None, self.CHILD: 300})
                self.assertEqual(f.result['status'], 'UNKNOWN')
                self.assertEqual(f.evidence['status'], 'UNKNOWN')
                self.assertEqual(f.runtime.run.call_count, 3)
                self.assertTrue(f.result['failure_reasons'])
                self.assertEqual(f.result['final_observation']['matching_rows'], [])
                self.assertIn('changed retained PGID' if label == 'changed_group' else
                              'not covered by frozen retained identity', f.evidence['error'])

    def test_cohort_resolution_preserves_errors_and_measurement_gaps(self):
        def late(runtime):
            runtime.now = 1.01
            return None
        p, c = self.row(self.PARENT), self.row(self.CHILD)
        cases = [
            ('root_residual', [[self.row(self.ROOT, ppid=1, pgid=self.ROOT), p, c], [c], []],
             {self.PARENT: None, self.CHILD: [300, None]}, 'PASS'),
            ('nested_unknown', [[p, c], [c], []],
             {self.PARENT: None, self.CHILD: [300, None]}, 'UNKNOWN'),
            ('late_query_error', [[p, c], [self.row(self.PARENT, state='?'), c],
                                  OSError('ordinary cohort observation error'), []],
             {self.PARENT: None, self.CHILD: [300, None]}, 'PASS'),
            ('late_identity_deadline', [[p, c], [c], [self.row(self.CHILD, state='?')], []],
             {self.PARENT: None, self.CHILD: [300, 300, late]}, 'PASS')]
        for label, snapshots, identities, nested in cases:
            with self.subTest(label=label):
                f = self.fixture(snapshots, identities=identities, nested=nested)
                self.assertEqual(f.result['status'], 'UNKNOWN')
                self.assertNotIn('completion_basis', f.result)
                if label in ('root_residual', 'nested_unknown'):
                    self.assertEqual(f.evidence['status'], 'RESOLVED_ABSENT')
                    if label == 'root_residual':
                        self.assertTrue(any('root_group_live_after_success' in reason
                                            for reason in f.result['failure_reasons']))
                    else:
                        self.assertEqual(f.result['nested']['status'], 'UNKNOWN')
                else:
                    self.assertEqual(f.evidence['status'], 'UNKNOWN')
                    self.assertEqual(f.attempts[0]['status'], 'PENDING_ABSENCE')
                    self.assertEqual(len(f.attempts), 2)
                    self.assertIn('error', f.attempts[1])
                    if label == 'late_query_error':
                        self.assertEqual(f.runtime.run.call_count, 4)
                        self.assertEqual(f.attempts[1]['query']['validation_status'], 'QUERY_FAILED')
                    else:
                        self.assertEqual(f.runtime.run.call_count, 3)
                        self.assertIn('TimeoutError', f.evidence['error'])
                        self.assertEqual(f.result['budget_seconds'], 1)
