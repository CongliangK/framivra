"""CEF performance controls stay explicit, bounded and cache-identity bound."""
from dataclasses import FrozenInstanceError
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import subprocess
import unittest
from unittest.mock import patch

from vtx.render import gfx_backend as backend, gfx_environment as environment

WORKER = os.environ.get('VTX_TEST_CEF_WORKER', '')


class CefRuntimeConfiguration(unittest.TestCase):
    def setUp(self):
        clean = patch.dict(os.environ, {}, clear=True)
        clean.start()
        self.addCleanup(clean.stop)

    def test_defaults_and_native_argv_have_one_normalized_source(self):
        options = backend.cef_runtime_options()
        self.assertEqual(options.to_dict(), dict(sync_mode='n', reference_context='shared', png_encoder='spng',
            png_level=1, png_filter='sub_up', encode_threads=2, capture_fps=120))
        self.assertEqual(options.argv(), ['--sync-mode=n', '--reference-context=shared', '--png-encoder=spng',
            '--png-level=1', '--png-filter=sub_up', '--encode-threads=2', '--capture-fps=120'])
        self.assertEqual(backend.cef_scheduling_options(),
                         dict(pages=7, split_frames=450, split_max=3))
        self.assertEqual(backend.cef_launch_flags('a1'),
                         ['mode=a1', 'sync-canary=exact'] + [v[2:] for v in options.argv()])
        with self.assertRaises(FrozenInstanceError):
            options.sync_mode = 'p'
        changed = options.to_dict()
        changed['sync_mode'] = 'p'
        self.assertEqual(options.sync_mode, 'n')

    def test_existing_five_positional_options_keep_their_meaning(self):
        options = backend.CefRuntimeOptions("p", "fpng", 3, "none", 4)
        self.assertEqual(options.to_dict(), dict(sync_mode="p", png_encoder="fpng",
            png_level=3, png_filter="none", encode_threads=4, reference_context="shared", capture_fps=120))

    def test_capture_rate_is_an_explicit_native_request(self):
        for value in (1, 30, 60, 120, 240):
            with self.subTest(value=value), patch.dict(os.environ, VTX_CEF_CAPTURE_FPS=str(value)):
                options = backend.cef_runtime_options()
                self.assertEqual(options.capture_fps, value)
                self.assertEqual(options.to_dict()["capture_fps"], value)
                self.assertIn("--capture-fps=" + str(value), options.argv())
                self.assertIn("capture-fps=" + str(value), backend.cef_launch_flags("a0"))

    def test_supported_choices_are_explicit_without_hook_detection(self):
        for name, values, field in (
            ('VTX_CEF_SYNC_MODE', ('n', 'p'), 'sync_mode'),
            ('VTX_CEF_REFERENCE_CONTEXT', ('shared', 'isolated'), 'reference_context'),
            ('VTX_CEF_PNG_ENCODER', ('spng', 'fpng', 'zlib'), 'png_encoder'),
            ('VTX_CEF_PNG_FILTER', ('none', 'sub', 'up', 'sub_up', 'all'), 'png_filter')):
            for value in values:
                with self.subTest(name=name, value=value), patch.dict(os.environ, {name: value}):
                    self.assertEqual(backend.cef_runtime_options().to_dict()[field], value)
            for value in ('', 'auto', values[0].upper(), values[0] + ' '):
                with self.subTest(name=name, value=value), patch.dict(os.environ, {name: value}):
                    with self.assertRaisesRegex(ValueError, name):
                        backend.cef_runtime_options()

    def test_numeric_controls_reject_silent_coercion_and_unbounded_pools(self):
        for name, low, high in (
            ('VTX_CEF_PNG_LEVEL', 1, 3), ('VTX_CEF_ENCODE_THREADS', 1, 16),
            ('VTX_CEF_CAPTURE_FPS', 1, 240),
            ('VTX_GFX_PAGES', 1, 32), ('VTX_GFX_SPLIT_FRAMES', 0, 65536),
            ('VTX_GFX_SPLIT_MAX', 1, 32)):
            for value in (str(low), str(high), str(high).zfill(6)):
                with self.subTest(name=name, value=value), patch.dict(os.environ, {name: value}):
                    backend.cef_configuration()
            for value in ('', ' ', '2.0', '2e0', '+2', '-1', ' 2', '2 ', '1_0',
                          '２', str(low - 1), str(high + 1), '9' * 10000):
                with self.subTest(name=name, value=value[:20]), patch.dict(os.environ, {name: value}):
                    with self.assertRaisesRegex(ValueError, name):
                        backend.cef_configuration()


class CefConfigurationIdentity(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='cef-config-')
        self.addCleanup(directory.cleanup)
        self.worker = Path(directory.name) / 'worker'
        self.worker.write_bytes(b'fixture native executable A')
        self.worker.chmod(0o700)
        clean = patch.dict(os.environ, {'VTX_GFX': 'cef',
            'VTX_CEF_WORKER': str(self.worker)}, clear=True)
        clean.start()
        self.addCleanup(clean.stop)

    def test_every_runtime_and_scheduler_axis_changes_identity(self):
        before = backend.identity()
        self.assertTrue(before.startswith('cef-alpha-exact-v2|'))
        for name, value in dict(VTX_CEF_MODE='a1', VTX_CEF_SYNC_MODE='p',
                VTX_CEF_REFERENCE_CONTEXT='isolated', VTX_CEF_CAPTURE_FPS='60',
                VTX_CEF_PNG_ENCODER='fpng', VTX_CEF_PNG_LEVEL='3',
                VTX_CEF_PNG_FILTER='none', VTX_CEF_ENCODE_THREADS='1',
                VTX_GFX_PAGES='2', VTX_GFX_SPLIT_FRAMES='0', VTX_GFX_SPLIT_MAX='4').items():
            with self.subTest(name=name), patch.dict(os.environ, {name: value}):
                self.assertNotEqual(before, backend.identity())
        with patch.dict(os.environ, VTX_CEF_ENCODE_THREADS='02', VTX_GFX_PAGES='07',
                        VTX_CEF_CAPTURE_FPS='00120'):
            self.assertEqual(before, backend.identity())
        self.worker.write_bytes(b'fixture native executable B')
        self.assertNotEqual(before, backend.identity())

    def test_adapter_and_scheduler_implementation_are_not_omitted(self):
        observed = []
        def digest(path):
            observed.append(Path(path))
            return 'original'
        with patch.object(backend, 'content_digest', side_effect=digest):
            before = backend.identity()
        required = {self.worker, Path(backend.__file__),
            backend.ROOT / 'vtx/render/gfx_cef.py', backend.ROOT / 'vtx/render/cef_client.py',
            backend.ROOT / 'vtx/render/gfx_cli.py', backend.ROOT / 'vtx/cli/commands.py',
            backend.ROOT / 'vtx/runtime/proc_supervisor.py'}
        self.assertEqual(set(observed), required)
        for changed in required:
            with self.subTest(path=changed), patch.object(backend, 'content_digest',
                    side_effect=lambda path: 'changed' if Path(path) == changed else 'original'):
                self.assertNotEqual(before, backend.identity())

    def test_invalid_control_is_rejected_before_native_launch_or_cache_reuse(self):
        with patch.dict(os.environ, VTX_GFX_PAGES='1000000000'):
            with self.assertRaisesRegex(ValueError, 'VTX_GFX_PAGES'):
                backend.cef_config()

    def test_cdp_legacy_key_is_not_affected_by_cef_configuration(self):
        with patch.dict(os.environ, VTX_GFX='cdp', VTX_CEF_SYNC_MODE='invalid',
                        VTX_GFX_PAGES='invalid', VTX_CEF_CAPTURE_FPS='invalid'):
            self.assertEqual(backend.identity(), '')

    def test_context_recheck_rejects_configuration_mutation(self):
        def collect(*unused):
            return dict(schema=environment.SCHEMA, status='input-bound', backend='cef',
                        cef_configuration=backend.cef_configuration())
        for name, value in dict(VTX_CEF_SYNC_MODE='p', VTX_CEF_REFERENCE_CONTEXT='isolated', VTX_CEF_CAPTURE_FPS='60',
                               VTX_CEF_PNG_FILTER='none',
                               VTX_GFX_PAGES='1', VTX_GFX_SPLIT_FRAMES='0').items():
            with self.subTest(name=name), patch.object(environment, 'collect', side_effect=collect):
                context = SimpleNamespace()
                pinned = environment.resolve(context)
                environment.recheck(context)
                with patch.dict(os.environ, {name: value}):
                    with self.assertRaisesRegex(environment.EnvironmentError, 'changed during invocation'):
                        environment.recheck(context)
                    self.assertNotEqual(pinned, environment.resolve(context))


@unittest.skipUnless(WORKER, 'VTX_TEST_CEF_WORKER enables real native CLI validation')
class NativeCefConfiguration(unittest.TestCase):
    """Runs the actual worker parser, not a Python reimplementation of it."""
    def run_worker(self, *args):
        return subprocess.run([WORKER, '--sanity', *args], capture_output=True,
                              text=True, timeout=30, start_new_session=True)

    def test_reference_context_is_explicit_and_checked_before_rendering(self):
        for value in ('auto', 'SHARED', 'shared ', 'storage'):
            with self.subTest(value=value):
                result = self.run_worker('--reference-context=' + value)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn('--reference-context', result.stderr)
        for value in ('shared', 'isolated'):
            with self.subTest(value=value):
                result = self.run_worker('--reference-context=' + value)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_capture_rate_range_and_numeric_syntax_are_checked_before_rendering(self):
        for value in ("0", "241", "-1", "+30", "30.0", " 30", "30 ", "auto"):
            with self.subTest(value=value):
                result = self.run_worker("--capture-fps=" + value)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("--capture-fps", result.stderr)
        for value in ("1", "30", "60", "120", "240"):
            with self.subTest(value=value):
                result = self.run_worker("--capture-fps=" + value)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_encoder_thread_boundary_is_checked_before_rendering(self):
        for value in ('0', '17', '1000000000'):
            with self.subTest(value=value):
                result = self.run_worker('--encode-threads=' + value)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn('--encode-threads', result.stderr)
        for value in ('1', '16'):
            with self.subTest(value=value):
                result = self.run_worker('--encode-threads=' + value)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('SANITY OK', result.stdout)
                self.assertIn('worker=paired-exact-v1', result.stdout)

    def test_noncanonical_numbers_are_not_silently_coerced(self):
        for value in ('+2', ' 2', '2 ', '2.0', '-1', '2e0', '9' * 100):
            with self.subTest(value=value):
                result = self.run_worker('--encode-threads=' + value)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn('--encode-threads', result.stderr)


if __name__ == '__main__':
    unittest.main()
