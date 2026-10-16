"""Page/queue observations without browsers or changes to scheduling policy."""
import unittest
from vtx import events
from vtx.gfx_observation import PageJobs
from tests.test_execution_events import Buffer


class PageObservationTests(unittest.TestCase):
    def job(self, execution, page_id, frame_range):
        task = execution.task('gfx', kind='gfx_block', metadata={
            'gfx_key': 'a' * 12, 'page_id': page_id, 'frame_range': frame_range, 'backend': 'cdp'})
        return ('clip', 'a' * 12, {}, 4, None, None, list(range(*frame_range)), task)

    def test_split_page_elapsed_uses_final_child_completion_not_sum(self):
        tick = [0.0]; stream = Buffer()
        execution = events.ExecutionContext(stream, lifecycle=True, clock=lambda: tick[0])
        with execution.task('gfx').scope():
            page_id = str(events.uuid.uuid4())
            jobs = [self.job(execution, page_id, [0, 2]), self.job(execution, page_id, [2, 4])]
            observed = PageJobs(jobs)
            tick[0] = .01
            def render(job):
                tick[0] += .02
                return 42
            self.assertEqual(observed.call(render, jobs[0]), 42)
            self.assertEqual(observed.call(render, jobs[1]), 42)
        execution.finish()
        summary = events.lifecycle_summary(stream.getvalue())
        self.assertTrue(summary['complete'])
        self.assertEqual(summary['pages'][0]['block_count'], 2)
        self.assertAlmostEqual(summary['pages'][0]['elapsed_ms'], 50)
        self.assertAlmostEqual(summary['operations']['queue']['cumulative_work_ms'], 40)

    def test_same_key_different_instances_do_not_merge_and_failure_is_retained(self):
        stream = Buffer(); execution = events.ExecutionContext(stream, lifecycle=True)
        original = RuntimeError('private business error')
        with execution.task('gfx').scope():
            jobs = [self.job(execution, str(events.uuid.uuid4()), [0, 4]) for _ in range(2)]
            jobs = [job[:6] + (None, job[7]) for job in jobs]  # Independent whole-page validators.
            observed = PageJobs(jobs)
            def fail(job):
                raise original
            with self.assertRaises(RuntimeError) as caught:
                observed.call(fail, jobs[0])
            self.assertIs(caught.exception, original)
            observed.call(lambda job: 42, jobs[1])
        execution.finish()
        summary = events.lifecycle_summary(stream.getvalue())
        self.assertTrue(summary['complete'])
        self.assertEqual([p['outcome'] for p in summary['pages']], ['failed', 'succeeded'])
        self.assertNotIn('private', stream.getvalue())

    def test_unstarted_block_is_incomplete_not_a_completed_page(self):
        stream = Buffer(); execution = events.ExecutionContext(stream, lifecycle=True)
        with execution.task('gfx').scope():
            PageJobs([self.job(execution, str(events.uuid.uuid4()), [0, 4])])
        execution.finish()
        summary = events.lifecycle_summary(stream.getvalue())
        self.assertFalse(summary['complete'])
        self.assertEqual(summary['pages'], [])

    def test_parallel_last_child_closes_parent_after_all_block_terminals(self):
        import threading
        from concurrent.futures import ThreadPoolExecutor
        stream = Buffer(); execution = events.ExecutionContext(stream, lifecycle=True)
        with execution.task('gfx').scope():
            page_id = str(events.uuid.uuid4())
            jobs = [self.job(execution, page_id, [0, 2]), self.job(execution, page_id, [2, 4])]
            observed = PageJobs(jobs)
            barrier = threading.Barrier(2)
            def render(job):
                barrier.wait(timeout=5)
                return 42
            with ThreadPoolExecutor(2) as pool:
                self.assertEqual(list(pool.map(lambda job: observed.call(render, job), jobs)), [42, 42])
        execution.finish()
        result = events.read_events(stream.getvalue())
        self.assertTrue(result['complete'])
        page = events.lifecycle_summary(stream.getvalue())['pages'][0]
        terminal = next(r for r in result['events'] if r['task_id'] == page['task_id'] and r['event'] == 'task_terminal')
        children = [r for r in result['events'] if r['parent_task_id'] == page['task_id']]
        self.assertTrue(all(r['seq'] < terminal['seq'] for r in children))

    def test_production_split_page_end_follows_actual_validation_gate(self):
        import os
        import sys
        from types import SimpleNamespace
        from unittest import mock
        from vtx import cli
        from tests.test_gfx_cli_fallback import GfxCliEntryTests, MINIMAL_PNG, PNG_SIGNATURE
        for corrupt, repeated in ((False, False), (True, False), (False, True), (True, True)):
            case = GfxCliEntryTests(); case.setUp()
            try:
                c = case.context(2)
                if repeated:
                    clips = c.tl['video_tracks'][0]['clips']
                    clips.append(dict(clips[0], dst_in=2))
                    import json
                    c.tl['project']['duration_frames'] = 4
                    c.tl_path.write_text(json.dumps(c.tl))
                    c = type(c)(c.tl_path)
                stream = Buffer(); execution = events.ExecutionContext(stream, lifecycle=True)
                c.execution = execution
                def render(html, directory, count, **kwargs):
                    for index in kwargs['frames']:
                        (directory / f'f{index:04d}.png').write_bytes(
                            PNG_SIGNATURE if corrupt and index == 1 else MINIMAL_PNG)
                    return {'failed': [], 'rendered': 1, 'ms_total': 1, 'tabs': 1,
                            'path_counts': {}, 'timings_ms': {}}
                fake = SimpleNamespace(available=lambda: True, render_page=render)
                validated_at = []
                original_validator = cli.gfx_cli.complete_page
                def validate(*args, **kwargs):
                    validated_at.append(execution.seq)
                    return original_validator(*args, **kwargs)
                with mock.patch.dict(sys.modules, {'vtx.gfx_render': fake}), \
                     mock.patch('vtx.gfx_render', fake, create=True), \
                     mock.patch.object(cli, 'find_chrome', return_value='unused'), \
                     mock.patch.object(cli.gfx_cli, 'complete_page', side_effect=validate), \
                     mock.patch.dict(os.environ, {'VTX_GFX': 'cdp', 'VTX_GFX_SPLIT_FRAMES': '1', 'VTX_GFX_PAGES': '1'}):
                    with execution.task('gfx').scope():
                        if corrupt:
                            with self.assertRaises(SystemExit):
                                cli.gfx(c)
                        else:
                            cli.gfx(c)
                execution.finish()
                summary = events.lifecycle_summary(stream.getvalue())
                self.assertTrue(summary['complete'])
                self.assertEqual(len(summary['pages']), 2 if repeated else 1)
                self.assertEqual(len(validated_at), 1)
                page_ids = {page['task_id'] for page in summary['pages']}
                rows = events.read_events(stream.getvalue())['events']
                self.assertTrue(all(row['seq'] > validated_at[0] for row in rows
                    if row['event'] == 'task_terminal' and row['task_id'] in page_ids))
                self.assertEqual(len({page['validation_group_id'] for page in summary['pages']}), 1)
                for page in summary['pages']:
                    self.assertEqual(page['block_count'], 2)
                    self.assertEqual(page['outcome'], 'failed' if corrupt else 'succeeded')
            finally:
                case.tearDown()
                case.doCleanups()
