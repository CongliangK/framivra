"""CEF Alpha production route + real native Mode N/opaque/transparent pixels.

VTX_TEST_CEF_WORKER enables real CEF (no Chrome/IME service involved).
VTX_CEF_EVIDENCE_DIR optionally retains the complete fixture and native logs.
"""
from tests.execution.test_execution_events import Buffer
from tests.gfx._environment import bind_environment
from vtx import events, gfx_cli, gfx_render

import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from vtx import cef_client, cli, gfx_backend, gfx_cef, hygiene
from vtx.cache_state import receipt_fingerprint
from vtx.composite import RenderPlan
from vtx.infra import Ctx
from tests.gfx.test_cef_frame_sync import decode_png_rgba
from tests.gfx._png import write_png
from vtx.frame_source import PngSequence, png_source
from vtx.hygiene import gfx_key

ROOT = Path(__file__).resolve().parents[2]
WORKER = os.environ.get('VTX_TEST_CEF_WORKER', '')

# Intentionally NO __vtx_render or __vtx_originals. All four corners have
# differing pixels, not equal to adjacent rows: neighbor-copy would fail.
PAGE = '''<!doctype html><style>
html,body{margin:0;width:100%;height:100%;overflow:hidden;background:transparent}
i{position:fixed;width:1px;height:1px;display:block}
</style><script>
let f=Number(new URLSearchParams(location.search).get('f'));
if(f%2)document.documentElement.style.background='rgb(17,33,65)';
for(let y=0;y<48;y++)for(let x=0;x<64;x++){
 if(x>9 && x<54 && y>9 && y<38)continue;
 let e=document.createElement('i');
 e.style.cssText=`left:${x}px;top:${y}px;background:rgb(${(x+f)%256},${y+9},${x^y})`;
 document.documentElement.appendChild(e);
}
</script>'''


def project(root, n=3):
    (root / 'page.html').write_text(PAGE)
    p = root / 'timeline.json'
    p.write_text(json.dumps(dict(version=2,
        project=dict(fps=30, width=64, height=48, duration_frames=n), sources=[],
        subtitles=[], audio_tracks=[], video_tracks=[dict(name='gfx', kind='gfx',
            clips=[dict(src='page.html', dst_in=0, dur=n)])])))
    return p


class Routing(unittest.TestCase):
    def setUp(self):
        from tests.gfx._environment import bind_environment
        bind_environment(self)
        self.tmp = tempfile.TemporaryDirectory(prefix='cef_alpha_')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.p = project(self.root)
        self.worker = self.root / 'worker'
        self.worker.write_text('#!/bin/sh\nexit 99\n')
        self.worker.chmod(0o755)
        self.env = mock.patch.dict(os.environ, VTX_GFX='cdp', VTX_GFX_CACHE_NS='',
                                   VTX_CEF_WORKER=str(self.worker), VTX_CEF_MODE='a0')
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_unknown_backend_fail_closed(self):
        for value in ('electron-typo', 'typo', ''):
            with mock.patch.dict(os.environ, VTX_GFX=value):
                with self.assertRaisesRegex(ValueError, 'no silent fallback'):
                    Ctx(self.p)

    def test_missing_worker_fails_before_render(self):
        with mock.patch.dict(os.environ, VTX_GFX='cef', VTX_CEF_WORKER=''):
            with self.assertRaisesRegex(ValueError, 'VTX_CEF_WORKER'):
                Ctx(self.p)

    def test_backend_namespace_and_receipt_identity(self):
        base = Ctx(self.p)
        cl = base.tl['video_tracks'][0]['clips'][0]
        base_key = hygiene.gfx_key(base, cl)
        with mock.patch.dict(os.environ, VTX_GFX='cef'):
            cef = Ctx(self.p)
            self.assertNotEqual(base_key, hygiene.gfx_key(cef, cl))
            self.assertIn('gfx_exp/cef-alpha/', str(hygiene.gfx_dir(cef, cl)))
            self.assertNotEqual(base.input_fingerprint, cef.input_fingerprint)
            first = receipt_fingerprint(cef)
            with mock.patch.dict(os.environ, VTX_CEF_MODE='a1'):
                self.assertNotEqual(first, receipt_fingerprint(cef))
            with mock.patch.dict(os.environ, VTX_GFX_CACHE_NS='experiment2'):
                self.assertNotEqual(first, receipt_fingerprint(cef))
            self.worker.write_text('#!/bin/sh\nexit 98\n')
            self.assertNotEqual(first, receipt_fingerprint(cef))

    def test_downstream_segment_keys_include_namespace_route(self):
        plan = SimpleNamespace(c=SimpleNamespace(cache=self.root / 'cache'),
                               W=64, H=48, draft=False)
        from vtx.frame_source import png_source
        c = Ctx(self.p)
        clip = c.tl['video_tracks'][0]['clips'][0]
        with mock.patch.dict(os.environ, VTX_GFX_CACHE_NS='one'):
            source = png_source(c, clip)
        layer = dict(gfx=True, source=source, cl={}, x=0, y=0, w2=64, h2=48)
        k = RenderPlan._seg_content_key(plan, 0, 3, [layer], False, None)
        with mock.patch.dict(os.environ, VTX_GFX_CACHE_NS='two'):
            layer['source'] = png_source(c, clip)
        self.assertEqual(source.directory.name, layer['source'].directory.name)
        self.assertNotEqual(k, RenderPlan._seg_content_key(plan, 0, 3, [layer], False, None))

    def test_cef_never_falls_back_after_native_failure(self):
        with mock.patch.dict(os.environ, VTX_GFX='cef'), \
                mock.patch.object(gfx_cef, 'render_page', side_effect=RuntimeError('native failed')), \
                mock.patch.object(cli.gfx_cli, 'render_cli') as fallback, \
                mock.patch('vtx.gfx_render.render_page') as cdp:
            c = Ctx(self.p)
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
                cli.gfx(c)
            fallback.assert_not_called()
            cdp.assert_not_called()
            rec = json.loads((self.root / 'out/receipt.json').read_text())
            self.assertFalse(rec['stages']['gfx']['ok'])

    def test_dry_cef_does_not_start_worker(self):
        with mock.patch.dict(os.environ, VTX_GFX='cef'), \
                mock.patch.object(gfx_cef, 'render_page') as render:
            c = Ctx(self.p)
            c.dry = True
            with contextlib.redirect_stdout(io.StringIO()):
                cli.gfx(c)
            render.assert_not_called()
            self.assertFalse(c.cache.exists())

@unittest.skipUnless(hasattr(cef_client.socket, "SCM_RIGHTS"), "requires POSIX SCM_RIGHTS")
class ClientHelloOwnership(unittest.TestCase):
    """Real client parser/validator with fake socket, fd and bytearray mapping only."""

    def hello(self, magic=cef_client.HELLO_MAGIC, version=cef_client.PROTOCOL_VERSION):
        return struct.pack(cef_client._HELLO_FMT, magic, version, 0, 123, 45, 4, 1, 1, 4,
                           *([0] * 7))

    def rights(self, *fds):
        return [(cef_client.socket.SOL_SOCKET, cef_client.socket.SCM_RIGHTS,
                 struct.pack(f'{len(fds)}i', *fds))]

    @contextlib.contextmanager
    def resources(self, packets):
        class Mapping(bytearray):
            pass
        size = 384 + 4 * 192
        if sys.platform == 'darwin':
            page = cef_client.mmap.PAGESIZE
            size = ((size + page - 1) // page) * page
        mapping = Mapping(size)
        mapping.close = mock.Mock()
        struct.pack_into('<IIHH', mapping, 0, cef_client.RING_MAGIC, cef_client.RING_MAGIC, 1, 4)
        struct.pack_into('<IIII', mapping, 12, 1, 1, 4, 192)
        struct.pack_into('<Q', mapping, 32, 123)
        sock = mock.Mock()
        sock.recvmsg.side_effect = packets
        with mock.patch.object(cef_client.os, 'close') as close, \
                mock.patch.object(cef_client.os, 'fstat', return_value=SimpleNamespace(st_size=size)) as stat, \
                mock.patch.object(cef_client.mmap, 'mmap', return_value=mapping) as mmap_call, \
                mock.patch.object(cef_client.socket, 'socket', side_effect=AssertionError('real socket')), \
                mock.patch.object(subprocess, 'Popen', side_effect=AssertionError('real process')):
            yield SimpleNamespace(sock=sock, mapping=mapping, close=close, stat=stat, mmap=mmap_call)

    def test_complete_fragmented_hello_transfers_one_fd_once(self):
        data = self.hello()
        for lengths in ((64,), (1, 63), (17, 20, 27)):
            for fd_part in range(len(lengths)):
                with self.subTest(lengths=lengths, fd_part=fd_part):
                    packets, offset = [], 0
                    for index, length in enumerate(lengths):
                        packets.append((data[offset:offset + length], self.rights(888) if index == fd_part else [], 0, None))
                        offset += length
                    with self.resources(packets) as r:
                        client = cef_client.CefWorkerClient.from_socket(r.sock)
                        self.assertEqual(client.hello['run_id'], 123)
                        r.close.assert_not_called(); r.sock.close.assert_not_called()
                        r.mapping.close.assert_not_called()
                        client.close(); client.close()
                        r.close.assert_called_once_with(888)
                        r.mapping.close.assert_called_once_with(); r.sock.close.assert_called_once_with()
                        self.assertEqual([c.args[0] for c in r.sock.recvmsg.call_args_list],
                                         [64 - sum(lengths[:i]) for i in range(len(lengths))])

    def test_hello_has_one_deadline_and_restores_socket_timeout(self):
        data = self.hello()
        packets = [(data[:1], self.rights(888), 0, None), (data[1:], [], 0, None)]
        with self.resources(packets) as r, \
                mock.patch.object(cef_client.time, 'monotonic', side_effect=[0, .25, 1.25]):
            with self.assertRaisesRegex(cef_client.CefWorkerError, 'Hello.*超时'):
                cef_client.CefWorkerClient.from_socket(r.sock, ready_timeout=1)
            self.assertEqual(r.sock.recvmsg.call_count, 1)
            r.close.assert_called_once_with(888); r.sock.close.assert_called_once_with()
            r.mmap.assert_not_called()
        with self.resources(packets) as r, \
                mock.patch.object(cef_client.time, 'monotonic', side_effect=[0, .25, .5, .5]):
            client = cef_client.CefWorkerClient.from_socket(r.sock, ready_timeout=1)
            self.assertEqual(r.sock.settimeout.call_args_list,
                             [mock.call(1), mock.call(.75), mock.call(.5), mock.call(1)])
            client.close()
        with self.resources(packets) as r:
            client = cef_client.CefWorkerClient.from_socket(r.sock, ready_timeout=None)
            self.assertEqual(r.sock.settimeout.call_args_list, [mock.call(None), mock.call(None)])
            client.close()

    def test_rejections_release_every_delivered_fd_and_partial_mapping(self):
        data, rights = self.hello(), self.rights(888)
        cases = [
            ('eof', [(data[:1], rights, 0, None), (b'', [], 0, None)], [888]),
            ('timeout', [(data[:1], rights, 0, None), cef_client.socket.timeout()], [888]),
            ('reset', [(data[:1], rights, 0, None), ConnectionResetError()], [888]),
            ('magic', [(self.hello(magic=0), rights, 0, None)], [888]),
            ('version', [(self.hello(version=0), rights, 0, None)], [888]),
            ('missing', [(data, [], 0, None)], []),
            ('extra', [(data, self.rights(888, 889), 0, None)], [888, 889]),
            ('later_extra', [(data[:1], rights, 0, None), (data[1:], self.rights(889), 0, None)], [888, 889]),
            ('truncated', [(data, rights, cef_client.socket.MSG_CTRUNC, None)], [888]),
            ('malformed', [(data, [(rights[0][0], rights[0][1], rights[0][2] + b'x')], 0, None)], [888]),
            ('malformed_then_fd', [(data, [(rights[0][0], rights[0][1], b'x'), *rights], 0, None)], [888]),
            *[(name, [(data, rights, 0, None)], [888]) for name in ('stat', 'map', 'ring')],
        ]
        for name, packets, fds in cases:
            with self.subTest(case=name), self.resources(packets) as r:
                if name == 'stat': r.stat.side_effect = OSError('stat')
                if name == 'map': r.mmap.side_effect = OSError('map')
                if name == 'ring': struct.pack_into('<I', r.mapping, 4, 0)
                with self.assertRaises((cef_client.CefWorkerError, OSError)):
                    cef_client.CefWorkerClient.from_socket(r.sock)
                self.assertCountEqual(r.close.call_args_list, [mock.call(fd) for fd in fds])
                r.sock.close.assert_called_once_with()
                self.assertEqual(r.mapping.close.call_count, 1 if name == 'ring' else 0)

    def test_close_attempts_fd_even_if_other_resource_close_raises(self):
        for failing in ('socket', 'mapping'):
            with self.subTest(failing=failing), self.resources([(self.hello(), self.rights(888), 0, None)]) as r:
                client = cef_client.CefWorkerClient.from_socket(r.sock)
                (r.sock.close if failing == 'socket' else r.mapping.close).side_effect = OSError(failing)
                with self.assertRaisesRegex(OSError, failing): client.close()
                client.close()
                r.close.assert_called_once_with(888)
                r.sock.close.assert_called_once_with(); r.mapping.close.assert_called_once_with()

    def prepare_slot(self, r, *, header=None, notice=None, seq=5):
        fields = dict.fromkeys(cef_client._HEADER_FIELDS, 0)
        fields.update(magic=cef_client.SLOT_MAGIC, protocol_version=1, run_id=123,
                      request_id=7, target_frame_index=3, canary_frame_index=3,
                      width=1, height=1, stride=4, pixel_format=1, color_space=1)
        fields.update(header or {})
        struct.pack_into(cef_client._HEADER_FMT, r.mapping, 384,
                         *(fields[k] for k in cef_client._HEADER_FIELDS))
        struct.pack_into('<II', r.mapping, 128, 2, seq)
        r.mapping[512:516] = b'abcd'
        n = dict(magic=cef_client.NOTICE_MAGIC, version=1, status=0,
                 request_id=7, frame=3, slot=0, seq=seq)
        n.update(notice or {})
        r.sock.recv.return_value = struct.pack(cef_client._NOTICE_FMT, n['magic'], n['version'],
            n['status'], n['request_id'], n['frame'], n['slot'], b'\0' * 3, n['seq'], 0)

    def test_slot_snapshot_and_release_bind_generation_including_wraparound(self):
        for seq in (0, 5, 0xffffffff):
            with self.subTest(seq=seq), self.resources([(self.hello(), self.rights(888), 0, None)]) as r:
                self.prepare_slot(r, seq=seq)
                with cef_client.CefWorkerClient.from_socket(r.sock) as client:
                    frame = client.request_slot(3, request_id=7)
                    self.assertEqual((frame.data, frame.seq), (b'abcd', seq))
                    frame.release(); frame.release()
                    self.assertEqual(client._gate(0), (0, seq))
                    self.assertEqual(client.stats()['released'], 1)
                r.close.assert_called_once_with(888)

    def test_slot_rejects_unbound_notice_header_and_capacity_without_releasing(self):
        cases = [('notice_' + k, {}, {k: v}) for k, v in
                 [('request_id', 99), ('frame', 4), ('seq', 6), ('slot', 4), ('magic', 0), ('version', 0)]]
        cases += [('header_' + k, {k: v}, {}) for k, v in
                  [('request_id', 99), ('target_frame_index', 4), ('canary_frame_index', -1),
                   ('status_code', 2), ('width', 0), ('height', 0), ('stride', 8),
                   ('pixel_format', 2), ('color_space', 2), ('run_id', 999)]]
        cases += [('oversize', {'width': 100, 'height': 100, 'stride': 400}, {}),
                  ('invalid_error_slot', {}, {'status': 1}),
                  ('unknown_status', {}, {'status': 99, 'slot': 255})]
        for name, header, notice in cases:
            with self.subTest(case=name), self.resources([(self.hello(), self.rights(888), 0, None)]) as r:
                self.prepare_slot(r, header=header, notice=notice)
                with cef_client.CefWorkerClient.from_socket(r.sock) as client:
                    held = cef_client.SlotFrame(client, 0, 5, {}, b'', 0)
                    with self.assertRaises(cef_client.CefWorkerError) as error, held:
                        client.request_slot(3, request_id=7)
                    self.assertIsNone(error.exception.status_code)
                    self.assertTrue(client._closed)
                    self.assertEqual(struct.unpack_from('<II', r.mapping, 128), (2, 5))
                    self.assertEqual(struct.unpack_from('<Q', r.mapping, 56)[0], 0)
                r.close.assert_called_once_with(888); r.mapping.close.assert_called_once_with()

    def test_slot_detects_snapshot_change_and_refuses_aba_release(self):
        for fault in ('state', 'seq', 'short', 'release_seq', 'release_close', 'body_error'):
            with self.subTest(fault=fault), self.resources([(self.hello(), self.rights(888), 0, None)]) as r:
                self.prepare_slot(r)
                with cef_client.CefWorkerClient.from_socket(r.sock) as client:
                    if fault in ('release_seq', 'release_close', 'body_error'):
                        held = client.request_slot(3, request_id=7)
                        struct.pack_into('<I', r.mapping, 132, 6)
                        if fault == 'release_close':
                            r.mapping.close.side_effect = OSError('mapping close failed')
                        if fault == 'body_error':
                            error = RuntimeError('body failure')
                            with self.assertRaises(RuntimeError) as caught, held:
                                raise error
                            self.assertIs(caught.exception, error)
                        else:
                            with self.assertRaisesRegex(cef_client.CefWorkerError, '槽世代已变'):
                                held.release()
                        held.release()  # closed session: no mapping access, no second error
                        r.close.assert_called_once_with(888)
                    else:
                        def read(mapping, key):
                            data = bytearray.__getitem__(mapping, key)
                            if isinstance(key, slice) and key.start == 512:
                                if fault == 'short': return data[:3]
                                struct.pack_into('<I', mapping, 128 if fault == 'state' else 132,
                                                 0 if fault == 'state' else 6)
                            return data
                        with mock.patch.object(type(r.mapping), '__getitem__', read), \
                                self.assertRaises(cef_client.CefWorkerError):
                            client.request_slot(3, request_id=7)
                    self.assertTrue(client._closed)
                    self.assertEqual(struct.unpack_from('<Q', r.mapping, 56)[0], 0)
                    if fault in ('seq', 'release_seq', 'release_close', 'body_error'):
                        self.assertEqual(struct.unpack_from('<II', r.mapping, 128), (2, 6))

    def test_slot_worker_errors_preserve_held_slots_but_transport_timeout_closes(self):
        for status in (1, 2, 3, 4):
            with self.subTest(status=status), self.resources([(self.hello(), self.rights(888), 0, None)]) as r:
                self.prepare_slot(r)
                with cef_client.CefWorkerClient.from_socket(r.sock) as client:
                    held = client.request_slot(3, request_id=7)
                    self.prepare_slot(r, notice={'status': status, 'slot': 255, 'request_id': 8, 'frame': 4})
                    with self.assertRaises(cef_client.CefWorkerError) as error:
                        client.request_slot(4, request_id=8)
                    self.assertEqual(error.exception.status_code, status)
                    self.assertFalse(client._closed)
                    held.release()
                    self.assertEqual(client.stats()['released'], 1)
                    self.prepare_slot(r, seq=6, header={'request_id': 9, 'target_frame_index': 4, 'canary_frame_index': 4},
                                      notice={'request_id': 9, 'frame': 4})
                    with client.request_slot(4, request_id=9) as frame:
                        self.assertEqual(frame.header['target_frame_index'], 4)
        for error in (cef_client.socket.timeout(), KeyboardInterrupt(), SystemExit(), MemoryError()):
            with self.subTest(error=type(error).__name__), \
                    self.resources([(self.hello(), self.rights(888), 0, None)]) as r:
                self.prepare_slot(r)
                r.sock.recv.side_effect = [error, r.sock.recv.return_value]
                with cef_client.CefWorkerClient.from_socket(r.sock) as client:
                    expected = cef_client.CefWorkerError if isinstance(error, OSError) else type(error)
                    with self.assertRaises(expected) as caught: client.request_slot(3, request_id=7)
                    if expected is not cef_client.CefWorkerError:
                        self.assertIs(caught.exception, error)
                    with self.assertRaisesRegex(cef_client.CefWorkerError, 'closed'):
                        client.request_slot(4, request_id=8)
                    self.assertEqual(r.sock.recv.call_count, 1)
                    self.assertEqual(r.sock.sendall.call_count, 1)
                    r.close.assert_called_once_with(888)

    def test_ring_binds_hello_geometry_and_slot_capacity_before_slot_reads(self):
        for offset, value in ((12, 0), (12, 2), (16, 2), (20, 8), (24, 256)):
            with self.subTest(offset=offset), self.resources([(self.hello(), self.rights(888), 0, None)]) as r:
                struct.pack_into('<I', r.mapping, offset, value)
                with self.assertRaises(cef_client.CefWorkerError):
                    cef_client.CefWorkerClient.from_socket(r.sock)
                r.mapping.close.assert_called_once_with(); r.close.assert_called_once_with(888)
                r.sock.recv.assert_not_called()

    def test_regular_receipt_uses_same_exact_reader_without_ancillary(self):
        sock = mock.Mock()
        sock.recv.side_effect = [b'a', b'bc']
        self.assertEqual(cef_client.CefWorkerClient._recv_exact(sock, 3, 'receipt'), b'abc')
        sock.recvmsg.assert_not_called()
        self.assertEqual(sock.recv.call_args_list, [mock.call(3), mock.call(2)])


@unittest.skipUnless(WORKER and Path(WORKER).is_file(), 'requires VTX_TEST_CEF_WORKER')
class NativeProduction(unittest.TestCase):
    def _assert_structure_css_veto(self, name, css):
        from vtx.cef_client import CefWorkerError
        from vtx.proc_supervisor import SupervisedProcessGroup
        with tempfile.TemporaryDirectory(prefix=f'cef_dom_{name}_') as td:
            root = Path(td).resolve()
            p = project(root, n=1)
            page = root / 'page.html'
            page.write_text('''<!doctype html><html><head><style>
html,body{margin:0;width:100%;height:100%;overflow:hidden;background:transparent}
#content{position:fixed;inset:0}''' + css + '''
</style></head><body><div id="content"></div></body></html>''')
            env = dict(os.environ, VTX_GFX='cef', VTX_CEF_WORKER=WORKER,
                       VTX_CEF_MODE='a0', VTX_GFX_CACHE_NS='')
            with mock.patch.dict(os.environ, env):
                c = Ctx(p)
                fds, worker_env = [], dict(os.environ)
                def argv(run, profile):
                    fd = os.open(run, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                    fds.append(fd)
                    worker_env['TMPDIR'] = str(run) if sys.platform == 'darwin' else f'/proc/{os.getpid()}/fd/{fd}'
                    return [WORKER, '--mode=a0', '--ozone-platform=headless',
                            f'--url={page.as_uri()}?f=0',
                            '--width=64', '--height=48', '--begin-frames=12',
                            '--out-png=baseline.png', '--out-json=baseline.json',
                            f'--root-cache-path={profile}']
                try:
                    with (root/'baseline.worker.log').open('wb') as log:
                        with SupervisedProcessGroup(c, argv, backend_id='cef_dom_baseline',
                                                    env=worker_env, stdout=log, stderr=log) as group:
                            self.assertEqual(group.proc.wait(timeout=60), 0)
                            shutil.copyfile(group.run_dir/'baseline.png', root/'baseline.png')
                finally:
                    for fd in fds:
                        os.close(fd)
                w, h, baseline = decode_png_rgba(root/'baseline.png')
                self.assertEqual((w, h), (64, 48))
                self.assertEqual(baseline, bytes((17, 231, 47, 255)) * (w * h))
                out = hygiene.gfx_dir(c, c.tl['video_tracks'][0]['clips'][0])
                out.mkdir(parents=True)
                with self.assertRaises(CefWorkerError):
                    gfx_cef.render_page(page, out, 1, ctx=c, width=w, height=h)
                self.assertFalse((out/'f0000.png').exists(), 'vetoed pixels published')
                events = [json.loads(line) for path in (c.cache/'reports/cef').glob('*.sync.jsonl')
                          for line in path.read_text().splitlines()]
                self.assertTrue(any(e['event'] == 'exact_first_verified' for e in events))
                self.assertTrue(any(e['event'] == 'exact_baseline_veto_armed' and
                                    e['role'] == 'equality_veto_only' and
                                    e['restoration_source'] is False for e in events))
                self.assertTrue(any(e['event'] == 'exact_baseline_mismatch' and
                                    e['reason'] == 'probe_content_perturbation' for e in events))
                self.assertFalse(any(e.get('to') == 'S5_ONPAINT_VERIFIED' for e in events))
                self.assertTrue(any(e['event'] == 'request_end' and e['status'] == 'failed'
                                    for e in events))
                receipts = [json.loads(path.read_text())
                            for path in (c.cache/'reports/cef').glob('*.receipts.json')]
                self.assertEqual(receipts, [[]])
                self.assertFalse(any((c.cache/'tmp').iterdir()), 'supervised worker leaked')
            evidence = os.environ.get('VTX_CEF_EVIDENCE_DIR')
            if evidence:
                shutil.copytree(root, Path(evidence)/f'dom-{name}', dirs_exist_ok=True)

    def test_structure_css_body_last_child_is_vetoed(self):
        self._assert_structure_css_veto('last_child',
            '#content{background:rgb(231,17,31)}'
            'body:last-child #content{background:rgb(17,231,47)}')

    def test_structure_css_html_has_third_child_is_vetoed(self):
        self._assert_structure_css_veto('has_third_child',
            '#content{background:rgb(17,231,47)}'
            'html:has(> :nth-child(3)) #content{background:rgb(231,17,31)}')

    # ---- #153：同步 JS 异常的稳定错误帧不得进入 S5（生产 render_page 面）----

    def _js_error_page(self, source):
        # 红底错误帧形状：异常打断目标绘制前，#content 维持 CSS 红。
        return ('<!doctype html><html><head><style>\n'
                'html,body{margin:0;width:100%;height:100%;overflow:hidden;'
                'background:transparent}\n'
                '#content{position:fixed;inset:0;background:rgb(231,17,31)}\n'
                '</style></head><body><div id="content"></div>\n'
                f'<script>{source}</script></body></html>')

    def test_sync_throw_error_frame_is_vetoed(self):
        # issue #153 原始形状：绿色目标绘制之前同步 throw，稳定红色错误
        # 底图（3072/3072）曾被 exact 验收接受进入 S5。宿主侧结构证据 =
        # 既有握手/状态机通道的 JS_ERR 显式失败；错误帧必须被拒，不得
        # 发布像素、不得进入 S5_ONPAINT_VERIFIED。
        from vtx.cef_client import CefWorkerError
        with tempfile.TemporaryDirectory(prefix='cef_js_throw_') as td:
            root = Path(td).resolve()
            p = project(root, n=1)
            page = root / 'page.html'
            page.write_text(self._js_error_page(
                "throw Error('CEF_153_SYNC_THROW');"
                "document.querySelector('#content').style.background="
                "'rgb(17,231,47)';"))
            env = dict(os.environ, VTX_GFX='cef', VTX_CEF_WORKER=WORKER,
                       VTX_CEF_MODE='a0', VTX_GFX_CACHE_NS='')
            with mock.patch.dict(os.environ, env):
                c = Ctx(p)
                out = hygiene.gfx_dir(c, c.tl['video_tracks'][0]['clips'][0])
                out.mkdir(parents=True)
                with self.assertRaises(CefWorkerError) as caught:
                    gfx_cef.render_page(page, out, 1, ctx=c, width=64, height=48)
                self.assertIn('JS_ERR', str(caught.exception))
                self.assertFalse((out / 'f0000.png').exists(),
                                 'JS-error frame pixels published')
                events = [json.loads(line)
                          for path in (c.cache / 'reports/cef').glob('*.sync.jsonl')
                          for line in path.read_text().splitlines()]
                self.assertFalse(any(e.get('to') == 'S5_ONPAINT_VERIFIED'
                                     for e in events), 'error frame verified')
                failed = [e for e in events if e['event'] == 'request_end'
                          and e.get('status') == 'failed']
                self.assertTrue(failed, 'request must fail explicitly')
                self.assertTrue(any(e.get('reason', '').startswith('JS_ERR')
                                    for e in failed), failed)
                receipts = [json.loads(path.read_text())
                            for path in (c.cache / 'reports/cef').glob('*.receipts.json')]
                self.assertEqual(receipts, [[]])
                self.assertFalse(any((c.cache / 'tmp').iterdir()),
                                 'supervised worker leaked')
            evidence = os.environ.get('VTX_CEF_EVIDENCE_DIR')
            if evidence:
                shutil.copytree(root, Path(evidence) / 'js-sync-throw',
                                dirs_exist_ok=True)

    def test_handled_exceptions_are_not_rejected(self):
        # 反例（不得误拒）：页面自行 try/catch 的同步异常、已处理的
        # Promise rejection、以及 console.error 文字，都不是失败证据；
        # 目标绿帧必须正常验收进入 S5。
        with tempfile.TemporaryDirectory(prefix='cef_js_handled_') as td:
            root = Path(td).resolve()
            p = project(root, n=1)
            page = root / 'page.html'
            page.write_text(self._js_error_page(
                "try { throw Error('CAUGHT_SYNC'); } catch (e) {}\n"
                "Promise.reject(Error('CAUGHT_REJECTION')).catch(() => {});\n"
                "console.error('JS_ERR hosterror uncaught');\n"
                "document.querySelector('#content').style.background="
                "'rgb(17,231,47)';"))
            env = dict(os.environ, VTX_GFX='cef', VTX_CEF_WORKER=WORKER,
                       VTX_CEF_MODE='a0', VTX_GFX_CACHE_NS='')
            with mock.patch.dict(os.environ, env):
                c = Ctx(p)
                out = hygiene.gfx_dir(c, c.tl['video_tracks'][0]['clips'][0])
                out.mkdir(parents=True)
                gfx_cef.render_page(page, out, 1, ctx=c, width=64, height=48)
                w, h, rgba = decode_png_rgba(out / 'f0000.png')
                self.assertEqual((w, h), (64, 48))
                self.assertEqual(rgba, bytes((17, 231, 47, 255)) * (w * h))
                events = [json.loads(line)
                          for path in (c.cache / 'reports/cef').glob('*.sync.jsonl')
                          for line in path.read_text().splitlines()]
                self.assertEqual(sum(e.get('to') == 'S5_ONPAINT_VERIFIED'
                                     for e in events), 1)
                self.assertFalse(any(e['event'] == 'request_end'
                                     and e.get('status') == 'failed'
                                     for e in events))
                self.assertFalse(any((c.cache / 'tmp').iterdir()),
                                 'unsigned browser leaked')
            evidence = os.environ.get('VTX_CEF_EVIDENCE_DIR')
            if evidence:
                shutil.copytree(root, Path(evidence) / 'js-handled',
                                dirs_exist_ok=True)

    def test_large_blurred_page_does_not_certify_initial_blank(self):
        # Real original-page failure shape: full-size opaque background behind
        # large software-rasterized blurred gradients. NO page render/originals
        # hooks; capture may first receive several identical transparent paints.
        with tempfile.TemporaryDirectory(prefix='cef_blur_') as td:
            root = Path(td).resolve()
            p = project(root, n=1)
            data = json.loads(p.read_text())
            data['project'].update(width=1920, height=1080)
            p.write_text(json.dumps(data))
            (root/'page.html').write_text('''<!doctype html><html><head><style>
*{margin:0;padding:0;box-sizing:border-box}
html,body{width:1920px;height:1080px;background:transparent;overflow:hidden}
#bg{position:absolute;inset:0;background:#faf9f6}
.blob{position:absolute;width:980px;height:980px;border-radius:50%;filter:blur(150px);opacity:.10}
.b1{left:-220px;top:-280px;background:radial-gradient(circle,#c8a24a,transparent 65%)}
.b2{right:-260px;bottom:-320px;background:radial-gradient(circle,#d97706,transparent 65%);opacity:.07}
</style></head><body><div id="bg"></div><div class="blob b1"></div><div class="blob b2"></div></body></html>''')
            env = dict(os.environ, VTX_GFX='cef', VTX_CEF_WORKER=WORKER,
                       VTX_CEF_MODE='a0', VTX_GFX_CACHE_NS='')
            with mock.patch.dict(os.environ, env):
                c = Ctx(p)
                out = root/'frames'
                out.mkdir()
                gfx_cef.render_page(root/'page.html', out, 1, ctx=c,
                                    width=1920, height=1080)
                w, h, rgba = decode_png_rgba(out/'f0000.png')
                self.assertEqual((w,h), (1920,1080))
                self.assertEqual(rgba[3::4], b'\xff' * (w*h), 'initial blank accepted')
                self.assertGreater(len(set(zip(rgba[0::4],rgba[1::4],rgba[2::4]))), 20)
                events = [json.loads(line) for path in (c.cache/'reports/cef').glob('*.sync.jsonl')
                          for line in path.read_text().splitlines()]
                self.assertEqual(sum(e.get('to') == 'S5_ONPAINT_VERIFIED' for e in events), 1)
                self.assertEqual(sum(e['event'] == 'exact_baseline_equal' and
                                     e['compared_bytes'] == w*h*4 for e in events), 1)
                self.assertFalse(any((c.cache/'tmp').iterdir()), 'unsigned browser leaked')
            evidence = os.environ.get('VTX_CEF_EVIDENCE_DIR')
            if evidence:
                shutil.copytree(root, Path(evidence)/'large-blur', dirs_exist_ok=True)

    def test_page_protocol_failures_are_native_timeouts(self):
        import socket
        from vtx.cef_client import CefWorkerClient, CefWorkerError
        from vtx.proc_supervisor import SupervisedProcessGroup
        with tempfile.TemporaryDirectory(prefix='cef_protocol_') as td:
            root = Path(td).resolve()
            p = project(root)
            c = Ctx(p)
            for mode, fault in [('p', 'none'), ('n', 'stale_canary'), ('n', 'no_ready')]:
                with self.subTest(mode=mode, fault=fault):
                    fds, env = [], dict(os.environ)
                    pair = socket.socketpair() if sys.platform == 'darwin' else None
                    def argv(run, profile):
                        fd = os.open(run, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                        fds.append(fd)
                        env['TMPDIR'] = str(run) if pair else f'/proc/{os.getpid()}/fd/{fd}'
                        args = [WORKER, '--mode=a0',
                                '--serve', '--sync',
                                f'--sync-mode={mode}', '--sync-canary=exact',
                                f'--sync-fault={fault}', '--sync-ready-timeout-ms=300',
                                '--sync-pump-timeout-ms=300', '--width=64', '--height=48',
                                f'--root-cache-path={profile}',
                                f'--url-template={(root/"page.html").as_uri()}?f=' + ('0' if mode == 'p' else '{f}')]
                        args.extend([f'--ipc-fd={pair[1].fileno()}'] if pair else
                                    ['--ozone-platform=headless', '--ipc-socket=ipc.sock'])
                        return args
                    try:
                        with SupervisedProcessGroup(c, argv, backend_id='cef_protocol', env=env,
                                                    pass_fds=(pair[1].fileno(),) if pair else ()) as group:
                            if pair:
                                pair[1].close()
                                connection = CefWorkerClient.from_socket(pair[0])
                            else:
                                connection = CefWorkerClient.connect(group.run_dir/'ipc.sock')
                            with connection as client:
                                with self.assertRaises(CefWorkerError) as caught:
                                    client.request_png(17, 'frame.png', request_id=1,
                                                       worker_relative=True, timeout=10)
                                self.assertEqual(caught.exception.status_code, 1)
                            self.assertEqual(group.proc.wait(timeout=15), 0)
                    finally:
                        if pair:
                            for endpoint in pair:
                                endpoint.close()
                        for fd in fds:
                            os.close(fd)

    def test_full_pipeline_fixture(self):
        import math
        import struct
        import wave
        with tempfile.TemporaryDirectory(prefix='cef_pipeline_') as td:
            root = Path(td).resolve()
            p = project(root, n=30)
            (root / 'page.html').write_text('''<!doctype html><style>
html,body{margin:0;background:#faf9f6;overflow:hidden}div{position:fixed;
width:80px;height:40px;top:60px;background:#c8a24a}</style><div></div><script>
let f=+new URLSearchParams(location.search).get('f');
document.querySelector('div').style.left=(20+f*3)+'px';</script>''')
            with wave.open(str(root / 'voice.wav'), 'wb') as w:
                w.setparams((1, 2, 48000, 0, 'NONE', 'not compressed'))
                w.writeframes(b''.join(struct.pack('<h', int(4000*math.sin(i*2*math.pi*440/48000)))
                                       for i in range(48000)))
            tl = json.loads(p.read_text())
            tl['project'].update(width=320, height=180)
            tl['sources'] = [dict(id='voice', path='voice.wav')]
            tl['audio_tracks'] = [dict(name='voice', events=[dict(src='voice', volume=1.0, **{'in': 0, 'out': 30})])]
            p.write_text(json.dumps(tl))
            env = dict(os.environ, VTX_GFX='cef', VTX_CEF_WORKER=WORKER,
                       VTX_CEF_MODE='a0', VTX_GFX_PAGES='1', VTX_GFX_CACHE_NS='',
                       VTX_PIPELINE='0', VTX_VIDEO_SEGMENTS='1')
            result = subprocess.run([os.sys.executable, str(ROOT/'vtx/vtx.py'), '-t', str(p), 'all'],
                                    env=env, capture_output=True, text=True, timeout=180)
            evidence = os.environ.get('VTX_CEF_EVIDENCE_DIR')
            if evidence:
                dest = Path(evidence) / 'pipeline-fixture'
                shutil.copytree(root, dest, dirs_exist_ok=True)
                (dest/'test.stdout.log').write_text(result.stdout + result.stderr)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            rec = json.loads((root/'out/receipt.json').read_text())
            for stage in ('validate', 'prepare', 'gfx', 'video', 'audio', 'mux', 'qc'):
                self.assertTrue(rec['stages'][stage]['ok'], stage)
            self.assertTrue((root/'out/final.mp4').is_file())

    def test_real_mode_n_exact_pixels_and_cache_repair(self):
        with tempfile.TemporaryDirectory(prefix='cef_production_') as td:
            root = Path(td).resolve()
            p = project(root)
            env = dict(os.environ, VTX_GFX='cef', VTX_CEF_WORKER=WORKER,
                       VTX_CEF_MODE='a0', VTX_GFX_PAGES='1', VTX_GFX_CACHE_NS='')
            command = [os.sys.executable, str(ROOT / 'vtx/vtx.py'), '-t', str(p),
                       '--events-jsonl', str(root / 'cef-events.jsonl'), 'gfx']
            result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=150)
            evidence = os.environ.get('VTX_CEF_EVIDENCE_DIR')
            if evidence:
                shutil.copytree(root, Path(evidence) / 'production-fixture', dirs_exist_ok=True)
                (Path(evidence) / 'production-fixture/startup.log').write_text(result.stdout + result.stderr)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('Mode N native-exact', result.stdout)
            from vtx import events as execution_events
            observed = execution_events.read_events((root / 'cef-events.jsonl').read_bytes())
            self.assertTrue(observed['complete'])
            self.assertTrue(observed['events'][-1]['extensions']['evidence_complete'])
            blocks = [row for row in observed['events'] if row['event'] == 'task_start'
                      and 'backend' in row['extensions']]
            self.assertEqual([row['extensions']['backend'] for row in blocks], ['cef'])
            (root / 'cef-events.initial.jsonl').write_bytes((root / 'cef-events.jsonl').read_bytes())
            with mock.patch.dict(os.environ, env):
                c = Ctx(p)
                out = hygiene.gfx_dir(c, c.tl['video_tracks'][0]['clips'][0])
            for f in range(3):
                w, h, rgba = decode_png_rgba(out / f'f{f:04d}.png')
                self.assertEqual((w, h), (64, 48))
                for y in range(h):
                    for x in range(w):
                        off = (y * w + x) * 4
                        if x <= 9 or x >= 54 or y <= 9 or y >= 38:
                            want = bytes(((x + f) % 256, y + 9, x ^ y, 255))
                        else:
                            want = bytes((17, 33, 65, 255) if f % 2 else (0, 0, 0, 0))
                        self.assertEqual(rgba[off:off + 4], want, (f, x, y))
            events = [json.loads(s) for path in (root / 'cache/reports/cef').glob('*.sync.jsonl')
                      for s in path.read_text().splitlines()]
            accepted = [e for e in events if e.get('to') == 'S5_ONPAINT_VERIFIED']
            self.assertEqual(len(accepted), 3)
            self.assertTrue(all(e['drive_mode'] == 'n' and
                                e['restore_source'] == 'native_cross_capture' for e in accepted))
            for accepted_event in accepted:
                request = [e for e in events if e['request_id'] == accepted_event['request_id']]
                baseline = [e for e in request if e['event'] == 'exact_baseline_veto_armed']
                equal = [e for e in request if e['event'] == 'exact_baseline_equal']
                self.assertEqual(len(baseline), 1)
                self.assertEqual(len(equal), 1)
                self.assertIs(baseline[0]['restoration_source'], False)
                self.assertEqual(equal[0]['compared_bytes'], 64 * 48 * 4)
                self.assertLess(equal[0]['seq'], accepted_event['seq'])
            cached = subprocess.run(command, env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(cached.returncode, 0, cached.stdout + cached.stderr)
            self.assertIn('已缓存', cached.stdout)
            (out / 'f0001.png').write_bytes(b'corrupt')
            repair = subprocess.run(command, env=env, capture_output=True, text=True, timeout=150)
            self.assertEqual(repair.returncode, 0, repair.stdout + repair.stderr)
            self.assertIn('native-exact×1', repair.stdout)
            self.assertEqual(decode_png_rgba(out / 'f0001.png')[2][:4], bytes((1, 9, 0, 255)))
            self.assertFalse(any((root / 'cache/tmp').iterdir()), 'supervised profiles leaked')
            evidence = os.environ.get('VTX_CEF_EVIDENCE_DIR')
            if evidence:
                target = Path(evidence) / 'production-fixture'
                shutil.copytree(root, target, dirs_exist_ok=True)
                (target / 'test.stdout.log').write_text(result.stdout + '\n' + repair.stdout)



class RendererEvents(unittest.TestCase):
    def setUp(self):
        bind_environment(self)

    def test_backend_allowlist_producer_reader_roundtrip_and_unknown_rejection(self):
        for backend in ('cdp', 'cli', 'cef'):
            with self.subTest(backend=backend):
                stream = Buffer()
                execution = events.ExecutionContext(stream)
                execution.task('gfx', metadata={'backend': backend}).call(lambda: None)
                execution.finish(0)
                text = stream.getvalue()
                parsed = events.read_events(text)
                self.assertTrue(parsed['complete'])
                row = next(r for r in parsed['events'] if r['event'] == 'task_start')
                self.assertEqual(row['extensions'], {'backend': backend})
                forged_rows = [json.loads(line) for line in text.splitlines()]
                next(r for r in forged_rows if r['event'] == 'task_start')['extensions']['backend'] = 'unknown'
                forged = ''.join(json.dumps(r) + '\n' for r in forged_rows)
                self.assertNotEqual(forged, text)
                with self.assertRaises(ValueError):
                    events.read_events(forged)
        self.assertEqual(events._metadata('task_start', {'backend': 'unknown'}, strict=False),
                         {'metadata_redacted': True})

    def test_renderer_driver_attribution_not_selected_backend_alias(self):
        for backend, available, expected in (('cef', False, 'cef'), ('cdp', True, 'cdp'),
                                              ('cdp', False, 'cli'), ('cli', True, 'cli')):
            with self.subTest(backend=backend, available=available), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                timeline = project(root, 1)
                worker = root / 'worker'
                worker.write_text('#!/bin/sh\nexit 1\n')
                worker.chmod(0o700)
                stream = Buffer()
                execution = events.ExecutionContext(stream)
                with mock.patch.dict(os.environ, VTX_GFX=backend, VTX_GFX_CACHE_NS='',
                                     VTX_CEF_WORKER=str(worker)), \
                     mock.patch.object(gfx_render, 'available', return_value=available), \
                     mock.patch.object(cli, 'budget_gate'), \
                     mock.patch.object(cli, 'find_chrome', return_value='unused'), \
                     mock.patch.object(gfx_render, 'render_page', return_value={'failed': [], 'path_counts': {}, 'ms_per_frame': 0}), \
                     mock.patch('vtx.gfx_cef.render_page', return_value='CEF fixture'), \
                     mock.patch.object(cli, '_cdp_via', return_value='CDP fixture'), \
                     mock.patch.object(gfx_cli, 'render_cli', return_value=1), \
                     mock.patch.object(gfx_cli, 'complete_page'), \
                     contextlib.redirect_stdout(io.StringIO()):
                    cli._gfx(Ctx(timeline, execution=execution))
                execution.finish(0)
                rows = events.read_events(stream.getvalue())['events']
                block = next(r for r in rows if r['event'] == 'task_start')
                self.assertEqual(block['extensions']['backend'], expected)



class MainIntegration(unittest.TestCase):
    def test_png_sequence_reads_directly_under_cef_without_worker_or_native_receipt(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write_png(root / 'f0000.png')
            source = PngSequence(root, 'fixture', 1, 30, 16, 16)
            with mock.patch.dict(os.environ, VTX_GFX='cef', VTX_CEF_WORKER=''), \
                 mock.patch.object(gfx_cef, 'render_page', side_effect=AssertionError('HTML execution')), \
                 mock.patch.object(gfx_backend, 'cef_config', side_effect=AssertionError('native receipt')):
                self.assertEqual(source.input_args()[-1], str(root / 'f%04d.png'))

    def test_cef_keeps_path_closure_identity_and_namespace(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            tl = project(root, 1)
            worker = root / 'worker'
            worker.write_text('#!/bin/sh\nexit 1\n')
            worker.chmod(0o700)
            with mock.patch.dict(os.environ, VTX_GFX='cef', VTX_CEF_WORKER=str(worker),
                                 VTX_GFX_CACHE_NS='integration'):
                c = Ctx(tl)
                clip = c.tl['video_tracks'][0]['clips'][0]
                original = gfx_key(c, clip)
                (root / 'same.html').write_bytes((root / 'page.html').read_bytes())
                self.assertNotEqual(original, gfx_key(c, dict(clip, src='same.html')))
                source = png_source(c, clip)
                self.assertEqual(source.identity, 'gfx_exp/integration/' + original)
                from vtx.impact import capture
                first = capture(c.tl, root)
                with mock.patch.dict(os.environ, VTX_GFX_CACHE_NS='another'):
                    second = capture(c.tl, root)
                self.assertNotEqual(first['nodes']['vseg:0']['key'],
                                    second['nodes']['vseg:0']['key'])
                # CDP's strict Fetch contract must not silently downgrade to a
                # native backend which cannot enforce that request allowlist.
                c.tl['project']['gfx_dependency_policy'] = 'strict'
                with mock.patch.object(gfx_cef, 'render_page') as native, \
                     self.assertRaisesRegex(ValueError, 'strict dependencies require CDP'):
                    cli._gfx(c)
                native.assert_not_called()

if __name__ == '__main__':
    unittest.main()
