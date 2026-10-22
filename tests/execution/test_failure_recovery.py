"""#34: interrupted single-file media and browser cache recovery, isolated fixtures."""
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import threading
import unittest
from unittest import mock
import wave

try:
    import fcntl
except ImportError:   # 非 POSIX 平台无跨进程 flock
    fcntl = None

from vtx import recovery, stages
from vtx.infra import Ctx, run_ffmpeg_task


def wav(path, samples=480):
    with wave.open(str(path), 'wb') as stream:
        stream.setnchannels(2)
        stream.setsampwidth(2)
        stream.setframerate(48000)
        stream.writeframes(b'\0' * samples * 4)


def context(root):
    p = root / 'timeline.json'
    p.write_text(json.dumps({'project': {'fps': 30, 'width': 64, 'height': 48,
                                       'duration_frames': 30},
                             'sources': [], 'video_tracks': [],
                             'audio_tracks': [], 'subtitles': []}))
    return Ctx(p)


# issue #155 确定性交错的两进程写者脚本：first 在 marker 临时文件真实驻留磁盘
# 期间放行 second；second 先探测目标锁（非阻塞 flock），再完整跑一次同目标
# atomic_ffmpeg。修复后 first 持目标锁发布 marker → second 探测阻塞并等待；
# 修复前 second 可整体完成事务并把 first 的 marker 临时文件当 orphan 删除 →
# first 的 os.replace 抛 FileNotFoundError（issue #155 的原始异常签名）。
_DUPLICATE_WRITER_SOURCE = '''\
import fcntl, json, os, secrets, subprocess, sys, time, wave
from pathlib import Path
root = Path(sys.argv[1]); role = sys.argv[2]
sig = root / 'barrier'
target = root / 'cache' / 'audio' / 'test.wav'
tl_path = root / (role + '-timeline.json')
tl_path.write_text(json.dumps({'project': {'fps': 30, 'width': 64,
                                           'height': 48, 'duration_frames': 30},
                               'sources': [], 'video_tracks': [],
                               'audio_tracks': [], 'subtitles': []}))
from vtx import recovery
from vtx.infra import Ctx
c = Ctx(tl_path)
cmd = ['ffmpeg', '-y', str(target)]

def encode(argv):
    with wave.open(str(argv[-1]), 'wb') as stream:
        stream.setnchannels(2); stream.setsampwidth(2); stream.setframerate(48000)
        stream.writeframes(b'\\0' * 480 * 4)
    return subprocess.CompletedProcess(argv, 0, '', '')

def wait_either(a, b, timeout):
    deadline = time.time() + timeout
    while time.time() < deadline and not a.exists() and not b.exists():
        time.sleep(0.02)
    return a.exists() or b.exists()

if role == 'first':
    def slow_marker(path, data, *, root=None):
        payload = data if isinstance(data, bytes) else str(data).encode('utf-8')
        tmp = path.parent / ('.' + path.name + '.' + secrets.token_hex(16) + '.tmp')
        with open(tmp, 'wb') as stream:
            stream.write(payload)
        (sig / 'a-marker-tmp').write_text(tmp.name)
        wait_either(sig / 'b-finished', sig / 'b-blocked', 20)
        (sig / 'a-tmp-survived').write_text('1' if tmp.exists() else '0')
        os.replace(tmp, path)  # 修复前：tmp 已被第二写者删除 → FileNotFoundError
    recovery.fstxn.atomic_write = slow_marker
    recovery.atomic_ffmpeg(c, cmd, encode)
    print('FIRST_OK')
else:
    deadline = time.time() + 20
    while time.time() < deadline and not (sig / 'a-marker-tmp').exists():
        time.sleep(0.02)
    if not (sig / 'a-marker-tmp').exists():
        sys.exit(3)
    lockfile = target.parent / (target.name + '.lock')
    fd = os.open(lockfile, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        (sig / 'b-blocked').write_text('1')
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
    recovery.atomic_ffmpeg(c, cmd, encode)
    (sig / 'b-finished').write_text('1')
    print('SECOND_OK')
'''


class MediaRecovery(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='vtx-recovery-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.c = context(self.root)
        self.target = self.root / 'cache' / 'audio' / 'test.wav'
        self.target.parent.mkdir(parents=True)
        self.cmd = ['ffmpeg', '-y', str(self.target)]

    def encode(self, cmd):
        wav(Path(cmd[-1]))
        return subprocess.CompletedProcess(cmd, 0, '', '')

    def test_success_publishes_verified_media_and_warm_hit(self):
        recovery.atomic_ffmpeg(self.c, self.cmd, self.encode)
        self.assertTrue(self.target.exists())
        self.assertTrue(recovery.completion_marker(self.target).exists())
        with mock.patch.object(recovery, '_inspect', side_effect=AssertionError('warm decode')):
            self.assertTrue(recovery.media_cache_valid(self.target))
        self.target.unlink()
        for has_marker in (True, False):
            if not has_marker:
                recovery.completion_marker(self.target).unlink()
            with self.subTest(missing_target=True, has_marker=has_marker), mock.patch.object(
                    recovery.subprocess, 'run', side_effect=AssertionError('missing media must not probe')):
                self.assertFalse(recovery.media_cache_valid(self.target))

        # Preview segments use the same certificate/frame obligation, not a
        # container header that can survive an interrupted encoder.
        from vtx import range as ranges

        class StopAfterCacheCheck(Exception):
            pass

        for state in ('missing_marker', 'malformed_marker', 'changed_media',
                      'wrong_frames', 'missing_media', 'valid'):
            with self.subTest(preview=state), tempfile.TemporaryDirectory() as d:
                c = context(Path(d))
                directory = c.cache / 'preview'
                directory.mkdir(parents=True)
                plan = ranges.RenderPlan(c, True, .5, 32, 24)
                subtitle = directory / 'subs_000000_000030.ass'
                plan.write_seg_ass(subtitle, 0, 30)
                key = plan._seg_content_key(0, 30, [], True, subtitle)
                target = directory / f'rseg_000000_000030_{key}.mp4'

                def inspect(path, kind):
                    return {'schema': 1, 'sha256': recovery._digest(path), 'frames': 30}

                # Only publication's media inspection is stubbed. The cache
                # decision below reads the real certificate and media bytes.
                with mock.patch.object(recovery, '_inspect', side_effect=inspect):
                    recovery.atomic_media(c, target, lambda path: path.write_bytes(b'mock mp4'))
                marker = recovery.completion_marker(target)
                if state == 'missing_marker':
                    marker.unlink()
                elif state == 'malformed_marker':
                    marker.write_text('{broken')
                elif state == 'changed_media':
                    target.write_bytes(b'changed mp4')
                elif state == 'wrong_frames':
                    record = json.loads(marker.read_text())
                    record['frames'] = 29
                    marker.write_text(json.dumps(record))
                elif state == 'missing_media':
                    target.unlink()
                with mock.patch.object(ranges, 'prepare_layers', return_value=[]), \
                        mock.patch.object(ranges.RenderPlan, 'composite') as composite, \
                        mock.patch.object(ranges, 'pick_vcodec', side_effect=StopAfterCacheCheck), \
                        mock.patch.object(subprocess, 'run', side_effect=AssertionError('warm probe')) as probe, \
                        contextlib.redirect_stdout(io.StringIO()):
                    with self.assertRaises(StopAfterCacheCheck):
                        ranges.run_range(c, 0, 30)
                self.assertEqual(composite.call_count, 0 if state == 'valid' else 1)
                probe.assert_not_called()

    def test_interruption_preserves_good_target_and_no_new_marker(self):
        wav(self.target)
        old = self.target.read_bytes()
        def interrupted(cmd):
            Path(cmd[-1]).write_bytes(b'half WAV')
            raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            recovery.atomic_ffmpeg(self.c, self.cmd, interrupted)
        self.assertEqual(self.target.read_bytes(), old)
        self.assertFalse(recovery.completion_marker(self.target).exists())
        self.assertEqual(list(self.target.parent.glob('.*.tmp')), [])

    def test_success_exit_with_invalid_media_cannot_publish(self):
        def invalid(cmd):
            Path(cmd[-1]).write_bytes(b'truncated')
            return subprocess.CompletedProcess(cmd, 0, '', '')
        with self.assertRaises(RuntimeError):
            recovery.atomic_ffmpeg(self.c, self.cmd, invalid)
        self.assertFalse(self.target.exists())
        self.assertFalse(recovery.completion_marker(self.target).exists())

    def test_changed_input_blocks_publication(self):
        def mutate(cmd):
            result = self.encode(cmd)
            self.c.tl_path.write_bytes(self.c.tl_path.read_bytes() + b' ')
            return result
        with self.assertRaises(RuntimeError):
            recovery.atomic_ffmpeg(self.c, self.cmd, mutate)
        self.assertFalse(self.target.exists())

    def test_orphan_retired_only_for_its_target_under_lock(self):
        orphan = self.target.parent / ('.' + self.target.name + '.killed.tmp')
        other = self.target.parent / '.other.wav.killed.tmp'
        orphan.write_bytes(b'partial')
        other.write_bytes(b'other')
        recovery.atomic_ffmpeg(self.c, self.cmd, self.encode)
        self.assertFalse(orphan.exists())
        self.assertTrue(other.exists())

    def test_marker_published_after_media_while_target_lock_held(self):
        original = recovery.fstxn.atomic_write
        def checked(path, data, *, root=None):
            self.assertTrue(self.target.exists())
            self.assertTrue(recovery.fstxn._thread_lock_for(self.target).locked())
            self.assertEqual(json.loads(data)['sha256'], recovery._digest(self.target))
            return original(path, data, root=root)
        with mock.patch.object(recovery.fstxn, 'atomic_write', checked):
            recovery.atomic_ffmpeg(self.c, self.cmd, self.encode)

    def test_legacy_marker_publication_retains_target_lock(self):
        with mock.patch.object(recovery.fstxn, '_legacy_guard', return_value=True):
            self.test_marker_published_after_media_while_target_lock_held()

    def test_marker_failure_releases_target_lock_for_retry(self):
        with mock.patch.object(recovery.fstxn, 'atomic_write', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                recovery.atomic_ffmpeg(self.c, self.cmd, self.encode)
        self.assertFalse(recovery.fstxn._thread_lock_for(self.target).locked())
        recovery.atomic_ffmpeg(self.c, self.cmd, self.encode)
        self.assertTrue(recovery.media_cache_valid(self.target))

    def test_orphan_cleanup_preserves_companion_temporary(self):
        companion = self.target.parent / ('.' + self.target.name + '.complete.json.active.tmp')
        companion.write_bytes(b'active companion')
        recovery.atomic_ffmpeg(self.c, self.cmd, self.encode)
        self.assertEqual(companion.read_bytes(), b'active companion')

    def test_orphan_target_names_are_literal_in_both_platform_paths(self):
        for legacy in (False, True):
            for name, other_name in [('a[1].wav', 'a1.wav'),
                                     ('a*.wav', 'ab.wav'),
                                     ('a?.wav', 'ab.wav'),
                                     ('plain.wav', 'other.wav')]:
                with self.subTest(legacy=legacy, name=name):
                    target = self.target.parent / name
                    own = target.parent / ('.' + name + '.12345678.tmp')
                    other = target.parent / ('.' + other_name + '.12345678.tmp')
                    companion = target.parent / ('.' + name + '.complete.json.active.tmp')
                    own.write_bytes(b'owned orphan')
                    other.write_bytes(b'other active writer')
                    companion.write_bytes(b'active companion')
                    mode = (mock.patch.object(recovery.fstxn, '_legacy_guard', return_value=True)
                            if legacy else contextlib.nullcontext())
                    with mode:
                        recovery.atomic_ffmpeg(self.c, ['ffmpeg', '-y', str(target)], self.encode)
                    self.assertFalse(own.exists())
                    self.assertEqual(other.read_bytes(), b'other active writer')
                    self.assertEqual(companion.read_bytes(), b'active companion')
                    self.assertTrue(recovery.media_cache_valid(target))

    def test_same_target_writer_cannot_enter_during_marker_publication(self):
        marker_entered = threading.Event()
        second_lock_attempt = threading.Event()
        second_encoded = threading.Event()
        original_write = recovery.fstxn.atomic_write
        original_lock = recovery.fstxn._thread_lock_for
        errors = []
        def lock(path):
            if threading.current_thread().name == 'second' and Path(path) == self.target:
                second_lock_attempt.set()
            return original_lock(path)
        def write(path, data, *, root=None):
            if threading.current_thread().name == 'first':
                marker_entered.set()
                if not second_lock_attempt.wait(5):
                    raise RuntimeError('second writer never attempted target lock')
                self.assertTrue(original_lock(self.target).locked())
                self.assertFalse(second_encoded.is_set())
            return original_write(path, data, root=root)
        def encode(argv):
            if threading.current_thread().name == 'second':
                second_encoded.set()
            return self.encode(argv)
        def worker():
            try:
                recovery.atomic_ffmpeg(self.c, self.cmd, encode)
            except BaseException as exc:
                errors.append(exc)
        with mock.patch.object(recovery.fstxn, 'atomic_write', write), \
                mock.patch.object(recovery.fstxn, '_thread_lock_for', lock):
            first = threading.Thread(target=worker, name='first')
            second = threading.Thread(target=worker, name='second')
            first.start()
            self.assertTrue(marker_entered.wait(5))
            second.start()
            first.join(10)
            second.join(10)
        self.assertFalse(first.is_alive() or second.is_alive())
        self.assertEqual(errors, [])
        self.assertTrue(second_encoded.is_set())
        self.assertTrue(recovery.media_cache_valid(self.target))
        self.assertEqual(list(self.target.parent.glob('.*.tmp')), [])

    def test_cross_process_duplicate_writer_cannot_retire_inflight_marker(self):
        # issue #155：两个真实进程对同一内容键目标并发编码，第二写者的 orphan
        # 清扫不得删除第一写者尚在写入的 marker 临时文件。屏障只固定合法交错
        # （首写者 marker 临时文件驻留磁盘期间放行第二写者），不改产品源代码。
        if fcntl is None:
            self.skipTest('fcntl unavailable')
        barrier = self.root / 'barrier'
        barrier.mkdir()
        script = self.root / 'duplicate_writer.py'
        script.write_text(_DUPLICATE_WRITER_SOURCE)
        env = dict(os.environ,
                   PYTHONPATH=str(Path(recovery.__file__).resolve().parent.parent))
        first = subprocess.Popen([sys.executable, str(script), str(self.root), 'first'],
                                 env=env, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True)
        second = subprocess.Popen([sys.executable, str(script), str(self.root), 'second'],
                                  env=env, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True)
        try:
            out1, err1 = first.communicate(timeout=120)
            out2, err2 = second.communicate(timeout=120)
        finally:
            for proc in (first, second):
                if proc.poll() is None:
                    proc.kill()
        self.assertEqual(first.returncode, 0, err1)
        self.assertEqual(second.returncode, 0, err2)
        self.assertIn('FIRST_OK', out1)
        self.assertIn('SECOND_OK', out2)
        self.assertEqual((barrier / 'a-tmp-survived').read_text(), '1')
        self.assertTrue((barrier / 'b-blocked').exists(),
                        'marker 发布期间第二写者必须被目标锁排除')
        self.assertTrue(recovery.media_cache_valid(self.target))
        self.assertEqual(list(self.target.parent.glob('.*.tmp')), [])

    def test_legacy_and_digest_mismatch_require_actual_complete_wav(self):
        wav(self.target)
        original = self.target.read_bytes()
        with mock.patch.object(recovery, '_inspect', side_effect=AssertionError('legacy adoption')):
            self.assertFalse(recovery.media_cache_valid(self.target))
        self.assertEqual(self.target.read_bytes(), original)
        recovery.atomic_ffmpeg(self.c, self.cmd, self.encode)
        self.target.write_bytes(self.target.read_bytes()[:-4])
        self.assertFalse(recovery.media_cache_valid(self.target))

    def test_prepare_rebuilds_only_bad_extraction(self):
        src = self.root / 'source.wav'
        wav(src)
        tl = json.loads(self.c.tl_path.read_text())
        tl['sources'] = [{'id': 's', 'path': 'source.wav'}]
        self.c.tl_path.write_text(json.dumps(tl))
        self.c = Ctx(self.c.tl_path)
        out, cmd = stages.source_audio_extraction(self.c, 's')
        self.assertIsNotNone(cmd)
        wav(out)
        self.assertIsNotNone(stages.source_audio_extraction(self.c, 's')[1])
        recovery.atomic_ffmpeg(self.c, cmd, self.encode)
        self.assertIsNone(stages.source_audio_extraction(self.c, 's')[1])
        out.write_bytes(out.read_bytes()[:-4])
        self.assertIsNotNone(stages.source_audio_extraction(self.c, 's')[1])
        wav(out, samples=240)  # finalized header, but a prematurely stopped extraction
        self.assertIsNotNone(stages.source_audio_extraction(self.c, 's')[1])

    def test_valid_media_changes_cannot_reuse_old_completion_digest(self):
        recovery.atomic_ffmpeg(self.c, self.cmd, self.encode)
        wav(self.target, samples=240)
        self.assertFalse(recovery.media_cache_valid(self.target))

    def test_wrong_frame_obligation_rejects_before_reading_media(self):
        self.target.write_bytes(b'certified video bytes')
        digest = recovery._digest(self.target)
        marker = recovery.completion_marker(self.target)
        for actual, requested, allow_short in [(29, 30, False), (31, 30, False),
                                               (31, 30, True), (0, 30, True),
                                               (None, 30, True), ('30', 30, True)]:
            with self.subTest(actual=actual, requested=requested, allow_short=allow_short):
                marker.write_text(json.dumps({'schema': 1, 'sha256': digest,
                                              'frames': actual}))
                before = marker.read_bytes()
                with mock.patch.object(recovery, '_digest',
                                       side_effect=AssertionError('known cache miss must not hash')):
                    self.assertFalse(recovery.media_cache_valid(
                        self.target, requested, allow_short_verified=allow_short))
                self.assertEqual(marker.read_bytes(), before)
                self.assertEqual(self.target.read_bytes(), b'certified video bytes')

    def test_matching_frame_obligation_still_hashes_current_media(self):
        marker = recovery.completion_marker(self.target)
        for actual, requested, allow_short in [(30, 30, False), (29, 30, True),
                                               (None, None, False)]:
            for changed in (False, True):
                with self.subTest(actual=actual, requested=requested, changed=changed):
                    self.target.write_bytes(b'certified media bytes')
                    marker.write_text(json.dumps({'schema': 1,
                                                  'sha256': recovery._digest(self.target),
                                                  'frames': actual}))
                    if changed:
                        self.target.write_bytes(b'changed media bytes')
                    with mock.patch.object(recovery, '_digest', wraps=recovery._digest) as digest:
                        self.assertEqual(recovery.media_cache_valid(
                            self.target, requested, allow_short_verified=allow_short), not changed)
                    digest.assert_called_once_with(self.target)

    def test_failed_prepare_is_not_success_then_reuses_repaired_cache(self):
        src = self.root / 'source.wav'
        wav(src)
        tl = json.loads(self.c.tl_path.read_text())
        tl['sources'] = [{'id': 's', 'path': 'source.wav'}]
        self.c.tl_path.write_text(json.dumps(tl))
        self.c = Ctx(self.c.tl_path)
        self.c.begin_stage('prepare')
        def invalid(argv, **kwargs):
            Path(argv[-1]).write_bytes(b'half WAV')
            return subprocess.CompletedProcess(argv, -9, '', 'owned worker killed')
        with mock.patch('vtx.infra.subprocess.run', invalid), \
                contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                stages.prepare(self.c)
        rec = json.loads((self.c.out / 'receipt.json').read_text())
        self.assertFalse(rec['stages'].get('prepare', {}).get('ok', False))
        out, cmd = stages.source_audio_extraction(self.c, 's')
        self.assertIsNotNone(cmd)
        self.assertFalse(out.exists())
        def encode(argv, **kwargs):
            return self.encode(argv)
        with mock.patch('vtx.infra.subprocess.run', encode):
            stages.prepare(self.c)
        rec = json.loads((self.c.out / 'receipt.json').read_text())
        self.assertTrue(rec['stages']['prepare']['ok'])
        with mock.patch('vtx.infra.subprocess.run', side_effect=AssertionError('warm extraction')):
            stages.prepare(self.c)

    def test_marker_write_failure_does_not_certify_bad_media(self):
        with mock.patch.object(recovery.fstxn, 'atomic_write', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                recovery.atomic_ffmpeg(self.c, self.cmd, self.encode)
        self.assertFalse(recovery.completion_marker(self.target).exists())
        # Complete bytes without the publication certificate are rebuilt once.
        with mock.patch.object(recovery, '_inspect', side_effect=AssertionError('legacy adoption')):
            self.assertFalse(recovery.media_cache_valid(self.target))

    def test_dry_has_no_staging_marker_or_lock(self):
        self.c.dry = True
        before = sorted(self.root.rglob('*'))
        run_ffmpeg_task(self.c, 'dry', self.cmd)
        self.assertEqual(sorted(self.root.rglob('*')), before)


    def test_generic_transaction_callbacks_share_target_lock_and_result(self):
        target = self.target
        wav(target, samples=240)
        old = target.read_bytes()
        order = []
        token = object()

        def producer(staging):
            self.assertTrue(recovery.fstxn._thread_lock_for(target).locked())
            self.assertNotEqual(Path(staging), target)
            wav(staging)
            order.append('producer')
            return token

        def before():
            self.assertTrue(recovery.fstxn._thread_lock_for(target).locked())
            self.assertEqual(target.read_bytes(), old)
            self.assertFalse(recovery.completion_marker(target).exists())
            order.append('before')

        def after():
            self.assertTrue(recovery.fstxn._thread_lock_for(target).locked())
            self.assertTrue(recovery.media_cache_valid(target))
            self.assertNotEqual(target.read_bytes(), old)
            order.append('after')

        result = recovery.atomic_media(self.c, target, producer,
                                       before_commit=before, after_commit=after)
        self.assertIs(result, token)
        self.assertEqual(order, ['producer', 'before', 'after'])
        self.assertFalse(recovery.fstxn._thread_lock_for(target).locked())

    def test_generic_transaction_failures_preserve_owned_state_and_propagate(self):
        for phase, error in [('producer', KeyboardInterrupt()), ('producer', SystemExit(9)),
                             ('before', RuntimeError('before')), ('after', RuntimeError('after'))]:
            with self.subTest(phase=phase, error=type(error).__name__):
                recovery.atomic_ffmpeg(self.c, self.cmd, self.encode)
                old = self.target.read_bytes()
                marker = recovery.completion_marker(self.target)
                old_marker = marker.read_bytes()

                def producer(staging):
                    wav(staging, samples=240)
                    if phase == 'producer':
                        raise error

                def before():
                    if phase == 'before':
                        raise error

                def after():
                    if phase == 'after':
                        raise error

                with self.assertRaises(type(error)) as caught:
                    recovery.atomic_media(self.c, self.target, producer,
                                          before_commit=before, after_commit=after)
                self.assertIs(caught.exception, error)
                if phase == 'after':
                    self.assertNotEqual(self.target.read_bytes(), old)
                    self.assertTrue(recovery.media_cache_valid(self.target))
                else:
                    self.assertEqual(self.target.read_bytes(), old)
                    self.assertEqual(marker.read_bytes(), old_marker)
                self.assertFalse(recovery.fstxn._thread_lock_for(self.target).locked())
                self.assertEqual(list(self.target.parent.glob('.*.tmp')), [])

    def test_generic_dry_and_output_recognition_preserve_execution_boundary(self):
        for executable, accepted in [('ffmpeg', True), ('ffmpeg.exe', True),
                                     (str(self.root / 'bin' / 'ffmpeg'), True),
                                     ('notffmpeg', False), ('ffmpeg-helper', False)]:
            with self.subTest(executable=executable):
                self.assertEqual(recovery.media_output(self.c, [executable, str(self.target)]),
                                 self.target if accepted else None)
        producer = mock.Mock(side_effect=AssertionError('producer must not execute'))
        self.c.dry = True
        before = sorted(self.root.rglob('*'))
        self.assertIsNone(recovery.atomic_media(self.c, self.target, producer,
                                               before_commit=producer, after_commit=producer))
        with mock.patch('vtx.infra.subprocess.run', side_effect=AssertionError('dry executable')):
            self.c.run(['ffmpeg.exe', '-y', str(self.target)],
                       before_commit=producer, after_commit=producer)
            run_ffmpeg_task(self.c, 'dry exe', ['ffmpeg.exe', '-y', str(self.target)])
        self.assertEqual(sorted(self.root.rglob('*')), before)
        self.c.dry = False
        with self.assertRaises(ValueError):
            recovery.atomic_media(self.c, self.target.with_suffix('.png'), producer)
        from vtx.infra import ProjectPathError
        with self.assertRaises(ProjectPathError):
            recovery.atomic_media(self.c, self.root.parent / 'outside.wav', producer)
        producer.assert_not_called()

    def test_before_commit_input_change_is_rechecked_before_publication(self):
        recovery.atomic_ffmpeg(self.c, self.cmd, self.encode)
        old = self.target.read_bytes()
        marker = recovery.completion_marker(self.target)
        old_marker = marker.read_bytes()
        effect = self.root / 'before-effect'

        def before():
            effect.write_text('completed callback effect is not rolled back')
            self.c.tl_path.write_bytes(self.c.tl_path.read_bytes() + b' ')

        with self.assertRaises(RuntimeError):
            recovery.atomic_media(self.c, self.target, lambda path: wav(path, samples=240),
                                  before_commit=before)
        self.assertEqual(self.target.read_bytes(), old)
        self.assertEqual(marker.read_bytes(), old_marker)
        self.assertTrue(effect.is_file())
        self.assertEqual(list(self.target.parent.glob('.*.tmp')), [])

    def test_ffmpeg_adapter_retains_frame_caps_and_nonzero_result_failure(self):
        target = self.target.with_suffix('.mkv')
        def encode(argv):
            Path(argv[-1]).write_bytes(b'inert video')
            return subprocess.CompletedProcess(argv, 0, '', '')
        for options, frames, succeeds in [(['-frames:v', '3'], 2, True),
                                         (['-vframes', '1'], 2, False),
                                         (['-frames:v', '3', '-vframes', '1'], 2, False)]:
            with self.subTest(options=options):
                target.unlink(missing_ok=True)
                recovery.completion_marker(target).unlink(missing_ok=True)
                with mock.patch.object(recovery, '_inspect', side_effect=lambda path, kind: {
                        'schema': 1, 'frames': frames, 'sha256': recovery._digest(path)}):
                    if succeeds:
                        recovery.atomic_ffmpeg(self.c, ['ffmpeg', *options, str(target)], encode)
                        self.assertTrue(recovery.media_cache_valid(target, 3, allow_short_verified=True))
                    else:
                        with self.assertRaises(RuntimeError):
                            recovery.atomic_ffmpeg(self.c, ['ffmpeg', *options, str(target)], encode)
                        self.assertFalse(target.exists())
        with mock.patch.object(recovery, '_inspect', side_effect=AssertionError('failed encoder inspection')):
            with self.assertRaises(RuntimeError):
                recovery.atomic_ffmpeg(self.c, self.cmd,
                                       lambda argv: subprocess.CompletedProcess(argv, 1, '', 'failed'))
        self.assertFalse(self.target.exists())

    def test_mux_two_writers_keep_history_digest_and_timeline_under_final_lock(self):
        # Real mux/Ctx.run/fstxn/history/VERSIONS with an inert encoder and
        # explicit fake video inspection. No media/browser subprocess is run.
        import hashlib
        final = self.c.out / 'final.mp4'
        final.parent.mkdir(parents=True)
        final.write_bytes(b'OLD FINAL')
        old_inode = final.stat().st_ino
        path_b = self.root / 'timeline-b.json'
        tl_b = dict(self.c.tl, label='second writer')
        path_b.write_text(json.dumps(tl_b))
        c_b = Ctx(path_b)
        first_recorded = threading.Event()
        second_attempt = threading.Event()
        encoded_second = threading.Event()
        original_lock = recovery.fstxn._thread_lock_for
        original_record = stages.versions_record
        errors = []
        payloads = {'first': b'FIRST ENCODE', 'second': b'SECOND ENCODE'}

        class BoundedFinalLock:
            # A lock-order regression must fail inside the safety mocks, not
            # leave a daemon waiting forever after those mocks are removed.
            def __enter__(self):
                if not original_lock(final).acquire(timeout=5):
                    raise RuntimeError('final lock acquisition exceeded test deadline')
                return self

            def __exit__(self, *exc):
                original_lock(final).release()

        def lock(path):
            if Path(path) == final:
                if threading.current_thread().name == 'second':
                    second_attempt.set()
                return BoundedFinalLock()
            return original_lock(path)

        def execute(argv, **kwargs):
            if '-encoders' in argv:
                return subprocess.CompletedProcess(argv, 0, 'aac', '')
            name = threading.current_thread().name
            self.assertTrue(original_lock(final).locked())
            self.assertNotEqual(Path(argv[-1]), final)
            if name == 'second':
                encoded_second.set()
            Path(argv[-1]).write_bytes(payloads[name])
            return subprocess.CompletedProcess(argv, 0, '', '')

        def inspect(path, kind=None):
            return {'schema': 1, 'sha256': recovery._digest(path), 'frames': 30}

        def record(ctx, path, label):
            self.assertTrue(original_lock(final).locked())
            self.assertTrue(recovery.media_cache_valid(final, 30))
            self.assertEqual(final.read_bytes(), payloads[threading.current_thread().name])
            original_record(ctx, path, label)
            if threading.current_thread().name == 'first':
                first_recorded.set()
                if not second_attempt.wait(5):
                    raise RuntimeError('second writer did not attempt final lock')
                self.assertFalse(encoded_second.is_set())

        def worker(ctx):
            try:
                stages.mux(ctx)
            except BaseException as error:
                errors.append(error)

        with mock.patch('subprocess.Popen', side_effect=AssertionError('no external process')), \
                mock.patch('vtx.infra.subprocess.run', execute), \
                mock.patch.object(recovery, '_inspect', inspect), \
                mock.patch.object(recovery.fstxn, '_thread_lock_for', lock), \
                mock.patch.object(stages, 'versions_record', record):
            first = threading.Thread(target=worker, args=(self.c,), name='first', daemon=True)
            second = threading.Thread(target=worker, args=(c_b,), name='second', daemon=True)
            first.start()
            try:
                self.assertTrue(first_recorded.wait(5))
                second.start()
            finally:
                first.join(10)
                if second.ident is not None:
                    second.join(10)
            self.assertFalse(first.is_alive() or second.is_alive())
        self.assertEqual(errors, [])
        self.assertTrue(encoded_second.is_set())
        versions = json.loads((self.c.out / 'VERSIONS.json').read_text())['versions']
        self.assertEqual([(row['timeline_md5'], row['md5']) for row in versions],
                         [(self.c.timeline_md5, hashlib.md5(payloads['first']).hexdigest()),
                          (c_b.timeline_md5, hashlib.md5(payloads['second']).hexdigest())])
        history = list((self.c.out / 'history').glob('final_*.mp4'))
        old = next(path for path in history if path.read_bytes() == b'OLD FINAL')
        self.assertEqual(old.stat().st_ino, old_inode)
        self.assertIn(payloads['first'], [path.read_bytes() for path in history])
        self.assertEqual(final.read_bytes(), payloads['second'])
        self.assertTrue(recovery.media_cache_valid(final, 30))
        self.assertFalse((self.c.out / '.final.tmp.mp4').exists())
        self.assertFalse(recovery.completion_marker(self.c.out / '.final.tmp.mp4').exists())
        self.assertEqual(list(self.c.out.glob('.*.tmp')), [])


@unittest.skipUnless(os.environ.get('VTX_TEST_FFMPEG'), 'set VTX_TEST_FFMPEG')
class RealFFmpegRecovery(unittest.TestCase):
    def _seg_context(self, root, *, frames=6, variants=False):
        """Public, dynamic CPU input; no browser, fixture download, or GPU."""
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
                        'testsrc2=size=96x54:rate=30:duration=0.4', '-frames:v', '12',
                        '-c:v', 'libx264', '-crf', '0', '-preset', 'ultrafast',
                        str(root / 'source.mp4')], check=True, capture_output=True)
        clips = [dict(src='a', src_in=0, src_out=frames, dur=frames, dst_in=i * frames)
                 for i in range(12)]
        sources = [dict(id='a', path='source.mp4')]
        if variants:
            shutil.copyfile(root / 'source.mp4', root / 'other.mp4')
            sources.append(dict(id='b', path='other.mp4'))
            clips += [dict(clips[0], src='b', dst_in=72),
                      dict(clips[0], src_in=1, src_out=7, dst_in=78),
                      dict(clips[0], opacity=0.5, fade_in_frames=2, dst_in=84)]
        timeline = dict(version=2, project=dict(fps=30, width=96, height=54,
                                                duration_frames=len(clips) * frames),
                        sources=sources, video_tracks=[dict(kind='broll', clips=clips)],
                        audio_tracks=[], subtitles=[])
        path = root / 'timeline.json'
        path.write_text(json.dumps(timeline))
        return Ctx(path)

    def _decoded_rgb_frames(self, path, width=96, height=54):
        result = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(path),
                                 '-threads', '1', '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-'],
                                capture_output=True, check=True)
        stride = width * height * 3
        self.assertEqual(len(result.stdout) % stride, 0)
        return [result.stdout[i:i + stride] for i in range(0, len(result.stdout), stride)]

    def test_prepare_duplicate_consumers_cold_warm_frames_and_markers(self):
        from vtx import composite, infra
        with tempfile.TemporaryDirectory(prefix='vtx-seg-dedup-') as directory, \
                mock.patch.dict(os.environ, {'VTX_SEG_NVENC': '0'}):
            c = self._seg_context(Path(directory))
            with mock.patch.object(infra, '_run_ffmpeg_task', wraps=infra._run_ffmpeg_task) as encode:
                layers = composite.prepare_layers(c)
                self.assertEqual(encode.call_count, 1)
                self.assertEqual(len(layers), 12)
                self.assertEqual([layer['cl']['dst_in'] for layer in layers], list(range(0, 72, 6)))
                self.assertEqual(len({layer['seg'] for layer in layers}), 1)
                target = layers[0]['seg']
                before = (target.read_bytes(), target.stat().st_mtime_ns,
                          recovery.completion_marker(target).read_bytes())
                self.assertEqual(composite.prepare_layers(c), layers)
                self.assertEqual(encode.call_count, 1)
                self.assertEqual(before, (target.read_bytes(), target.stat().st_mtime_ns,
                                          recovery.completion_marker(target).read_bytes()))
            self.assertTrue(recovery.media_cache_valid(target, 6))
            record = json.loads(recovery.completion_marker(target).read_text())
            self.assertEqual(record['sha256'], recovery._digest(target))
            frames = self._decoded_rgb_frames(target)
            self.assertEqual(len(frames), 6)
            self.assertGreater(len(set(frames)), 1)
            # All twelve placed consumers must reach the real compositor, including the tail.
            plan = composite.RenderPlan(c=c, draft=False, f=1, W=c.w, H=c.h, layers=layers)
            c.out.mkdir(exist_ok=True)
            final = c.out / 'deduplicated.mp4'
            plan.composite(layers, final, False, a=0, b=c.dur)
            self.assertTrue(recovery.media_cache_valid(final, 72))
            rendered = self._decoded_rgb_frames(final)
            self.assertEqual(len(rendered), 72)
            passes = list((c.cache / 'vseg_pass').glob('*.mkv'))
            self.assertTrue(passes, 'actual multilayer path must publish intermediate passes')
            self.assertTrue(all(recovery.media_cache_valid(path, 72) for path in passes))
            self.assertFalse(any('.part.' in path.name for path in (c.cache / 'vseg_pass').iterdir()))
            with mock.patch.object(c, 'run', wraps=c.run) as warm:
                plan.composite(layers, final, False, a=0, b=c.dur)
                self.assertEqual(warm.call_count, 0, 'all passes including final must be certified warm hits')
                final.unlink()
                recovery.completion_marker(final).unlink()
                plan.composite(layers, final, False, a=0, b=c.dur)
                self.assertEqual(warm.call_count, 1, 'only the missing final pass must be rebuilt')
            self.assertEqual(self._decoded_rgb_frames(final), rendered)
            for start in range(0, 72, 6):
                self.assertGreater(len(set(rendered[start:start + 6])), 1)
            # A deliberate consumer-only plan with no scheduling builds the unchanged oracle.
            reference_layers = [dict(cl=clip, seg=target, x=0, y=0, w2=None, h2=None,
                                     gfx=False, ti=0) for clip in c.tl['video_tracks'][0]['clips']]
            self.assertEqual(layers, reference_layers)
            reference = c.out / 'reference.mp4'
            plan.composite(reference_layers, reference, False, a=0, b=c.dur)
            self.assertEqual(self._decoded_rgb_frames(reference), rendered)

    def test_prepare_different_source_trim_and_alpha_stay_separate(self):
        from vtx import composite, infra
        with tempfile.TemporaryDirectory(prefix='vtx-seg-variants-') as directory, \
                mock.patch.dict(os.environ, {'VTX_SEG_NVENC': '0'}):
            c = self._seg_context(Path(directory), variants=True)
            with mock.patch.object(infra, '_run_ffmpeg_task', wraps=infra._run_ffmpeg_task) as encode:
                layers = composite.prepare_layers(c)
                self.assertEqual(encode.call_count, 4)
                self.assertEqual(len(layers), 15)
                self.assertEqual(len({layer['seg'] for layer in layers}), 4)
                self.assertEqual(composite.prepare_layers(c), layers)
                self.assertEqual(encode.call_count, 4)
            self.assertEqual(layers[-1]['seg'].suffix, '.mov')
            for target in {layer['seg'] for layer in layers}:
                self.assertTrue(recovery.media_cache_valid(target, 6))
                self.assertEqual(len(self._decoded_rgb_frames(target)), 6)
            self.assertNotEqual(self._decoded_rgb_frames(layers[0]['seg']),
                                self._decoded_rgb_frames(layers[-2]['seg']))

    def test_prepare_failed_unique_encoder_blocks_consumers_and_can_retry(self):
        from vtx import composite, infra
        with tempfile.TemporaryDirectory(prefix='vtx-seg-failure-') as directory, \
                mock.patch.dict(os.environ, {'VTX_SEG_NVENC': '0'}):
            c = self._seg_context(Path(directory))
            c.receipt('video', True)
            c.begin_stage('video')
            original = infra._run_ffmpeg_task
            def fail(ctx, label, argv, nvenc=False):
                argv = list(argv)
                argv[argv.index('-c:v') + 1] = 'vtx_intentionally_missing_encoder'
                return original(ctx, label, argv, nvenc)
            with mock.patch.object(infra, '_run_ffmpeg_task', side_effect=fail) as encode, \
                    self.assertRaises(SystemExit) as caught:
                composite.prepare_layers(c)
            self.assertEqual(caught.exception.code, 1)
            self.assertEqual(encode.call_count, 1)
            self.assertEqual(list((c.cache / 'seg').glob('*.mp4')), [])
            self.assertEqual(list((c.cache / 'seg').glob('*.complete.json')), [])
            self.assertEqual(list((c.cache / 'seg').glob('.*.tmp')), [])
            self.assertFalse(json.loads((c.out / 'receipt.json').read_text())['stages'].get('video', {}).get('ok', False))
            with mock.patch.object(infra, '_run_ffmpeg_task', wraps=original) as retry:
                layers = composite.prepare_layers(c)
                self.assertEqual(retry.call_count, 1)
                self.assertEqual(composite.prepare_layers(c), layers)
                self.assertEqual(retry.call_count, 1)

    def test_prepare_rejects_source_change_before_unique_producer_commit(self):
        from vtx import composite, infra
        with tempfile.TemporaryDirectory(prefix='vtx-seg-source-change-') as directory, \
                mock.patch.dict(os.environ, {'VTX_SEG_NVENC': '0'}):
            c = self._seg_context(Path(directory))
            c.receipt('video', True)
            c.begin_stage('video')
            source = c.root / 'source.mp4'
            original = infra._run_ffmpeg_task
            def change(ctx, label, argv, nvenc=False):
                result = original(ctx, label, argv, nvenc)
                source.write_bytes(source.read_bytes() + b'changed after encoding')
                return result
            with mock.patch.object(infra, '_run_ffmpeg_task', side_effect=change) as encode, \
                    self.assertRaises(SystemExit):
                composite.prepare_layers(c)
            self.assertEqual(encode.call_count, 1)
            self.assertEqual(list((c.cache / 'seg').glob('*.mp4')), [])
            self.assertEqual(list((c.cache / 'seg').glob('*.complete.json')), [])
            self.assertEqual(list((c.cache / 'seg').glob('.*.tmp')), [])
            self.assertFalse(json.loads((c.out / 'receipt.json').read_text())['stages'].get('video', {}).get('ok', False))

    def test_prepare_marker_failure_still_propagates_without_success_receipt(self):
        from vtx import composite, infra
        with tempfile.TemporaryDirectory(prefix='vtx-seg-marker-failure-') as directory, \
                mock.patch.dict(os.environ, {'VTX_SEG_NVENC': '0'}):
            c = self._seg_context(Path(directory))
            c.receipt('video', True)
            c.begin_stage('video')
            original_write = recovery.fstxn.atomic_write
            def fail_marker(path, data, *, root=None):
                if str(path).endswith('.mp4.complete.json'):
                    raise OSError('injected marker publication failure')
                return original_write(path, data, root=root)
            with mock.patch.object(recovery.fstxn, 'atomic_write', side_effect=fail_marker), \
                    mock.patch.object(infra, '_run_ffmpeg_task', wraps=infra._run_ffmpeg_task) as encode, \
                    self.assertRaisesRegex(OSError, 'marker publication failure'):
                composite.prepare_layers(c)
            self.assertEqual(encode.call_count, 1)
            self.assertEqual(len(list((c.cache / 'seg').glob('*.mp4'))), 1)
            self.assertEqual(list((c.cache / 'seg').glob('*.complete.json')), [])
            self.assertFalse(json.loads((c.out / 'receipt.json').read_text())['stages'].get('video', {}).get('ok', False))
            # Missing completion evidence requires one rebuild, never adoption.
            with mock.patch.object(infra, '_run_ffmpeg_task', wraps=infra._run_ffmpeg_task) as retry:
                layers = composite.prepare_layers(c)
                self.assertEqual(len(layers), 12)
                self.assertEqual(retry.call_count, 1)
                self.assertEqual(composite.prepare_layers(c), layers)
                self.assertEqual(retry.call_count, 1)

    def test_prepare_verified_short_source_semantics_are_unchanged(self):
        from vtx import composite, infra
        with tempfile.TemporaryDirectory(prefix='vtx-seg-short-') as directory, \
                mock.patch.dict(os.environ, {'VTX_SEG_NVENC': '0'}):
            c = self._seg_context(Path(directory), frames=18)
            with mock.patch.object(infra, '_run_ffmpeg_task', wraps=infra._run_ffmpeg_task) as encode:
                layers = composite.prepare_layers(c)
                self.assertEqual(encode.call_count, 1)
                self.assertEqual(composite.prepare_layers(c), layers)
                self.assertEqual(encode.call_count, 1)
            self.assertTrue(recovery.media_cache_valid(layers[0]['seg'], 18, allow_short_verified=True))
            self.assertFalse(recovery.media_cache_valid(layers[0]['seg'], 18))
            self.assertEqual(len(self._decoded_rgb_frames(layers[0]['seg'])), 12)

    def test_killed_writer_then_retry_preserves_verified_sibling(self):
        with tempfile.TemporaryDirectory(prefix='vtx-kill-ffmpeg-') as d:
            root = Path(d)
            c = context(root)
            target = root / 'cache' / 'seg' / 'out.mp4'
            target.parent.mkdir(parents=True)
            cmd = ['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
                   'color=red:s=64x48:r=30:d=0.2', '-c:v', 'libx264', str(target)]
            c.run(cmd)
            c.receipt('video', True)
            c.begin_stage('video')
            self.assertTrue(recovery.media_cache_valid(target, 10, allow_short_verified=True))
            self.assertFalse(recovery.media_cache_valid(target, 10))
            sibling = target.with_name('sibling.mp4')
            shutil.copyfile(target, sibling)
            sibling_bytes = sibling.read_bytes()
            old = target.read_bytes()
            worker = '''import json,subprocess,sys\nfrom pathlib import Path\nfrom vtx.infra import Ctx\nc=Ctx(Path(sys.argv[1]))\noriginal=subprocess.run\ndef run(cmd,**kw):\n if cmd[0]=='ffmpeg':\n  p=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE)\n  print('OWNED_FFMPEG_PID='+str(p.pid),flush=True)\n  out,err=p.communicate()\n  return subprocess.CompletedProcess(cmd,p.returncode,out.decode(),err.decode())\n return original(cmd,**kw)\nsubprocess.run=run\nc.run(['ffmpeg','-v','error','-y','-re','-f','lavfi','-i','color=blue:s=64x48:r=30:d=30','-c:v','libx264','-preset','ultrafast','-tune','zerolatency','-g','1','-flush_packets','1','-movflags','frag_keyframe+empty_moov',sys.argv[2]])\n'''
            proc = subprocess.Popen([sys.executable, '-c', worker, str(c.tl_path), str(target)],
                                    cwd=Path(__file__).resolve().parents[2],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    text=True, start_new_session=True)
            try:
                pidline = proc.stdout.readline().strip()
                self.assertTrue(pidline.startswith('OWNED_FFMPEG_PID='), pidline)
                encoder_pid = int(pidline.split('=')[1])
                deadline = time.monotonic() + 10
                partial = []
                while time.monotonic() < deadline:
                    partial = [p for p in target.parent.glob('.out.mp4.*.tmp')
                               if p.stat().st_size > 64]
                    if partial:
                        break
                    if proc.poll() is not None:
                        self.fail(proc.stderr.read())
                    time.sleep(.03)
                self.assertTrue(partial, 'owned writer must actually create partial bytes')
                print('KILLED_OWNED_PGID=%d FFMPEG_PID=%d' % (proc.pid, encoder_pid))
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait(timeout=5)
                self.assertEqual(target.read_bytes(), old)
                rec = json.loads((c.out / 'receipt.json').read_text())
                self.assertFalse(rec['stages'].get('video', {}).get('ok', False))
                self.assertTrue(recovery.media_cache_valid(target, 6))
                c.run(cmd)
                c.receipt('video', True)
                self.assertFalse(partial[0].exists())
                self.assertTrue(recovery.media_cache_valid(target, 6))
                self.assertEqual(sibling.read_bytes(), sibling_bytes)
            finally:
                if proc.poll() is None:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait(timeout=5)
                proc.stdout.close()
                proc.stderr.close()


@unittest.skipUnless(os.environ.get('VTX_TEST_CHROME'), 'set VTX_TEST_CHROME')
class RealChromeRecovery(unittest.TestCase):
    def test_kill_own_browser_reuses_completed_frames(self):
        from vtx.render import gfx_render
        with tempfile.TemporaryDirectory(prefix='vtx-kill-chrome-') as d:
            root = Path(d)
            html = root / 'page.html'
            html.write_text('<html><body style="margin:0;background:rgb(30,50,90)">'
                            '<div style="width:10px;height:10px;background:red"></div></body></html>')
            out = root / 'frames'
            browsers = []
            original_start = gfx_render.Browser.start
            original_write = gfx_render._atomic_write_bytes
            def start(browser):
                original_start(browser)
                browsers.append(browser)
            killed = []
            def interrupt(path, data, *, root=None):
                original_write(path, data, root=root)
                if not killed:
                    proc = browsers[-1].proc
                    killed.append(proc.pid)
                    print('KILLED_OWNED_CHROME_PID=%d' % proc.pid)
                    proc.kill()
                    proc.wait(timeout=5)
            with mock.patch.object(gfx_render.Browser, 'start', start), \
                    mock.patch.object(gfx_render, '_atomic_write_bytes', interrupt), \
                    mock.patch.object(gfx_render.Browser, 'hard_restart',
                                      side_effect=RuntimeError('restart unavailable this run')):
                interrupted = gfx_render.render_page(
                    html, out, 3, chrome_path=os.environ['VTX_TEST_CHROME'],
                    tabs=1, width=64, height=48)
            self.assertTrue(interrupted['failed'])
            kept = {p.name: p.read_bytes() for p in out.glob('f*.png')}
            self.assertTrue(kept)
            self.assertLess(len(kept), 3)
            stats = gfx_render.render_page(html, out, 3, chrome_path=os.environ['VTX_TEST_CHROME'],
                                           tabs=1, width=64, height=48)
            self.assertEqual(stats['failed'], [])
            self.assertEqual(stats['rendered'], 3 - len(kept))
            for name, data in kept.items():
                self.assertEqual((out / name).read_bytes(), data)
            with mock.patch.object(gfx_render, 'Browser', side_effect=AssertionError('warm browser')):
                self.assertEqual(gfx_render.render_page(html, out, 3, tabs=1, width=64,
                                                       height=48)['rendered'], 0)


if __name__ == '__main__':
    unittest.main()
