"""PNG FrameSource contracts and the exact, one-time cache identity migration."""
import dataclasses
import inspect
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
        composite._graph_code_digest.cache_clear()
        self.assertEqual(composite._graph_code_digest(), 'e94c2171a336')
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
                    self.assertNotEqual(composite._graph_code_digest(), 'e94c2171a336')

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
    @unittest.skipUnless(os.environ.get('VTX_TEST_FFMPEG') == '1', 'set VTX_TEST_FFMPEG=1')
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
