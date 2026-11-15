"""Small CPU-only study instrumentation tests; not rendering/performance evidence."""
from contextlib import ExitStack
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from experiments.perf_html_workflow import observe
from experiments.perf_html_workflow.quality import audio_timeline, png_inventory
from vtx.cache import session as cache_session
from experiments.perf_html_workflow.summarize import spans, qualified_median
from vtx.render import gfx_render


class SpanTests(unittest.TestCase):
    def test_median_excludes_unqualified_pairs_but_retains_observations(self):
        rows = [{'name': '1-cold', 'external_wall_s': 1, 'qualified_pair': False},
                {'name': '2-cold', 'external_wall_s': 10, 'qualified_pair': True},
                {'name': '3-cold', 'external_wall_s': 12, 'qualified_pair': True}]
        self.assertEqual(qualified_median(rows, 'cold'), 11)
        self.assertIsNone(qualified_median(rows, 'warm'))
        self.assertEqual(len(rows), 3)

    def test_overlapping_work_is_not_wall_union_and_failures_remain(self):
        result = spans([{'start_s': 0, 'end_s': 3, 'status': 'ok'},
                        {'start_s': 1, 'end_s': 4, 'status': 'failed'},
                        {'start_s': 6, 'end_s': 7, 'status': 'ok'}])
        self.assertEqual(result, {'calls': 3, 'failed_calls': 1, 'work_s': 7, 'union_s': 5})
        self.assertEqual(spans([]), {'calls': 0, 'failed_calls': 0, 'work_s': 0, 'union_s': 0})


class ObserverTests(unittest.TestCase):
    def invoke(self, cli):
        originals = [(gfx_render.Browser, 'start'), (gfx_render.Browser, 'stop'),
                     (gfx_render.Tab, 'call'), (gfx_render.Tab, 'navigate'),
                     (gfx_render, '_atomic_write_bytes'), (gfx_render, 'render_page'),
                     (cache_session, 'hydrate_graphics'), (cache_session, 'compact_graphics')]
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            output = Path(directory) / 'observation.json'
            for owner, name in originals:
                # Restore every monkeypatch made by observe.main, even on exceptions.
                stack.enter_context(mock.patch.object(owner, name, getattr(owner, name)))
            stack.enter_context(mock.patch.object(gfx_render, 'render_page', return_value={'sentinel': 1}))
            stack.enter_context(mock.patch('vtx.cli.commands.main', side_effect=cli))
            stack.enter_context(mock.patch.object(sys, 'argv', ['observe', str(output), 'all']))
            value, error = None, None
            try:
                value = observe.main()
            except BaseException as exc:
                error = exc
            return value, error, json.loads(output.read_text())

    def test_preserves_return_and_renderer_return(self):
        def cli():
            self.assertEqual(gfx_render.render_page('chart.html', frames=[0]), {'sentinel': 1})
            return 5
        value, error, result = self.invoke(cli)
        self.assertEqual(value, 5)
        self.assertIsNone(error)
        self.assertEqual(result['exit_code'], 5)
        row = result['records'][0]
        self.assertEqual((row['operation'], row['status'], row['frames']), ('render_page', 'ok', [0]))
        self.assertEqual(row['stats'], {'sentinel': 1})

    def test_preserves_zero_and_nonzero_system_exit(self):
        for code in (0, 2):
            with self.subTest(code=code):
                def cli():
                    raise SystemExit(code)
                _, error, result = self.invoke(cli)
                self.assertIsInstance(error, SystemExit)
                self.assertEqual(error.code, code)
                self.assertEqual(result['exit_code'], code)

    def test_preserves_exception_and_failed_operation(self):
        failure = RuntimeError('test failure')
        def cli():
            # The captured original below raises, not an actual renderer.
            raise failure
        _, error, result = self.invoke(cli)
        self.assertIs(error, failure)
        self.assertEqual(result['exit_code'], 1)

    def test_preserves_failed_wrapped_call(self):
        failure = ValueError('frame failed')
        original = gfx_render.render_page
        def fake(*args, **kwargs):
            raise failure
        # invoke's controlled render stub is itself replaced before observation wraps it.
        with mock.patch.object(gfx_render, 'render_page', fake):
            with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
                output = Path(directory) / 'observation.json'
                for owner, name in [(gfx_render.Browser, 'start'), (gfx_render.Browser, 'stop'),
                                    (gfx_render.Tab, 'call'), (gfx_render.Tab, 'navigate'),
                                    (gfx_render, '_atomic_write_bytes'), (gfx_render, 'render_page'),
                     (cache_session, 'hydrate_graphics'), (cache_session, 'compact_graphics')]:
                    stack.enter_context(mock.patch.object(owner, name, getattr(owner, name)))
                stack.enter_context(mock.patch.object(sys, 'argv', ['observe', str(output), 'all']))
                stack.enter_context(mock.patch('vtx.cli.commands.main', side_effect=lambda: gfx_render.render_page('chart.html')))
                with self.assertRaises(ValueError) as caught:
                    observe.main()
                self.assertIs(caught.exception, failure)
                result = json.loads(output.read_text())
                self.assertEqual(result['records'][0]['status'], 'failed')
                self.assertEqual(result['records'][0]['error'], 'frame failed')
        self.assertIs(gfx_render.render_page, original)


class InventoryTests(unittest.TestCase):
    def test_tmp_parent_retains_pack_and_excludes_only_cache_tmp(self):
        from experiments.perf_html_workflow.run import inventory
        with tempfile.TemporaryDirectory(dir='/tmp') as directory:
            project = Path(directory).resolve()
            kept = project / 'cache/gfx_packs/gfx/page.vtxp'
            skipped = project / 'cache/tmp/transient.vtxp'
            for path in (kept, skipped):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'fixture')
            self.assertEqual(set(inventory(project)), {'cache/gfx_packs/gfx/page.vtxp'})

    def test_real_small_pack_and_loose_decode_match_without_mutation(self):
        from PIL import Image
        from vtx.render.gfx_pack import pack_page
        from experiments.perf_html_workflow.quality import sha
        with tempfile.TemporaryDirectory(dir='/tmp') as directory:
            project = Path(directory).resolve()
            page = project / 'source'
            page.mkdir()
            for index in range(2):
                Image.new('RGBA', (1, 1), (index * 120, 20, 30, 255)).save(page / ('f%04d.png' % index))
            record = {'schema': 1, 'key': 'fixture', 'frames': 2, 'width': 1, 'height': 1,
                      'sha256': [sha(page / ('f%04d.png' % i)) for i in range(2)],
                      'environment': {'status': 'input-bound'}}
            (page / 'complete.json').write_text(json.dumps(record))
            target = project / 'cache/gfx_packs/gfx/page.vtxp'
            target.parent.mkdir(parents=True)
            pack_page(page, target, expected_record=record, root=project)
            before = {str(p): (sha(p), p.stat().st_mtime_ns) for p in project.rglob('*') if p.is_file()}
            packed = png_inventory(project, 2, (1, 1))
            after = {str(p): (sha(p), p.stat().st_mtime_ns) for p in project.rglob('*') if p.is_file()}
            self.assertEqual(before, after)
            self.assertEqual(packed['storage'], 'page_pack')
            self.assertEqual(packed['packed_sources'][0]['header']['record'], record)
            self.assertEqual(json.loads(packed['packed_sources'][0]['metadata']['complete.json']['utf8']), record)
            with tempfile.TemporaryDirectory(dir='/tmp') as loose_directory:
                import shutil
                loose_project = Path(loose_directory).resolve()
                shutil.copytree(page, loose_project / 'cache/gfx/page')
                loose = png_inventory(loose_project, 2, (1, 1))
                self.assertEqual(loose['file_aggregate_sha256'], packed['file_aggregate_sha256'])
                self.assertEqual(loose['rgba_aggregate_sha256'], packed['rgba_aggregate_sha256'])
            with self.assertRaises(ValueError):
                png_inventory(project)  # A tiny fixture cannot masquerade as the 600f workload.


class AudioTimelineTests(unittest.TestCase):
    stream = {'sample_rate': '48000', 'channels': 2, 'time_base': '1/48000',
              'duration_ts': 960000, 'start_time': '0.000000'}

    def text(self, pts=0, duration=960000, size=3840000):
        return '#tb 0: 1/48000\n0, %s, %s, %s, %s, %s\n' % (pts, pts, duration, size, 'a' * 32)

    def test_complete_original_timeline(self):
        result = audio_timeline(self.text(), self.stream)
        self.assertEqual(result['sample_frames'], 960000)
        self.assertTrue(result['continuity_valid'])

    def test_offset_gap_overlap_and_bad_duration_reject(self):
        for text in (self.text(pts=1), self.text(duration=959999),
                     '#tb 0: 1/48000\n0, 0, 0, 480000, 1920000, ' + 'a' * 32 +
                     '\n0, 480001, 480001, 480000, 1920000, ' + 'b' * 32 + '\n',
                     '#tb 0: 1/48000\n0, 0, 0, 480000, 1920000, ' + 'a' * 32 +
                     '\n0, 479999, 479999, 480000, 1920000, ' + 'b' * 32 + '\n'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                audio_timeline(text, self.stream)

    def test_source_offset_and_wrong_format_reject(self):
        for changes in ({'start_time': '0.1'}, {'sample_rate': '44100'},
                        {'channels': 1}, {'duration_ts': 962000}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                audio_timeline(self.text(), dict(self.stream, **changes))

    def test_missing_truncated_and_duplicate_header_reject(self):
        for text in ('', self.text(size=4, duration=1), '#tb 0: 1/48000\n' + self.text()):
            with self.subTest(text=text), self.assertRaises(ValueError):
                audio_timeline(text, self.stream)


if __name__ == '__main__':
    unittest.main()
