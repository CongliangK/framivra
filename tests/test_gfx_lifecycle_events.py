"""Host lifecycle contract; these tests are not real browser acceptance."""
import copy
import json
import unittest
from unittest.mock import patch
from vtx import events
from tests.test_execution_events import Buffer


def encode(rows):
    return ''.join(json.dumps(r) + '\n' for r in rows)


class LifecycleTests(unittest.TestCase):
    def fixture(self):
        tick = [0.0]
        stream = Buffer()
        execution = events.ExecutionContext(stream, lifecycle=True, clock=lambda: tick[0])
        with execution.task('gfx').scope():
            with events.operation('page_lifecycle'):
                with events.operation('capture_rpc', frame_index=0):
                    tick[0] = .01
                try:
                    with events.operation('capture_rpc', frame_index=1):
                        tick[0] = .03
                        raise RuntimeError('SECRET URL')
                except RuntimeError:
                    pass
        execution.finish()
        return stream.getvalue()

    def test_raw_samples_include_failure_without_overlapping_wall_sum(self):
        data = self.fixture()
        result = events.lifecycle_summary(data)
        self.assertTrue(result['complete'])
        self.assertAlmostEqual(result['command_wall_ms'], 30)
        capture = result['operations']['capture_rpc']
        self.assertEqual(capture['succeeded'], 1)
        self.assertEqual(capture['failed'], 1)
        self.assertAlmostEqual(capture['p95_ms'], 20)
        self.assertAlmostEqual(capture['p50_ms'], 10)
        self.assertAlmostEqual(capture['cumulative_work_ms'], 30)
        self.assertIsNone(result['critical_path_ms'])
        self.assertNotIn('SECRET', data)

    def test_default_remains_v1_and_does_not_allocate_operations(self):
        stream = Buffer(); execution = events.ExecutionContext(stream)
        with execution.task('gfx').scope():
            with patch.object(execution, 'task', side_effect=AssertionError('allocated')):
                with events.operation('capture_rpc') as span:
                    self.assertIsNone(span)
        execution.finish()
        result = events.read_events(stream.getvalue())
        self.assertTrue(result['complete'])
        self.assertTrue(all(r['schema_version'] == 'vtx.events/1' for r in result['events']))
        self.assertEqual(events.lifecycle_summary(stream.getvalue())['operations'], {})

    def test_v1_cannot_silently_accept_v2_and_mixing_rejects(self):
        rows = events.read_events(self.fixture())['events']
        rows[-1]['schema_version'] = events.SCHEMA
        with self.assertRaises(ValueError):
            events.read_events(encode(rows))
        for row in rows:
            row['schema_version'] = events.SCHEMA
        with self.assertRaises(ValueError):
            events.read_events(encode(rows))

    def test_metadata_privacy_and_bounds(self):
        for key, value in [('operation', 'SECRET'), ('gfx_key', '/private'),
                           ('generation', True), ('frame_index', -1),
                           ('copy_head', 2 ** 40), ('browser_id', '/private'),
                           ('frame_origin', 'inferred'), ('url', 'SECRET')]:
            rows = events.read_events(self.fixture())['events']
            row = next(r for r in rows if r['extensions'].get('operation') == 'capture_rpc')
            row['extensions'][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                events.read_events(encode(rows))
        stream = Buffer(); execution = events.ExecutionContext(stream, lifecycle=True)
        with execution.task('gfx').scope():
            with events.operation('capture_rpc', url='SECRET'):
                pass
        execution.finish()
        self.assertNotIn('SECRET', stream.getvalue())
        self.assertIn('metadata_redacted', stream.getvalue())

    def test_truncation_never_reports_complete_coverage(self):
        data = self.fixture()
        self.assertFalse(events.lifecycle_summary(data.rsplit('\n', 2)[0] + '\n')['complete'])

    def test_duplicate_and_replay_semantics_preserved(self):
        rows = events.read_events(self.fixture())['events']
        rows.insert(3, copy.deepcopy(rows[2]))
        with self.assertRaises(ValueError):
            events.read_events(encode(rows))
        self.assertTrue(events.read_events(encode(rows), replay=True)['complete'])


class ProductionBoundaryTests(unittest.TestCase):
    def test_screenshot_rpc_and_decode_are_separate_host_boundaries(self):
        from vtx import gfx_render as gfx
        tick = [0.0]; stream = Buffer()
        execution = events.ExecutionContext(stream, lifecycle=True, clock=lambda: tick[0])
        tab = object.__new__(gfx.Tab)
        def call(*args, **kwargs):
            tick[0] += .02
            return {'data': 'YWJj'}
        tab.call = call
        with execution.task('gfx').scope():
            self.assertEqual(tab.shot(), b'abc')
        execution.finish()
        result = events.lifecycle_summary(stream.getvalue())
        self.assertEqual(result['operations']['capture_rpc']['count'], 1)
        self.assertEqual(result['operations']['base64_decode']['count'], 1)
        self.assertEqual(result['operations']['capture_rpc']['cumulative_work_ms'], 20)
        self.assertIsNone(result['browser_internal_timings']['browser_png_encode']['value'])

    def test_deferred_copy_and_thread_frames_remain_inside_page(self):
        from tests.test_graphics_portability import StaticSpanMock
        stream = Buffer(); execution = events.ExecutionContext(stream, lifecycle=True)
        with execution.task('gfx').scope():
            result, files = StaticSpanMock()._run(6, spans=[[2, 5]])
        execution.finish()
        summary = events.lifecycle_summary(stream.getvalue())
        self.assertTrue(summary['complete'])
        self.assertEqual(result['rendered'], 4)
        self.assertEqual(summary['operations']['frame']['succeeded'], 4)
        self.assertEqual(summary['operations']['static_copy']['succeeded'], 2)
        self.assertEqual(summary['operations']['final_validation']['succeeded'], 1)
        self.assertEqual(len(files), 6)
        rows = events.read_events(stream.getvalue())['events']
        page = next(r for r in rows if r['extensions'].get('operation') == 'page_lifecycle')
        end = next(r for r in rows if r['event'] == 'task_terminal' and r['task_id'] == page['task_id'])
        copies = [r for r in rows if r['extensions'].get('operation') == 'static_copy']
        self.assertTrue(all(r['seq'] < end['seq'] for r in copies))
        frames = [r for r in rows if r['extensions'].get('operation') == 'frame']
        self.assertTrue(all(r['parent_task_id'] == page['task_id'] for r in frames))

    def test_start_failure_records_cleanup_and_failed_page(self):
        import tempfile
        from pathlib import Path
        from vtx import gfx_render as gfx
        stream = Buffer(); execution = events.ExecutionContext(stream, lifecycle=True)
        with tempfile.TemporaryDirectory() as tmp:
            page = Path(tmp) / 'page.html'; page.write_text('<html/>')
            with execution.task('gfx').scope():
                with patch.object(gfx.Browser, '_find_chrome', side_effect=RuntimeError('PRIVATE')):
                    with self.assertRaises(RuntimeError):
                        gfx.render_page(page, Path(tmp) / 'frames', 1, width=6, height=4)
        execution.finish()
        summary = events.lifecycle_summary(stream.getvalue())
        self.assertTrue(summary['complete'])
        self.assertEqual(summary['operations']['browser_start']['failed'], 1)
        self.assertEqual(summary['operations']['browser_stop']['succeeded'], 1)
        self.assertEqual(summary['operations']['page_lifecycle']['failed'], 1)
        self.assertNotIn('PRIVATE', stream.getvalue())

    def test_failed_retries_are_retained_without_committed_frames(self):
        from tests.test_graphics_portability import GfxStatsMock
        stream = Buffer(); execution = events.ExecutionContext(stream, lifecycle=True)
        with execution.task('gfx').scope():
            result = GfxStatsMock()._run('fail', n=1)
        execution.finish()
        summary = events.lifecycle_summary(stream.getvalue())
        self.assertTrue(summary['complete'])
        self.assertEqual(summary['operations']['frame']['failed'], 3)
        self.assertEqual(summary['operations']['frame']['succeeded'], 0)
        self.assertNotIn('atomic_commit', summary['operations'])
        self.assertEqual(summary['operations']['page_lifecycle']['failed'], 1)
        self.assertEqual(result['rendered'], 0)
        rows = events.read_events(stream.getvalue())['events']
        attempts = [r['extensions']['attempt_index'] for r in rows
                    if r['extensions'].get('operation') == 'frame']
        self.assertEqual(attempts, [1, 2, 3])

    def test_fully_cached_block_never_launches_browser(self):
        from tests.test_graphics_portability import StaticSpanMock
        stream = Buffer(); execution = events.ExecutionContext(stream, lifecycle=True)
        with execution.task('gfx').scope():
            result, files = StaticSpanMock()._run(2, present=(0, 1))
        execution.finish()
        summary = events.lifecycle_summary(stream.getvalue())
        self.assertTrue(summary['complete'])
        self.assertEqual(result['rendered'], 0)
        self.assertNotIn('browser_start', summary['operations'])
        self.assertNotIn('frame', summary['operations'])
        self.assertEqual(summary['operations']['page_lifecycle']['succeeded'], 1)


class ReviewRegressionTests(unittest.TestCase):
    def test_schema_type_errors_are_protocol_valueerrors(self):
        for schema in ([], {}, None, False, 1, 'vtx.events/3'):
            rows = events.read_events(LifecycleTests().fixture())['events']
            rows[0]['schema_version'] = schema
            with self.subTest(schema=schema), self.assertRaises(ValueError):
                events.read_events(encode(rows))

    def test_redacted_operation_retains_observations_but_never_complete_coverage(self):
        for name in ('capture_rpx', [], {}):
            stream = Buffer(); execution = events.ExecutionContext(stream, lifecycle=True)
            with execution.task('gfx').scope():
                with events.operation(name):
                    pass
                with events.operation('capture_rpc'):
                    pass
            execution.finish()
            summary = events.lifecycle_summary(stream.getvalue())
            self.assertFalse(summary['complete'])
            self.assertEqual(summary['operations']['capture_rpc']['succeeded'], 1)
            rows = events.read_events(stream.getvalue())['events']
            self.assertFalse(rows[-1]['extensions']['evidence_complete'])
            # A self-serving completion flag cannot erase observed redaction.
            rows[-1]['extensions']['evidence_complete'] = True
            self.assertFalse(events.lifecycle_summary(encode(rows))['complete'])

    def test_evaluation_data_failed_key_is_not_an_rpc_failure(self):
        from vtx import gfx_render as gfx
        stream = Buffer(); execution = events.ExecutionContext(stream, lifecycle=True)
        tab = object.__new__(gfx.Tab)
        tab.call = lambda *args, **kwargs: {'result': {'value': {'failed': True}}}
        with execution.task('gfx').scope():
            self.assertEqual(tab.eval('({failed:true})'), {'failed': True})
        execution.finish()
        summary = events.lifecycle_summary(stream.getvalue())
        self.assertEqual(summary['operations']['evaluate_rpc']['succeeded'], 1)
        self.assertEqual(summary['operations']['evaluate_rpc']['failed'], 0)

    def test_heal_lock_wait_is_separate_from_held_work(self):
        tick = [0.0]; stream = Buffer()
        execution = events.ExecutionContext(stream, lifecycle=True, clock=lambda: tick[0])
        class Lock:
            def acquire(self):
                tick[0] += .010
            def release(self):
                tick[0] += .001
        with execution.task('gfx').scope():
            with events.observed_lock(Lock()):
                tick[0] += .020
        execution.finish()
        result = events.lifecycle_summary(stream.getvalue())
        self.assertAlmostEqual(result['operations']['heal_wait']['cumulative_work_ms'], 10)
        self.assertAlmostEqual(result['command_wall_ms'], 31)


class LifecycleClockIntegrityTests(unittest.TestCase):
    def test_bad_clock_at_each_nested_boundary_preserves_rpc_result(self):
        import contextlib
        import io
        from tests.test_execution_clock import SampleClock
        from vtx import gfx_render as gfx
        # Origin/run start, parent start, RPC start/end, decode start/end,
        # parent end and run terminal all use the same guarded clock.
        for failure in ('nan', 'inf', 'exception', 'backwards'):
            for position in range(2 if failure == 'backwards' else 1, 13):
                with self.subTest(failure=failure, position=position):
                    stream = Buffer()
                    with contextlib.redirect_stderr(io.StringIO()):
                        execution = events.ExecutionContext(stream, lifecycle=True,
                            clock=SampleClock(position, failure))
                        tab = object.__new__(gfx.Tab)
                        tab.call = lambda *args, **kwargs: {'data': 'YWJj'}
                        with execution.task('gfx').scope():
                            self.assertEqual(tab.shot(), b'abc')
                        execution.finish()
                    self.assertTrue(execution.incomplete)
                    summary = events.lifecycle_summary(stream.getvalue())
                    self.assertFalse(summary['complete'])
                    self.assertIsNone(summary['command_wall_ms'])
                    self.assertNotIn('PRIVATE', stream.getvalue())

    def test_clock_failure_does_not_replace_original_operation_exception(self):
        import contextlib
        import io
        tick = [0.0]
        stream = Buffer()
        execution = events.ExecutionContext(stream, lifecycle=True, clock=lambda: tick[0])
        original = RuntimeError('business failure')
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(RuntimeError) as caught:
                with execution.task('gfx').scope():
                    with events.operation('capture_rpc'):
                        tick[0] = float('nan')
                        raise original
            execution.finish(1)
        self.assertIs(caught.exception, original)
        self.assertFalse(events.lifecycle_summary(stream.getvalue())['complete'])

    def test_concurrent_disable_between_wrapper_check_and_operation_entry(self):
        import contextlib
        from vtx import process_samples
        stream = Buffer(); execution = events.ExecutionContext(stream, lifecycle=True)
        execution.process_samples = True
        @contextlib.contextmanager
        def disabled_operation(*args, **kwargs):
            execution.incomplete = True
            yield None
        @events.observed('page_lifecycle', failed=lambda result: True)
        def failed_page():
            return {'failed': [0]}
        @process_samples.observed_tree
        def sampled_page():
            return 42
        for body, expected in ((failed_page, {'failed': [0]}), (sampled_page, 42)):
            execution.incomplete = False
            with execution.task('gfx').bind():
                with patch.object(events, 'operation', disabled_operation):
                    self.assertEqual(body(), expected)
        execution.finish()
        self.assertFalse(events.lifecycle_summary(stream.getvalue())['complete'])
