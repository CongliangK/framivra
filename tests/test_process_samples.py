"""No real processes or browser launch: deterministic numeric sampling fixtures."""
import unittest
from vtx.process_samples import TreeSamples, parse_stat, MAX_STAT_BYTES


def row(pid, ppid, start, cpu=10, rss=100):
    return dict(pid=pid, ppid=ppid, start_ticks=start, cpu_ms=cpu, rss_bytes=rss)


class Reader:
    def __init__(self):
        self.rows = {10: row(10, 1, 100)}
        self.status = 'sampled'
    def identity(self, pid):
        r = self.rows[pid]
        return pid, r['start_ticks']
    def snapshot(self):
        return self.rows.copy(), self.status


class SamplingTests(unittest.TestCase):
    def test_no_root_is_unmeasured_not_zero(self):
        result = TreeSamples(Reader()).sample()
        self.assertEqual(result['status'], 'not_started')
        self.assertIsNone(result['rss_bytes'])
        self.assertIsNone(result['cpu_ms'])

    def test_descendants_deduplicate_multiple_roots(self):
        reader = Reader(); reader.rows[11] = row(11, 10, 101)
        samples = TreeSamples(reader)
        samples.register(10); samples.register(11)
        result = samples.sample()
        self.assertEqual(result['process_count'], 2)
        self.assertEqual(result['rss_bytes'], 200)
        self.assertEqual(result['cpu_ms'], 20)

    def test_pid_reuse_never_adopts_unrelated_root_children(self):
        reader = Reader(); samples = TreeSamples(reader); samples.register(10)
        samples.sample()
        reader.rows = {10: row(10, 1, 999), 12: row(12, 10, 1000)}
        result = samples.sample()
        self.assertEqual(result['process_count'], 0)
        self.assertEqual(result['rss_bytes'], 0)  # Successfully sampled tree has exited.
        self.assertEqual(result['cpu_ms'], 10)

    def test_exited_and_reparented_children_and_new_restart(self):
        reader = Reader(); reader.rows[11] = row(11, 10, 101)
        samples = TreeSamples(reader); samples.register(10); samples.sample()
        reader.rows = {11: row(11, 1, 101, cpu=15), 20: row(20, 1, 200, cpu=3)}
        samples.register(20)
        result = samples.sample()
        self.assertEqual(result['process_count'], 2)
        self.assertEqual(result['cpu_ms'], 28)

    def test_unavailable_never_reports_zero(self):
        reader = Reader(); samples = TreeSamples(reader); samples.register(10)
        reader.status = 'unavailable'; reader.rows = {}
        self.assertIsNone(samples.sample()['rss_bytes'])

    def test_parse_ignores_sensitive_comm_and_supports_parentheses(self):
        fields = ['S', '1'] + ['0'] * 20
        fields[11], fields[12], fields[19], fields[21] = '2', '3', '456', '7'
        value = parse_stat('10 (PRIVATE ) name) ' + ' '.join(fields) + '\n', 10,
                           ticks_per_second=100, page_size=4096)
        self.assertEqual(value['cpu_ms'], 50)
        self.assertEqual(value['rss_bytes'], 28672)
        self.assertNotIn('PRIVATE', repr(value))
        for raw in ('bad', 'x' * (MAX_STAT_BYTES + 1)):
            with self.assertRaises(ValueError):
                parse_stat(raw, 10, ticks_per_second=100, page_size=4096)


class SamplerContractTests(unittest.TestCase):
    def fixture(self):
        from vtx import events
        from vtx.process_samples import ProcessSampler
        from tests.test_execution_events import Buffer
        stream = Buffer(); execution = events.ExecutionContext(stream, lifecycle=True)
        with execution.task('gfx').scope() as task:
            sampler = ProcessSampler(task, Reader())
            sampler.sample()
            sampler.register(10)
            sampler.sample()
            sampler.close()
        execution.finish()
        return stream.getvalue()

    def test_samples_use_single_sink_and_strict_tree_scope(self):
        from vtx import events
        result = events.read_events(self.fixture())
        self.assertTrue(result['complete'])
        samples = [r for r in result['events'] if r['event'] == 'resource_sample']
        self.assertEqual(len(samples), 3)
        self.assertIsNone(samples[0]['resources']['rss']['value'])
        self.assertEqual(samples[1]['resources']['rss']['value'], 100)
        self.assertEqual(samples[1]['resources']['rss']['scope'], 'process_tree')
        self.assertIsNone(samples[1]['resources']['gpu']['value'])
        self.assertEqual(samples[1]['extensions']['process_count'], 1)

    def test_partial_snapshot_is_unavailable_not_false_zero(self):
        reader = Reader(); samples = TreeSamples(reader); samples.register(10)
        reader.rows = {}; reader.status = 'partial'
        result = samples.sample()
        self.assertEqual(result['status'], 'partial')
        self.assertIsNone(result['cpu_ms'])
        self.assertIsNone(result['rss_bytes'])

    def test_forged_tree_samples_reject(self):
        import copy, json
        from vtx import events
        rows = events.read_events(self.fixture())['events']
        target = next(i for i, r in enumerate(rows) if r['event'] == 'resource_sample' and r['extensions']['sample_status'] == 'sampled')
        for mutate in (
            lambda r: r['extensions'].update(sample_index=0),
            lambda r: r['extensions'].update(process_count=None),
            lambda r: r['extensions'].update(sample_status='unavailable'),
            lambda r: r['resources']['gpu'].update(value=0, method='proc_stat_tree_snapshot', unavailable_reason=None),
            lambda r: r['resources']['rss'].update(scope='process'),
        ):
            changed = copy.deepcopy(rows); mutate(changed[target])
            with self.assertRaises(ValueError):
                events.read_events(''.join(json.dumps(r) + '\n' for r in changed))

    def test_disabled_registration_does_not_touch_process(self):
        from vtx.process_samples import register_browser
        class Process:
            @property
            def pid(self):
                raise AssertionError('must not read process in disabled path')
        register_browser(Process())

    def test_thread_stops_and_sample_budget_is_enforced(self):
        from unittest.mock import patch
        from vtx import events, process_samples
        from tests.test_execution_events import Buffer
        stream = Buffer(); execution = events.ExecutionContext(stream, lifecycle=True)
        with execution.task('gfx').scope() as task:
            with patch.object(process_samples, 'MAX_SAMPLES', 2):
                sampler = process_samples.ProcessSampler(task, Reader())
                sampler.start()
                sampler.register(10)
                sampler.sample()
                sampler.close()
                sampler.sample()
                self.assertFalse(sampler.thread.is_alive())
                self.assertEqual(sampler.index, 2)
        execution.finish()
        summary = events.lifecycle_summary(stream.getvalue())
        tree = next(iter(summary['process_trees'].values()))
        self.assertFalse(tree['sampling_complete'])
        self.assertEqual(len(tree['samples']), 2)
        self.assertEqual(tree['samples'][-1]['sample_status'], 'limit_reached')

    def test_sampled_peak_and_cpu_are_per_tree_not_global_fake_peak(self):
        from vtx import events
        summary = events.lifecycle_summary(self.fixture())
        tree = next(iter(summary['process_trees'].values()))
        self.assertTrue(tree['sampling_complete'])
        self.assertEqual(tree['sampled_peak_rss_bytes'], 100)
        self.assertEqual(tree['sampled_cumulative_cpu_ms'], 10)
        self.assertIn('short_lived_processes_may_be_missed', tree['limitations'])

    def test_disabled_adapter_never_constructs_sampler(self):
        from unittest.mock import patch
        from vtx import events, process_samples
        from tests.test_execution_events import Buffer
        stream = Buffer(); execution = events.ExecutionContext(stream, lifecycle=True)
        @process_samples.observed_tree
        def body():
            return 42
        with execution.task('gfx').scope():
            with patch.object(process_samples, 'ProcessSampler', side_effect=AssertionError('allocated')):
                self.assertEqual(body(), 42)
        execution.finish()
        self.assertTrue(events.read_events(stream.getvalue())['complete'])

    def test_sampler_platform_failure_preserves_business_exception(self):
        from unittest.mock import patch
        from vtx import events, process_samples
        from tests.test_execution_events import Buffer
        stream = Buffer(); execution = events.ExecutionContext(stream, lifecycle=True)
        execution.process_samples = True
        error = RuntimeError('business sentinel')
        @process_samples.observed_tree
        def body():
            raise error
        with execution.task('gfx').scope():
            with patch.object(process_samples, 'ProcReader', side_effect=OSError('private')):
                try:
                    body()
                except RuntimeError as caught:
                    self.assertIs(caught, error)
                else:
                    self.fail('lost business exception')
        execution.finish()
        self.assertFalse(events.read_events(stream.getvalue())['complete'])
        self.assertNotIn('private', stream.getvalue())

    def test_proc_scan_entry_bound_and_private_fields(self):
        import tempfile
        from pathlib import Path
        from vtx.process_samples import ProcReader
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fields = ['S', '1'] + ['0'] * 20
            fields[19], fields[21] = '100', '1'
            for pid in (10, 11):
                directory = root / str(pid); directory.mkdir()
                (directory / 'stat').write_text(f'{pid} (private command) ' + ' '.join(fields) + '\n')
            reader = ProcReader(root, limit=1)
            rows, status = reader.snapshot()
            self.assertEqual(status, 'limit_reached')
            self.assertLessEqual(len(rows), 1)
            self.assertNotIn('private', repr(rows))

    def test_registration_failure_remains_unavailable(self):
        class Missing(Reader):
            def identity(self, pid):
                raise OSError('private process failure')
        samples = TreeSamples(Missing())
        self.assertFalse(samples.register(10))
        result = samples.sample()
        self.assertEqual(result['status'], 'unavailable')
        self.assertIsNone(result['cpu_ms'])

    def test_missed_slots_remain_incomplete_after_later_good_sample(self):
        import types
        from unittest.mock import patch
        from vtx import events, process_samples as ps
        from tests.test_execution_events import Buffer
        stream = Buffer(); execution = events.ExecutionContext(stream, lifecycle=True)
        with execution.task('gfx').scope() as task:
            sampler = ps.ProcessSampler(task, Reader()); sampler.register(10)
            with patch.object(ps, 'time', types.SimpleNamespace(monotonic=iter([0.0, 1.0, 1.01]).__next__)):
                sampler.sample(); sampler.sample(); sampler.close()
        execution.finish()
        summary = events.lifecycle_summary(stream.getvalue())
        tree = next(iter(summary['process_trees'].values()))
        self.assertTrue(summary['complete'])
        self.assertEqual(tree['samples'][1]['missed_intervals'], 4)
        self.assertFalse(tree['sampling_complete'])
        self.assertEqual(len(tree['observed_window_ms']), 2)

    def test_scheduler_failure_is_sticky_and_thread_stops_without_business_error(self):
        import types
        from unittest.mock import patch
        from vtx import events, process_samples as ps
        from tests.test_execution_events import Buffer
        for bad in (OSError('private scheduler'), float('nan'), float('inf'), -1.0):
            stream = Buffer(); execution = events.ExecutionContext(stream, lifecycle=True)
            calls = [0]
            def clock():
                calls[0] += 1
                if calls[0] == 2:
                    if isinstance(bad, Exception):
                        raise bad
                    return bad
                return calls[0] * .01
            with execution.task('gfx').scope() as task:
                sampler = ps.ProcessSampler(task, Reader()); sampler.register(10)
                with patch.object(ps, 'time', types.SimpleNamespace(monotonic=clock)), \
                     patch.object(ps, 'SAMPLE_INTERVAL_SECONDS', .001):
                    sampler.start()
                    self.assertTrue(sampler.stop_event.wait(2))
                    sampler.close()
                self.assertFalse(sampler.thread.is_alive())
            execution.finish()
            summary = events.lifecycle_summary(stream.getvalue())
            tree = next(iter(summary['process_trees'].values()))
            self.assertTrue(summary['complete'])
            self.assertFalse(tree['sampling_complete'])
            self.assertEqual(tree['samples'][-1]['sample_status'], 'unavailable')
            self.assertNotIn('private', stream.getvalue())
