"""Issue #33: PNG/page integrity, resumable repair, completion and zero-write dry.

Fixtures use isolated TemporaryDirectory projects; no real project is touched.
VTX_TEST_CHROME enables the small real-browser cache-repair check explicitly.
"""
from types import SimpleNamespace
from tests.gfx._png import write_png
from tests._support import temporary_root
from tests.gfx._environment import bind_environment
from vtx.render import frame_source, gfx_cache_reader
from vtx.render.gfx_environment import certificate
from vtx.cache.hygiene import gfx_dir, gfx_key

import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import struct
import tempfile
import unittest
import zlib
from unittest import mock

from tests.gfx._png import png, chunk

from vtx.cli import commands as cli
from vtx.render import gfx_cli, gfx_render
from vtx.cache import hygiene
from vtx.render import gfx_environment
from vtx.pipeline.infra import Ctx
from vtx.pipeline.composite import prepare_layers
from vtx.runtime import events






def stats(n):
    return dict(failed=[], rendered=n, ms_total=1, tabs=1, ms_per_frame=1,
                path_counts={}, timings_ms={}, static_spans=0)


class CacheIntegrity(unittest.TestCase):
    def setUp(self):
        from tests.gfx._environment import bind_environment
        bind_environment(self)
        self.tmp = tempfile.TemporaryDirectory(prefix='vtx_issue33_')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'page.html').write_text('<html><body>fixture</body></html>')
        self.timeline = self.root / 'timeline.json'
        self.timeline.write_text(json.dumps(dict(
            project=dict(fps=30, width=6, height=4, duration_frames=3), sources=[],
            subtitles=[], audio_tracks=[], video_tracks=[dict(kind='gfx', clips=[
                dict(src='page.html', dst_in=0, dur=3)])])))
        self.c = Ctx(self.timeline)
        self.cl = self.c.tl['video_tracks'][0]['clips'][0]
        self.key = hygiene.gfx_key(self.c, self.cl)
        self.d = hygiene.gfx_dir(self.c, self.cl)
        self.d.mkdir(parents=True)
        self.rendered = []

    def frames(self):
        for i in range(3):
            (self.d / f'f{i:04d}.png').write_bytes(png(value=i))

    def complete(self):
        gfx_cli.complete_page(self.d, 3, self.key, 6, 4,
                              verify_inputs=lambda: self.c.verify_inputs('gfx'),
                              environment=gfx_environment.certificate(self.c))

    def renderer(self, html, d, n, **kw):
        targets = range(n) if kw['frames'] is None else kw['frames']
        work = [i for i, _ in gfx_cli.verify_frames(d, targets, width=6, height=4)]
        for i in work:
            self.rendered.append(i)
            (d / f'f{i:04d}.png').write_bytes(png(value=i))
        return stats(len(work))

    def run_gfx(self, renderer=None, split='0', pages='1'):
        with mock.patch.dict(os.environ, VTX_GFX='cdp', VTX_GFX_SPLIT_FRAMES=split,
                             VTX_GFX_SPLIT_MAX='3', VTX_GFX_PAGES=pages), \
                mock.patch.object(cli, 'find_chrome', return_value='unit-fixture-chrome'), \
                mock.patch.object(gfx_render, 'available', return_value=True), \
                mock.patch.object(gfx_render, 'render_page', side_effect=renderer or self.renderer), \
                contextlib.redirect_stdout(io.StringIO()):
            cli.gfx(self.c)

    def duplicate_clips(self, *, project=None, **changes):
        timeline = json.loads(self.timeline.read_text())
        first = dict(src='page.html', dst_in=0, dur=3)
        second = dict(first, dst_in=3, x=2, y=1, scale=.5, opacity=.75,
                      fade_in_frames=1)
        second.update(changes)
        timeline['video_tracks'][0]['clips'] = [first, second]
        timeline['project'].update(project or {})
        timeline['project']['duration_frames'] = max(cl['dst_in'] + cl['dur']
                                                     for cl in (first, second))
        self.timeline.write_text(json.dumps(timeline))
        self.c = Ctx(self.timeline)
        self.cl = self.c.tl['video_tracks'][0]['clips'][0]
        self.key, self.d = hygiene.gfx_key(self.c, self.cl), hygiene.gfx_dir(self.c, self.cl)
        self.d.mkdir(parents=True, exist_ok=True)

    def assert_gfx_layers(self):
        # Exercise the real compositor preparation and authenticated PNG consumer,
        # without probing an encoder or invoking FFmpeg.
        with mock.patch.dict(os.environ, VTX_SEG_NVENC='0'):
            layers = prepare_layers(self.c)
        clips = self.c.tl['video_tracks'][0]['clips']
        self.assertEqual(len(layers), len(clips))
        for layer, clip in zip(layers, clips):
            self.assertIs(layer['cl'], clip)
            self.assertEqual((layer['x'], layer['y'], layer['ti'], layer['gfx']),
                             (clip.get('x', 0), clip.get('y', 0), 0, True))
            self.assertEqual((layer['w2'], layer['h2']), (None, None))
            source = layer['source']
            self.assertEqual(source.directory, hygiene.gfx_dir(self.c, clip))
            self.assertEqual(source.frame_count, clip['dur'])
            self.assertEqual(source.input_args()[-1], str(source.directory / 'f%04d.png'))

    def test_duplicate_clips_produce_once_per_block_and_keep_layers(self):
        self.duplicate_clips()
        for pages, split in (('1', '0'), ('3', '0'), ('1', '1'), ('3', '1')):
            shutil.rmtree(self.d)
            self.d.mkdir()
            for state, repaired in (('cold', [0, 1, 2]), ('warm', []),
                                    ('corrupt', [1]), ('partial', [2])):
                with self.subTest(pages=pages, split=split, state=state):
                    if state == 'corrupt':
                        (self.d / 'f0001.png').write_bytes(b'broken')
                    elif state == 'partial':
                        (self.d / 'f0002.png').unlink()
                    self.rendered.clear()
                    stream = io.StringIO()
                    self.c.execution = events.ExecutionContext(stream)
                    renderer = mock.Mock(side_effect=self.renderer)
                    with mock.patch.object(gfx_cli, 'inspect_page', wraps=gfx_cli.inspect_page) as inspect, \
                            mock.patch.object(gfx_cli, 'complete_page', wraps=gfx_cli.complete_page) as complete:
                        self.run_gfx(renderer, split=split, pages=pages)
                    expected = len(repaired) if split == '1' else int(bool(repaired))
                    self.assertEqual(renderer.call_count, expected)
                    self.assertEqual(inspect.call_count, 2)
                    self.assertEqual(complete.call_count, int(bool(repaired)))
                    self.assertCountEqual(self.rendered, repaired)
                    self.assertCountEqual([call.kwargs['frames'] for call in renderer.call_args_list],
                                          [[i] for i in repaired] if split == '1' else
                                          ([None] if repaired else []))
                    detail = json.loads((self.c.out / 'receipt.json').read_text())['stages']['gfx']['detail']
                    self.assertTrue(detail.startswith('2 gfx clips'))
                    self.assert_gfx_layers()
                    with mock.patch.object(stream, 'close'):
                        self.c.execution.finish(0)
                    observed = events.read_events(stream.getvalue())
                    self.assertTrue(observed['complete'])
                    blocks = [row for row in observed['events']
                              if row['event'] == 'task_start' and row['scope'] == 'gfx_block']
                    self.assertEqual(len(blocks), expected)
                    self.assertEqual(len({row['extensions']['page_id'] for row in blocks}),
                                     int(bool(repaired)))
                    self.assertEqual(len({row['task_id'] for row in blocks}), expected)

    def test_duplicate_clips_dry_preserves_bytes_and_schedules_one_page(self):
        self.duplicate_clips()
        for state in ('cold', 'warm', 'corrupt'):
            with self.subTest(state=state):
                self.c.dry = False
                if state == 'warm':
                    self.frames()
                    self.complete()
                elif state == 'corrupt':
                    (self.d / 'f0001.png').write_bytes(b'broken')
                before = {str(p.relative_to(self.root)): p.read_bytes() if p.is_file() else None
                          for p in self.root.rglob('*')}
                self.c.dry = True
                renderer = mock.Mock(side_effect=AssertionError('dry producer'))
                with mock.patch.object(cli.events, 'task', wraps=cli.events.task) as task, \
                        mock.patch.object(self.c, 'receipt', wraps=self.c.receipt) as receipt:
                    self.run_gfx(renderer, split='1', pages='3')
                renderer.assert_not_called()
                self.assertEqual(sum(call.kwargs.get('kind') == 'gfx_block'
                                     for call in task.call_args_list), int(state != 'warm'))
                self.assertEqual(receipt.call_args.args, ('gfx', True, '2 gfx clips'))
                after = {str(p.relative_to(self.root)): p.read_bytes() if p.is_file() else None
                         for p in self.root.rglob('*')}
                self.assertEqual(before, after)

    def test_duplicate_clips_failure_and_cancellation_cannot_certify(self):
        self.duplicate_clips()
        for failure, code in ((stats(0), 1), (SystemExit(130), 130)):
            with self.subTest(failure=type(failure).__name__):
                shutil.rmtree(self.d)
                self.d.mkdir()
                renderer = mock.Mock(side_effect=failure) if isinstance(failure, BaseException) else \
                    mock.Mock(return_value=failure)
                with self.assertRaises(SystemExit) as raised:
                    self.run_gfx(renderer, pages='3')
                self.assertEqual(raised.exception.code, code)
                self.assertEqual(renderer.call_count, 1)
                self.assertFalse((self.d / gfx_cli.COMPLETE_FILE).exists())
                self.assertFalse(json.loads((self.c.out / 'receipt.json').read_text())['stages']['gfx']['ok'])
                for clip in self.c.tl['video_tracks'][0]['clips']:
                    with self.assertRaises(ValueError):
                        frame_source.png_source(self.c, clip).require_ready()
                retry = mock.Mock(side_effect=self.renderer)
                self.run_gfx(retry, pages='3')
                self.assertEqual(retry.call_count, 1)
                self.assert_gfx_layers()

    def test_duplicate_clips_do_not_skip_later_input_or_cache_validation(self):
        for policy in ('legacy', 'strict'):
            with self.subTest(policy=policy):
                self.duplicate_clips(project={'gfx_dependency_policy': policy})
                inspect = gfx_cli.inspect_page
                calls = []
                def check(*args, **kwargs):
                    calls.append(args)
                    if len(calls) == 2:
                        raise ValueError('second clip validation failed')
                    return inspect(*args, **kwargs)
                renderer = mock.Mock(side_effect=AssertionError('unvalidated clip'))
                with mock.patch.object(gfx_cli, 'inspect_page', side_effect=check), \
                        mock.patch.object(cli, 'gfx_key', wraps=cli.gfx_key) as key, \
                        self.assertRaises(SystemExit):
                    self.run_gfx(renderer)
                self.assertEqual(key.call_count, 2)
                self.assertEqual(len(calls), 2)
                renderer.assert_not_called()
                self.assertFalse((self.d / gfx_cli.COMPLETE_FILE).exists())

    def test_distinct_source_duration_and_dependency_keep_separate_jobs(self):
        (self.root / 'copy.html').write_bytes((self.root / 'page.html').read_bytes())
        (self.root / 'asset.txt').write_text('declared dependency')
        for change in ({'src': 'copy.html'}, {'dur': 4}, {'cache_dependencies': ['asset.txt']}):
            with self.subTest(change=change):
                self.duplicate_clips(**change)
                shutil.rmtree(self.c.cache)
                renderer = mock.Mock(side_effect=self.renderer)
                self.run_gfx(renderer, pages='1')
                self.assertEqual(renderer.call_count, 2)
                calls = renderer.call_args_list
                self.assertEqual(len({call.args[1] for call in calls}), 1 if 'src' in change else 2)
                self.assertEqual([call.args[0].name for call in calls],
                                 ['page.html', change.get('src', 'page.html')])
                self.assert_gfx_layers()

    def test_duplicate_strict_clips_keep_dependency_completion(self):
        self.duplicate_clips(project={'gfx_dependency_policy': 'strict'})
        def render(html, d, n, **kwargs):
            result = self.renderer(html, d, n, **kwargs)
            result.update(dependency_enforcement='fetch-pinned-local-v1',
                          dependency_observed=[kwargs['dependency_record']['html_uri']])
            return result
        renderer = mock.Mock(side_effect=render)
        self.run_gfx(renderer, split='1', pages='3')
        self.assertEqual(renderer.call_count, 1)  # strict does not split or fall back
        self.assertIsNone(renderer.call_args.kwargs['frames'])
        record = json.loads((self.d / gfx_cli.COMPLETE_FILE).read_text())
        self.assertEqual(record['dependency_enforcement'], 'fetch-pinned-local-v1')
        self.assertEqual(record['dependency_observed'], [(self.root / 'page.html').as_uri()])
        self.assert_gfx_layers()
        renderer.reset_mock()
        self.run_gfx(renderer, pages='3')
        renderer.assert_not_called()

    def test_duplicate_cef_clips_dispatch_once_without_cdp_or_cli(self):
        worker = self.root / 'fake-worker'
        worker.write_text('#!/bin/sh\nexit 99\n')
        worker.chmod(0o700)
        def render(html, d, n, **kwargs):
            for i in range(n):
                (d / f'f{i:04d}.png').write_bytes(png(16, 16, value=i))
            return 'unit-fixture CEF'
        with mock.patch.dict(os.environ, VTX_GFX='cef', VTX_CEF_WORKER=str(worker),
                             VTX_CEF_MODE='a0', VTX_GFX_PAGES='3', VTX_GFX_SPLIT_FRAMES='1'), \
                mock.patch('vtx.render.gfx_cef.render_page', side_effect=render) as renderer, \
                mock.patch.object(gfx_render, 'render_page', side_effect=AssertionError('CDP')), \
                mock.patch.object(gfx_cli, 'render_cli', side_effect=AssertionError('CLI')), \
                contextlib.redirect_stdout(io.StringIO()):
            self.duplicate_clips(project={'width': 16, 'height': 16})
            cli.gfx(self.c)
            self.assertEqual(renderer.call_count, 1)
            self.assert_gfx_layers()
            cli.gfx(self.c)
            self.assertEqual(renderer.call_count, 1)

    def test_missing_or_malformed_marker_requires_environment_rerender(self):
        self.frames()
        for content in (None, '{broken', json.dumps(dict(schema=999))):
            marker = self.d / gfx_cli.COMPLETE_FILE
            if content is None:
                marker.unlink(missing_ok=True)
            else:
                marker.write_text(content)
            self.rendered.clear()
            self.run_gfx()
            self.assertEqual(self.rendered, [0, 1, 2])
            self.assertEqual(gfx_cli.inspect_page(self.d, 3, self.key, 6, 4), ([], False))
            self.assertEqual(len(json.loads(marker.read_text())['sha256']), 3)

    def test_wrong_size_truncation_crc_and_filter_are_repaired(self):
        for bad in (png(5, 4), png()[:-1], png()[:29] + bytes([png()[29] ^ 1]) + png()[30:],
                    png(filter_byte=5)):
            with self.subTest(kind=bad[:35]):
                self.frames()
                self.complete()
                (self.d / 'f0001.png').write_bytes(bad)
                kept = (self.d / 'f0000.png').read_bytes()
                self.rendered.clear()
                self.run_gfx()
                self.assertEqual(self.rendered, [1])
                self.assertEqual((self.d / 'f0000.png').read_bytes(), kept)
                self.assertEqual(gfx_cli.inspect_page(self.d, 3, self.key, 6, 4), ([], False))

    def test_valid_but_changed_frame_digest_forces_repair(self):
        self.frames()
        self.complete()
        (self.d / 'f0002.png').write_bytes(png(value=77))
        self.run_gfx()
        self.assertEqual(self.rendered, [2])
        self.assertEqual((self.d / 'f0002.png').read_bytes(), png(value=2))

    def test_stale_contract_marker_forces_full_repair(self):
        self.frames()
        self.complete()
        marker = self.d / gfx_cli.COMPLETE_FILE
        obj = json.loads(marker.read_text())
        obj['width'] = 1
        marker.write_text(json.dumps(obj))
        self.run_gfx()
        self.assertEqual(self.rendered, [0, 1, 2])

    def test_partial_page_revokes_marker_and_resumes_only_missing(self):
        self.frames()
        self.complete()
        (self.d / 'f0002.png').unlink()
        def fail(html, d, n, **kw):
            self.assertFalse((d / gfx_cli.COMPLETE_FILE).exists())
            return stats(0)  # lies about success: final whole-page gate must reject
        with self.assertRaises(SystemExit), contextlib.redirect_stdout(io.StringIO()):
            self.run_gfx(renderer=fail)
        self.assertFalse((self.d / gfx_cli.COMPLETE_FILE).exists())
        self.assertFalse(json.loads((self.c.out / 'receipt.json').read_text())['stages']['gfx']['ok'])
        self.run_gfx()
        self.assertEqual(self.rendered, [2])
        self.assertEqual(gfx_cli.inspect_page(self.d, 3, self.key, 6, 4), ([], False))

    def test_split_final_gate_and_marker_wait_for_all_blocks(self):
        def split(html, d, n, **kw):
            self.assertFalse((d / gfx_cli.COMPLETE_FILE).exists())
            return self.renderer(html, d, n, **kw)
        self.run_gfx(renderer=split, split='1')
        self.assertEqual(self.rendered, [0, 1, 2])
        self.assertEqual(gfx_cli.inspect_page(self.d, 3, self.key, 6, 4), ([], False))

    def test_split_bad_dimensions_cannot_publish_completion(self):
        def bad(html, d, n, **kw):
            for i in kw['frames']:
                (d / f'f{i:04d}.png').write_bytes(png(1, 1))
            return stats(len(kw['frames']))
        with self.assertRaises(SystemExit):
            self.run_gfx(renderer=bad, split='1')
        self.assertFalse((self.d / gfx_cli.COMPLETE_FILE).exists())
        self.assertFalse(json.loads((self.c.out / 'receipt.json').read_text())['stages']['gfx']['ok'])

    def test_warm_cache_never_starts_browser_or_cli(self):
        self.frames()
        self.complete()
        with mock.patch.object(gfx_render, 'Browser', side_effect=AssertionError('Chrome')), \
                mock.patch.object(gfx_cli, 'run_cli_shot', side_effect=AssertionError('CLI')):
            self.run_gfx(renderer=lambda *a, **k: self.fail('warm cache'))

    def test_dry_preserves_invalid_cache_and_marker_bytes(self):
        self.frames()
        self.complete()
        (self.d / 'f0002.png').write_bytes(b'broken')
        before = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.c.dry = True
        self.run_gfx(renderer=lambda *a, **k: self.fail('dry Chrome'))
        after = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(before, after)

    def test_input_change_blocks_new_marker(self):
        self.frames()
        self.timeline.write_bytes(self.timeline.read_bytes() + b' ')
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            self.complete()
        self.assertFalse((self.d / gfx_cli.COMPLETE_FILE).exists())

    def test_atomic_marker_failure_does_not_certify_or_leave_tmp(self):
        self.frames()
        with mock.patch('vtx.runtime.fstxn.os.replace', side_effect=OSError('injected interruption')):
            with self.assertRaises(OSError):
                self.complete()
        self.assertFalse((self.d / gfx_cli.COMPLETE_FILE).exists())
        self.assertFalse(list(self.d.glob('*.tmp')))

    def test_png_structure_filters_and_dimensions(self):
        target = self.d / 'f0000.png'
        for bad in (png() + b'trailing', png(filter_byte=255),
                    gfx_cli.PNG_SIGNATURE + chunk(b'IHDR', struct.pack('>IIBBBBB', 6, 4, 8, 6, 0, 0, 0))
                    + chunk(b'IHDR', b'X' * 13) + png()[33:]):
            target.write_bytes(bad)
            self.assertIsNotNone(gfx_cli.png_invalid_reason(target, width=6, height=4))
        # Scanline filters cross both compressed read and bounded output boundaries.
        big = self.d / 'big.png'
        big.write_bytes(png(400, 700, filter_byte=0))
        self.assertIsNone(gfx_cli.png_invalid_reason(big, width=400, height=700))
        self.assertIsNotNone(gfx_cli.png_invalid_reason(big, width=6, height=4))


@unittest.skipUnless(os.environ.get('VTX_TEST_CHROME'), 'set VTX_TEST_CHROME for real cache repair')
class RealBrowserIntegrity(unittest.TestCase):
    def test_real_cdp_repair_and_zero_browser_warm_hit(self):
        import numpy as np
        with tempfile.TemporaryDirectory(prefix='vtx_issue33_chrome_') as tmp:
            root = Path(tmp)
            (root / 'page.html').write_text('''<!doctype html><style>html,body{margin:0;background:transparent}
                div{width:120px;height:100px;background:rgba(255,0,30,.5)}</style><div></div>''')
            p = root / 'timeline.json'
            p.write_text(json.dumps(dict(project=dict(fps=30, width=320, height=240, duration_frames=3),
                                        sources=[], subtitles=[], audio_tracks=[], video_tracks=[
                                            dict(kind='gfx', clips=[dict(src='page.html', dst_in=0, dur=3)])])))
            c = Ctx(p)
            cl = c.tl['video_tracks'][0]['clips'][0]
            d = hygiene.gfx_dir(c, cl)
            key = hygiene.gfx_key(c, cl)
            with mock.patch.dict(os.environ, VTX_CHROME=os.environ['VTX_TEST_CHROME'], VTX_GFX='cdp',
                                 VTX_GFX_PAGES='1', VTX_GFX_TABS='1', VTX_GFX_SPLIT_FRAMES='0'):
                cli.gfx(c)
                pixels = [gfx_render.png_decode_rgba((d / f'f{i:04d}.png').read_bytes()) for i in range(3)]
                (d / 'f0001.png').write_bytes(png(320, 240)[:-1])
                cli.gfx(Ctx(p))
                for i in range(3):
                    np.testing.assert_array_equal(pixels[i], gfx_render.png_decode_rgba((d / f'f{i:04d}.png').read_bytes()))
                self.assertEqual(gfx_cli.inspect_page(d, 3, key, 320, 240), ([], False))
                with mock.patch.object(gfx_render, 'Browser', side_effect=AssertionError('warm Chrome')):
                    cli.gfx(Ctx(p))



class CompletionConsumers(unittest.TestCase):
    """Lazy and whole-page consumers share proof, not their byte-read scope."""
    def setUp(self):
        bind_environment(self)
        self.root = temporary_root(self)
        (self.root / 'page.html').write_text('<html><body>inert</body></html>')
        self.clip = dict(src='page.html', dst_in=0, dur=3)
        self.c = SimpleNamespace(root=self.root, cache=self.root / 'cache',
                                 fps=30, w=16, h=16, tl={'project': {}})
        patch = mock.patch.dict(os.environ, VTX_GFX='cdp', VTX_GFX_CACHE_NS='selected')
        patch.start()
        self.addCleanup(patch.stop)

    def publish(self, *, strict=False):
        self.c.tl['project'] = {'gfx_dependency_policy': 'strict'} if strict else {}
        self.directory = gfx_dir(self.c, self.clip)
        self.directory.mkdir(parents=True, exist_ok=True)
        for i in range(3):
            write_png(self.directory / f'f{i:04d}.png', frame=i)
        extra = {}
        if strict:
            from vtx.render.gfx_dependencies import resolve, digest
            extra = dict(dependency_digest=digest(resolve(self.c, self.clip)),
                         dependency_enforcement='fetch-pinned-local-v1',
                         dependency_observed=[(self.root / 'page.html').as_uri()])
        gfx_cli.complete_page(self.directory, 3, self.directory.name, 16, 16,
                              verify_inputs=lambda: None, backend_ns='selected',
                              environment=certificate(self.c), **extra)
        self.marker = self.directory / gfx_cli.COMPLETE_FILE
        return json.loads(self.marker.read_text())

    def consumers(self):
        return [('interval', lambda: frame_source.png_source(self.c, self.clip).require_ready(0, 1)),
                ('bridge', lambda: gfx_cache_reader.resolve_frames(self.c, self.clip))]

    def reject(self):
        for name, consume in self.consumers():
            with self.subTest(consumer=name), self.assertRaises(ValueError):
                consume()

    def test_completion_metadata_shape_is_exact_for_both_consumers(self):
        record = self.publish()
        for _, consume in self.consumers():
            consume()
        variants = [dict(record, **change) for change in (
            {'schema': True}, {'schema': 2}, {'key': 'other'}, {'width': 15},
            {'height': 15}, {'frames': 2}, {'backend_ns': 'other'},
            {'environment': {}}, {'mixed_producer': True}, {'mixed_producer': False},
            {'extra': 'not producer evidence'}, {'sha256': []},
            {'sha256': [record['sha256'][0], None, record['sha256'][2]]},
            {'sha256': [record['sha256'][0], 'not-a-digest', record['sha256'][2]]})]
        variants.extend({k: v for k, v in record.items() if k != field}
                        for field in ('backend_ns', 'environment', 'sha256'))
        for variant in variants:
            with self.subTest(record=variant):
                self.marker.write_text(json.dumps(variant))
                self.reject()

    def test_strict_runtime_evidence_is_required_by_both_consumers(self):
        record = self.publish(strict=True)
        for _, consume in self.consumers():
            consume()
        variants = [dict(record, **change) for change in (
            {'dependency_digest': 'wrong'}, {'dependency_enforcement': 'other'},
            {'dependency_observed': []}, {'dependency_observed': [1]})]
        variants.extend({k: v for k, v in record.items() if k != field}
                        for field in ('dependency_digest', 'dependency_enforcement', 'dependency_observed'))
        for variant in variants:
            with self.subTest(record=variant):
                self.marker.write_text(json.dumps(variant))
                self.reject()

    def test_pending_frames_are_never_adopted_by_read_only_consumers(self):
        self.publish()
        self.marker.unlink()
        gfx_cli.begin_environment_page(self.directory, certificate(self.c),
                                       self.directory.name, 3, 16, 16)
        before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.directory.iterdir()}
        with mock.patch.object(gfx_cli, 'complete_page', side_effect=AssertionError('read-only')):
            self.reject()
        self.assertEqual(before, {p.name: (p.read_bytes(), p.stat().st_mtime_ns)
                                  for p in self.directory.iterdir()})

    def test_producer_inspection_preserves_pending_resume_and_exact_repair(self):
        record = self.publish()
        pending_path = self.directory / gfx_cli.ENV_PENDING_FILE
        matching = dict(environment=certificate(self.c), key=self.directory.name,
                        frames=3, width=16, height=16)
        def inspect():
            return gfx_cli.inspect_page(self.directory, 3, self.directory.name, 16, 16,
                                        backend_ns='selected', environment=certificate(self.c))
        for marker, pending, resume in (
                (None, matching, True), ('{unreadable', matching, True),
                ('null', matching, True), (None, None, False),
                (None, dict(matching, key='other'), False),
                (json.dumps(dict(record, schema=999)), matching, False),
                (json.dumps(dict(record, schema=True)), matching, False)):
            with self.subTest(marker=marker, pending=pending):
                if marker is None:
                    self.marker.unlink(missing_ok=True)
                else:
                    self.marker.write_text(marker)
                if pending is None:
                    pending_path.unlink(missing_ok=True)
                else:
                    pending_path.write_text(json.dumps(pending))
                bad, needs_publication = inspect()
                self.assertTrue(needs_publication)
                self.assertEqual([i for i, _ in bad], [] if resume else [0, 1, 2])
        self.marker.write_text(json.dumps(record))
        write_png(self.directory / 'f0001.png', frame=99)
        self.assertEqual(inspect(), ([(1, '帧内容与完成标记不符')], True))
        record = self.publish(strict=True)
        self.marker.unlink()
        gfx_cli.begin_environment_page(self.directory, certificate(self.c),
                                       self.directory.name, 3, 16, 16)
        bad, needs_publication = gfx_cli.inspect_page(
            self.directory, 3, self.directory.name, 16, 16, backend_ns='selected',
            environment=certificate(self.c), dependency_digest=record['dependency_digest'])
        self.assertEqual(bad, [(i, 'strict dependency enforcement certificate missing') for i in range(3)])
        self.assertTrue(needs_publication)

    def test_interval_does_not_scan_unrequested_frames_or_change_input_bounds(self):
        self.publish(strict=True)
        source = frame_source.png_source(self.c, self.clip)
        (self.directory / 'f0002.png').unlink()
        args = source.input_args(0, 1)
        self.assertEqual(args, ['-reinit_filter', '0', '-framerate', '30', '-start_number', '0',
                                '-i', str(self.directory / 'f%04d.png')])
        with self.assertRaises(ValueError):
            gfx_cache_reader.resolve_frames(self.c, self.clip)
        write_png(self.directory / 'f0000.png', frame=99)
        with self.assertRaisesRegex(ValueError, 'digest changed'):
            source.require_ready(0, 1)

    def test_shared_proof_failure_revokes_scoped_png_facts(self):
        record = self.publish(strict=True)
        source = frame_source.png_source(self.c, self.clip)
        with frame_source.png_consumer_scope([{'gfx': True, 'source': source}]) as layers, \
             mock.patch.object(frame_source, 'png_invalid_reason',
                               wraps=frame_source.png_invalid_reason) as parses:
            bound = layers[0]['source']
            bound.require_ready(0, 1)
            bound.require_ready(0, 1)
            self.assertEqual(parses.call_count, 1)
            self.marker.write_text(json.dumps(dict(record, backend_ns='other')))
            with self.assertRaises(ValueError):
                bound.input_args(0, 1)
            self.assertFalse(bound._png_facts.scope.active)
            self.assertFalse(bound._png_facts.facts)
            self.marker.write_text(json.dumps(record))
            bound.require_ready(0, 1)
            self.assertEqual(parses.call_count, 2)


class LegacyCertificate(unittest.TestCase):
    def setUp(self):
        bind_environment(self)
        self.root = temporary_root(self)
        (self.root / 'page.html').write_text('<html><body>inert</body></html>')
        self.clip = dict(src='page.html', dst_in=0, dur=2)
        self.c = SimpleNamespace(root=self.root, cache=self.root / 'cache',
                                 fps=30, w=16, h=16, tl={'project': {}})
        worker = self.root / 'worker'
        worker.write_text('#!/bin/sh\nexit 1\n')
        worker.chmod(0o700)
        patch = mock.patch.dict(os.environ, VTX_GFX='cdp', VTX_GFX_CACHE_NS='',
                                VTX_CEF_WORKER=str(worker), VTX_CEF_MODE='a0')
        patch.start()
        self.addCleanup(patch.stop)
        self.legacy = self.c.cache / 'page'
        self.legacy.mkdir(parents=True)
        for i in range(2):
            write_png(self.legacy / f'f{i:04d}.png', frame=i)
        gfx_cli.complete_page(self.legacy, 2, gfx_key(self.c, self.clip), 16, 16,
                              verify_inputs=lambda: None, environment=certificate(self.c))

    def reject(self):
        with self.assertRaises(gfx_cache_reader.GfxCacheError):
            gfx_cache_reader.resolve_frames(self.c, self.clip)

    def canonical(self):
        directory = gfx_dir(self.c, self.clip)
        directory.parent.mkdir(parents=True)
        self.legacy.rename(directory)
        self.assertEqual(gfx_cache_reader.resolve_frames(self.c, self.clip),
                         [directory / f'f{i:04d}.png' for i in range(2)])
        return directory

    def test_verified_default_cdp_cli_stem_is_readonly_but_production_stays_closed(self):
        for backend in ('cdp', 'cli'):
            with self.subTest(backend=backend), mock.patch.dict(os.environ, VTX_GFX=backend):
                # CDP and CLI no longer share a renderer identity. A current
                # legacy-layout certificate must identify this selected path.
                gfx_cli.complete_page(self.legacy, 2, gfx_key(self.c, self.clip), 16, 16,
                                      verify_inputs=lambda: None, environment=certificate(self.c))
                directory = gfx_dir(self.c, self.clip)
                self.assertFalse(directory.exists())
                self.assertTrue(gfx_cli.inspect_page(directory, 2, gfx_key(self.c, self.clip), 16, 16)[0])
                with self.assertRaises(ValueError):
                    frame_source.png_source(self.c, self.clip).require_ready()
                before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.legacy.iterdir()}
                with contextlib.ExitStack() as audit:
                    for name in ('vtx.pipeline.infra.Ctx', 'vtx.render.gfx_cli.complete_page',
                                 'vtx.render.gfx_cef.render_page', 'vtx.render.gfx_render.render_page',
                                 'subprocess.Popen', 'vtx.runtime.fstxn.atomic_write',
                                 'vtx.runtime.fstxn.mkdir', 'vtx.runtime.fstxn.unlink', 'vtx.runtime.fstxn.locked_staging',
                                 'pathlib.Path.glob', 'pathlib.Path.rglob', 'pathlib.Path.iterdir'):
                        audit.enter_context(mock.patch(name, side_effect=AssertionError(name)))
                    with self.assertRaisesRegex(gfx_cache_reader.GfxCacheError, 'rerun vtx gfx'):
                        gfx_cache_reader.resolve_frames(self.c, self.clip)
                self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.legacy.iterdir()})

    def test_missing_malformed_stale_wrong_geometry_count_namespace_hash_marker(self):
        directory = self.canonical()
        marker = directory / 'complete.json'
        original = marker.read_text()
        variants = ['{bad', '{}', 'null']
        record = json.loads(original)
        variants += [json.dumps(dict(record, **change)) for change in (
            {'schema': 2}, {'key': 'stale'}, {'width': 15}, {'height': 15},
            {'frames': 3}, {'backend_ns': 'other'}, {'sha256': []},
            {'sha256': ['0' * 64] * 2})]
        marker.unlink()
        self.reject()
        for value in variants:
            with self.subTest(marker=value):
                marker.write_text(value)
                self.reject()
        marker.write_text(original)
        self.assertEqual(gfx_cache_reader.resolve_frames(self.c, self.clip)[0].parent, directory)
        write_png(directory / 'f0000.png', frame=99)
        self.reject()

    def test_stale_html_and_hole_rejected(self):
        directory = self.canonical()
        page = self.root / 'page.html'
        original = page.read_text()
        page.write_text('<html>changed</html>')
        self.reject()
        page.write_text(original)
        self.assertEqual(gfx_cache_reader.resolve_frames(self.c, self.clip)[0].parent, directory)
        (directory / 'f0001.png').unlink()
        write_png(directory / 'f9999.png')
        self.reject()

    def test_existing_broken_canonical_never_hidden(self):
        directory = gfx_dir(self.c, self.clip)
        directory.mkdir(parents=True)
        self.reject()
        directory.rmdir()
        directory.write_text('not a directory')
        self.reject()
        directory.unlink()
        directory.symlink_to(self.root / 'absent', target_is_directory=True)
        self.reject()
        directory.unlink()
        directory.mkdir()
        for i in range(2):
            write_png(directory / f'f{i:04d}.png', frame=i)
        (directory / 'complete.json').write_text('{}')
        self.reject()

    def test_namespace_cef_unknown_never_use_certified_stem(self):
        for backend, namespace in (('cdp', 'other'), ('cli', 'other'), ('cef', ''), ('typo', '')):
            with self.subTest(backend=backend), mock.patch.dict(
                    os.environ, VTX_GFX=backend, VTX_GFX_CACHE_NS=namespace):
                with self.assertRaises(ValueError):
                    gfx_cache_reader.resolve_frames(self.c, self.clip)

if __name__ == '__main__':
    unittest.main()
