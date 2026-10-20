"""CPU-only CEF PNG configuration contracts; never launch a worker or IPC.

Environment collection and native execution are explicit unit-test doubles.
These checks establish Python routing/provenance, not CEF runtime qualification.
"""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
import zlib

from tests.gfx_environment_fixture import bind_environment
from vtx import gfx_backend, gfx_cef, hygiene
from vtx.cache_state import load_receipt, receipt_fingerprint
from vtx.infra import Ctx


class _Fixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='cef_png_config_')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.worker = self.root / 'worker'
        self.worker.write_bytes(b'unit metadata fixture, never executed\n')
        self.worker.chmod(0o700)
        self.page = self.root / 'page.html'
        self.page.write_text('<!doctype html><body>CPU-only fixture</body>')
        self.timeline = self.root / 'timeline.json'
        self.timeline.write_text(json.dumps({
            'version': 2, 'project': {'fps': 30, 'width': 16, 'height': 16,
                                      'duration_frames': 2},
            'sources': [], 'subtitles': [], 'audio_tracks': [],
            'video_tracks': [{'name': 'gfx', 'kind': 'gfx', 'clips': [
                {'src': 'page.html', 'dst_in': 0, 'dur': 2}]}]}))
        environment = mock.patch.dict(os.environ, {
            'VTX_GFX': 'cef', 'VTX_CEF_WORKER': str(self.worker),
            'VTX_CEF_MODE': 'a0'}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        bind_environment(self)

    def snapshot(self):
        context = Ctx(self.timeline)
        clip = context.tl['video_tracks'][0]['clips'][0]
        return (gfx_backend.identity(), hygiene.gfx_key(context, clip),
                receipt_fingerprint(context))


class ConfigContract(_Fixture):
    def test_defaults_keep_legacy_cef_config_shape(self):
        self.assertEqual(gfx_backend.cef_config(), (self.worker.resolve(), 'a0'))
        self.assertEqual(gfx_backend.cef_png_config(), ('spng', 1, 'sub_up'))
        self.assertIsInstance(gfx_backend.cef_png_config(), tuple)

    def test_spng_native_domain(self):
        for level in ('1', '2', '3'):
            for name in ('none', 'sub', 'up', 'sub_up', 'all'):
                with self.subTest(level=level, filter=name), mock.patch.dict(
                        os.environ, VTX_CEF_PNG_LEVEL=level, VTX_CEF_PNG_FILTER=name):
                    self.assertEqual(gfx_backend.cef_png_config(), ('spng', int(level), name))

    def test_filter_aliases_are_canonical(self):
        for name in ('sub_up', 'sub-up', 'subup'):
            with self.subTest(filter=name), mock.patch.dict(os.environ, VTX_CEF_PNG_FILTER=name):
                self.assertEqual(gfx_backend.cef_png_config(), ('spng', 1, 'sub_up'))

    def test_invalid_values_fail_closed(self):
        invalid = {
            'VTX_CEF_PNG_ENCODER': ('', ' ', 'zlib', 'Spng', 'spng ', 'typo'),
            'VTX_CEF_PNG_LEVEL': ('', ' ', '0', '4', '-1', '+1', '01', '1.0',
                                  ' 1', '1 ', '1x', '１'),
            'VTX_CEF_PNG_FILTER': ('', ' ', 'None', 'paeth', 'sub up', 'up ', 'typo'),
        }
        for name, values in invalid.items():
            for value in values:
                with self.subTest(name=name, value=value), mock.patch.dict(os.environ, {name: value}):
                    with self.assertRaisesRegex(ValueError, name):
                        gfx_backend.cef_png_config()

    def test_fpng_has_no_spng_axes(self):
        with mock.patch.dict(os.environ, VTX_CEF_PNG_ENCODER='fpng'):
            self.assertEqual(gfx_backend.cef_png_config(), ('fpng', None, None))

    def test_fpng_rejects_explicit_spng_axes(self):
        for name, values in (('VTX_CEF_PNG_LEVEL', ('1', '3', '', 'typo')),
                             ('VTX_CEF_PNG_FILTER', ('sub_up', 'none', '', 'typo'))):
            for value in values:
                with self.subTest(name=name, value=value), mock.patch.dict(
                        os.environ, {'VTX_CEF_PNG_ENCODER': 'fpng', name: value}):
                    with self.assertRaisesRegex(ValueError, name + ' is not applicable to fpng'):
                        gfx_backend.cef_png_config()

    def test_non_cef_identity_ignores_cef_png_options(self):
        for backend in ('cdp', 'cli'):
            with self.subTest(backend=backend), mock.patch.dict(os.environ, VTX_GFX=backend):
                original = self.snapshot()
                with mock.patch.dict(os.environ, VTX_CEF_PNG_ENCODER='invalid',
                                     VTX_CEF_PNG_LEVEL='', VTX_CEF_PNG_FILTER='invalid'), \
                     mock.patch.object(gfx_backend, 'cef_png_config',
                                       side_effect=AssertionError('unselected CEF config read')):
                    self.assertEqual(self.snapshot(), original)
                    self.assertEqual(gfx_backend.identity(), '')


class IdentityContract(_Fixture):
    def test_default_identity_is_versioned(self):
        digest = hashlib.sha256(self.worker.read_bytes()).hexdigest()
        legacy = f'cef-alpha-exact-v1|a0|mode-n|{digest}'
        with mock.patch.object(gfx_backend, '_cef_identity', return_value=legacy):
            previous = self.snapshot()
        current = self.snapshot()
        self.assertEqual(current[0], f'cef-alpha-exact-v2|a0|mode-n|{digest}|["spng",1,"sub_up"]')
        for old, new in zip(previous, current):
            self.assertNotEqual(old, new)
        with mock.patch.dict(os.environ, VTX_CEF_PNG_ENCODER='spng',
                             VTX_CEF_PNG_LEVEL='1', VTX_CEF_PNG_FILTER='sub_up'):
            self.assertEqual(self.snapshot(), current)

    def test_canonical_aliases_share_identity(self):
        original = self.snapshot()
        context = Ctx(self.timeline)
        for name in ('sub_up', 'sub-up', 'subup'):
            with self.subTest(filter=name), mock.patch.dict(os.environ, VTX_CEF_PNG_FILTER=name):
                self.assertEqual(self.snapshot(), original)
                context.verify_inputs('gfx')

    def test_experiment_arms_change_gfx_and_receipt_keys(self):
        snapshots = []
        for level in ('1', '2', '3'):
            for name in ('none', 'sub', 'up', 'sub_up', 'all'):
                with self.subTest(level=level, filter=name), mock.patch.dict(
                        os.environ, VTX_CEF_PNG_LEVEL=level, VTX_CEF_PNG_FILTER=name):
                    snapshots.append(self.snapshot())
        with mock.patch.dict(os.environ, VTX_CEF_PNG_ENCODER='fpng'):
            snapshots.append(self.snapshot())
        # Includes the three preregistered arms plus all supported spng axes.
        for identities in zip(*snapshots):
            self.assertEqual(len(set(identities)), 16)

    def test_mode_and_native_bytes_remain_identity_axes(self):
        original = self.snapshot()
        with mock.patch.dict(os.environ, VTX_CEF_MODE='a1'):
            changed_mode = self.snapshot()
        # Same size, a changed executable body: the content digest still matters.
        self.worker.write_bytes(self.worker.read_bytes().replace(b'unit', b'next', 1))
        changed_binary = self.snapshot()
        for before, mode, binary in zip(original, changed_mode, changed_binary):
            self.assertEqual(len({before, mode, binary}), 3)

    def test_config_change_rejects_inflight_context(self):
        for changes in ({'VTX_CEF_PNG_ENCODER': 'fpng'}, {'VTX_CEF_PNG_LEVEL': '2'},
                        {'VTX_CEF_PNG_FILTER': 'none'}):
            with self.subTest(changes=changes):
                context = Ctx(self.timeline)
                context.receipt('gfx', True, 'unit metadata only')
                with mock.patch.dict(os.environ, changes), contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as caught:
                        context.verify_inputs('gfx')
                    self.assertEqual(caught.exception.code, 1)
                record = json.loads((context.out / 'receipt.json').read_text())
                self.assertEqual(record['input_fingerprint'], context.input_fingerprint)
                self.assertFalse(record['stages']['gfx']['ok'])

    def test_config_change_revokes_downstream_receipt(self):
        for changes in ({'VTX_CEF_PNG_ENCODER': 'fpng'}, {'VTX_CEF_PNG_LEVEL': '3'},
                        {'VTX_CEF_PNG_FILTER': 'all'}):
            with self.subTest(changes=changes):
                context = Ctx(self.timeline)
                for stage in ('gfx', 'video', 'mux', 'qc'):
                    context.receipt(stage, True, 'unit metadata only')
                path = context.out / 'receipt.json'
                self.assertTrue(load_receipt(path, context.input_fingerprint)['stages']['qc']['ok'])
                with mock.patch.dict(os.environ, changes):
                    replacement = Ctx(self.timeline)
                    self.assertNotEqual(context.input_fingerprint, replacement.input_fingerprint)
                    self.assertEqual(load_receipt(path, replacement.input_fingerprint)['stages'], {})
                    with self.assertRaisesRegex(RuntimeError, 'gfx'):
                        replacement.begin_stage('video')


def _png(frame):
    def chunk(kind, payload):
        return (struct.pack('>I', len(payload)) + kind + payload
                + struct.pack('>I', zlib.crc32(kind + payload)))
    rows = (b'\0' + bytes((frame, 17, 33, 255)) * 16) * 16
    return (b'\x89PNG\r\n\x1a\n'
            + chunk(b'IHDR', struct.pack('>IIBBBBB', 16, 16, 8, 6, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b''))


class ArgvContract(_Fixture):
    def render_mocked(self, mutate_before_argv=None):
        run, profile, out = (self.root / name for name in ('run', 'profile', 'frames'))
        for directory in (run, profile, out):
            directory.mkdir()
        context = SimpleNamespace(cache=self.root / 'cache', verify_inputs=mock.Mock())
        captured = {}
        group = mock.MagicMock()
        group.run_dir = run
        group.proc.pid = 12345
        group.proc.wait.return_value = 0
        group.__enter__.return_value = group
        group.__exit__.return_value = False

        def group_factory(ctx, builder, **kwargs):
            if mutate_before_argv:
                mutate_before_argv()
            captured['argv'] = builder(run, profile)
            captured['kwargs'] = kwargs
            return group

        client = mock.MagicMock()
        client.hello = {'pid': 12345}
        client.__enter__.return_value = client
        client.__exit__.return_value = False

        def request(frame, path, **kwargs):
            (run / path).write_bytes(_png(frame))
            return SimpleNamespace(header={'canary_frame_index': frame, 'width': 16,
                'height': 16, 'stride': 64, 'pixel_format': 1})

        client.request_png.side_effect = request
        with mock.patch.object(gfx_cef, 'supervision_available', return_value=True), \
             mock.patch.object(gfx_cef, 'SupervisedProcessGroup', side_effect=group_factory), \
             mock.patch.object(gfx_cef.CefWorkerClient, 'connect', return_value=client), \
             mock.patch.object(gfx_cef.CefWorkerClient, 'from_socket',
                               side_effect=AssertionError('unexpected socket IPC')), \
             mock.patch.object(gfx_cef.socket, 'socketpair',
                               side_effect=AssertionError('unexpected socketpair')), \
             mock.patch('subprocess.Popen', side_effect=AssertionError('unexpected process')), \
             mock.patch.object(gfx_cef.sys, 'platform', 'linux'), \
             mock.patch.object(gfx_cef, 'cef_png_config', wraps=gfx_backend.cef_png_config) as config:
            captured['result'] = gfx_cef.render_page(self.page, out, 2, ctx=context, width=16, height=16)
            self.assertEqual(config.call_count, 1)
        self.assertEqual(client.request_png.call_count, 2)
        context.verify_inputs.assert_called_once_with('gfx')
        for frame in range(2):
            self.assertEqual((out / f'f{frame:04d}.png').read_bytes(), _png(frame))
        return captured

    def test_spng_argv_preserves_exact_route(self):
        with mock.patch.dict(os.environ, VTX_CEF_PNG_LEVEL='3', VTX_CEF_PNG_FILTER='sub-up'):
            captured = self.render_mocked()
        self.assertEqual(captured['argv'], [
            str(self.worker), '--mode=a0', '--serve', '--sync', '--sync-mode=n',
            '--sync-canary=exact', '--sync-log=sync.jsonl', '--ipc-socket=ipc.sock',
            f'--root-cache-path={self.root / "profile"}',
            f'--url-template={self.page.as_uri()}?f={{f}}', '--width=16', '--height=16',
            '--png-encoder=spng', '--png-level=3', '--png-filter=sub_up',
            '--ozone-platform=headless'])
        self.assertEqual(captured['kwargs']['backend_id'], 'cef_a0')
        self.assertEqual(captured['kwargs']['pass_fds'], ())

    def test_fpng_argv_omits_spng_flags(self):
        with mock.patch.dict(os.environ, VTX_CEF_PNG_ENCODER='fpng', VTX_CEF_MODE='a1'):
            captured = self.render_mocked()
        self.assertEqual([arg for arg in captured['argv'] if arg.startswith('--png-')],
                         ['--png-encoder=fpng'])
        self.assertIn('--mode=a1', captured['argv'])
        self.assertIn('--sync-canary=exact', captured['argv'])
        self.assertNotIn('--ozone-platform=headless', captured['argv'])

    def test_render_freezes_png_config_once(self):
        captured = self.render_mocked(
            mutate_before_argv=lambda: os.environ.update(VTX_CEF_PNG_ENCODER='fpng'))
        self.assertEqual([arg for arg in captured['argv'] if arg.startswith('--png-')],
                         ['--png-encoder=spng', '--png-level=1', '--png-filter=sub_up'])
        self.assertEqual(gfx_backend.cef_png_config(), ('fpng', None, None))

    def test_invalid_config_fails_before_worker(self):
        context = SimpleNamespace(cache=self.root / 'cache')
        for changes in ({'VTX_CEF_PNG_ENCODER': ''}, {'VTX_CEF_PNG_LEVEL': '4'},
                        {'VTX_CEF_PNG_FILTER': ''},
                        {'VTX_CEF_PNG_ENCODER': 'fpng', 'VTX_CEF_PNG_LEVEL': '1'}):
            with self.subTest(changes=changes), mock.patch.dict(os.environ, changes), \
                 mock.patch.object(gfx_cef, 'SupervisedProcessGroup') as group, \
                 mock.patch.object(gfx_cef.socket, 'socketpair') as pair:
                with self.assertRaisesRegex(ValueError, 'VTX_CEF_PNG_'):
                    gfx_cef.render_page(self.page, self.root / 'frames', 2,
                                        ctx=context, width=16, height=16)
                group.assert_not_called()
                pair.assert_not_called()
                self.assertFalse(context.cache.exists())


if __name__ == '__main__':
    unittest.main()
