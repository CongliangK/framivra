"""Windows CEF transport with a real inherited-handle peer (no CEF dependency)."""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest import mock

from tests.gfx._png import png
from vtx.cli import commands
from vtx.render import cef_client, gfx_cef
from vtx.runtime import windows_browser


_PEER = r'''
import msvcrt, os, struct, sys, time
args = dict(arg.removeprefix('--').split('=', 1) for arg in sys.argv[2:] if '=' in arg)
mode = sys.argv[1]
read, write = [int(args['ipc-' + direction + '-handle']) for direction in ('read', 'write')]
for handle in (read, write):
    os.set_handle_inheritable(handle, False)
read = msvcrt.open_osfhandle(read, os.O_RDONLY | os.O_BINARY)
write = msvcrt.open_osfhandle(write, os.O_WRONLY | os.O_BINARY)
print('peer stdout separate from protocol', flush=True)
print('peer stderr separate from protocol', file=sys.stderr, flush=True)
private = os.path.dirname(os.environ['TEMP'])
assert os.path.basename(private).startswith('vtxcef-') and os.path.isdir(os.environ['TEMP'])
assert os.getenv('USERPROFILE') == os.path.join(private, 'home')
assert os.getenv('VTX_TEST_CEF_UNTRUSTED') is None
if mode == 'die':
    os._exit(9)
if mode == 'stall':
    time.sleep(90)
for direction in ('read', 'write'):
    assert args['ipc-' + direction + '-handle'].isdecimal()
width, height, run = 16, 16, 91
hello = struct.pack('<IHHQiIIII7I', 0x56545848, 1, 3 if mode == 'ring' else 2, run,
                    os.getpid(), 4 if mode == 'ring' else 0, width, height, width * height * 4, *([0] * 7))
os.write(write, hello[:23])
time.sleep(.02)
os.write(write, hello[23:])
if mode == 'ring':
    sys.exit(0)

def packet(length):
    data = b''
    while len(data) < length:
        chunk = os.read(read, length - len(data))
        if not chunk:
            raise AssertionError('unexpected EOF in request')
        data += chunk
    return data

requests = [struct.unpack('<IHHIiii40s', packet(64)) for _ in range(2)]
if mode == 'stall-receipt':
    time.sleep(90)
for index, (magic, version, flags, req, frame, w, h, leaf) in enumerate(requests):
    assert (magic, version, flags, w, h) == (0x56545851, 1, 0, width, height)
    assert leaf.rstrip(b'\0') == ('frame%d.png' % index).encode('ascii')
    if mode == 'render':
        assert '--serve' in sys.argv and '--sync' in sys.argv
        assert args['sync-canary'] == 'exact' and args['mode'] == 'a0'
        assert 'ipc-socket' not in args and 'ipc-fd' not in args and 'ozone-platform' not in args
        assert os.path.dirname(args['root-cache-path']) == private
        assert len(args['root-cache-path']) + 60 < len(os.getcwd())
        target = leaf.rstrip(b'\0').decode()
        temp = target + '.tmp.12345.12345'
        with open(temp, 'xb') as file:
            file.write(bytes.fromhex(args['fixture' + str(index)]))
        os.replace(temp, target)
        with open(args['sync-log'], 'a', encoding='utf-8') as file:
            file.write('{"index": %d}\n' % frame)
    rcpt = struct.pack('<IHHQIIiiIIIBBHQQQQQ', 0x56545846, 1, 0, run, 1,
                       req, frame, frame, w, h, w * 4, 1, 1, 0, 0, 0, 0, 0, 0)
    if mode == 'partial':
        os.write(write, rcpt[:17])
        os._exit(7)
    rcpt += bytes(128 - len(rcpt))
    os.write(write, rcpt[:13])
    time.sleep(.02)
    os.write(write, rcpt[13:])
assert os.read(read, 1) == b''
'''


@unittest.skipUnless(sys.platform == 'win32', 'requires real Win32 Jobs and anonymous pipes')
class NativeWindowsPipeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='cef 管道 ')
        self.addCleanup(temp.cleanup)
        root = Path(temp.name).resolve()
        self.ctx = SimpleNamespace(root=root, cache=root / 'cache')
        self.local = root / 'local'
        self.local.mkdir()
        local = mock.patch.object(gfx_cef, '_windows_local_appdata', return_value=self.local)
        local.start()
        self.addCleanup(local.stop)
        self.log = root / 'worker.log'
        # A Windows venv launcher can start a second process. The synthetic
        # native peer must itself own the Job's initial process identity.
        self.python_peer = Path(getattr(sys, '_base_executable', None) or sys.executable)
        ambient = mock.patch.dict(os.environ, VTX_TEST_CEF_UNTRUSTED='not inherited')
        ambient.start()
        self.addCleanup(ambient.stop)

    def _group(self, log, mode):
        return gfx_cef._windows_group(self.ctx, lambda *_: [str(self.python_peer), '-c', _PEER, mode], log)

    @staticmethod
    def _connect(group, timeout=5):
        return cef_client.CefWorkerClient.from_windows_pipes(
            group.pipes, expected_pid=group.proc.pid, expected_wh=(16, 16), ready_timeout=timeout)

    @staticmethod
    def _submit_two(client):
        for index in range(2):
            client.submit_png(index + 10, f'frame{index}.png', request_id=index + 17,
                              expected_wh=(16, 16), timeout=4, worker_relative=True)

    def test_fragmented_hello_and_fifo_receipts_with_separate_diagnostics(self):
        with self.log.open('wb') as log, self._group(log, 'ok') as group:
            run = group.run_dir
            self.assertEqual(group.pipes.child_handles, (None, None))
            with self._connect(group) as client:
                self.assertEqual(client.hello['run_id'], 91)
                with self.assertRaisesRegex(cef_client.CefWorkerError, 'shared-memory'):
                    client.request_slot(10)
                with self.assertRaisesRegex(cef_client.CefWorkerError, 'worker-relative'):
                    client.submit_png(10, 'frame0.png')
                self._submit_two(client)
                self.assertEqual([client.receive_png().header['request_id'] for _ in range(2)], [17, 18])
            self.assertEqual(group.proc.wait(4), 0)
        self.assertFalse(run.exists(), 'scratch retained after proved settlement')
        self.assertIn(b'peer stdout separate from protocol', self.log.read_bytes())
        self.assertIn(b'peer stderr separate from protocol', self.log.read_bytes())

    def test_bad_hello_truncated_receipt_and_worker_death_fail_closed(self):
        cases = [('ring', 'PNG-only'), ('die', 'EOF'), ('partial', 'EOF')]
        for mode, reason in cases:
            with self.subTest(mode=mode), self.log.open('wb') as log:
                with self.assertRaisesRegex(cef_client.CefWorkerError, reason):
                    with self._group(log, mode) as group:
                        run = group.run_dir
                        with self._connect(group) as client:
                            self._submit_two(client)
                            client.receive_png()
                self.assertFalse(run.exists())

    def test_deadline_and_full_request_pipe_do_not_block(self):
        with self.log.open('wb') as log, self._group(log, 'stall') as group:
            run = group.run_dir
            group.pipes.settimeout(.15)
            started = time.monotonic()
            # The peer never reads; this exceeds CreatePipe's default buffer.
            with self.assertRaises(TimeoutError):
                group.pipes.sendall(bytes(1024 * 1024))
            elapsed = time.monotonic() - started
            self.assertGreaterEqual(elapsed, .15)
            self.assertLess(elapsed, 3)
            with self.assertRaisesRegex(cef_client.CefWorkerError, 'Hello.*超时'):
                self._connect(group, timeout=.1)
        self.assertFalse(run.exists())

    def test_render_page_publishes_only_valid_ordered_outputs_and_reports(self):
        original_group = gfx_cef._windows_group
        width = height = 16
        deep = self.ctx.root / ('深' * 38) / ('空' * 42)
        deep.mkdir(parents=True)
        self.ctx.root, self.ctx.cache = deep, deep / 'cache'
        worker = self.python_peer
        self.ctx._gfx_environment = (None, {'backend': 'cef', 'status': 'input-bound'}, worker, 'a0')
        self.ctx.verify_inputs = mock.Mock()
        html = self.ctx.root / '素材 #42 with spaces.html'
        html.touch()
        output = self.ctx.cache / 'frames'
        output.mkdir(parents=True)
        fixtures = [png(width, height, value) for value in (18, 71)]

        def group(ctx, argv, log, *, cancel=None):
            self.ctx.verify_inputs.assert_called_once_with('gfx')

            def python_peer(run, profile):
                args = argv(run, profile)
                self.assertEqual(args[0], str(worker))
                self.assertIn('--url-template=' + html.as_uri() + '?f={f}', args)
                return [str(worker), '-c', _PEER, 'render', *args[1:],
                        *(f'--fixture{i}={data.hex()}' for i, data in enumerate(fixtures))]

            return original_group(ctx, python_peer, log, cancel=cancel)

        with mock.patch.object(gfx_cef, 'cef_config', return_value=(worker, 'a0')), \
                mock.patch.object(gfx_cef, '_windows_group', side_effect=group):
            result = gfx_cef.render_page(html, output, 2, ctx=self.ctx, width=width, height=height)
        self.assertIn('native-exact×2', result)
        self.assertEqual([p.read_bytes() for p in sorted(output.iterdir())], fixtures)
        self.assertFalse(list((self.ctx.cache / 'tmp').iterdir()))
        self.assertFalse(list((self.local / 'Temp').iterdir()))
        report = self.ctx.cache / 'reports' / 'cef'
        pipeline_path, = report.glob('*.pipeline.json')
        pipeline = json.loads(pipeline_path.read_bytes())
        self.assertIs(pipeline['completed'], True)
        self.assertEqual(pipeline['rendered_frames'], [0, 1])
        self.assertEqual(pipeline['output_commits'], {'rename': 2, 'copy': 0})
        receipts_path, = report.glob('*.receipts.json')
        self.assertEqual([r['canary_frame_index'] for r in json.loads(receipts_path.read_bytes())], [0, 1])
        sync_path, = report.glob('*.sync.jsonl')
        self.assertEqual([json.loads(line)['index'] for line in sync_path.read_bytes().splitlines()], [0, 1])

    def test_retargeted_worker_rejected_before_lookup_or_launch(self):
        worker = self.ctx.root / 'canonical.exe'
        self.ctx._gfx_environment = (None, {'backend': 'cef', 'status': 'input-bound'}, worker, 'a0')
        with mock.patch.object(gfx_cef, 'cef_config', return_value=(Path(sys.executable), 'a0')), \
                mock.patch.object(gfx_cef.gfx_cli, 'png_invalid_reason') as lookup, \
                mock.patch.object(gfx_cef, '_windows_group') as launch:
            with self.assertRaisesRegex(cef_client.CefWorkerError, 'pinned worker identity'):
                gfx_cef.render_page('unused.html', self.ctx.cache, 1, ctx=self.ctx, width=16, height=16)
        lookup.assert_not_called()
        launch.assert_not_called()
        self.assertFalse(self.ctx.cache.exists())

    def test_interrupted_page_pool_cancels_blocked_receipt_and_queued_work(self):
        cancel, awaiting_receipt = threading.Event(), threading.Event()
        started_jobs, runs = [], []
        interrupt = KeyboardInterrupt('original CLI interrupt')

        def active(index):
            started_jobs.append(index)
            with self.log.open('wb') as log, gfx_cef._windows_group(
                    self.ctx, lambda *_: [str(self.python_peer), '-c', _PEER, 'stall-receipt'], log,
                    cancel=cancel) as group:
                runs.append(group.run_dir)
                with self._connect(group) as client:
                    self._submit_two(client)
                    awaiting_receipt.set()
                    client.receive_png()

        def interrupt_wait(*args, **kwargs):
            self.assertTrue(awaiting_receipt.wait(4))
            raise interrupt

        t0 = time.monotonic()
        with mock.patch.object(commands, 'wait', side_effect=interrupt_wait):
            with self.assertRaisesRegex(KeyboardInterrupt, 'original CLI interrupt') as context:
                commands._run_windows_cef_blocks(active, [0, 1], 1, cancel)
        self.assertIs(context.exception, interrupt)
        self.assertTrue(cancel.is_set())
        self.assertEqual(started_jobs, [0])
        self.assertLess(time.monotonic() - t0, 4)
        self.assertTrue(runs and not runs[0].exists())

    def test_unconfirmed_job_cleanup_retains_scratch_and_primary_error(self):
        close, original_group = windows_browser._OwnedJob.close, gfx_cef._windows_group
        worker = self.python_peer
        self.ctx._gfx_environment = (None, {'backend': 'cef', 'status': 'input-bound'}, worker, 'a0')
        self.ctx.verify_inputs = mock.Mock()

        def late_failure(job):
            # Settle the real test process first; simulate lost settlement proof.
            close(job)
            raise OSError('accounting unavailable')

        def group(ctx, argv, log, *, cancel=None):
            return original_group(ctx, lambda *_: [str(worker), '-c', _PEER, 'partial'], log, cancel=cancel)

        with mock.patch.object(windows_browser._OwnedJob, 'close', late_failure), \
                mock.patch.object(gfx_cef, 'cef_config', return_value=(worker, 'a0')), \
                mock.patch.object(gfx_cef, '_windows_group', side_effect=group):
            with self.assertRaisesRegex(cef_client.CefWorkerError, 'EOF.*settlement unconfirmed') as context:
                gfx_cef.render_page(self.ctx.root / 'page.html', self.ctx.cache / 'frames', 2,
                                    ctx=self.ctx, width=16, height=16)
        self.assertIsInstance(context.exception.__cause__, windows_browser.JobSettlementError)
        run, = (self.ctx.cache / 'tmp').iterdir()
        profile, = (self.local / 'Temp').iterdir()
        self.assertTrue(run.is_dir())
        self.assertTrue((profile / 'profile').is_dir())
        self.assertIn(str(profile), str(context.exception.__cause__))


if __name__ == '__main__':
    unittest.main()
