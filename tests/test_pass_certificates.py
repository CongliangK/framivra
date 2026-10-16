"""#63 final-basename pass certificates: real transactions, fake media processes.

No encoder/decoder is launched. This locks publication and verification plumbing;
real FFV1 pixel/frame acceptance remains a separate integration gate.
"""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

from vtx import fstxn, recovery
from vtx.composite import RenderPlan
from vtx.frame_source import PngSequence
from vtx.infra import Ctx
from tests.test_frame_source import write_png


class PassCertificates(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='vtx-pass-cert-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        warning_state = mock.patch.object(fstxn, '_DEGRADED_NOTICED', False)
        warning_state.start()
        self.addCleanup(warning_state.stop)
        timeline = self.root / 'timeline.json'
        timeline.write_text(json.dumps({'project': {'fps': 30, 'width': 64,
            'height': 48, 'duration_frames': 6}, 'sources': [],
            'video_tracks': [], 'audio_tracks': [], 'subtitles': []}))
        self.c = Ctx(timeline)
        self.plan = RenderPlan(c=self.c, draft=False, f=1., W=64, H=48,
                              nframes=None, nseg=1, layers=[])
        self.layers = [{'cl': {'dst_in': 0, 'dur': 6}, 'gfx': True,
            'source': PngSequence(self.root / name, name, 6, 30, 64, 48),
            'x': 0, 'y': 0, 'w2': None, 'h2': None, 'ti': 0}
            for name in ('a', 'b')]
        for layer in self.layers:
            source = layer['source']
            source.directory.mkdir()
            for frame in range(6):
                write_png(source.frame_path(frame), 64, 48, frame)
        self.out = self.root / 'seg.mp4'
        self.commands = []
        self.payload = b'mock-media-6'
        self.frames = 6
        patcher = mock.patch('subprocess.run', side_effect=self.execute)
        self.process = patcher.start()
        self.addCleanup(patcher.stop)

    def execute(self, cmd, **kwargs):
        self.commands.append(list(cmd))
        if '-filter_complex' in cmd:
            Path(cmd[-1]).write_bytes(self.payload)
            return subprocess.CompletedProcess(cmd, 0, '', '')
        if cmd[0] == 'ffprobe':
            p = Path(cmd[-1])
            ok = p.is_file() and p.read_bytes().startswith(b'mock-media-')
            return subprocess.CompletedProcess(cmd, 0 if ok else 1,
                                               str(self.frames) if ok else '', '')
        if '-xerror' in cmd:
            self.assertTrue(Path(cmd[cmd.index('-i') + 1]).is_file())
            return subprocess.CompletedProcess(cmd, 0, '', '')
        raise AssertionError('Unexpected process: %r' % cmd)

    def render(self, with_subs=False):
        return self.plan._composite_passes(self.layers, self.out, with_subs,
            0, 6, self.root / 'subs.ass' if with_subs else None)

    def intermediate(self):
        paths = list((self.c.cache / 'vseg_pass').glob('p0_*.mkv'))
        self.assertEqual(len(paths), 1)
        return paths[0]

    def inventory(self):
        return {str(p.relative_to(self.root)): (p.read_bytes(), p.stat().st_mtime_ns)
                for p in self.root.rglob('*') if p.is_file()}

    def test_cold_warm_final_regeneration_and_tamper(self):
        with mock.patch.object(recovery, '_inspect', wraps=recovery._inspect) as inspect:
            self.render()
            p = self.intermediate()
            for media in (p, self.out):
                self.assertTrue(recovery._marker(media).is_file())
                record = json.loads(recovery._marker(media).read_text())
                self.assertEqual(record['sha256'], recovery._digest(media))
                self.assertEqual(record['frames'], 6)
            self.assertEqual(inspect.call_count, 3)  # absent pass lookup + two encodes
            self.assertEqual(p.with_suffix('.mkv.n').read_text(), '6')
            self.assertFalse(list(self.root.rglob('*.part*')))
            self.assertEqual(sum('-count_frames' in c for c in self.commands), 2)
            self.assertEqual(sum('-xerror' in c for c in self.commands), 2)
            before = self.inventory()
            with mock.patch('subprocess.run', side_effect=AssertionError('warm process')), \
                 mock.patch.object(recovery, '_inspect', side_effect=AssertionError('warm inspection')):
                self.render()
            self.assertEqual(before, self.inventory())
            self.out.unlink()
            n = inspect.call_count
            self.render()
            self.assertEqual(inspect.call_count - n, 1)
            st = p.stat()
            p.write_bytes(b'evil-media-6')
            os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))
            self.assertEqual(p.stat().st_size, st.st_size)
            self.assertFalse(self.plan._pass_fresh(p, 6, sidecar=True))
            n = inspect.call_count
            self.render()
            self.assertEqual(inspect.call_count - n, 1)
            self.assertTrue(self.plan._pass_fresh(p, 6, sidecar=True))

    def test_main_subtitle_and_qc_keys_remain_separate(self):
        self.render(with_subs=True)
        main = self.intermediate()
        self.out.unlink()
        self.render(with_subs=False)
        passes = list((self.c.cache / 'vseg_pass').glob('p0_*.mkv'))
        self.assertEqual(len(passes), 2)
        for p in passes:
            self.assertTrue(self.plan._pass_fresh(p, 6, sidecar=True))
        encodes = [c for c in self.commands if '-filter_complex' in c]
        self.assertEqual(len(encodes), 4)
        for cmd in (encodes[0], encodes[2]):
            self.assertEqual(cmd[cmd.index('-c:v') + 1], 'ffv1')
            self.assertEqual(cmd[cmd.index('-level') + 1], '3')
            self.assertEqual(cmd[cmd.index('-g') + 1], '1')
            self.assertIn('matroska', cmd)
        self.assertTrue(main.exists())

    def test_first_marker_failure_is_legacy_media_not_a_pass_hit(self):
        real_write = fstxn.atomic_write
        def fail_marker(path, data, **kwargs):
            if str(path).endswith('.complete.json'):
                raise OSError('first marker failure')
            return real_write(path, data, **kwargs)
        with mock.patch.object(fstxn, 'atomic_write', side_effect=fail_marker), \
             self.assertRaises(OSError):
            self.render()
        p = self.intermediate()
        self.assertFalse(recovery._marker(p).exists())
        self.assertFalse(p.with_suffix('.mkv.n').exists())
        self.assertFalse(self.out.exists())
        with mock.patch.object(recovery, '_inspect', wraps=recovery._inspect) as inspect:
            self.assertFalse(self.plan._pass_fresh(p, 6, sidecar=True))
            self.assertEqual(inspect.call_count, 1)
        self.render()
        self.assertTrue(self.plan._pass_fresh(p, 6, sidecar=True))

    def test_marker_and_numeric_sidecar_matrix(self):
        self.render()
        p = self.intermediate()
        marker = recovery._marker(p)
        good = marker.read_text()
        rec = json.loads(good)
        for value in ('{', '[]', json.dumps(dict(rec, schema=2)),
                      json.dumps(dict(rec, sha256='wrong')), json.dumps(dict(rec, frames=5))):
            with self.subTest(marker=value):
                marker.write_text(value)
                with mock.patch.object(recovery, '_inspect', side_effect=AssertionError('invalid marker fallback')):
                    self.assertFalse(self.plan._pass_fresh(p, 6, sidecar=True))
        marker.write_text(good)
        sidecar = p.with_suffix('.mkv.n')
        for value in (None, 'broken', '5'):
            with self.subTest(sidecar=value):
                if value is None:
                    sidecar.unlink()
                else:
                    sidecar.write_text(value)
                self.assertFalse(self.plan._pass_fresh(p, 6, sidecar=True))
        sidecar.write_text('6')
        marker.unlink()
        with mock.patch.object(recovery, '_inspect', wraps=recovery._inspect) as inspect:
            self.assertTrue(self.plan._pass_fresh(p, 6, sidecar=True))
            self.assertEqual(inspect.call_count, 1)  # genuine legacy full validation
        p.unlink()
        self.assertFalse(self.plan._pass_fresh(p, 6, sidecar=True))

    def test_short_encode_fails_exact_pass_length(self):
        self.frames = 5
        with self.assertRaises(SystemExit):
            self.render()
        p = self.intermediate()
        self.assertFalse(p.with_suffix('.mkv.n').exists())
        self.assertFalse(self.out.exists())
        self.assertFalse(self.plan._pass_fresh(p, 6))

    def test_dry_run_has_no_files_and_shows_both_commands(self):
        self.c.dry = True
        before = self.inventory()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.render()
        self.assertEqual(output.getvalue().count('[dry-run]'), 2)
        self.assertEqual(before, self.inventory())
        self.assertEqual(self.commands, [])

    def test_failures_preserve_or_reject_pair_and_allow_retry(self):
        for legacy in (False, True):
            for failure in ('encoder', 'inspect', 'identity', 'media', 'marker', 'sidecar'):
                with self.subTest(legacy=legacy, failure=failure), contextlib.ExitStack() as stack:
                    if legacy:
                        stack.enter_context(mock.patch.object(fstxn, '_fd_write_supported', return_value=False))
                    self.render()
                    p = self.intermediate()
                    marker = recovery._marker(p)
                    old_media, old_marker = p.read_bytes(), marker.read_bytes()
                    # A missing numeric constraint forces a rebuild without invalidating
                    # the existing good media/certificate pair before the transaction.
                    sidecar = p.with_suffix('.mkv.n')
                    sidecar.unlink()
                    self.payload = b'mock-media-new'
                    real_replace = os.replace
                    real_write = fstxn.atomic_write
                    def replace(src, dst, **kwargs):
                        if (failure == 'media' and Path(dst).name == p.name or
                            failure == 'marker' and Path(dst).name == marker.name):
                            raise OSError('injected replacement failure')
                        return real_replace(src, dst, **kwargs)
                    def write(path, data, **kwargs):
                        if failure == 'sidecar' and Path(path) == sidecar:
                            raise OSError('injected numeric sidecar failure')
                        return real_write(path, data, **kwargs)
                    with contextlib.ExitStack() as faults:
                        faults.enter_context(mock.patch('os.replace', side_effect=replace))
                        faults.enter_context(mock.patch.object(fstxn, 'atomic_write', side_effect=write))
                        if failure == 'encoder':
                            faults.enter_context(mock.patch('subprocess.run', return_value=subprocess.CompletedProcess([], 1, '', 'failed')))
                        elif failure == 'inspect':
                            faults.enter_context(mock.patch.object(recovery, '_inspect', return_value=None))
                        elif failure == 'identity':
                            faults.enter_context(mock.patch('vtx.cache_state.verify_input_identity', side_effect=RuntimeError('changed input')))
                        with self.assertRaises((SystemExit, OSError)):
                            self.render()
                    if failure in ('encoder', 'inspect', 'identity', 'media'):
                        self.assertEqual(p.read_bytes(), old_media)
                        self.assertEqual(marker.read_bytes(), old_marker)
                    elif failure == 'marker':
                        self.assertEqual(p.read_bytes(), self.payload)
                        self.assertEqual(marker.read_bytes(), old_marker)
                        self.assertFalse(recovery.media_cache_valid(p, 6))
                    else:
                        self.assertTrue(recovery.media_cache_valid(p, 6))
                    self.assertFalse(sidecar.exists())
                    self.assertFalse(self.plan._pass_fresh(p, 6, sidecar=True))
                    self.assertFalse(list(self.root.rglob('*.tmp')))
                    self.render()
                    self.assertTrue(self.plan._pass_fresh(p, 6, sidecar=True))
                    # Different bytes ensure next marker-failure iteration is a mismatch.
                    self.payload = b'mock-media-6'
                    sidecar.unlink()
                    self.render()

    def test_duplicate_writers_hold_final_lock_through_marker(self):
        for legacy in (False, True):
            with self.subTest(legacy=legacy), contextlib.ExitStack() as stack:
                if legacy:
                    stack.enter_context(mock.patch.object(fstxn, '_fd_write_supported', return_value=False))
                self.render()
                p = self.intermediate()
                p.with_suffix('.mkv.n').unlink()
                entered, release, second_waiting = (threading.Event() for _ in range(3))
                real_write, real_lock = fstxn.atomic_write, fstxn._thread_lock_for
                writes = []
                def write(path, data, **kwargs):
                    if Path(path) == recovery._marker(p):
                        writes.append(path)
                        if len(writes) == 1:
                            entered.set()
                            if not release.wait(5):
                                raise AssertionError('marker barrier timed out')
                    return real_write(path, data, **kwargs)
                def lock(path):
                    if Path(path) == p and entered.is_set():
                        second_waiting.set()
                    return real_lock(path)
                stack.enter_context(mock.patch.object(fstxn, 'atomic_write', side_effect=write))
                stack.enter_context(mock.patch.object(fstxn, '_thread_lock_for', side_effect=lock))
                pool = ThreadPoolExecutor(max_workers=2)
                try:
                    first = pool.submit(self.render)
                    self.assertTrue(entered.wait(5))
                    count = len(self.commands)
                    second = pool.submit(self.render)
                    self.assertTrue(second_waiting.wait(5))
                    self.assertEqual(len(self.commands), count)
                    self.assertFalse(second.done())
                    release.set()
                    first.result(timeout=5)
                    second.result(timeout=5)
                finally:
                    release.set()
                    pool.shutdown(wait=True)
                self.assertEqual(len(writes), 2)
                self.assertTrue(self.plan._pass_fresh(p, 6, sidecar=True))
                self.assertFalse(list(self.root.rglob('*.tmp')))


if __name__ == '__main__':
    unittest.main()
