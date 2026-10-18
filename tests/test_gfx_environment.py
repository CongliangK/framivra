"""#48 falsification of environment identity and production cache contracts."""
import copy
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from vtx import gfx_environment as env, gfx_cli, hygiene
from tests.test_gfx_cache_integrity import png


class EnvironmentContracts(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.dist = self.root / 'dist'; self.dist.mkdir()
        self.binary = self.dist / 'chrome'; self.binary.write_bytes(b'\x7fELFfixture')
        self.font = self.root / 'font.ttf'; self.font.write_bytes(b'font A')
        self.config = self.root / 'fonts.conf'; self.config.write_text('<fontconfig/>')
        self.lib = self.root / 'libfont.so'; self.lib.write_bytes(b'library A')
        self.tool = self.root / 'fc-list'; self.tool.write_bytes(b'fontconfig tool')
        original_files = env._files
        self.patches = [
            patch.dict(os.environ, {'VTX_GFX': 'cdp', 'FONTCONFIG_FILE': str(self.config),
                'LD_LIBRARY_PATH': '', 'LD_PRELOAD': '', 'LIBGL_DRIVERS_PATH': '',
                'VK_ICD_FILENAMES': '', 'VK_DRIVER_FILES': ''}),
            patch.object(env.shutil, 'which', side_effect=lambda s:
                         str(self.tool) if s in ('fc-list', 'fc-conflist') else s),
            patch.object(env.platform, 'system', return_value='Linux'),
            patch.object(env, '_command', side_effect=self.command),
            patch.object(env, '_python_runtime', return_value={'fixture': True}),
            patch.object(env, '_files', side_effect=lambda paths: original_files(
                [{'/fixture/font.ttf': self.font, '/fixture/libfont.so': self.lib,
                  '/fixture/fonts.conf': self.config, '/etc/machine-id': self.lib,
                  '/proc/sys/kernel/random/boot_id': self.lib, '/etc/ld.so.cache': self.lib}.get(str(p), p)
                 for p in paths]))]

        for p in self.patches:
            p.start(); self.addCleanup(p.stop)

    def command(self, args):
        if len(args) == 2 and args[1] in ('--version', '--sanity'):
            return 'fixture renderer version 1'
        if args[0] == 'fc-list':
            return '/fixture/font.ttf\n'
        if args[0] == 'fc-conflist':
            return '+ /fixture/fonts.conf: fixture\n'
        if args[0] == 'ldd':
            return 'libfont.so => /fixture/libfont.so (0x1234)\n'
        self.fail(str(args))

    def collect(self):
        # Real temp file hashes + inventory, no renderer and no external command.
        return env.collect('cdp', self.binary)

    def test_font_runtime_config_and_library_bytes_invalidate_with_restored_mtime(self):
        a = self.collect()
        for path in (self.font, self.binary, self.config, self.lib):
            original, st = path.read_bytes(), path.stat()
            path.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
            os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))
            self.assertNotEqual(env.digest(a), env.digest(self.collect()), str(path))
            path.write_bytes(original)
        self.assertEqual(a, self.collect())

    def test_linux_inventory_preserves_cross_drive_native_font_path(self):
        # The Linux protocol inventory is independent of the host filesystem.
        # Simulate the hosted runner's D: font while hashing real temp bytes;
        # no drive mapping or system configuration is created by this fixture.
        physical_font = self.font
        self.font = Path('D:/renderer-fixture/font.ttf')
        original_file, observed = env._file, []
        def hash_input(path):
            if str(path) == str(self.font):
                observed.append(str(path))
                return original_file(physical_font)
            return original_file(path)
        with patch.object(env, '_file', side_effect=hash_input):
            before = self.collect()
            st = physical_font.stat()
            physical_font.write_bytes(b'font B')
            os.utime(physical_font, ns=(st.st_atime_ns, st.st_mtime_ns))
            self.assertNotEqual(env.digest(before), env.digest(self.collect()))
        self.assertTrue(observed)
        self.assertTrue(all(path.startswith('D:') for path in observed))

    def test_new_distribution_member_and_visual_flags_invalidate(self):
        a = self.collect()
        (self.dist / 'resources.pak').write_bytes(b'new runtime resources')
        self.assertNotEqual(a, self.collect())
        with patch.dict(os.environ, VTX_GFX_NAVFREE='1'):
            self.assertNotEqual(a['environment'], self.collect()['environment'])

    def test_png_compression_does_not_invalidate_rgba_identity(self):
        with patch.dict(os.environ, VTX_GFX_PNG_LEVEL='0', VTX_GFX_PAGES='1'):
            a = self.collect()
        with patch.dict(os.environ, VTX_GFX_PNG_LEVEL='9', VTX_GFX_PAGES='7'):
            self.assertEqual(a, self.collect())

    def test_missing_font_unresolved_library_and_unknown_platform_fail_closed(self):
        self.font.unlink()
        with self.assertRaises(env.EnvironmentError):
            self.collect()
        with patch.object(env, '_command', return_value='libcef.so => not found'):
            with self.assertRaises(env.EnvironmentError):
                self.collect()
        with patch.object(env.platform, 'system', return_value='unsupported'):
            with self.assertRaises(env.EnvironmentError):
                self.collect()

    def test_context_pins_rechecks_and_refuses_unknown_certificate(self):
        c = SimpleNamespace(w=6, h=4, fps=30)
        record = self.collect()
        with patch.object(env, 'collect', return_value=record), \
                patch('vtx.encoders.find_chrome', return_value=str(self.binary)):
            cert = env.certificate(c)
            self.assertEqual(env.verify_certificate(cert), cert['sha256'])
            env.recheck(c)
            changed = copy.deepcopy(record); changed['driver'] = {'changed': 'driver'}
            with patch.object(env, 'collect', return_value=changed):
                with self.assertRaises(env.EnvironmentError):
                    env.recheck(c)
        with patch.object(env, 'collect', side_effect=env.EnvironmentError('unavailable')), \
                patch('vtx.encoders.find_chrome', return_value=str(self.binary)):
            unknown = SimpleNamespace(w=6, h=4, fps=30)
            self.assertEqual(env.resolve(unknown)['status'], 'unknown')
            for _ in range(2):
                with self.assertRaises(env.EnvironmentError):
                    env.certificate(unknown)
        cert['axes']['fonts'] = {}
        with self.assertRaises(env.EnvironmentError):
            env.verify_certificate(cert)

    def test_environment_key_and_receipt_axes_share_dependency_resolver(self):
        from tests.gfx_environment_fixture import bind_environment
        from vtx.infra import Ctx
        bind_environment(self)
        (self.root / 'p.html').write_text('<html>text</html>')
        tp = self.root / 'timeline.json'
        tp.write_text(json.dumps({'project': {'fps': 30, 'width': 6, 'height': 4,
            'duration_frames': 1}, 'video_tracks': [{'kind': 'gfx', 'clips': [
                {'src': 'p.html', 'dur': 1, 'dst_in': 0}]}]}))
        a = Ctx(tp); clip = a.tl['video_tracks'][0]['clips'][0]
        key = hygiene.gfx_key(a, clip)
        with patch.dict(os.environ, VTX_GFX='cli'):
            b = Ctx(tp)
            self.assertNotEqual(key, hygiene.gfx_key(b, clip))
            self.assertNotEqual(a.input_fingerprint, b.input_fingerprint)


class PageEnvironmentContracts(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.d = Path(tmp.name)
        self.environment = {'schema': env.SCHEMA, 'sha256': 'unit-only', 'status': 'input-bound'}
        for i in range(2):
            (self.d / f'f{i:04d}.png').write_bytes(png())

    def inspect(self, **kw):
        return gfx_cli.inspect_page(self.d, 2, 'key', 6, 4,
                                     environment=self.environment, **kw)

    def complete(self, **kw):
        gfx_cli.complete_page(self.d, 2, 'key', 6, 4, verify_inputs=lambda: None,
                              environment=self.environment, **kw)

    def test_old_pngs_and_old_marker_cannot_be_recertified(self):
        self.assertEqual(len(self.inspect()[0]), 2)
        gfx_cli.complete_page(self.d, 2, 'key', 6, 4, verify_inputs=lambda: None)
        self.assertEqual(len(self.inspect()[0]), 2)
        self.complete()
        self.assertEqual(self.inspect(), ([], False))
        self.environment['sha256'] = 'different browser/font/runtime'
        self.assertEqual(len(self.inspect()[0]), 2)

    def test_equal_unknown_records_never_authorize_reuse_or_completion(self):
        self.environment['status'] = 'unknown'
        (self.d / gfx_cli.COMPLETE_FILE).write_text(json.dumps({'environment': self.environment}))
        self.assertEqual(len(self.inspect()[0]), 2)
        with self.assertRaises(gfx_cli.GfxCliError):
            self.complete()

    def test_unknown_marker_schema_or_missing_frame_hashes_cannot_be_recertified(self):
        for changed in ({'schema': 999}, {'schema': True}, {'sha256': []}, {'sha256': None}):
            self.complete()
            marker = self.d / gfx_cli.COMPLETE_FILE
            record = json.loads(marker.read_text()); record.update(changed)
            marker.write_text(json.dumps(record))
            self.assertEqual(len(self.inspect()[0]), 2)

    def test_pending_identity_is_page_bound_and_mixed_producer_never_warm_hits(self):
        gfx_cli.begin_environment_page(self.d, self.environment, 'wrong-key', 2, 6, 4)
        self.assertEqual(len(self.inspect()[0]), 2)
        gfx_cli.begin_environment_page(self.d, self.environment, 'key', 2, 6, 4)
        self.assertEqual(self.inspect(), ([], True))
        with self.assertRaisesRegex(gfx_cli.GfxCliError, 'VTX_GFX=cli'):
            self.complete(mixed_producer=True)
        self.assertEqual(len(self.inspect()[0]), 2)
        self.assertFalse((self.d / gfx_cli.ENV_PENDING_FILE).exists())

    def test_read_only_consumer_rejects_legacy_and_accepts_current_certificate(self):
        from vtx.gfx_cache_reader import resolve_frames, GfxCacheError
        c = SimpleNamespace(root=self.d, cache=self.d, w=6, h=4, fps=30,
                            tl={'project': {}})
        source = SimpleNamespace(directory=self.d, frame_count=2)
        # The directory's actual key is used by the production reader.
        def publish():
            gfx_cli.complete_page(self.d, 2, self.d.name, 6, 4, verify_inputs=lambda: None,
                                  environment=self.environment)
        with patch('vtx.frame_source.png_source', return_value=source), \
                patch.object(env, 'certificate', return_value=self.environment), \
                patch.object(env, 'recheck'), patch.dict(os.environ, VTX_GFX='cdp', VTX_GFX_CACHE_NS=''):
            with self.assertRaises(GfxCacheError):
                resolve_frames(c, {'src': 'p.html', 'dur': 2})
            publish()
            self.assertEqual(len(resolve_frames(c, {'src': 'p.html', 'dur': 2})), 2)

    def test_frame_source_requires_certificate_and_rejects_valid_png_replacement(self):
        from vtx.frame_source import png_source
        c = SimpleNamespace(root=self.d, cache=self.d.parent, w=6, h=4, fps=30)
        with patch('vtx.frame_source.gfx_dir', return_value=self.d), \
                patch.object(env, 'certificate', return_value=self.environment):
            source = png_source(c, {'src': 'p.html', 'dur': 2})
            with self.assertRaisesRegex(ValueError, 'certificate missing'):
                source.require_ready()
            gfx_cli.complete_page(self.d, 2, self.d.name, 6, 4,
                                  verify_inputs=lambda: None, environment=self.environment)
            source.require_ready()
            (self.d / 'f0001.png').write_bytes(png(value=77))
            with self.assertRaisesRegex(ValueError, 'digest changed'):
                source.require_ready()


class ArchivedEnvironmentEvidence(unittest.TestCase):
    def test_archived_full_frame_comparisons_match_committed_pngs(self):
        import hashlib
        from PIL import Image
        root = Path(__file__).parent / 'fixtures/gfx-environment'
        report = json.loads((root / 'acceptance-2026-10-18.json').read_text(encoding='utf-8'))
        self.assertTrue(report['passed'])
        self.assertTrue(report['cef_runtime_negative']['worker_unchanged'])
        self.assertNotEqual(report['cef_runtime_negative']['library_before'],
                            report['cef_runtime_negative']['library_after'])
        pairs = {'font_change': ('cdp-font-a', 'cdp-font-b'),
                 'browser_change': ('cdp-font-b', 'alternate-browser'),
                 'cdp_cli': ('cdp-font-b', 'cli-font-b'),
                 'cef_library_replacement': ('cef-runtime-a', 'cef-runtime-b'),
                 'cef_font_change': ('cef-runtime-a', 'cef-font-a')}
        for comparison, sides in pairs.items():
            self.assertEqual(len(report['comparisons'][comparison]), 4)
            for row in report['comparisons'][comparison]:
                pixels = []
                for side in sides:
                    path = root / side / row['frame']
                    self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),
                                     report['runs'][side]['frames'][row['frame']])
                    with Image.open(path) as im:
                        self.assertEqual(im.size, (640, 180))
                        pixels.append(im.convert('RGBA').tobytes())
                self.assertEqual(pixels[0] == pixels[1], row['equal_rgba'])
