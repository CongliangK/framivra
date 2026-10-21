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
from vtx import qc
from tests.qc._support import _FakeContext, _run_qc


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
        tree = ast.parse(Path(qc.__file__).read_text())
        helper = next(node for node in ast.walk(tree)
                      if isinstance(node, ast.FunctionDef) and node.name == 'gray_frame')
        module = ast.fix_missing_locations(ast.Module(body=[helper], type_ignores=[]))
        for producer_fails in (False, True):
            with self.subTest(producer_fails=producer_fails):
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
                if producer_fails:
                    def run(*args, **kwargs):
                        raise primary
                    c.run = run
                env = dict(qc.__dict__, c=c)
                exec(compile(module, 'production-gray-frame', 'exec'), env)
                sibling = c.cache / 'qc_probe_sibling'
                sibling.mkdir(); (sibling / 'keep').write_bytes(b'keep')
                try:
                    raise ValueError('unrelated caller exception')
                except ValueError:
                    with patch.object(qc.tempfile, 'TemporaryDirectory', Temp):
                        with self.assertRaises(PermissionError) as caught:
                            env['gray_frame'](c.out / 'final.mp4', 0)
                self.assertIs(caught.exception, primary if producer_fails else cleanup)
                self.assertEqual((sibling / 'keep').read_bytes(), b'keep')
                self.assertEqual(list(c.cache.glob('qc_probe*')), [sibling])

    def test_gfx_bbox_owns_parallel_outputs_and_preserves_legacy_symlink(self):
        c = self.context(); c.w = c.h = 2
        png_dir = c.cache / 'gfx_fixture'; png_dir.mkdir()
        (png_dir / 'f0000.png').write_bytes(
            b'\x89PNG\r\n\x1a\n' + b'\0' * 4 + b'IHDR' + (2).to_bytes(4, 'big') * 2)
        sentinel = c.root / 'sentinel'; sentinel.write_bytes(b'keep')
        legacy = c.cache / 'qc_gfx_bbox.raw'; legacy.symlink_to(sentinel)
        tree = ast.parse(Path(qc.__file__).read_text())
        helper = next(node for node in ast.walk(tree)
                      if isinstance(node, ast.FunctionDef) and node.name == 'gfx_content_crop')
        module = ast.fix_missing_locations(ast.Module(body=[helper], type_ignores=[]))
        env = dict(qc.__dict__, c=c, gfx_dir=lambda *_: png_dir)
        exec(compile(module, 'production-gfx-bbox', 'exec'), env)
        paths = []; barrier = threading.Barrier(2, timeout=5)
        def run(cmd, **kwargs):
            self.assertIs(kwargs.get('need'), False)
            target = Path(cmd[-1]); paths.append(target)
            target.write_bytes(bytes([255]) * 16)
            barrier.wait()
        c.run = run
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(2) as executor:
            results = list(executor.map(lambda _: env['gfx_content_crop']({'dur': 1}), range(2)))
        self.assertEqual(results, ['2:2:0:0'] * 2)
        self.assertEqual(len(set(paths)), 2)
        self.assertTrue(all(not p.parent.exists() for p in paths))
        self.assertEqual(sentinel.read_bytes(), b'keep')
        self.assertTrue(legacy.is_symlink())
        # Failure and short output preserve the existing whole-frame fallback,
        # without consulting an old shared raw file or leaving owned outputs.
        for write_short in (False, True):
            def failed_run(cmd, **kwargs):
                paths.append(Path(cmd[-1]))
                if write_short:
                    Path(cmd[-1]).write_bytes(b'x')
            c.run = failed_run
            self.assertIsNone(env['gfx_content_crop']({'dur': 1}))
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
