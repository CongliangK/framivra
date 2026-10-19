"""Explicit ordinary tests only: no browser, security-path probes, or media writes."""
import inspect
import json
from pathlib import Path
import unittest
from unittest import mock

from .dry_plan import measure
from .observer import Observer, union_ns
from .observe_video import check_invocation


class ObserverTests(unittest.TestCase):
    def test_interval_union(self):
        self.assertEqual(union_ns([(0, 4), (2, 6), (7, 9), (3, 3)]), 8)

    def test_nested_exclusive_and_clipping(self):
        observer = Observer()
        observer.spans = [
            dict(id=0, parent=None, thread=1, name='outer', start=0, end=100),
            dict(id=1, parent=0, thread=1, name='inner', start=20, end=70),
            dict(id=2, parent=None, thread=2, name='worker', start=30, end=80)]
        result = observer.summary(10, 60)
        self.assertEqual(result['wall_ns'], 50)
        self.assertEqual(result['observed_union_ns'], 50)
        self.assertEqual(result['categories']['outer']['inclusive_ns'], 50)
        self.assertEqual(result['categories']['outer']['exclusive_ns'], 10)
        self.assertEqual(result['categories']['inner']['exclusive_ns'], 40)
        self.assertEqual(result['thread_union_ns'], {'1': 50, '2': 30})
        self.assertGreater(sum(r['exclusive_ns'] for r in result['categories'].values()), 50)

    def test_return_exception_and_cancellation_identity(self):
        observer = Observer()
        value = object()
        self.assertIs(observer.wrap(lambda: value, 'return')(), value)
        for error in (ValueError('not serialized'), SystemExit(17), KeyboardInterrupt()):
            def fail():
                raise error
            with self.assertRaises(type(error)) as caught:
                observer.wrap(fail, 'failure')()
            self.assertIs(caught.exception, error)
        self.assertEqual(observer.local.stack, [])
        self.assertNotIn('not serialized', json.dumps(observer.report()))

    def test_launcher_reports_system_exit_semantics_and_preserves_object(self):
        import sys
        import tempfile
        from vtx import cli, gfx_cli
        from . import observe_video
        original_validator = gfx_cli.png_invalid_reason
        previous_argv = sys.argv
        # The native command is a mock; no platform check is bypassed to run
        # real rendering, FFmpeg, a browser, or an existing project.
        for code, expected in ((None, 0), (0, 0), (17, 17),
                               ('private failure text', 1), ('', 1)):
            with self.subTest(code=code), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                project = root / 'new-project'
                report_path = root / 'observation.json'
                error = SystemExit(code)
                with mock.patch.object(observe_video, 'check_invocation', return_value=project), \
                        mock.patch.object(cli, 'main', side_effect=error) as native, \
                        self.assertRaises(SystemExit) as caught:
                    observe_video.main(['--report', str(report_path),
                                        '--isolated-project', str(project), '--ack-render-writes',
                                        '--', '-t', str(project / 'timeline.json'), 'video'])
                self.assertIs(caught.exception, error)
                native.assert_called_once_with()
                self.assertIs(sys.argv, previous_argv)
                self.assertIs(gfx_cli.png_invalid_reason, original_validator)
                report = json.loads(report_path.read_text())
                self.assertEqual(report['exit_code'], expected)
                self.assertEqual(report['invocation_outcome'], 'raised')
                self.assertNotIn('private failure text', report_path.read_text())

    def test_thread_stacks_are_separate(self):
        from concurrent.futures import ThreadPoolExecutor
        import threading
        observer = Observer()
        barrier = threading.Barrier(2, timeout=5)
        child = observer.wrap(lambda: barrier.wait(), 'child')
        outer = observer.wrap(child, 'outer')
        with ThreadPoolExecutor(2) as pool:
            list(pool.map(lambda _: outer(), range(2)))
        parents = {s['id']: s for s in observer.spans if s['name'] == 'outer'}
        children = [s for s in observer.spans if s['name'] == 'child']
        self.assertEqual(len(parents), 2)
        self.assertEqual(len({s['thread'] for s in children}), 2)
        for child_span in children:
            self.assertEqual(child_span['thread'], parents[child_span['parent']]['thread'])

    def test_record_limit_reports_unknown_without_dropping_calls(self):
        observer = Observer(max_spans=1)
        seen = []
        wrapped = observer.wrap(lambda: seen.append(True), 'call')
        wrapped(); wrapped()
        self.assertEqual(seen, [True, True])
        self.assertTrue(observer.truncated)
        self.assertEqual(observer.report()['whole_invocation']['status'], 'UNKNOWN')

    def test_installed_preserves_source_digest_and_restores_aliases(self):
        from vtx import composite, frame_source, gfx_cli
        digest = composite._graph_code_digest.__wrapped__()
        source = inspect.getsource(composite.RenderPlan._composite_graph)
        original = gfx_cli.png_invalid_reason
        observer = Observer()
        with observer.installed():
            self.assertEqual(observer.identity, 'PASS')
            self.assertIs(gfx_cli.png_invalid_reason, frame_source.png_invalid_reason)
            self.assertIsNot(gfx_cli.png_invalid_reason, original)
            self.assertEqual(composite._graph_code_digest.__wrapped__(), digest)
            self.assertEqual(inspect.getsource(composite.RenderPlan._composite_graph), source)
        self.assertIs(gfx_cli.png_invalid_reason, original)
        self.assertIs(frame_source.png_invalid_reason, original)

    def test_source_mismatch_refuses_and_uninstalls(self):
        from vtx import composite, gfx_cli
        original = gfx_cli.png_invalid_reason
        with mock.patch.object(composite._graph_code_digest, '__wrapped__', side_effect=['before', 'after']):
            with self.assertRaisesRegex(RuntimeError, 'source identity'):
                with Observer().installed():
                    self.fail('must not enter the native command')
        self.assertIs(gfx_cli.png_invalid_reason, original)

    def test_actual_ffmpeg_boundary_does_not_assume_encoder_role(self):
        observer = Observer(clock=lambda: 200)
        observer.origin = 0
        observer.spans = [
            dict(id=0, parent=None, thread=1, name='video_stage', start=10, end=190, outcome='returned'),
            dict(id=1, parent=0, thread=1, name='media_cache_validation', start=20, end=70, outcome='returned'),
            dict(id=2, parent=1, thread=1, name='ffmpeg_subprocess_run', start=30, end=60, outcome='returned')]
        window = observer.report()['video_windows'][0]
        self.assertEqual(window['first_ffmpeg_offset_ns'], 30)
        self.assertEqual(window['first_ffmpeg_role'], 'unknown')
        self.assertEqual(window['pre_first_ffmpeg']['wall_ns'], 20)
        self.assertEqual(window['first_ffmpeg_ancestor_categories'], ['media_cache_validation', 'video_stage'])

    def test_no_ffmpeg_remains_unobserved(self):
        observer = Observer(clock=lambda: 20)
        observer.spans = [dict(id=0, parent=None, thread=1, name='video_stage',
                               start=1, end=10, outcome='raised')]
        window = observer.report()['video_windows'][0]
        self.assertIsNone(window['first_ffmpeg_offset_ns'])
        self.assertIsNone(window['pre_first_ffmpeg'])

    def test_subprocess_classifier_never_serializes_argv(self):
        self.assertTrue(Observer._is_ffmpeg((['/usr/bin/ffmpeg', 'private'],), {}))
        self.assertFalse(Observer._is_ffmpeg((['ffprobe'],), {}))
        self.assertFalse(Observer._is_ffmpeg(('ffmpeg private',), {}))

    def test_real_cloud_invocation_is_refused_before_files(self):
        with self.assertRaisesRegex(ValueError, 'Mac-only'):
            check_invocation(['-t', 'not-read.json', 'all'], Path('not-read'), True, platform='linux')
        with self.assertRaisesRegex(ValueError, 'ack-render-writes'):
            check_invocation([], Path('not-read'), False, platform='darwin')

    def test_dry_planning_keeps_logical_duplicates_and_does_not_execute(self):
        timeline = dict(project=dict(fps=30, width=64, height=64, duration_frames=6),
                        subtitles=[], video_tracks=[dict(kind='broll', clips=[
                            dict(src='paper', src_in=0, src_out=3, dst_in=0, dur=3),
                            dict(src='paper', src_in=0, src_out=3, dst_in=3, dur=3)])])
        with mock.patch('subprocess.run', side_effect=AssertionError('must not execute')):
            result = measure(json.dumps(timeline).encode(), 2)
        self.assertEqual(result['layers'], 2)
        self.assertEqual(result['non_gfx_descriptor_multiplicities'], [2])
        self.assertEqual(result['overlay_count'], 2)
        self.assertEqual(result['input_count_including_canvas'], 3)


if __name__ == '__main__':
    unittest.main()
