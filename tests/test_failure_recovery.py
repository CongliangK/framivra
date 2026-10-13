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
import unittest
from unittest import mock
import wave

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
        self.assertTrue(recovery._marker(self.target).exists())
        with mock.patch.object(recovery, '_inspect', side_effect=AssertionError('warm decode')):
            self.assertTrue(recovery.media_cache_valid(self.target))

    def test_interruption_preserves_good_target_and_no_new_marker(self):
        wav(self.target)
        old = self.target.read_bytes()
        def interrupted(cmd):
            Path(cmd[-1]).write_bytes(b'half WAV')
            raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            recovery.atomic_ffmpeg(self.c, self.cmd, interrupted)
        self.assertEqual(self.target.read_bytes(), old)
        self.assertFalse(recovery._marker(self.target).exists())
        self.assertEqual(list(self.target.parent.glob('.*.tmp')), [])

    def test_success_exit_with_invalid_media_cannot_publish(self):
        def invalid(cmd):
            Path(cmd[-1]).write_bytes(b'truncated')
            return subprocess.CompletedProcess(cmd, 0, '', '')
        with self.assertRaises(RuntimeError):
            recovery.atomic_ffmpeg(self.c, self.cmd, invalid)
        self.assertFalse(self.target.exists())
        self.assertFalse(recovery._marker(self.target).exists())

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

    def test_legacy_and_digest_mismatch_require_actual_complete_wav(self):
        wav(self.target)
        self.assertTrue(recovery.media_cache_valid(self.target))
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
        self.assertIsNone(stages.source_audio_extraction(self.c, 's')[1])
        out.write_bytes(out.read_bytes()[:-4])
        self.assertIsNotNone(stages.source_audio_extraction(self.c, 's')[1])
        wav(out, samples=240)  # finalized header, but a prematurely stopped extraction
        self.assertIsNotNone(stages.source_audio_extraction(self.c, 's')[1])

    def test_valid_media_changes_cannot_reuse_old_completion_digest(self):
        recovery.atomic_ffmpeg(self.c, self.cmd, self.encode)
        wav(self.target, samples=240)
        self.assertFalse(recovery.media_cache_valid(self.target))

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
        self.assertFalse(recovery._marker(self.target).exists())
        # The encoder finished: this is good legacy media, not a false marker.
        self.assertTrue(recovery.media_cache_valid(self.target))

    def test_dry_has_no_staging_marker_or_lock(self):
        self.c.dry = True
        before = sorted(self.root.rglob('*'))
        run_ffmpeg_task(self.c, 'dry', self.cmd)
        self.assertEqual(sorted(self.root.rglob('*')), before)


@unittest.skipUnless(os.environ.get('VTX_TEST_FFMPEG'), 'set VTX_TEST_FFMPEG')
class RealFFmpegRecovery(unittest.TestCase):
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
                                    cwd=Path(__file__).resolve().parents[1],
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
        from vtx import gfx_render
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
            def interrupt(path, data):
                original_write(path, data)
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
