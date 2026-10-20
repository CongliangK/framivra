"""Explicit gfx backend dispatch with ordinary mock renderers and valid PNGs.

No browser processes or adversarial files are used. CDP startup/operational
failures must not trigger a second producer, while explicit CLI remains usable.
"""
import builtins
import contextlib
import io
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
import zlib

from vtx import cli, gfx_cli, gfx_environment, hygiene
from vtx.infra import Ctx
from tests.gfx_environment_fixture import bind_environment


def png():
    def chunk(kind, data):
        return (struct.pack('>I', len(data)) + kind + data
                + struct.pack('>I', zlib.crc32(kind + data)))
    return (gfx_cli.PNG_SIGNATURE
            + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 6, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(b'\x00\x20\x40\x60\xff'))
            + chunk(b'IEND', b''))


class GfxBackendDispatchTests(unittest.TestCase):
    def setUp(self):
        env = mock.patch.dict(os.environ, VTX_GFX='cdp', VTX_GFX_CACHE_NS='',
                              VTX_GFX_SPLIT_FRAMES='0', VTX_GFX_SPLIT_MAX='3',
                              VTX_GFX_PAGES='1')
        env.start()
        self.addCleanup(env.stop)
        bind_environment(self)
        tmp = tempfile.TemporaryDirectory(prefix='vtx_backend_dispatch_')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / 'page.html').write_text('<html><body>fixture</body></html>')
        self.c = self.context()
        self.d = hygiene.gfx_dir(self.c, self.c.tl['video_tracks'][0]['clips'][0])
        self.rendered = []
        self.renderer = mock.Mock(side_effect=self.render_cdp)
        self.fake_cdp = SimpleNamespace(available=mock.Mock(return_value=True),
                                        render_page=self.renderer)
        for target in ('vtx.cli.budget_gate', 'vtx.proc_supervisor.reap_orphan_workers'):
            patch = mock.patch(target)
            patch.start()
            self.addCleanup(patch.stop)

    def context(self, has_gfx=True):
        timeline = self.root / 'timeline.json'
        timeline.write_text(json.dumps(dict(
            version=2, project=dict(fps=30, width=1, height=1, duration_frames=3),
            sources=[], subtitles=[], audio_tracks=[], video_tracks=[
                dict(kind='gfx', clips=[dict(src='page.html', dst_in=0, dur=3)])
            ] if has_gfx else [])))
        c = Ctx(timeline)
        c.out.mkdir(exist_ok=True)
        return c

    def render_cdp(self, html, d, n, **kwargs):
        frames = list(range(n) if kwargs['frames'] is None else kwargs['frames'])
        for i in frames:
            (d / f'f{i:04d}.png').write_bytes(png())
        self.rendered.extend(frames)
        return dict(failed=[], rendered=len(frames), ms_total=1, tabs=1,
                    ms_per_frame=1, path_counts={}, timings_ms={})

    @contextlib.contextmanager
    def dispatch(self):
        with mock.patch.dict(sys.modules, {'vtx.gfx_render': self.fake_cdp}), \
                mock.patch('vtx.gfx_render', self.fake_cdp, create=True), \
                mock.patch.object(cli, 'find_chrome', return_value='unit-chrome'), \
                mock.patch.object(gfx_cli, 'render_cli') as cli_renderer, \
                mock.patch.object(gfx_cli, 'complete_page', wraps=gfx_cli.complete_page) as complete, \
                contextlib.redirect_stdout(io.StringIO()):
            yield cli_renderer, complete

    def receipt(self):
        return json.loads((self.c.out / 'receipt.json').read_text())['stages']['gfx']

    def assert_failed(self, cli_renderer, complete):
        with self.assertRaises(SystemExit) as caught:
            cli.gfx(self.c)
        self.assertEqual(caught.exception.code, 1)
        cli_renderer.assert_not_called()
        complete.assert_not_called()
        self.assertIs(self.receipt()['ok'], False)
        self.assertFalse((self.d / gfx_cli.COMPLETE_FILE).exists())

    def test_missing_numpy_fails_before_render(self):
        self.fake_cdp.available.return_value = False
        with self.dispatch() as (cli_renderer, complete):
            self.assert_failed(cli_renderer, complete)
        self.renderer.assert_not_called()
        self.assertFalse(self.d.exists())
        self.assertIn('numpy', self.receipt()['detail'])
        self.assertIn('VTX_GFX=cli', self.receipt()['detail'])

    @contextlib.contextmanager
    def without_renderer_import(self):
        original_import = builtins.__import__

        def import_without_renderer(name, globals=None, locals=None, fromlist=(), level=0):
            if level == 1 and name == '' and 'gfx_render' in fromlist:
                raise ImportError('mock renderer import unavailable')
            return original_import(name, globals, locals, fromlist, level)

        with mock.patch('builtins.__import__', side_effect=import_without_renderer):
            yield

    def test_renderer_import_failure_fails_before_render(self):
        with self.dispatch() as (cli_renderer, complete), self.without_renderer_import():
            self.assert_failed(cli_renderer, complete)
        self.fake_cdp.available.assert_not_called()
        self.renderer.assert_not_called()
        self.assertFalse(self.d.exists())
        self.assertIn('mock renderer import unavailable', self.receipt()['detail'])

    def test_cdp_exception_never_calls_cli(self):
        self.renderer.side_effect = RuntimeError('mock CDP interruption')
        with self.dispatch() as (cli_renderer, complete):
            self.assert_failed(cli_renderer, complete)
        self.renderer.assert_called_once()
        self.assertIn('mock CDP interruption', self.receipt()['detail'])

    def test_cdp_failed_frames_never_call_cli(self):
        self.renderer.side_effect = None
        self.renderer.return_value = dict(failed=[1], path_counts={})
        with self.dispatch() as (cli_renderer, complete):
            self.assert_failed(cli_renderer, complete)
        self.renderer.assert_called_once()
        self.assertIn('CDP 帧 [1]', self.receipt()['detail'])

    def test_split_cdp_failure_never_completes_page(self):
        def interrupted(html, d, n, **kwargs):
            if kwargs['frames'] == [1]:
                raise RuntimeError('mock split interruption')
            return self.render_cdp(html, d, n, **kwargs)

        self.renderer.side_effect = interrupted
        with self.dispatch() as (cli_renderer, complete), \
                mock.patch.dict(os.environ, VTX_GFX_SPLIT_FRAMES='1'):
            self.assert_failed(cli_renderer, complete)
        self.assertIn(0, self.rendered)
        self.assertTrue((self.d / 'f0000.png').is_file())
        self.assertIn('mock split interruption', self.receipt()['detail'])

    def test_explicit_cli_succeeds_without_cdp(self):
        def render_cli(frames, d, html, chrome, **kwargs):
            self.assertEqual(chrome, 'unit-chrome')
            for i in frames:
                (d / f'f{i:04d}.png').write_bytes(png())

        self.fake_cdp.available.return_value = False
        with mock.patch.dict(os.environ, VTX_GFX='cli'):
            self.c = self.context()
            self.d = hygiene.gfx_dir(self.c, self.c.tl['video_tracks'][0]['clips'][0])
            with self.dispatch() as (cli_renderer, complete):
                cli_renderer.side_effect = render_cli
                cli.gfx(self.c)
                cli_renderer.assert_called_once()
                complete.assert_called_once()
        self.fake_cdp.available.assert_not_called()
        self.renderer.assert_not_called()
        self.assertIs(self.receipt()['ok'], True)
        self.assertTrue((self.d / gfx_cli.COMPLETE_FILE).is_file())

    def test_cdp_whole_page_succeeds(self):
        with self.dispatch() as (cli_renderer, complete):
            cli.gfx(self.c)
            cli_renderer.assert_not_called()
            complete.assert_called_once()
        self.renderer.assert_called_once()
        self.assertEqual(self.rendered, [0, 1, 2])
        self.assertIs(self.receipt()['ok'], True)

    def test_cdp_split_succeeds(self):
        with self.dispatch() as (cli_renderer, complete), \
                mock.patch.dict(os.environ, VTX_GFX_SPLIT_FRAMES='1'):
            cli.gfx(self.c)
            cli_renderer.assert_not_called()
            complete.assert_called_once()
        self.assertEqual(self.renderer.call_count, 3)
        self.assertEqual(self.rendered, [0, 1, 2])
        self.assertIs(self.receipt()['ok'], True)
        self.assertIn('split×3', self.receipt()['detail'])

    def test_dry_run_does_not_require_cdp(self):
        self.c.dry = True
        self.fake_cdp.available.return_value = False
        before = {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        with self.dispatch() as (cli_renderer, complete):
            cli.gfx(self.c)
            cli_renderer.assert_not_called()
            complete.assert_not_called()
        self.fake_cdp.available.assert_not_called()
        self.renderer.assert_not_called()
        self.assertEqual(before, {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def test_no_gfx_does_not_require_cdp(self):
        self.c = self.context(has_gfx=False)
        self.fake_cdp.available.return_value = False
        with self.dispatch() as (cli_renderer, complete):
            cli.gfx(self.c)
            cli_renderer.assert_not_called()
            complete.assert_not_called()
        self.fake_cdp.available.assert_not_called()
        self.renderer.assert_not_called()
        self.assertIs(self.receipt()['ok'], True)

    def check_warm_cache(self, import_failure=False):
        with self.dispatch():
            cli.gfx(self.c)
        before_certificate = gfx_environment.certificate(self.c)
        before = {p.name: p.read_bytes() for p in self.d.iterdir() if p.is_file()}
        self.c = Ctx(self.c.tl_path)
        after_certificate = gfx_environment.certificate(self.c)
        self.assertEqual(before_certificate, after_certificate)
        key = hygiene.gfx_key(self.c, self.c.tl['video_tracks'][0]['clips'][0])
        self.assertEqual(gfx_cli.inspect_page(self.d, 3, key, 1, 1,
                                             environment=after_certificate), ([], False))
        self.renderer.reset_mock()
        self.fake_cdp.available.reset_mock()
        if not import_failure:
            self.fake_cdp.available.return_value = False
        import_patch = self.without_renderer_import() if import_failure else contextlib.nullcontext()
        with self.dispatch() as (cli_renderer, complete), import_patch, \
                contextlib.redirect_stdout(io.StringIO()) as output:
            cli.gfx(self.c)
            self.renderer.assert_not_called()
            cli_renderer.assert_not_called()
            complete.assert_not_called()
        self.fake_cdp.available.assert_not_called()
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.d.iterdir() if p.is_file()})
        self.assertTrue((self.d / gfx_cli.COMPLETE_FILE).is_file())
        self.assertIs(self.receipt()['ok'], True)
        self.assertIn('已缓存', output.getvalue())

    def test_warm_cache_without_available_renderer(self):
        self.check_warm_cache()

    def test_warm_cache_without_renderer_import(self):
        self.check_warm_cache(import_failure=True)


if __name__ == '__main__':
    unittest.main()
