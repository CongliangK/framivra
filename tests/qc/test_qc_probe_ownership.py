"""QC concurrent probe ownership; fake-provider race and opt-in real FFmpeg raw output."""
import ast
import contextlib
import io
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
from vtx.inspection import qc
from vtx import composite
from vtx.render.frame_source import PngSequence
from tests.gfx._png import png
from tests.qc._support import _FakeContext, _run_qc


def _production_helper(c, name, **bindings):
    tree = ast.parse(Path(qc.__file__).read_text())
    helper = next(node for node in ast.walk(tree)
                  if isinstance(node, ast.FunctionDef) and node.name == name)
    module = ast.fix_missing_locations(ast.Module(body=[helper], type_ignores=[]))
    env = dict(qc.__dict__, c=c, **bindings)
    exec(compile(module, 'production-' + name, 'exec'), env)
    return env[name]


class ProbeOwnership(unittest.TestCase):
    def context(self):
        root = tempfile.TemporaryDirectory(); self.addCleanup(root.cleanup)
        c = _FakeContext(Path(root.name), enabled=True)
        c.tl['video_tracks'] = [
            {'name': 'base', 'kind': 'aroll', 'clips': [
                {'src': 'a', 'src_in': 0, 'dst_in': 0, 'dur': 24}]},
            *[{'name': 'gfx' + str(i), 'kind': 'gfx', 'clips': [
                {'src': 'page' + str(i) + '.html', 'dst_in': 2, 'dur': 12}]}
              for i in range(2)]]
        return c

    def concurrent(self, real=False):
        c = self.context(); original = c.run
        if real:
            c.w = 64; c.h = 48
            c.crop169 = lambda src: 'crop=64:48'
        barrier = threading.Barrier(2, timeout=10)
        lock = threading.Lock(); paths = []; local = threading.local()
        original_read = Path.read_bytes
        def run(cmd, **kwargs):
            if 'rawvideo' not in cmd:
                return original(cmd, **kwargs)
            target = Path(cmd[-1])
            with lock:
                number = len(paths); paths.append(target)
            if real:
                # Real rawvideo output at the exact production destination;
                # deterministic lavfi replaces only the synthetic provider input.
                args = list(cmd); index = args.index('-i')
                args[index:index + 2] = ['-f', 'lavfi', '-i', 'color=c=gray:s=64x48:r=30']
                args[0] = os.environ['VTX_TEST_FFMPEG']
                subprocess.run(args, check=True, capture_output=True, timeout=10)
                local.expected = None
            else:
                local.expected = bytes([number + 1]) * 1296
                target.write_bytes(local.expected)
            frame = round(float(cmd[cmd.index('-ss') + 1]) * c.fps)
            if str(cmd[cmd.index('-i') + 1]).endswith('final.mp4') and frame == 5:
                barrier.wait()  # Both same-frame outputs exist before either reads.
        def read(path):
            data = original_read(path)
            if path.name.startswith('qc_probe'):
                expected = getattr(local, 'expected', None)
                if expected is not None:
                    self.assertEqual(data, expected, 'probe consumed another call output')
                self.assertEqual(len(data), 1296)
            return data
        c.run = run
        with patch.object(qc.loudness_cache, 'lookup', return_value={'i_lufs': -14, 'tp_dbfs': -2}), \
                patch.object(Path, 'read_bytes', read):
            _, _, report = _run_qc(c)
        self.assertIsNotNone(report)
        self.assertGreaterEqual(len(paths), 4)
        self.assertEqual(len(paths), len(set(paths)))
        self.assertFalse(list(c.cache.glob('qc_probe*')))

    def test_same_frame_parallel_qc_probes_have_independent_outputs(self):
        self.concurrent()

    @unittest.skipUnless(os.environ.get('VTX_TEST_FFMPEG'), 'set VTX_TEST_FFMPEG for real rawvideo output')
    def test_real_ffmpeg_parallel_raw_outputs(self):
        self.concurrent(real=True)
        # Small real PNGs exercise the production layer/filter and crop on both
        # RGB and subsampled output. Upscale is a runtime primitive case, not a
        # claim that the formal schema accepts scale > 1. Lanczos fringe pixels
        # need not have the same bounds as the projected source alpha domain.
        c = self.context(); c.w = 64; c.h = 48
        directory = c.cache / 'geometry'; directory.mkdir()
        pixels = bytes(channel for y in range(27) for x in range(35)
                       for channel in (255, 255, 255, 255 if 4 <= x < 31 and 4 <= y < 23 else 24))
        (directory / 'f0000.png').write_bytes(png(35, 27, rgba=pixels))
        def run(cmd, **kwargs):
            args = [os.environ['VTX_TEST_FFMPEG'], *map(str, cmd[1:])]
            result = subprocess.run(args, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
            return result
        c.run = run
        cropper = _production_helper(c, 'gfx_content_crop', gfx_dir=lambda *_: directory)
        sample = _production_helper(c, 'gray_frame')
        source = PngSequence(directory, 'geometry', 1, 30, 35, 27)
        plan = composite.RenderPlan(c=c, draft=False, f=1, W=c.w, H=c.h)
        for pixel_format in ('bgra', 'yuv420p'):
            for scale, x, y, expected in (
                    (1, 5, 3, '26:18:9:7'), (0.5, 5, 3, '14:10:6:4'),
                    (0.75, -3, -1, '20:16:0:1'), (1.5, 39, 31, '20:12:44:36')):
                with self.subTest(pixel_format=pixel_format, scale=scale, x=x, y=y):
                    clip = dict(dst_in=0, dur=1, scale=scale, x=x, y=y)
                    crop = cropper(clip)
                    self.assertEqual(crop, expected)
                    layer = dict(gfx=True, cl=clip, source=source, x=x, y=y)
                    slave, overlay, label = plan._layer_chain(layer, 0, 'base', 0, 1, 1)
                    output = directory / 'rendered.mkv'
                    run(['ffmpeg', '-v', 'error', '-y', '-filter_complex_threads', '1',
                         '-f', 'lavfi', '-i', f'color=black:s=64x48:r=30,format={pixel_format}',
                         *plan._layer_input_args(layer, 0), '-filter_complex',
                         ';'.join(['[0:v]null[base]', slave, overlay]), '-map', f'[{label}]',
                         '-frames:v', '1', '-c:v', 'ffv1', '-threads', '1',
                         '-pix_fmt', pixel_format, output])
                    whole = run(['ffmpeg', '-v', 'error', '-i', output, '-vf', 'format=gray',
                                 '-frames:v', '1', '-f', 'rawvideo', '-']).stdout
                    cropped = run(['ffmpeg', '-v', 'error', '-i', output, '-vf',
                                   f'crop={crop},format=gray', '-frames:v', '1',
                                   '-f', 'rawvideo', '-']).stdout
                    width, height, left, top = map(int, expected.split(':'))
                    # crop's default chroma alignment is observed on real 4:2:0;
                    # do not bake that alignment into Python placement geometry.
                    if pixel_format == 'yuv420p':
                        left = left // 2 * 2; top = top // 2 * 2
                    self.assertEqual(len(whole), c.w * c.h)
                    expected_pixels = b''.join(whole[row*c.w+left:row*c.w+left+width]
                                               for row in range(top, top + height))
                    self.assertEqual(len(cropped), width * height)
                    self.assertEqual(cropped, expected_pixels)
                    # The authored white interior retains the frame's peak,
                    # without assuming gray range conversion for each format.
                    self.assertEqual(max(cropped), max(whole))
                    self.assertGreater(max(cropped), qc.QC_PIXEL_DIFF)
                    gray = sample(output, 0, pre_crop='crop=' + crop)
                    self.assertTrue(sum(gray) / len(gray) > qc.QC_WINDOW_MEAN_MIN or
                                    sum(v > qc.QC_PIXEL_DIFF for v in gray) / len(gray) > qc.QC_WINDOW_COVER_MIN)

    def test_primary_io_error_survives_cleanup_error(self):
        c = self.context(); original = c.run
        failure = PermissionError('producer failure'); owned = []
        actual_temp = tempfile.TemporaryDirectory
        class Temp:
            def __init__(self, **kw):
                self.inner = actual_temp(**kw); self.name = self.inner.name; owned.append(self)
            def cleanup(self):
                self.inner.cleanup()
                raise PermissionError('cleanup failure')
        def run(cmd, **kw):
            if 'rawvideo' in cmd:
                raise failure
            return original(cmd, **kw)
        c.run = run
        sibling = c.cache / 'qc_probe_sibling'; sibling.mkdir(); (sibling / 'keep').write_bytes(b'keep')
        with patch.object(qc.tempfile, 'TemporaryDirectory', Temp), \
                patch.object(qc.loudness_cache, 'lookup', return_value={'i_lufs': -14, 'tp_dbfs': -2}):
            with self.assertRaises(PermissionError) as caught, contextlib.redirect_stdout(io.StringIO()):
                qc.qc(c)
        self.assertIs(caught.exception, failure)
        self.assertEqual(c.diagnostics, [])
        self.assertTrue(owned)
        self.assertTrue(all(not Path(t.name).exists() for t in owned))
        self.assertEqual((sibling / 'keep').read_bytes(), b'keep')

    def test_cleanup_failure_after_success_is_not_hidden(self):
        c = self.context(); failure = PermissionError('cleanup failure')
        actual_temp = tempfile.TemporaryDirectory
        class Temp:
            def __init__(self, **kw):
                self.inner = actual_temp(**kw); self.name = self.inner.name
            def cleanup(self):
                self.inner.cleanup()
                raise failure
        with patch.object(qc.tempfile, 'TemporaryDirectory', Temp), \
                patch.object(qc.loudness_cache, 'lookup', return_value={'i_lufs': -14, 'tp_dbfs': -2}):
            with self.assertRaises(PermissionError) as caught, contextlib.redirect_stdout(io.StringIO()):
                qc.qc(c)
        self.assertIs(caught.exception, failure)
        self.assertEqual(c.diagnostics, [])
        self.assertFalse(list(c.cache.glob('qc_probe*')))

    def test_direct_helper_inside_ambient_exception(self):
        # Extract the exact nested production helper: executor workers normally
        # have no ambient exception, so whole-QC tests cannot expose this edge.
        for failure_at in ('none', 'producer', 'read', 'shape'):
            with self.subTest(failure_at=failure_at):
                c = self.context()
                primary = PermissionError('producer failure')
                cleanup = PermissionError('cleanup failure')
                actual_temp = tempfile.TemporaryDirectory
                class Temp:
                    def __init__(self, **kw):
                        self.inner = actual_temp(**kw); self.name = self.inner.name
                    def cleanup(self):
                        self.inner.cleanup()
                        raise cleanup
                original = c.run; original_read = Path.read_bytes
                payload = bytes(range(256)) * 5 + bytes(range(16))
                def run(cmd, **kwargs):
                    if failure_at == 'producer':
                        raise primary
                    result = original(cmd, **kwargs)
                    Path(cmd[-1]).write_bytes(b'x' if failure_at == 'shape' else payload)
                    return result
                def read(path):
                    if failure_at == 'read' and path.name == 'qc_probe_frame.raw':
                        raise primary
                    return original_read(path)
                c.run = run
                frame = _production_helper(c, 'gray_frame')
                if failure_at == 'none':
                    c.w = 64; c.h = 48  # Project/crop dimensions do not set the gray sample size.
                    self.assertEqual(frame(c.out / 'final.mp4', 0, 'crop=64:48'), payload)
                    self.assertEqual(c.commands[-1][c.commands[-1].index('-vf') + 1],
                                     'crop=64:48,scale=48:27:flags=bicubic+accurate_rnd,format=gray')
                sibling = c.cache / 'qc_probe_sibling'
                sibling.mkdir(); (sibling / 'keep').write_bytes(b'keep')
                try:
                    raise ValueError('unrelated caller exception')
                except ValueError:
                    with patch.object(qc.tempfile, 'TemporaryDirectory', Temp), \
                            patch.object(Path, 'read_bytes', read):
                        with self.assertRaises(SystemExit if failure_at == 'shape' else PermissionError) as caught:
                            frame(c.out / 'final.mp4', 0)
                if failure_at == 'shape':
                    self.assertEqual(caught.exception.code, 1)
                    self.assertEqual(c.diagnostics[-1]['rule_id'], 'qc.rawvideo_size')
                else:
                    self.assertIs(caught.exception, primary if failure_at != 'none' else cleanup)
                    self.assertEqual(c.diagnostics, [])
                self.assertEqual((sibling / 'keep').read_bytes(), b'keep')
                self.assertEqual(list(c.cache.glob('qc_probe*')), [sibling])

    def test_gfx_bbox_owns_parallel_outputs_and_preserves_legacy_symlink(self):
        c = self.context(); c.w = c.h = 2
        png_dir = c.cache / 'gfx_fixture'; png_dir.mkdir()
        (png_dir / 'f0000.png').write_bytes(
            b'\x89PNG\r\n\x1a\n' + b'\0' * 4 + b'IHDR' + (2).to_bytes(4, 'big') * 2)
        sentinel = c.root / 'sentinel'; sentinel.write_bytes(b'keep')
        legacy = c.cache / 'qc_gfx_bbox.raw'; legacy.symlink_to(sentinel)
        crop = _production_helper(c, 'gfx_content_crop', gfx_dir=lambda *_: png_dir)
        paths = []; barrier = threading.Barrier(2, timeout=5)
        def run(cmd, **kwargs):
            self.assertIs(kwargs.get('need'), False)
            target = Path(cmd[-1]); paths.append(target)
            target.write_bytes(bytes([255]) * 16)
            barrier.wait()
        c.run = run
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(2) as executor:
            results = list(executor.map(lambda _: crop({'dur': 1}), range(2)))
        self.assertEqual(results, ['2:2:0:0'] * 2)
        self.assertEqual(len(set(paths)), 2)
        self.assertTrue(all(not p.parent.exists() for p in paths))
        self.assertEqual(sentinel.read_bytes(), b'keep')
        self.assertTrue(legacy.is_symlink())
        # Non-divisible dimensions use the compositor's even target, while
        # alpha==24 remains outside the source-domain bbox. No scaled raw exists.
        c.w = 64; c.h = 48
        (png_dir / 'f0000.png').write_bytes(
            b'\x89PNG\r\n\x1a\n' + b'\0' * 4 + b'IHDR' +
            (35).to_bytes(4, 'big') + (27).to_bytes(4, 'big'))
        payload = bytes(channel for y in range(27) for x in range(35)
                        for channel in (255, 255, 255, 255 if 4 <= x < 31 and 4 <= y < 23 else 24))
        def source_sized_run(cmd, **kwargs):
            self.assertEqual(cmd[cmd.index('-vf') + 1], 'format=rgba')
            self.assertIs(kwargs.get('need'), False)
            paths.append(Path(cmd[-1])); Path(cmd[-1]).write_bytes(payload)
        c.run = source_sized_run
        for scale, x, y, expected in (
                (1, 0, 0, '26:18:4:4'), (1, 5, 3, '26:18:9:7'),
                (0.5, 5, 3, '14:10:6:4'), (0.75, -3, -1, '20:16:0:1'),
                (1.5, 39, 31, '20:12:44:36'), (0.5, -12, 0, '2:10:0:1'),
                (0.5, 80, 0, None), (0.5, -14, 0, None), (0.01, 0, 0, None),
                (1000000, -4000000, -4000000, '64:48:0:0')):
            with self.subTest(scale=scale, x=x, y=y):
                self.assertEqual(crop(dict(dur=1, scale=scale, x=x, y=y)), expected)
        payload = bytes((255, 255, 255, 24)) * (35 * 27)
        self.assertIsNone(crop(dict(dur=1, scale=0.5)))
        # Failure and short output preserve the existing whole-frame fallback,
        # without consulting an old shared raw file or leaving owned outputs.
        for write_short in (False, True):
            def failed_run(cmd, **kwargs):
                paths.append(Path(cmd[-1]))
                if write_short:
                    Path(cmd[-1]).write_bytes(b'x')
            c.run = failed_run
            self.assertIsNone(crop({'dur': 1}))
        self.assertTrue(all(not p.parent.exists() for p in paths))
        self.assertEqual(sentinel.read_bytes(), b'keep')
        self.assertTrue(legacy.is_symlink())

    def test_temporary_directory_create_failure_propagates(self):
        c = self.context(); failure = PermissionError('create failure')
        with patch.object(qc.tempfile, 'TemporaryDirectory', side_effect=failure), \
                patch.object(qc.loudness_cache, 'lookup', return_value={'i_lufs': -14, 'tp_dbfs': -2}):
            with self.assertRaises(PermissionError) as caught, contextlib.redirect_stdout(io.StringIO()):
                qc.qc(c)
        self.assertIs(caught.exception, failure)
        self.assertEqual(c.diagnostics, [])


if __name__ == '__main__':
    unittest.main()
