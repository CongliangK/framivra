"""Six approved methods / 20 ordinary mock fixtures; no native process probes."""
from io import StringIO
import json
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import owned_launcher as launcher
from resource_summary import summarize_samples


ROOT = 50403
PARENT = 50482
FIRST = 50504
SECOND = 50512
KNOWN = 50503
CHILD = 50530
NEW = 50517
OBSERVER = 900001
UNRELATED = 900002


def process(pid, ppid, state='S', pgid=PARENT, rss=128):
    return dict(pid=pid, ppid=ppid, pgid=pgid, state=state, rss_kib=rss,
                cpu_percent=0.5, cpu_time='00:00.01',
                command='PRIVATE_UNVERIFIED' if pid in (FIRST, SECOND, UNRELATED)
                        else 'ordinary-mock-worker')


def base_rows():
    return [process(ROOT, 1, pgid=ROOT), process(PARENT, ROOT),
            process(KNOWN, PARENT, rss=256)]


def original_rows():
    return base_rows() + [process(FIRST, PARENT), process(SECOND, PARENT, state='Z', rss=0)]


class ResourceCandidateSetRecoveryTests(unittest.TestCase):
    def fixture(self, original, fresh, *, retained=(), fresh_terminals=(),
                extra_identities=None, mode=None):
        state = dict(now=1.0, query_count=0, phase=0)
        identities = {ROOT: 100, PARENT: 200, KNOWN: 300, CHILD: 400, NEW: 500}
        identities.update(extra_identities or {})
        identities.update({FIRST: None, SECOND: None})
        probe_calls = []

        def probe(pid):
            probe_calls.append((state['phase'], pid))
            value = identities.get(pid)
            if mode == 'deadline_during_fresh_identity' and state['phase'] == 1 and pid == PARENT:
                state['now'] = 31.0
            return value

        owner = SimpleNamespace(proc_starttime=Mock(side_effect=probe))
        runtime = launcher.Runtime(owner)
        runtime.clock = Mock(side_effect=lambda: state['now'])
        runtime.sleep = Mock()
        registry = launcher.OwnedRegistry.__new__(launcher.OwnedRegistry)
        registry.owner = owner
        registry.root_pid = ROOT
        registry.processes = {ROOT: dict(pid=ROOT, pgid=ROOT, start_identity=100)}
        for pid in retained:
            registry.processes[pid] = dict(pid=pid, pgid=PARENT, start_identity=pid + 1000)
        registry.groups = {ROOT}
        registry.generations = {}
        registry.terminal_anchors = {}
        registry.read_ledgers = Mock()
        registry.nested_coverage = Mock(return_value={'status': 'PASS'})
        retained_before = {pid: dict(registry.processes[pid]) for pid in retained}
        child = SimpleNamespace(poll=Mock(return_value=None), wait=Mock())

        def inventory(rows):
            structural = [process(OBSERVER, 1, pgid=OBSERVER),
                          process(UNRELATED, 1, pgid=UNRELATED)] + rows
            return ''.join(
                '{pid} {ppid} {pgid} {state} {rss_kib} {cpu_percent} {cpu_time} {command}\n'.format(**r)
                for r in structural)

        def run(argv, **kwargs):
            self.assertEqual(argv, launcher.PS_ARGV)
            self.assertTrue(kwargs['check'])
            self.assertLessEqual(kwargs['timeout'], 2)
            index = state['query_count']
            state['query_count'] += 1
            self.assertLess(index, 2, 'No third inventory or unrelated subprocess is permitted')
            state['phase'] = index
            if index == 0:
                return SimpleNamespace(stdout=inventory(original), returncode=0)
            state['now'] = 2.0
            if mode == 'query_error':
                raise subprocess.CalledProcessError(1, argv, output='ordinary mock query failure')
            return SimpleNamespace(stdout='malformed\n' if mode == 'malformed' else inventory(fresh),
                                   returncode=0)

        runtime.run = Mock(side_effect=run)

        def terminal(row, retained_start, deadline):
            self.assertEqual(deadline, 30)
            qualified = state['phase'] == 1 and row['pid'] in fresh_terminals
            record = dict(pid=row['pid'], ppid=row['ppid'], pgid=row['pgid'],
                ps_state=row['state'], flavor=3, arg=1, returned_bytes=136 if qualified else 0,
                expected_bytes=136, errno=0 if qualified else 3,
                status='QUALIFIED_TERMINAL' if qualified else 'UNKNOWN',
                rss_status='NOT_APPLICABLE_TO_LIVE_RSS' if qualified else 'UNKNOWN',
                ps_reported_rss_kib=row['rss_kib'], ps_reported_cpu_percent=row['cpu_percent'],
                ps_reported_cpu_time=row['cpu_time'])
            if qualified:
                record['start_identity'] = retained_start or row['pid'] + 1000
            return record

        runtime.terminal_information = Mock(side_effect=terminal)

        class EvidenceStream(StringIO):
            def write(self, value):
                result = super().write(value)
                if mode == 'deadline_before_refresh' and '"sample_status": "INCOMPLETE"' in value:
                    state['now'] = 31.0
                return result

        stream = EvidenceStream()
        status = dict(stop_reasons=[], incomplete_samples=0, resource_identity_recoveries=[])
        owned = None
        caught = None
        with patch.object(launcher.os, 'getpid', return_value=OBSERVER), \
                patch.object(launcher.os, 'getpgid', return_value=OBSERVER), \
                patch.object(launcher.time, 'monotonic', side_effect=lambda: state['now']), \
                patch.object(launcher.os, 'killpg') as killpg, \
                patch.object(launcher.os, 'kill') as kill, \
                patch.object(launcher.os, 'waitpid') as waitpid, \
                patch.object(launcher, 'cleanup') as cleanup:
            try:
                owned, before, recovered_id = launcher.observe_resources(
                    child, registry, runtime, stream, status, 30, 0, 1)
            except Exception as error:
                caught = error
            killpg.assert_not_called()
            kill.assert_not_called()
            waitpid.assert_not_called()
            cleanup.assert_not_called()
        runtime.sleep.assert_not_called()
        child.wait.assert_not_called()
        samples = [json.loads(line) for line in stream.getvalue().splitlines()]
        if owned is not None:
            samples.append(dict(wall_seconds=before, processes=owned,
                tree_rss_kib=sum(row['rss_kib'] for row in owned),
                disk_free_bytes=50 * launcher.GIB,
                terminal_process_observations=registry.resource_terminal_observations,
                recovery_of_incomplete_sample_id=recovered_id))
        for pid, record in retained_before.items():
            self.assertEqual(registry.processes[pid], record)
        for pid in {FIRST, SECOND} - set(retained):
            self.assertNotIn(pid, registry.processes)
        self.assertNotIn(FIRST, registry.groups)
        self.assertNotIn(SECOND, registry.groups)
        self.assertFalse(registry.generations)
        self.assertNotIn('PRIVATE_UNVERIFIED', json.dumps(samples) + json.dumps(status))
        self.assertEqual(samples[0]['sample_status'], 'INCOMPLETE')
        self.assertNotIn('processes', samples[0])
        self.assertNotIn('tree_rss_bytes', samples[0])
        self.assertEqual(samples[0]['unavailable_identity_pids'], sorted([FIRST, SECOND]))
        self.assertEqual(samples[0]['verified_rss_lower_bound_bytes'], 512 * 1024)
        self.assertEqual(len([pid for phase, pid in probe_calls if phase == 0]),
                         len({pid for phase, pid in probe_calls if phase == 0}))
        return SimpleNamespace(owned=owned, error=caught, status=status, samples=samples,
            recovery=status['resource_identity_recoveries'][0], registry=registry,
            query_count=runtime.run.call_count, probe_calls=probe_calls,
            terminal_calls=runtime.terminal_information.call_args_list)

    def assert_success(self, result, expected_pids=None):
        self.assertIsNone(result.error)
        self.assertEqual(result.query_count, 2)
        self.assertEqual(result.status['incomplete_samples'], 1)
        self.assertEqual(result.recovery['status'], 'FRESH_COMPLETE')
        self.assertEqual(result.recovery['unresolved_candidate_pids'], [FIRST, SECOND])
        self.assertEqual(result.samples[-1]['recovery_of_incomplete_sample_id'], 'incomplete-1')
        self.assertEqual(summarize_samples(result.samples)['status'], 'PARTIAL')
        self.assertEqual({r['pid'] for r in result.owned}, expected_pids or {ROOT, PARENT, KNOWN})

    def assert_unknown(self, result, queries=2):
        self.assertIsNotNone(result.error)
        self.assertIsNone(result.owned)
        self.assertEqual(result.query_count, queries)
        self.assertEqual(result.recovery['status'], 'UNKNOWN')
        self.assertTrue(all(row.get('sample_status') == 'INCOMPLETE' for row in result.samples))

    def test_two_first_seen_failed_leaves_absent_after_one_refresh(self):
        rows = original_rows()
        orders = [rows, list(reversed(rows)), [rows[4], rows[0], rows[3], rows[2], rows[1]]]
        for index, ordered in enumerate(orders):
            with self.subTest(order=index):
                result = self.fixture(ordered, base_rows())
                self.assert_success(result)
                self.assertTrue(all(r['retained_start_identity'] is None
                                    for r in result.recovery['unresolved_candidates']))
                self.assertEqual({r['visibility'] for r in result.recovery['fresh_candidate_observations']},
                                 {'OBSERVED_ABSENT'})
                self.assertEqual(result.samples[-1]['tree_rss_kib'], 512)
                self.assertFalse(result.samples[-1]['terminal_process_observations'])
                terminal = result.samples[0]['terminal_process_observations'][0]
                self.assertEqual((terminal['returned_bytes'], terminal['expected_bytes'], terminal['errno']),
                                 (0, 136, 3))

    def test_retained_and_first_seen_leaves_use_one_frozen_candidate_set(self):
        for retained in ((FIRST, SECOND), (FIRST,)):
            with self.subTest(retained=retained):
                result = self.fixture(original_rows(), base_rows(), retained=retained)
                self.assert_success(result)
                self.assertEqual(
                    {r['pid'] for r in result.recovery['unresolved_candidates']
                     if r['retained_start_identity'] is not None}, set(retained))

    def test_absent_and_positively_terminal_qualified_candidates_recover(self):
        for terminal_pid in (FIRST, SECOND):
            with self.subTest(terminal_pid=terminal_pid):
                fresh = base_rows() + [process(terminal_pid, PARENT, state='Z', rss=0)]
                result = self.fixture(original_rows(), fresh, fresh_terminals=(terminal_pid,))
                self.assert_success(result)
                self.assertEqual(result.samples[-1]['tree_rss_kib'], 512)
                observations = {r['pid']: r['visibility']
                                for r in result.recovery['fresh_candidate_observations']}
                self.assertEqual(observations[terminal_pid], 'QUALIFIED_TERMINAL')
                self.assertEqual(observations[SECOND if terminal_pid == FIRST else FIRST], 'OBSERVED_ABSENT')
                self.assertEqual(summarize_samples(result.samples)['qualified_terminal_rows_excluded_count'], 1)

    def test_any_visible_unproved_candidate_blocks_whole_set(self):
        for state in ('S', '?', 'Z'):
            with self.subTest(state=state):
                result = self.fixture(original_rows(), base_rows() + [process(SECOND, PARENT, state=state)])
                self.assert_unknown(result)
                self.assertNotIn('QUALIFIED_TERMINAL',
                    {r['visibility'] for r in result.recovery['fresh_candidate_observations']})

    def test_any_original_or_fresh_descendant_blocks_set_recovery(self):
        for phase in ('original', 'fresh'):
            for parent in (FIRST, SECOND):
                with self.subTest(phase=phase, parent=parent):
                    original = original_rows()
                    fresh = base_rows()
                    (original if phase == 'original' else fresh).append(process(CHILD, parent))
                    result = self.fixture(original, fresh)
                    self.assert_unknown(result, queries=1 if phase == 'original' else 2)
                    self.assertNotIn(CHILD, result.registry.processes)
                    if phase == 'fresh':
                        self.assertEqual(result.recovery['fresh_failed_descendant_pids'], [CHILD])

    def test_fresh_live_coverage_and_original_budget_stay_required(self):
        cases = ('fresh_unresolved', 'verified_reparented', 'query_error',
                 'malformed', 'deadline_before_refresh', 'deadline_during_fresh_identity')
        for case in cases:
            with self.subTest(case=case):
                original = original_rows()
                fresh = base_rows()
                identities = {}
                if case == 'fresh_unresolved':
                    fresh.append(process(NEW, PARENT))
                    identities[NEW] = None
                elif case == 'verified_reparented':
                    # KNOWN was independently verified in the incomplete original.
                    fresh = [process(ROOT, 1, pgid=ROOT), process(KNOWN, 1, rss=256)]
                result = self.fixture(original, fresh, extra_identities=identities, mode=case)
                if case == 'verified_reparented':
                    self.assert_success(result, {ROOT, KNOWN})
                    self.assertEqual(result.samples[-1]['tree_rss_kib'], 384)
                else:
                    self.assert_unknown(result, queries=1 if case == 'deadline_before_refresh' else 2)
                if case == 'fresh_unresolved':
                    self.assertEqual(result.status['incomplete_samples'], 2)
                    self.assertEqual(result.samples[1]['failed_pid'], NEW)
                    self.assertNotIn(NEW, result.registry.processes)
                elif case == 'deadline_during_fresh_identity':
                    self.assertNotIn((1, KNOWN), result.probe_calls)
                    self.assertIsInstance(result.error, TimeoutError)
                elif case == 'deadline_before_refresh':
                    self.assertFalse(any(phase == 1 for phase, pid in result.probe_calls))
                    self.assertIsInstance(result.error, TimeoutError)
