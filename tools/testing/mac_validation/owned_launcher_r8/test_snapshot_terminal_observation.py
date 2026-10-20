"""Twelve approved methods, 29 ordinary mock fixtures; no live process exercises."""
import ctypes
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import owned_launcher as launcher
from resource_summary import summarize_samples
from test_owned_launcher import LauncherResourceIdentityRaceTests
from test_resource_summary import sample


F = LauncherResourceIdentityRaceTests
PARENT = 876546
LATE = 876550


def registry(identities, retained=None):
    owner = SimpleNamespace(proc_starttime=Mock(side_effect=lambda pid: identities.get(pid)))
    reg = launcher.OwnedRegistry.__new__(launcher.OwnedRegistry)
    reg.owner = owner
    reg.root_pid = F.ROOT
    reg.processes = {F.ROOT: dict(pid=F.ROOT, pgid=F.ROOT, start_identity=100)}
    reg.processes.update(retained or {})
    reg.groups = {F.ROOT}
    reg.generations = {}
    reg.read_ledgers = Mock()
    return reg


def qualified(row, retained=None):
    return dict(pid=row['pid'], ppid=row['ppid'], pgid=row['pgid'],
        status='QUALIFIED_TERMINAL', start_identity=retained or 200,
        ps_state=row['state'], ps_reported_rss_kib=row['rss_kib'],
        ps_reported_cpu_percent=row['cpu_percent'], ps_reported_cpu_time=row['cpu_time'],
        rss_status='NOT_APPLICABLE_TO_LIVE_RSS')


class CompleteSnapshotObservationTests(unittest.TestCase):
    def test_independent_branches_are_fully_probed_in_any_row_order(self):
        f = F(); root = f.process(f.ROOT, 1)
        leaf = f.process(f.LEAF, f.ROOT, command='UNVERIFIED_PRIVATE_LEAF')
        parent = f.process(PARENT, f.ROOT, pgid=PARENT)
        child = f.process(f.GRANDCHILD, PARENT, pgid=f.GRANDCHILD, rss=256)
        fresh = [root, f.process(f.GRANDCHILD, 1, pgid=f.GRANDCHILD, rss=256)]
        for rows in ([root, leaf, parent, child], [root, parent, child, leaf],
                     [child, leaf, parent, root]):
            with self.subTest(order=[r['pid'] for r in rows]):
                result = f.run_fixture(rows, fresh, identities={
                    f.ROOT: 100, PARENT: 400, f.GRANDCHILD: 300, f.LEAF: None})
                self.assertEqual(len(result.calls), 2)
                self.assertEqual(result.status['samples'], 1)
                original, complete = result.samples
                self.assertEqual(original['sample_status'], 'INCOMPLETE')
                self.assertEqual(original['verified_rss_lower_bound_bytes'], 512 * 1024)
                self.assertEqual(len(original['identity_probes']), 4)
                self.assertEqual(len({p['pid'] for p in original['identity_probes']}), 4)
                self.assertEqual(len(original['candidate_identity_coverage']), 4)
                self.assertEqual(complete['tree_rss_bytes'], 384 * 1024)
                self.assertIn(f.GRANDCHILD, result.registry.processes)
                self.assertNotIn('UNVERIFIED_PRIVATE_LEAF', str(result.samples) + str(result.status))
                self.assertNotIn('UNRELATED_PRIVATE_COMMAND', str(result.samples) + str(result.status))

    def test_unavailable_parent_keeps_descendants_unowned(self):
        f = F()
        rows = [f.process(f.ROOT, 1), f.process(f.LEAF, f.ROOT),
                f.process(f.GRANDCHILD, f.LEAF), f.process(LATE, f.ROOT)]
        result = f.run_fixture(rows, None, identities={f.ROOT: 100, f.GRANDCHILD: 300, LATE: 500})
        self.assertEqual(len(result.calls), 1)
        self.assertEqual(result.status['samples'], 0)
        coverage = {p['pid']: p['status'] for p in result.samples[0]['candidate_identity_coverage']}
        self.assertEqual(coverage[f.GRANDCHILD], 'BLOCKED_UNVERIFIED_ANCESTRY')
        self.assertEqual(coverage[LATE], 'VERIFIED_OWNED')
        self.assertNotIn(f.GRANDCHILD, result.registry.processes)
        self.assertTrue(result.cleanup.kwargs['request_stop'])

    def test_fresh_child_of_absent_verified_parent_remains_unknown(self):
        f = F(); root = f.process(f.ROOT, 1)
        rows = [root, f.process(PARENT, f.ROOT, pgid=PARENT), f.process(f.LEAF, f.ROOT)]
        fresh = [root, f.process(f.GRANDCHILD, PARENT, pgid=f.GRANDCHILD)]
        result = f.run_fixture(rows, fresh, identities={f.ROOT: 100, PARENT: 400, f.GRANDCHILD: 300})
        self.assertEqual(len(result.calls), 2)
        self.assertEqual(result.status['samples'], 0)
        self.assertNotIn(f.GRANDCHILD, result.registry.processes)
        recovery = result.status['resource_identity_recoveries'][0]
        self.assertEqual(recovery['status'], 'UNKNOWN')
        self.assertIn(f.GRANDCHILD, recovery['unverified_related_pids'])

    def test_unavailable_probe_cache_and_multiple_failures_are_explicit(self):
        f = F()
        for multiple in (False, True):
            with self.subTest(multiple=multiple):
                rows = [f.process(f.ROOT, 1), f.process(f.LEAF, f.ROOT),
                        f.process(PARENT, f.ROOT), f.process(LATE, PARENT)]
                if multiple:
                    rows.insert(2, f.process(f.GRANDCHILD, f.ROOT))
                reg = registry({f.ROOT: 100, PARENT: 400, LATE: 500}, retained={
                    f.LEAF: dict(pid=f.LEAF, pgid=f.ROOT, start_identity=200)})
                with self.assertRaises(launcher.ResourceIdentityUnavailable) as caught:
                    reg.update(rows)
                pids = [call.args[0] for call in reg.owner.proc_starttime.call_args_list]
                self.assertEqual(pids.count(f.LEAF), 1)
                self.assertEqual(len(pids), len(set(pids)))
                self.assertIn(LATE, caught.exception.verified_pids)
                self.assertEqual(caught.exception.unavailable_pids,
                                 {f.LEAF, f.GRANDCHILD} if multiple else {f.LEAF})

    def test_persistent_terminal_leaf_is_separate_from_live_rss_and_ownership(self):
        f = F(); siblings = [876550, 876551, 876552, 876553]
        rows = [f.process(f.ROOT, 1), f.process(PARENT, f.ROOT, pgid=PARENT),
                f.process(f.LEAF, PARENT, state='Z', rss=0, pgid=PARENT)]
        rows += [f.process(pid, PARENT, pgid=PARENT) for pid in siblings]
        identities = {f.ROOT: 100, PARENT: 400, **{pid: pid for pid in siblings}}
        reg = registry(identities)
        terminal = Mock(side_effect=qualified)
        for tick in range(2):
            if tick == 1:
                identities[f.LEAF] = 200  # Same known terminal identity, no PID reuse.
            owned = reg.update(rows, terminal_probe=terminal, checkpoint=Mock())
            self.assertEqual({p['pid'] for p in owned}, {f.ROOT, PARENT, *siblings})
            self.assertEqual(sum(p['rss_kib'] for p in owned), 6 * 128)
            self.assertNotIn(f.LEAF, reg.processes)
            self.assertNotIn(f.LEAF, reg.groups)
            record = reg.resource_terminal_observations[0]
            self.assertEqual(record['live_identity_probe']['returned_none'], tick == 0)
            self.assertEqual(record['live_identity_probe']['observed_start_identity'], None if tick == 0 else 200)
            self.assertEqual(record['rss_status'], 'NOT_APPLICABLE_TO_LIVE_RSS')
            self.assertIn(f.LEAF, reg.terminal_anchors)
        self.assertEqual(terminal.call_count, 2)
        self.assertFalse(reg.generations)

    def test_terminal_rows_cannot_hide_current_or_later_descendants(self):
        f = F(); root = f.process(f.ROOT, 1)
        z = f.process(f.LEAF, f.ROOT, state='Z', rss=0)
        child = f.process(f.GRANDCHILD, f.LEAF)
        for later in (False, True):
            with self.subTest(later=later):
                reg = registry({f.ROOT: 100, f.GRANDCHILD: 300})
                terminal = Mock(side_effect=qualified)
                if later:
                    reg.update([root, z], terminal_probe=terminal)
                    rows = [root, child]
                else:
                    rows = [root, z, child]
                with self.assertRaises(launcher.ResourceIdentityUnavailable) as caught:
                    reg.update(rows, terminal_probe=terminal)
                self.assertNotIn(f.GRANDCHILD, reg.processes)
                self.assertNotIn(f.GRANDCHILD, caught.exception.verified_pids)
                self.assertEqual(terminal.call_count, 1 if later else 0)
                self.assertEqual(caught.exception.coverage_failure, later)

    def test_visible_nonterminal_or_unproved_terminal_stays_unknown(self):
        f = F(); root = f.process(f.ROOT, 1); leaf = f.process(f.LEAF, f.ROOT)
        for state in ('S', 'Z'):
            with self.subTest(state=state):
                with patch.object(launcher.Runtime, 'terminal_information', return_value={
                        'pid': f.LEAF, 'status': 'UNKNOWN', 'reason': 'ordinary unavailable result'}):
                    result = f.run_fixture([root, leaf], [root, f.process(f.LEAF, f.ROOT, state=state)])
                self.assertEqual(len(result.calls), 2)
                self.assertEqual(result.status['samples'], 0)
                self.assertEqual(result.status['resource_identity_recoveries'][0]['status'], 'UNKNOWN')
                self.assertTrue(result.cleanup.kwargs['request_stop'])
                self.assertNotIn(f.LEAF, result.registry.processes)

    def test_fresh_absent_or_proved_terminal_leaf_preserves_incomplete_origin(self):
        f = F(); root = f.process(f.ROOT, 1); leaf = f.process(f.LEAF, f.ROOT)
        for terminal in (False, True):
            with self.subTest(terminal=terminal):
                fresh = [root] + ([f.process(f.LEAF, f.ROOT, state='Z', rss=0)] if terminal else [])
                with patch.object(launcher.Runtime, 'terminal_information',
                                  side_effect=lambda row, retained, deadline: qualified(row, retained)):
                    result = f.run_fixture([root, leaf], fresh)
                self.assertEqual(len(result.calls), 2)
                self.assertEqual(result.status['samples'], 1)
                self.assertEqual(result.samples[0]['sample_status'], 'INCOMPLETE')
                self.assertNotIn('tree_rss_bytes', result.samples[0])
                self.assertEqual(result.samples[-1]['tree_rss_bytes'], 128 * 1024)
                self.assertEqual(len(result.samples[-1]['terminal_process_observations']), int(terminal))
                self.assertEqual(summarize_samples(result.samples)['status'], 'PARTIAL')

    def test_each_probe_obeys_existing_deadline(self):
        f = F(); root = f.process(f.ROOT, 1)
        for phase in ('before_live', 'after_live', 'before_terminal'):
            with self.subTest(phase=phase):
                reg = registry({f.ROOT: 100})
                runtime = launcher.Runtime(reg.owner)
                clock_values = ([31] if phase == 'before_live' else
                                [1, 31] if phase == 'after_live' else [1, 1, 1, 1, 31])
                runtime.clock = Mock(side_effect=clock_values)
                terminal = Mock(side_effect=qualified)
                rows = [root]
                if phase == 'before_terminal':
                    rows.append(f.process(f.LEAF, f.ROOT, state='Z', rss=0))
                with self.assertRaises(TimeoutError):
                    reg.update(rows, terminal_probe=terminal,
                               checkpoint=lambda: runtime.remaining(30, 1))
                terminal.assert_not_called()
                self.assertEqual(reg.owner.proc_starttime.call_count,
                                 0 if phase == 'before_live' else 1 if phase == 'after_live' else 2)
                reg.read_ledgers.assert_not_called()


class MockBSDInfo(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in
                ('pid', 'ppid', 'pgid', 'status', 'start_sec', 'start_usec')]


class TerminalProbeTests(unittest.TestCase):
    def probe(self, changes=None, count=None, missing=False, retained=None):
        f = F(); row = f.process(f.LEAF, f.ROOT, state='Z', rss=0)
        def call(pid, flavor, arg, pointer, size):
            info = pointer._obj
            values = dict(pid=f.LEAF, ppid=f.ROOT, pgid=f.ROOT, status=5, start_sec=10, start_usec=123)
            values.update(changes or {})
            for key, value in values.items():
                setattr(info, key, value)
            ctypes.set_errno(3 if count == 0 else 0)
            return size if count is None else count
        api = Mock(side_effect=call)
        owner = SimpleNamespace(_DARWIN_PROC=None if missing else SimpleNamespace(proc_pidinfo=api),
                                _DarwinBSDInfo=MockBSDInfo)
        runtime = launcher.Runtime(owner)
        runtime.clock = Mock(return_value=1.0)
        result = runtime.terminal_information(row, retained, 30)
        return result, api

    def test_arg_one_returns_only_structural_terminal_evidence(self):
        for retained in (None, 10_000_123):
            with self.subTest(retained=retained):
                result, api = self.probe(retained=retained)
                self.assertEqual(result['status'], 'QUALIFIED_TERMINAL')
                self.assertEqual(result['start_identity'], 10_000_123)
                self.assertEqual(result['returned_bytes'], ctypes.sizeof(MockBSDInfo))
                self.assertEqual(api.call_args.args[:3], (F.LEAF, 3, 1))
                self.assertEqual(result['kernel']['status'], 5)
                self.assertNotIn('command', str(result))
                self.assertNotIn('name', result['kernel'])
                self.assertNotIn('uid', result['kernel'])

    def test_invalid_or_nonterminal_bsdinfo_never_qualifies(self):
        cases = [dict(count=0), dict(count=ctypes.sizeof(MockBSDInfo)-1),
                 dict(changes={'status': 2}), dict(changes={'pid': F.LEAF+10}),
                 dict(changes={'ppid': 1}), dict(changes={'pgid': 1}),
                 dict(changes={'start_usec': 1_000_000}), dict(missing=True)]
        for index, case in enumerate(cases):
            with self.subTest(case=index):
                result, _ = self.probe(**case)
                self.assertEqual(result['status'], 'UNKNOWN')
                self.assertTrue(result['reason'])


class TerminalSummaryTests(unittest.TestCase):
    def test_terminal_placeholders_do_not_change_live_rss_or_cpu_maxima(self):
        for overlap in (False, True):
            with self.subTest(overlap=overlap):
                first = sample(t=0, pid=12, start=100, cpu='0:02.50')
                second = sample(t=5, pid=13, start=101, cpu='0:01.00')
                second['terminal_process_observations'] = [dict(
                    pid=13 if overlap else 12, start_identity=100, status='QUALIFIED_TERMINAL',
                    ps_reported_rss_kib=0, ps_reported_cpu_time='0:00.00',
                    rss_status='NOT_APPLICABLE_TO_LIVE_RSS')]
                result = summarize_samples([first, second])
                if overlap:
                    self.assertEqual(result['valid_sample_count'], 1)
                    self.assertEqual(result['status'], 'PARTIAL')
                else:
                    self.assertEqual(result['sampled_observed_cpu_seconds_lower_bound'], 3.5)
                    self.assertEqual(result['sampled_tree_sum_rss_max_bytes'], 2048 * 1024)
                    self.assertEqual(result['qualified_terminal_rows_excluded_count'], 1)
                    self.assertFalse(result['errors'])
