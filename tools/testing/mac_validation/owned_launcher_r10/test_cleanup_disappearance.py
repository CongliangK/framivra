"""Approved ordinary cleanup observation mocks: 8 methods, 24 fixtures.

No native process, actual ps/libproc, security, PID-reuse or path tests.
New identity-mismatch fixture is excluded; its guard is static-review only.
"""
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import subprocess

import owned_launcher as launcher
from test_owned_launcher import FakeChild, FakeRegistry


class CleanupDisappearanceTests(unittest.TestCase):
    ROOT = 876543
    AUX = 876545
    LEAF = 876546
    SIBLING = 876547
    NEW = 876548
    OTHER = 876549
    TERMINAL = 876550
    OBSERVER = 876560

    def row(self, pid=None, *, ppid=None, pgid=None, state='S'):
        return dict(pid=self.LEAF if pid is None else pid,
                    ppid=self.AUX if ppid is None else ppid,
                    pgid=self.AUX if pgid is None else pgid, state=state)

    def inventory(self, rows):
        observer = self.row(self.OBSERVER, ppid=1, pgid=self.OBSERVER)
        unrelated = self.row(self.OTHER, ppid=1, pgid=self.OTHER)
        return ''.join('{pid} {ppid} {pgid} {state}\n'.format(**r)
                       for r in [observer, unrelated] + rows)

    def fixture(self, snapshots, *, identities=None, nested='PASS',
                request_stop=False, total=2, before_recheck=None, repeat_last=False):
        registry = FakeRegistry(nested)
        registry.groups.add(self.AUX)
        registry.processes.update({
            self.LEAF: dict(pid=self.LEAF, pgid=self.AUX, start_identity=200),
            self.SIBLING: dict(pid=self.SIBLING, pgid=self.AUX, start_identity=300)})
        registry.terminal_anchors = {self.TERMINAL: {'start_identity': 400}}
        owner = SimpleNamespace(_assert_kill_whitelisted=Mock())
        runtime = launcher.Runtime(owner)
        runtime.now = 0.0
        runtime.clock = lambda: runtime.now
        runtime.sleep = Mock(side_effect=lambda seconds: setattr(runtime, 'now', runtime.now + seconds))
        values = {self.ROOT: 100, self.LEAF: None, self.SIBLING: 300}
        if identities is not None:
            values.update(identities)
        def identity(pid):
            value = values.get(pid)
            return value(runtime) if callable(value) else value
        owner.proc_starttime = Mock(side_effect=identity)
        pending = iter(snapshots)
        last = []
        def query(*args, **kwargs):
            nonlocal last
            item = next(pending, last if repeat_last else [])
            last = item
            if callable(item):
                item = item(runtime)
            if isinstance(item, Exception):
                raise item
            raw = self.inventory(item) if isinstance(item, list) else item
            return SimpleNamespace(stdout=raw, returncode=0)
        runtime.run = Mock(side_effect=query)
        original_recheck = launcher.cleanup_disappearance_recheck
        def recheck(*args):
            if before_recheck:
                before_recheck(runtime)
            return original_recheck(*args)
        with patch.object(launcher.os, 'getpid', return_value=self.OBSERVER), \
                patch.object(launcher.os, 'getpgid', return_value=self.OBSERVER), \
                patch.object(launcher.os, 'killpg') as kill, \
                patch.object(launcher, 'cleanup_disappearance_recheck', side_effect=recheck) as recovery, \
                patch.object(launcher, 'observe_resources') as resource:
            result = launcher.cleanup(FakeChild(), registry, runtime,
                request_stop=request_stop, total_seconds=total)
        kill.assert_not_called()
        resource.assert_not_called()
        self.assertEqual(result['steps'], [])
        self.assertNotIn('"pid": ' + str(self.OTHER), json.dumps(result))
        self.assertNotIn('"pid": ' + str(self.OBSERVER), json.dumps(result))
        self.assertEqual(set(registry.processes), {self.ROOT, self.LEAF, self.SIBLING})
        return SimpleNamespace(result=result, runtime=runtime, registry=registry,
                               recheck=recovery, original=self.inventory(snapshots[0]))

    def first_recheck(self, fixture):
        return fixture.result['observations'][0]['disappearance_recheck']

    def last_followup(self, fixture):
        return self.first_recheck(fixture)['followup_observations'][-1]

    def assert_original_incomplete(self, fixture):
        original = fixture.result['observations'][0]
        self.assertEqual(original['identity_coverage_status'], 'INCOMPLETE')
        failed = [c for c in original['identity_checks'] if c['pid'] == self.LEAF]
        self.assertEqual(failed[0]['status'], 'UNKNOWN')
        self.assertIsNone(failed[0]['observed_start_identity'])
        self.assertEqual(original['observation']['snapshot_sha256'],
                         launcher.hashlib.sha256(fixture.original.encode()).hexdigest())
        self.assertTrue(original['observation']['live_rows'])

    def test_structural_cleanup_inventory_is_validated_and_privacy_filtered(self):
        valid = self.inventory([self.row()])
        variants = [valid, valid + 'malformed\n', valid + self.inventory([]).splitlines()[0] + '\n',
                    ''.join(valid.splitlines(keepends=True)[1:])]
        for index, raw in enumerate(variants):
            with self.subTest(index=index):
                runtime = launcher.Runtime(object())
                runtime.clock = lambda: 0.0
                runtime.run = Mock(return_value=SimpleNamespace(stdout=raw, returncode=0))
                with patch.object(launcher.os, 'getpid', return_value=self.OBSERVER), \
                        patch.object(launcher.os, 'getpgid', return_value=self.OBSERVER):
                    if index:
                        with self.assertRaises(ValueError):
                            runtime.observe_groups({self.AUX}, 2)
                        self.assertEqual(runtime.cleanup_query_evidence['validation_status'], 'INCOMPLETE')
                    else:
                        result = runtime.observe_groups({self.AUX}, 2)
                        self.assertIsInstance(result, launcher.CleanupObservation)
                        self.assertEqual(len(result.inventory), 3)
                        self.assertEqual(result['matching_rows'], [self.row()])
                        self.assertEqual(result['argv'], ['/bin/ps', '-axo', 'pid=,ppid=,pgid=,stat='])
                        self.assertNotIn(str(self.OTHER), json.dumps(result))
                        self.assertNotIn(str(self.OBSERVER), json.dumps(result))
                        self.assertEqual(result['validation_status'], 'VALID')
                        self.assertEqual(result['snapshot_sha256'], launcher.hashlib.sha256(raw.encode()).hexdigest())
                        self.assertEqual(result['deadline_monotonic'], 2)
                        self.assertEqual(result['started_monotonic'], 0)
                        self.assertEqual(result['finished_monotonic'], 0)

    def test_absent_retained_auxiliary_leaf_resolves_one_pending_sequence(self):
        for sibling in (False, True):
            with self.subTest(sibling=sibling):
                rest = [self.row(self.SIBLING)] if sibling else []
                snapshots = ([[self.row()] + rest, rest, rest, []] if sibling else
                             [[self.row()], [self.row()], [self.row(state='Z')], [], []])
                f = self.fixture(snapshots)
                self.assertEqual(f.result['status'], 'PASS', f.result)
                self.assertEqual(self.first_recheck(f)['status'], 'RESOLVED_ABSENT')
                self.assertEqual(f.result['recovered_incomplete_observations'], 1)
                self.assertEqual(f.recheck.call_count, 1)
                self.assertEqual(f.runtime.run.call_count, 4 if sibling else 5)
                self.assertEqual(f.result['failure_reasons'], [])
                self.assertEqual(f.result['final_observation']['live_rows'], [])
                self.assert_original_incomplete(f)
                if sibling:
                    checks = self.last_followup(f)['identity_checks']
                    self.assertEqual([(c['pid'], c['status']) for c in checks], [(self.SIBLING, 'PASS')])
                    self.assertAlmostEqual(f.result['natural_wait']['wall_seconds'], .2)
                else:
                    attempts = self.first_recheck(f)['followup_observations']
                    self.assertEqual([a['status'] for a in attempts],
                                     ['PENDING_ABSENCE', 'PENDING_ABSENCE', 'RESOLVED_ABSENT'])
                    self.assertIsNone(attempts[0]['identity_checks'][0]['observed_start_identity'])
                    self.assertEqual(attempts[1]['observation']['matching_rows'][0]['state'], 'Z')
                    self.assertEqual(attempts[1]['identity_checks'], [])
                    self.assertAlmostEqual(f.result['natural_wait']['wall_seconds'], .3)

    def test_failed_pid_still_visible_never_becomes_absent(self):
        for state, pgid in [('S', self.OTHER), ('Z', self.AUX)]:
            with self.subTest(state=state):
                # A structural group-change response is not a PID-reuse exercise.
                snapshots = [[self.row()], [self.row(state=state, pgid=pgid)]]
                f = self.fixture(snapshots if state == 'Z' else snapshots + [[]],
                                 repeat_last=state == 'Z', total=.3 if state == 'Z' else 2)
                self.assertEqual(f.result['status'], 'UNKNOWN')
                self.assertIn('TimeoutError' if state == 'Z' else 'changed retained PGID',
                              self.first_recheck(f)['error'])
                self.assertEqual(f.runtime.run.call_count, 3)
                self.assertEqual(f.result['final_observation']['live_rows'], [])
                if state == 'Z':
                    self.assertEqual(self.last_followup(f)['status'], 'PENDING_ABSENCE')
                    self.assertEqual(f.result['final_observation']['matching_rows'][0]['state'], 'Z')
                    self.assertAlmostEqual(f.result['wall_seconds'], .3)
                self.assert_original_incomplete(f)

    def test_noneligible_failures_remain_sticky(self):
        variants = [
            ('unretained', [self.row(self.NEW)], {}, False),
            ('root', [self.row(self.ROOT, pgid=self.ROOT)], {self.ROOT: None}, False),
            ('stop', [self.row()], {}, True),
            ('verified_root_plus_auxiliary', [self.row(self.ROOT, pgid=self.ROOT), self.row()], {}, False)]
        for label, original, identities, stop in variants:
            with self.subTest(label=label):
                f = self.fixture([original, [], []], identities=identities, request_stop=stop)
                self.assertEqual(f.result['status'], 'UNKNOWN')
                if label == 'verified_root_plus_auxiliary':
                    self.assertEqual(self.first_recheck(f)['status'], 'RESOLVED_ABSENT')
                    self.assertTrue(any('root_group_live_after_success' in r for r in f.result['failure_reasons']))
                    self.assertEqual(f.runtime.run.call_count, 3)
                else:
                    self.assertEqual(f.runtime.run.call_count, 2)
                    if not stop:
                        self.assertEqual(self.first_recheck(f)['status'], 'UNKNOWN')
                        self.assertNotIn('followup_observations', self.first_recheck(f))
                    else:
                        f.recheck.assert_not_called()
                self.assertTrue(f.result['failure_reasons'])

    def test_original_and_fresh_descendants_block_leaf_recovery(self):
        original_child = self.row(self.NEW, ppid=self.LEAF, pgid=self.NEW)
        grandchild = self.row(self.NEW + 10, ppid=self.NEW, pgid=self.NEW + 10)
        cases = [
            ([self.row(), original_child, grandchild], [], 2),
            ([self.row()], [original_child], 3),
            ([self.row()], [self.row(self.NEW, ppid=self.TERMINAL, pgid=self.NEW)], 3)]
        for index, (original, fresh, queries) in enumerate(cases):
            with self.subTest(index=index):
                f = self.fixture([original, fresh, []])
                self.assertEqual(f.result['status'], 'UNKNOWN')
                self.assertEqual(f.runtime.run.call_count, queries)
                recheck = self.first_recheck(f)
                self.assertEqual(recheck['status'], 'UNKNOWN')
                if index == 0:
                    self.assertEqual({r['pid'] for r in recheck['original_related_rows']},
                                     {self.LEAF, self.NEW, self.NEW + 10})
                else:
                    self.assertIn(self.NEW, {r['pid'] for r in self.last_followup(f)['related_rows']})

    def test_recheck_query_errors_remain_unknown(self):
        for error in (subprocess.TimeoutExpired('ordinary mock cleanup ps', 1),
                      OSError('ordinary mock query failure'), 'malformed\n'):
            with self.subTest(error=type(error).__name__):
                f = self.fixture([[self.row()], error, []])
                self.assertEqual(f.result['status'], 'UNKNOWN')
                self.assertEqual(f.runtime.run.call_count, 3)
                self.assertEqual(f.recheck.call_count, 1)
                recheck = self.first_recheck(f)
                self.assertEqual(recheck['status'], 'UNKNOWN')
                self.assertIn('error', recheck)
                self.assertIn('query', self.last_followup(f))
                self.assertNotEqual(self.last_followup(f)['query']['validation_status'], 'VALID')
                self.assert_original_incomplete(f)

    def test_recheck_and_identity_calls_share_existing_deadline(self):
        def late_query(runtime):
            runtime.now = 1.01
            return []
        def late_identity(runtime):
            runtime.now = 1.01
            return 300
        variants = [
            ('before_query', [], {}, lambda r: setattr(r, 'now', 1), 1),
            ('after_query', late_query, {}, None, 2),
            ('after_identity', [self.row(self.SIBLING)], {self.SIBLING: late_identity}, None, 2)]
        for label, fresh, identities, before, queries in variants:
            with self.subTest(label=label):
                f = self.fixture([[self.row()], fresh, []], identities=identities,
                                 before_recheck=before, total=1)
                self.assertEqual(f.result['status'], 'UNKNOWN')
                self.assertEqual(f.runtime.run.call_count, queries)
                self.assertIn('TimeoutError', self.first_recheck(f)['error'])
                self.assertEqual(f.result['budget_seconds'], 1)
                self.assertTrue(all(call.kwargs['timeout'] <= 1 for call in f.runtime.run.call_args_list))
                if label == 'before_query':
                    self.assertEqual(self.first_recheck(f)['followup_observations'], [])

    def test_fresh_coverage_and_prior_failures_stay_required(self):
        variants = [
            ([self.row(self.SIBLING)], {self.SIBLING: None}, 'PASS'),
            ([self.row(self.NEW, ppid=1)], {}, 'PASS'),
            ([], {}, 'UNKNOWN')]
        for index, (fresh, identities, nested) in enumerate(variants):
            with self.subTest(index=index):
                f = self.fixture([[self.row()], fresh, []], identities=identities, nested=nested)
                self.assertEqual(f.result['status'], 'UNKNOWN')
                self.assertEqual(f.runtime.run.call_count, 3)
                self.assertEqual(f.recheck.call_count, 1)
                recheck = self.first_recheck(f)
                if index < 2:
                    self.assertIn('coverage unknown', recheck['error'])
                    self.assertTrue(any(c['status'] == 'UNKNOWN' for c in self.last_followup(f)['identity_checks']))
                else:
                    self.assertEqual(recheck['status'], 'RESOLVED_ABSENT')
                    self.assertEqual(f.result['nested']['status'], 'UNKNOWN')
                self.assert_original_incomplete(f)
