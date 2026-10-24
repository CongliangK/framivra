"""CEF Alpha production route + real native Mode N/opaque/transparent pixels.

VTX_TEST_CEF_WORKER enables real CEF (no Chrome/IME service involved).
VTX_CEF_EVIDENCE_DIR optionally retains the complete fixture and native logs.
"""
from tests.execution.test_execution_events import Buffer
from tests.gfx._environment import bind_environment
from vtx.runtime import events
from vtx.render import gfx_cli, gfx_render

import contextlib
import errno
import io
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import struct
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

from vtx.render import cef_client
from vtx.cli import commands as cli
from vtx.render import gfx_backend, gfx_cef
from vtx.cache import hygiene
from vtx.cache.cache_state import receipt_fingerprint
from vtx.pipeline.composite import RenderPlan
from vtx.pipeline.infra import Ctx
from tests.gfx.test_cef_frame_sync import decode_png_rgba
from tests.gfx._png import write_png
from vtx.render.frame_source import PngSequence, png_source
from vtx.cache.hygiene import gfx_key

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


def _rights(*fds):
    return [(socket.SOL_SOCKET, socket.SCM_RIGHTS, struct.pack(f'{len(fds)}i', *fds))]


def _packet_receipt(**changes):
    fields = dict.fromkeys(cef_client._HEADER_FIELDS, 0)
    fields.update(magic=cef_client.SLOT_MAGIC, protocol_version=1, run_id=123,
                  request_id=7, target_frame_index=3, canary_frame_index=3,
                  width=1, height=1, stride=4, pixel_format=1, color_space=1)
    fields.update(changes)
    raw = struct.pack(cef_client._HEADER_FMT, *(fields[k] for k in cef_client._HEADER_FIELDS))
    return raw + bytes(128 - len(raw))


@contextlib.contextmanager
def _native_test_ring(width=1, height=1, *, run_id=123, pid=45, flags=0):
    """One owned protocol-v1 mmap file and its matching Hello, shared by peers."""
    frame_bytes = width * height * 4
    stride = (128 + frame_bytes + 63) & ~63
    size = 384 + stride * 4
    if sys.platform == 'darwin':
        page = cef_client.mmap.PAGESIZE
        size = ((size + page - 1) // page) * page
    layout = bytearray(size)
    struct.pack_into('<IIHH', layout, 0, cef_client.RING_MAGIC, cef_client.RING_MAGIC, 1, 4)
    struct.pack_into('<IIII', layout, 12, width, height, frame_bytes, stride)
    struct.pack_into('<Q', layout, 32, run_id)
    hello = struct.pack(cef_client._HELLO_FMT, cef_client.HELLO_MAGIC, 1, flags,
                        run_id, pid, 4, width, height, frame_bytes, *([0] * 7))
    with tempfile.TemporaryFile() as file:
        file.write(layout)
        file.flush()
        yield SimpleNamespace(fd=file.fileno(), size=size, hello=hello)


def _read_test_request(peer, *, allow_eof=False):
    request = b''
    while len(request) < 64:
        chunk = peer.recv(64 - len(request))
        if not chunk:
            if allow_eof and not request:
                return None
            raise AssertionError('host EOF before a complete 64-byte frame request')
        request += chunk
    return request


@contextlib.contextmanager
def _socket_peer(serve):
    """Own the real socketpair/thread; each case supplies only its peer behavior."""
    server, consumer = socket.socketpair()
    server.settimeout(5)
    failures = []

    def run():
        try:
            serve(server)
        except BaseException as error:
            failures.append(error)
        finally:
            server.close()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    try:
        yield SimpleNamespace(socket=consumer, thread=thread, failures=failures)
    finally:
        consumer.close()
        thread.join(6)
        server.close()


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
        from vtx.render.frame_source import png_source
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
                mock.patch('vtx.render.gfx_render.render_page') as cdp:
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

    rights = staticmethod(_rights)

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

    def test_connect_attempts_close_on_failure_and_obey_one_deadline(self):
        for error in (PermissionError('denied'), OSError('connect failed'),
                      socket.timeout('backlog full'), KeyboardInterrupt(), MemoryError()):
            sock = mock.Mock()
            sock.connect.side_effect = error
            expected = cef_client.CefWorkerError if isinstance(error, socket.timeout) else type(error)
            with self.subTest(error=type(error).__name__), \
                    mock.patch.object(cef_client.socket, 'socket', return_value=sock), \
                    mock.patch.object(cef_client.CefWorkerClient, 'from_socket') as hello, \
                    self.assertRaises(expected) as caught:
                cef_client.CefWorkerClient._connect('owned.sock', 1, 2)
            if isinstance(error, socket.timeout):
                self.assertIs(caught.exception.__cause__, error)
            else:
                self.assertIs(caught.exception, error)
            sock.close.assert_called_once_with()
            hello.assert_not_called()
            self.assertGreater(sock.settimeout.call_args.args[0], 0)
            self.assertLessEqual(sock.settimeout.call_args.args[0], 1)
        first, second = mock.Mock(), mock.Mock()
        first.connect.side_effect = FileNotFoundError('starting')
        with mock.patch.object(cef_client.socket, 'socket', side_effect=[first, second]), \
                mock.patch.object(cef_client.CefWorkerClient, 'from_socket', return_value='client') as hello, \
                mock.patch.object(cef_client.time, 'monotonic', side_effect=[0, .1, .2, .5]), \
                mock.patch.object(cef_client.time, 'sleep') as sleep:
            self.assertEqual(cef_client.CefWorkerClient._connect('owned.sock', 1, 2), 'client')
        first.close.assert_called_once_with()
        second.close.assert_not_called()
        first.settimeout.assert_called_once_with(.9)
        second.settimeout.assert_called_once_with(.5)
        hello.assert_called_once_with(second, 2)
        sleep.assert_called_once_with(.05)
        refused = ConnectionRefusedError('not ready')
        first.reset_mock(); first.connect.side_effect = refused
        with mock.patch.object(cef_client.socket, 'socket', return_value=first) as create, \
                mock.patch.object(cef_client.time, 'monotonic', side_effect=[0, .1, 1.1, 1.2]), \
                mock.patch.object(cef_client.time, 'sleep'), \
                self.assertRaises(cef_client.CefWorkerError) as caught:
            cef_client.CefWorkerClient._connect('owned.sock', 1, 2)
        self.assertIs(caught.exception.__cause__, refused)
        first.close.assert_called_once_with()
        self.assertEqual(create.call_count, 1)
        for timeout in (0, -1, float('nan'), float('inf')):
            with self.subTest(timeout=timeout), \
                    mock.patch.object(cef_client.socket, 'socket') as create, \
                    self.assertRaises(ValueError):
                cef_client.CefWorkerClient._connect('owned.sock', timeout, 2)
            create.assert_not_called()

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


    png_receipt = staticmethod(_packet_receipt)

    def test_png_split_api_has_one_inflight_request_and_does_not_mix_receipts(self):
        with self.resources([(self.hello(), self.rights(888), 0, None)]) as r:
            r.sock.recv.return_value = self.png_receipt()
            with cef_client.CefWorkerClient.from_socket(r.sock) as client:
                client.submit_png(3, 'frame0.png', request_id=7, worker_relative=True)
                r.sock.recv.assert_not_called()
                packet = struct.unpack(cef_client._REQUEST_FMT, r.sock.sendall.call_args.args[0])
                self.assertEqual(packet[3:7], (7, 3, 0, 0))
                self.assertEqual(packet[-1].rstrip(b'\0'), b'frame0.png')
                for call in (lambda: client.submit_png(4, 'frame1.png', worker_relative=True),
                             lambda: client.request_slot(4)):
                    with self.assertRaisesRegex(cef_client.CefWorkerError, 'pending'):
                        call()
                self.assertEqual(r.sock.sendall.call_count, 1)
                self.assertEqual(client.receive_png().header['target_frame_index'], 3)
                with self.assertRaisesRegex(cef_client.CefWorkerError, 'no PNG request'):
                    client.receive_png()
                self.assertEqual(client.request_png(3, 'frame1.png', request_id=7,
                    worker_relative=True).header['request_id'], 7)
                self.assertEqual(r.sock.sendall.call_count, 2)

    def test_png_request_owns_long_path_descriptor_until_receipt_or_close(self):
        for finish in ('receive', 'close', 'transport_error', 'send_error'):
            with self.subTest(finish=finish), mock.patch.object(cef_client.sys, 'platform', 'linux'), \
                    self.resources([(self.hello(), self.rights(888), 0, None)]) as r, \
                    mock.patch.object(cef_client.os, 'open', return_value=999):
                client = cef_client.CefWorkerClient.from_socket(r.sock)
                r.sock.recv.return_value = self.png_receipt()
                if finish == 'send_error':
                    r.sock.sendall.side_effect = BrokenPipeError('send failed')
                    with self.assertRaises(cef_client.CefWorkerError):
                        client.submit_png(3, '/long-project-' + 'x' * 80 + '/f.png', request_id=7)
                else:
                    client.submit_png(3, '/long-project-' + 'x' * 80 + '/f.png', request_id=7)
                    r.close.assert_not_called()
                    if finish == 'receive':
                        client.receive_png()
                        r.close.assert_called_once_with(999)
                    elif finish == 'transport_error':
                        r.sock.recv.side_effect = ConnectionResetError('reset')
                        with self.assertRaises(cef_client.CefWorkerError):
                            client.receive_png()
                client.close(); client.close()
                self.assertCountEqual(r.close.call_args_list, [mock.call(888), mock.call(999)])

    def test_png_protocol_uncertainty_closes_instead_of_consuming_next_frame(self):
        invalid = [{'magic': 0}, {'protocol_version': 0}, {'run_id': 124},
                   {'request_id': 8}, {'target_frame_index': 4}, {'status_code': 99}]
        for changes in invalid:
            with self.subTest(changes=changes), self.resources([(self.hello(), self.rights(888), 0, None)]) as r:
                client = cef_client.CefWorkerClient.from_socket(r.sock)
                r.sock.recv.return_value = self.png_receipt(**changes)
                with self.assertRaises(cef_client.CefWorkerError):
                    client.request_png(3, 'frame.png', request_id=7, worker_relative=True)
                self.assertTrue(client._closed)
                with self.assertRaisesRegex(cef_client.CefWorkerError, 'closed'):
                    client.submit_png(4, 'frame.png', worker_relative=True)
                self.assertEqual(r.sock.sendall.call_count, 1)
                r.close.assert_called_once_with(888)
        for error in (b'', socket.timeout(), ConnectionResetError(), OSError('recv'),
                      KeyboardInterrupt(), RuntimeError('consumer')):
            with self.subTest(error=error), self.resources([(self.hello(), self.rights(888), 0, None)]) as r:
                client = cef_client.CefWorkerClient.from_socket(r.sock)
                if isinstance(error, bytes): r.sock.recv.return_value = error
                else: r.sock.recv.side_effect = error
                expected = cef_client.CefWorkerError if isinstance(error, (bytes, OSError)) else type(error)
                with self.assertRaises(expected):
                    client.request_png(3, 'frame.png', request_id=7, worker_relative=True)
                self.assertTrue(client._closed)
                r.close.assert_called_once_with(888)

    def test_png_bound_worker_errors_clear_admission_but_keep_connection_usable(self):
        for status in (1, 2, 3, 4):
            with self.subTest(status=status), self.resources([(self.hello(), self.rights(888), 0, None)]) as r:
                with cef_client.CefWorkerClient.from_socket(r.sock) as client:
                    r.sock.recv.return_value = self.png_receipt(status_code=status)
                    with self.assertRaises(cef_client.CefWorkerError) as caught:
                        client.request_png(3, 'frame.png', request_id=7, worker_relative=True)
                    self.assertEqual(caught.exception.status_code, status)
                    self.assertEqual(caught.exception.detail['request_id'], 7)
                    self.assertFalse(client._closed)
                    self.assertIsNone(client._pending_png)
                    r.sock.recv.return_value = self.png_receipt()
                    client.request_png(3, 'frame.png', request_id=7, worker_relative=True)

    def test_png_timeout_is_one_absolute_deadline_including_host_overlap(self):
        for timestamps, fragments in (([0, .25, 1.25], 1), ([0, 1.25], 0)):
            with self.subTest(timestamps=timestamps), self.resources([(self.hello(), self.rights(888), 0, None)]) as r:
                client = cef_client.CefWorkerClient.from_socket(r.sock)
                raw = self.png_receipt()
                r.sock.recv.side_effect = [raw[:1], raw[1:]]
                with mock.patch.object(cef_client.time, 'monotonic', side_effect=timestamps):
                    client.submit_png(3, 'frame.png', request_id=7, timeout=1, worker_relative=True)
                    with self.assertRaisesRegex(cef_client.CefWorkerError, '回执.*超时'):
                        client.receive_png()
                self.assertEqual(r.sock.recv.call_count, fragments)
                self.assertTrue(client._closed)

    def test_png_split_requests_over_real_socketpair_and_transferred_shm_fd(self):
        """Exercise actual recv/send/SCM_RIGHTS/mmap, not mocked socket methods."""
        requests, eof = [], []
        with _native_test_ring() as ring:
            def serve(server):
                server.sendmsg([ring.hello], _rights(ring.fd))
                for expected_frame in (0, 65535, 7):
                    fields = struct.unpack(cef_client._REQUEST_FMT, _read_test_request(server))
                    requests.append(fields)
                    if fields[4] != expected_frame:
                        raise AssertionError('unexpected frame order')
                    response = self.png_receipt(request_id=fields[3],
                        target_frame_index=expected_frame, canary_frame_index=expected_frame)
                    server.sendall(response[:17])
                    server.sendall(response[17:])
                eof.append(server.recv(1))

            with _socket_peer(serve) as peer:
                with cef_client.CefWorkerClient.from_socket(peer.socket, ready_timeout=5) as client:
                    for frame in (0, 65535, 7):
                        client.submit_png(frame, '界.png', request_id=frame + 1,
                                          timeout=5, worker_relative=True)
                        self.assertEqual(client.receive_png().header['target_frame_index'], frame)
                # Client closes its SCM_RIGHTS duplicate, never the sender's fd.
                self.assertEqual(os.fstat(ring.fd).st_size, ring.size)
        self.assertFalse(peer.thread.is_alive(), 'serial peer must see EOF after host closes')
        self.assertEqual(peer.failures, [])
        self.assertEqual(eof, [b''])
        self.assertEqual([r[4] for r in requests], [0, 65535, 7])
        self.assertTrue(all(r[-1].rstrip(b'\0') == '界.png'.encode() for r in requests))

    def test_real_png_timeout_closes_socket_mapping_and_owned_output_descriptor(self):
        received = []
        with _native_test_ring() as ring, tempfile.TemporaryDirectory(prefix='cef_timeout_') as td:
            def stalled_peer(server):
                server.sendmsg([ring.hello], _rights(ring.fd))
                received.append(_read_test_request(server))
                # No receipt: client must time out and close, not send a retry.
                received.append(server.recv(1))

            with _socket_peer(stalled_peer) as peer:
                client = cef_client.CefWorkerClient.from_socket(peer.socket, ready_timeout=5)
                try:
                    if sys.platform == 'darwin':
                        kwargs = dict(out_png_path='frame.png', worker_relative=True)
                    else:
                        directory = Path(td) / ('long-output-parent-' * 4)
                        directory.mkdir()
                        kwargs = dict(out_png_path=directory / 'f.png')
                    client.submit_png(3, request_id=7, timeout=.05, **kwargs)
                    output_fd = client._pending_png.output_fd
                    shared_fd = client._shm_fd
                    with self.assertRaisesRegex(cef_client.CefWorkerError, '回执.*超时'):
                        client.receive_png()
                    self.assertTrue(client._closed)
                    self.assertIsNone(client._pending_png)
                    self.assertEqual(peer.socket.fileno(), -1)
                    for fd in (shared_fd, output_fd):
                        if fd is not None:
                            with self.assertRaises(OSError) as caught:
                                os.fstat(fd)
                            self.assertEqual(caught.exception.errno, errno.EBADF)
                    with self.assertRaises(ValueError):
                        client._mm[0]
                    with self.assertRaisesRegex(cef_client.CefWorkerError, 'closed'):
                        client.submit_png(4, 'frame.png', worker_relative=True)
                    client.close()
                finally:
                    client.close()
        self.assertFalse(peer.thread.is_alive())
        self.assertEqual(peer.failures, [])
        self.assertEqual(len(received[0]), 64)
        self.assertEqual(received[1:], [b''])

    def test_png_local_argument_errors_do_not_send_or_poison_session(self):
        cases = [{'out_png_path': s} for s in ('', '.', '..', 'dir/file.png', '/file.png',
                                             'x\0.png', '界' * 14)]
        cases += [{'timeout': v} for v in (-1, 0, float('nan'), float('inf'))]
        cases += [{'request_id': -1}, {'expected_wh': (2 ** 32, 1)}]
        with self.resources([(self.hello(), self.rights(888), 0, None)]) as r:
            with cef_client.CefWorkerClient.from_socket(r.sock) as client:
                for case in cases:
                    with self.subTest(case=case), self.assertRaises((cef_client.CefWorkerError, ValueError, struct.error)):
                        arguments = dict(frame_index=3, out_png_path='frame.png', worker_relative=True)
                        arguments.update(case)
                        client.submit_png(**arguments)
                    self.assertFalse(client._closed)
                    self.assertIsNone(client._pending_png)
                r.sock.sendall.assert_not_called()
                r.sock.recv.return_value = self.png_receipt()
                client.request_png(3, '界' * 13, request_id=7, timeout=None, worker_relative=True)
                self.assertIsNone(r.sock.settimeout.call_args.args[0])


@unittest.skipUnless(hasattr(os, 'O_NOFOLLOW') and hasattr(socket, 'SCM_RIGHTS'),
                     'CEF host requires POSIX no-follow and SCM_RIGHTS')
class HostOutputPipeline(unittest.TestCase):
    """Real PNG parser/atomic publication with a strictly serial native stand-in."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='cef_host_')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.out = self.root / 'cache' / 'gfx' / 'page'
        self.out.mkdir(parents=True)
        self.page = self.root / 'page.html'
        self.page.write_text('<!doctype html>')
        self.ctx = SimpleNamespace(root=self.root, cache=self.root / 'cache', verify_inputs=mock.Mock())
        self.options = gfx_backend.CefRuntimeOptions('n', 'spng', 1, 'sub_up', 2)

    @contextlib.contextmanager
    def pipeline(self, *, corrupt=None, header=None, fail_frame=None, exit_code=0,
                 platform=None, pid=123, hello_flags=cef_client.HELLO_FLAG_PAIRED_EXACT):
        test, steps = self, []
        run = self.root / 'cache' / 'tmp' / 'native'
        requested, generated = [], {}

        class SerialClient:
            hello = {'pid': pid, 'flags': hello_flags}
            pending = None
            closed = False

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.closed = True
                steps.append(('close', self.pending[0] if self.pending else None))

            def submit_png(self, frame, output, *, request_id, expected_wh, worker_relative):
                test.assertIsNone(self.pending, 'more than one native request in flight')
                test.assertTrue(worker_relative)
                test.assertEqual((request_id, expected_wh), (frame + 1, (16, 16)))
                test.assertFalse((run / output).exists(), 'reused a still-owned output buffer')
                self.pending = (frame, output)
                requested.append(frame)
                steps.append(('submit', frame, output))

            def receive_png(self):
                test.assertIsNotNone(self.pending)
                frame, output = self.pending
                self.pending = None
                steps.append(('receive', frame, output))
                if frame == fail_frame:
                    raise cef_client.CefWorkerError('native JS_ERR', status_code=3,
                                                    detail={'target_frame_index': frame})
                write_png(run / output, frame=frame)
                if corrupt is not None:
                    corrupt(frame, run / output)
                generated[frame] = (run / output).read_bytes()
                h = dict(canary_frame_index=frame, width=16, height=16, stride=64,
                         pixel_format=1, color_space=1, target_frame_index=frame,
                         request_id=frame + 1, run_id=777)
                h.update(header or {})
                return cef_client.FrameReceipt(h)

        client = SerialClient()
        state = SimpleNamespace(steps=steps, requested=requested, generated=generated,
                                client=client, run=run, argv=None, launch=None, exited=False)

        class Group:
            proc = SimpleNamespace(pid=123)
            run_dir = run

            def __init__(self, ctx, argv, **kwargs):
                state.launch = kwargs
                state.builder = argv

            def __enter__(self):
                run.mkdir(parents=True)
                profile = run / 'profile'
                profile.mkdir()
                state.argv = state.builder(run, profile)
                (run / 'sync.jsonl').write_text('{"event":"test"}\n')
                self.proc.wait = mock.Mock(return_value=exit_code)
                state.proc = self.proc
                return self

            def __exit__(self, *args):
                state.exited = True
                steps.append(('stop',))
                shutil.rmtree(run)

        validate = gfx_cli.png_invalid_reason
        commit = gfx_cef._commit_png

        def checked(path, **kwargs):
            if Path(path).parent == run:
                steps.append(('validate', Path(path).name))
            return validate(path, **kwargs)

        def committed(source, target, **kwargs):
            steps.append(('commit', Path(source).name, Path(target).name))
            return commit(source, target, **kwargs)

        with contextlib.ExitStack() as stack:
            for target, name, kwargs in (
                    (gfx_cef, 'cef_config', {'return_value': (self.root / 'worker', 'a0')}),
                    (gfx_cef, 'cef_runtime_options', {'return_value': self.options}),
                    (gfx_cef, 'supervision_available', {'return_value': True}),
                    (gfx_cef, 'SupervisedProcessGroup', {'new': Group}),
                    (gfx_cef.CefWorkerClient, 'connect', {'return_value': client}),
                    (gfx_cef.CefWorkerClient, 'from_socket', {'return_value': client}),
                    (gfx_cli, 'png_invalid_reason', {'side_effect': checked}),
                    (gfx_cef, '_commit_png', {'side_effect': committed})):
                stack.enter_context(mock.patch.object(target, name, **kwargs))
            if platform:
                stack.enter_context(mock.patch.object(gfx_cef, 'sys', SimpleNamespace(platform=platform)))
            yield state

    def render(self, n=4, frames=None):
        return gfx_cef.render_page(self.page, self.out, n, ctx=self.ctx,
                                   width=16, height=16, frames=frames)

    def report(self, kind):
        files = list((self.ctx.cache / 'reports' / 'cef').glob('*.' + kind + '.json'))
        self.assertEqual(len(files), 1)
        return json.loads(files[0].read_text())

    @contextlib.contextmanager
    def serial_native_workers(self, *, fault=None, synchronize=True):
        """Real socketpair protocol peers; no listener/network or CEF executable.

        Exercise the Darwin inherited-socket production branch on any POSIX
        host. Process creation is replaced by owned serial peer threads only.
        """
        lock = threading.Lock()
        workers, requests, failures = [], [], []
        state = SimpleNamespace(active=0, peak=0, workers=workers,
                                requests=requests, failures=failures)
        barrier = threading.Barrier(2) if fault is None and synchronize else None

        class Group:
            def __init__(self, ctx, argv, **kwargs):
                with lock:
                    self.index = len(workers)
                    workers.append(self)
                self.ctx, self.builder, self.launch = ctx, argv, kwargs
                self.run_dir = ctx.cache / 'tmp' / f'unit-native-{self.index}'
                self.proc = SimpleNamespace(pid=20000 + self.index, wait=self.wait)
                self.connection = None
                self.stopped = False
                self.completed = threading.Event()

            def __enter__(self):
                self.run_dir.mkdir(parents=True)
                profile = self.run_dir / 'profile'
                profile.mkdir()
                self.argv = self.builder(self.run_dir, profile)
                self.width = int(next(a.split('=', 1)[1] for a in self.argv if a.startswith('--width=')))
                self.height = int(next(a.split('=', 1)[1] for a in self.argv if a.startswith('--height=')))
                self.connection = socket.socket(fileno=os.dup(self.launch['pass_fds'][0]))
                with lock:
                    state.active += 1
                    state.peak = max(state.peak, state.active)
                self.thread = threading.Thread(target=self.serve, daemon=True)
                self.thread.start()
                return self

            def serve(self):
                try:
                    conn = self.connection
                    conn.settimeout(5)
                    run_id = 777 + self.index
                    with _native_test_ring(self.width, self.height, run_id=run_id,
                            pid=self.proc.pid, flags=1 | cef_client.HELLO_FLAG_PAIRED_EXACT) as ring:
                        conn.sendmsg([ring.hello], _rights(ring.fd))
                        count = 0
                        while True:
                            raw = _read_test_request(conn, allow_eof=True)
                            if raw is None:
                                return
                            fields = struct.unpack(cef_client._REQUEST_FMT, raw)
                            frame = fields[4]
                            if (fields[0:3] != (cef_client.REQUEST_MAGIC, 1, 0)
                                    or fields[3] != frame + 1
                                    or fields[5:7] != (self.width, self.height)):
                                raise AssertionError('invalid request contract')
                            output = self.run_dir / fields[-1].split(b'\0', 1)[0].decode()
                            if output.parent != self.run_dir or output.exists():
                                raise AssertionError('output not an empty owned buffer')
                            with lock:
                                requests.append((self.index, frame))
                            if barrier is not None and self.index < 2 and count == 0:
                                barrier.wait(5)  # stage-wide pool must really overlap
                            count += 1
                            write_png(output, width=self.width, height=self.height, frame=frame)
                            if fault == 'png' and frame == 2:
                                output.write_bytes(output.read_bytes()[:-1])
                            header = _packet_receipt(run_id=run_id, request_id=frame + 1,
                                target_frame_index=(frame + 1 if fault == 'receipt' and frame == 2 else frame),
                                canary_frame_index=frame, width=self.width, height=self.height,
                                stride=self.width * 4)
                            conn.sendall(header)
                except (BrokenPipeError, ConnectionResetError):
                    if fault is None:
                        failures.append('unexpected transport interruption')
                except BaseException as error:
                    failures.append(error)
                finally:
                    if self.connection is not None:
                        self.connection.close()
                    self.completed.set()

            def wait(self, timeout):
                if not self.completed.wait(timeout):
                    raise AssertionError('worker did not observe host EOF')
                self.thread.join(timeout)
                return 0

            def __exit__(self, *args):
                try:
                    self.wait(6)
                finally:
                    self.stopped = True
                    with lock:
                        state.active -= 1
                    shutil.rmtree(self.run_dir)

        with mock.patch.object(gfx_cef, 'SupervisedProcessGroup', Group), \
                mock.patch.object(gfx_cef, 'supervision_available', return_value=True), \
                mock.patch.object(gfx_cef, 'sys', SimpleNamespace(platform='darwin')):
            yield state
        self.assertEqual(failures, [])
        self.assertTrue(all(w.stopped and not w.thread.is_alive() for w in workers))
        self.assertEqual(state.active, 0)

    def stage_context(self):
        bind_environment(self)
        timeline = project(self.root, n=6)
        data = json.loads(timeline.read_text())
        data['project'].update(width=16, height=16)
        timeline.write_text(json.dumps(data))
        worker = self.root / 'worker'
        worker.write_text('#!/bin/sh\nexit 99\n')
        worker.chmod(0o700)
        environment = mock.patch.dict(os.environ, VTX_GFX='cef', VTX_CEF_WORKER=str(worker),
            VTX_CEF_MODE='a0', VTX_CEF_SYNC_MODE='n', VTX_GFX_PAGES='2',
            VTX_GFX_SPLIT_FRAMES='2', VTX_GFX_SPLIT_MAX='3', VTX_GFX_CACHE_NS='')
        environment.start()
        self.addCleanup(environment.stop)
        ctx = Ctx(timeline)
        self.out = hygiene.gfx_dir(ctx, ctx.tl['video_tracks'][0]['clips'][0])
        return ctx

    def test_shared_stage_pool_to_real_ipc_hosts_certifies_once_and_reuses_warm_cache(self):
        ctx = self.stage_context()
        with self.serial_native_workers() as peers, \
                mock.patch.object(gfx_cli, 'complete_page', wraps=gfx_cli.complete_page) as complete, \
                mock.patch.object(gfx_cli, 'render_cli', side_effect=AssertionError('CLI fallback')), \
                mock.patch.object(gfx_render, 'render_page', side_effect=AssertionError('CDP fallback')), \
                contextlib.redirect_stdout(io.StringIO()) as stdout:
            cli.gfx(ctx)
            self.assertEqual(complete.call_count, 1)
            self.assertEqual(len(peers.workers), 3)
            self.assertEqual(peers.peak, 2)
            self.assertCountEqual([frame for _, frame in peers.requests], range(6))
            for worker in peers.workers:
                self.assertTrue(worker.stopped)
            record = json.loads((self.out / gfx_cli.COMPLETE_FILE).read_text())
            self.assertEqual(len(record['sha256']), 6)
            cli.gfx(ctx)  # real completion/environment gate, no new host session
            self.assertEqual(len(peers.workers), 3)
            self.assertEqual(complete.call_count, 1)
        self.assertIn('split×3', stdout.getvalue())
        diagnostics = [json.loads(p.read_text()) for p in (ctx.cache / 'reports' / 'cef').glob('*.pipeline.json')]
        self.assertEqual(len(diagnostics), 3)
        self.assertTrue(all(p['execution']['run_id'] == ctx.execution.run_id for p in diagnostics))
        self.assertEqual(len({p['task']['task_id'] for p in diagnostics}), 3)
        self.assertTrue(all(p['task']['attempt_no'] == 1 for p in diagnostics))
        self.assertTrue(all(p['completed'] and p['max_native_inflight'] == 1 for p in diagnostics))
        self.assertEqual(sorted(f for p in diagnostics for f in p['rendered_frames']), list(range(6)))
        self.assertTrue(all('not native CPU time' in p['request_wall_scope'] for p in diagnostics))
        self.assertEqual(gfx_cli.verify_frames(self.out, range(6), width=16, height=16), [])
        self.assertFalse(any((ctx.cache / 'tmp').iterdir()))

    def test_shared_stage_real_ipc_faults_never_certify_partial_page_and_are_repairable(self):
        ctx = self.stage_context()
        for fault in ('png', 'receipt'):
            with self.subTest(fault=fault):
                if self.out.exists():
                    shutil.rmtree(self.out)
                with self.serial_native_workers(fault=fault) as peers, \
                        mock.patch.object(gfx_cli, 'complete_page', wraps=gfx_cli.complete_page) as complete, \
                        mock.patch.object(gfx_cli, 'render_cli', side_effect=AssertionError('CLI fallback')), \
                        mock.patch.object(gfx_render, 'render_page', side_effect=AssertionError('CDP fallback')), \
                        contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
                    cli.gfx(ctx)
                complete.assert_not_called()
                self.assertFalse((self.out / gfx_cli.COMPLETE_FILE).exists())
                self.assertFalse((self.out / 'f0002.png').exists())
                self.assertFalse(any((ctx.cache / 'tmp').iterdir()))
                receipt = json.loads((ctx.root / 'out' / 'receipt.json').read_text())
                self.assertFalse(receipt['stages']['gfx']['ok'])
                # A failed session leaves only independently complete old frames;
                # a fresh stage may repair them, never pretend the failed run passed.
                with self.serial_native_workers(synchronize=False) as repair, \
                        contextlib.redirect_stdout(io.StringIO()):
                    cli.gfx(ctx)
                self.assertEqual(gfx_cli.verify_frames(self.out, range(6), width=16, height=16), [])
                self.assertTrue((self.out / gfx_cli.COMPLETE_FILE).is_file())
                self.assertIn(2, [frame for _, frame in repair.requests])

    def test_pipeline_overlaps_next_request_uses_two_buffers_and_moves_exact_bytes(self):
        with self.pipeline() as p, mock.patch.object(Path, 'read_bytes', autospec=True,
                side_effect=Path.read_bytes) as reads:
            result = self.render()
        self.assertIn('host-pipeline×2', result)
        self.assertEqual(p.requested, [0, 1, 2, 3])
        self.assertEqual([s[2] for s in p.steps if s[0] == 'submit'],
                         ['frame0.png', 'frame1.png', 'frame0.png', 'frame1.png'])
        self.assertLess(p.steps.index(('submit', 1, 'frame1.png')),
                        p.steps.index(('validate', 'frame0.png')))
        self.assertLess(p.steps.index(('commit', 'frame0.png', 'f0000.png')),
                        p.steps.index(('receive', 1, 'frame1.png')))
        # Four stand-in writes inspect their own bytes; host reads only sync log,
        # never an additional PNG payload before the atomic rename.
        png_reads = [c.args[0] for c in reads.call_args_list if str(c.args[0]).endswith('.png')]
        self.assertEqual(len(png_reads), 4)
        for frame, expected in p.generated.items():
            output = self.out / f'f{frame:04d}.png'
            self.assertEqual(output.read_bytes(), expected)
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
        pipeline = self.report('pipeline')
        self.assertEqual(pipeline['output_commits'], {'rename': 4, 'copy': 0})
        self.assertEqual(pipeline['max_native_inflight'], 1)
        self.assertEqual(pipeline['output_buffers'], 2)
        self.assertEqual(pipeline['rendered_frames'], [0, 1, 2, 3])
        self.assertEqual(pipeline['worker_options'], self.options.to_dict())
        self.assertEqual(pipeline['execution'], {'run_id': None})
        self.assertEqual(pipeline['task'], {'task_id': None, 'attempt_no': None})
        for sample in pipeline['request_roundtrips']:
            offsets = [sample[k] for k in ('submit_offset_us', 'receive_offset_us',
                'validate_start_offset_us', 'validate_end_offset_us',
                'commit_start_offset_us', 'commit_end_offset_us')]
            self.assertEqual(offsets, sorted(offsets))
            self.assertLessEqual(offsets[-1], pipeline['block_wall_us'])
            self.assertTrue(sample['committed'])
        for previous, following in zip(pipeline['request_roundtrips'], pipeline['request_roundtrips'][1:]):
            self.assertLessEqual(following['submit_offset_us'], previous['validate_start_offset_us'])
        self.assertEqual([r['frame'] for r in pipeline['request_roundtrips']], [0, 1, 2, 3])
        self.assertTrue(pipeline['completed'])
        self.assertGreaterEqual(pipeline['block_wall_us'], pipeline['host_commit_us'])
        self.assertEqual(len(self.report('receipts')), 4)
        self.ctx.verify_inputs.assert_called_once_with('gfx')
        self.assertTrue(p.client.closed and p.exited)
        self.assertFalse(p.run.exists())
        self.assertFalse((self.out / gfx_cli.COMPLETE_FILE).exists(), 'only page coordinator may certify')

    def test_real_serial_peer_renders_next_file_during_previous_host_validation(self):
        """Event ordering proves overlap without timing-dependent speed assertions."""
        next_ready = threading.Event()
        received, expected = [], {}
        validate = gfx_cli.png_invalid_reason
        attach = cef_client.CefWorkerClient.from_socket
        with _native_test_ring(16, 16, pid=123, flags=cef_client.HELLO_FLAG_PAIRED_EXACT) as ring, \
                self.pipeline() as p:
            def serve(server):
                server.sendmsg([ring.hello], _rights(ring.fd))
                for frame in range(3):
                    fields = struct.unpack(cef_client._REQUEST_FMT, _read_test_request(server))
                    if (fields[3], fields[4]) != (frame + 1, frame):
                        raise AssertionError('request identity mismatch')
                    output = p.run / fields[-1].split(b'\0', 1)[0].decode()
                    if output.exists():
                        raise AssertionError('host reused a still-owned output buffer')
                    write_png(output, frame=frame)
                    expected[frame] = output.read_bytes()
                    received.append(frame)
                    server.sendall(_packet_receipt(request_id=frame + 1,
                        target_frame_index=frame, canary_frame_index=frame,
                        width=16, height=16, stride=64))
                    if frame == 1:
                        next_ready.set()
                if server.recv(1) != b'':
                    raise AssertionError('unexpected request after final frame')

            def validating(path, **kwargs):
                if Path(path) == p.run / 'frame0.png' and not (self.out / 'f0000.png').exists():
                    self.assertTrue(next_ready.wait(5), 'next frame never overlapped validation')
                    # The previous native file remains intact even though the
                    # peer has already finished producing the next PNG.
                    self.assertEqual(Path(path).read_bytes(), expected[0])
                return validate(path, **kwargs)

            with _socket_peer(serve) as peer:
                client = attach(peer.socket, ready_timeout=5)
                with mock.patch.object(gfx_cef.CefWorkerClient, 'connect', return_value=client), \
                        mock.patch.object(gfx_cef.CefWorkerClient, 'from_socket', return_value=client), \
                        mock.patch.object(gfx_cli, 'png_invalid_reason', side_effect=validating):
                    self.render(n=3)
        self.assertFalse(peer.thread.is_alive())
        self.assertEqual(peer.failures, [])
        self.assertEqual(received, [0, 1, 2])
        for frame in range(3):
            self.assertEqual((self.out / f'f{frame:04d}.png').read_bytes(), expected[frame])

    def test_subset_does_not_request_or_require_other_blocks_and_skips_valid_frames(self):
        write_png(self.out / 'f0002.png', frame=2)
        cached = (self.out / 'f0002.png').read_bytes()
        with self.pipeline() as p:
            self.render(n=6, frames=[1, 2, 4])
        self.assertEqual(p.requested, [1, 4])
        self.assertEqual((self.out / 'f0002.png').read_bytes(), cached)
        self.assertEqual(sorted(x.name for x in self.out.glob('*.png')),
                         ['f0001.png', 'f0002.png', 'f0004.png'])
        self.assertEqual(self.report('pipeline')['frames'], [1, 2, 4])

    def test_empty_or_fully_valid_subset_starts_no_worker(self):
        write_png(self.out / 'f0001.png', frame=1)
        for frames in ([], [1]):
            with self.subTest(frames=frames), self.pipeline() as p:
                self.assertIn('native-exact×0', self.render(frames=frames))
            self.assertIsNone(p.argv)
            self.assertEqual(p.requested, [])
        self.assertFalse((self.ctx.cache / 'reports').exists())

    def test_frame_selection_does_not_exhaust_oversized_iterable(self):
        seen = []

        def excessive():
            for frame in range(50):
                seen.append(frame)
                yield frame

        with self.pipeline() as p, self.assertRaises(ValueError):
            self.render(n=3, frames=excessive())
        self.assertEqual(seen, [0, 1, 2, 3])
        self.assertIsNone(p.argv)
        self.assertEqual(list(self.out.iterdir()), [])

    def test_cached_validation_timing_excludes_report_directory_setup(self):
        now = [0]
        mkdir = gfx_cef.fstxn.mkdir

        def slow_report_setup(path, **kwargs):
            result = mkdir(path, **kwargs)
            if Path(path).name == 'cef':
                now[0] += 1_000_000
            return result

        with self.pipeline(), mock.patch.object(gfx_cef.time, 'perf_counter_ns', side_effect=lambda: now[0]), \
                mock.patch.object(gfx_cef.fstxn, 'mkdir', side_effect=slow_report_setup):
            self.render(n=1)
        pipeline = self.report('pipeline')
        self.assertEqual(pipeline['cached_validate_us'], 0)
        self.assertEqual(pipeline['block_wall_us'], 1000)

    def test_invalid_viewport_rejects_before_native_start_or_cache_writes(self):
        for width, height in ((15, 16), (16, 15), (True, 16), (16, 16.0),
                              (16385, 16), (16, 16385), (2 ** 32, 16)):
            with self.subTest(width=width, height=height), self.pipeline() as p, self.assertRaises(ValueError):
                gfx_cef.render_page(self.page, self.out, 1, ctx=self.ctx,
                                   width=width, height=height)
            self.assertIsNone(p.argv)
        self.assertEqual(list(self.out.iterdir()), [])
        self.assertFalse((self.ctx.cache / 'reports').exists())

    def test_invalid_frame_selection_is_rejected_before_worker_or_output_mutation(self):
        cases = [(-1, None), (65537, None), (True, None), (3.0, None),
                 (3, [-1]), (3, [3]), (3, [True]), (3, [0.0]), (3, ['1']),
                 (3, [1, 1]), (3, [2, 1])]
        for count, frames in cases:
            with self.subTest(count=count, frames=frames), self.pipeline() as p, self.assertRaises(ValueError):
                self.render(n=count, frames=frames)
            self.assertIsNone(p.argv)
        self.assertEqual(list(self.out.iterdir()), [])

    def test_invalid_png_aborts_speculative_request_without_publishing_it(self):
        from tests.gfx._png import png
        cases = [b'\x89PNG\r\n\x1a\n', png(w=16, h=16, filter_byte=5),
                 png(w=15, h=16), png(w=16, h=16)[:-1],
                 png(w=16, h=16) + b'trailing']
        for data in cases:
            with self.subTest(data=data[:24]), self.pipeline(
                    corrupt=lambda frame, path: path.write_bytes(data) if frame == 0 else None) as p:
                with self.assertRaisesRegex(cef_client.CefWorkerError, 'output invalid'):
                    self.render()
            self.assertEqual(p.requested, [0, 1])
            self.assertEqual(list(self.out.glob('*.png')), [])
            self.assertTrue(p.client.closed and p.exited)
            self.assertFalse(p.run.exists())
            self.assertNotIn(('receive', 1, 'frame1.png'), p.steps)
            p.proc.wait.assert_not_called()
            self.assertIn(('close', 1), p.steps)

    def test_move_failure_closes_pending_next_frame_without_receipt_wait_or_retry(self):
        original = OSError(errno.EACCES, 'atomic commit denied')
        with self.pipeline() as p, \
                mock.patch.object(gfx_cef.fstxn, 'replace', side_effect=original) as move:
            with self.assertRaisesRegex(cef_client.CefWorkerError, 'atomic commit denied') as caught:
                self.render()
        self.assertIs(caught.exception.__cause__, original)
        self.assertEqual(p.requested, [0, 1])
        self.assertIn(('close', 1), p.steps)
        self.assertNotIn(('receive', 1, 'frame1.png'), p.steps)
        p.proc.wait.assert_not_called()
        move.assert_called_once()
        self.assertEqual(list(self.out.glob('*.png')), [])
        self.assertEqual(self.report('receipts'), [])
        self.assertFalse(self.report('pipeline')['completed'])
        self.assertTrue(p.client.closed and p.exited)
        self.assertFalse(p.run.exists())

    def test_pipeline_report_failure_preserves_primary_failure_and_pending_cleanup(self):
        write = gfx_cef.fstxn.atomic_write
        report_error = OSError(errno.ENOSPC, 'pipeline report disk full')
        move_error = OSError(errno.EACCES, 'frame commit denied')

        def report_failed(path, *args, **kwargs):
            if str(path).endswith('.pipeline.json'):
                raise report_error
            return write(path, *args, **kwargs)

        for kind in ('png', 'move', 'native'):
            corrupt = (lambda frame, path: path.write_bytes(b'bad PNG') if frame == 0 else None) if kind == 'png' else None
            with self.subTest(kind=kind), self.pipeline(corrupt=corrupt,
                    fail_frame=1 if kind == 'native' else None) as p, \
                    mock.patch.object(gfx_cef.fstxn, 'atomic_write', side_effect=report_failed), \
                    contextlib.ExitStack() as stack:
                if kind == 'move':
                    stack.enter_context(mock.patch.object(gfx_cef.fstxn, 'replace', side_effect=move_error))
                with self.assertRaises(cef_client.CefWorkerError) as caught:
                    self.render()
            self.assertIn('CEF pipeline diagnostic failed', str(caught.exception))
            self.assertIn('pipeline report disk full', str(caught.exception))
            if kind == 'move':
                self.assertIs(caught.exception.__cause__, move_error)
                self.assertIn('frame commit denied', str(caught.exception))
            elif kind == 'png':
                self.assertIn('CEF output invalid', str(caught.exception))
            else:
                self.assertEqual(caught.exception.status_code, 3)
                self.assertEqual(caught.exception.detail, {'target_frame_index': 1})
            self.assertTrue(p.client.closed and p.exited)
            p.proc.wait.assert_not_called()
            self.assertFalse(p.run.exists())
            if kind != 'native':
                self.assertIn(('close', 1), p.steps)
                self.assertNotIn(('receive', 1, 'frame1.png'), p.steps)

    def test_pipeline_report_failure_after_success_is_still_explicit(self):
        write = gfx_cef.fstxn.atomic_write
        error = OSError(errno.ENOSPC, 'pipeline report disk full')

        def report_failed(path, *args, **kwargs):
            if str(path).endswith('.pipeline.json'):
                raise error
            return write(path, *args, **kwargs)

        with self.pipeline() as p, mock.patch.object(gfx_cef.fstxn, 'atomic_write', side_effect=report_failed):
            with self.assertRaises(OSError) as caught:
                self.render(n=1)
        self.assertIs(caught.exception, error)
        self.assertTrue(p.client.closed and p.exited)
        self.assertFalse((self.out / gfx_cli.COMPLETE_FILE).exists())

    def test_native_evidence_failure_retains_primary_status_and_alone_still_fails(self):
        write, replace = gfx_cef.fstxn.atomic_write, gfx_cef.fstxn.os.replace
        for artifact in ('sync.jsonl', 'receipts.json', 'worker.log'):
            for native_failed in (True, False):
                for path in self.out.glob('*.png'):
                    path.unlink()
                diagnostic = OSError(errno.ENOSPC, artifact + ' disk full')

                def bad_write(path, *args, **kwargs):
                    if str(path).endswith('.' + artifact):
                        raise diagnostic
                    return write(path, *args, **kwargs)

                def bad_replace(source, target, *args, **kwargs):
                    if str(target).endswith('.worker.log'):
                        raise diagnostic
                    return replace(source, target, *args, **kwargs)

                expected = cef_client.CefWorkerError if native_failed or artifact != 'worker.log' else OSError
                with self.subTest(artifact=artifact, native_failed=native_failed), \
                        self.pipeline(fail_frame=1 if native_failed else None) as p, \
                        contextlib.ExitStack() as stack:
                    stack.enter_context(mock.patch.object(gfx_cef.fstxn, 'atomic_write', side_effect=bad_write))
                    if artifact == 'worker.log':
                        stack.enter_context(mock.patch.object(gfx_cef.fstxn.os, 'replace', side_effect=bad_replace))
                    with self.assertRaises(expected) as caught:
                        self.render(n=4 if native_failed else 1)
                self.assertIn(artifact + ' disk full', str(caught.exception))
                if native_failed:
                    self.assertEqual(caught.exception.status_code, 3)
                    self.assertEqual(caught.exception.detail, {'target_frame_index': 1})
                    self.assertIsInstance(caught.exception.__cause__, cef_client.CefWorkerError)
                    self.assertEqual(caught.exception.__cause__.status_code, 3)
                    self.assertIn('native JS_ERR', str(caught.exception))
                    p.proc.wait.assert_not_called()
                self.assertTrue(p.client.closed and p.exited)
                self.assertFalse(p.run.exists())

    def test_failed_phase_timings_are_measured_and_unrun_phases_are_null(self):
        for phase in ('startup', 'receive', 'final_validation'):
            for path in self.out.glob('*.png'):
                path.unlink()
            report = self.ctx.cache / 'reports'
            if report.exists():
                shutil.rmtree(report)
            ticks = [0]

            def clock():
                ticks[0] += 1_000_000
                return ticks[0]

            expected = ValueError if phase == 'final_validation' else cef_client.CefWorkerError
            with self.subTest(phase=phase), self.pipeline(fail_frame=0 if phase == 'receive' else None) as p, \
                    mock.patch.object(gfx_cef.time, 'perf_counter_ns', side_effect=clock), \
                    contextlib.ExitStack() as stack:
                if phase == 'startup':
                    for name in ('connect', 'from_socket'):
                        stack.enter_context(mock.patch.object(gfx_cef.CefWorkerClient, name,
                            side_effect=cef_client.CefWorkerError('startup timeout')))
                elif phase == 'final_validation':
                    stack.enter_context(mock.patch.object(gfx_cli, 'require_frames',
                        side_effect=ValueError('final validation failed')))
                with self.assertRaises(expected):
                    self.render(n=1)
            pipeline = self.report('pipeline')
            self.assertFalse(pipeline['completed'])
            self.assertGreater(pipeline['worker_startup_us'], 0)
            if phase == 'startup':
                self.assertEqual(pipeline['native_receive_attempts'], 0)
                self.assertEqual(pipeline['native_wait_us'], 0)
            else:
                self.assertEqual(pipeline['native_receive_attempts'], 1)
                self.assertGreater(pipeline['native_wait_us'], 0)
            if phase == 'final_validation':
                self.assertGreater(pipeline['final_validate_us'], 0)
            else:
                self.assertIsNone(pipeline['final_validate_us'])
                self.assertEqual(pipeline['request_roundtrips'], [])
            self.assertTrue(p.exited)
            self.assertFalse(p.run.exists())

    def test_bad_receipt_metadata_stops_admission_before_next_frame(self):
        for h in ({'canary_frame_index': -1}, {'width': 17}, {'height': 17},
                  {'stride': 65}, {'pixel_format': 2}, {'color_space': 2}):
            with self.subTest(header=h), self.pipeline(header=h) as p, \
                    self.assertRaisesRegex(cef_client.CefWorkerError, 'geometry/canary/pixel format'):
                self.render()
            self.assertEqual(p.requested, [0])
            self.assertEqual(list(self.out.glob('*.png')), [])
            self.assertTrue(p.client.closed and p.exited)

    def test_worker_failure_and_nonzero_exit_preserve_failure_and_cleanup(self):
        with self.pipeline(fail_frame=1) as p:
            with self.assertRaises(cef_client.CefWorkerError) as caught:
                self.render()
        self.assertEqual(caught.exception.status_code, 3)
        self.assertEqual(caught.exception.detail, {'target_frame_index': 1})
        self.assertEqual(p.requested, [0, 1])
        self.assertEqual(self.report('pipeline')['rendered_frames'], [0])
        self.assertEqual(len(self.report('receipts')), 1)
        self.assertFalse((self.out / 'f0001.png').exists())
        self.assertTrue(p.client.closed and p.exited)
        self.assertFalse(p.run.exists())
        with self.pipeline(exit_code=4) as p, self.assertRaisesRegex(cef_client.CefWorkerError, 'exited 4'):
            self.render(n=2)
        self.assertTrue(p.client.closed and p.exited)

    def test_old_worker_without_paired_exact_handshake_is_rejected_before_admission(self):
        for flags in (0, 1):
            with self.subTest(flags=flags), self.pipeline(hello_flags=flags) as p, \
                    self.assertRaisesRegex(cef_client.CefWorkerError, 'paired-exact.*rebuild'):
                self.render()
            self.assertEqual(p.requested, [])
            self.assertTrue(p.client.closed and p.exited)
            self.assertEqual(list(self.out.glob('*.png')), [])

    def test_hello_pid_mismatch_never_admits_a_frame(self):
        with self.pipeline(pid=456) as p, self.assertRaisesRegex(cef_client.CefWorkerError, 'PID'):
            self.render()
        self.assertEqual(p.requested, [])
        self.assertTrue(p.client.closed and p.exited)

    def test_mode_p_has_one_fixed_url_for_distinct_frames_and_mode_n_expands(self):
        for mode in ('p', 'n'):
            self.options = gfx_backend.CefRuntimeOptions(mode, 'spng', 1, 'sub_up', 2)
            frames = [1, 3] if mode == 'p' else [0, 2]
            with self.subTest(mode=mode), self.pipeline() as p:
                self.render(frames=frames)
            self.assertEqual(p.requested, frames)
            expected = self.page.as_uri() + ('?f=0' if mode == 'p' else '?f={f}')
            self.assertIn('--url-template=' + expected, p.argv)
            self.assertIn('--sync-mode=' + mode, p.argv)

    def test_darwin_uses_supervised_socketpair_and_closes_both_endpoints(self):
        pair = socket.socketpair()
        self.addCleanup(lambda: [s.close() for s in pair])
        worker_fd = pair[1].fileno()
        with self.pipeline(platform='darwin') as p, \
                mock.patch.object(gfx_cef.socket, 'socketpair', return_value=pair):
            self.render(n=1)
        self.assertEqual(p.launch['pass_fds'], (worker_fd,))
        self.assertIn(f'--ipc-fd={worker_fd}', p.argv)
        self.assertFalse(any(a.startswith('--ipc-socket=') for a in p.argv))
        self.assertEqual(p.launch['env']['TMPDIR'], str(p.run))
        self.assertEqual([s.fileno() for s in pair], [-1, -1])

    def test_atomic_move_rejects_unsafe_paths_and_preserves_existing_file_on_error(self):
        source, target = self.root / 'source.png', self.out / 'f0000.png'
        write_png(source)
        target.write_bytes(b'previous target')
        with mock.patch.object(gfx_cef.fstxn, 'replace', side_effect=OSError(errno.EACCES, 'denied')), \
                mock.patch.object(gfx_cef.fstxn, 'atomic_write') as copy:
            with self.assertRaises(OSError):
                gfx_cef._commit_png(source, target, root=self.root)
            copy.assert_not_called()
        self.assertEqual(target.read_bytes(), b'previous target')
        victim = self.root / 'victim'
        victim.write_bytes(b'untouched')
        target.unlink()
        target.symlink_to(victim)
        from vtx.pipeline.infra import ProjectPathError
        try:
            gfx_cef._commit_png(source, target, root=self.root)
        except ProjectPathError:
            self.assertTrue(source.is_file())
        else:
            # fstxn's legacy-platform contract may replace the symlink itself;
            # it must never follow that leaf or modify the victim inode.
            self.assertFalse(target.is_symlink())
            self.assertFalse(source.exists())
        self.assertEqual(victim.read_bytes(), b'untouched')

    def test_cross_device_commit_retains_atomic_write_and_consumes_source(self):
        source, target = self.root / 'source.png', self.out / 'f0000.png'
        write_png(source)
        data = source.read_bytes()
        with mock.patch.object(gfx_cef.fstxn, 'replace', side_effect=OSError(errno.EXDEV, 'cross-device')):
            self.assertEqual(gfx_cef._commit_png(source, target, root=self.root), 'copy')
        self.assertEqual(target.read_bytes(), data)
        self.assertFalse(source.exists())


@unittest.skipUnless(WORKER and Path(WORKER).is_file(), 'requires VTX_TEST_CEF_WORKER')
class NativeProduction(unittest.TestCase):
    def _assert_structure_css_veto(self, name, css):
        from vtx.render.cef_client import CefWorkerError
        from vtx.runtime.proc_supervisor import SupervisedProcessGroup
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
        from vtx.render.cef_client import CefWorkerError
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
        from vtx.render.cef_client import CefWorkerClient, CefWorkerError
        from vtx.runtime.proc_supervisor import SupervisedProcessGroup
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
            from vtx.runtime import events as execution_events
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
                     mock.patch('vtx.render.gfx_cef.render_page', return_value='CEF fixture'), \
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
                from vtx.inspection.impact import capture
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
