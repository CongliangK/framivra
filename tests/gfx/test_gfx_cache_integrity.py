"""Issue #33: PNG/page integrity, resumable repair, completion and zero-write dry.

Fixtures use isolated TemporaryDirectory projects; no real project is touched.
VTX_TEST_CHROME enables the small real-browser cache-repair check explicitly.
"""
from types import SimpleNamespace
from tests.gfx._png import write_png
from tests.gfx._environment import bind_environment
from vtx import frame_source, gfx_cache_reader
from vtx.gfx_environment import certificate
from vtx.hygiene import gfx_dir, gfx_key

import contextlib
import io
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
import zlib
from unittest import mock

from tests.gfx._png import png, chunk

from vtx import cli, gfx_cli, gfx_render, hygiene, gfx_environment
from vtx.infra import Ctx






def stats(n):
    return dict(failed=[], rendered=n, ms_total=1, tabs=1, ms_per_frame=1,
                path_counts={}, timings_ms={}, static_spans=0)


class CacheIntegrity(unittest.TestCase):
    def setUp(self):
        from tests.gfx._environment import bind_environment
        bind_environment(self)
        self.tmp = tempfile.TemporaryDirectory(prefix='vtx_issue33_')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'page.html').write_text('<html><body>fixture</body></html>')
        self.timeline = self.root / 'timeline.json'
        self.timeline.write_text(json.dumps(dict(
            project=dict(fps=30, width=6, height=4, duration_frames=3), sources=[],
            subtitles=[], audio_tracks=[], video_tracks=[dict(kind='gfx', clips=[
                dict(src='page.html', dst_in=0, dur=3)])])))
        self.c = Ctx(self.timeline)
        self.cl = self.c.tl['video_tracks'][0]['clips'][0]
        self.key = hygiene.gfx_key(self.c, self.cl)
        self.d = hygiene.gfx_dir(self.c, self.cl)
        self.d.mkdir(parents=True)
        self.rendered = []

    def frames(self):
        for i in range(3):
            (self.d / f'f{i:04d}.png').write_bytes(png(value=i))

    def complete(self):
        gfx_cli.complete_page(self.d, 3, self.key, 6, 4,
                              verify_inputs=lambda: self.c.verify_inputs('gfx'),
                              environment=gfx_environment.certificate(self.c))

    def renderer(self, html, d, n, **kw):
        targets = range(n) if kw['frames'] is None else kw['frames']
        work = [i for i, _ in gfx_cli.verify_frames(d, targets, width=6, height=4)]
        for i in work:
            self.rendered.append(i)
            (d / f'f{i:04d}.png').write_bytes(png(value=i))
        return stats(len(work))

    def run_gfx(self, renderer=None, split='0'):
        with mock.patch.dict(os.environ, VTX_GFX='cdp', VTX_GFX_SPLIT_FRAMES=split,
                             VTX_GFX_PAGES='1'), \
                mock.patch.object(gfx_render, 'available', return_value=True), \
                mock.patch.object(gfx_render, 'render_page', side_effect=renderer or self.renderer), \
                contextlib.redirect_stdout(io.StringIO()):
            cli.gfx(self.c)

    def test_missing_or_malformed_marker_requires_environment_rerender(self):
        self.frames()
        for content in (None, '{broken', json.dumps(dict(schema=999))):
            marker = self.d / gfx_cli.COMPLETE_FILE
            if content is None:
                marker.unlink(missing_ok=True)
            else:
                marker.write_text(content)
            self.rendered.clear()
            self.run_gfx()
            self.assertEqual(self.rendered, [0, 1, 2])
            self.assertEqual(gfx_cli.inspect_page(self.d, 3, self.key, 6, 4), ([], False))
            self.assertEqual(len(json.loads(marker.read_text())['sha256']), 3)

    def test_wrong_size_truncation_crc_and_filter_are_repaired(self):
        for bad in (png(5, 4), png()[:-1], png()[:29] + bytes([png()[29] ^ 1]) + png()[30:],
                    png(filter_byte=5)):
            with self.subTest(kind=bad[:35]):
                self.frames()
                self.complete()
                (self.d / 'f0001.png').write_bytes(bad)
                kept = (self.d / 'f0000.png').read_bytes()
                self.rendered.clear()
                self.run_gfx()
                self.assertEqual(self.rendered, [1])
                self.assertEqual((self.d / 'f0000.png').read_bytes(), kept)
                self.assertEqual(gfx_cli.inspect_page(self.d, 3, self.key, 6, 4), ([], False))

    def test_valid_but_changed_frame_digest_forces_repair(self):
        self.frames()
        self.complete()
        (self.d / 'f0002.png').write_bytes(png(value=77))
        self.run_gfx()
        self.assertEqual(self.rendered, [2])
        self.assertEqual((self.d / 'f0002.png').read_bytes(), png(value=2))

    def test_stale_contract_marker_forces_full_repair(self):
        self.frames()
        self.complete()
        marker = self.d / gfx_cli.COMPLETE_FILE
        obj = json.loads(marker.read_text())
        obj['width'] = 1
        marker.write_text(json.dumps(obj))
        self.run_gfx()
        self.assertEqual(self.rendered, [0, 1, 2])

    def test_partial_page_revokes_marker_and_resumes_only_missing(self):
        self.frames()
        self.complete()
        (self.d / 'f0002.png').unlink()
        def fail(html, d, n, **kw):
            self.assertFalse((d / gfx_cli.COMPLETE_FILE).exists())
            return stats(0)  # lies about success: final whole-page gate must reject
        with self.assertRaises(SystemExit), contextlib.redirect_stdout(io.StringIO()):
            self.run_gfx(renderer=fail)
        self.assertFalse((self.d / gfx_cli.COMPLETE_FILE).exists())
        self.assertFalse(json.loads((self.c.out / 'receipt.json').read_text())['stages']['gfx']['ok'])
        self.run_gfx()
        self.assertEqual(self.rendered, [2])
        self.assertEqual(gfx_cli.inspect_page(self.d, 3, self.key, 6, 4), ([], False))

    def test_split_final_gate_and_marker_wait_for_all_blocks(self):
        def split(html, d, n, **kw):
            self.assertFalse((d / gfx_cli.COMPLETE_FILE).exists())
            return self.renderer(html, d, n, **kw)
        self.run_gfx(renderer=split, split='1')
        self.assertEqual(self.rendered, [0, 1, 2])
        self.assertEqual(gfx_cli.inspect_page(self.d, 3, self.key, 6, 4), ([], False))

    def test_split_bad_dimensions_cannot_publish_completion(self):
        def bad(html, d, n, **kw):
            for i in kw['frames']:
                (d / f'f{i:04d}.png').write_bytes(png(1, 1))
            return stats(len(kw['frames']))
        with self.assertRaises(SystemExit):
            self.run_gfx(renderer=bad, split='1')
        self.assertFalse((self.d / gfx_cli.COMPLETE_FILE).exists())
        self.assertFalse(json.loads((self.c.out / 'receipt.json').read_text())['stages']['gfx']['ok'])

    def test_warm_cache_never_starts_browser_or_cli(self):
        self.frames()
        self.complete()
        with mock.patch.object(gfx_render, 'Browser', side_effect=AssertionError('Chrome')), \
                mock.patch.object(gfx_cli, 'run_cli_shot', side_effect=AssertionError('CLI')):
            self.run_gfx(renderer=lambda *a, **k: self.fail('warm cache'))

    def test_dry_preserves_invalid_cache_and_marker_bytes(self):
        self.frames()
        self.complete()
        (self.d / 'f0002.png').write_bytes(b'broken')
        before = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.c.dry = True
        self.run_gfx(renderer=lambda *a, **k: self.fail('dry Chrome'))
        after = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(before, after)

    def test_input_change_blocks_new_marker(self):
        self.frames()
        self.timeline.write_bytes(self.timeline.read_bytes() + b' ')
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            self.complete()
        self.assertFalse((self.d / gfx_cli.COMPLETE_FILE).exists())

    def test_atomic_marker_failure_does_not_certify_or_leave_tmp(self):
        self.frames()
        with mock.patch('vtx.fstxn.os.replace', side_effect=OSError('injected interruption')):
            with self.assertRaises(OSError):
                self.complete()
        self.assertFalse((self.d / gfx_cli.COMPLETE_FILE).exists())
        self.assertFalse(list(self.d.glob('*.tmp')))

    def test_png_structure_filters_and_dimensions(self):
        target = self.d / 'f0000.png'
        for bad in (png() + b'trailing', png(filter_byte=255),
                    gfx_cli.PNG_SIGNATURE + chunk(b'IHDR', struct.pack('>IIBBBBB', 6, 4, 8, 6, 0, 0, 0))
                    + chunk(b'IHDR', b'X' * 13) + png()[33:]):
            target.write_bytes(bad)
            self.assertIsNotNone(gfx_cli.png_invalid_reason(target, width=6, height=4))
        # Scanline filters cross both compressed read and bounded output boundaries.
        big = self.d / 'big.png'
        big.write_bytes(png(400, 700, filter_byte=0))
        self.assertIsNone(gfx_cli.png_invalid_reason(big, width=400, height=700))
        self.assertIsNotNone(gfx_cli.png_invalid_reason(big, width=6, height=4))


@unittest.skipUnless(os.environ.get('VTX_TEST_CHROME'), 'set VTX_TEST_CHROME for real cache repair')
class RealBrowserIntegrity(unittest.TestCase):
    def test_real_cdp_repair_and_zero_browser_warm_hit(self):
        import numpy as np
        with tempfile.TemporaryDirectory(prefix='vtx_issue33_chrome_') as tmp:
            root = Path(tmp)
            (root / 'page.html').write_text('''<!doctype html><style>html,body{margin:0;background:transparent}
                div{width:120px;height:100px;background:rgba(255,0,30,.5)}</style><div></div>''')
            p = root / 'timeline.json'
            p.write_text(json.dumps(dict(project=dict(fps=30, width=320, height=240, duration_frames=3),
                                        sources=[], subtitles=[], audio_tracks=[], video_tracks=[
                                            dict(kind='gfx', clips=[dict(src='page.html', dst_in=0, dur=3)])])))
            c = Ctx(p)
            cl = c.tl['video_tracks'][0]['clips'][0]
            d = hygiene.gfx_dir(c, cl)
            key = hygiene.gfx_key(c, cl)
            with mock.patch.dict(os.environ, VTX_CHROME=os.environ['VTX_TEST_CHROME'], VTX_GFX='cdp',
                                 VTX_GFX_PAGES='1', VTX_GFX_TABS='1', VTX_GFX_SPLIT_FRAMES='0'):
                cli.gfx(c)
                pixels = [gfx_render.png_decode_rgba((d / f'f{i:04d}.png').read_bytes()) for i in range(3)]
                (d / 'f0001.png').write_bytes(png(320, 240)[:-1])
                cli.gfx(Ctx(p))
                for i in range(3):
                    np.testing.assert_array_equal(pixels[i], gfx_render.png_decode_rgba((d / f'f{i:04d}.png').read_bytes()))
                self.assertEqual(gfx_cli.inspect_page(d, 3, key, 320, 240), ([], False))
                with mock.patch.object(gfx_render, 'Browser', side_effect=AssertionError('warm Chrome')):
                    cli.gfx(Ctx(p))



class LegacyCertificate(unittest.TestCase):
    def setUp(self):
        bind_environment(self)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'page.html').write_text('<html><body>inert</body></html>')
        self.clip = dict(src='page.html', dst_in=0, dur=2)
        self.c = SimpleNamespace(root=self.root, cache=self.root / 'cache',
                                 fps=30, w=16, h=16, tl={'project': {}})
        worker = self.root / 'worker'
        worker.write_text('#!/bin/sh\nexit 1\n')
        worker.chmod(0o700)
        patch = mock.patch.dict(os.environ, VTX_GFX='cdp', VTX_GFX_CACHE_NS='',
                                VTX_CEF_WORKER=str(worker), VTX_CEF_MODE='a0')
        patch.start()
        self.addCleanup(patch.stop)
        self.legacy = self.c.cache / 'page'
        self.legacy.mkdir(parents=True)
        for i in range(2):
            write_png(self.legacy / f'f{i:04d}.png', frame=i)
        gfx_cli.complete_page(self.legacy, 2, gfx_key(self.c, self.clip), 16, 16,
                              verify_inputs=lambda: None, environment=certificate(self.c))

    def reject(self):
        with self.assertRaises(gfx_cache_reader.GfxCacheError):
            gfx_cache_reader.resolve_frames(self.c, self.clip)

    def test_verified_default_cdp_cli_stem_is_readonly_but_production_stays_closed(self):
        for backend in ('cdp', 'cli'):
            with self.subTest(backend=backend), mock.patch.dict(os.environ, VTX_GFX=backend):
                # CDP and CLI no longer share a renderer identity. A current
                # legacy-layout certificate must identify this selected path.
                gfx_cli.complete_page(self.legacy, 2, gfx_key(self.c, self.clip), 16, 16,
                                      verify_inputs=lambda: None, environment=certificate(self.c))
                directory = gfx_dir(self.c, self.clip)
                self.assertFalse(directory.exists())
                self.assertTrue(gfx_cli.inspect_page(directory, 2, gfx_key(self.c, self.clip), 16, 16)[0])
                with self.assertRaises(ValueError):
                    frame_source.png_source(self.c, self.clip).require_ready()
                before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.legacy.iterdir()}
                with contextlib.ExitStack() as audit:
                    for name in ('vtx.infra.Ctx', 'vtx.gfx_cli.complete_page',
                                 'vtx.gfx_cef.render_page', 'vtx.gfx_render.render_page',
                                 'subprocess.Popen', 'vtx.fstxn.atomic_write',
                                 'vtx.fstxn.mkdir', 'vtx.fstxn.unlink', 'vtx.fstxn.locked_staging'):
                        audit.enter_context(mock.patch(name, side_effect=AssertionError(name)))
                    self.assertEqual(gfx_cache_reader.resolve_frames(self.c, self.clip),
                                     [self.legacy / f'f{i:04d}.png' for i in range(2)])
                self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.legacy.iterdir()})

    def test_missing_malformed_stale_wrong_geometry_count_namespace_hash_marker(self):
        marker = self.legacy / 'complete.json'
        original = marker.read_text()
        variants = ['{bad', '{}', 'null']
        record = json.loads(original)
        variants += [json.dumps(dict(record, **change)) for change in (
            {'schema': 2}, {'key': 'stale'}, {'width': 15}, {'height': 15},
            {'frames': 3}, {'backend_ns': 'other'}, {'sha256': []},
            {'sha256': ['0' * 64] * 2})]
        marker.unlink()
        self.reject()
        for value in variants:
            with self.subTest(marker=value):
                marker.write_text(value)
                self.reject()
        marker.write_text(original)
        write_png(self.legacy / 'f0000.png', frame=99)
        self.reject()

    def test_stale_html_and_hole_rejected(self):
        page = self.root / 'page.html'
        original = page.read_text()
        page.write_text('<html>changed</html>')
        self.reject()
        page.write_text(original)
        (self.legacy / 'f0001.png').unlink()
        write_png(self.legacy / 'f9999.png')
        self.reject()

    def test_existing_broken_canonical_never_hidden(self):
        directory = gfx_dir(self.c, self.clip)
        directory.mkdir(parents=True)
        self.reject()
        directory.rmdir()
        directory.write_text('not a directory')
        self.reject()
        directory.unlink()
        directory.symlink_to(self.root / 'absent', target_is_directory=True)
        self.reject()
        directory.unlink()
        directory.mkdir()
        for i in range(2):
            write_png(directory / f'f{i:04d}.png', frame=i)
        (directory / 'complete.json').write_text('{}')
        self.reject()

    def test_namespace_cef_unknown_never_use_certified_stem(self):
        for backend, namespace in (('cdp', 'other'), ('cli', 'other'), ('cef', ''), ('typo', '')):
            with self.subTest(backend=backend), mock.patch.dict(
                    os.environ, VTX_GFX=backend, VTX_GFX_CACHE_NS=namespace):
                with self.assertRaises(ValueError):
                    gfx_cache_reader.resolve_frames(self.c, self.clip)

if __name__ == '__main__':
    unittest.main()
