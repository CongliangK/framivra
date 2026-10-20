"""Bounded bridge certificate and renderer-event regressions (CPU fixtures)."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from tests.test_execution_events import Buffer
from tests.test_frame_source import write_png
from tests.test_gfx_cef_alpha import project
from tests.gfx_environment_fixture import bind_environment
from vtx.gfx_environment import certificate
from vtx import cli, events, frame_source, gfx_cli, gfx_cache_reader, gfx_render
from vtx.hygiene import gfx_dir, gfx_key
from vtx.infra import Ctx


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
                                              ('cdp', False, None), ('cli', True, 'cli'),
                                              ('cli', False, 'cli')):
            with self.subTest(backend=backend, available=available), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                timeline = project(root, 1)
                worker = root / 'worker'
                worker.write_text('#!/bin/sh\nexit 1\n')
                real_access = os.access

                def fixture_access(path, mode, *args, **kwargs):
                    # All renderers are mocked; only this fixture's executable
                    # preflight is simulated, without changing file permissions.
                    if Path(path) == worker and mode == os.X_OK:
                        return True
                    return real_access(path, mode, *args, **kwargs)

                stream = Buffer()
                execution = events.ExecutionContext(stream)
                with mock.patch.dict(os.environ, VTX_GFX=backend, VTX_GFX_CACHE_NS='',
                                     VTX_CEF_WORKER=str(worker), VTX_CEF_MODE='a0'), \
                     mock.patch('vtx.gfx_backend.os.access', side_effect=fixture_access), \
                     mock.patch.object(gfx_render, 'available', return_value=available), \
                     mock.patch.object(cli, 'budget_gate'), \
                     mock.patch.object(cli, 'find_chrome', return_value='unused'), \
                     mock.patch.object(gfx_render, 'render_page', return_value={'failed': [], 'path_counts': {}, 'ms_per_frame': 0}) as cdp_renderer, \
                     mock.patch('vtx.gfx_cef.render_page', return_value='CEF fixture') as cef_renderer, \
                     mock.patch.object(cli, '_cdp_via', return_value='CDP fixture'), \
                     mock.patch.object(gfx_cli, 'render_cli', return_value=1) as cli_renderer, \
                     mock.patch.object(gfx_cli, 'complete_page'), \
                     contextlib.redirect_stdout(io.StringIO()):
                    c = Ctx(timeline, execution=execution)
                    if expected is None:
                        with self.assertRaisesRegex(RuntimeError, 'numpy'):
                            cli._gfx(c)
                    else:
                        cli._gfx(c)
                    for name, renderer in (('cdp', cdp_renderer), ('cef', cef_renderer),
                                           ('cli', cli_renderer)):
                        self.assertEqual(renderer.call_count, int(name == expected))
                execution.finish(1 if expected is None else 0)
                rows = events.read_events(stream.getvalue())['events']
                starts = [r for r in rows if r['event'] == 'task_start']
                if expected is None:
                    self.assertEqual(starts, [])
                else:
                    self.assertEqual(starts[0]['extensions']['backend'], expected)


if __name__ == '__main__':
    unittest.main()
