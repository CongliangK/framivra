"""PNG FrameSource contracts and the exact, one-time cache identity migration."""
import dataclasses
import inspect
import hashlib
import json
import os
import struct
import subprocess
import tempfile
import unittest
import zlib
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tests.gfx._png import write_png

from vtx import composite, frame_source
from vtx.frame_source import PngSequence, png_source




class TestPngSequence(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        self.source = PngSequence(self.path, 'contentkey', 4, 30, 16, 16)

    def test_contract_and_immutable(self):
        self.assertEqual(str(self.source.time_base), '1/30')
        self.assertEqual((self.source.pixel_format, self.source.alpha, self.source.color_space), ('unknown',) * 3)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            self.source.identity = 'changed'

    def test_exact_legacy_arguments_random_seek_repeat_and_end(self):
        for start in (0, 2, 1, 3, 0):
            options = ['-t', '0.100']
            self.assertEqual(self.source.input_args(start, start + 1, options=options, ready=False),
                             ['-reinit_filter', '0', '-framerate', '30', '-start_number', str(start), *options,
                              '-i', str(self.path / 'f%04d.png')])
        for a, b in ((-1, 1), (0, 5), (4, 4), (2, 1)):
            with self.assertRaises(ValueError):
                self.source.input_args(a, b, ready=False)

    def test_ready_rejects_hole_corruption_and_recovers(self):
        for i in range(4):
            write_png(self.path / f'f{i:04d}.png', frame=i)
        self.source.require_ready()
        p = self.path / 'f0001.png'
        valid = p.read_bytes()
        p.unlink()
        write_png(self.path / 'f9999.png')  # equal glob count cannot hide a hole
        with self.assertRaisesRegex(ValueError, 'rerun vtx gfx'):
            self.source.require_ready()
        p.write_bytes(valid[:24])
        with self.assertRaises(ValueError):
            self.source.require_ready()
        p.write_bytes(valid)
        self.source.require_ready()
        write_png(p, width=8)
        with self.assertRaises(ValueError):
            self.source.require_ready()

    def test_prepare_does_not_inspect_frames(self):
        (self.path / 'a.html').write_text('page')
        c = SimpleNamespace(root=self.path, cache=self.path / 'cache', fps=30, w=16, h=16,
                            tl={'video_tracks': [{'kind': 'gfx', 'clips': [{'src': 'a.html', 'dur': 4, 'dst_in': 0}]}]},
                            verify_inputs=lambda stage: None)
        with mock.patch.object(PngSequence, 'require_ready', side_effect=AssertionError('eager IO')), \
             mock.patch.object(composite, 'seg_encoder_kind', return_value='x264'), \
             mock.patch.object(composite, 'run_parallel'):
            layers = composite.prepare_layers(c)
        self.assertEqual(layers[0]['source'], png_source(c, layers[0]['cl']))
        self.assertFalse(layers[0]['source'].directory.exists())

    def test_consumer_fails_before_ffmpeg(self):
        c = SimpleNamespace(fps=30, dry=False)
        plan = composite.RenderPlan(c=c, draft=False, f=1, W=16, H=16)
        layer = {'gfx': True, 'cl': {'dst_in': 0}, 'source': self.source}
        with self.assertRaisesRegex(ValueError, 'not ready'):
            plan._layer_input_args(layer, 0)

    def test_png_converter_is_before_padding_and_video_path_is_unchanged(self):
        c = SimpleNamespace(fps=30, spf=1 / 30)
        plan = composite.RenderPlan(c=c, draft=False, f=1, W=16, H=16)
        gfx = {'gfx': True, 'cl': {'dst_in': 0, 'dur': 4}, 'source': self.source,
               'x': 0, 'y': 0, 'w2': None, 'h2': None}
        slave, _, _ = plan._layer_chain(gfx, 0, 'cv', 0, 4, 1)
        self.assertIn('[1:v]scale=iw:ih:flags=neighbor+bitexact,format=rgba,tpad=', slave)
        self.assertLess(slave.index('format=rgba'), slave.index('tpad='))
        video = dict(gfx, gfx=False, seg=self.path / 'ordinary.mkv')
        self.assertEqual(plan._layer_input_args(video, 0), ['-i', str(video['seg'])])
        video_slave, _, _ = plan._layer_chain(video, 0, 'cv', 0, 4, 1)
        self.assertNotIn('format=rgba', video_slave)
        self.assertNotIn('scale=iw:ih:flags=neighbor+bitexact', video_slave)


class TestMigrationIdentity(unittest.TestCase):
    def tearDown(self):
        composite._graph_code_digest.cache_clear()

    def test_exact_baseline_and_mutation_invalidation(self):
        # Hash the pinned pre-migration source on THIS interpreter. ast.dump
        # itself changed across supported Python versions before this feature.
        fixture = json.loads((Path(__file__).parents[1] / 'fixtures/png_graph_d9ce4aa.json').read_text())
        self.assertEqual(fixture['baseline_commit'], 'd9ce4aab52fec57b770f689550fbbde1a860243b')
        self.assertEqual([m['name'] for m in fixture['methods']], list(composite._SYNTH_FUNCS))
        legacy_source = ''.join(composite._normalized_ast_src(m['source']) for m in fixture['methods'])
        expected = hashlib.sha1(legacy_source.encode('utf-8')).hexdigest()[:12]
        composite._graph_code_digest.cache_clear()
        # Issue #52 extracts pass identity after #68's exact migration. Keep
        # the old mapping intact, but never alias this new implementation.
        self.assertIn(expected, composite._PNG_MIGRATION_DIGESTS.values())
        source = ''.join(composite._normalized_ast_src(inspect.getsource(
            getattr(composite.RenderPlan, name))) for name in composite._SYNTH_FUNCS)
        source += composite._normalized_ast_src(inspect.getsource(frame_source))
        source += composite._normalized_ast_src(inspect.getsource(composite.gfx_cli))
        current = composite._graph_code_digest()
        self.assertEqual(current, hashlib.sha1(source.encode('utf-8')).hexdigest()[:12])
        self.assertNotIn(current, composite._PNG_MIGRATION_DIGESTS.values())
        original = inspect.getsource
        for target, before, after in (
            (frame_source, '"-framerate"', '"-r"'),
            (composite.RenderPlan._layer_input_args, 'max(0, -rel_in)', 'max(1, -rel_in)'),
            (composite.gfx_cli, '_MAX_ZLIB_OUT = 1 << 20', '_MAX_ZLIB_OUT = 1 << 19'),
        ):
            with self.subTest(target=target):
                def changed(obj):
                    text = original(obj)
                    if obj is target:
                        self.assertIn(before, text)
                        return text.replace(before, after, 1)
                    return text
                with mock.patch.object(inspect, 'getsource', side_effect=changed):
                    composite._graph_code_digest.cache_clear()
                    self.assertNotIn(composite._graph_code_digest(), composite._PNG_MIGRATION_DIGESTS.values())
                    self.assertNotEqual(composite._graph_code_digest(), current)

    def test_unknown_ast_form_cannot_use_any_migration_alias(self):
        normalize = composite._normalized_ast_src
        with mock.patch.object(composite, '_normalized_ast_src',
                               side_effect=lambda source: normalize(source) + 'UNKNOWN_AST_FORM'):
            composite._graph_code_digest.cache_clear()
            self.assertNotIn(composite._graph_code_digest(), composite._PNG_MIGRATION_DIGESTS.values())

    def test_identity_ignores_location_but_not_content(self):
        c = SimpleNamespace(gpu=False)
        plan = composite.RenderPlan(c=c, draft=False, f=1, W=16, H=16)
        layer = {'gfx': True, 'source': PngSequence(Path('/one/key'), 'key', 4, 30, 16, 16),
                 'cl': {'dur': 4, 'dst_in': 0}, 'x': 0, 'y': 0, 'w2': None, 'h2': None}
        a = plan._seg_content_key(0, 4, [layer], False, None)
        layer['source'] = dataclasses.replace(layer['source'], directory=Path('/two/key'))
        self.assertEqual(a, plan._seg_content_key(0, 4, [layer], False, None))
        layer['source'] = dataclasses.replace(layer['source'], identity='newkey')
        self.assertNotEqual(a, plan._seg_content_key(0, 4, [layer], False, None))


class TestPngDynamicFormatIntegration(unittest.TestCase):
    """Optional exact filterchain test; skipped unless the pinned ffmpeg is supplied."""
    @staticmethod
    def _write_typed_png(path, width, height, pixel):
        from tests.gfx._png import chunk
        channels = len(pixel)
        rows = b''.join(b'\0' + bytes(pixel) * width for _ in range(height))
        path.write_bytes(b'\x89PNG\r\n\x1a\n'
                         + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8,
                                                       6 if channels == 4 else 2, 0, 0, 0))
                         + chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b''))

    @staticmethod
    def _framehash(ffmpeg, plan, source, count):
        layer = {'gfx': True, 'cl': {'dst_in': 0, 'dur': count}, 'source': source,
                 'x': 0, 'y': 0, 'w2': None, 'h2': None}
        args = plan._layer_input_args(layer, 0)
        slave, _, _ = plan._layer_chain(layer, 0, 'unused', 0, count, 0)
        graph = slave.replace('[L0T]', '[out]')
        cmd = [ffmpeg, '-v', 'error', '-filter_complex_threads', '1', *args,
               '-filter_complex', graph, '-map', '[out]', '-frames:v', str(count),
               '-fps_mode', 'passthrough', '-pix_fmt', 'rgba', '-f', 'framehash',
               '-hash', 'sha256', '-']
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(f'ffmpeg exited {result.returncode}: {result.stderr}')
        tb = None
        frames = []
        for line in result.stdout.splitlines():
            if line.startswith('#tb '):
                tb = Fraction(line.rsplit(':', 1)[1].strip())
            elif line and not line.startswith('#'):
                fields = [field.strip() for field in line.split(',')]
                frames.append((int(fields[2]), fields[-1]))
        if tb is None:
            raise AssertionError(f'framehash omitted time base: {result.stdout}')
        return tb, frames

    def test_framehash_binds_png_input_and_production_filterchain(self):
        with tempfile.TemporaryDirectory(prefix='png_framehash_contract_') as td:
            directory = Path(td)
            for i in range(2):
                self._write_typed_png(directory / f'f{i:04d}.png', 8, 8,
                                      (10 + i, 20, 30, 0 if i == 0 else 1))
            source = PngSequence(directory, 'fixture', 2, 30, 8, 8)
            plan = composite.RenderPlan(c=SimpleNamespace(fps=30, spf=1 / 30, dry=False),
                                        draft=False, f=1, W=8, H=8)
            fake = SimpleNamespace(returncode=0, stderr='', stdout=(
                '#tb 0: 1/60\n'
                '0, 0, 0, 2, 2048, ' + 'a' * 64 + '\n'
                '0, 2, 2, 2, 2048, ' + 'b' * 64 + '\n'))
            with mock.patch.object(subprocess, 'run', return_value=fake) as run:
                tb, frames = self._framehash('/pinned/ffmpeg', plan, source, 2)
            self.assertEqual(tb, Fraction(1, 60))
            self.assertEqual(frames, [(0, 'a' * 64), (2, 'b' * 64)])
            argv = run.call_args.args[0]
            self.assertEqual(argv[argv.index('-reinit_filter') + 1], '0')
            self.assertIn('-fps_mode', argv)
            self.assertIn('passthrough', argv)
            graph = argv[argv.index('-filter_complex') + 1]
            self.assertIn('[0:v]scale=iw:ih:flags=neighbor+bitexact,format=rgba,tpad=', graph)

    @unittest.skipUnless(os.environ.get('VTX_TEST_FFMPEG'),
                         'set VTX_TEST_FFMPEG to pinned ffmpeg executable')
    def test_mixed_rgb_rgba_chain_matches_normalized_rgba_oracle(self):
        count, width, height = 10, 8, 8
        with tempfile.TemporaryDirectory(prefix='png_dynamic_format_') as td:
            root = Path(td)
            mixed, oracle = root / 'mixed', root / 'oracle'
            mixed.mkdir()
            oracle.mkdir()
            for i in range(count):
                rgb = (19 + i * 7, 73 + i * 3, 141 + i * 5)
                rgba = (*rgb, 0 if i == 1 else 1 if i == 5 else 96 + i)
                pixel = rgb if i % 2 == 0 else rgba
                self._write_typed_png(mixed / f'f{i:04d}.png', width, height, pixel)
                full_rgba = (*pixel, 255) if len(pixel) == 3 else pixel
                self._write_typed_png(oracle / f'f{i:04d}.png', width, height, full_rgba)
            mixed_source = PngSequence(mixed, 'mixed', count, 30, width, height)
            oracle_source = PngSequence(oracle, 'oracle', count, 30, width, height)
            plan = composite.RenderPlan(c=SimpleNamespace(fps=30, spf=1 / 30, dry=False),
                                        draft=False, f=1, W=width, H=height)
            mixed_tb, mixed_frames = self._framehash(os.environ['VTX_TEST_FFMPEG'],
                                                      plan, mixed_source, count)
            oracle_tb, oracle_frames = self._framehash(os.environ['VTX_TEST_FFMPEG'],
                                                        plan, oracle_source, count)
            self.assertEqual(len(mixed_frames), count)
            self.assertEqual(len(oracle_frames), count)
            self.assertEqual([Fraction(pts) * mixed_tb for pts, _ in mixed_frames],
                             [Fraction(i, 30) for i in range(count)])
            self.assertEqual([Fraction(pts) * oracle_tb for pts, _ in oracle_frames],
                             [Fraction(i, 30) for i in range(count)])
            expected_hashes = []
            for i in range(count):
                rgb = (19 + i * 7, 73 + i * 3, 141 + i * 5)
                pixel = rgb if i % 2 == 0 else (*rgb, 0 if i == 1 else 1 if i == 5 else 96 + i)
                full_rgba = (*pixel, 255) if len(pixel) == 3 else pixel
                expected_hashes.append(hashlib.sha256(bytes(full_rgba) * (width * height)).hexdigest())
            self.assertEqual([digest for _, digest in mixed_frames], expected_hashes)
            self.assertEqual([digest for _, digest in oracle_frames], expected_hashes)
            self.assertEqual(mixed_frames, oracle_frames)


class TestGeneratedPngConsumerIntegration(unittest.TestCase):
    """CPU consumer coverage; generated PNGs deliberately need no browser."""
    @unittest.skipUnless(os.environ.get('VTX_TEST_FFMPEG'), 'set VTX_TEST_FFMPEG to ffmpeg executable')
    def test_full_warm_draft_range_and_missing_frame_failure(self):
        import json
        import os
        import subprocess
        import sys
        import math
        import wave
        from vtx.infra import Ctx
        repo = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory(prefix='framesource_cli_') as td:
            root = Path(td)
            (root / 'page.html').write_text('<html>generated PNG fixture</html>')
            with wave.open(str(root / 'tone.wav'), 'wb') as wav:
                wav.setparams((1, 2, 48000, 0, 'NONE', 'not compressed'))
                wav.writeframes(b''.join(struct.pack('<h', int(4000 * math.sin(i * 2 * math.pi * 440 / 48000)))
                                          for i in range(48000 * 4)))
            tl = root / 'timeline.json'
            tl.write_text(json.dumps(dict(version=2,
                project=dict(fps=30, width=64, height=64, duration_frames=120),
                sources=[dict(id='tone', path='tone.wav')], subtitles=[],
                audio_tracks=[dict(name='voice', events=[{'src': 'tone', 'in': 0, 'out': 120, 'volume': 1}])],
                video_tracks=[dict(name='gfx', kind='gfx', clips=[
                    dict(src='page.html', dst_in=i, dur=18, fade_in_frames=3, fade_out_frames=4)
                    for i in (0, 60)])])))
            c = Ctx(tl)
            source = png_source(c, c.tl['video_tracks'][0]['clips'][0])
            source.directory.mkdir(parents=True)
            for i in range(18):
                write_png(source.frame_path(i), 64, 64, i)
            env = dict(os.environ, VTX_NO_NVENC='1', VTX_SEG_NVENC='0', VTX_VIDEO_SEGMENTS='2')
            def run(*args, ok=True):
                result = subprocess.run([sys.executable, str(repo / 'vtx/vtx.py'), '-t', str(tl), *args],
                                        capture_output=True, text=True, env=env)
                self.assertEqual(result.returncode == 0, ok, result.stdout + result.stderr)
                return result
            run('gfx')  # Certify generated fixture cache; no browser rendering.
            run('video')
            warm = run('video')
            self.assertIn('已缓存', warm.stdout)
            run('video', '--draft', '--frames', '12')
            receipt = (root / 'out/receipt.json').read_bytes()
            run('video', '--draft', '--range', '8f', '19f')
            self.assertEqual(receipt, (root / 'out/receipt.json').read_bytes())
            self.assertTrue((root / 'cache/preview/range_0.267_0.633.mp4').is_file())
            source.frame_path(3).unlink()
            failure = run('video', '--draft', '--range', '0f', '10f', ok=False)
            self.assertIn('gfx', failure.stdout)
            self.assertEqual(receipt, (root / 'out/receipt.json').read_bytes())


class TestSubtitleReadiness(unittest.TestCase):
    def test_preview_checks_only_requested_frame_and_full_consumer_still_fails(self):
        import contextlib
        import io
        from tests._support import tree_sha256
        from vtx import recovery, subs
        from vtx.hygiene import seg_key
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = PngSequence(root, 'partial', 24, 30, 16, 16)
            write_png(source.frame_path(0))
            c = SimpleNamespace(fps=30, w=16, h=16, dry=False,
                                tl={'video_tracks': [{'kind': 'gfx', 'clips': [
                                    {'src': 'page.html', 'dst_in': 0, 'dur': 24}]}]})
            with mock.patch.object(subs, 'png_source', return_value=source), \
                 mock.patch.object(subs, 'seg_encoder_kind', return_value='x264'):
                layers = subs._layer_ready(c, 0)
                self.assertIsNotNone(layers)
                plan = composite.RenderPlan(c=c, draft=False, f=1, W=16, H=16)
                self.assertEqual(plan._layer_input_args(layers[0], 0),
                                 source.input_args(0, 1))
                full_layer = dict(layers[0])
                full_layer.pop('source_stop')
                with self.assertRaisesRegex(ValueError, 'f0001.png'):
                    plan._layer_input_args(full_layer, 0)
                source.frame_path(0).unlink()
                self.assertIsNone(subs._layer_ready(c, 0))

        # Owned single-file caches use the producer's shared certificate reader;
        # these synthetic bytes test selection, not media decoding/publication.
        states = ('missing_marker', 'malformed_marker', 'nonobject_marker',
                  'wrong_schema', 'changed_media', 'wrong_frames', 'missing_media',
                  'short', 'valid')
        for owner, alpha in (('seg', False), ('seg', True), ('qc_clean', False)):
            for state in states:
                with self.subTest(owner=owner, alpha=alpha, state=state), \
                        tempfile.TemporaryDirectory() as td:
                    root = Path(td)
                    cache = root / 'cache'
                    (cache / 'seg').mkdir(parents=True)
                    original = root / 'original.mp4'
                    original.write_bytes(b'user input has no completion certificate')
                    clip = dict(src='source', src_in=0, src_out=24, dur=24, dst_in=0)
                    if alpha:
                        clip['opacity'] = .5
                    c = SimpleNamespace(root=root, cache=cache, fps=30, spf=1/30,
                                        dur=24, w=16, h=16, dry=False, run=mock.Mock(),
                                        src_path=lambda sid: original,
                                        tl={'video_tracks': [{'kind': 'aroll', 'clips': [clip]}]})
                    key = seg_key(c, clip, 0, 0, False, alpha, 'prores' if alpha else 'x264')
                    seg = cache / 'seg' / (key + ('.mov' if alpha else '.mp4'))
                    qc = cache / 'qc_clean.mp4'
                    target = seg if owner == 'seg' else qc
                    for media in {seg, target}:
                        media.write_bytes(b'completed fixture media')
                        recovery.completion_marker(media).write_text(json.dumps({
                            'schema': 1, 'sha256': hashlib.sha256(media.read_bytes()).hexdigest(),
                            'frames': 24}))
                    marker = recovery.completion_marker(target)
                    record = json.loads(marker.read_text())
                    if state == 'missing_marker':
                        marker.unlink()
                    elif state == 'malformed_marker':
                        marker.write_text('{broken')
                    elif state == 'nonobject_marker':
                        marker.write_text('[]')
                    elif state in ('wrong_schema', 'wrong_frames', 'short'):
                        record['schema' if state == 'wrong_schema' else 'frames'] = (
                            2 if state == 'wrong_schema' else 25 if state == 'wrong_frames' else 12)
                        marker.write_text(json.dumps(record))
                    elif state == 'changed_media':
                        target.write_bytes(b'interrupted replacement')
                    elif state == 'missing_media':
                        target.unlink()
                    valid = state == 'valid' or (owner == 'seg' and state == 'short')
                    expected = ('cache/qc_clean.mp4' if owner == 'qc_clean' and valid else
                                '图层单帧' if owner == 'qc_clean' or valid else '兜底')
                    before = tree_sha256(root)
                    with mock.patch.object(subs, 'seg_encoder_kind', return_value='x264'), \
                            mock.patch.object(composite.RenderPlan, 'composite') as render, \
                            mock.patch.object(recovery, '_inspect', side_effect=AssertionError('read-only cache')), \
                            contextlib.redirect_stdout(io.StringIO()) as output:
                        for dry in (True, False):
                            c.run.reset_mock()
                            render.reset_mock()
                            path, description = subs._bg_frame(c, 18, root, dry)
                            self.assertEqual(path, root / 'bg.png')
                            self.assertIn(expected, description)
                            self.assertEqual(c.run.call_count, 0 if dry else 1)
                            self.assertEqual(render.call_count, int(not dry and expected == '图层单帧'))
                            if not dry and expected == 'cache/qc_clean.mp4':
                                self.assertIn(str(qc), c.run.call_args.args[0])
                        self.assertEqual(tree_sha256(root), before)  # No legacy adoption/cleanup.
                        if expected == '兜底':
                            self.assertIn('未通过完成校验', output.getvalue())
                        if owner == 'qc_clean' and valid:
                            c.run.side_effect = RuntimeError('decoder failed')
                            with self.assertRaisesRegex(RuntimeError, 'decoder failed'):
                                subs._bg_frame(c, 18, root, False)

    @unittest.skipUnless(os.environ.get('VTX_TEST_FFMPEG'), 'set VTX_TEST_FFMPEG to ffmpeg executable')
    def test_partial_preview_renders_and_missing_current_uses_black_fallback(self):
        import subprocess
        from vtx import subs
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            sequence = root / 'partial'
            sequence.mkdir()
            source = PngSequence(sequence, 'partial', 24, 30, 16, 16)
            write_png(source.frame_path(0), frame=10)
            c = SimpleNamespace(cache=root / 'cache', fps=30, spf=1/30, dur=24,
                                w=16, h=16, dry=False, gpu=False,
                                run=lambda cmd: subprocess.run(cmd, check=True, capture_output=True),
                                tl={'video_tracks': [{'kind': 'gfx', 'clips': [
                                    {'src': 'page.html', 'dst_in': 0, 'dur': 24}]}]})
            with mock.patch.object(subs, 'png_source', return_value=source), \
                 mock.patch.object(subs, 'seg_encoder_kind', return_value='x264'):
                preview = root / 'preview'
                preview.mkdir()
                path, description = subs._bg_frame(c, 0, preview, False)
                self.assertTrue(path.is_file())
                self.assertIn('图层单帧', description)
                source.frame_path(0).unlink()
                path, description = subs._bg_frame(c, 0, preview, False)
                self.assertTrue(path.is_file())
                self.assertIn('兜底', description)
                rgba = subprocess.check_output(['ffmpeg', '-v', 'error', '-i', str(path),
                                                '-f', 'rawvideo', '-pix_fmt', 'rgba', '-'])
                self.assertEqual(rgba, b'\0\0\0\xff' * (16 * 16))
