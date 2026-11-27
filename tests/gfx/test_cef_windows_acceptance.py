"""Opt-in Windows native CEF acceptance for a small trusted local project (#420).

Set VTX_TEST_CEF_WORKER to the built .exe, then run:
    python -B -m unittest tests.gfx.test_cef_windows_acceptance -v
Optionally set VTX_CEF_EVIDENCE_DIR to a directory outside the source checkout;
projects, logs, and reports are kept there even on failure. Not a release or
performance qualification, and not an arbitrary-HTML compatibility matrix.
"""
from contextlib import ExitStack
import ctypes
from ctypes import wintypes
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
import wave

from tests.gfx.test_cef_frame_sync import decode_png_rgba
from tests.gfx.test_gfx_cef_alpha import PAGE, project
from vtx.render import cef_client

ROOT = Path(__file__).resolve().parents[2]
WORKER = os.environ.get('VTX_TEST_CEF_WORKER', '')


def _json(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')


def _media_project(root):
    timeline = project(root, n=30)
    (root / 'page.html').write_text('''<!doctype html><style>
html,body{margin:0;background:#faf9f6;overflow:hidden}div{position:fixed;
width:80px;height:40px;top:60px;background:#c8a24a}</style><div></div><script>
let f=+new URLSearchParams(location.search).get('f');
document.querySelector('div').style.left=(20+f*3)+'px';</script>''', encoding='utf-8')
    with wave.open(str(root / 'voice.wav'), 'wb') as wav:
        wav.setparams((1, 2, 48000, 0, 'NONE', 'not compressed'))
        wav.writeframes(b''.join(struct.pack('<h', int(4000 * math.sin(i * 2 * math.pi * 440 / 48000)))
                                 for i in range(48000)))
    data = json.loads(timeline.read_bytes())
    data['project'].update(width=320, height=180)
    data['sources'] = [dict(id='voice', path='voice.wav')]
    data['audio_tracks'] = [dict(name='voice', events=[
        {'src': 'voice', 'volume': 1.0, 'in': 0, 'out': 30}])]
    _json(timeline, data)
    return timeline


@unittest.skipUnless(sys.platform == 'win32' and WORKER,
                     'requires Windows and explicit VTX_TEST_CEF_WORKER')
class WindowsCefAcceptance(unittest.TestCase):
    def setUp(self):
        worker = Path(WORKER).resolve(strict=True)
        self.assertTrue(worker.is_file() and worker.suffix.lower() == '.exe', worker)
        evidence = os.environ.get('VTX_CEF_EVIDENCE_DIR')
        if evidence:
            parent = Path(evidence).resolve()
            self.assertFalse(parent == ROOT or ROOT in parent.parents,
                             'CEF evidence must be outside the source checkout')
            parent.mkdir(parents=True, exist_ok=True)
            # Keep the harness path short; Chromium appends its own profile
            # subtrees to the CLI's already-nested scratch path on Windows.
            work = tempfile.mkdtemp(dir=parent, prefix='case-')
            print(f'Windows CEF evidence: {work}', flush=True)
        else:
            temp = tempfile.TemporaryDirectory(prefix='cef-win-')
            self.addCleanup(temp.cleanup)
            work = temp.name
        self.work = Path(work).resolve()
        config = self.work / 'isolated-config.json'
        _json(config, {})
        # Use the real runtime and inherited system PATH, not local application overrides.
        self.env = {k: v for k, v in os.environ.items() if not k.upper().startswith(('VTX_', 'PYTHON'))}
        self.env.update(VTX_CONFIG=str(config), VTX_GFX='cef', VTX_CEF_WORKER=str(worker),
                        VTX_CEF_MODE='a0', VTX_CEF_SYNC_MODE='n', VTX_GFX_PAGES='1',
                        VTX_GFX_SPLIT_FRAMES='0', VTX_CEF_PAIR_SWAP='0', VTX_PIPELINE='0',
                        VTX_VIDEO_SEGMENTS='1', VTX_NO_NVENC='1')
        _json(self.work / 'fixture.json', {
            'python': sys.executable, 'worker': str(worker),
            'worker_sha256': hashlib.sha256(worker.read_bytes()).hexdigest(),
            'windows_version': str(sys.getwindowsversion()),
            'ffmpeg': shutil.which('ffmpeg'), 'ffprobe': shutil.which('ffprobe'),
            'configuration': {k: v for k, v in self.env.items() if k.startswith('VTX_')},
        })

    def _run(self, log, argv, *, timeout=150, expected=0):
        stdout = self.work / (log + '.stdout.log')
        stderr = self.work / (log + '.stderr.log')
        _json(self.work / (log + '.argv.json'), list(argv))
        # Stream to files so a timeout does not discard diagnostics or wait on
        # descendant writers holding a caller-owned capture pipe open.
        with stdout.open('wb') as out, stderr.open('wb') as err:
            result = subprocess.run(argv, cwd=ROOT, env=self.env, stdout=out, stderr=err,
                                    timeout=timeout)
        text = stdout.read_text(encoding='utf-8', errors='replace')
        self.assertEqual(result.returncode, expected,
                         text + stderr.read_text(encoding='utf-8', errors='replace'))
        return text

    def _cli(self, timeline, label, *command, **kwargs):
        return self._run(label, [sys.executable, '-B', '-X', 'utf8', '-m', 'vtx',
                                '-t', str(timeline), *command], **kwargs)

    def _isolated_cli(self, timeline, label, method, **kwargs):
        source = ('import sys; from pathlib import Path; '
                  'from tests.gfx.test_cef_windows_acceptance import WindowsCefAcceptance; '
                  f'WindowsCefAcceptance().{method}(Path(sys.argv[1]))')
        return self._run(label, [sys.executable, '-B', '-X', 'utf8', '-c', source, str(timeline)],
                         **kwargs)

    def _native(self, root, *, completed, rendered, pipeline_path=None):
        reports = root / 'cache/reports/cef'
        if pipeline_path is None:
            pipeline_path, = reports.glob('*.pipeline.json')
        prefix = pipeline_path.name.removesuffix('.pipeline.json')
        pipeline = json.loads(pipeline_path.read_bytes())
        self.assertIs(pipeline['completed'], completed, pipeline)
        self.assertEqual(pipeline['rendered_frames'], rendered, pipeline)
        hello = pipeline['worker_hello']
        self.assertEqual((hello['flags'], hello['ring_slots']),
                         (cef_client.HELLO_FLAG_PAIRED_EXACT, 0), hello)
        receipts = json.loads((reports / (prefix + '.receipts.json')).read_bytes())
        self.assertEqual([(r['target_frame_index'], r['canary_frame_index'], r['status_code'])
                          for r in receipts], [(f, f, 0) for f in rendered])
        self.assertTrue(all(r['run_id'] == pipeline['worker_hello']['run_id'] for r in receipts))
        sync = [json.loads(line) for line in (reports / (prefix + '.sync.jsonl')).read_bytes().splitlines()]
        self.assertTrue((reports / (prefix + '.worker.log')).is_file())
        self.assertFalse(list((root / 'cache/tmp').iterdir()), 'CEF Job scratch/profile leaked')
        return sync

    def test_exact_mode_n_pixels_in_unicode_hash_paths(self):
        # Stress the real physical project path without making published
        # Windows paths exceed MAX_PATH. The earlier 155-character fixture
        # exposed Chromium's profile-subtree length before Hello.
        prefix = self.work / '工程 #42 空 白'
        root = prefix / ('深' * max(1, 155 - len(str(prefix)) - 1))
        root.mkdir(parents=True)
        timeline = project(root)
        source = '素材 #片/图层 #1.html'
        html = root / source
        html.parent.mkdir()
        html.write_text(PAGE, encoding='utf-8')
        (root / 'page.html').unlink()
        data = json.loads(timeline.read_bytes())
        data['video_tracks'][0]['clips'][0]['src'] = source
        _json(timeline, data)

        self.assertIn('Mode N native-exact', self._cli(timeline, 'exact', 'gfx'))
        sync = self._native(root, completed=True, rendered=[0, 1, 2])
        proved = [e for e in sync if e.get('to') == 'S5_ONPAINT_VERIFIED']
        self.assertEqual([e['target_frame_index'] for e in proved], [0, 1, 2])
        self.assertTrue(all(e['drive_mode'] == 'n' and e['restore_source'] == 'native_cross_capture'
                            for e in proved))
        paths = []
        for frame in range(3):
            path, = (root / 'cache').rglob(f'f{frame:04d}.png')
            paths.append(path)
            width, height, rgba = decode_png_rgba(path)
            self.assertEqual((width, height), (64, 48))
            for y in range(height):
                for x in range(width):
                    if x <= 9 or x >= 54 or y <= 9 or y >= 38:
                        expected = ((x + frame) % 256, y + 9, x ^ y, 255)
                    else:
                        expected = (17, 33, 65, 255) if frame % 2 else (0, 0, 0, 0)
                    offset = (y * width + x) * 4
                    self.assertEqual(rgba[offset:offset + 4], bytes(expected), (frame, x, y))

        certificate = paths[0].parent / 'complete.json'
        reports = set((root / 'cache/reports/cef').glob('*.pipeline.json'))

        def identity(path):
            stat = path.stat()
            return stat.st_ino, stat.st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest()

        cold = {p: identity(p) for p in [*paths, certificate]}
        self.assertIn('已缓存', self._isolated_cli(timeline, 'warm', '_warm_fixture'))
        self.assertEqual(set((root / 'cache/reports/cef').glob('*.pipeline.json')), reports)
        self.assertEqual({p: identity(p) for p in cold}, cold)

        missing = paths[1]
        expected = decode_png_rgba(missing)
        missing.unlink()
        self._cli(timeline, 'repair', 'gfx')
        repair_report, = set((root / 'cache/reports/cef').glob('*.pipeline.json')) - reports
        sync = self._native(root, completed=True, rendered=[1], pipeline_path=repair_report)
        self.assertEqual([row['target_frame_index'] for row in sync if row['event'] == 'request_begin'], [1])
        self.assertEqual(decode_png_rgba(missing), expected)
        self.assertTrue(certificate.is_file())
        self.assertEqual({p: identity(p) for p in (paths[0], paths[2])},
                         {p: cold[p] for p in (paths[0], paths[2])})

    def _warm_fixture(self, timeline):
        from vtx import main
        from vtx.runtime import windows_browser as wb

        launch = wb._OwnedJob._launch

        def forbid_cef_launch(job, args, *rest, **kwargs):
            if Path(args[0]).resolve() == Path(os.environ['VTX_CEF_WORKER']):
                self.fail('warm cache launched the native worker')
            return launch(job, args, *rest, **kwargs)

        with mock.patch.object(wb._OwnedJob, '_launch', forbid_cef_launch), \
             mock.patch.object(sys, 'argv', ['vtx', '-t', str(timeline), 'gfx']):
            raise SystemExit(main())

    def test_short_delivery_and_independent_strict_qc(self):
        root = self.work
        timeline = _media_project(root)
        self._cli(timeline, 'all', 'all', timeout=220)
        receipt = root / 'out/receipt.json'
        first = json.loads(receipt.read_bytes())
        for stage in ('validate', 'prepare', 'gfx', 'video', 'audio', 'mux', 'qc'):
            self.assertIs(first['stages'][stage]['ok'], True, stage)
        _json(root / 'all.receipt.json', first)
        self._native(root, completed=True, rendered=list(range(30)))

        media = root / 'out/final.mp4'
        sha256 = hashlib.sha256(media.read_bytes()).hexdigest()
        self._cli(timeline, 'strict', 'qc', '--strict', timeout=120)
        self.assertIs(json.loads(receipt.read_bytes())['stages']['qc']['ok'], True)
        qc = json.loads((root / 'out/qc_report.json').read_bytes())
        self.assertEqual(qc['skipped_probes'], [])
        self.assertTrue(qc['checks'] and all(check['pass'] for check in qc['checks']), qc)
        self.assertEqual(sha256, hashlib.sha256(media.read_bytes()).hexdigest(), 'QC changed media')
        _json(root / 'media.sha256.json', {'final.mp4': sha256})

        probe = json.loads(self._run('ffprobe', ['ffprobe', '-v', 'error', '-count_frames',
                                                '-show_streams', '-of', 'json', str(media)], timeout=30))
        self.assertEqual(sorted(s['codec_type'] for s in probe['streams']), ['audio', 'video'])
        video = next(s for s in probe['streams'] if s['codec_type'] == 'video')
        audio = next(s for s in probe['streams'] if s['codec_type'] == 'audio')
        self.assertEqual((video['width'], video['height'], video['avg_frame_rate'],
                          int(video['nb_read_frames'])), (320, 180, '30/1', 30))
        self.assertAlmostEqual(float(video['duration']), 1.0, delta=1e-6)
        self.assertEqual((audio['codec_name'], audio['sample_rate'], audio['channels']), ('aac', '48000', 2))
        self.assertGreater(int(audio['nb_read_frames']), 0)
        self.assertAlmostEqual(float(audio['duration']), 1.0, delta=1 / 30)

    def test_malformed_js_fails_closed(self):
        root = self.work
        timeline = project(root, n=1)
        (root / 'page.html').write_text('''<!doctype html>
<style>html,body{margin:0;background:#e7111f}</style>
<script>const malformed = ;</script>''', encoding='utf-8')
        self._cli(timeline, 'rejected', 'gfx', expected=1)
        receipt = json.loads((root / 'out/receipt.json').read_bytes())
        self.assertIs(receipt['stages']['gfx']['ok'], False)
        self.assertIn('JS_ERR', receipt['stages']['gfx']['detail'])
        self.assertFalse(list((root / 'cache').rglob('f0000.png')))
        self.assertFalse(list((root / 'cache').rglob('complete.json')))
        self.assertFalse((root / 'out/final.mp4').exists())
        sync = self._native(root, completed=False, rendered=[])
        self.assertFalse(any(e.get('to') == 'S5_ONPAINT_VERIFIED' for e in sync))
        failed, = [e for e in sync if e['event'] == 'request_end']
        self.assertEqual(failed['status'], 'failed', failed)
        self.assertTrue(failed['reason'].startswith('JS_ERR'), failed)

    def test_cancellation_settles_native_job(self):
        timeline = project(self.work)
        # Keep the third capture in S1. The native two-ticket pipeline sends
        # the previous receipt after the current capture, so frame 0 can be
        # published while the host's next receive remains genuinely blocked.
        (self.work / 'page.html').write_text(PAGE + '''<script>
if (f === 2) Object.defineProperty(document.fonts, 'ready', {value: new Promise(() => {})});
</script>''', encoding='utf-8')
        # Only the isolated child's own Python main thread will receive SIGINT.
        self._isolated_cli(timeline, 'cancel', '_cancel_fixture', timeout=120, expected=130)
        sync = self._native(self.work, completed=False, rendered=[0])
        self.assertFalse(any(e['event'] == 'request_end' and e['request_id'] == 3 for e in sync),
                         'the native ready timeout must not substitute for CLI cancellation')
        self.assertEqual(sorted(p.name for p in (self.work / 'cache').rglob('f????.png')), ['f0000.png'])
        self.assertFalse(list((self.work / 'cache').rglob('complete.json')))

    def _cancel_fixture(self, timeline):
        import _thread
        from vtx import main
        from vtx.render import gfx_cef
        from vtx.runtime import windows_browser as wb

        root = timeline.parent
        evidence = root / 'job-proof.json'
        proof = {}
        interrupt = KeyboardInterrupt('test SIGINT on CLI coordinator')
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE,
                                          ctypes.POINTER(wintypes.BOOL)]
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        launch, settle = wb._OwnedJob._launch, wb._OwnedJob._wait_job_empty
        receive = gfx_cef.CefWorkerClient.receive_png
        owned, handles, calls = [], {}, []

        def observe_launch(job, args, *rest, **kwargs):
            launch(job, args, *rest, **kwargs)
            if Path(args[0]).resolve() == Path(os.environ['VTX_CEF_WORKER']):
                owned.append(job)

        def observe_settlement(job, deadline):
            settle(job, deadline)
            if owned == [job]:
                accounting = wb._BasicAccounting()
                self.assertTrue(job._api.QueryInformationJobObject(
                    job._job, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None))
                proof['active_processes_after_cleanup'] = accounting.active

        def interrupt_coordinator(signum, _frame):
            self.assertEqual(signum, signal.SIGINT)
            proof['interrupt_handled_on_main'] = threading.current_thread() is threading.main_thread()
            raise interrupt

        def receive_or_cancel(client, *args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                return receive(client, *args, **kwargs)
            self.assertEqual(len(calls), 2)
            self.assertIsNot(threading.current_thread(), threading.main_thread())
            self.assertEqual(len(list((root / 'cache').rglob('f0000.png'))), 1)
            job, = owned
            self.assertEqual(client.hello['pid'], job.proc.pid)
            log, = (root / 'cache/tmp').rglob('sync.jsonl')
            end = time.monotonic() + 8
            while time.monotonic() < end:
                # The next request can write concurrently; only complete lines count.
                sync = [json.loads(line) for line in log.read_bytes().split(b'\n')[:-1]]
                if any(e['event'] == 'request_begin' and e['request_id'] == 3 for e in sync):
                    break
                time.sleep(.02)
            else:
                self.fail('native worker did not enter the intentionally held third capture')
            timing, = [e for e in sync if e['event'] == 'adapter_timing' and e['request_id'] == 1]
            renderer_pid = timing['signed_renderer_pid']
            self.assertGreater(renderer_pid, 0, timing)
            self.assertNotEqual(renderer_pid, job.proc.pid)
            proof['job_member_pids'] = {'worker': job.proc.pid, 'renderer': renderer_pid}
            for pid in (job.proc.pid, renderer_pid):
                # Observation rights only; production teardown owns all termination.
                handle = kernel.OpenProcess(0x100000 | 0x1000, False, pid)
                self.assertTrue(handle, (pid, ctypes.get_last_error()))
                close.callback(kernel.CloseHandle, handle)
                handles[pid] = handle
                member = wintypes.BOOL()
                self.assertTrue(kernel.IsProcessInJob(handle, job._job, ctypes.byref(member)))
                self.assertTrue(member.value, pid)
                self.assertEqual(kernel.WaitForSingleObject(handle, 0), 258, pid)  # WAIT_TIMEOUT
            proof['members_alive_in_job_before_cancel'] = True
            proof['native_held_request_id'] = 3
            proof['cancel_requested_at_monotonic'] = time.monotonic()
            _thread.interrupt_main()
            # Exercise the real blocked receipt wait, not an exception injected
            # into this render thread. Only the coordinator handles the SIGINT.
            return receive(client, *args, **kwargs)

        with ExitStack() as close:
            try:
                previous = signal.signal(signal.SIGINT, interrupt_coordinator)
                close.callback(signal.signal, signal.SIGINT, previous)
                argv = ['vtx', '-t', str(timeline), 'gfx']
                with mock.patch.object(wb._OwnedJob, '_launch', observe_launch), \
                     mock.patch.object(wb._OwnedJob, '_wait_job_empty', observe_settlement), \
                     mock.patch.object(gfx_cef.CefWorkerClient, 'receive_png', receive_or_cancel), \
                     mock.patch.object(sys, 'argv', argv), self.assertRaises(KeyboardInterrupt) as cancelled:
                    main()
                self.assertIs(cancelled.exception, interrupt)
                self.assertIsNone(cancelled.exception.__cause__)
                self.assertTrue(proof['interrupt_handled_on_main'], proof)
                proof['cancel_to_cli_exit_seconds'] = time.monotonic() - proof['cancel_requested_at_monotonic']
                self.assertLess(proof['cancel_to_cli_exit_seconds'], 15,
                                'CLI waited for the 20-second native readiness deadline')
                proof['wait_results'] = {pid: kernel.WaitForSingleObject(handle, 5000)
                                         for pid, handle in handles.items()}
                self.assertEqual(proof['active_processes_after_cleanup'], 0, proof)
                self.assertEqual(list(proof['wait_results'].values()), [0, 0], proof)  # WAIT_OBJECT_0
                self.assertIsNone(owned[0]._job.value, proof)
            finally:
                _json(evidence, proof)
        raise SystemExit(130)
