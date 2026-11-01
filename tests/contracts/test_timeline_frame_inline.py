"""Inline transport and anchored artifacts; no browser, gfx producer or cold fill."""
import base64
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zlib

from vtx.api import frame_timeline, timeline_capabilities
from vtx.render import png_artifact as artifact
from vtx.render.gfx_cli import png_invalid_reason, _png_stream_invalid_reason
from vtx.timeline_frame import _LIMIT, _finish
from vtx.timeline_reference import _wire_size

REPO = Path(__file__).resolve().parents[2]


def chunk(tag, data):
    return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data) & 0xffffffff)


def png(rgb=b'\x10\x20\x30', padding=0, idat=None):
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0))
            + (chunk(b'tEXt', b'x' * padding) if padding else b'')
            + chunk(b'IDAT', zlib.compress(b'\0' + rgb) if idat is None else idat) + chunk(b'IEND', b''))


def decode_inline(image):
    """Test-owned canonical decoder and independent RGB PNG unfilter oracle."""
    inline = image['inline']
    assert inline['encoding'] == 'base64'
    data = base64.b64decode(inline['data'], validate=True)
    assert base64.b64encode(data).decode('ascii') == inline['data']
    assert len(data) == image['bytes']
    assert hashlib.sha256(data).hexdigest() == image['sha256']
    assert data[:8] == b'\x89PNG\r\n\x1a\n'
    offset, compressed, width, height = 8, bytearray(), None, None
    while offset < len(data):
        size = int.from_bytes(data[offset:offset+4], 'big')
        tag, payload = data[offset+4:offset+8], data[offset+8:offset+8+size]
        assert zlib.crc32(tag + payload) & 0xffffffff == int.from_bytes(data[offset+8+size:offset+12+size], 'big')
        offset += size + 12
        if tag == b'IHDR':
            width, height, depth, color, comp, filt, interlace = struct.unpack('>IIBBBBB', payload)
            assert (depth, color, comp, filt, interlace) == (8, 2, 0, 0, 0)
        if tag == b'IDAT':
            compressed.extend(payload)
        if tag == b'IEND':
            assert offset == len(data)
            break
    assert (width, height) == (image['width'], image['height'])
    raw = zlib.decompress(compressed)
    stride = width * 3
    assert len(raw) == height * (stride + 1)
    pixels, previous = bytearray(), bytearray(stride)
    for y in range(height):
        start = y * (stride + 1)
        filter_type, row = raw[start], bytearray(raw[start+1:start+stride+1])
        assert filter_type <= 4
        for i in range(stride):
            left, up, corner = row[i-3] if i >= 3 else 0, previous[i], previous[i-3] if i >= 3 else 0
            if filter_type == 1:
                predictor = left
            elif filter_type == 2:
                predictor = up
            elif filter_type == 3:
                predictor = (left + up) // 2
            elif filter_type == 4:
                p = left + up - corner
                distances = (abs(p-left), abs(p-up), abs(p-corner))
                predictor = (left, up, corner)[distances.index(min(distances))]
            else:
                predictor = 0
            row[i] = (row[i] + predictor) & 255
        pixels.extend(row)
        previous = row
    return data, bytes(pixels)


class AnchoredArtifacts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'project'
        self.root.mkdir()
        self.directory = self.root / 'cache' / 'preview'
        self.directory.mkdir(parents=True)
        self.path = self.directory / 'image.png'
        self.data = png()
        self.path.write_bytes(self.data)
        self.identity = artifact.project_identity(self.root)

    def read(self, **kwargs):
        return artifact.read_png(self.path, root=self.root, root_identity=self.identity,
                                 width=1, height=1, **kwargs)

    def test_same_bytes_hash_size_and_official_full_gate(self):
        with patch.object(Path, 'open', side_effect=AssertionError('pathname payload open')):
            facts, data = self.read()
        self.assertEqual(data, self.data)
        self.assertEqual(facts, {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)})
        self.assertEqual(decode_inline({**facts, 'width': 1, 'height': 1,
            'inline': {'encoding': 'base64', 'data': base64.b64encode(data).decode()}})[1], b'\x10\x20\x30')
        for damaged in (self.data[:33], self.data[:-1], self.data + b'tail',
                        self.data[:-8] + b'BAD!' + self.data[-4:], png(idat=b'junk'),
                        png(idat=zlib.compress(b'\5abc')), png(idat=zlib.compress(b'\0abc') + b'tail')):
            self.path.write_bytes(damaged)
            reason = png_invalid_reason(self.path)
            self.assertIsInstance(reason, str)
            with self.assertRaises(artifact.ArtifactError) as caught:
                self.read()
            self.assertEqual(str(caught.exception), reason)

    def test_wrapper_stream_signature_append_and_error_compatibility(self):
        digest, size = [], []
        self.assertIsNone(png_invalid_reason(self.path, width=1, height=1, _fingerprint=digest, _verified_size=size))
        self.assertEqual((digest, size), ([hashlib.sha256(self.data).hexdigest()], [len(self.data)]))
        self.assertEqual(png_invalid_reason(self.directory / 'missing'), '缺失')
        self.path.write_bytes(b'')
        self.assertEqual(png_invalid_reason(self.path), '零字节')
        self.path.write_bytes(self.data)
        with patch('builtins.open', side_effect=OSError('read-error')):
            self.assertEqual(png_invalid_reason(self.path), '不可读或截断（read-error）')
        self.assertIsNone(_png_stream_invalid_reason(io.BytesIO(self.data), len(self.data)))

    def test_initial_leaf_and_in_project_ancestor_links_rejected_before_read(self):
        actual = self.directory / 'actual.png'
        self.path.rename(actual)
        self.path.symlink_to(actual.name)
        with patch('vtx.render.png_artifact.os.read', side_effect=AssertionError('read')):
            with self.assertRaises((OSError, ValueError)):
                self.read()
        self.path.unlink()
        actual.rename(self.path)
        moved = self.root / 'other'
        self.directory.rename(moved)
        self.directory.symlink_to(moved, target_is_directory=True)
        with patch('vtx.render.png_artifact.os.read', side_effect=AssertionError('read')):
            with self.assertRaises((OSError, ValueError)):
                self.read()

    def test_special_fifo_socket_directory_never_read_or_block(self):
        self.path.unlink()
        for make in (lambda: os.mkfifo(self.path), lambda: self.path.mkdir()):
            make()
            with patch('vtx.render.png_artifact.os.read', side_effect=AssertionError('read')):
                with self.assertRaises((OSError, ValueError)):
                    self.read()
            if self.path.is_dir():
                self.path.rmdir()
            else:
                self.path.unlink()
        self.path = self.root / 's'
        with socket.socket(socket.AF_UNIX) as sock:
            sock.bind(str(self.path))
            with self.assertRaises((OSError, ValueError)):
                self.read()

    def test_leaf_exchange_fifo_after_identity_check_uses_nonblock_and_no_read(self):
        original = os.open
        def exchange(name, flags, *args, **kwargs):
            if name == self.path.name and not flags & os.O_DIRECTORY:
                self.assertTrue(flags & os.O_NOFOLLOW)
                self.assertTrue(flags & os.O_NONBLOCK)
                self.path.unlink()
                os.mkfifo(self.path)
            return original(name, flags, *args, **kwargs)
        with patch('vtx.render.png_artifact.os.open', side_effect=exchange), \
                patch('vtx.render.png_artifact.os.read', side_effect=AssertionError('read')):
            with self.assertRaises((OSError, ValueError)):
                self.read()

    def test_ancestor_rename_exchange_and_root_exchange_fail_without_read(self):
        for root_swap in (False, True):
            target = self.root if root_swap else self.directory
            moved = target.with_name(target.name + '-moved')
            original = artifact.fstxn.pinned_parent
            @contextlib.contextmanager
            def exchange(*args, **kwargs):
                with original(*args, **kwargs) as pinned:
                    target.rename(moved)
                    target.mkdir()
                    try:
                        yield pinned
                    finally:
                        target.rmdir()
                        moved.rename(target)
            with patch.object(artifact.fstxn, 'pinned_parent', exchange), \
                    patch('vtx.render.png_artifact.os.read', side_effect=AssertionError('read')):
                with self.assertRaises((OSError, ValueError)):
                    self.read()

    def test_gc_and_different_leaf_after_open_fail_without_wrong_payload(self):
        original = os.open
        for delete_only in (False, True):
            self.path.write_bytes(self.data)
            def exchange(name, flags, *args, **kwargs):
                fd = original(name, flags, *args, **kwargs)
                if name == self.path.name and not flags & os.O_DIRECTORY:
                    self.path.unlink()
                    if not delete_only:
                        self.path.write_bytes(png(b'xyz'))
                return fd
            with patch('vtx.render.png_artifact.os.open', side_effect=exchange), \
                    patch('vtx.render.png_artifact.os.read', side_effect=AssertionError('read')):
                with self.assertRaises((OSError, ValueError)):
                    self.read()

    def test_short_growing_and_mutating_reads_fail_and_descriptors_close(self):
        original = os.read
        count_before = len(os.listdir('/proc/self/fd'))
        for mode in ('short', 'grow', 'mutate', 'cancel'):
            self.path.write_bytes(self.data)
            called = False
            def change(fd, size):
                nonlocal called
                if mode == 'short':
                    return b''
                if mode == 'cancel':
                    raise KeyboardInterrupt
                if not called:
                    called = True
                    self.path.write_bytes(self.data + b'x' if mode == 'grow' else png(b'xyz'))
                return original(fd, size)
            with patch('vtx.render.png_artifact.os.read', side_effect=change):
                with self.assertRaises((OSError, ValueError, KeyboardInterrupt)):
                    self.read(limit=len(self.data))
            self.assertEqual(len(os.listdir('/proc/self/fd')), count_before)

    def test_png_exact_8mib_boundaries_no_truncation(self):
        for size in (_LIMIT-1, _LIMIT, _LIMIT+1):
            data = png(padding=size-len(png())-12)
            self.assertEqual(len(data), size)
            self.path.write_bytes(data)
            if size > _LIMIT:
                with self.assertRaises(artifact.ArtifactError) as caught:
                    self.read(limit=_LIMIT)
                self.assertEqual(caught.exception.code, 'image.limit')
            else:
                self.assertEqual(self.read(limit=_LIMIT)[1], data)


class InlineTransport(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'timeline.json'
        self.raw = (' {"version":2,"project":{"fps":30,"width":1920,"height":1080,"duration_frames":16},'
                    '"sources":[],"video_tracks":[],"audio_tracks":[],"subtitles":[],"unknown":'
                    '{"big":9007199254740993,"precise":1.0000000000000001,"text":"原文\\u4e2d\\n"}}\n')
        self.path.write_text(self.raw)
        self.data = png()
        self.image = {'state': 'generated', 'kind': 'current-composite', 'approximate': False,
                      'width': 1, 'height': 1, 'bytes': len(self.data), 'sha256': hashlib.sha256(self.data).hexdigest()}

    def fence(self):
        return {str(p): (p.stat().st_mode, p.stat().st_size, p.stat().st_mtime_ns, p.stat().st_ctime_ns,
                         p.read_bytes() if p.is_file() else None) for p in [self.root, *self.root.rglob('*')]}

    def test_strict_type_permission_stale_default_zero_write_real_stat_fence(self):
        before = self.fence()
        with patch('vtx.pipeline.infra.Ctx.__init__', side_effect=AssertionError('Ctx')), \
                patch('subprocess.Popen', side_effect=AssertionError('process')):
            default = frame_timeline(self.path, frame=1)
            explicit_false = frame_timeline(self.path, frame=1, include_png=False)
            self.assertEqual(default, explicit_false)
            for value in (1, 0, None, 'true', [], {}):
                result = frame_timeline(self.path, frame=1, render=True, include_png=value)
                self.assertFalse(result['ok'])
                self.assertEqual(result['diagnostics'][0]['code'], 'image.inline_type')
            for kwargs in ({'include_png': True}, {'include_png': True, 'render': True,
                           'expected_content_version': 'stale'}, {'include_png': True, 'render': True, 'frame': True}):
                result = frame_timeline(self.path, **{'frame': 1, **kwargs})
                self.assertFalse(result['ok'])
                self.assertIsNone(result['image'])
        self.assertEqual(before, self.fence())
        self.assertEqual(default['reference']['document']['raw'], self.raw)

    def test_capability_is_exactly_additive_separate_from_read_only_and_store(self):
        caps = timeline_capabilities()['capabilities']
        self.assertEqual(caps['operations'], ['inspect', 'query', 'reference', 'capabilities'])
        self.assertTrue(caps['read_only'])
        self.assertEqual(caps['write_operations'], [])
        self.assertEqual(caps['frame'], {'schema': 'framivra.timeline.frame/v1', 'explicit_render_required': True,
            'inline_png': {'flag': '--include-png', 'encoding': 'base64', 'wire_limit_bytes': 8388608}})
        from vtx.api import timeline_store_capabilities
        self.assertNotIn('frame', timeline_store_capabilities())

    def test_inline_only_when_requested_raw_preserved_and_independent_decode(self):
        for include in (False, True):
            with patch('vtx.timeline_frame._render', return_value=(dict(self.image), self.data)):
                result = frame_timeline(self.path, frame=1, render=True, include_png=include)
            self.assertTrue(result['ok'])
            self.assertEqual(result['reference']['document']['raw'], self.raw)
            self.assertEqual('inline' in result['image'], include)
            if include:
                self.assertEqual(decode_inline(result['image']), (self.data, b'\x10\x20\x30'))

    def test_cli_usage_all_foreign_commands_and_explicit_render_required(self):
        before = self.fence()
        combinations = [('timeline', 'frame', '--frame', '1'), ('inspect',), ('query',), ('reference',),
                        ('capabilities',), ('store', 'capabilities'), ('validate',)]
        for args in combinations:
            proc = subprocess.run([sys.executable, '-B', '-m', 'vtx', '-t', str(self.path), *args, '--include-png'],
                                  cwd=REPO, capture_output=True)
            self.assertEqual(proc.returncode, 2, proc.stderr)
            if proc.stdout:
                self.assertFalse(json.loads(proc.stdout)['ok'])
        self.assertEqual(before, self.fence())

    def test_wire_ascii_unicode_raw_tokens_base64_and_actual_cli_newline_boundaries(self):
        from vtx.cli.timeline_frame import run_frame_command
        from types import SimpleNamespace
        for text in ('ascii', '中文😀\\\"\n'):
            for size in (_LIMIT-1, _LIMIT, _LIMIT+1):
                image = {**self.image, 'padding': text}
                with patch('vtx.timeline_frame._render', return_value=(image, self.data)):
                    baseline = frame_timeline(self.path, frame=1, render=True, include_png=True)
                wire = json.dumps(baseline, ensure_ascii=True, allow_nan=False).encode() + b'\n'
                image = {**self.image, 'padding': text + 'x' * (size-len(wire))}
                args = SimpleNamespace(command='timeline', timeline=str(self.path), subs_extra=['frame'],
                    frame='1', render=True, include_png=True, expected_content_version=None, max_png_bytes=None)
                with patch('vtx.timeline_frame._render', return_value=(image, self.data)), \
                        contextlib.redirect_stdout(io.StringIO()) as output:
                    code = run_frame_command(args, None)
                result = json.loads(output.getvalue())
                self.assertEqual(result['ok'], size <= _LIMIT)
                self.assertEqual(code, 0 if size <= _LIMIT else 1)
                if result['ok']:
                    self.assertEqual(len(output.getvalue().encode('ascii')), size)
                    self.assertEqual(result['reference']['document']['raw'], self.raw)
                    self.assertEqual(decode_inline(result['image'])[0], self.data)
                else:
                    self.assertIsNone(result['image'])
                    self.assertIsNone(result['reference'])
                    self.assertEqual(result['diagnostics'][0]['code'], 'output.limit')
                self.assertEqual(_wire_size(result, _LIMIT), len(output.getvalue())-1)

    def test_eager_8mib_png_base64_overflow_does_not_encode(self):
        image = {**self.image, 'bytes': _LIMIT}
        with patch('vtx.timeline_frame._render', return_value=(image, b'x'*_LIMIT)), \
                patch('vtx.timeline_frame.base64.b64encode', side_effect=AssertionError('oversized encoding')):
            result = frame_timeline(self.path, frame=1, render=True, include_png=True)
        self.assertFalse(result['ok'])
        self.assertEqual(result['diagnostics'][0]['code'], 'output.limit')
        self.assertIsNone(result['reference'])
        self.assertIsNone(result['image'])


if __name__ == '__main__':
    unittest.main()
