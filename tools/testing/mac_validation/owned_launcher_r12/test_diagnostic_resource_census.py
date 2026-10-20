"""Six approved methods / 17 ordinary fixtures. All external observations mocked."""
from contextlib import ExitStack
from io import StringIO
import datetime
import inspect
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch

import owned_launcher as launcher
from resource_summary import summarize_samples


ROOT, HOST, AUX, OTHER, DESC, GUARD, WORKER_GROUP = range(1001, 1008)
OBSERVER, UNRELATED = 900001, 900002


def process(pid, ppid, *, pgid=ROOT, state='S', rss=128, command='ordinary-mock'):
    return dict(pid=pid, ppid=ppid, pgid=pgid, state=state, rss_kib=rss,
                cpu_percent=0.5, cpu_time='00:00.25', command=command)


class DiagnosticResourceCensusTests(unittest.TestCase):
    def fixture(self, auxiliary, *, previous=(), identities=None, pressure=55,
                free=50*launcher.GIB, logical=0, expired=False, malformed=False,
                root_identity=100, ledger=False, cleanup_status='PASS', unrelated_pgid=UNRELATED):
        state = dict(now=31.0 if expired else 1.0)
        rows = [process(ROOT, 1)] + auxiliary
        values = {ROOT: root_identity}
        values.update(identities or {})
        owner = SimpleNamespace(proc_starttime=Mock(side_effect=lambda pid: values.get(pid)),
                                WORKER_LEDGER='ordinary-ledger.json')
        registry = launcher.OwnedRegistry.__new__(launcher.OwnedRegistry)
        registry.owner = owner
        registry.root_pid = ROOT
        registry.processes = {ROOT: dict(pid=ROOT, pgid=ROOT, start_identity=100)}
        registry.groups = {ROOT}
        registry.generations = {}
        registry.boot = 'ordinary-mock-boot'
        registry.expected_nested_generations = 1 if ledger else 0
        registry.terminal_anchors = {}
        registry.resource_observation_pids = set(previous)
        if ledger:
            project = MagicMock()
            temporary = project.__truediv__.return_value.__truediv__.return_value
            temporary.exists.return_value = True
            ledger_path = MagicMock()
            ledger_path.__str__.return_value = '/mock/project/cache/tmp/run/ordinary-ledger.json'
            ledger_path.parent.__str__.return_value = '/mock/project/cache/tmp/run'
            ledger_path.read_bytes.return_value = json.dumps(dict(
                host_pid=HOST, host_start_time_ticks=200, boot_id=registry.boot,
                pgid=WORKER_GROUP, start_time_ticks=400)).encode()
            temporary.glob.return_value = [ledger_path]
            registry.project = project
        else:
            registry.read_ledgers = Mock()

        class Child:
            returncode = None
            pid = ROOT
            def poll(self):
                return self.returncode
            def wait(self, timeout):
                self.returncode = 0
                return 0
        child = Child()
        child.wait = Mock(side_effect=child.wait)
        runtime = launcher.Runtime(owner)
        runtime.clock = Mock(side_effect=lambda: state['now'])
        runtime.sleep = Mock()
        runtime.pressure = Mock(return_value=(pressure, 'ordinary mock pressure'))
        runtime.terminal_information = Mock()
        inventory = rows + [
            process(OBSERVER, 1, pgid=OBSERVER),
            process(UNRELATED, 1, pgid=unrelated_pgid, command='UNRELATED_PRIVATE_ARGS')]
        raw = ''.join('{pid} {ppid} {pgid} {state} {rss_kib} {cpu_percent} {cpu_time} {command}\n'.format(**row)
                      for row in inventory)
        runtime.run = Mock(return_value=SimpleNamespace(
            stdout='malformed\n' if malformed else raw, returncode=0))
        stream = StringIO()
        output = MagicMock()
        output.__truediv__.return_value.open.return_value.__enter__.return_value = stream
        fs = SimpleNamespace(f_bavail=free, f_frsize=1)
        with ExitStack() as stack:
            stack.enter_context(patch.object(launcher.os, 'getpid', return_value=OBSERVER))
            stack.enter_context(patch.object(launcher.os, 'getpgid', return_value=OBSERVER))
            stack.enter_context(patch.object(launcher.time, 'monotonic', side_effect=lambda: state['now']))
            date = stack.enter_context(patch.object(datetime, 'datetime'))
            date.now.return_value.isoformat.return_value = '2026-10-20T00:00:00+00:00'
            stack.enter_context(patch.object(launcher.os, 'statvfs', return_value=fs))
            killpg = stack.enter_context(patch.object(launcher.os, 'killpg'))
            kill = stack.enter_context(patch.object(launcher.os, 'kill'))
            waitpid = stack.enter_context(patch.object(launcher.os, 'waitpid'))
            cleanup = stack.enter_context(patch.object(launcher, 'cleanup',
                return_value=dict(status=cleanup_status)))
            scan = stack.enter_context(patch.object(launcher, 'logical_bytes', return_value=logical))
            stack.enter_context(patch.object(launcher, 'dump'))
            status = launcher.monitor(child, registry, runtime, output, [Path('/mock/output')],
                30, 0, resource_observation_mode=launcher.RESOURCE_MODE_DIAGNOSTIC)
            killpg.assert_not_called()
            kill.assert_not_called()
            waitpid.assert_not_called()
            cleanup.assert_called_once()
        runtime.sleep.assert_not_called()
        runtime.terminal_information.assert_not_called()
        self.assertLessEqual(runtime.run.call_count, 1)
        for call in runtime.run.call_args_list:
            self.assertEqual(call.args[0], launcher.PS_ARGV)
            self.assertLessEqual(call.kwargs['timeout'], 2)
            self.assertGreater(call.kwargs['timeout'], 0)
        records = [json.loads(line) for line in stream.getvalue().splitlines()]
        serialized = json.dumps(records) + json.dumps(status)
        self.assertNotIn('UNRELATED_PRIVATE_ARGS', serialized)
        self.assertNotIn('UNKNOWN_PRIVATE_ARGS', serialized)
        for row in records:
            self.assertEqual(row['record_schema'], launcher.RESOURCE_CENSUS_SCHEMA)
            self.assertNotIn('processes', row)
            self.assertNotIn('tree_rss_kib', row)
            self.assertNotIn('tree_rss_bytes', row)
            self.assertEqual(row['reported_census_rss_kib'],
                row['verified_live_rss_kib'] + row['unverified_reported_rss_kib'])
            self.assertEqual(row['reported_census_rss_kib'],
                sum(p['rss_kib'] for p in row['census_processes']))
            self.assertEqual(len(row['census_processes']),
                len({p['pid'] for p in row['census_processes']}))
            unknown = set(row['unverified_candidate_pids'])
            for p in row['census_processes']:
                if p['pid'] in unknown:
                    self.assertNotIn('command', p)
                    self.assertNotIn('start_identity', p)
                    if p['pid'] != ROOT:  # Root's original retained record is not a new adoption.
                        self.assertNotIn(p['pid'], registry.processes)
            self.assertNotIn('terminal_process_observations', row)
        return SimpleNamespace(records=records, status=status, registry=registry, runtime=runtime,
                               child=child, scan=scan, cleanup=cleanup)

    def assert_continues(self, result):
        self.assertFalse(result.status['stop_reasons'])
        self.assertFalse(result.status['errors'])
        result.child.wait.assert_called_once()
        self.assertEqual(result.child.returncode, 0)
        self.assertFalse(result.cleanup.call_args.kwargs['request_stop'])
        self.assertEqual(result.runtime.run.call_count, 1)

    def assert_stops(self, result, reason):
        self.assertIn(reason, result.status['stop_reasons'])
        result.child.wait.assert_not_called()
        self.assertTrue(result.cleanup.call_args.kwargs['request_stop'])

    def test_auxiliary_gaps_count_reported_rss_without_ownership(self):
        cases = [
            [process(AUX, ROOT, state='S', rss=77, command='UNKNOWN_PRIVATE_ARGS')],
            [process(AUX, ROOT, state='Z', rss=0, command='UNKNOWN_PRIVATE_ARGS')],
            [process(AUX, ROOT, state='?', rss=33, command='UNKNOWN_PRIVATE_ARGS')],
            [process(AUX, ROOT, rss=11, command='UNKNOWN_PRIVATE_ARGS'),
             process(OTHER, ROOT, state='Z', rss=22, command='UNKNOWN_PRIVATE_ARGS')],
        ]
        for index, rows in enumerate(cases):
            with self.subTest(fixture=index):
                result = self.fixture(rows)
                self.assert_continues(result)
                sample = result.records[0]
                self.assertEqual(sample['reported_census_rss_kib'], 128+sum(p['rss_kib'] for p in rows))
                self.assertEqual(sample['attribution_status'], 'PARTIAL')
                self.assertEqual(result.registry.groups, {ROOT})
                self.assertFalse(result.registry.generations)
                self.assertIn('printed zero is not absence', sample['resource_scope'])
                self.assertFalse(result.registry.resource_terminal_observations)

    def test_unknown_branches_and_reparented_candidates_remain_accounted(self):
        cases = [
            ([process(AUX, ROOT, command='UNKNOWN_PRIVATE_ARGS'),
              process(DESC, AUX, command='UNKNOWN_PRIVATE_ARGS')], ()),
            ([process(AUX, 1, command='UNKNOWN_PRIVATE_ARGS')], (AUX,)),
        ]
        for index, (rows, previous) in enumerate(cases):
            with self.subTest(fixture=index):
                result = self.fixture(rows, previous=previous)
                self.assert_continues(result)
                self.assertEqual({p['pid'] for p in result.records[0]['census_processes']},
                                 {ROOT} | {p['pid'] for p in rows})
                self.assertEqual(result.registry.resource_observation_pids,
                                 {ROOT} | {p['pid'] for p in rows})

    def test_unverified_group_boundary_and_pid_cap_stop_without_dropping_rows(self):
        cases = [
            ('boundary', [process(AUX, ROOT, pgid=WORKER_GROUP, rss=23)]),
            ('cap', [process(pid, ROOT, rss=1) for pid in range(20000, 24096)]),
        ]
        for name, rows in cases:
            with self.subTest(fixture=name):
                result = self.fixture(rows, unrelated_pgid=WORKER_GROUP if name == 'boundary' else UNRELATED)
                self.assert_stops(result, 'diagnostic_observation_scope_failed')
                sample = result.records[0]
                self.assertEqual(len(sample['census_processes']), len(rows)+1)
                self.assertEqual(sample['reported_census_rss_kib'], 128+sum(p['rss_kib'] for p in rows))
                self.assertEqual(result.registry.groups, {ROOT})
                if name == 'boundary':
                    self.assertIn('unverified_group_boundary', sample['observation_errors'])
                    self.assertEqual(sample['outside_trusted_group_pids'], [AUX])
                else:
                    self.assertIn('observation_pid_cap_exceeded', sample['observation_errors'])
                    self.assertEqual(sample['observation_pid_count'], 4097)
                    self.assertEqual(sample['observation_pid_cap'], 4096)
                    self.assertEqual(result.registry.resource_observation_pids, set())

    def test_required_resource_and_observation_guards_remain_fatal(self):
        cases = [
            ('rss', dict(), 6*launcher.GIB//1024-128, 'tree_rss_at_or_above_6GiB'),
            ('pressure', dict(pressure=10), 17, 'system_memory_free_observation_floor'),
            ('disk', dict(free=20*launcher.GIB-1), 17, 'volume_free_below_20GiB'),
            ('logical', dict(logical=24*launcher.GIB), 17, 'new_logical_bytes_at_or_above_24GiB'),
            ('deadline', dict(expired=True), 17, 'deadline_or_resource_observation_failed'),
            ('malformed', dict(malformed=True), 17, 'deadline_or_resource_observation_failed'),
            ('root', dict(root_identity=None), 17, 'diagnostic_observation_scope_failed'),
        ]
        for name, options, rss, reason in cases:
            with self.subTest(fixture=name):
                result = self.fixture([process(AUX, ROOT, rss=rss)], **options)
                self.assert_stops(result, reason)
                if name == 'deadline':
                    self.assertEqual(result.runtime.run.call_count, 0)
                if name == 'root':
                    self.assertIn('root_identity_not_verified', result.records[0]['observation_errors'])
                if name == 'rss':
                    self.assertEqual(result.records[0]['reported_census_rss_kib']*1024, 6*launcher.GIB)
                    result.scan.assert_not_called()

    def test_verified_ledger_and_guard_maintenance_progress_during_auxiliary_gap(self):
        command = '-m vtx.proc_supervisor guard {} 123'.format(WORKER_GROUP)
        result = self.fixture([
            process(HOST, ROOT), process(GUARD, HOST, pgid=GUARD, command=command),
            process(AUX, ROOT, command='UNKNOWN_PRIVATE_ARGS')],
            identities={HOST: 200, GUARD: 300}, ledger=True)
        self.assert_continues(result)
        generation = result.registry.generations[str(WORKER_GROUP)+':400']
        self.assertEqual(generation['record']['host_start_time_ticks'], 200)
        self.assertEqual(generation['record']['boot_id'], 'ordinary-mock-boot')
        self.assertEqual(generation['guard'], dict(pid=GUARD, pgid=GUARD, start_identity=300))
        self.assertEqual(result.registry.groups, {ROOT, GUARD, WORKER_GROUP})
        self.assertEqual(result.registry.nested_coverage()['status'], 'PASS')
        self.assertNotIn(AUX, result.registry.processes)

    def test_diagnostic_summary_preserves_limits_and_cleanup_gate(self):
        result = self.fixture([process(AUX, ROOT, rss=71, command='UNKNOWN_PRIVATE_ARGS')],
                              cleanup_status='UNKNOWN')
        self.assert_continues(result)
        incomplete = dict(sample_status='INCOMPLETE', resource_status='UNKNOWN',
                          wall_seconds=0, failed_pid=AUX)
        before = json.dumps(incomplete, sort_keys=True)
        summary = summarize_samples([incomplete] + result.records)
        self.assertEqual(summary['status'], 'PARTIAL')
        self.assertEqual(summary['sampled_tree_sum_rss_max_bytes'], 128*1024)
        self.assertEqual(summary['diagnostic_census']['captured_reported_sum_rss_max_bytes'], (128+71)*1024)
        self.assertEqual(summary['diagnostic_census']['status'], 'PARTIAL')
        self.assertEqual(summary['sampled_observed_cpu_seconds_lower_bound'], .25)
        self.assertEqual(summary['cumulative_cpu_coverage'], 'PARTIAL')
        self.assertEqual(summary['valid_sample_count'], 1)
        self.assertEqual(json.dumps(incomplete, sort_keys=True), before)
        status = launcher.finalize_status(dict(result.status, exit=result.child.poll()))
        self.assertEqual(status['state'], 'FAILED')
        self.assertFalse(status['next_arm_allowed'])
        self.assertEqual(status['cleanup']['status'], 'UNKNOWN')
        self.assertEqual(inspect.signature(launcher.monitor).parameters['resource_observation_mode'].default,
                         launcher.RESOURCE_MODE_STRICT)
        self.assertIn('not an upper or lower bound', summary['diagnostic_census']['scope'])
