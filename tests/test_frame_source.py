"""PNG FrameSource contracts and the exact, one-time cache identity migration."""
import dataclasses
import inspect
import hashlib
import json
import os
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from vtx import composite, frame_source
from vtx.frame_source import PngSequence, png_source


def write_png(path, width=16, height=16, frame=0):
    def chunk(tag, data):
        return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data))
    raw = b''.join(b'\0' + bytes((frame * 7 % 256, y * 11 % 256, 180, frame * 13 % 256)) * width
                   for y in range(height))
    path.write_bytes(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0))
                     + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b''))


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
                             ['-framerate', '30', '-start_number', str(start), *options,
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


class TestMigrationIdentity(unittest.TestCase):
    def tearDown(self):
        composite._graph_code_digest.cache_clear()

    def test_exact_baseline_and_mutation_invalidation(self):
        # Hash the pinned pre-migration source on THIS interpreter. ast.dump
        # itself changed across supported Python versions before this feature.
        fixture = json.loads((Path(__file__).parent / 'fixtures/png_graph_d9ce4aa.json').read_text())
        self.assertEqual(fixture['baseline_commit'], 'd9ce4aab52fec57b770f689550fbbde1a860243b')
        self.assertEqual([m['name'] for m in fixture['methods']], list(composite._SYNTH_FUNCS))
        legacy_source = ''.join(composite._normalized_ast_src(m['source']) for m in fixture['methods'])
        expected = hashlib.sha1(legacy_source.encode('utf-8')).hexdigest()[:12]
        composite._graph_code_digest.cache_clear()
        self.assertEqual(composite._graph_code_digest(), expected)
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
        repo = Path(__file__).resolve().parents[1]
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
        from vtx import subs
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
