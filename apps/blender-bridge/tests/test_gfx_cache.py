"""Real Blender regression for content-addressed gfx (opt in VTX_TEST_BLENDER=1).

Synthetic PNGs use the production completion writer; no browser/CEF rendering.
The worker fixture is an identity-only executable and is never launched.
"""
import json
import importlib.util
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
import zlib

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
from vtx.api import gfx_dir, gfx_key
from vtx import gfx_cli
from vtx.gfx_environment import certificate

# app discovery owns a different `tests` package; load the shared unit seam by
# its exact file, never substitute a fake worker for real environment evidence.
_spec = importlib.util.spec_from_file_location(
    'gfx_environment_fixture', REPO / 'tests/gfx_environment_fixture.py')
_environment_fixture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_environment_fixture)


def png_bytes(value=0):
    def chunk(kind, data):
        return (struct.pack('>I', len(data)) + kind + data
                + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff))
    return (gfx_cli.PNG_SIGNATURE
            + chunk(b'IHDR', struct.pack('>IIBBBBB', 8, 6, 8, 6, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress((b'\0' + bytes([value, 40, 80, 255]) * 8) * 6))
            + chunk(b'IEND', b''))


class CacheFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith('VTX_')}
        self.env['PYTHONDONTWRITEBYTECODE'] = '1'
        self.worker = self.root / 'worker'
        self.worker.write_bytes(b'identity-only-worker-v1')
        self.worker.chmod(0o700)
        self.env.update(VTX_GFX='cef', VTX_CEF_MODE='a0',
                        VTX_CEF_WORKER=str(self.worker), VTX_GFX_CACHE_NS='selected-ns')
        self.patch = mock.patch.dict(os.environ, self.env, clear=True)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        _environment_fixture.bind_environment(self)
        (self.root / 'gfx').mkdir()
        (self.root / 'gfx' / 'page.html').write_text('<html>frame driven</html>')
        self.clip = {'src': 'gfx/page.html', 'dst_in': 0, 'dur': 3}
        self.tl = {'version': 2,
                   'project': {'fps': 30, 'width': 8, 'height': 6, 'duration_frames': 3},
                   'sources': [], 'video_tracks': [
                       {'name': 'gfx', 'kind': 'gfx', 'clips': [self.clip]}],
                   'audio_tracks': [], 'subtitles': []}
        self.timeline = self.root / 'timeline.json'
        self.timeline.write_text(json.dumps(self.tl))
        self.c = SimpleNamespace(root=self.root, cache=self.root / 'cache',
                                 tl=self.tl, fps=30, w=8, h=6)

    def seed(self, directory=None, ns='selected-ns', marker=True):
        directory = directory or gfx_dir(self.c, self.clip)
        directory.mkdir(parents=True, exist_ok=True)
        for i in range(3):
            (directory / f'f{i:04d}.png').write_bytes(png_bytes(i))
        if marker:
            gfx_cli.complete_page(directory, 3, gfx_key(self.c, self.clip), 8, 6,
                                  verify_inputs=lambda: None, backend_ns=ns,
                                  environment=certificate(self.c))
        return directory


class ReadOnlyCache(CacheFixture):
    def resolve(self):
        from vtx import gfx_cache_reader
        return gfx_cache_reader.resolve_frames(self.c, self.clip)

    def assert_rejected(self):
        from vtx import gfx_cache_reader
        with self.assertRaises(gfx_cache_reader.GfxCacheError):
            self.resolve()

    def snapshot(self):
        return {str(p.relative_to(self.root)): (p.stat().st_size, p.stat().st_mtime_ns)
                for p in self.root.rglob('*') if p.is_file()}

    def test_current_completed_page_is_exact_and_read_only(self):
        d = self.seed()
        before = self.snapshot()
        self.assertEqual(self.resolve(), [d / f'f{i:04d}.png' for i in range(3)])
        self.assertEqual(self.snapshot(), before)

    def test_other_namespace_never_selected(self):
        d = gfx_dir(self.c, self.clip)
        self.seed(d.parent.parent / 'other-ns' / d.name, ns='other-ns')
        self.assert_rejected()

    def test_missing_complete_is_not_certified_or_published(self):
        self.seed(marker=False)
        before = self.snapshot()
        self.assert_rejected()
        self.assertEqual(self.snapshot(), before)

    def test_malformed_or_mismatched_completion_is_rejected(self):
        d = self.seed()
        marker = d / 'complete.json'
        original = json.loads(marker.read_text())
        changes = [{'schema': 2}, {'key': 'wrong'}, {'width': 9}, {'height': 7},
                   {'frames': 4}, {'backend_ns': 'other-ns'}, {'sha256': []},
                   {'sha256': ['0' * 64] * 3}]
        for change in changes:
            with self.subTest(change=change):
                marker.write_text(json.dumps(dict(original, **change)))
                self.assert_rejected()
        for malformed in ('{invalid', '{}', '[]', 'null'):
            with self.subTest(malformed=malformed):
                marker.write_text(malformed)
                self.assert_rejected()

    def test_wrong_worker_mode_or_html_never_reuses_stale_page(self):
        self.seed()
        for name, value in [('VTX_CEF_MODE', 'a1'), ('VTX_CEF_WORKER', str(self.root / 'worker2'))]:
            (self.root / 'worker2').write_bytes(b'other worker')
            (self.root / 'worker2').chmod(0o700)
            with self.subTest(name=name), mock.patch.dict(os.environ, {name: value}):
                self.assert_rejected()
        (self.root / 'gfx' / 'page.html').write_text('changed html')
        self.assert_rejected()

    def test_declared_dependency_participates_in_production_key(self):
        dep = self.root / 'style.css'
        dep.write_text('original')
        self.tl['project']['cache_dependencies'] = ['style.css']
        self.seed()
        self.resolve()
        dep.write_text('changed')
        self.assert_rejected()

    def test_gap_is_not_hidden_by_extra_file(self):
        d = self.seed()
        frame = d / 'f0001.png'
        self.assertEqual(frame.resolve().parent, d.resolve())
        frame.unlink()
        (d / 'f9999.png').write_bytes(png_bytes())
        self.assert_rejected()

    def test_valid_but_changed_png_digest_is_rejected(self):
        d = self.seed()
        (d / 'f0001.png').write_bytes(png_bytes(88))
        self.assert_rejected()

    def test_corrupt_png_is_rejected(self):
        d = self.seed()
        (d / 'f0001.png').write_bytes(gfx_cli.PNG_SIGNATURE + b'broken')
        self.assert_rejected()

    def test_cef_never_falls_back_to_formal_or_stem_cache(self):
        self.seed(self.c.cache / 'page', marker=False)
        with mock.patch.dict(os.environ, {'VTX_GFX': 'cdp', 'VTX_GFX_CACHE_NS': ''}):
            self.seed(marker=False)
        self.assert_rejected()

    def test_default_cdp_cli_canonical_only_no_stem_discovery(self):
        for backend in ('cdp', 'cli'):
            with self.subTest(backend=backend), mock.patch.dict(
                    os.environ, {'VTX_GFX': backend, 'VTX_GFX_CACHE_NS': ''}):
                d = self.seed(ns=None)
                self.assertEqual(self.resolve()[0].parent, d)
                # A separate read-only view tests the old stem layout without
                # moving or aliasing any modern cache directory.
                old = SimpleNamespace(**dict(vars(self.c), cache=self.root / ('old-' + backend)))
                self.seed(old.cache / 'page', marker=False)
                from vtx import gfx_cache_reader
                with self.assertRaises(gfx_cache_reader.GfxCacheError):
                    gfx_cache_reader.resolve_frames(old, self.clip)

    def test_incomplete_modern_default_does_not_fall_back_to_stem(self):
        with mock.patch.dict(os.environ, {'VTX_GFX': 'cdp', 'VTX_GFX_CACHE_NS': ''}):
            gfx_dir(self.c, self.clip).mkdir(parents=True)
            self.seed(self.c.cache / 'page', marker=False)
            self.assert_rejected()

    def test_explicit_cdp_namespace_requires_completion(self):
        with mock.patch.dict(os.environ, {'VTX_GFX': 'cdp'}):
            self.seed(marker=False)
            self.assert_rejected()

    def test_symlink_to_other_namespace_does_not_bypass_marker(self):
        selected = gfx_dir(self.c, self.clip)
        other = self.seed(selected.parent.parent / 'other-ns' / selected.name, ns='other-ns')
        selected.parent.mkdir(parents=True)
        selected.symlink_to(other, target_is_directory=True)
        self.assert_rejected()

    def test_strict_canonical_bridge_requires_dependency_certificate(self):
        from vtx.gfx_dependencies import resolve, digest
        with mock.patch.dict(os.environ, VTX_GFX='cdp', VTX_GFX_CACHE_NS=''):
            self.tl['project']['gfx_dependency_policy'] = 'strict'
            d = self.seed(marker=False)
            self.assert_rejected()
            self.seed(ns=None)
            self.assert_rejected()  # PNG hashes alone do not prove strict delivery.
            gfx_cli.complete_page(d, 3, gfx_key(self.c, self.clip), 8, 6,
                                  verify_inputs=lambda: None,
                                  dependency_digest=digest(resolve(self.c, self.clip)),
                                  dependency_enforcement='fetch-pinned-local-v1',
                                  dependency_observed=[(self.root / self.clip['src']).as_uri()],
                                  environment=certificate(self.c))
            before = self.snapshot()
            self.assertEqual(self.resolve()[0].parent, d)
            self.assertEqual(self.snapshot(), before)

    def test_bad_namespace_fails_before_cache_access(self):
        with mock.patch.dict(os.environ, {'VTX_GFX_CACHE_NS': '../escape'}):
            with self.assertRaises(ValueError):
                self.resolve()
        self.assertFalse(self.c.cache.exists())


@unittest.skipUnless(os.environ.get('VTX_TEST_BLENDER') == '1' and shutil.which('blender'),
                     'requires VTX_TEST_BLENDER=1 and blender')
class RealBlenderCache(CacheFixture):
    def blender_command(self, script, *args):
        # Blender itself is real, while the explicitly identity-only fake worker
        # remains a unit fixture in both parent and child interpreters.
        wrapper = self.root / ('fixture_' + Path(script).name)
        wrapper.write_text(
            'import sys,runpy,importlib.util\nfrom unittest.mock import patch\n'
            + 'sys.path.insert(0, ' + repr(str(REPO)) + ')\n'
            + '_s=importlib.util.spec_from_file_location("gfx_environment_fixture", '
            + repr(str(REPO / 'tests/gfx_environment_fixture.py')) + ')\n'
            + '_f=importlib.util.module_from_spec(_s);_s.loader.exec_module(_f)\n'
            + 'with patch("vtx.gfx_environment.collect", side_effect=_f.collect), '
            + 'patch("vtx.encoders.find_chrome", side_effect=_f.find_chrome):\n'
            + '    runpy.run_path(' + repr(str(script)) + ', run_name="__main__")\n')
        return [shutil.which('blender'), '--background', '--factory-startup',
                '--python', str(wrapper), '--', *map(str, args)]

    def export(self, expected=0):
        out = self.root / 'preview.blend'
        cmd = self.blender_command(REPO / 'apps/blender-bridge/json2blend.py', self.timeline, out)
        result = subprocess.run(cmd, env=dict(os.environ), cwd=self.root,
                                capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return out

    def test_current_cef_namespace_exports_and_verifies(self):
        self.seed()
        out = self.export()
        result = subprocess.run(self.blender_command(REPO / 'apps/blender-bridge/verify_blend.py',
            self.timeline, out), capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('失败 0 项', result.stdout)

    def test_other_namespace_cannot_satisfy_selected_namespace(self):
        d = gfx_dir(self.c, self.clip)
        self.seed(d.parent.parent / 'other-ns' / d.name, ns='other-ns')
        self.export(expected=1)

    def test_missing_complete_rejected(self):
        self.seed(marker=False)
        self.export(expected=1)

    def test_missing_frame_not_replaced_by_extra_png(self):
        d = self.seed()
        frame = d / 'f0001.png'
        self.assertEqual(frame.resolve().parent, d.resolve())
        frame.unlink()
        (d / 'f9999.png').write_bytes(png_bytes())
        self.export(expected=1)

    def test_other_worker_cannot_reuse_selected_namespace_cache(self):
        self.seed()
        self.worker.write_bytes(b'identity-only-worker-v2')
        self.export(expected=1)

    def test_wrong_completion_namespace_rejected(self):
        self.seed(ns='wrong-ns')
        self.export(expected=1)

    def test_default_cdp_cli_canonical_and_certified_legacy_export(self):
        for backend in ('cdp', 'cli'):
            with self.subTest(backend=backend), mock.patch.dict(
                    os.environ, {'VTX_GFX': backend, 'VTX_GFX_CACHE_NS': ''}):
                self.seed(ns=None)
                self.export()
                legacy_cache = self.root / ('legacy-' + backend)
                # Point the whole project fixture's cache at a distinct legacy
                # layout, never rename/copy a CEF page into a stem alias.
                legacy = legacy_cache / 'cache' / 'page'
                self.seed(legacy, marker=False)
                (legacy_cache / 'gfx').mkdir()
                (legacy_cache / 'gfx' / 'page.html').write_bytes(
                    (self.root / 'gfx' / 'page.html').read_bytes())
                timeline = legacy_cache / 'timeline.json'
                timeline.write_text(json.dumps(self.tl))
                old_timeline = self.timeline
                self.timeline = timeline
                try:
                    self.export(expected=1)  # unmarked legacy is not provenance
                    old = SimpleNamespace(**dict(vars(self.c), root=legacy_cache,
                                                 cache=legacy_cache / 'cache'))
                    gfx_cli.complete_page(legacy, 3, gfx_key(old, self.clip), 8, 6,
                                          verify_inputs=lambda: None, environment=certificate(old))
                    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in legacy.iterdir()}
                    self.export()
                    self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mtime_ns)
                                              for p in legacy.iterdir()})
                finally:
                    self.timeline = old_timeline


if __name__ == '__main__':
    unittest.main()
