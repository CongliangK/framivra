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
        fd_directory = '/dev/fd' if sys.platform == 'darwin' else '/proc/self/fd'
        count_before = len(os.listdir(fd_directory))
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
            self.assertEqual(len(os.listdir(fd_directory)), count_before)

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
            'inline_png': {'flag': '--include-png', 'encoding': 'base64', 'wire_limit_bytes': 8388608},
            'prepare_gfx': {'flag': '--prepare-gfx', 'trust_flag': '--trust-html',
                            'kind': 'current-composite', 'preview_only': True, 'inline_required': True}})
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


class PreviewWindowCandidate(unittest.TestCase):
    def test_cli_only_exact_ingress_and_platform_capability(self):
        from vtx.cli.commands import _main
        from vtx.timeline_preview_window import SCHEMA

        argv = ['vtx', '-t', 'not-opened.json', 'timeline', 'preview-window', '--frame', '2',
                '--expected-content-version=sha256:' + 'a' * 64, '--render', '--include-media', '--prepare-inputs']
        cases = ((argv[:-1], 2, 'cli.arguments'), (argv + ['--dry-run'], 2, 'cli.arguments'),
                 (argv + ['--render'], 2, 'cli.arguments'),
                 (argv + ['--wheel-sha256=' + 'b' * 64], 2, 'cli.arguments'),
                 (argv[:-1] + ['--read-warm-svg'], 2, 'cli.arguments'),
                 (argv + ['--read-warm-svg'], 1, 'gfx.unsupported'),
                 (argv + ['--use-program-audio'], 1, 'audio.program_unsupported'),
                 (argv[:-1] + ['--use-program-audio'], 2, 'cli.arguments'),
                 (argv + ['--use-program-audio', '--read-warm-svg'], 2, 'cli.arguments'),
                 (argv, 1, 'platform.unsupported'))
        for args, code, diagnostic in cases:
            with patch.object(sys, 'argv', args), patch.object(sys, 'platform', 'darwin'), \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(_main(), code)
                result = json.loads(output.getvalue())
                self.assertEqual(set(result), {'schema', 'ok', 'diagnostics'})
                self.assertEqual(result['schema'], SCHEMA)
                self.assertFalse(result['ok'])
                self.assertEqual(result['diagnostics'][0]['code'], diagnostic)

        for platform in ('darwin', 'win32', 'linux'):
            with patch.object(sys, 'argv', ['vtx', 'capabilities']), patch.object(sys, 'platform', platform), \
                    patch('vtx.runtime.fstxn.directory_write_capability', return_value=platform != 'linux'), \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(_main(), 0)
                self.assertNotIn('preview_window', json.loads(output.getvalue())['capabilities'])
        with patch.object(sys, 'platform', 'linux'), patch('vtx.timeline_preview_window.supported', return_value=True):
            self.assertEqual(timeline_capabilities()['capabilities']['preview_window'],
                             {'schema': SCHEMA, 'inline_media': True, 'prepare_inputs': True})

    def test_program_admission_keeps_duck_work_bounded(self):
        from fractions import Fraction
        from vtx.pipeline import preview_program_audio as program

        root = Path('/project')
        rate = Fraction(30000, 1001)
        tracks = [{'name': 'bed', 'duck': True, 'duck_by': ['voice'], 'events': [{'src': 'bed'}]},
                  {'name': 'voice', 'events': [{'src': 'voice'}]}]
        stored = {'project': {'duration_frames': 900}, 'audio_tracks': tracks,
                  'sources': [{'id': sid, 'path': sid + '.wav'} for sid in ('bed', 'voice')]}
        self.assertEqual(program.inputs(stored, root, rate),
                         {'bed': root / 'bed.wav', 'voice': root / 'voice.wav'})
        for invalid in ({'duck_params': {'ratio': 1e99}}, {'duck_by': ['voice'] * 2},
                        {'duck_by': list('abcdefgh')}):
            with self.subTest(invalid=invalid), self.assertRaises(program.ProgramAudioError):
                program.inputs(dict(stored, audio_tracks=[dict(tracks[0], **invalid), tracks[1]]), root, rate)

    def test_program_pcm_rejects_false_riff_extents_and_cuts_absolute_ntsc_samples(self):
        from fractions import Fraction
        import struct
        import wave
        from vtx.pipeline.constraints import frame_sample
        from vtx.pipeline import preview_program_audio as program

        def wav(width, pcm):
            result = io.BytesIO()
            with wave.open(result, 'wb') as stream:
                stream.setparams((2, width, 48000, 0, 'NONE', 'not compressed'))
                stream.writeframes(pcm)
            return result.getvalue()

        stock_shape = wav(3, b'\x01\x02\x03\x04\x05\x06' * 50)
        self.assertEqual(program._pcm(stock_shape), (24, 50))
        for offset, value, code in ((4, 0xffffffff, '<I'), (20, 3, '<H'),
                                    (28, 8000, '<I'), (40, 0xffffffff, '<I')):
            corrupt = bytearray(stock_shape)
            struct.pack_into(code, corrupt, offset, value)
            with self.subTest(offset=offset), self.assertRaises(program.ProgramAudioError):
                program._pcm(corrupt)
        with self.assertRaises(program.ProgramAudioError):
            program._pcm(stock_shape + b'junk')

        rate = Fraction(30000, 1001)
        total, start, stop = (frame_sample(frame, rate) for frame in (7, 1, 4))
        self.assertNotEqual(stop - start, frame_sample(3, rate))
        pcm = b''.join(struct.pack('<hh', i, -i) for i in range(total))
        with wave.open(io.BytesIO(program._cut(wav(2, pcm), total, start, stop)), 'rb') as chunk:
            self.assertEqual(chunk.getnframes(), stop - start)
            self.assertEqual(chunk.readframes(stop - start), pcm[start * 4:stop * 4])

    @unittest.skipUnless(sys.platform == 'linux' and artifact.fstxn.directory_write_capability(),
                         'requires Linux directory anchoring')
    def test_first_frame_is_supervised_provisional_and_uses_the_same_bound_files(self):
        from fractions import Fraction
        from vtx.runtime import preview_window_isolation as isolation, preview_window_stream as stream
        from vtx.timeline_preview_window import SCHEMA, WIRE_LIMIT

        raw = (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1920, 1080, 8, 2, 0, 0, 0))
               + chunk(b'tEXt', b'unapproved\0metadata')
               + chunk(b'IDAT', zlib.compress(b'\0' * (1080 * (1 + 1920 * 3)))) + chunk(b'IEND', b''))
        nonce, version, fp, env = 'a' * 32, 'sha256:' + hashlib.sha256(b'timeline').hexdigest(), 'b' * 64, 'sha256:' + 'c' * 64
        first = {'frame': 10, 'window': [10, 40], 'segment': [10, 18], 'fps': {'num': 30, 'den': 1},
                 'content_version': version, 'input_fingerprint': fp, 'vpts': 0, 'width': 1920, 'height': 1080,
                 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(),
                 'base64': base64.b64encode(raw).decode('ascii'), 'environment': env}
        payload = b'\0\0\0\x18ftypisom\0\0\0\0'  # fake final producer, no media tools
        final = {'schema': SCHEMA, 'ok': True, 'content_version': version, 'start': 10, 'end': 40,
                 'fps': first['fps'], 'width': 1920, 'height': 1080, 'audio_sample_rate': 48000,
                 'audio_channels': 2, 'mime': 'video/mp4', 'byte_length': len(payload),
                 'sha256': hashlib.sha256(payload).hexdigest(), 'base64': base64.b64encode(payload).decode('ascii'),
                 '_input_environment': env}
        profile = (Path('/opt/trusted-source'), Path('/usr/bin/python3'), [], [], {})
        proof = {'launcher': 'framivra.preview-window.fixed-wheel/v1', 'wheel_sha256': 'd' * 64,
                 'runtime_projection_sha256': 'e' * 64}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            timeline, media = root / 'timeline.json', root / 'media.wav'
            timeline.write_bytes(b'timeline')
            roles = {timeline: [('timeline',)], media: [('source', 'voice', media.name)]}
            for case in ('good', 'before', 'after', 'profile-changed', 'nonce', 'position', 'sha', 'png', 'trailing',
                         'worker-final-failed'):
                with self.subTest(case=case):
                    media.write_bytes(b'original')
                    events, images = [], []

                    def reverify():
                        events.append('profile')
                        if case == 'profile-changed':
                            raise ValueError('profile changed')

                    def emit(image, check):
                        self.assertEqual(events, ['profile', 'inputs'])
                        self.assertFalse(images)
                        self.assertTrue(image['provisional'])
                        data = base64.b64decode(image['base64'])
                        self.assertNotIn(b'unapproved', data)
                        self.assertEqual(hashlib.sha256(data).hexdigest(), image['sha256'])
                        images.append(image)

                    def worker(argv, fds, check, limit, *, framed):
                        self.assertEqual(argv[-1], 'first-frame:' + nonce)
                        self.assertEqual({os.fstat(fd).st_ino for fd in fds}, {timeline.stat().st_ino, media.stat().st_ino})
                        if case == 'before':
                            media.write_bytes(b'changed!')
                        record = dict(first)
                        if case == 'position':
                            record['segment'] = [10, 19]
                        if case == 'sha':
                            record['sha256'] = '0' * 64
                        if case == 'png':
                            corrupt = raw[:-1] + bytes([raw[-1] ^ 1])
                            record.update(base64=base64.b64encode(corrupt).decode('ascii'),
                                          sha256=hashlib.sha256(corrupt).hexdigest())
                        framed_reply = stream.pack(stream.FIRST, {'nonce': 'f' * 32 if case == 'nonce' else nonce,
                                                                  'result': record}, limit)
                        framed.feed(framed_reply[:2])
                        self.assertFalse(images)
                        framed.feed(framed_reply[2:])
                        # The parent qualified/emitted it without receiving a terminal or an exit status.
                        self.assertEqual(len(images), 1)
                        if case == 'after':
                            media.write_bytes(b'changed!')
                        terminal = {'ok': False} if case == 'worker-final-failed' else final
                        framed.feed(stream.pack(stream.FINAL, {'nonce': nonce, 'result': terminal}, limit))
                        if case == 'trailing':
                            framed.feed(b'unexpected stdout')
                        return (1 if case == 'worker-final-failed' else 0), framed.finish(), b'private diagnostics'

                    def run():
                        with contextlib.ExitStack() as stack:
                            return isolation.execute(stack, timeline, roles, 10, version, lambda _caption, _project_digest: fp, 1024**2, WIRE_LIMIT,
                                                     lambda: None, root_identity=artifact.project_identity(root),
                                                     qualification=proof, recheck=lambda _caption: events.append('inputs'),
                                                     first_frame=(nonce, lambda env: (40, 18, Fraction(30)), reverify, emit))

                    with patch.object(isolation, 'available', return_value=True), \
                            patch.object(isolation, '_profile', return_value=profile), \
                            patch.object(isolation, '_read_worker', side_effect=worker):
                        if case == 'good':
                            result = run()
                            self.assertRegex(result['input_epoch'], r'^sha256:[0-9a-f]{64}$')
                            self.assertNotIn('_input_environment', result)
                        else:
                            with self.assertRaises((isolation.IsolationError, ValueError)):
                                run()
                        self.assertEqual(bool(images), case in ('good', 'after', 'trailing', 'worker-final-failed'))

    def test_preflight_rejects_declared_bgm_mid_window_gfx_and_unsafe_environment(self):
        from vtx.timeline_preview_window import _preflight, WindowError

        base = {'version': 2, 'project': {'fps': 29.97, 'width': 1920, 'height': 1080, 'duration_frames': 45},
                'sources': [{'id': 'unused', 'path': 'voice.wav'}],
                'video_tracks': [], 'audio_tracks': [], 'subtitles': []}
        bgm = {'id': 'score', 'path': 'score.json', 'bgm': {'output': 'master'}}
        gfx = {'name': 'gfx', 'kind': 'gfx', 'clips': [{'src': 'scene.html', 'dst_in': 40, 'dur': 5}]}
        with self.assertRaises(WindowError) as declaration:
            _preflight(dict(base, sources=[bgm]), 15)
        self.assertEqual(declaration.exception.code, 'plugins.unsupported')
        with self.assertRaises(WindowError) as graphics:
            _preflight(dict(base, video_tracks=[gfx]), 15)
        self.assertEqual(graphics.exception.code, 'gfx.unsupported')
        with patch.dict(os.environ, {'FFREPORT': ''}, clear=True), self.assertRaises(WindowError) as env:
            _preflight(base, 15)
        self.assertEqual(env.exception.code, 'environment.unsupported')
        with patch('vtx.timeline_preview_window._safe_environment'):
            with self.assertRaisesRegex(WindowError, 'off-window graphics'):
                _preflight(dict(base, video_tracks=[gfx]), 10)
            end, rate = _preflight(base, 10)
            self.assertEqual((end, rate.numerator, rate.denominator), (40, 30000, 1001))
            self.assertEqual(_preflight(base, 44)[0], 45)

    def test_warm_preflight_requires_exactly_one_intersecting_svg_clip(self):
        from vtx.timeline_preview_window import _preflight, WindowError

        base = {'version': 2, 'project': {'fps': 30, 'width': 1920, 'height': 1080, 'duration_frames': 60},
                'sources': [{'id': 'voice', 'path': 'voice.wav'}], 'video_tracks': [], 'audio_tracks': [],
                'subtitles': []}
        svg = {'src': 'scene.json', 'format': 'svg-sequence', 'dst_in': 30, 'dur': 15}
        html = {'src': 'off.html', 'dst_in': 0, 'dur': 10}

        def preflight(start, *clips):
            tracks = [{'name': f'gfx {i}', 'kind': 'gfx', 'clips': [clip]} for i, clip in enumerate(clips)]
            return _preflight(dict(base, video_tracks=tracks), start, allow_svg=True)

        with patch('vtx.timeline_preview_window._safe_environment'), patch.dict(os.environ, {}, clear=True):
            self.assertEqual(preflight(15, svg)[0], 45)
            for start, clips, error in ((15, (), 'requires an intersecting SVG clip'),
                                        (0, (svg,), 'requires an intersecting SVG clip'),
                                        (15, (svg, dict(svg, dst_in=40)), 'only one intersecting SVG clip'),
                                        (15, (svg, html), 'mixed HTML/SVG')):
                with self.subTest(start=start, clips=clips), self.assertRaisesRegex(WindowError, error):
                    preflight(start, *clips)

    @unittest.skipUnless(sys.platform == 'linux' and artifact.fstxn.directory_write_capability(),
                         'requires Linux directory anchoring')
    def test_svg_warm_projection_selects_only_one_pack_after_input_only_plan(self):
        import hashlib
        from vtx.runtime import preview_window_isolation as isolation
        from vtx.timeline_preview_window import _project_files

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / 'project'
            root.mkdir()
            timeline = root / 'timeline.json'
            stored = {'project': {'cache_dependencies': ['off.dep']}, 'sources': [], 'video_tracks': [
                {'kind': 'gfx', 'clips': [{'src': 'main.json', 'dur': 1}, {'src': 'off.json', 'dur': 1}]}]}
            timeline.write_text(json.dumps(stored))
            for name in ('main', 'off'):
                (root / f'{name}.json').write_text(json.dumps({'format': 'framivra-svg-sequence/v1',
                                                               'frames': [f'{name}.svg']}))
                (root / f'{name}.svg').write_text('<svg/>')
            (root / 'off.dep').write_bytes(b'other declaration')
            roles, _, _ = _project_files(timeline, stored, svg=True)
            self.assertEqual(set(roles), {root / name for name in
                                         ('timeline.json', 'main.json', 'main.svg', 'off.json', 'off.svg', 'off.dep')})
            rel = 'cache/gfx_packs/gfx_exp/svg_inkscape_v1/' + 'a' * 24 + '.vtxp'
            pack = root / rel
            pack.parent.mkdir(parents=True)
            pack.write_bytes(b'inert archive placeholder; worker boundary mocked')
            version = 'sha256:' + hashlib.sha256(timeline.read_bytes()).hexdigest()
            calls = []

            def worker(argv, descriptors, check, limit):
                bound = {Path(argv[i + 2]): int(argv[i + 1]) for i, arg in enumerate(argv) if arg == '--ro-bind-fd'}
                if argv[-1] == 'svg-plan':
                    calls.append('svg-plan')
                    self.assertEqual(set(bound), set(roles))
                    result = {'ok': True, 'fingerprint': 'b' * 64, 'pack': rel}
                else:
                    self.assertEqual(argv[-3:], ['b' * 64, mode, rel])
                    calls.append(mode)
                    self.assertEqual(set(bound), set(roles) | {pack})
                    self.assertEqual(os.fstat(bound[pack]).st_ino, pack.stat().st_ino)
                    if consume_status:
                        return consume_status, b'{"ok": false, "diagnostics": []}', b''
                    result = {'ok': True}
                    if mode == 'svg-media':
                        result['_input_environment'] = 'sha256:' + 'e' * 64
                return 0, json.dumps(result).encode(), b''

            def execute(start=0, recheck=None):
                calls.clear()
                with contextlib.ExitStack() as stack:
                    return isolation.execute(stack, timeline, roles, start, version, None, 1024**2, 8*1024**2,
                                             lambda: None, root_identity=artifact.project_identity(root),
                                             qualification={'test': True}, svg_mode=mode, recheck=recheck)

            profile = (Path('/opt/trusted-source'), Path('/usr/bin/python3'), [], [], {})
            with patch.object(isolation, 'available', return_value=True), \
                    patch.object(isolation, '_profile', return_value=profile), \
                    patch.object(isolation, '_project_mounts', side_effect=lambda files: tuple(files)), \
                    patch.object(isolation, '_read_worker', side_effect=worker):
                mode, consume_status = 'svg-read', 0
                self.assertEqual(execute(), {'ok': True})  # digest mode never carries a warm media marker or epoch
                self.assertEqual(calls, ['svg-plan', 'svg-read'])
                mode = 'svg-media'
                first = execute()
                self.assertEqual(calls, ['svg-plan', 'svg-media'])
                self.assertIs(first['warm'], True)
                self.assertRegex(first['input_epoch'], r'^sha256:[0-9a-f]{64}$')
                self.assertNotIn('_input_environment', first)
                self.assertEqual(execute(start=1)['input_epoch'], first['input_epoch'])
                pack.write_bytes(b'different whole archive bytes at the same canonical path')
                self.assertNotEqual(execute()['input_epoch'], first['input_epoch'])
                consume_status = 1  # e.g. corrupt pack; no fallback or successful receipt
                self.assertEqual(execute(), {'ok': False, 'diagnostics': []})
                self.assertEqual(calls, ['svg-plan', 'svg-media'])
                consume_status = 2  # even a JSON reply cannot certify an unsettled worker
                with self.assertRaisesRegex(isolation.IsolationError, 'settled result'):
                    execute()
                consume_status = 0
                with self.assertRaisesRegex(isolation.IsolationError, 'input changed'):
                    execute(recheck=lambda _: pack.write_bytes(b'changed after worker exited'))
                pack.unlink()
                with self.assertRaisesRegex(isolation.IsolationError, 'expected canonical type'):
                    execute()
                self.assertEqual(calls, ['svg-plan'])

    def test_project_mount_index_checks_leaf_mounts_and_refreshes_each_view(self):
        from vtx.runtime import preview_window_isolation as isolation
        root = Path('/project')
        file = root / 'many' / 'a.svg'
        view = {'/': ([], 'btrfs'), str(root): ([], 'ext4'), str(file): ([], 'tmpfs')}
        with patch.object(isolation, '_mount_view', return_value=view):
            self.assertEqual(isolation._project_mounts([file, file.parent]),
                             {file: (file, 'tmpfs'), file.parent: (root, 'ext4')})
            view[str(file)] = ([], 'nfs')
            with self.assertRaisesRegex(isolation.IsolationError, 'supported local project filesystem'):
                isolation._project_mounts([file])

    @unittest.skipUnless(sys.platform == 'linux' and artifact.fstxn.directory_write_capability(),
                         'requires Linux directory anchoring')
    def test_portable_svg_frames_share_one_readonly_projection_without_widening_admission(self):
        import shutil
        import uuid
        from vtx.runtime import preview_window_isolation as isolation, preview_window_portable_svg as portable

        with tempfile.TemporaryDirectory(dir='/tmp') as tmp, contextlib.ExitStack() as mocks:
            root = Path(tmp).resolve()
            timeline = root / 'timeline.json'
            manifest = root / 'gfx/demo.frames.json'
            directory = root / 'gfx/frames-svg'
            directory.mkdir(parents=True)
            timeline.write_bytes(b'timeline fixture')
            manifest.write_bytes(b'manifest fixture')
            (directory / 'unrelated.txt').write_bytes(b'must not enter the worker')
            version = 'sha256:' + hashlib.sha256(timeline.read_bytes()).hexdigest()
            roles = {timeline: [('timeline',)], manifest: [('svg-manifest', 'gfx/demo.frames.json')]}
            data = b'<svg/>'
            expected = {f'f{i:04d}.svg' for i in range(900)}
            folded = hashlib.sha256(b'framivra:portable-v2:svg:v1\0')
            for i, name in enumerate(sorted(expected)):
                leaf = directory / name
                leaf.write_bytes(data)
                roles[leaf] = [('svg-frame', 'gfx/demo.frames.json', leaf.relative_to(root).as_posix())]
                folded.update(i.to_bytes(4, 'big') + len(data).to_bytes(8, 'big') + data)
            rel = 'cache/gfx_packs/gfx_exp/svg_inkscape_v1/' + 'a' * 24 + '.vtxp'
            pack = root / rel
            pack.parent.mkdir(parents=True)
            pack.write_bytes(b'inert archive placeholder')
            fd_seen, mutate, unsettled = [], False, False

            def worker(argv, descriptors, check, limit, *, keep_on_signal):
                self.assertTrue(keep_on_signal)
                bound = {Path(argv[i + 2]): int(argv[i + 1]) for i, arg in enumerate(argv) if arg == '--ro-bind-fd'}
                stage_fd = bound[directory]
                self.assertEqual(set(os.listdir(stage_fd)), expected)
                self.assertNotEqual(artifact.fstxn.entry_identity(os.fstat(stage_fd)),
                                    artifact.fstxn.entry_identity(directory.stat()))
                fd_seen.append(stage_fd)
                if argv[-1] == 'svg-plan':
                    self.assertEqual(set(bound), {timeline, manifest, directory})
                    result = {'ok': True, 'fingerprint': 'b' * 64, 'pack': rel}
                else:
                    self.assertEqual(argv[-2:], ['svg-media', rel])
                    self.assertEqual(set(bound), {timeline, manifest, directory, pack})
                    if unsettled:
                        error = isolation.IsolationError('mock unreaped worker')
                        error._vtx_isolation_unsettled = True
                        raise error
                    if mutate:
                        (directory / 'f0000.svg').write_bytes(b'<bad/>')
                    result = {'ok': True, '_input_environment': 'sha256:' + 'e' * 64}
                return 0, json.dumps(result).encode(), b''

            prefix = f'framivra-preview-test-{uuid.uuid4().hex}-'
            slot = portable._SHM / f'{prefix}{os.geteuid()}'
            profile = (Path('/opt/trusted-source'), Path('/usr/bin/python3'), [], [], {})
            for stub in (patch.object(isolation, 'available', return_value=True),
                         patch.object(isolation, '_profile', return_value=profile),
                         patch.object(portable, 'TIMELINE_VERSION', version),
                         patch.object(portable, 'MANIFEST_BYTES', (manifest.stat().st_size,
                                      hashlib.sha256(manifest.read_bytes()).hexdigest())),
                         patch.object(portable, '_TOTAL', len(expected) * len(data)),
                         patch.object(portable, '_FRAME_SHA', folded.hexdigest()),
                         patch.object(portable, '_SLOT_PREFIX', prefix)):
                mocks.enter_context(stub)
            launch = mocks.enter_context(patch.object(isolation, '_read_worker', side_effect=worker))

            def execute(mode='svg-media', content_version=version):
                with contextlib.ExitStack() as stack:
                    return isolation.execute(stack, timeline, roles, 0, content_version, None, 1024**2,
                                             8*1024**2, lambda: None, root_identity=artifact.project_identity(root),
                                             qualification={'test': True}, svg_mode=mode)

            for mode, digest in ((None, version), ('svg-media', 'sha256:' + '0' * 64)):
                with self.assertRaisesRegex(isolation.IsolationError, 'too many separately anchored'):
                    execute(mode, digest)
            frame_roles = roles[directory / 'f0000.svg']
            frame_roles.append(('dependency', 'gfx/frames-svg/f0000.svg'))
            with self.assertRaisesRegex(isolation.IsolationError, 'fixed SVG input roles'):
                execute()
            frame_roles.pop()
            linked = directory / 'unrelated-link.svg'
            os.link(directory / 'f0000.svg', linked)
            try:
                with self.assertRaisesRegex(isolation.IsolationError, 'type, links or size'):
                    execute()
            finally:
                linked.unlink()
            self.assertFalse(slot.exists())
            manifest.write_bytes(b'changed manifest')
            with self.assertRaisesRegex(isolation.IsolationError, 'manifest does not match'):
                execute()
            manifest.write_bytes(b'manifest fixture')
            slot.touch(exist_ok=False)
            try:
                with self.assertRaisesRegex(isolation.IsolationError, 'staging slot is occupied'):
                    execute()
                self.assertTrue(slot.is_file())
            finally:
                slot.unlink()
            launch.assert_not_called()
            self.assertIs(execute()['warm'], True)
            self.assertEqual(fd_seen, [fd_seen[0]] * 2)
            self.assertFalse(slot.exists())
            mutate = True
            with self.assertRaisesRegex(isolation.IsolationError, 'frames do not match'):
                execute()
            self.assertFalse(slot.exists())
            (directory / 'f0000.svg').write_bytes(data)
            mutate, unsettled = False, True
            try:
                with self.assertRaisesRegex(isolation.IsolationError, 'unreaped worker') as error:
                    execute()
                self.assertTrue(error.exception._vtx_isolation_unsettled)
                self.assertEqual({p.name for p in slot.iterdir()}, expected)
                call_count = launch.call_count
                with self.assertRaisesRegex(isolation.IsolationError, 'staging slot is occupied'):
                    execute()
                self.assertEqual(launch.call_count, call_count)
            finally:
                if slot.is_dir():  # Unique fixture slot; worker subprocesses were mocked.
                    slot.chmod(0o700)
                    shutil.rmtree(slot)

    @unittest.skipUnless(sys.platform == 'linux' and artifact.fstxn.directory_write_capability(),
                         'requires Linux directory anchoring')
    def test_svg_media_and_external_caption_share_a_pinned_input_epoch(self):
        import wave
        from vtx.cache.cache_state import verify_input_identity
        from vtx.cache.session import pack_path
        from vtx.pipeline.infra import Ctx
        from vtx.render import gfx_svg
        from vtx.runtime import preview_window_isolation as isolation, preview_window_svg as svg
        from vtx.timeline_preview_window import (SCHEMA, WindowError, _caption_reader, _context,
                                                 _dispatch, _svg_projection)

        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as mocks:
            root = Path(tmp).resolve()
            timeline, caption = root / 'timeline.json', root / 'caption.json'
            audio = root / 'voice.wav'
            with wave.open(str(audio), 'wb') as stream:
                stream.setparams((2, 2, 48000, 0, 'NONE', 'not compressed'))
                stream.writeframes(b'\0' * (2 * 48000 * 4))
            original = b'[{"in":28,"out":32,"text":"first"}]'
            caption.write_bytes(original)
            frame = '<svg xmlns="http://www.w3.org/2000/svg" width="1920" height="1080" viewBox="0 0 1920 1080"><rect width="1920" height="1080" fill="red"/></svg>'
            (root / 'frame.svg').write_text(frame, encoding='utf-8')
            (root / 'scene.json').write_text(json.dumps(dict(format=gfx_svg.FORMAT, fps=29.97,
                width=1920, height=1080, frame_count=2, frames=['frame.svg', 'frame.svg'])))
            clip = dict(format='svg-sequence', src='scene.json', dst_in=29, dur=2)
            stored = dict(version=2, project=dict(fps=29.97, width=1920, height=1080, duration_frames=59),
                          sources=[dict(id='voice', path=audio.name)],
                          video_tracks=[dict(name='graphics', kind='gfx', clips=[clip])],
                          audio_tracks=[], subtitles=[], subtitles_src=caption.name)
            raw = json.dumps(stored)
            timeline.write_text(raw, encoding='utf-8')
            version = 'sha256:' + hashlib.sha256(raw.encode()).hexdigest()
            reference = {'document': {'raw': raw}, 'content_version': version}
            identity = artifact.project_identity(root)
            proof = {'launcher': 'framivra.preview-window.fixed-wheel/v1', 'wheel_sha256': 'a' * 64,
                     'runtime_projection_sha256': 'b' * 64}
            profile = (Path('/opt/trusted-source'), Path('/usr/bin/python3'), [], [], {})
            for stub in (patch.dict(os.environ, {'VTX_CONFIG': str(root / 'absent-config.json')}, clear=True),
                         patch.object(gfx_svg._PosixRuntimeIdentity, 'engine_identity', return_value={'fixture': 'engine'}),
                         patch.object(gfx_svg._PosixRuntimeIdentity, 'fontconfig_identity',
                                      return_value={'fixture': 'fontconfig'}),
                         patch.object(svg, 'check_tools'), patch.object(isolation, 'check_mount'),
                         patch.object(isolation, 'available', return_value=True),
                         patch.object(isolation, '_profile', return_value=profile),
                         patch.object(isolation, 'managed_font', return_value=None),
                         patch('vtx.timeline_preview_window._safe_environment')):
                mocks.enter_context(stub)
            pack = pack_path(Ctx(timeline), clip)
            pack.parent.mkdir(parents=True)
            pack.write_bytes(b'opaque pack; consumer boundary mocked')
            selected = str(pack.relative_to(root))
            expected_paths = {timeline, caption, audio, root / 'scene.json', root / 'frame.svg'}
            plan_fd = None
            text, mutate = 'first', None
            pin_binding = isolation._pin_binding

            def worker(argv, descriptors, check, limit):
                nonlocal plan_fd
                bound = {Path(argv[i + 2]): int(argv[i + 1]) for i, arg in enumerate(argv) if arg == '--ro-bind-fd'}
                self.assertEqual(set(bound.values()), set(descriptors))
                if argv[-1] == 'svg-plan':
                    self.assertEqual(set(bound), expected_paths)
                    plan_fd = bound[caption]
                    self.assertEqual(argv[-2], hashlib.sha256(original.replace(b'first', text.encode())).hexdigest())
                    result = _svg_projection(timeline, reference, identity, start, check, argv[-2],
                                             mode='svg-plan', relative=None)
                    self.assertEqual(result['pack'], selected)
                else:
                    self.assertEqual(argv[-2:], ['svg-media', selected])
                    self.assertEqual(set(bound), expected_paths | {pack})
                    self.assertEqual(bound[caption], plan_fd)
                    with contextlib.ExitStack() as stack:
                        binding = pin_binding(stack, caption, root, single_link=True, root_identity=identity)
                        c = _context(timeline, check, {}, lambda: raw.encode(), _caption_reader(*binding, check))
                        self.assertEqual(c.input_fingerprint, argv[-3])
                        self.assertEqual(c.tl['subtitles'], [{'in': 28, 'out': 32, 'text': text}])
                        verify_input_identity(c)
                    if mutate is not None:
                        mutate.write_bytes(b'late change')
                    result = {'schema': SCHEMA, 'ok': True, 'content_version': version, 'start': start,
                              'end': min(start + 30, 59), '_input_environment': 'sha256:' + 'e' * 64}
                return 0, json.dumps(result).encode(), b''

            launch = mocks.enter_context(patch.object(isolation, '_read_worker', side_effect=worker))
            start = 0
            def dispatch(mode='svg-media', qualification=proof):
                return _dispatch(timeline, reference, identity, start, lambda: None, qualification, svg_mode=mode)

            for mode, qualification, message in ((None, proof, 'intersects a GFX clip'),
                ('svg-media', None, 'explicitly qualified fixed wheel'), ('svg-read', proof, 'requires inline subtitles')):
                with self.subTest(mode=mode), self.assertRaisesRegex(WindowError, message):
                    dispatch(mode, qualification)
            launch.assert_not_called()
            first = dispatch()
            self.assertIs(first['warm'], True)
            start = 30
            self.assertEqual(dispatch()['input_epoch'], first['input_epoch'])
            text = 'other'
            caption.write_bytes(original.replace(b'first', text.encode()))
            changed = dispatch()['input_epoch']
            self.assertNotEqual(changed, first['input_epoch'])
            pack.write_bytes(b'different pack at the same canonical path')
            self.assertNotEqual(dispatch()['input_epoch'], changed)

            def aliased_pack(stack, path, *args, **kwargs):
                # Emulate a single-link bind alias: a different path, same inode.
                return pin_binding(stack, caption if path == pack else path, *args, **kwargs)

            launch.reset_mock()
            with patch.object(isolation, '_pin_binding', side_effect=aliased_pack), \
                    self.assertRaisesRegex(isolation.IsolationError, 'pack cannot share another input role'):
                dispatch()
            launch.assert_called_once()
            mutate = caption
            with self.assertRaisesRegex(isolation.IsolationError, 'input changed'):
                dispatch()

    def test_container_count_does_not_bless_wrong_pts_or_aac_tail(self):
        from fractions import Fraction
        from vtx.timeline_preview_window import _probe_facts, WindowError

        rate = Fraction(30000, 1001)
        streams = [{'index': 0, 'codec_type': 'video', 'codec_name': 'h264', 'width': 1920, 'height': 1080,
                    'time_base': '1/30000', 'start_pts': 0, 'duration_ts': 1001, 'avg_frame_rate': str(rate)},
                   {'index': 1, 'codec_type': 'audio', 'codec_name': 'aac', 'sample_rate': '48000', 'channels': 2,
                    'time_base': '1/48000', 'start_pts': 0, 'duration_ts': 1602}]
        vp = {'stream_index': 0, 'pts': 0, 'duration': 1001}
        priming = {'stream_index': 1, 'pts': -1024, 'duration': 1024,
                   'side_data_list': [{'side_data_type': 'Skip Samples', 'skip_samples': 1024}]}
        first = {'stream_index': 1, 'pts': 0, 'duration': 1024}
        tail = {'stream_index': 1, 'pts': 1024, 'duration': 578}
        self.assertEqual(_probe_facts({'streams': streams, 'packets': [priming, vp, first, tail]}, rate, 1, 1602),
                         {'width': 1920, 'height': 1080, 'audio_sample_rate': 48000, 'audio_channels': 2})
        # Same coded dimensions and exact clocks, but Chromium presents this
        # anamorphic stream as 1920x1920 rather than the project's 1920x1080.
        for sar, dar, accepted in [('1:1', '16:9', True), ('9:16', '1:1', False)]:
            info = {'streams': [dict(streams[0], sample_aspect_ratio=sar, display_aspect_ratio=dar), streams[1]],
                    'packets': [priming, vp, first, tail]}
            if accepted:
                self.assertEqual(_probe_facts(info, rate, 1, 1602)['width'], 1920)
            else:
                with self.assertRaises(WindowError) as caught:
                    _probe_facts(info, rate, 1, 1602)
                self.assertEqual(caught.exception.code, 'media.streams')
        for packets, code in (([priming, dict(vp, pts=1), first, tail], 'media.video_clock'),
                              ([priming, vp, first, dict(tail, duration=1024)], 'media.audio_clock'),
                              ([dict(priming, side_data_list=[]), vp, first, tail], 'media.audio_clock')):
            with self.assertRaises(WindowError) as caught:
                _probe_facts({'streams': streams, 'packets': packets}, rate, 1, 1602)
            self.assertEqual(caught.exception.code, code)

    @unittest.skipUnless(sys.platform == 'linux' and artifact.fstxn.directory_write_capability(),
                         'requires Linux directory anchoring')
    def test_isolated_projection_uses_only_pinned_files_and_no_host_scratch(self):
        from vtx.cache import cache_state
        from vtx.pipeline.infra import Ctx
        from vtx.runtime import preview_window_isolation as isolation
        from vtx.timeline_preview_window import SCHEMA, _dispatch, WindowError, WIRE_LIMIT

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / 'project'
            root.mkdir()
            timeline = root / 'timeline.json'
            media = root / 'audio.wav'
            small, dependency = root / 'small.wav', root / 'declared.dat'
            caption = root / 'captions' / 'declared.srt'
            caption.parent.mkdir()
            caption.write_bytes(b'00:00:00,100 --> 00:00:00,900\ncaption')
            (caption.parent / 'undeclared.srt').write_bytes(b'not projected')
            media.write_bytes(b'a' * cache_state.SAMPLE_BYTES + b'middle' + b'z' * cache_state.SAMPLE_BYTES)
            small.write_bytes(b'media')
            dependency.write_bytes(b'declared content')
            doc = {'version': 2, 'project': {'fps': 30, 'width': 1920, 'height': 1080, 'duration_frames': 60,
                                              'cache_dependencies': ['./declared.dat']},
                   'sources': [{'id': 'unused', 'path': media.name}, {'id': 'small', 'path': small.name}],
                   'video_tracks': [], 'audio_tracks': [], 'subtitles': [], 'subtitles_src': 'captions/declared.srt'}
            timeline.write_bytes(json.dumps(doc).encode())
            expected_fingerprint = Ctx(timeline).input_fingerprint
            file_digest = cache_state.file_digest

            def trusted_only(path):
                self.assertNotIn(Path(path), {media, small, dependency})
                return file_digest(path)

            wire = json.dumps({'schema': SCHEMA, 'ok': False, 'diagnostics': []}).encode()

            def execute(**changes):
                raw = json.dumps(dict(doc, **changes))
                data = raw.encode()
                timeline.write_bytes(data)
                reference = {'document': {'raw': raw}, 'content_version': 'sha256:' + hashlib.sha256(data).hexdigest()}
                return _dispatch(timeline, reference, artifact.project_identity(root), 0, lambda: None)

            def worker(argv, descriptors, check, limit):
                binds = {(int(argv[i + 1]), Path(argv[i + 2])) for i, arg in enumerate(argv)
                         if arg == '--ro-bind-fd'}
                self.assertEqual({p for _, p in binds}, {timeline, media, small, dependency, caption})
                self.assertEqual({fd for fd, _ in binds}, set(descriptors))
                for fd, dest in binds:
                    self.assertEqual(os.fstat(fd).st_ino, dest.stat().st_ino)
                self.assertEqual(argv[-2:], [expected_fingerprint, 'unqualified'])
                self.assertNotIn('--bind', argv)
                self.assertEqual(argv[argv.index('--tmpfs') + 1], str(root / 'cache/preview'))
                self.assertFalse((root / 'cache').exists())
                return 1, wire, b''

            profile = (Path('/opt/trusted-source'), Path('/usr/bin/python3'), [], [], {})
            with patch.object(isolation, 'available', return_value=True), \
                    patch.object(isolation, '_profile', return_value=profile), \
                    patch.object(isolation, 'managed_font', return_value=None), \
                    patch('vtx.timeline_preview_window._safe_environment'), \
                    patch.object(cache_state, 'media_stat_digest', side_effect=AssertionError('path-based source read')), \
                    patch.object(cache_state, 'file_digest', side_effect=trusted_only), \
                    patch.object(isolation, '_read_worker', side_effect=worker) as launch:
                self.assertFalse(execute()['ok'])
                self.assertFalse((root / 'cache').exists())
                for name in (str(caption), 'captions/../declared.srt', 'scene.html',
                             'cache/preview/declared.srt', timeline.name):
                    with self.subTest(name=name), self.assertRaises(WindowError):
                        execute(subtitles_src=name)
                with self.assertRaisesRegex(WindowError, 'mutually exclusive'):
                    execute(subtitles=[{'in': 3, 'out': 27, 'text': 'inline'}])
                with self.assertRaisesRegex(WindowError, 'cannot share another input role'):
                    execute(project=dict(doc['project'], cache_dependencies=['./captions/declared.srt']))
                linked = root / 'linked.srt'
                linked.symlink_to(caption)
                fifo = root / 'pipe.srt'
                os.mkfifo(fifo)
                for unsafe in (linked, fifo, root / 'missing.srt'):
                    with self.subTest(unsafe=unsafe.name), self.assertRaisesRegex(isolation.IsolationError, 'file type'):
                        execute(subtitles_src=unsafe.name)
                linked.unlink()
                os.link(caption, linked)
                with self.assertRaisesRegex(isolation.IsolationError, 'multiple links'):
                    execute()
                linked.unlink()
                large = root / 'large.json'
                with large.open('wb') as stream:
                    stream.truncate(WIRE_LIMIT + 1)
                with self.assertRaisesRegex(WindowError, 'candidate byte limit'):
                    execute(subtitles_src=large.name)
                self.assertEqual(launch.call_count, 1)
                for changed in (media, caption):
                    def mutate(*args):
                        changed.write_bytes(b'changed')
                        return 1, wire, b''
                    launch.side_effect = mutate
                    with self.subTest(changed=changed), self.assertRaisesRegex(isolation.IsolationError, 'input changed'):
                        execute()

    @unittest.skipUnless(sys.platform == 'linux' and artifact.fstxn.directory_write_capability(),
                         'requires Linux directory anchoring')
    def test_window_ctx_uses_production_subtitle_semantics_on_pinned_bytes(self):
        from vtx.pipeline.infra import Ctx
        from vtx.pipeline.stages import validate_core
        from vtx.runtime import preview_window_isolation as isolation
        from vtx.timeline_preview_window import _caption_reader, _context

        cases = [
            ('clip.SRT', b'\xef\xbb\xbf00:00:00,050 --> 00:00:01.001 X1:5\r\nfirst\r\nsecond\r\n',
             [{'in': 1, 'out': 30, 'text': 'first\\Nsecond'}]),
            ('clip.json', b'{"subtitles":[{"in":1,"out":30,"text":"caption","ignored":"metadata"}]}',
             [{'in': 1, 'out': 30, 'text': 'caption'}]),
            ('bad.json', b'[{"in":true,"out":30,"text":"caption"}]', None),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            timeline = root / 'timeline.json'
            for name, payload, expected in cases:
                with self.subTest(name=name), contextlib.ExitStack() as stack:
                    caption = root / name
                    caption.write_bytes(payload)
                    data = json.dumps({'version': 2, 'project': {'fps': 29.97, 'width': 1920, 'height': 1080,
                                                                  'duration_frames': 60},
                                       'sources': [], 'video_tracks': [], 'audio_tracks': [],
                                       'subtitles_src': name}).encode()
                    timeline.write_bytes(data)
                    production = Ctx(timeline)
                    binding = isolation._pin_binding(stack, caption, root, single_link=True)
                    read = _caption_reader(*binding, lambda: None)
                    open_path = Path.open

                    def no_caption_reopen(path, *args, **kwargs):
                        self.assertNotEqual(path, caption, 'Ctx reopened the caption pathname')
                        return open_path(path, *args, **kwargs)

                    with patch.object(Path, 'open', no_caption_reopen):
                        window = _context(timeline, lambda: None, {}, lambda: data, read)
                        errors = validate_core(window)
                    self.assertEqual(window.input_fingerprint, production.input_fingerprint)
                    self.assertEqual(window.subtitles_origin, production.subtitles_origin)
                    self.assertEqual(window.tl['subtitles'], expected if expected is not None else [])
                    self.assertEqual(errors, validate_core(production))
                    self.assertEqual(bool(errors), expected is None)

    @unittest.skipUnless(sys.platform == 'linux' and artifact.fstxn.directory_write_capability(),
                         'requires Linux directory anchoring')
    def test_input_epoch_hashes_full_bound_inputs_and_not_window_endpoints(self):
        import hashlib
        from vtx.cache.cache_state import media_stat_digest
        from vtx.runtime import preview_window_isolation as isolation
        from vtx.timeline_preview_window import _project_files

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / 'project'
            root.mkdir()
            timeline, media, dependency = (root / name for name in ('timeline.json', 'audio.wav', 'meta.dat'))
            stored = {'sources': [{'id': 'voice', 'path': media.name}],
                      'project': {'cache_dependencies': [dependency.name]}}
            timeline.write_text(json.dumps(stored), encoding='utf-8')
            media.write_bytes(b'a' * (512 * 1024))
            dependency.write_bytes(b'dependency')
            version = 'sha256:' + hashlib.sha256(timeline.read_bytes()).hexdigest()
            old_fingerprint = media_stat_digest(media)
            media_times = media.stat()
            roles, _, _ = _project_files(timeline, stored)
            proof = {'launcher': 'framivra.preview-window.fixed-wheel/v1', 'wheel_sha256': 'a' * 64,
                     'runtime_projection_sha256': 'b' * 64}
            profile = (Path('/opt/trusted-source'), Path('/usr/bin/python3'), [], [], {})
            environment = isolation.identity_digest('observed-environment', 1, 'font A')

            def worker(argv, descriptors, check, limit):
                self.assertEqual(argv[-1], 'input-epoch')
                start = int(argv[-4])
                return 0, json.dumps({'ok': True, 'start': start, 'end': start + 30,
                                      '_input_environment': environment}).encode(), b''

            def program_worker(argv, descriptors, check, limit):
                from vtx.pipeline.preview_program_audio import manifest

                self.assertEqual(argv[-2:], ['program-audio', manifest([media], {
                    str(media): (media.stat().st_size, hashlib.sha256(media.read_bytes()).hexdigest())})])
                start = int(argv[-5])
                return 0, json.dumps({'ok': True, 'start': start, 'end': start + 30, 'program_audio': True,
                                      '_input_environment': environment}).encode(), b''

            def render(start, run=worker, qualification=proof, *, program_paths=()):
                with patch.object(isolation, 'available', return_value=True), \
                        patch.object(isolation, '_profile', return_value=profile), \
                        patch.object(isolation, '_read_worker', side_effect=run), contextlib.ExitStack() as stack:
                    return isolation.execute(stack, timeline, roles, start, version, lambda *_: old_fingerprint,
                                             1024**2, 8*1024**2, lambda: None,
                                             root_identity=artifact.project_identity(root), qualification=qualification,
                                             program_paths=program_paths)

            def edit_middle(byte):
                with media.open('r+b') as stream:
                    stream.seek(256 * 1024)
                    stream.write(byte)
                os.utime(media, ns=(media_times.st_atime_ns, media_times.st_mtime_ns))

            first, repeat, second_window = render(0), render(0), render(30)
            self.assertNotIn('_input_environment', first)
            self.assertRegex(first['input_epoch'], r'^sha256:[0-9a-f]{64}$')
            self.assertEqual(first, repeat)
            self.assertEqual((second_window['start'], second_window['end']), (30, 60))
            self.assertEqual(second_window['input_epoch'], first['input_epoch'])
            program = render(0, program_worker, program_paths=(media,))
            self.assertIs(program['program_audio'], True)
            self.assertNotEqual(program['input_epoch'], first['input_epoch'])
            self.assertEqual(render(30, program_worker, program_paths=(media,))['input_epoch'], program['input_epoch'])
            with self.assertRaisesRegex(isolation.IsolationError, 'missing its program audio proof'):
                render(0, lambda *args: (0, b'{"ok": true}', b''), program_paths=(media,))
            environment = isolation.identity_digest('different-observed-environment')
            self.assertNotEqual(render(0)['input_epoch'], first['input_epoch'])
            environment = isolation.identity_digest('observed-environment', 1, 'font A')
            self.assertNotEqual(render(0, qualification=dict(proof, runtime_projection_sha256='c' * 64))
                                ['input_epoch'], first['input_epoch'])
            edit_middle(b'b')
            self.assertEqual(media_stat_digest(media), old_fingerprint)
            self.assertEqual('sha256:' + hashlib.sha256(timeline.read_bytes()).hexdigest(), version)
            changed = render(0)['input_epoch']
            self.assertNotEqual(changed, first['input_epoch'])
            dependency.write_bytes(b'dependency!')
            self.assertNotEqual(render(0)['input_epoch'], changed)

            def mutated(*args):
                edit_middle(b'c')
                return worker(*args)

            with self.assertRaisesRegex(isolation.IsolationError, 'input changed'):
                render(0, mutated)
            with self.assertRaisesRegex(isolation.IsolationError, 'environment could not be verified'):
                render(0, lambda *args: (0, b'{"ok": true}', b''))

    @unittest.skipUnless(sys.platform == 'linux' and artifact.fstxn.directory_write_capability(),
                         'requires Linux directory anchoring')
    def test_caption_epoch_binds_exact_projected_bytes_across_ntsc_windows(self):
        import fcntl
        import wave
        from vtx.cache import cache_state
        from vtx.runtime import preview_window_isolation as isolation
        from vtx.timeline_preview_window import SCHEMA, _caption_reader, _context, _dispatch

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / 'project'
            root.mkdir()
            timeline, caption, media = (root / name for name in ('timeline.json', 'clip.SRT', 'unused.wav'))
            payload = b'\xef\xbb\xbf00:00:00,050 --> 00:00:01.001 X1:5\r\nfirst\r\nsecond\r\n'
            caption.write_bytes(payload)
            with wave.open(str(media), 'wb') as stream:
                stream.setparams((2, 2, 48000, 0, 'NONE', 'not compressed'))
                stream.writeframes(b'\0' * 6400)
            stored = {'version': 2, 'project': {'fps': 29.97, 'width': 1920, 'height': 1080, 'duration_frames': 59},
                      'sources': [{'id': 'unused', 'path': media.name}], 'video_tracks': [], 'audio_tracks': [],
                      'subtitles': [], 'subtitles_src': caption.name}
            raw = json.dumps(stored)
            timeline.write_bytes(raw.encode())
            version = 'sha256:' + hashlib.sha256(raw.encode()).hexdigest()
            reference = {'document': {'raw': raw}, 'content_version': version}
            proof = {'launcher': 'framivra.preview-window.fixed-wheel/v1', 'wheel_sha256': 'a' * 64,
                     'runtime_projection_sha256': 'b' * 64}
            profile = (Path('/opt/trusted-source'), Path('/usr/bin/python3'), [], [], {})
            environment = isolation.identity_digest('unchanged environment')
            root_identity = artifact.project_identity(root)
            caption_ino = caption.stat().st_ino
            pread = os.pread
            file_digest = cache_state.file_digest

            def digest_other_files(path):
                self.assertNotEqual(path, caption, 'receipt reopened the caption by path')
                return file_digest(path)

            def observe(start, expected_text):
                host_reads, worker_reads = [], []
                in_worker = False
                bound_fd = None

                def read(fd, count, offset):
                    data = pread(fd, count, offset)
                    if data and os.fstat(fd).st_ino == caption_ino:
                        (worker_reads if in_worker else host_reads).append((fd, offset, data))
                    return data

                def worker(argv, descriptors, check, limit):
                    nonlocal bound_fd, in_worker
                    binds = [(Path(argv[i + 2]), int(argv[i + 1])) for i, arg in enumerate(argv)
                             if arg == '--ro-bind-fd']
                    self.assertCountEqual([dest for dest, _ in binds], [timeline, caption, media])
                    self.assertCountEqual([fd for _, fd in binds], descriptors)
                    bound_fd = dict(binds)[caption]
                    self.assertEqual(os.fstat(dict(binds)[media]).st_ino, media.stat().st_ino)
                    self.assertEqual(fcntl.fcntl(bound_fd, fcntl.F_GETFL) & os.O_ACCMODE, os.O_RDONLY)
                    self.assertEqual((os.fstat(bound_fd).st_ino, argv[-4], argv[-3], argv[-1]),
                                     (caption_ino, str(start), version, 'input-epoch'))
                    in_worker = True
                    with contextlib.ExitStack() as stack:
                        binding = isolation._pin_binding(stack, caption, root, single_link=True, root_identity=root_identity)
                        window = _context(timeline, check, {}, lambda: raw.encode(), _caption_reader(*binding, check))
                    in_worker = False
                    self.assertEqual(window.input_fingerprint, argv[-2])
                    self.assertEqual(window.fps_expr, '30000/1001')
                    self.assertEqual(window.tl['subtitles'], [{'in': 1, 'out': 30, 'text': expected_text}])
                    self.assertEqual(worker_reads, [(binding[0], 0, caption.read_bytes())] * 2,
                                     'parse shares the initial read; the second read is the post-parse verification')
                    return 0, json.dumps({'schema': SCHEMA, 'ok': True, 'content_version': version,
                                          'start': start, 'end': min(window.dur, start + 30),
                                          '_input_environment': environment}).encode(), b''

                with patch.object(isolation, 'available', return_value=True), \
                        patch.object(isolation, '_profile', return_value=profile), \
                        patch.object(isolation, 'managed_font', return_value=None), \
                        patch('vtx.timeline_preview_window._safe_environment'), \
                        patch.object(isolation, '_read_worker', side_effect=worker), \
                        patch.object(cache_state, 'file_digest', side_effect=digest_other_files), \
                        patch.object(os, 'pread', side_effect=read):
                    result = _dispatch(timeline, reference, root_identity, start, lambda: None, qualification=proof)
                self.assertEqual(host_reads, [(bound_fd, 0, caption.read_bytes())] * 2,
                                 'one full read per phase on the same FD for the receipt and epoch')
                return result

            first = observe(0, 'first\\Nsecond')
            adjacent = observe(30, 'first\\Nsecond')
            self.assertEqual((first['start'], first['end'], adjacent['start'], adjacent['end']), (0, 30, 30, 59))
            self.assertEqual(first['input_epoch'], adjacent['input_epoch'])
            caption.write_bytes(payload.replace(b'first', b'alter'))
            changed = observe(30, 'alter\\Nsecond')
            self.assertNotEqual(adjacent['input_epoch'], changed['input_epoch'])
            # A parse-equivalent change still changes the complete byte identity.
            caption.write_bytes(caption.read_bytes() + b'\r\n')
            self.assertNotEqual(changed['input_epoch'], observe(30, 'alter\\Nsecond')['input_epoch'])
            self.assertEqual(timeline.read_bytes(), raw.encode())

    @unittest.skipUnless(sys.platform == 'linux' and artifact.fstxn.directory_write_capability(),
                         'requires Linux directory anchoring')
    def test_input_environment_binds_effective_config_and_provider_bytes(self):
        from vtx.runtime import preview_window_epoch as epoch

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            font, config, tool = (root / name for name in ('test.ttf', 'fonts.conf', 'tool'))
            font.write_bytes(b'font A')
            config.write_bytes(b'config A')
            tool.write_bytes(b'tool')
            environment = {'VTX_FILTER_THREADS': '1', 'XDG_CACHE_HOME': '/project/cache/preview/window_input_v1/font-cache'}

            def snapshot(address='0123', *, font_listing=None):
                witness = epoch.EnvironmentWitness(lambda: None, 1024, font_config=environment.get('FONTCONFIG_FILE'))
                witness.observe(['fc-list'], f'{font}\n' if font_listing is None else font_listing)
                witness.observe(['fc-conflist'], f'+ {config}: active configuration\n')
                witness.observe(['ldd', str(tool)], f'libtest.so => {tool} (0x{address})\n')
                with patch.object(epoch, '_PROVIDER_FILES', ()), patch.object(sys, 'executable', str(tool)), \
                        patch.object(Path, 'read_text', return_value='00000000-0000-0000-0000-000000000000'):
                    return witness.snapshot({'existing_certificate': 'retained'}, [], environment)[0]

            baseline = snapshot()
            self.assertEqual(snapshot('4321'), baseline)  # Loader addresses are not a tool input.
            environment['VTX_FILTER_THREADS'] = '2'
            changed_config = snapshot()
            self.assertNotEqual(changed_config, baseline)
            config.write_bytes(b'config B')
            changed_font_config = snapshot()
            self.assertNotEqual(changed_font_config, changed_config)
            font.write_bytes(b'font B')
            self.assertNotEqual(snapshot(), changed_font_config)

            system = root / 'system-fonts'; system.mkdir()
            fallback = system / 'fallback.ttf'; fallback.write_bytes(b'fallback')
            environment['FONTCONFIG_FILE'] = str(config)
            with patch.object(epoch, '_MANAGED_FONTS', root), patch.object(epoch, '_DISTRIBUTION_FONTS', system), \
                    patch.object(epoch, '_MANAGED_FONT_PINS', {font.name: 'unused by the inventory check'}):
                self.assertNotEqual(snapshot(font_listing=f'{font}\n{fallback}\n'), changed_font_config)
                # The approved file is present but is not recognized by fc-list.
                with self.assertRaisesRegex(epoch.IsolationError, 'missing an approved font'):
                    snapshot(font_listing=f'{fallback}\n')
                with self.assertRaisesRegex(epoch.IsolationError, 'non-distribution font'):
                    snapshot(font_listing=f'{font}\n{tool}\n')

    @unittest.skipUnless(sys.platform == 'linux' and artifact.fstxn.directory_write_capability(),
                         'requires Linux memfd and directory anchoring')
    def test_managed_font_projection_reads_only_approved_leaves_and_seals_them(self):
        from scripts.install_runtime_fonts import SOURCES
        from vtx.runtime import preview_window_isolation as isolation

        self.assertEqual(isolation._MANAGED_FONT_PINS, {s['filename']: s['sha256'] for s in SOURCES if s['kind'] == 'font'})
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / '.preview-enrollment-installation'; root.mkdir(mode=0o700)
            fonts = root / 'fonts'; fonts.mkdir()
            live = root / 'preview'; live.mkdir()
            scratch = root / '.preview-enrollment-test'; scratch.mkdir(mode=0o700)
            staged = scratch / 'staged-preview'; staged.mkdir()
            font = fonts / 'Approved.ttf'; font.write_bytes(b'approved')
            (fonts / 'other.ttf').symlink_to('/does-not-exist')
            (fonts / 'fonts.conf').write_bytes(b'not an approved config')
            pins = {font.name: hashlib.sha256(b'approved').hexdigest()}
            with patch.object(isolation, '_MANAGED_FONT_PINS', pins), contextlib.ExitStack() as stack:
                check = lambda: None
                one = isolation._managed_font_projection(stack, live, check)
                two = isolation._managed_font_projection(stack, staged, check)
                self.assertEqual([row for _, _, row in one], [row for _, _, row in two])
                self.assertEqual([dest.name for _, dest, _ in one], ['Approved.ttf', 'fonts.conf'])
                font.write_bytes(b'changed')
                self.assertEqual(os.pread(one[0][0], 64, 0), b'approved')
                self.assertEqual(os.pread(one[1][0], 4096, 0), isolation._MANAGED_FONT_CONFIG)
                with self.assertRaises(PermissionError):
                    os.pwrite(one[0][0], b'changed', 0)
                with self.assertRaisesRegex(isolation.IsolationError, 'approved bytes'):
                    isolation._managed_font_projection(stack, live, check)
                font.unlink(); font.symlink_to(fonts / 'fonts.conf')
                with self.assertRaises((isolation.IsolationError, OSError, ValueError)):
                    isolation._managed_font_projection(stack, live, check)

    def test_managed_window_keeps_omitted_font_lookup_roots_reserved(self):
        from vtx.runtime import preview_window_isolation as isolation

        home, data = Path('/fixture/home'), Path('/fixture/data')
        profile = (Path('/runtime'), '/usr/bin/python3', [], [], {'HOME': str(home), 'XDG_DATA_HOME': str(data)})
        with patch.object(isolation, 'available', return_value=True), \
                patch.object(isolation, '_profile', return_value=profile), \
                patch.object(isolation, '_project_mounts') as mounts:
            for root in (Path('/usr/local/share/fonts'), home / '.fonts', data / 'fonts'):
                with self.subTest(root=root), contextlib.ExitStack() as stack:
                    timeline = root / 'fixture' / 'timeline.json'
                    with self.assertRaisesRegex(isolation.IsolationError, 'overlaps a fixed runtime mount'):
                        isolation.execute(stack, timeline, {timeline: ()}, 0, 'sha256:' + '0' * 64,
                                          None, 4096, 4096, lambda: None, root_identity=(),
                                          qualification={}, managed_font_profile=Path('/install/preview'))
            mounts.assert_not_called()

    @unittest.skipUnless(sys.platform == 'linux' and artifact.fstxn.directory_write_capability(),
                         'requires Linux directory anchoring')
    def test_owned_request_cleanup_preserves_unsettled_ledger_and_primary_failure(self):
        from vtx.runtime.proc_supervisor import WORKER_LEDGER
        from vtx.timeline_preview_window import _request, error_result, WindowError

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            identity = artifact.project_identity(root)
            result = error_result('test', 'test')
            with _request(root, identity, result) as (directory, _):
                (directory / 'private').write_text('generated')
                peer = directory.parent / 'old.mp4'
                peer.write_bytes(b'keep')
            self.assertFalse(directory.exists())
            self.assertEqual(peer.read_bytes(), b'keep')
            primary = WindowError('render.cancelled', 'cancelled by host')
            with self.assertRaises(WindowError) as caught:
                with _request(root, identity, result) as (directory, _):
                    child = directory / 'render'
                    child.mkdir()
                    (child / WORKER_LEDGER).write_text('unsettled')
                    raise primary
            self.assertIs(caught.exception, primary)
            self.assertEqual((child / WORKER_LEDGER).read_text(), 'unsettled')
            self.assertEqual(result['diagnostics'][-1]['code'], 'render.unsettled')


if __name__ == '__main__':
    unittest.main()
