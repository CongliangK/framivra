"""PR166 integration: PNG consumption is not HTML execution or recertification."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.test_frame_source import write_png
from tests.test_gfx_cef_alpha import project
from vtx import cli, gfx_backend, gfx_cef
from vtx.frame_source import PngSequence, png_source
from vtx.hygiene import gfx_key
from vtx.infra import Ctx


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
                from vtx.impact import capture
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
